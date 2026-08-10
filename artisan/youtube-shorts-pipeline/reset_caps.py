"""Reset today's daily upload and source caps.

Resets the 24-hour upload window counters for channels and source videos,
and clears the dead-channel cache so discovery and uploads are unconstrained.
"""

import json
import sqlite3
from pathlib import Path

REPO_ROOT = Path(r'C:\Users\user\Desktop\milo-portable-system\artisan\youtube-shorts-pipeline')
DB_PATH = REPO_ROOT / 'data' / 'processed_videos.db'
DEAD_CHANNELS_PATH = REPO_ROOT / 'data' / 'dead_channels.json'


def clear_daily_caps():
    if not DB_PATH.exists():
        print("Database file not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check recent uploads
    cursor.execute("""
        SELECT COUNT(*) FROM generated_shorts
        WHERE uploaded_at IS NOT NULL AND uploaded_at >= datetime('now', '-24 hours')
    """)
    recent_count = cursor.fetchone()[0]

    print(f"Found {recent_count} uploads recorded in the last 24 hours.")

    # Shift uploaded_at timestamps back by 25 hours to clear the daily budget
    cursor.execute("""
        UPDATE generated_shorts
        SET uploaded_at = datetime(uploaded_at, '-25 hours')
        WHERE uploaded_at IS NOT NULL AND uploaded_at >= datetime('now', '-24 hours')
    """)
    conn.commit()
    conn.close()

    print(f"Cleared daily upload limits for {recent_count} shorts.")

    # Clear dead channels cache
    if DEAD_CHANNELS_PATH.exists():
        backup_path = DEAD_CHANNELS_PATH.with_suffix('.json.bak')
        with open(DEAD_CHANNELS_PATH, 'r', encoding='utf-8') as f:
            data = f.read()
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(data)
        
        with open(DEAD_CHANNELS_PATH, 'w', encoding='utf-8') as f:
            f.write('{}')
        print("Cleared dead-channel cache.")

    print("\nDaily upload caps successfully reset! All channels now have full upload capacity.")


if __name__ == '__main__':
    clear_daily_caps()
