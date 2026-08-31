#!/usr/bin/env python3
"""Capture every Maintenance Rate creator record from the authorized Firefox session."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from pathlib import Path

from firefox_bidi_capture import Bidi, click_next_page, evaluate

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "maintenance-rate-firefox-capture.json"
TEMP = ROOT / "data" / "maintenance-rate-firefox-capture.partial.json"
PREVIOUS = ROOT / "data" / "maintenance-rate-firefox-capture.previous.json"
DEFAULT_MAINTENANCE_URL = "https://live-backstage.tiktok.com/portal/revenue/task?Month=202608&PreviewJobID=7679378715277967374&SettleJobID=7679589621419900942&SettleSubJobID=7679589621419917326&TaskID=7605816519033618488&subViewTab=LastMonthInAgencyAndGraduatedHostCnt&viewTab=by_creator"
def maintenance_url():
    raw = os.environ.get("MAINTENANCE_RATE_URL", "").strip() or DEFAULT_MAINTENANCE_URL
    parts = urlsplit(raw)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    params["Month"] = datetime.now(timezone.utc).strftime("%Y%m")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))


def body(bidi, context):
    return str(evaluate(bidi, context, "document.body ? document.body.innerText : ''"))

def page_range(text):
    match = re.search(r"Showing\s+(\d+)\s*(?:to|-)\s*(\d+)\s+of\s+(\d+)", text, re.I)
    return tuple(map(int, match.groups())) if match else None

def creator_grid(bidi, context):
    code = """(() => {
      const table = [...document.querySelectorAll('table')].find(t => {
        const text = t.innerText || '';
        return text.includes('Estimated bonus contribution') && text.includes('Creator');
      });
      if (!table) return JSON.stringify({header:'', rows:[], cells:[]});
      return JSON.stringify({
        header: table.querySelector('thead')?.innerText || '',
        rows: [...table.querySelectorAll('tbody tr')].map(row => row.innerText || '').filter(Boolean),
        cells: [...table.querySelectorAll('tbody tr')].map(row => [...row.querySelectorAll('td')].map(cell => cell.innerText || ''))
      });
    })()"""
    return json.loads(str(evaluate(bidi, context, code)))

def wait_for_creator_page(bidi, context, previous=None, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        text = body(bidi, context)
        showing = page_range(text)
        grid = creator_grid(bidi, context)
        rows = grid["rows"]
        expected = (showing[1] - showing[0] + 1) if showing else 0
        first = rows[0] if rows else ""
        if showing and len(rows) == expected:
            if previous is None or (showing[0] > previous[0][0] and first != previous[1]):
                return text, showing, grid, first
        time.sleep(1)
    raise RuntimeError("Maintenance Rate creator grid did not finish loading a verified page.")

def capture_all(bidi, context):
    text, showing, grid, first = wait_for_creator_page(bidi, context)
    pages = []
    while True:
        start, end, total = showing
        pages.append({"showing": showing, "text": text, "header": grid["header"], "rows": grid["rows"]})
        if end >= total:
            break
        if not click_next_page(bidi, context, total):
            raise RuntimeError(f"Could not advance Maintenance Rate after {end}/{total}.")
        text, showing, grid, first = wait_for_creator_page(bidi, context, (showing, first))
    starts = [page["showing"][0] for page in pages]
    actual = sum(len(page["rows"]) for page in pages)
    if not starts or len(starts) != len(set(starts)) or starts[0] != 1 or pages[-1]["showing"][1] != pages[-1]["showing"][2] or actual != pages[-1]["showing"][2]:
        raise RuntimeError(f"Maintenance Rate page validation failed: starts={starts}, rows={actual}")
    return {"expected": pages[-1]["showing"][2], "pages": pages}

def main():
    TEMP.unlink(missing_ok=True)
    bidi = Bidi()
    try:
        bidi.call("session.new", {"capabilities":{"alwaysMatch":{}}})
        contexts = bidi.call("browsingContext.getTree", {}).get("contexts", [])
        if not contexts:
            raise RuntimeError("Firefox did not provide the authorized Backstage page.")
        context = contexts[0]["context"]
        bidi.call("browsingContext.navigate", {"context":context, "url":maintenance_url(), "wait":"complete"})
        result = {"source":"maintenance_rate_firefox", "captured_at":datetime.now(timezone.utc).isoformat(), "url":maintenance_url(), "maintenance_rate":capture_all(bidi, context)}
        TEMP.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        if OUT.exists():
            OUT.replace(PREVIOUS)
        TEMP.replace(OUT)
        print(f"VALIDATED Maintenance Rate: {result['maintenance_rate']['expected']} creator rows across {len(result['maintenance_rate']['pages'])} pages")
    finally:
        try:
            bidi.call("session.end")
        except Exception:
            pass
        bidi.close()

if __name__ == "__main__":
    main()
