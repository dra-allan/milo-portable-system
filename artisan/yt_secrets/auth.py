#!/usr/bin/env python3
"""Human-run, foreground OAuth for every configured YouTube channel.

Run from the artisan directory:
    python -m yt_secrets auth
    python -m yt_secrets status
    python -m yt_secrets auth --channel capital_mindset

This process owns its callback server for its whole lifetime. Do not launch it
through a daemon, scheduler, agent shell, or redirected background process.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Install PyYAML first: python -m pip install PyYAML") from exc

HERE = Path(__file__).resolve().parent
LEGACY_DIR = HERE.parent / "yt-secrets"
REPO_ROOT = HERE.parent.parent
REGISTRY = LEGACY_DIR / "channels.yaml"
TOKEN_URI = "https://oauth2.googleapis.com/token"
YT_CHANNELS_URI = "https://youtube.googleapis.com/youtube/v3/channels"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def load_channels() -> Dict[str, Dict[str, Any]]:
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {}
    return data.get("channels", {})


def token_path(key: str, info: Dict[str, Any]) -> Path:
    return REPO_ROOT / info["token_dir"] / f"youtube_token_{key}.json"


def credentials_path(info: Dict[str, Any], override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    return LEGACY_DIR / info["slug"] / "credentials.json"


def installed_client(doc: Dict[str, Any]) -> Dict[str, Any]:
    client = doc.get("installed") or doc.get("web")
    if not client or not client.get("client_id") or not client.get("client_secret"):
        raise ValueError("credentials.json must contain an installed or web OAuth client")
    return client


def build_auth_url(client: Dict[str, Any], redirect_uri: str) -> str:
    params = {
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return client.get("auth_uri", "https://accounts.google.com/o/oauth2/auth") + "?" + urllib.parse.urlencode(params)


def post_form(url: str, values: Dict[str, str]) -> Dict[str, Any]:
    request = urllib.request.Request(url, data=urllib.parse.urlencode(values).encode(), method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def exchange(client: Dict[str, Any], code: str, redirect_uri: str) -> Dict[str, Any]:
    return post_form(TOKEN_URI, {
        "client_id": client["client_id"], "client_secret": client["client_secret"],
        "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri,
    })


def refresh(doc: Dict[str, Any]) -> Dict[str, Any]:
    return post_form(TOKEN_URI, {
        "client_id": doc["client_id"], "client_secret": doc["client_secret"],
        "refresh_token": doc["refresh_token"], "grant_type": "refresh_token",
    })


def verify(doc: Dict[str, Any]) -> Tuple[str, str]:
    refreshed = refresh(doc)
    access_token = refreshed.get("access_token")
    if not access_token:
        raise RuntimeError("Google returned no access token during refresh")
    request = urllib.request.Request(YT_CHANNELS_URI + "?part=snippet&mine=true")
    request.add_header("Authorization", f"Bearer {access_token}")
    with urllib.request.urlopen(request, timeout=60) as response:
        items = json.load(response).get("items", [])
    if not items:
        raise RuntimeError("OAuth succeeded, but this Google account owns no YouTube channel")
    snippet = items[0].get("snippet", {})
    return str(snippet.get("title") or "(unnamed channel)"), str(items[0].get("id") or "")


def token_document(client: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    expires = int(values.get("expires_in", 3600))
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires)).replace(microsecond=0)
    doc = {
        "token": values.get("access_token"), "refresh_token": values.get("refresh_token"),
        "token_uri": TOKEN_URI, "client_id": client["client_id"],
        "client_secret": client["client_secret"], "scopes": SCOPES,
        "universe_domain": "googleapis.com", "expiry": expiry.isoformat().replace("+00:00", "Z"),
    }
    if not doc["refresh_token"]:
        raise RuntimeError("Google did not return a refresh token; retry with consent prompt")
    return doc


def foreground_flow(key: str, info: Dict[str, Any], client: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    result: Dict[str, str] = {}
    port = 0
    nonce = secrets.token_urlsafe(12)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            return
        def do_GET(self) -> None:  # noqa: N802
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state", [""])[0] != nonce:
                result["error"] = "state mismatch"
            else:
                result["code"] = query.get("code", [""])[0]
                result["error"] = query.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h2>Authentication received. You can close this tab.</h2>")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/oauth2callback"
    params = {"state": nonce}
    url = build_auth_url(client, redirect_uri) + "&" + urllib.parse.urlencode(params)
    print(f"\n[{key}] Sign in as {info['email']} and approve access.")
    print("Opening the URL in your browser. If it does not open, copy this exact URL:\n")
    print(url)
    webbrowser.open(url)
    server.timeout = 1
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline and not result:
            server.handle_request()
    finally:
        server.server_close()
    if result.get("error"):
        raise RuntimeError(f"OAuth error: {result['error']}")
    if not result.get("code"):
        raise TimeoutError(f"no callback received after {timeout // 60} minutes")
    return exchange(client, result["code"], redirect_uri)


def authenticate(key: str, info: Dict[str, Any], override: str | None, timeout: int) -> None:
    creds = credentials_path(info, override)
    if not creds.exists():
        raise FileNotFoundError(f"credentials missing: {creds} (download the {info['slug']} project OAuth JSON)")
    client = installed_client(json.loads(creds.read_text(encoding="utf-8")))
    doc = token_document(client, foreground_flow(key, info, client, timeout))
    title, channel_id = verify(doc)
    out = token_path(key, info)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    try:
        tmp.replace(out)
    finally:
        if tmp.exists():
            tmp.unlink()
    print(f"OK  {key}: {title} ({channel_id}), refreshed successfully -> {out}")


def status(keys: Iterable[str]) -> int:
    channels = load_channels()
    failures = 0
    for key in keys:
        info = channels[key]
        path = token_path(key, info)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            title, channel_id = verify(doc)
            print(f"OK  {key}: {title} ({channel_id}), refresh works")
        except Exception as exc:
            failures += 1
            print(f"BAD {key}: {exc}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m yt_secrets")
    sub = parser.add_subparsers(dest="command", required=True)
    auth = sub.add_parser("auth", help="authenticate one channel or every active channel")
    auth.add_argument("--channel", help="registry key; omit to authenticate all active channels")
    auth.add_argument("--credentials", help="override credentials.json for this run")
    auth.add_argument("--timeout-minutes", type=int, default=60)
    check = sub.add_parser("status", help="refresh-check tokens and show YouTube channel names")
    check.add_argument("--channel")
    args = parser.parse_args(argv)
    channels = load_channels()
    if args.command == "auth":
        keys = [args.channel] if args.channel else [k for k, v in channels.items() if v.get("active", False)]
        if not keys:
            parser.error("no active channels in channels.yaml")
        for key in keys:
            if key not in channels:
                parser.error(f"unknown channel: {key}")
            try:
                authenticate(key, channels[key], args.credentials, args.timeout_minutes * 60)
            except Exception as exc:
                print(f"FAIL {key}: {exc}", file=sys.stderr)
                return 1
        return 0
    keys = [args.channel] if args.channel else [k for k, v in channels.items() if v.get("active", False)]
    if args.channel and args.channel not in channels:
        parser.error(f"unknown channel: {args.channel}")
    return status(keys)


if __name__ == "__main__":
    raise SystemExit(main())
