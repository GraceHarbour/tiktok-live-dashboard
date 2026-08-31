#!/usr/bin/env python3
import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from backstage_session import publish_snapshot
from firefox_bidi_capture import Bidi, click_next_page, evaluate, page_range


URLS = {
    "scouting_to_follow": "https://live-backstage.tiktok.com/portal/anchor/scout-creators?agent=%255B%255D",
    "scouting_applied": "https://live-backstage.tiktok.com/portal/anchor/scout-creators?agent=%255B%255D",
    "scouting_invited": "https://live-backstage.tiktok.com/portal/anchor/scout-creators?leadStage=3&agent=%255B%255D",
}
HEADERS = {
    "scouting_to_follow": ["Creator", "Applied to join", "Last 30 days data", "Assigned to", "Source", "Leads expire in", "Leads added", "Action"],
    "scouting_applied": ["Creator", "Applied to join", "Last 30 days data", "Assigned to", "Source", "Leads expire in", "Leads added", "Action"],
    "scouting_invited": ["Creator", "Scouting status", "Last 30 days data", "Invitation type", "Assigned to", "Source", "Leads expire in", "Invited at", "Action"],
}


def table_rows(browser, context):
    raw = str(evaluate(browser, context, "JSON.stringify(Array.from(document.querySelectorAll('table tbody tr')).map(row=>Array.from(row.children).map(cell=>(cell.innerText||'').trim())))"))
    return [row for row in json.loads(raw) if any(str(cell).strip() for cell in row)]


def body_text(browser, context):
    return str(evaluate(browser, context, "document.body ? document.body.innerText : ''"))


def clear_assigned_filter(browser, context):
    """Remove TikTok's persisted manager filter and verify it is empty."""
    return bool(evaluate(browser, context, """(() => {
        const assigned = Array.from(document.querySelectorAll('[role="combobox"]'))
            .find(node => (node.innerText || '').includes('Assigned to'));
        if (!assigned) return false;
        const selected = assigned.querySelector('.semi-select-selection-text');
        const selectedText = (selected && selected.innerText || '').trim();
        if (!selectedText || !selectedText.includes('@')) return true;
        const clear = assigned.querySelector('img[alt="clear"], [aria-label="clear"]');
        if (clear) { clear.click(); return true; }
        assigned.click();
        return false;
    })()"""))


def check_applied_filter(browser, context):
    """Select Applied on the To follow source without touching other filters."""
    return str(evaluate(browser, context, """(() => {
        const checkbox = Array.from(document.querySelectorAll('input[type="checkbox"]')).find(node => {
            const label = document.getElementById(node.getAttribute('aria-labelledby') || '');
            const nearby = node.parentElement && node.parentElement.innerText || '';
            return (label && label.innerText || '').trim() === 'Applied' || nearby.trim() === 'Applied';
        });
        if (!checkbox) return 'missing';
        if (checkbox.getAttribute('aria-checked') === 'true' || checkbox.checked) return 'checked';
        checkbox.click();
        return 'clicked';
    })()"""))


def select_list_view(browser, context):
    """Use TikTok's right-hand List view so all required fields are present."""
    return str(evaluate(browser, context, """(() => {
        const radios = Array.from(document.querySelectorAll('input[type="radio"], [role="radio"]'));
        if (radios.length < 2) return 'missing';
        const listView = radios[radios.length - 1];
        if (listView.getAttribute('aria-checked') === 'true' || listView.checked) return 'checked';
        listView.click();
        return 'clicked';
    })()"""))


def prepare_to_follow(browser, context):
    """Apply the required read setup in order and verify every step."""
    deadline = time.time() + 25
    while time.time() < deadline:
        state = check_applied_filter(browser, context)
        if state == "clicked":
            time.sleep(0.5)
            continue
        if state != "checked":
            time.sleep(0.5)
            continue
        if not clear_assigned_filter(browser, context):
            time.sleep(0.5)
            continue
        list_state = select_list_view(browser, context)
        if list_state == "clicked":
            time.sleep(0.5)
            continue
        if list_state != "checked":
            time.sleep(0.5)
            continue
        time.sleep(1)
        return
    raise RuntimeError("Could not verify Applied, empty Assigned to, and List view.")


