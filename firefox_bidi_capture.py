#!/usr/bin/env python3
"""Read the visible TikTok Backstage Creator table through Firefox BiDi.

This intentionally attaches only to Firefox's remote-debugging WebDriver BiDi
endpoint. It does not launch, depend on, or fall back to Chromium.
"""

from __future__ import annotations

import json
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
        self.ws = websocket.create_connection(
            url, timeout=30, origin="http://127.0.0.1"
        )
        self.message_id = 0

    def call(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.message_id += 1
        message_id = self.message_id
        self.ws.send(
            json.dumps({"id": message_id, "method": method, "params": params or {}})
        )
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
    if value.get("type") == "string":
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


def main() -> int:
    output = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path("data/firefox-creator-page.txt")
    )
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
            text = str(
                evaluate(bidi, context, "document.body ? document.body.innerText : ''")
            )
            if "Diamonds" in text and "Valid go LIVE days" in text:
                # Backstage renders labels before creator rows. Keep the page open
                # long enough for the visible source values to arrive.
                if headers_visible_at is None:
                    headers_visible_at = time.time()
                elif time.time() - headers_visible_at >= 25:
                    break
            time.sleep(2)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        html_output = output.with_suffix(".html")
        html = str(evaluate(bidi, context, "document.documentElement.outerHTML"))
        html_output.write_text(html, encoding="utf-8")
        # Keep a visible layout map as well as the raw page text. Backstage
        # renders its table as positioned elements, so coordinates retain
        # the row/column relationship for the values shown on the page.
        layout = str(
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
        output.with_name("firefox-creator-layout.json").write_text(
            layout, encoding="utf-8"
        )
        print(f"Saved {len(text)} characters from Firefox to {output}")
        if "Diamonds" not in text:
            raise RuntimeError("The Creator table did not load in Firefox before the deadline.")
        return 0
    finally:
        try:
            bidi.call("session.end")
        except Exception:
            pass
        bidi.close()


if __name__ == "__main__":
    raise SystemExit(main())
