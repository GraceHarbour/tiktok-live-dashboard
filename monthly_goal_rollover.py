#!/usr/bin/env python3
"""Lock the final 7:59 PM ET Goal read and roll next month's diamond goals."""

from __future__ import annotations

import datetime as dt
import decimal
import os
from pathlib import Path

import psycopg


MILESTONES = [
    (5_000_000, 15, 30, "TikTok Universe", 44_999),
    (2_000_000, 15, 30, "TikTok Stars", 39_999),
    (1_500_000, 15, 30, "Dragon Flame", 26_999),
    (1_000_000, 15, 30, "Adam's Dream", 25_999),
    (500_000, 10, 20, "Interstellar", 10_000),
    (300_000, 8, 20, "Leon the Kitten", 4_888),
    (150_000, 8, 20, "Motorcycle", 2_988),
]


def tier_eligible(tier_status: object, rank_progress: object) -> bool:
    value = f"{tier_status or ''} {rank_progress or ''}".casefold()
    if "not maintain" in value or "not maintained" in value:
        return any(token in value for token in ("ranked up", "ranking up", "rank up"))
    return any(token in value for token in ("maintained", "maintaining", "maintain", "ranked up", "ranking up", "rank up"))


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
                CREATE TABLE IF NOT EXISTS monthly_reward_results (
                    month_key text NOT NULL,
                    creator_id text NOT NULL,
                    username text NOT NULL,
                    manager_name text NOT NULL DEFAULT '',
                    diamonds bigint NOT NULL DEFAULT 0,
                    valid_live_days integer NOT NULL DEFAULT 0,
                    valid_live_hours numeric NOT NULL DEFAULT 0,
                    maintained_or_ranked boolean NOT NULL DEFAULT false,
                    reward_name text NOT NULL DEFAULT '',
                    reward_value bigint NOT NULL DEFAULT 0,
                    qualified_milestone bigint NOT NULL DEFAULT 0,
                    eligible boolean NOT NULL DEFAULT false,
                    disqualification_reason text NOT NULL DEFAULT '',
                    captured_at timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (month_key, creator_id)
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
            cursor.execute(
                """
                SELECT creator_id, username, COALESCE(manager_name, manager, ''),
                       COALESCE(diamonds, 0), COALESCE(valid_live_days, 0),
                       COALESCE(valid_live_hours, 0), tier_status, rank_up_progress
                FROM goal_creators
                """
            )
            reward_rows = []
            for creator_id, username, manager, diamonds, days, hours, tier_status, rank_progress in cursor.fetchall():
                reached = next((item for item in MILESTONES if int(diamonds) >= item[0]), None)
                if not reached:
                    continue
                milestone, days_needed, hours_needed, reward_name, reward_value = reached
                maintained = tier_eligible(tier_status, rank_progress)
                reasons = []
                if not maintained:
                    reasons.append("Did not maintain or rank up tier")
                if int(days) < days_needed:
                    reasons.append(f"Needs {days_needed} valid LIVE days")
                if float(hours) < hours_needed:
                    reasons.append(f"Needs {hours_needed} valid LIVE hours")
                reward_rows.append((
                    closed_month, str(creator_id), str(username or creator_id), str(manager or "Unassigned"),
                    int(diamonds), int(days), float(hours), maintained, reward_name, reward_value,
                    milestone, not reasons, "; ".join(reasons),
                ))
            cursor.execute("DELETE FROM monthly_reward_results WHERE month_key = %s", (closed_month,))
            cursor.executemany(
                """
                INSERT INTO monthly_reward_results
                    (month_key,creator_id,username,manager_name,diamonds,valid_live_days,
                     valid_live_hours,maintained_or_ranked,reward_name,reward_value,
                     qualified_milestone,eligible,disqualification_reason)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                reward_rows,
            )
        connection.commit()
    print(f"Month close {closed_month}: baseline={baseline}, minimum={minimum}, total={total}")


if __name__ == "__main__":
    main()
