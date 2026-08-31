#!/usr/bin/env python3
from datetime import datetime, timezone
from pathlib import Path

import psycopg


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS community_events (
        event_id TEXT PRIMARY KEY,
        event_name TEXT NOT NULL,
        start_at TEXT NOT NULL,
        end_at TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS community_event_participants (
        event_id TEXT NOT NULL,
        creator_id TEXT NOT NULL,
        username TEXT,
        manager TEXT,
        added_at TEXT NOT NULL,
        PRIMARY KEY (event_id, creator_id)
    )""",
    """CREATE TABLE IF NOT EXISTS community_event_snapshots (
        event_id TEXT NOT NULL,
        phase TEXT NOT NULL,
        creator_id TEXT NOT NULL,
        username TEXT,
        manager TEXT,
        diamonds INTEGER NOT NULL,
        captured_at TEXT NOT NULL,
        PRIMARY KEY (event_id, phase, creator_id)
    )""",
]


def snapshot(cursor, event_id: str, phase: str, captured_at: str) -> int:
    cursor.execute(
        "SELECT 1 FROM community_event_snapshots WHERE event_id = %s AND phase = %s LIMIT 1",
        (event_id, phase),
    )
    if cursor.fetchone():
        return 0
    cursor.execute(
        """
        INSERT INTO community_event_snapshots
            (event_id, phase, creator_id, username, manager, diamonds, captured_at)
        SELECT %s, %s, creator_id, username,
               COALESCE(NULLIF(manager_name, ''), manager, ''),
               COALESCE(diamonds, 0), %s
        FROM goal_creators
        ON CONFLICT (event_id, phase, creator_id) DO NOTHING
        """,
        (event_id, phase, captured_at),
    )
    return cursor.rowcount


def main() -> None:
    database_url = (Path.home() / ".config/creator-reader/database-url").read_text(encoding="utf-8").strip()
    now = datetime.now(timezone.utc)
    captured_at = now.isoformat()
    captured = []
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            for statement in SCHEMA:
                cursor.execute(statement)
            cursor.execute("SELECT event_id, event_name, start_at, end_at FROM community_events ORDER BY start_at")
            events = cursor.fetchall()
            for event_id, event_name, start_at, end_at in events:
                start_value = datetime.fromisoformat(str(start_at).replace("Z", "+00:00"))
                end_value = datetime.fromisoformat(str(end_at).replace("Z", "+00:00"))
                if start_value.tzinfo is None:
                    start_value = start_value.replace(tzinfo=timezone.utc)
                if end_value.tzinfo is None:
                    end_value = end_value.replace(tzinfo=timezone.utc)
                if now >= start_value:
                    rows = snapshot(cursor, event_id, "start", captured_at)
                    if rows:
                        captured.append(f"{event_name}: start ({rows} creators)")
                    cursor.execute(
                        "UPDATE community_events SET status = %s WHERE event_id = %s",
                        ("completed" if now >= end_value else "live", event_id),
                    )
                if now >= end_value:
                    rows = snapshot(cursor, event_id, "end", captured_at)
                    if rows:
                        captured.append(f"{event_name}: end ({rows} creators)")
                    cursor.execute("UPDATE community_events SET status = 'completed' WHERE event_id = %s", (event_id,))
    print("; ".join(captured) if captured else "No event snapshots due.")


if __name__ == "__main__":
    main()
