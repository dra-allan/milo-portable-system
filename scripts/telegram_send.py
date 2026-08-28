#!/usr/bin/env python
"""
telegram_send.py — Universal resilient CLI tool for Milo to push messages to Telegram.
======================================================================================
Usage:
    python C:\\milo-portable-system\\scripts\\telegram_send.py "Hello Allan! YouTube Shorts pipeline complete."
    python C:\\milo-portable-system\\scripts\\telegram_send.py --file "C:\\path\\to\\report.txt"
"""
from __future__ import annotations

import os
import sys
import time
import argparse
from pathlib import Path

# Fix Windows console UTF-8 encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8844481759:AAExAkAIOl_m_JBQ3_RxTf9tM7Afn32Y3nM")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "8101147332")


def send_telegram(text: str, chat_id: str = TELEGRAM_CHAT_ID, token: str = TELEGRAM_BOT_TOKEN, max_retries: int = 5) -> bool:
    import requests

    if not token or not chat_id:
        print("[!] Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured.", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    # Chunk text if necessary
    chunks = []
    limit = 3800
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        idx = text.rfind("\n", 0, limit)
        if idx < limit // 2:
            idx = limit
        chunks.append(text[:idx])
        text = text[idx:].lstrip("\n")

    overall = True
    for i, chunk in enumerate(chunks):
        sent = False
        for attempt in range(max_retries):
            try:
                resp = requests.post(url, json={
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                }, timeout=20)
                if resp.status_code == 200:
                    sent = True
                    break
                else:
                    print(f"[!] Telegram API error ({resp.status_code}): {resp.text}", file=sys.stderr)
            except Exception as e:
                print(f"[!] Telegram network retry {attempt+1}/{max_retries}: {e}", file=sys.stderr)
            time.sleep(min(2 ** attempt, 10))

        if sent:
            print(f"[+] Delivered chunk {i+1}/{len(chunks)} to Telegram ({chat_id})")
        else:
            overall = False
            print(f"[X] Failed to deliver chunk {i+1} to Telegram", file=sys.stderr)

    return overall


def main():
    parser = argparse.ArgumentParser(description="Send message to Telegram")
    parser.add_argument("message", nargs="*", help="Message text to send")
    parser.add_argument("--file", "-f", help="Read message text from file")
    parser.add_argument("--chat-id", "-c", default=TELEGRAM_CHAT_ID, help="Target Telegram Chat ID")
    parser.add_argument("--token", "-t", default=TELEGRAM_BOT_TOKEN, help="Telegram bot token")
    args = parser.parse_args()

    text = ""
    if args.file:
        p = Path(args.file)
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="replace")
        else:
            print(f"[!] File not found: {args.file}", file=sys.stderr)
            sys.exit(1)
    elif args.message:
        text = " ".join(args.message)

    if not text.strip():
        print("Usage: telegram_send.py \"message\" OR telegram_send.py --file path", file=sys.stderr)
        sys.exit(1)

    ok = send_telegram(text.strip(), chat_id=args.chat_id, token=args.token)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
