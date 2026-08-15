#!/usr/bin/env python
"""Mint a YouTube upload token for one channel using its owning email's
Google Cloud project.

Reads yt-secrets/channels.yaml, resolves the channel's email/slug and the
pipeline token_dir, runs the interactive OAuth flow against that email's
credentials.json (yt-secrets/<slug>/credentials.json), and writes
youtube_token_<key>.json into token_dir.

Usage:
    python -m yt_secrets.mint <channel-key>
    # or, from anywhere:
    python mint_token.py <channel-key> [--credentials <path>]

The --credentials override is for a first run on a machine where the email's
credentials.json has not been downloaded yet -- you can point at any
credentials.json you already have for that email and mint the token, then
drop the proper file in place later.

Exit 0 on success. The written token is validated by refreshing it once
before printing OK.
"""
import argparse
import json
import sys
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
TOKEN_URI = "https://oauth2.googleapis.com/token"
HERE = Path(__file__).resolve().parent
CHANNELS_YAML = HERE / "channels.yaml"


def load_channels():
    import yaml
    data = yaml.safe_load(CHANNELS_YAML.read_text(encoding="utf-8"))
    return data.get("channels", {})


def build_auth_url(client, port):
    p = client["installed"]
    params = {
        "client_id": p["client_id"],
        "redirect_uri": f"http://localhost:{port}/",
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return p["auth_uri"] + "?" + urllib.parse.urlencode(params)


def exchange(client, port, code):
    body = urllib.parse.urlencode({
        "client_id": client["installed"]["client_id"],
        "client_secret": client["installed"]["client_secret"],
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": f"http://localhost:{port}/",
    }).encode()
    req = urllib.request.Request(TOKEN_URI, data=body)
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def refresh_token(doc):
    body = urllib.parse.urlencode({
        "client_id": doc["client_id"],
        "client_secret": doc["client_secret"],
        "refresh_token": doc["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(TOKEN_URI, data=body)
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("channel")
    ap.add_argument("--credentials", default=None)
    ap.add_argument("--port", type=int, default=8891)
    args = ap.parse_args()

    channels = load_channels()
    if args.channel not in channels:
        sys.exit(f"unknown channel '{args.channel}'. Known: {', '.join(sorted(channels))}")

    info = channels[args.channel]
    slug = info["slug"]
    creds_path = Path(args.credentials).expanduser() if args.credentials else HERE / slug / "credentials.json"
    if not creds_path.exists():
        sys.exit(f"credentials not found: {creds_path} (download from the {info['email']} Cloud project)")

    client = json.loads(creds_path.read_text(encoding="utf-8"))
    url = build_auth_url(client, args.port)
    got = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got["code"] = qs.get("code", [None])[0]
            got["error"] = qs.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if got.get("code"):
                self.wfile.write(b"<html><body><h2>Auth OK - close this tab.</h2></body></html>")
            else:
                self.wfile.write(b"<html><body><h2>Auth cancelled.</h2></body></html>")

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Open this URL in the {info['email']} profile:\n{url}\n", flush=True)

    for _ in range(300):
        if "code" in got or "error" in got:
            break
        import time
        time.sleep(1)
    server.shutdown()

    if got.get("error"):
        sys.exit(f"OAuth error: {got['error']}")
    if not got.get("code"):
        sys.exit("timed out waiting for OAuth callback")

    tokens = exchange(client, args.port, got["code"])
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=int(tokens.get("expires_in", 3600)))).replace(microsecond=0)
    doc = {
        "token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "token_uri": TOKEN_URI,
        "client_id": client["installed"]["client_id"],
        "client_secret": client["installed"]["client_secret"],
        "scopes": SCOPES,
        "universe_domain": "googleapis.com",
        "account": "",
        "expiry": expiry.isoformat().replace("+00:00", "Z"),
    }

    # Validate by refreshing before writing.
    refresh_token(doc)

    root = HERE.parent.parent  # artisan/
    out = root / info["token_dir"] / f"youtube_token_{args.channel}.json"
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"OK wrote {out}", flush=True)


if __name__ == "__main__":
    main()