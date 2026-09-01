#!/usr/bin/env python3
"""Lock the final 7:59 PM ET Goal read and roll next month's diamond goals."""

from __future__ import annotations

import datetime as dt
import decimal
import os
from pathlib import Path

import psycopg


def database_url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if value:
        return value
    return Path("/home/graceharbourmedia/.config/creator-reader/database-url").read_text().strip()


def main() -> None:
    # The shell captures the cron start time before the reader begins. A run that
    # starts at 7:59 PM on the month's final day is the authoritative close.
    started = dt.datetime.fromisoformat(os.environ["GOAL_RUN_STARTED_ET"])
    next_day = started.date() + dt.timedelta(days=1)
    is_month_close = started.hour == 19 and started.minute == 59 and next_day.day == 1
    if not is_month_close:
        return

    with psycopg.connect(database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COALESCE(SUM(diamonds), 0) FROM goal_creators")
            baseline = decimal.Decimal(cursor.fetchone()[0] or 0)
            if baseline <= 0:
                raise RuntimeError("Refusing month rollover because the final Goal read has no diamonds")

            minimum = (baseline * decimal.Decimal("1.15")).quantize(
                decimal.Decimal("1"), rounding=decimal.ROUND_HALF_UP
            )
            total = (baseline * decimal.Decimal("1.15") * decimal.Decimal("1.30")).quantize(
                decimal.Decimal("1"), rounding=decimal.ROUND_HALF_UP
            )
            closed_month = started.strftime("%Y-%m")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS goal_month_end_snapshots (
                    month_key text PRIMARY KEY,
                    diamonds numeric NOT NULL,
                    minimum_goal numeric NOT NULL,
                    total_goal numeric NOT NULL,
                    captured_at timestamptz NOT NULL
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO goal_month_end_snapshots
                    (month_key, diamonds, minimum_goal, total_goal, captured_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (month_key) DO UPDATE SET
                    diamonds = EXCLUDED.diamonds,
                    minimum_goal = EXCLUDED.minimum_goal,
                    total_goal = EXCLUDED.total_goal,
                    captured_at = EXCLUDED.captured_at
                """,
                (closed_month, baseline, minimum, total),
            )
            rows = [
                ("prior_month_diamonds", str(baseline)),
                ("minimum_diamond_goal", str(minimum)),
                ("total_diamond_goal", str(total)),
            ]
            cursor.executemany(
                """
                INSERT INTO dashboard_monthly_metrics (metric_name, metric_value, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (metric_name) DO UPDATE SET
                    metric_value = EXCLUDED.metric_value,
                    updated_at = EXCLUDED.updated_at
                """,
                rows,
            )
        connection.commit()
    print(f"Month close {closed_month}: baseline={baseline}, minimum={minimum}, total={total}")


if __name__ == "__main__":
    main()
