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


def compact_number(value: object) -> float:
    raw = first_line(value).replace(",", "")
    match = re.search(r"(-?[\d.]+)\s*([KMB]?)", raw, flags=re.I)
    if not match:
        return 0.0
    multiplier = {"K": 1000, "M": 1000000, "B": 1000000000}.get(match.group(2).upper(), 1)
    return float(match.group(1)) * multiplier


def scouting_frames(payload: dict[str, object]) -> list[dict[str, object]]:
    source = str(payload.get("source") or "")
    rows = payload.get("rows", [])
    if source not in {"scouting_applied", "scouting_invited"} or not isinstance(rows, list):
        raise ValueError("Unsupported Scouting snapshot.")
    captured_at = str(payload.get("captured_at") or "")
    records = []
    for raw in rows:
        if not isinstance(raw, list) or not raw:
            continue
        cells = [str(cell or "").strip() for cell in raw]
        creator = first_line(cells[0])
        if not creator or creator.casefold() in {"creator", "creators"}:
            continue
        profile = cells[0]
        def field(pattern: str, text_value: str = profile) -> float:
            match = re.search(pattern, text_value, re.I)
            return compact_number(match.group(1)) if match else 0.0
        metrics = cells[2] if len(cells) > 2 else ""
        invited = source == "scouting_invited"
        assigned_index = 4 if invited else 3
        source_index = 5 if invited else 4
        expiry_index = 6 if invited else 5
        records.append({
            "source": source, "username": creator,
            "followers": field(r"([\d.,]+[KMB]?)\s+followers"),
            "likes": field(r"([\d.,]+[KMB]?)\s+likes"),
            "applied_to_join": (first_line(cells[1]).casefold() == "yes") if len(cells) > 1 and not invited else False,
            "scouting_status": first_line(cells[1]) if invited and len(cells) > 1 else "",
            "live_streams": field(r"([\d.,]+[KMB]?)\s+LIVE streams?", metrics),
            "diamonds": field(r"([\d.,]+[KMB]?)\s+Diamonds", metrics),
            "live_hours": field(r"([\d.]+)\s+h", metrics),
            "avg_live_viewers": field(r"([\d.,]+[KMB]?)\s+Avg\. LIVE viewers", metrics),
            "invitation_type": first_line(cells[3]) if invited and len(cells) > 3 else "",
            "assigned_manager": first_line(cells[assigned_index]) if len(cells) > assigned_index else "Unassigned",
            "source_label": first_line(cells[source_index]) if len(cells) > source_index else "",
            "lead_expiry": first_line(cells[expiry_index]) if len(cells) > expiry_index else "",
            "captured_at": captured_at,
        })
    if not records:
        raise ValueError("Scouting snapshot did not contain usable rows.")
    return records


def replace_scouting_records(engine, payload: dict[str, object]) -> int:
    records = scouting_frames(payload)
    source = str(payload["source"])
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM scouting_records WHERE source = :source"), {"source": source})
        connection.execute(text("INSERT INTO scouting_records (source, username, followers, likes, applied_to_join, scouting_status, live_streams, diamonds, live_hours, avg_live_viewers, invitation_type, assigned_manager, source_label, lead_expiry, captured_at) VALUES (:source, :username, :followers, :likes, :applied_to_join, :scouting_status, :live_streams, :diamonds, :live_hours, :avg_live_viewers, :invitation_type, :assigned_manager, :source_label, :lead_expiry, :captured_at)"), records)
    return len(records)


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
            if str(payload.get("source") or "").startswith("scouting_"):
                rows = replace_scouting_records(database_engine(), payload)
                self._send(200, {"saved": True, "scouting_rows": rows})
            else:
                creators, managers = creator_frames(payload)
                replace_dashboard_data(database_engine(), creators, managers, "authorized-backstage-snapshot")
                self._send(200, {"saved": True, "creator_rows": int(len(creators))})
        except Exception as error:
            self._send(400, {"saved": False, "error": str(error)})

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Receiver).serve_forever()
