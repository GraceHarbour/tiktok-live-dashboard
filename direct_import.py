"""Import the reader's verified Backstage captures directly into Supabase.

This file is intended for the private reader VM.  The database URL is read
from its locked local configuration file and is never printed.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import psycopg


ROOT = Path(__file__).resolve().parent
DATABASE_URL = Path.home() / ".config/creator-reader/database-url"


def number(value: object) -> int:
    try:
        return int(str(value).replace(",", "").replace(" ", ""))
    except (TypeError, ValueError):
        return 0


def live_hours(value: object) -> float:
    try:
        return float(str(value).strip().removesuffix("h").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def first_line(value: object) -> str:
    return str(value or "").split("\n", 1)[0].strip()


def import_goals(cursor, candidate: dict[str, object]) -> tuple[int, int]:
    creators = candidate.get("creators", [])
    if not isinstance(creators, list) or not creators:
        raise RuntimeError("Goal capture contains no creator rows")
    cursor.execute("delete from goal_creators")
    cursor.execute("delete from goal_managers")
    summaries: dict[str, dict[str, int]] = defaultdict(lambda: {"diamonds": 0, "creators": 0})
    for row in creators:
        if not isinstance(row, dict):
            continue
        username = first_line(row.get("creator")) or "Unknown creator"
        creator_id = str(row.get("creator_id") or username.casefold())
        manager = first_line(row.get("manager")) or "Unassigned"
        diamonds = number(row.get("diamonds"))
        cursor.execute(
            """insert into goal_creators
               (creator_id, username, manager, manager_name, group_name, diamonds,
                valid_live_days, valid_live_hours, estimated_bonus, tier_status,
                rank_up_progress, activeness_level, live_now,
                diamonds_display, valid_live_days_display, valid_live_duration_display,
                bonus_display, rank_up_detail, activeness_display, avatar_url)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                creator_id, username, manager, manager, manager, diamonds,
                number(row.get("valid_live_days")), live_hours(row.get("valid_live_duration")),
                number(row.get("bonus")), first_line(row.get("tier_status") or row.get("tier")),
                first_line(row.get("rank_up_status")), number(row.get("activeness")),
                int(bool(row.get("is_live"))),
                str(row.get("diamonds_display") or row.get("diamonds") or "").strip(),
                str(row.get("valid_live_days_display") or row.get("valid_live_days") or "").strip(),
                str(row.get("valid_live_duration_display") or row.get("valid_live_duration") or "").strip(),
                str(row.get("bonus_display") or row.get("bonus") or "").strip(),
                str(row.get("rank_up_detail") or "").strip(),
                str(row.get("activeness_display") or row.get("activeness") or "").strip(),
                str(row.get("avatar_url") or "").strip(),
            ),
        )
        summaries[manager]["diamonds"] += diamonds 
        summaries[manager]["creators"] += 1
    for manager, totals in summaries.items():
        cursor.execute(
            """insert into goal_managers
               (manager, manager_name, role, group_name, diamonds, diamond_goal,
                new_creators, new_creator_goal, managed_creators)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (manager, manager, "Manager", manager, totals["diamonds"], 0, 0, 0, totals["creators"]),
        )
    return len(creators), len(summaries)


def import_business(cursor, capture: dict[str, object]) -> int:
    rows = capture.get("rows", [])
    headers = capture.get("headers", [])
    month = str(capture.get("month") or datetime.now(timezone.utc).strftime("%Y-%m"))
    if not isinstance(rows, list) or not isinstance(headers, list) or not rows:
        raise RuntimeError("Business capture contains no rows")
    cursor.execute("delete from business_essentials_rows where snapshot_month=%s", (month,))
    captured_at = str(capture.get("captured_at") or datetime.now(timezone.utc).isoformat())
    for index, row in enumerate(rows):
        if not isinstance(row, list):
            continue
        if len(row) != len(headers):
            continue
        payload = {
            "headers": [str(header) for header in headers],
            "row": row,
            "overview": capture.get("overview", {}),
        }
        values = {str(headers[i]): row[i] for i in range(len(headers))}
        section = first_line(values.get("Record type")) or "Business essentials"
        key = "|".join((section, first_line(values.get("Manager")), first_line(values.get("Creator"))))
        cursor.execute(
            """insert into business_essentials_rows
               (section, snapshot_month, row_key, row_index, payload, captured_at)
               values (%s,%s,%s,%s,%s::jsonb,%s)""",
            (section, month, key, index, json.dumps(payload), captured_at),
        )
    return len(rows)


def main() -> None:
    goal = json.loads((ROOT / "data/goal-creators-candidate.json").read_text(encoding="utf-8"))
    business = json.loads((ROOT / "data/business-essentials-latest.json").read_text(encoding="utf-8"))
    with psycopg.connect(DATABASE_URL.read_text(encoding="utf-8").strip()) as connection:
        with connection.cursor() as cursor:
            creator_count, manager_count = import_goals(cursor, goal)
            business_count = import_business(cursor, business)
    print(f"Imported {creator_count} creators, {manager_count} managers, and {business_count} Business Essentials rows.")


if __name__ == "__main__":
    main()