def applied_rows(text):
    lines = [line.strip() for line in text.splitlines() if line.strip() and line.strip() != "·"]
    rows = []
    for follower_index, line in enumerate(lines):
        if "follower" not in line.lower() or follower_index == 0:
            continue
        username = lines[follower_index - 1]
        if username in {"Creator", "Scout creators"}:
            continue
        end = next((n for n in range(follower_index + 1, len(lines)) if lines[n].startswith("Showing ") or "follower" in lines[n].lower()), len(lines))
        block = lines[follower_index - 1:end]
        application_type = next(
            (value for value in block if "premium" in value.casefold() or "not available" in value.casefold()),
            "",
        )
        followers = line
        likes = next((value for value in block[2:7] if "like" in value.lower()), "")
        manager = ""
        if "Assigned to" in block:
            assigned = block.index("Assigned to")
            if assigned + 1 < len(block):
                manager = block[assigned + 1]
        values = {}
        for label in ("LIVE streams", "Diamonds", "h", "Avg. LIVE viewers"):
            if label in block:
                label_index = block.index(label)
                values[label] = block[label_index - 1] if label_index else "0"
        metrics = "\n".join((
            values.get("LIVE streams", "0"), "LIVE streams",
            values.get("Diamonds", "0"), "Diamonds",
            values.get("h", "0"), "h",
            values.get("Avg. LIVE viewers", "0"), "Avg. LIVE viewers",
        ))
        rows.append([f"{username}\n{followers}\n{likes}", application_type, metrics, manager, "", "", "", ""])
    return rows


def applied_page_rows(browser, context):
    collected = {}
    for ratio in (0, 0.2, 0.4, 0.6, 0.8, 1):
        evaluate(browser, context, f"""(() => {{
            const nodes = [document.scrollingElement, ...document.querySelectorAll('*')];
            for (const node of nodes) {{
                if (node && node.scrollHeight > node.clientHeight + 40) {{
                    node.scrollTop = (node.scrollHeight - node.clientHeight) * {ratio};
                }}
            }}
            return true;
        }})()""")
        time.sleep(0.6)
        for row in applied_rows(body_text(browser, context)):
            collected[row[0].splitlines()[0]] = row
    return list(collected.values())


def read_rows(browser, context, source):
    return applied_page_rows(browser, context) if source in {"scouting_applied", "scouting_to_follow"} else table_rows(browser, context)


def capture(source):
    browser = Bidi()
    try:
        browser.call("session.new", {"capabilities": {"alwaysMatch": {}}})
        context = browser.call("browsingContext.getTree", {})["contexts"][0]["context"]
        browser.call("browsingContext.navigate", {"context": context, "url": URLS[source], "wait": "complete"})
        if source in {"scouting_applied", "scouting_to_follow"}:
            prepare_to_follow(browser, context)
        deadline = time.time() + 60
        rows = []
        while time.time() < deadline:
            rows = read_rows(browser, context, source)
            if rows:
                break
            time.sleep(2)
        if not rows:
            raise RuntimeError("The Scouting table did not finish loading.")
        all_rows = []
        seen = set()
        for _ in range(200):
            rows = read_rows(browser, context, source)
            for row in rows:
                key = tuple(row)
                if key and key not in seen:
                    seen.add(key)
                    all_rows.append(row)
            status = page_range(body_text(browser, context))
            if not status or status[1] >= status[2]:
                break
            current_start = status[0]
            if not click_next_page(browser, context):
                raise RuntimeError("Could not advance Scouting pagination.")
            wait = time.time() + 25
            while time.time() < wait:
                time.sleep(1)
                next_status = page_range(body_text(browser, context))
                if next_status and next_status[0] > current_start:
                    break
            else:
                raise RuntimeError("Scouting pagination did not load the next page.")
        return {"source": source, "month": datetime.now().strftime("%Y%m"), "captured_at": datetime.now().astimezone().isoformat(), "headers": HEADERS[source], "rows": all_rows}
    finally:
        try:
            browser.call("session.end")
        except Exception:
            pass
        browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=URLS, required=True)
    parser.add_argument("--publish-url", required=True)
    parser.add_argument("--sync-secret-file", type=Path, required=True)
    parser.add_argument("--iap-audience")
    args = parser.parse_args()
    snapshot = capture(args.source)
    publish_snapshot(snapshot, url=args.publish_url, secret=args.sync_secret_file.read_text().strip(), iap_audience=args.iap_audience)
    print(f"Sent {len(snapshot['rows'])} {args.source} rows.")


if __name__ == "__main__":
    main()
