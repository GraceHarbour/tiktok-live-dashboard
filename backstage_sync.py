"""Download the current Backstage Creator data export and sync it to the dashboard DB.

The browser session and database URL are supplied only through encrypted secrets.
For local verification, pass --file with an existing Backstage Excel export.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
from sqlalchemy import create_engine, text


DEFAULT_BACKSTAGE_URL = "https://live-backstage.tiktok.com/portal/data/data/"


def database_engine():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    elif url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgres://")
    return create_engine(url, pool_pre_ping=True)


def ensure_schema(engine):
    statements = [
        "CREATE TABLE IF NOT EXISTS goal_creators (creator_id TEXT PRIMARY KEY, username TEXT, manager TEXT, manager_name TEXT, group_name TEXT, diamonds INTEGER, valid_live_days INTEGER, valid_live_hours REAL, estimated_bonus REAL, tier_status TEXT, rank_up_progress TEXT, activeness_level INTEGER, live_now INTEGER)",
        "CREATE TABLE IF NOT EXISTS goal_managers (manager TEXT PRIMARY KEY, manager_name TEXT, role TEXT, group_name TEXT, diamonds INTEGER, diamond_goal INTEGER, new_creators INTEGER, new_creator_goal INTEGER, managed_creators INTEGER)",
        "CREATE TABLE IF NOT EXISTS manager_performance (manager TEXT PRIMARY KEY, active_creators INTEGER, live_streams INTEGER, valid_live_creators INTEGER, live_hours REAL, creators_under_15h_pct REAL, diamonds INTEGER, diamond_goal INTEGER, diamond_change_pct REAL, period_start TEXT, period_end TEXT)",
        "CREATE TABLE IF NOT EXISTS data_updates (updated_at TEXT, source_file TEXT, creator_rows INTEGER)",
        "CREATE TABLE IF NOT EXISTS collector_runs (started_at TEXT, finished_at TEXT, status TEXT, detail TEXT, creator_rows INTEGER)",
        "CREATE TABLE IF NOT EXISTS scouting_records (source TEXT NOT NULL, username TEXT NOT NULL, followers DOUBLE PRECISION, likes DOUBLE PRECISION, applied_to_join BOOLEAN, scouting_status TEXT, live_streams DOUBLE PRECISION, diamonds DOUBLE PRECISION, live_hours DOUBLE PRECISION, avg_live_viewers DOUBLE PRECISION, invitation_type TEXT, assigned_manager TEXT, source_label TEXT, lead_expiry TEXT, captured_at TEXT, PRIMARY KEY (source, username))",
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def duration_hours(value):
    if pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    value = str(value)
    hours = re.search(r"([\d.]+)h", value)
    minutes = re.search(r"([\d.]+)m", value)
    seconds = re.search(r"([\d.]+)s", value)
    return (
        (float(hours.group(1)) if hours else 0)
        + (float(minutes.group(1)) / 60 if minutes else 0)
        + (float(seconds.group(1)) / 3600 if seconds else 0)
    )


def parse_export(file_or_path, engine):
    if isinstance(file_or_path, (str, Path)):
        filename = str(file_or_path).lower()
    else:
        filename = getattr(file_or_path, "name", "creator-data.xlsx").lower()
    if filename.endswith(".csv"):
        source = pd.read_csv(file_or_path, dtype=str)
    else:
        source = pd.read_excel(file_or_path, dtype=str)

    required = [
        "Creator ID",
        "Creator's username",
        "Creator Network manager",
        "Diamonds",
        "LIVE duration",
        "Valid go LIVE days",
        "Tier status",
    ]
    missing = [column for column in required if column not in source.columns]
    if missing:
        raise ValueError("Unsupported Backstage export; missing: " + ", ".join(missing))

    with engine.connect() as connection:
        current_managers = pd.read_sql(text("SELECT * FROM goal_managers"), connection)
    existing_names = current_managers.set_index("manager")["manager_name"].to_dict() if not current_managers.empty else {}
    existing_goals = current_managers.set_index("manager").to_dict("index") if not current_managers.empty else {}

    records = []
    for _, row in source.iterrows():
        username = "" if pd.isna(row["Creator's username"]) else str(row["Creator's username"]).strip()
        raw_id = "" if pd.isna(row["Creator ID"]) else str(row["Creator ID"]).split(".", 1)[0].strip()
        numeric_id = bool(re.fullmatch(r"\d{10,}", raw_id))
        if not username and numeric_id:
            username = raw_id
        if not username:
            continue
        creator_id = raw_id if numeric_id else f"missing:{username}"
        manager = "Unassigned" if pd.isna(row["Creator Network manager"]) else str(row["Creator Network manager"]).strip()
        if not manager or manager == "-":
            manager = "Unassigned"
        manager_name = existing_names.get(manager, manager.split("@", 1)[0] if manager != "Unassigned" else "Unassigned")
        group_name = "" if "Group" not in source.columns or pd.isna(row.get("Group")) else str(row.get("Group")).strip()
        diamonds = pd.to_numeric(row["Diamonds"], errors="coerce")
        days = pd.to_numeric(row["Valid go LIVE days"], errors="coerce")
        records.append(
            {
                "creator_id": creator_id,
                "username": username,
                "manager": manager,
                "manager_name": manager_name,
                "group_name": group_name,
                "diamonds": int(diamonds) if pd.notna(diamonds) else 0,
                "valid_live_days": int(days) if pd.notna(days) else 0,
                "valid_live_hours": duration_hours(row["LIVE duration"]),
                "estimated_bonus": 0.0,
                "tier_status": "" if pd.isna(row["Tier status"]) else str(row["Tier status"]).strip(),
                "rank_up_progress": "",
                "activeness_level": 0,
                "live_now": 0,
            }
        )

    creators = pd.DataFrame(records)
    if creators.empty:
        raise ValueError("The Backstage export did not contain any usable creator records")
    creators = creators.drop_duplicates(subset=["creator_id"], keep="last")
    managers = []
    for manager, group in creators[creators["manager"] != "Unassigned"].groupby("manager"):
        previous = existing_goals.get(manager, {})
        managers.append(
            {
                "manager": manager,
                "manager_name": previous.get("manager_name") or group["manager_name"].iloc[0],
                "role": previous.get("role", "Manager"),
                "group_name": previous.get("group_name") or next((value for value in group["group_name"] if value), ""),
                "diamonds": int(group["diamonds"].sum()),
                "diamond_goal": int(previous.get("diamond_goal", 0) or 0),
                "new_creators": int(previous.get("new_creators", 0) or 0),
                "new_creator_goal": int(previous.get("new_creator_goal", 0) or 0),
                "managed_creators": int(len(group)),
            }
        )
    return creators, pd.DataFrame(managers)


def replace_dashboard_data(engine, creators, managers, source_name):
    now = datetime.now(timezone.utc)
    performance = []
    for manager, group in creators[creators["manager"] != "Unassigned"].groupby("manager"):
        manager_goal = managers.loc[managers["manager"] == manager, "diamond_goal"]
        performance.append(
            {
                "manager": manager,
                "active_creators": int(len(group)),
                "live_streams": 0,
                "valid_live_creators": int((group["valid_live_days"] > 0).sum()),
                "live_hours": float(group["valid_live_hours"].sum()),
                "creators_under_15h_pct": float((group["valid_live_hours"] < 15).mean() * 100),
                "diamonds": int(group["diamonds"].sum()),
                "diamond_goal": int(manager_goal.iloc[0]) if not manager_goal.empty else 0,
                "diamond_change_pct": 0.0,
                "period_start": now.replace(day=1).date().isoformat(),
                "period_end": now.date().isoformat(),
            }
        )

    with engine.begin() as connection:
        connection.execute(text("DELETE FROM goal_creators"))
        connection.execute(text("DELETE FROM goal_managers"))
        connection.execute(text("DELETE FROM manager_performance"))
        if not creators.empty:
            connection.execute(
                text("INSERT INTO goal_creators (creator_id,username,manager,manager_name,group_name,diamonds,valid_live_days,valid_live_hours,estimated_bonus,tier_status,rank_up_progress,activeness_level,live_now) VALUES (:creator_id,:username,:manager,:manager_name,:group_name,:diamonds,:valid_live_days,:valid_live_hours,:estimated_bonus,:tier_status,:rank_up_progress,:activeness_level,:live_now)"),
                creators.to_dict("records"),
            )
        if not managers.empty:
            connection.execute(
                text("INSERT INTO goal_managers (manager,manager_name,role,group_name,diamonds,diamond_goal,new_creators,new_creator_goal,managed_creators) VALUES (:manager,:manager_name,:role,:group_name,:diamonds,:diamond_goal,:new_creators,:new_creator_goal,:managed_creators)"),
                managers.to_dict("records"),
            )
        if performance:
            connection.execute(
                text("INSERT INTO manager_performance (manager,active_creators,live_streams,valid_live_creators,live_hours,creators_under_15h_pct,diamonds,diamond_goal,diamond_change_pct,period_start,period_end) VALUES (:manager,:active_creators,:live_streams,:valid_live_creators,:live_hours,:creators_under_15h_pct,:diamonds,:diamond_goal,:diamond_change_pct,:period_start,:period_end)"),
                performance,
            )
        connection.execute(
            text("INSERT INTO data_updates VALUES (:updated_at,:source_file,:creator_rows)"),
            {"updated_at": now.isoformat(), "source_file": source_name, "creator_rows": int(len(creators))},
        )


def creator_data_url():
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    query = urlencode({"anchorID": "", "startTime": int(start.timestamp()), "endTime": int(now.timestamp())})
    override = os.environ.get("BACKSTAGE_CREATOR_DATA_URL", "").strip()
    return override or f"{DEFAULT_BACKSTAGE_URL}?{query}"


def download_export(destination: Path):
    state_b64 = os.environ.get("TIKTOK_STORAGE_STATE_B64", "").strip()
    if not state_b64:
        raise RuntimeError("TIKTOK_STORAGE_STATE_B64 is not configured")
    try:
        state = json.loads(base64.b64decode(state_b64).decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("TIKTOK_STORAGE_STATE_B64 is not valid") from exc

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(storage_state=state, accept_downloads=True)
        page = context.new_page()
        page.goto(creator_data_url(), wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(4_000)
        if "login" in page.url.lower() or "sign" in page.title().lower():
            raise RuntimeError("The saved Backstage session has expired; capture a new session")

        export = page.get_by_role("button", name=re.compile(r"export", re.I))
        if export.count() == 0:
            export = page.get_by_text(re.compile(r"^export$", re.I))
        if export.count() == 0:
            raise RuntimeError("Could not find the Backstage Export button")
        with page.expect_download(timeout=120_000) as download_info:
            export.first.click()
        download_info.value.save_as(destination)
        context.close()
        browser.close()


def log_run(engine, started_at, status, detail, creator_rows=0):
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO collector_runs VALUES (:started_at,:finished_at,:status,:detail,:creator_rows)"),
            {
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "status": status,
                "detail": detail[:1000],
                "creator_rows": int(creator_rows),
            },
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="Use an existing Backstage .xlsx/.csv instead of browser collection")
    args = parser.parse_args()
    started_at = datetime.now(timezone.utc).isoformat()
    engine = database_engine()
    ensure_schema(engine)
    temp_path = None
    try:
        if args.file:
            export_path = Path(args.file)
        else:
            handle = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
            handle.close()
            temp_path = Path(handle.name)
            download_export(temp_path)
            export_path = temp_path
        creators, managers = parse_export(export_path, engine)
        replace_dashboard_data(engine, creators, managers, export_path.name)
        log_run(engine, started_at, "success", "Backstage data updated", len(creators))
        print(f"Updated {len(creators)} creators across {len(managers)} managers")
    except Exception as exc:
        log_run(engine, started_at, "failed", str(exc))
        raise
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


if __name__ == "__main__":
    main()
