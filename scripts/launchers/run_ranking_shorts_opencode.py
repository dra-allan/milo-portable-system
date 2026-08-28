#!/usr/bin/env python
"""
run_ranking_shorts_opencode.py — OpenCode & Visible Runner for Ranking Shorts Pipeline
=======================================================================================
Runs the Ranking Shorts pipeline in a visible interactive terminal session with
live streaming logs and OpenCode agent supervision.
"""
from __future__ import annotations

import os
import sys
import time
import subprocess
from datetime import datetime
from pathlib import Path

# Fix Windows console UTF-8 encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PIPELINE_DIR = Path(r"C:\milo-portable-system\artisan\ranking-shorts-pipeline")
PYTHON_EXE = PIPELINE_DIR / "venv" / "Scripts" / "python.exe"
if not PYTHON_EXE.exists():
    PYTHON_EXE = Path(sys.executable)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8844481759:AAExAkAIOl_m_JBQ3_RxTf9tM7Afn32Y3nM")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "8101147332")


def notify_telegram(message: str):
    """Send a summary to Telegram."""
    try:
        import requests
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=15)
    except Exception as e:
        print(f"[!] Telegram notification error: {e}")


def main():
    print("=" * 75)
    print(f"  [MILO] RANKING SHORTS PIPELINE — INTERACTIVE SESSION")
    print(f"  Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 75)
    print(f"Directory: {PIPELINE_DIR}")
    print(f"Python:    {PYTHON_EXE}\n")

    # Run the pipeline sweep with live stdout streaming
    print("▶ Running ranking pipeline sweep (Discover -> Download -> Vet -> Rank -> Voiceover -> Render)...")
    cmd = [str(PYTHON_EXE), "-m", "src.main", "--mode", "sweep", "--variant", "mixed"]
    
    start_time = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PIPELINE_DIR),
            text=True,
            timeout=3600,  # Up to 1 hour
            encoding="utf-8",
            errors="replace"
        )
        elapsed = round(time.time() - start_time, 1)
        print("\n" + "=" * 75)
        if proc.returncode == 0:
            print(f"[+] Ranking pipeline run completed successfully in {elapsed}s!")
            notify_telegram(f"Milo Ranking Pipeline Report\nRanking Shorts pipeline completed successfully in {elapsed}s.")
        else:
            print(f"[!] Ranking pipeline exited with code {proc.returncode} after {elapsed}s.")
            notify_telegram(f"Milo Ranking Pipeline Notice\nRanking Shorts pipeline finished with exit code {proc.returncode}.")
    except Exception as e:
        print(f"[X] Ranking pipeline error: {e}")
        notify_telegram(f"Milo Ranking Pipeline Error\nRanking Shorts pipeline error: {e}")

    print(f"  Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 75)


if __name__ == "__main__":
    main()
