"""Re-auth a single pipeline channel against its own GCP project.

Usage (after Allan has created the project + Desktop OAuth client and
downloaded the client_secrets JSON from the console):

    python reauth_channel.py flick_shorts --secrets C:\\path\\to\\downloaded.json
    python reauth_channel.py RankDrop      --secrets C:\\path\\to\\downloaded.json

This:
  1. Installs the secrets file into the per-channel location the pipeline
     actually resolves (shorts: config/youtube_client_secrets_<ch>.json;
     ranking: pov/config/credentials_<ch>.json + the with_name fallback).
  2. Starts a local OAuth server on a fixed port and prints the auth URL.
  3. Waits for you to complete consent in a browser signed into the channel's
     owner Gmail (use the right opencli profile), then saves the token to the
     exact file the pipeline will load.
  4. Verifies the resulting credentials by calling channels().list(mine=True).
"""
import argparse
import json
import shutil
import sys
import threading
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SHORTS_ROOT = Path(r"C:\Users\user\Desktop\milo-portable-system\artisan\youtube-shorts-pipeline")
RANKING_ROOT = Path(r"C:\Users\user\Desktop\milo-portable-system\artisan\ranking-shorts-pipeline")
POV_CONFIG = Path(r"C:\Users\user\Desktop\Milo Video Factory\pov\config")

SCOPES = ['https://www.googleapis.com/auth/youtube.upload',
          'https://www.googleapis.com/auth/youtube',
          'https://www.googleapis.com/auth/youtube.force-ssl']

SHORTS_CHANNELS = ['flick_shorts', 'capital_mindset', 'wealth_mindset', 'chop_ug', 'NXS']
RANKING_CHANNELS = ['RankDrop', 'the other guys']
PORT = 8095


def install_secrets(channel: str, secrets_src: Path) -> list:
    src = Path(secrets_src)
    if not src.exists():
        raise FileNotFoundError(f'secrets file not found: {src}')
    data = json.loads(src.read_text(encoding='utf-8'))
    assert 'installed' in data or 'web' in data, 'not a client_secrets JSON'
    targets = []
    if channel in SHORTS_CHANNELS:
        targets.append(SHORTS_ROOT / 'config' / f'youtube_client_secrets_{channel}.json')
    elif channel in RANKING_CHANNELS:
        targets.append(POV_CONFIG / f'credentials_{channel}.json')
        targets.append(SHORTS_ROOT / f'youtube_client_secrets_ranking_{channel}.json')
    else:
        print(f'Unknown channel {channel!r}; known: {SHORTS_CHANNELS + RANKING_CHANNELS}')
        sys.exit(2)
    for t in targets:
        t.parent.mkdir(parents=True, exist_ok=True)
        if t.resolve() != src.resolve():
            shutil.copyfile(src, t)
            print(f'installed secrets -> {t}')
        else:
            print(f'secrets already in place -> {t}')
    return targets


def token_target(channel: str) -> Path:
    if channel in RANKING_CHANNELS:
        legacy = RANKING_ROOT / 'config' / f'youtube_token_ranking_{channel}.json'
        if legacy.exists():
            return legacy
        return POV_CONFIG / f'youtube_token_{channel}.json'
    base = SHORTS_ROOT / 'config' / 'youtube_token.json'
    candidate = base.with_name(f'youtube_token_{channel}.json')
    return candidate if candidate.exists() else base


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument('channel')
    p.add_argument('--secrets', required=True)
    args = p.parse_args(argv)

    secrets_path = install_secrets(args.channel, Path(args.secrets))[0]
    token_file = token_target(args.channel)
    print(f'token target -> {token_file}')

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    flow.redirect_uri = f'http://localhost:{PORT}/'
    auth_url, _ = flow.authorization_url(access_type='offline', prompt='consent')
    print('\nOPEN THIS URL in a browser signed into the channel owner Gmail:')
    print(auth_url)
    print(f'\nWaiting for the redirect to http://localhost:{PORT}/ ... (script is live now)')

    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs

    error = {}
    code_holder = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            if 'code' in q:
                code_holder['code'] = q['code'][0]
            elif 'error' in q:
                error['error'] = q['error'][0]
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html><body>You can close this tab now.</body></html>')

        def log_message(self, *a):
            pass

    server = HTTPServer(('localhost', PORT), Handler)
    while 'code' not in code_holder and 'error' not in error:
        server.handle_request()
    server.server_close()

    if 'error' in error:
        print(f'AUTH_ERROR: {error["error"]}')
        return 1

    creds = flow.fetch_token(code=code_holder['code'])
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding='utf-8')
    print(f'\nTOKEN_SAVED -> {token_file}')

    from googleapiclient.discovery import build
    yt = build('youtube', 'v3', credentials=creds, cache_discovery=False)
    items = yt.channels().list(part='snippet', mine=True).execute().get('items', [])
    if items:
        print(f"CHANNEL: {items[0]['id']} | {items[0]['snippet']['title']}")
    else:
        print('CHANNEL: none (token has no channel)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
