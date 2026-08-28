"""Private Cloud Run receiver for authorized Backstage snapshots.

Cloud Run IAM restricts this service to the reader service account. The
receiver writes into the same PostgreSQL tables already used by app.py.
"""

from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd

from backstage_sync import database_engine, ensure_schema, replace_dashboard_data


def first_line(value: object) -> str:
    for line in str(value or "").splitlines():
        clean = line.strip()
        if clean and clean.casefold() not in {"/not set", "not set", "not allocated"}:
            return clean
    return ""


def column_index(headers: list[object], *names: str) -> int | None:
    normalized = {str(header).strip().casefold(): index for index, header in enumerate(headers)}
    for name in names:
        if name.casefold() in normalized:
            return normalized[name.casefold()]
    return None


def value_at(row: list[object], index: int | None) -> str:
    return first_line(row[index]) if index is not None and index < len(row) else ""


def number(value: object) -> float:
    match = re.search(r"-?[\d,]+(?:\.\d+)?", first_line(value))
    return float(match.group(0).replace(",", "")) if match else 0.0


def hours(value: object) -> float:
    text = first_line(value)
    result = 0.0
    for suffix, multiplier in (("h", 1.0), ("m", 1 / 60), ("s", 1 / 3600)):
        match = re.search(r"([\d.]+)\s*" + suffix, text, flags=re.I)
        if match:
            result += float(match.group(1)) * multiplier
    return result


def display_tier_status(outcome: str, tier: str) -> str:
    lowered = outcome.casefold()
    if "ranked up" in lowered:
        return f"Ranked up to {tier.casefold()}" if tier else "Ranked up"
    if "maintain" in lowered and "not" not in lowered:
        return f"Maintaining {tier.casefold()}" if tier else "Maintained"
    if "not maintained" in lowered:
        return f"Tier not maintained / {tier}" if tier else "Tier not maintained"
    return outcome or tier


def creator_frames(payload: dict[str, object]) -> tuple[pd.DataFrame, pd.DataFrame]:
    headers = payload.get("headers", [])
    rows = payload.get("rows", [])
    if not isinstance(headers, list) or not isinstance(rows, list):
        raise ValueError("Snapshot does not contain a table.")

    indexes = {
        "creator": column_index(headers, "Creator", "Creator's username"),
        "creator_id": column_index(headers, "Creator ID", "ID"),
        "manager": column_index(headers, "Manager", "Creator Network manager", "Manager name"),
        "diamonds": column_index(headers, "Diamonds"),
        "days": column_index(headers, "Valid go LIVE days", "Valid LIVE days"),
        "duration": column_index(headers, "Valid LIVE duration", "LIVE duration"),
        "bonus": column_index(headers, "Estimated bonus", "Bonus contribution"),
        "tier": column_index(headers, "Tier"),
        "tier_status": column_index(headers, "Tier status", "Rank-up status", "Tier outcome"),
        "progress": column_index(headers, "Rank-up incentive progress", "Rank up incentive progress"),
        "activeness": column_index(headers, "Activeness level", "Activeness"),
    }
    if indexes["creator"] is None:
        raise ValueError("Snapshot is missing the Creator column.")
    if indexes["tier_status"] is None:
        raise ValueError("Snapshot is missing the Tier status column.")

    records: list[dict[str, object]] = []
    for raw in rows:
        if not isinstance(raw, list):
            continue
        username = value_at(raw, indexes["creator"])
        if not username:
            continue
        raw_id = value_at(raw, indexes["creator_id"])
        creator_id = raw_id if re.fullmatch(r"\d{8,}", raw_id) else f"missing:{username.casefold()}"
        manager = value_at(raw, indexes["manager"]) or "Unassigned"
        tier = value_at(raw, indexes["tier"])
        outcome = value_at(raw, indexes["tier_status"])
        records.append({
            "creator_id": creator_id,
            "username": username,
            "manager": manager,
            "manager_name": manager,
            "group_name": "",
            "diamonds": int(number(value_at(raw, indexes["diamonds"]))),
            "valid_live_days": int(number(value_at(raw, indexes["days"]))),
            "valid_live_hours": hours(value_at(raw, indexes["duration"])),
            "estimated_bonus": number(value_at(raw, indexes["bonus"])),
            "tier_status": display_tier_status(outcome, tier),
            "rank_up_progress": value_at(raw, indexes["progress"]),
            "activeness_level": int(number(value_at(raw, indexes["activeness"]))),
            "live_now": 0,
        })

    creators = pd.DataFrame(records).drop_duplicates(subset=["creator_id"], keep="last")
    if creators.empty:
        raise ValueError("Snapshot did not contain usable Creator rows.")

    meta = payload.get("meta", {})
    new_creator_total = int(number(meta.get("new_creators", 0))) if isinstance(meta, dict) else 0

    engine = database_engine()
    ensure_schema(engine)
    with engine.connect() as connection:
        current = pd.read_sql("SELECT * FROM goal_managers", connection)
    existing = current.set_index("manager").to_dict("index") if not current.empty else {}

    manager_rows = []
    grouped = list(creators[creators["manager"] != "Unassigned"].groupby("manager"))
    for index, (manager, group) in enumerate(grouped):
        prior = existing.get(manager, {})
        manager_rows.append({
            "manager": manager,
            "manager_name": prior.get("manager_name") or manager,
            "role": prior.get("role", "Manager"),
            "group_name": prior.get("group_name", ""),
            "diamonds": int(group["diamonds"].sum()),
            "diamond_goal": int(prior.get("diamond_goal", 0) or 0),
            "new_creators": new_creator_total if index == 0 else 0,
            "new_creator_goal": int(prior.get("new_creator_goal", 0) or 0),
            "managed_creators": int(len(group)),
        })
    return creators, pd.DataFrame(manager_rows)


class Receiver(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._send(200 if self.path == "/healthz" else 404, {"ok": self.path == "/healthz"})

    def do_POST(self) -> None:
        if self.path != "/internal/backstage/snapshot":
            self._send(404, {"error": "not found"})
            return
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Snapshot must be an object.")
            creators, managers = creator_frames(payload)
            replace_dashboard_data(database_engine(), creators, managers, "authorized-backstage-snapshot")
            self._send(200, {"saved": True, "creator_rows": int(len(creators))})
        except Exception as error:
            self._send(400, {"saved": False, "error": str(error)})

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Receiver).serve_forever()
