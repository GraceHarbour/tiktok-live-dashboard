"""Refresh small live metrics used by the hourly Goals dashboard cards."""

from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timezone

import psycopg
from playwright.sync_api import sync_playwright


CREATOR_LIST_URL = "https://live-backstage.tiktok.com/portal/anchor/list"
BUSINESS_ESSENTIALS_URL = "https://live-backstage.tiktok.com/portal/revenue/business-essentials"


def active_creator_count(page) -> int:
    page.goto(CREATOR_LIST_URL, wait_until="domcontentloaded", timeout=90_000)
    tab = page.get_by_role("tab", name=re.compile(r"All creators", re.I)).first
    tab.wait_for(state="visible", timeout=90_000)
    label = tab.inner_text().strip()
    match = re.search(r"All creators\s*\(([\d,]+)\)", label, re.I)
    if not match:
        raise RuntimeError(f"Could not read the All creators count from {label!r}")
    count = int(match.group(1).replace(",", ""))
    if count <= 0:
        raise RuntimeError("Refusing to store an empty active-creator count")
    return count


def maintenance_rate(page) -> float:
    page.goto(BUSINESS_ESSENTIALS_URL, wait_until="domcontentloaded", timeout=90_000)
    page.get_by_text("Mature creator rank-up and maintenance rate this month", exact=False).first.wait_for(
        state="visible", timeout=90_000
    )
    body = page.locator("body").inner_text()
    match = re.search(
        r"Mature creator rank-up and maintenance rate this month.{0,500}?"
        r"([\d.]+)\s*%\s*/\s*50\s*%",
        body,
        re.I | re.S,
    )
    if not match:
        raise RuntimeError("Could not read the current maintenance rate from Business Essentials")
    rate = float(match.group(1))
    if rate < 0 or rate > 100:
        raise RuntimeError(f"Refusing invalid maintenance rate {rate}")
    return rate


def main() -> None:
    database_url = os.environ["DATABASE_URL"].strip()
    state = json.loads(base64.b64decode(os.environ["TIKTOK_STORAGE_STATE_B64"]).decode("utf-8"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(storage_state=state, viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        count = active_creator_count(page)
        rate = maintenance_rate(page)
        context.close()
        browser.close()

    captured_at = datetime.now(timezone.utc).isoformat()
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO dashboard_monthly_metrics (metric_name, metric_value, updated_at)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (metric_name) DO UPDATE
                   SET metric_value = EXCLUDED.metric_value, updated_at = EXCLUDED.updated_at""",
                [
                    ("active_creators", count, captured_at),
                    ("maintenance_rate", rate, captured_at),
                ],
            )
    print(f"Updated active_creators={count}, maintenance_rate={rate:.2f}%")


if __name__ == "__main__":
    main()
