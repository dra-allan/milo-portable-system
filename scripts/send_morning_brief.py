#!/usr/bin/env python
"""Send morning brief to Telegram daily."""
import re
from pathlib import Path
from datetime import datetime
import requests

TELEGRAM_BOT_TOKEN = "8844481759:AAExAkAIOl_m_JBQ3_RxTf9tM7Afn32Y3nM"
TELEGRAM_CHAT_ID = "8101147332"

VAULT_DIR = Path(r"C:\Users\Administrator\Desktop\dra-brains")
DAILY_NOTES_DIR = VAULT_DIR / "01 - Daily Notes"

def escape_md(text: str) -> str:
    """Escape markdown special chars for Telegram."""
    for ch in r"_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text

def get_today_brief() -> str | None:
    today = datetime.now().strftime("%Y-%m-%d")
    note_path = DAILY_NOTES_DIR / f"{today}.md"
    if not note_path.exists():
        return None
    content = note_path.read_text(encoding="utf-8")
    match = re.search(r"## Routine: morning-briefing\n\n- \*\*\d{2}:\d{2}\*\* — \*\*Morning briefing — [^\n]+\*\*(.*?)(?=\n## |\n---|\Z)", content, re.DOTALL)
    if match:
        brief = match.group(1).strip()
        return f"Morning Brief — {today}\n\n{brief}"
    return None

def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # Escape markdown, send as plain text to avoid parse errors
    safe_text = escape_md(text)
    if len(safe_text) > 4000:
        safe_text = safe_text[:3900] + "\n...[truncated]"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": safe_text,
        "parse_mode": "MarkdownV2"
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        if not resp.ok:
            print(f"Telegram error: {resp.status_code} {resp.text}")
        return resp.ok
    except Exception as e:
        print(f"Telegram send failed: {e}")
        return False

def main():
    brief = get_today_brief()
    if brief:
        if send_telegram(brief):
            print("Morning brief sent to Telegram")
        else:
            print("Failed to send morning brief")
    else:
        print("No morning brief found for today")

if __name__ == "__main__":
    main()