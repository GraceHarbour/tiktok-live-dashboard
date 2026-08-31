#!/usr/bin/env python3
import json, re, shutil, time
from datetime import datetime, timezone
from pathlib import Path
from firefox_bidi_capture import Bidi, evaluate, click_next_page

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data/business-firefox-capture.json"
TEMP = ROOT / "data/business-firefox-capture.partial.json"
PREVIOUS = ROOT / "data/business-firefox-capture.previous.json"
BUSINESS_URL = "https://live-backstage.tiktok.com/portal/revenue/business-essentials"

def body(bidi, context):
    return str(evaluate(bidi, context, "document.body ? document.body.innerText : ''"))

def page_range(text):
    match = re.search(r"Showing\s+(\d+)\s*(?:to|-)\s*(\d+)\s+of\s+(\d+)", text, re.I)
    return tuple(map(int, match.groups())) if match else None

def table_rows(bidi, context):
    raw = str(evaluate(bidi, context, "JSON.stringify([...document.querySelectorAll('table tr')].map(r => r.innerText || ''))"))
    return [row for row in json.loads(raw) if row.strip()]

def click_selector(bidi, context, selector):
    code = "(()=>{const e=document.querySelector(" + json.dumps(selector) + "); if(!e) return 'missing'; e.click(); return 'clicked'})()"
    return str(evaluate(bidi, context, code))

def click_tab(bidi, context, label):
    code = "(()=>{const label=" + json.dumps(label) + "; const e=[...document.querySelectorAll('[role=tab]')].find(x=>(x.innerText||'').trim()===label); if(!e) return 'missing'; e.click(); return 'clicked'})()"
    return str(evaluate(bidi, context, code))

def listed_tabs(bidi, context):
    raw = str(evaluate(bidi, context, "JSON.stringify([...document.querySelectorAll('[role=tab]')].map(t=>(t.innerText||'').trim()).filter(Boolean))"))
    return list(dict.fromkeys(json.loads(raw)))

def wait_for_table(bidi, context, previous=None, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        text = body(bidi, context)
        showing = page_range(text)
        rows = table_rows(bidi, context)
        first = rows[1] if len(rows) > 1 else ""
        if showing and first and len(rows) - 1 == showing[1] - showing[0] + 1:
            if previous is None or (showing[0] > previous[0][0] and first != previous[1]):
                return text, showing, rows, first
        time.sleep(1)
    raise RuntimeError("Business Essentials table did not finish loading a verified page.")

def capture_current(bidi, context, label):
    text, showing, rows, first = wait_for_table(bidi, context)
    pages = []
    while True:
        start, end, total = showing
        pages.append({"showing": showing, "text": text, "table_rows": rows})
        if end >= total:
            break
        if not click_next_page(bidi, context, total):
            raise RuntimeError(f"Could not advance {label} after {end}/{total}.")
        text, showing, rows, first = wait_for_table(bidi, context, (showing, first))
    starts = [page["showing"][0] for page in pages]
    actual = sum(len(page["table_rows"]) - 1 for page in pages)
    if not starts or len(starts) != len(set(starts)) or starts[0] != 1 or pages[-1]["showing"][1] != pages[-1]["showing"][2] or actual != pages[-1]["showing"][2]:
        raise RuntimeError(f"Page validation failed for {label}: starts={starts}, rows={actual}")
    return {"expected": pages[-1]["showing"][2], "pages": pages}

def wait_for_ready(bidi, context):
    deadline = time.time() + 120
    while time.time() < deadline:
        if len(body(bidi, context)) > 1000:
            return
        time.sleep(2)
    raise RuntimeError("Business Essentials page did not load.")

def checkpoint(payload):
    TEMP.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

def main():
    TEMP.unlink(missing_ok=True)
    bidi = Bidi()
    try:
        bidi.call("session.new", {"capabilities": {"alwaysMatch": {}}})
        contexts = bidi.call("browsingContext.getTree", {}).get("contexts", [])
        if not contexts:
            raise RuntimeError("Firefox did not provide a Business Essentials page.")
        context = contexts[0]["context"]
        bidi.call("browsingContext.navigate", {"context": context, "url": BUSINESS_URL, "wait": "complete"})
        wait_for_ready(bidi, context)
        result = {
            "source": "business_essentials_firefox",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "tabs": {},
        }

        # Creator Graduation has its own evaluated/graduation tabs.
        click_selector(bidi, context, '[data-id="Creator graduation"]')
        time.sleep(3)
        graduation_tabs = [label for label in listed_tabs(bidi, context) if re.search(r"(\d+\s+Evaluated|\d+\s+Reached graduation)", label, re.I)]
        if not graduation_tabs:
            raise RuntimeError("Creator Graduation tabs did not load.")
        for label in graduation_tabs:
            click_tab(bidi, context, label)
            time.sleep(2)
            key = f"Creator Graduation — {label}"
            result["tabs"][key] = capture_current(bidi, context, key)
            checkpoint(result)

        # Creator Stability is the authoritative source for evaluated/new/quit cards.
        click_selector(bidi, context, '[data-id="Creator stability"]')
        time.sleep(3)
        key = "Creator Stability — Evaluated Creators"
        result["tabs"][key] = capture_current(bidi, context, key)
        checkpoint(result)

        # This card opens the six-creator list and exposes the completed-incentive result.
        click_selector(bidi, context, '[data-id="Creator graduation"]')
        time.sleep(2)
        click_selector(bidi, context, '[data-id="mature_creators_earned_extra_rewards"]')
        time.sleep(3)
        key = "Creator Graduation — Creators with Extra Reward"
        result["tabs"][key] = capture_current(bidi, context, key)
        checkpoint(result)

        if OUT.exists():
            shutil.copy2(OUT, PREVIOUS)
        TEMP.replace(OUT)
        print("VALIDATED", {name: {"pages": len(tab["pages"]), "rows": sum(len(page["table_rows"]) - 1 for page in tab["pages"])} for name, tab in result["tabs"].items()})
    finally:
        try:
            bidi.call("session.end")
        except Exception:
            pass
        bidi.close()

if __name__ == "__main__":
    main()
