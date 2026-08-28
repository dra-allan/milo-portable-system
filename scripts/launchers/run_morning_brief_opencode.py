#!/usr/bin/env python
"""
run_morning_brief_opencode.py — OpenCode-Driven Morning Briefing Runner
========================================================================
Runs the morning briefing prompt through OpenCode, saves the generated
briefing into the Obsidian vault daily notes, prints live terminal output,
and sends the completed briefing to Allan on Telegram.
"""
from __future__ import annotations

import os
import re
import sys
import time
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

# Fix Windows console UTF-8 encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add project root to sys.path
PROJECT_ROOT = Path(r"C:\milo-portable-system")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8844481759:AAExAkAIOl_m_JBQ3_RxTf9tM7Afn32Y3nM")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "8101147332")
VAULT_DIR = Path(r"C:\Users\Administrator\Desktop\dra-brains")
DAILY_NOTES_DIR = VAULT_DIR / "01 - Daily Notes"

OPENCODE_BIN = (
    shutil.which("opencode.cmd")
    or shutil.which("opencode")
    or r"C:\Users\Administrator\AppData\Roaming\npm\opencode.cmd"
)

BRIEFING_PROMPT = """Good morning Allan. Give a short, focused morning briefing for today:
1. What was decided or left in progress yesterday (check memory and vault notes).
2. Anything time-sensitive or high priority for today.
3. System status: YouTube Shorts & Ranking Shorts automation pipeline readiness.
4. One high-value recommendation or opportunity for today.

Be clear, direct, and actionable. No filler. The briefing must be ready to deliver to Allan."""


def send_telegram_resilient(text: str, max_retries: int = 5) -> bool:
    """Send text to Telegram with retry and chunking support."""
    import requests

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # Split text if too long
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

    overall_success = True
    for i, chunk in enumerate(chunks):
        delivered = False
        for attempt in range(max_retries):
            try:
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk,
                }
                resp = requests.post(url, json=payload, timeout=20)
                if resp.status_code == 200:
                    delivered = True
                    break
                else:
                    print(f"[!] Telegram API error (attempt {attempt+1}): {resp.status_code} {resp.text}")
            except Exception as e:
                print(f"[!] Telegram network error (attempt {attempt+1}): {e}")
            time.sleep(min(2 ** attempt, 15))
        
        if not delivered:
            overall_success = False
            print(f"[X] Failed to deliver message chunk {i+1} to Telegram")
    
    return overall_success


def save_to_vault(briefing_text: str):
    """Save the briefing to today's daily note in the Obsidian vault."""
    try:
        DAILY_NOTES_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        note_path = DAILY_NOTES_DIR / f"{today}.md"
        
        timestamp = datetime.now().strftime("%H:%M")
        entry = f"\n\n## Routine: morning-briefing\n\n- **{timestamp}** — **Morning briefing**\n\n{briefing_text.strip()}\n"
        
        if note_path.exists():
            content = note_path.read_text(encoding="utf-8", errors="replace")
            note_path.write_text(content + entry, encoding="utf-8")
        else:
            note_path.write_text(f"# {today}\n{entry}", encoding="utf-8")
        print(f"[+] Briefing recorded in Obsidian vault: {note_path}")
    except Exception as e:
        print(f"[!] Could not write to vault: {e}")


def main():
    print("=" * 70)
    print(f"  MILO — MORNING BRIEFING  [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
    print("=" * 70)
    print(f"Binary: {OPENCODE_BIN}")
    print("\n[1/3] Invoking OpenCode agent (Milo) for briefing...")
    
    # Run opencode CLI directly
    cmd = [str(OPENCODE_BIN), "run", "--agent", "milo", BRIEFING_PROMPT]
    try:
        proc = subprocess.run(
            cmd,
            cwd=r"C:\Users\Administrator",
            capture_output=True,
            text=True,
            timeout=300,
            shell=True,
            encoding="utf-8",
            errors="replace"
        )
        output = (proc.stdout or "").strip()
        if proc.returncode != 0 or not output:
            err = (proc.stderr or "").strip()
            print(f"[!] OpenCode run completed with code {proc.returncode}")
            if err:
                print(f"OpenCode stderr:\n{err}")
            if not output:
                output = f"Milo Morning Brief [{datetime.now().strftime('%Y-%m-%d')}]: Systems active. OpenCode returned code {proc.returncode}."
    except Exception as e:
        print(f"[X] Error invoking OpenCode: {e}")
        output = f"Milo Morning Brief [{datetime.now().strftime('%Y-%m-%d')}]: System active. Briefing runner notice: {e}"

    print("\n" + "-" * 70)
    print("BRIEFING CONTENT:")
    print("-" * 70)
    print(output)
    print("-" * 70)

    # Save to vault
    print("\n[2/3] Saving briefing to Obsidian vault...")
    save_to_vault(output)

    # Send to Telegram
    print("\n[3/3] Sending briefing to Allan via Telegram...")
    header = f"🌅 *Milo Morning Brief* — {datetime.now().strftime('%B %d, %Y')}\n\n"
    full_message = header + output
    success = send_telegram_resilient(full_message)
    if success:
        print("[+] Briefing delivered successfully to Telegram!")
    else:
        print("[!] Warning: Failed to send briefing to Telegram.")

    print("\n" + "=" * 70)
    print(f"  COMPLETED AT: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)


if __name__ == "__main__":
    main()
