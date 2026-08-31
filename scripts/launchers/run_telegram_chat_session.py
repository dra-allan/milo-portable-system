#!/usr/bin/env python
"""
run_telegram_chat_session.py — Visible OpenCode Terminal Session for Telegram Chat
==================================================================================
Runs inside a visible cmd terminal window spawned on the VPS desktop when Allan
sends a message on Telegram. Executes OpenCode with the Milo agent, prints all
reasoning, tool calls, and outputs live to the terminal screen, and sends the
resulting response back to Telegram.
"""
from __future__ import annotations

import os
import sys
import time
import shutil
import argparse
import subprocess
from datetime import datetime
from pathlib import Path

# Fix Windows console UTF-8 encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(r"C:\milo-portable-system")
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
TELEGRAM_SEND_PY = SCRIPTS_DIR / "telegram_send.py"

OPENCODE_BIN = (
    shutil.which("opencode.cmd")
    or shutil.which("opencode")
    or r"C:\Users\Administrator\AppData\Roaming\npm\opencode.cmd"
)


def send_to_telegram(text: str, chat_id: str | None = None):
    """Send final response to Telegram using telegram_send.py."""
    if not text.strip():
        text = "(Milo completed the session with no text output)"
    cmd = [
        sys.executable,
        str(TELEGRAM_SEND_PY),
        text
    ]
    if chat_id:
        cmd.extend(["--chat-id", str(chat_id)])
    try:
        subprocess.run(cmd, timeout=30)
    except Exception as e:
        print(f"[!] Failed to deliver response to Telegram: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Run visible OpenCode session for Telegram chat")
    parser.add_argument("--prompt", "-p", required=True, help="User prompt / instruction")
    parser.add_argument("--chat-id", "-c", default=None, help="Telegram chat ID")
    parser.add_argument("--dir", "-d", default=r"C:\Users\Administrator", help="Working directory")
    parser.add_argument("--agent", "-a", default="milo", help="Agent name")
    parser.add_argument("--model", "-m", default=None, help="Model override")
    args = parser.parse_args()

    print("=" * 80)
    print(f"  [MILO] OPENCODE LIVE SESSION — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    print(f"Working Dir: {args.dir}")
    print(f"Agent:       {args.agent}")
    if args.model:
        print(f"Model:       {args.model}")
    print(f"Binary:      {OPENCODE_BIN}")
    print("-" * 80)
    print(f"USER PROMPT:\n{args.prompt}")
    print("-" * 80)
    print("\n▶ [OpenCode Executing] Live agent actions and thoughts:\n")

    # Build opencode CLI command
    cmd = [
        str(OPENCODE_BIN),
        "run",
        "--agent", args.agent,
        "--dir", args.dir,
    ]
    if args.model:
        cmd.extend(["--model", args.model])
    cmd.append(args.prompt)

    start_time = time.time()
    try:
        # Run process interactively so output streams live to this visible console
        # while capturing output for Telegram delivery
        proc = subprocess.Popen(
            cmd,
            cwd=args.dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=True
        )

        output_lines = []
        for line in iter(proc.stdout.readline, ""):
            sys.stdout.write(line)
            sys.stdout.flush()
            output_lines.append(line)

        proc.wait(timeout=600)
        elapsed = round(time.time() - start_time, 1)
        full_output = "".join(output_lines).strip()

        print("\n" + "=" * 80)
        print(f"  [SESSION FINISHED] Exit code: {proc.returncode} | Elapsed: {elapsed}s")
        print("=" * 80)

        # Deliver to Telegram
        print("\n[+] Delivering response to Telegram...")
        send_to_telegram(full_output, chat_id=args.chat_id)
        print("[+] Done!")

    except Exception as e:
        print(f"\n[X] Error during OpenCode execution: {e}", file=sys.stderr)
        send_to_telegram(f"❌ Milo execution error: {e}", chat_id=args.chat_id)

    print("\nWindow will remain open for inspection. Press Enter or close window when done.")
    try:
        input()
    except Exception:
        pass


if __name__ == "__main__":
    main()
