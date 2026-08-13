"""Local computer-use MCP server for Milo.

Browser control rides the OpenCLI Browser Bridge, which drives the real Chrome
already on this machine (the one with the bridge extension loaded and Allan
logged in). Milo therefore never spawns a fresh Chromium, never needs a CDP
endpoint, and works in every tab the user actually has open.

Each MCP tool shells out to ``opencli browser <session> <command>``. The
session defaults to ``milo`` (override with ``MILO_BROWSER_SESSION``). The
first time a session is used it must be bound to the live tab with
``opencli browser milo bind`` — the ``browser_bind`` tool does exactly that.

Destructive actions (downloads) require an approval token in
``MILO_COMPUTER_APPROVAL``, matching the old Playwright gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List

def _approval() -> str:
    return os.environ.get("MILO_COMPUTER_APPROVAL", "")


def _opencli() -> str:
    """Full path to the opencli shim.

    Windows ships opencli as a ``.CMD`` wrapper; CreateProcess only finds
    ``.exe``/``.bat`` on PATH, so resolve the real file. Falls back to the bare
    name (POSIX) if resolution somehow fails.
    """
    resolved = shutil.which("opencli") or shutil.which("opencli.cmd")
    if not resolved:
        raise RuntimeError("opencli not found on PATH — install the OpenCLI CLI and load the browser bridge extension")
    return resolved


def _bridge(*parts: str, timeout: int = 120) -> Dict[str, Any]:
    """Run one ``opencli browser <session> <command>`` invocation."""
    session = os.environ.get("MILO_BROWSER_SESSION", "milo")
    argv = [_opencli(), "browser", session, *parts]
    try:
        p = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        raise RuntimeError("opencli not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"browser command timed out after {timeout}s") from exc

    out = ((p.stdout or "") + (p.stderr or "")).strip()
    payload: Dict[str, Any] = {"command": " ".join(parts)}
    if p.returncode != 0:
        raise RuntimeError(out or f"opencli browser exited {p.returncode}")
    if not out:
        return payload
    try:
        parsed = json.loads(out)
        if isinstance(parsed, (dict, list)):
            payload["result"] = parsed
    except (ValueError, TypeError):
        pass
    payload["output"] = out
    return payload


def call(name: str, args: Dict[str, Any]) -> Any:
    if name == "browser_bind":
        return _bridge("bind")
    if name == "browser_open":
        url = args["url"]
        tab = args.get("tab") or ""
        return _bridge("open", *(f"--tab={tab}" if tab else ()), url)
    if name == "browser_snapshot":
        return _bridge("state")
    if name == "browser_extract":
        return _bridge("extract")
    if name == "browser_screenshot":
        path = args.get("path") or "milo-screenshot.png"
        flags = ["--annotate"] if args.get("annotate") else []
        return _bridge("screenshot", *flags, path)
    if name == "browser_click":
        return _click_or_keys("click", args)
    if name == "browser_type":
        target = args.get("target") or ""
        text = args.get("text", "")
        if not text:
            raise ValueError("browser_type needs 'text'")
        return _bridge("fill", target, text) if target else _fill_by_semantic("fill", args)
    if name == "browser_keys":
        return _click_or_keys("keys", args, is_keys=True)
    if name == "browser_scroll":
        direction = args.get("direction", "down")
        if direction not in ("up", "down"):
            raise ValueError("direction must be 'up' or 'down'")
        pixels = args.get("pixels", 500)
        return _bridge("scroll", direction, f"--amount={pixels}")
    if name == "browser_watch":
        seconds = min(int(args.get("seconds", 10)), 120)
        return _bridge("wait", "time", str(seconds), timeout=seconds + 30)
    if name == "browser_download":
        if args.get("approval") != _approval() or not _approval():
            raise PermissionError("download requires MILO_COMPUTER_APPROVAL")
        pattern = args.get("pattern") or args.get("filename") or ""
        if not pattern:
            raise ValueError("browser_download needs a filename/URL pattern to wait for")
        return _bridge("wait", "download", pattern, timeout=180)
    raise KeyError(name)


def _click_or_keys(name: str, args: Dict[str, Any], *, is_keys: bool = False) -> Any:
    """click/keys: a numeric ref, CSS selector, or semantic locator."""
    target = args.get("target") or ""
    opts: List[str] = []
    for flag in ("role", "name", "label", "text", "testid"):
        val = args.get(flag)
        if val:
            opts.append(f"--{flag}={val}")
    nth = args.get("nth")
    if nth is not None:
        opts.append(f"--nth={nth}")
    if is_keys:
        key = target or args.get("key") or ""
        if not key:
            raise ValueError("browser_keys needs 'target'/'key' (e.g. Enter)")
        return _bridge("keys", *opts, key)
    if target or not opts:
        return _bridge("click", *opts, target)
    raise ValueError("browser_click needs 'target' (ref/CSS) or a semantic locator flag")


def _fill_by_semantic(sub: str, args: Dict[str, Any]) -> Any:
    opts: List[str] = []
    for flag in ("role", "name", "label", "testid"):
        val = args.get(flag)
        if val:
            opts.append(f"--{flag}={val}")
    return _bridge(sub, *opts, args["text"])


TOOLS: Dict[str, str] = {
    "browser_bind": "Bind the currently focused Chrome tab to the browser session (run once before other tools).",
    "browser_open": "Open a URL in the bound browser session.",
    "browser_snapshot": "Read the current page: URL, title, and interactive elements with numeric indices.",
    "browser_extract": "Extract the current page as readable markdown.",
    "browser_screenshot": "Capture the current page (optionally with annotated element refs) to a file.",
    "browser_click": "Click an element by numeric ref, CSS selector, or semantic locator.",
    "browser_type": "Fill a text field with exact text (set-and-verify).",
    "browser_keys": "Press a keyboard key (Enter, Escape, Tab, Control+a).",
    "browser_scroll": "Scroll the page up or down by pixels.",
    "browser_watch": "Wait a number of seconds while observing the page (e.g. a video).",
    "browser_download": "Wait for a browser download to land, guarded by an approval token.",
}

_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "browser_open": {"url": {"type": "string", "description": "URL to open"}},
    "browser_screenshot": {"path": {"type": "string", "description": "Output path (default milo-screenshot.png)"}, "annotate": {"type": "boolean", "description": "Overlay element ref labels"}},
    "browser_click": {"target": {"type": "string", "description": "Numeric ref (from snapshot), CSS selector, or empty with a semantic flag"}, "role": {"type": "string"}, "name": {"type": "string"}, "nth": {"type": "integer"}},
    "browser_type": {"target": {"type": "string", "description": "Numeric ref or CSS selector"}, "text": {"type": "string", "description": "Text to set"}, "role": {"type": "string"}, "name": {"type": "string"}},
    "browser_keys": {"key": {"type": "string", "description": "Key to press, e.g. Enter, Escape, Control+a"}},
    "browser_scroll": {"direction": {"type": "string", "description": "up or down"}, "pixels": {"type": "integer", "description": "Pixels to scroll (default 500)"}},
    "browser_watch": {"seconds": {"type": "integer", "description": "Seconds to wait (max 120)"}},
    "browser_download": {"pattern": {"type": "string", "description": "Download filename/URL pattern to wait for"}, "approval": {"type": "string", "description": "MILO_COMPUTER_APPROVAL token"}},
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
                result = {"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": _SCHEMAS.get(n, {})}} for n, d in TOOLS.items()]}
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
