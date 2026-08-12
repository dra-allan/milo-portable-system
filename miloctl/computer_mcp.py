"""Local computer-use MCP server for Milo.

Browser control is intentionally explicit and auditable. It uses Playwright
when installed and connects to a local browser over CDP, so Milo never needs a
remote browser credential. Destructive actions require an approval token.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

_BROWSER = None
_PAGES = {}
_APPROVAL = os.environ.get("MILO_COMPUTER_APPROVAL", "")


def _page(page_id: str = "current"):
    if page_id in _PAGES:
        return _PAGES[page_id]
    if not _PAGES:
        if sync_playwright is None:
            raise RuntimeError("install the optional browser extra: pip install playwright")
        global _BROWSER
        _BROWSER = sync_playwright().start()
        endpoint = os.environ.get("MILO_CDP_URL", "http://127.0.0.1:9222")
        browser = _BROWSER.chromium.connect_over_cdp(endpoint)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        _PAGES["current"] = context.pages[0] if context.pages else context.new_page()
        return _PAGES["current"]
    raise KeyError("unknown page: " + page_id)


def call(name: str, args: Dict[str, Any]) -> Any:
    page = _page(args.get("page_id", "current"))
    if name == "browser_open":
        page.goto(args["url"], wait_until="domcontentloaded", timeout=30000)
        return {"url": page.url, "title": page.title()}
    if name == "browser_snapshot":
        return {"url": page.url, "title": page.title(), "text": page.locator("body").inner_text()[:20000]}
    if name == "browser_screenshot":
        path = args.get("path", "milo-screenshot.png")
        page.screenshot(path=path, full_page=bool(args.get("full_page", False)))
        return {"path": path, "url": page.url}
    if name == "browser_click":
        page.locator(args["selector"]).click(timeout=15000)
        return {"ok": True, "url": page.url}
    if name == "browser_type":
        page.locator(args["selector"]).fill(args["text"])
        return {"ok": True}
    if name == "browser_scroll":
        page.mouse.wheel(0, int(args.get("pixels", 700)))
        return {"ok": True}
    if name == "browser_watch":
        seconds = min(int(args.get("seconds", 10)), 120)
        page.wait_for_timeout(seconds * 1000)
        return {"ok": True, "url": page.url, "title": page.title()}
    if name == "browser_download":
        if args.get("approval") != _APPROVAL or not _APPROVAL:
            raise PermissionError("download requires MILO_COMPUTER_APPROVAL")
        with page.expect_download() as download:
            page.locator(args["selector"]).click(timeout=15000)
        item = download.value
        path = args.get("path", item.suggested_filename)
        item.save_as(path)
        return {"path": path}
    raise KeyError(name)


TOOLS = {
    "browser_open": "Open a URL in Milo's connected browser.",
    "browser_snapshot": "Read the current page text and metadata.",
    "browser_screenshot": "Capture the current page for visual inspection.",
    "browser_click": "Click a CSS selector in the current page.",
    "browser_type": "Fill a text field in the current page.",
    "browser_scroll": "Scroll the current page.",
    "browser_watch": "Wait while observing the current page or video.",
    "browser_download": "Download from the page, guarded by explicit approval.",
}


def serve() -> int:
    for line in sys.stdin:
        try:
            req = json.loads(line)
            method = req.get("method")
            rid = req.get("id")
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "milo-computer", "version": "1.0"}}
            elif method == "tools/list":
                result = {"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": {}}} for n, d in TOOLS.items()]}
            elif method == "tools/call":
                p = req.get("params", {})
                result = {"content": [{"type": "text", "text": json.dumps(call(p["name"], p.get("arguments", {})))}]}
            elif method == "ping":
                result = {}
            else:
                continue
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
            sys.stdout.flush()
        except Exception as exc:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32000, "message": str(exc)}}) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
