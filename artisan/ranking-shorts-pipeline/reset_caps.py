#!/usr/bin/env python3
"""Reset the ranking daily-upload cap window in the local database.

The 24h daily cap (UPLOAD_MAX_PER_DAY) counts builds whose uploaded_at falls
inside the last day. This zeroes those timestamps so the next run can post
again immediately. It does NOT re-upload anything and does NOT change statuses;
it only forgets *when* recent uploads happened.
"""
from __future__ import annotations
from src.config import config
from src.database import RankingDatabase


def main() -> int:
    db = RankingDatabase(config.db_path)
    with db._connect() as conn:
        cur = conn.execute(
            "UPDATE builds SET uploaded_at = NULL "
            "WHERE status='uploaded' AND uploaded_at IS NOT NULL"
        )
    print(f'RESET_CAPS cleared upload timestamps on {cur.rowcount} build(s); '
          f'daily cap window is now empty')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
