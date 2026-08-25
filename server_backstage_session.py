"""Interactive, user-controlled reader for pages visible in Creator Network Backstage."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import hmac
from pathlib import Path
import json
import os

import requests

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


SOURCE_URLS = {
    "managers": "https://live-backstage.tiktok.com/portal/administration/permission",
    "creators": "https://live-backstage.tiktok.com/portal/anchor/list",
    "goals": "https://live-backstage.tiktok.com/portal/administration/term-target-management?tab=agent&month={month}",
    "business_essentials": "https://live-backstage.tiktok.com/portal/revenue/business-essentials",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Open Backstage for manual sign-in, then capture visible text from the current page."
    )
    parser.add_argument("--url", default="https://live-backstage.tiktok.com/")
    parser.add_argument(
        "--source",
        choices=sorted(SOURCE_URLS),
        help="Open a supported Backstage data page directly after the authorized session is available.",
    )
    parser.add_argument(
        "--goal-view",
        choices=["managers", "creators"],
        default="managers",
        help="For goals, capture either the Manager or Creator monthly view.",
    )
    parser.add_argument("--section", default="backstage", help="A label, such as administration or scout-creator.")
    parser.add_argument("--save", action="store_true", help="Save the visible text locally under data/.")
    parser.add_argument(
        "--save-visible-text",
        action="store_true",
        help="Also save the full visible page text locally. This is off by default because it can contain more data than the dashboard needs.",
    )
    parser.add_argument(
        "--keep-session",
        action="store_true",
        help="Reuse a local browser session until Backstage expires or signs it out.",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        help="Use this existing browser profile directory. The user signs in to this profile directly.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the authorized reader without displaying the browser window.",
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Do not pause for terminal input. Never use this option to automate sign-in.",
    )
    parser.add_argument(
        "--every-minutes",
        type=int,
        default=0,
        help="Capture again at this interval; for example, 15. Keeps the browser open.",
    )
    parser.add_argument(
        "--publish-url",
        help="Private dashboard endpoint that accepts signed snapshots. This never sends browser credentials or cookies.",
    )
    parser.add_argument(
        "--sync-secret-file",
        type=Path,
        help="Path to the worker's HMAC secret file. Required with --publish-url.",
    )
    parser.add_argument(
        "--iap-audience",
        help="IAP OAuth client ID used to obtain a service-account identity token for the private endpoint.",
    )
    args = parser.parse_args()
    if args.every_minutes and not args.keep_session:
        parser.error("--every-minutes requires --keep-session.")
    if args.every_minutes < 0:
        parser.error("--every-minutes must be positive.")
    if bool(args.publish_url) != bool(args.sync_secret_file):
        parser.error("--publish-url and --sync-secret-file must be used together.")

    if args.no_prompt and not args.keep_session:
        parser.error("--no-prompt requires --keep-session so the reader can use a user-authorized browser profile.")

    with sync_playwright() as playwright:
        if args.keep_session:
            profile = args.profile_dir or Path(os.environ.get("BACKSTAGE_BROWSER_PROFILE", "data/backstage-browser-profile"))
            context = playwright.chromium.launch_persistent_context(
                str(profile), headless=args.headless, viewport={"width": 1440, "height": 1000}
            )
        else:
            browser = playwright.chromium.launch(headless=args.headless)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.pages[0] if context.pages else context.new_page()
        target_url = SOURCE_URLS.get(args.source, args.url)
        target_url = target_url.format(month=datetime.now().strftime("%Y%m"))
        if args.source == "goals" and args.goal_view == "creators":
            # Backstage's Creator view has its own URL state. Opening it directly
            # avoids landing back on the Manager grid before the read begins.
            target_url = target_url.replace("tab=agent", "tab=creator")
        page.goto(target_url, wait_until="domcontentloaded")
        if not args.no_prompt:
            input(
                "If Backstage asks, sign in yourself and complete any MFA/CAPTCHA. Then navigate to the exact section to read. "
                "Return here and press Enter when the data is visible. "
            )
        while True:
            snapshot = None
            if "/login" in page.url:
                raise RuntimeError("Backstage sign-in is required. The reader will not sign in automatically.")
            if args.source == "goals" and args.goal_view == "managers":
                goal_tab = "Managers"
                for attempt in range(3):
                    try:
                        page.get_by_role("tab", name=goal_tab).click(timeout=15_000)
                        break
                    except PlaywrightTimeoutError:
                        # The current Backstage screen exposes these as visible controls,
                        # rather than ARIA tabs, in some authorized sessions.
                        try:
                            page.get_by_text(goal_tab, exact=True).click(timeout=5_000)
                            break
                        except PlaywrightTimeoutError:
                            pass
                        if attempt == 2:
                            raise
                        page.wait_for_timeout(2_000)
                page.wait_for_timeout(500)
            elif args.source == "goals" and args.goal_view == "creators":
                # The manager grid remains in the DOM while the Creator view loads.
                # Wait for the visible Creator grid rather than capturing that stale grid.
                creator_grid_ready = False
                for _ in range(45):
                    for candidate in page.locator(
                        '[role="grid"], [role="treegrid"], [role="table"], table, '
                        '[class*="table"], [class*="Table"]'
                    ).all():
                        candidate_headers = [
                            text.strip().lower()
                            for text in candidate.locator(
                                '[role="columnheader"], th, [class*="header"], [class*="Header"]'
                            ).all_inner_texts()
                        ]
                        candidate_text = candidate.inner_text()
                        if (
                            any(header == "creator" or header.startswith("creator ") for header in candidate_headers)
                            or ("\nCreator\n" in candidate_text and "\nManager\n" in candidate_text and "\nDiamonds" in candidate_text)
                        ):
                            creator_grid_ready = True
                            break
                    if creator_grid_ready:
                        break
                    page.wait_for_timeout(1_000)
                if not creator_grid_ready:
                    raise RuntimeError("The Backstage Creator table did not finish loading before capture.")
            page.wait_for_timeout(1000)
            visible_text = page.locator("body").inner_text(timeout=15_000)

            if args.save:
                safe_section = "".join(character if character.isalnum() or character in "-_" else "-" for character in args.section.lower())
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                if args.source:
                    output = Path("data") / f"backstage-{safe_section}-{timestamp}.json"
                    output.parent.mkdir(parents=True, exist_ok=True)
                    snapshot = capture_grid(page, args.source, args.goal_view, visible_text)
                    output.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
                    print(f"Captured {len(snapshot['rows'])} structured {args.source} rows.")
                    if args.save_visible_text:
                        text_output = output.with_suffix(".txt")
                        text_output.write_text(visible_text, encoding="utf-8")
                        print(f"Saved full page text locally to {text_output}.")
                elif args.save_visible_text:
                    output = Path("data") / f"backstage-visible-{safe_section}-{timestamp}.txt"
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(visible_text, encoding="utf-8")
                    print(f"Saved full page text locally to {output}.")
            elif args.source:
                snapshot = capture_grid(page, args.source, args.goal_view, visible_text)
            else:
                snapshot = None

            if snapshot and args.publish_url and args.sync_secret_file:
                publish_snapshot(
                    snapshot,
                    url=args.publish_url,
                    secret=args.sync_secret_file.read_text(encoding="utf-8").strip(),
                    iap_audience=args.iap_audience,
                )
                print("Sent the signed capture to the private dashboard.")
            elif snapshot:
                print(f"Captured {len(snapshot['rows'])} structured {args.source} rows.")

            if not args.every_minutes:
                break
            print(f"\nNext capture in {args.every_minutes} minutes. Leave this window and browser open.")
            page.wait_for_timeout(args.every_minutes * 60 * 1000)
            page.reload(wait_until="domcontentloaded")

        if not args.no_prompt:
            input("Press Enter to close the browser. ")
        context.close()
    return 0


def capture_grid(page, source: str, goal_view: str, visible_text: str) -> dict[str, object]:
    """Extract the currently rendered Backstage grid without editing any portal data."""
    grids = page.locator(
        '[role="grid"], [role="treegrid"], [role="table"], table, '
        '[class*="table"], [class*="Table"]'
    ).all()
    if not grids:
        raise RuntimeError("The Backstage table is not available for capture.")
    grid = grids[0]
    if source == "goals" and goal_view == "creators":
        # The Manager grid can remain mounted but hidden after switching views.
        # Prefer the grid whose headers identify individual creators.
        for candidate in grids:
            try:
                candidate_headers = [
                    text.strip().lower()
                    for text in candidate.locator(
                        '[role="columnheader"], th, [class*="header"], [class*="Header"]'
                    ).all_inner_texts()
                ]
                candidate_text = candidate.inner_text()
            except Exception:
                continue
            if any("creator" in header for header in candidate_headers):
                grid = candidate
                break
            # This Backstage variant exposes the visible Creator header as plain
            # text in a styled container rather than as an ARIA/table header.
            if "\nCreator\n" in candidate_text and "\nDiamonds" in candidate_text:
                grid = candidate
                break
    headers = [
        text.strip()
        for text in grid.locator('[role="columnheader"], th, [class*="header"], [class*="Header"]').all_inner_texts()
    ]
    if source == "goals" and goal_view == "creators" and not any("creator" in header.lower() for header in headers):
        # Use only the columns shown in the Creator view. Do not invent a
        # manager, email, or ID column that Backstage did not present.
        headers = [
            "Creator", "Diamonds", "Valid go LIVE days", "Valid LIVE duration",
            "Bonus contribution", "Tier", "Activeness",
        ]
    def read_current_page() -> list[list[str]]:
        current_rows: list[list[str]] = []
        for row in grid.locator('[role="row"], tr, [class*="row"], [class*="Row"]').all():
            cells = [
                text.strip()
                for text in row.locator('[role="gridcell"], td, [class*="cell"], [class*="Cell"]').all_inner_texts()
            ]
            if not cells and source == "goals" and goal_view == "creators":
                cells = [line.strip() for line in row.inner_text().splitlines() if line.strip()]
            if cells:
                current_rows.append(cells)
        return current_rows

    rows: list[list[str]] = []
    seen_rows: set[tuple[str, ...]] = set()

    def add_rows(current_rows: list[list[str]]) -> None:
        for row in current_rows:
            key = tuple(row)
            if key and key not in seen_rows:
                seen_rows.add(key)
                rows.append(row)

    add_rows(read_current_page())

    # The Creator goal grid normally shows 10 creators per page. Pagination is
    # read-only, so walk each page and de-duplicate rows without clicking any
    # edit, export, or portal-changing control. If Backstage changes its
    # pagination markup, this safely keeps the first visible page instead of
    # guessing at a control.
    if source == "goals" and goal_view == "creators":
        for _ in range(99):
            next_button = None
            for selector in (
                "button[aria-label*='next' i]",
                "[role='button'][aria-label*='next' i]",
                "button[title*='next' i]",
                "[role='button'][title*='next' i]",
                "button:has-text('Next')",
                "[role='button']:has-text('Next')",
            ):
                for candidate in page.locator(selector).all():
                    disabled = (candidate.get_attribute("disabled") is not None
                                or candidate.get_attribute("aria-disabled") == "true"
                                or "disabled" in (candidate.get_attribute("class") or "").casefold())
                    if not disabled:
                        next_button = candidate
                        break
                if next_button:
                    break
            if next_button is None:
                break
            before = tuple(tuple(row) for row in read_current_page())
            try:
                next_button.click(timeout=5_000)
            except Exception:
                break
            changed_rows: list[list[str]] | None = None
            for _ in range(20):
                page.wait_for_timeout(500)
                candidate_rows = read_current_page()
                if tuple(tuple(row) for row in candidate_rows) != before:
                    changed_rows = candidate_rows
                    break
            if changed_rows is None:
                break
            add_rows(changed_rows)
    return {
        "source": source,
        "view": goal_view if source == "goals" else None,
        "month": datetime.now().strftime("%Y%m"),
        "captured_at": datetime.now().astimezone().isoformat(),
        "headers": headers,
        "rows": rows,
    }


def publish_snapshot(snapshot: dict[str, object], *, url: str, secret: str, iap_audience: str | None) -> None:
    """Send data to the private dashboard using a worker identity and HMAC signature."""
    if not secret:
        raise ValueError("The sync secret file is empty.")
    body = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    timestamp = datetime.now().astimezone().isoformat()
    signature = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Creator-Timestamp": timestamp,
        "X-Creator-Signature": signature,
    }
    if iap_audience:
        from google.auth.transport.requests import Request
        from google.oauth2.id_token import fetch_id_token

        headers["Authorization"] = f"Bearer {fetch_id_token(Request(), iap_audience)}"
    response = requests.post(url, data=body, headers=headers, timeout=30)
    response.raise_for_status()


if __name__ == "__main__":
    raise SystemExit(main())
