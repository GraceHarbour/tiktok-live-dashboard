#!/usr/bin/env python3
"""Read the visible TikTok Backstage Creator table through Firefox BiDi.

This intentionally attaches only to Firefox's remote-debugging WebDriver BiDi
endpoint.  It does not launch, depend on, or fall back to Chromium.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import websocket


CREATOR_URL = (
    "https://live-backstage.tiktok.com/portal/administration/term-target-management"
    "?agent=%255B%255D&tab=creator&month=202608"
)


class Bidi:
    def __init__(self, url: str = "ws://localhost:9222/session") -> None:
        self.ws = websocket.create_connection(url, timeout=30, origin="http://127.0.0.1")
        self.message_id = 0

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.message_id += 1
        message_id = self.message_id
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        while True:
            reply = json.loads(self.ws.recv())
            if reply.get("id") != message_id:
                continue
            if "error" in reply:
                raise RuntimeError(f"{method}: {reply['error']}")
            return reply.get("result", {})

    def close(self) -> None:
        self.ws.close()


def remote_value(result: dict[str, Any]) -> Any:
    value = result.get("result", {})
    if value.get("type") in {"string", "boolean", "number"}:
        return value.get("value", "")
    return value


def evaluate(bidi: Bidi, context: str, expression: str) -> Any:
    result = bidi.call(
        "script.evaluate",
        {
            "expression": expression,
            "target": {"context": context},
            "awaitPromise": True,
            "resultOwnership": "none",
        },
    )
    return remote_value(result)


def visible_layout(bidi: Bidi, context: str) -> list[dict[str, Any]]:
    raw = str(
        evaluate(
            bidi,
            context,
            """JSON.stringify([...document.body.querySelectorAll('*')]
                .filter(el => el.children.length === 0 && el.innerText?.trim())
                .map(el => {
                    const rect = el.getBoundingClientRect();
                    return {text: el.innerText.trim(), x: Math.round(rect.x),
                            y: Math.round(rect.y), width: Math.round(rect.width),
                            height: Math.round(rect.height)};
                })
                .filter(item => item.width > 0 && item.height > 0))""",
        )
    )
    return json.loads(raw)


def visible_images(bidi: Bidi, context: str) -> list[dict[str, Any]]:
    raw = str(evaluate(bidi, context, """JSON.stringify([...document.images]
      .map(img => { const r=img.getBoundingClientRect(); return {src: img.currentSrc || img.src,
        x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height)}; })
      .filter(i => i.src && i.width > 0 && i.height > 0))"""))
    return json.loads(raw)


def page_range(text: str) -> tuple[int, int, int] | None:
    matches = re.findall(r"Showing\s+(\d+)\s*(?:-|to)\s*(\d+)\s+of\s+(\d+)", text, re.I)
    if matches:
        return max((tuple(int(value) for value in match) for match in matches), key=lambda item: item[2])
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 6 and parts[0] == "Showing" and parts[2] == "to" and parts[4] == "of":
            try:
                return int(parts[1]), int(parts[3]), int(parts[5])
            except ValueError:
                continue
    return None


def click_next_page(bidi: Bidi, context: str, total: int) -> bool:
    raw = str(
        evaluate(
            bidi,
            context,
            f"""(() => {{
                const pagination = Array.from(document.querySelectorAll('.semi-table-pagination-outer'))
                    .find(node => new RegExp('of\\\\s*{total}\\\\b').test(node.innerText || ''));
                const next = pagination && pagination.querySelector(
                    'button[aria-label="Next"]:not(:disabled), '
                    + '[role="button"][aria-label="Next"]:not([aria-disabled="true"])'
                );
                if (!next) return false;
                next.click();
                return true;
            }})()""",
        )
    )
    return raw.lower() == "true"


def pagination_diagnostics(bidi: Bidi, context: str) -> list[dict[str, Any]]:
    """Save enough DOM detail to identify Backstage's real page control."""
    raw = str(
        evaluate(
            bidi,
            context,
            """(() => {
                const elements = [...document.querySelectorAll('*')];
                const showing = elements
                  .filter(el => (el.innerText || '').trim().startsWith('Showing '))
                  .sort((a, b) => a.innerText.length - b.innerText.length)
                  .slice(0, 5)
                  .map(el => ({
                    kind: 'showing', tag: el.tagName, className: String(el.className),
                    role: el.getAttribute('role'), ariaLabel: el.getAttribute('aria-label'),
                    text: el.innerText.trim(), html: el.outerHTML.slice(0, 4000)
                  }));
                const pages = elements
                  .filter(el => /^\\d+$/.test((el.innerText || '').trim()))
                  .map(el => {
                    const rect = el.getBoundingClientRect();
                    return {
                      kind: 'number', tag: el.tagName, className: String(el.className),
                      role: el.getAttribute('role'), ariaLabel: el.getAttribute('aria-label'),
                      text: el.innerText.trim(), x: Math.round(rect.x), y: Math.round(rect.y),
                      width: Math.round(rect.width), height: Math.round(rect.height),
                      html: el.outerHTML.slice(0, 1200)
                    };
                  })
                  .filter(item => item.width > 0 && item.height > 0)
                  .slice(-120);
                return JSON.stringify([...showing, ...pages]);
            })()""",
        )
    )
    return json.loads(raw)


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/firefox-creator-page.txt")
    bidi = Bidi()
    try:
        bidi.call("session.new", {"capabilities": {"alwaysMatch": {}}})
        contexts = bidi.call("browsingContext.getTree", {}).get("contexts", [])
        if not contexts:
            raise RuntimeError("Firefox did not provide a browser tab.")
        context = contexts[0]["context"]
        bidi.call(
            "browsingContext.navigate",
            {"context": context, "url": CREATOR_URL, "wait": "complete"},
        )
        deadline = time.time() + 90
        text = ""
        headers_visible_at: float | None = None
        while time.time() < deadline:
            text = str(evaluate(bidi, context, "document.body ? document.body.innerText : ''"))
            if "Diamonds" in text and "Valid go LIVE days" in text and page_range(text):
                # Backstage renders the column labels before it renders the
                # creator rows.  Keep the source page open long enough for
                # those visible values to arrive before saving the snapshot.
                if headers_visible_at is None:
                    headers_visible_at = time.time()
                elif time.time() - headers_visible_at >= 25:
                    break
            time.sleep(2)
        if "Diamonds" not in text:
            raise RuntimeError("The Creator table did not load in Firefox before the deadline.")
        output.parent.mkdir(parents=True, exist_ok=True)
        pages: list[dict[str, Any]] = []
        pages_output = output.with_name("firefox-creator-pages.json")
        while True:
            showing = page_range(text)
            if not showing:
                raise RuntimeError("Firefox did not expose the Creator pagination range.")
            pages.append({"showing": showing, "text": text, "layout": visible_layout(bidi, context), "images": visible_images(bidi, context)})
            pages_output.write_text(json.dumps(pages), encoding="utf-8")
            _start, end, total = showing
            if end >= total:
                break
            output.with_name("firefox-creator-pagination-diagnostics.json").write_text(
                json.dumps(pagination_diagnostics(bidi, context), indent=2), encoding="utf-8"
            )
            if not click_next_page(bidi, context, total):
                raise RuntimeError(f"Firefox could not advance past Creator page ending at {end}.")
            next_deadline = time.time() + 30
            while time.time() < next_deadline:
                next_text = str(evaluate(bidi, context, "document.body ? document.body.innerText : ''"))
                next_range = page_range(next_text)
                if next_range and next_range[0] > _start:
                    # Pagination updates before the virtualized table rows.
                    time.sleep(2)
                    text = str(evaluate(bidi, context, "document.body ? document.body.innerText : ''"))
                    break
                time.sleep(1)
            else:
                raise RuntimeError(f"Firefox did not load the Creator page after {end}.")
        output.write_text(pages[0]["text"], encoding="utf-8")
        output.with_name("firefox-creator-layout.json").write_text(
            json.dumps(pages[0]["layout"]), encoding="utf-8"
        )
        html_output = output.with_suffix(".html")
        html = str(evaluate(bidi, context, "document.documentElement.outerHTML"))
        html_output.write_text(html, encoding="utf-8")
        print(f"Saved {len(pages)} Creator pages through Firefox to {pages_output}")
        return 0
    finally:
        try:
            bidi.call("session.end")
        except Exception:
            pass
        bidi.close()


if __name__ == "__main__":
    raise SystemExit(main())
