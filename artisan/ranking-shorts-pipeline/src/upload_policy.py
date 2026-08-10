"""Shared upload policy primitives for ranking publishing.

The caller must group its queue by channel before uploading. A channel that has
no remaining budget is removed from the active queue, never revisited during
that run. Missing files and unauthenticated channels are skips, not retries.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Set

@dataclass
class UploadSummary:
    queued: int = 0
    uploaded: int = 0
    missing: int = 0
    unauthenticated: int = 0
    cap_skips: int = 0
    failed: int = 0
    channel_rows: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def print(self) -> None:
        print("\n=== RANKING UPLOAD SUMMARY ===")
        print(f"  queued:          {self.queued}")
        print(f"  uploaded:        {self.uploaded}")
        print(f"  missing files:   {self.missing}")
        print(f"  unauthenticated: {self.unauthenticated}")
        print(f"  cap skips:       {self.cap_skips}")
        print(f"  failed:          {self.failed}")
        for channel, row in self.channel_rows.items():
            print(f"  {channel}: {row.get('uploaded', 0)} uploaded, "
                  f"{row.get('remaining', 0)}/{row.get('cap', 0)} budget left")

def authenticated_channels(token_base: Path) -> Set[str]:
    parent = Path(token_base).parent
    prefix = 'youtube_token_ranking_'
    return {p.name[len(prefix):-5] for p in parent.glob(prefix + '*.json')
            if p.is_file() and p.name.endswith('.json')}

def remaining_budget(db, channel: str, cap: int) -> int:
    return max(0, int(cap) - int(db.uploaded_count_for_channel_since(channel)))

def active_channels(db, channels, authenticated, cap, summary):
    active = []
    for channel in channels:
        left = remaining_budget(db, channel, cap)
        summary.channel_rows[channel] = {'uploaded': 0, 'remaining': left, 'cap': cap}
        if channel not in authenticated:
            summary.unauthenticated += 1
            continue
        if left <= 0:
            summary.cap_skips += 1
            continue
        active.append(channel)
    return active
