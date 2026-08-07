"""Scheduled discovery: which videos should this run pick up?

The channel listing is cheap (yt-dlp flat playlist, metadata only). The
expensive parts -- download, transcription, rendering -- are downstream and
must never run on a video we already processed or that can't produce a usable
clip. So discovery is a pure filter pipeline: fetch candidates per channel,
drop placeholders, drop already-processed IDs, drop out-of-band durations,
drop negative-keyword titles, then rank and slice to the budget.
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class DiscoveryResult:
    candidates: List[Dict] = field(default_factory=list)
    skipped_already_processed: List[str] = field(default_factory=list)
    skipped_duration: List[str] = field(default_factory=list)
    skipped_negative_keywords: List[str] = field(default_factory=list)
    channels_queried: List[str] = field(default_factory=list)


def discover_candidates(downloader, db, niche, max_videos: int, lookback: int) -> DiscoveryResult:
    """Return the videos a scheduled run should process for `niche`.

    Args:
        downloader: has `search_videos_by_channel(channel_id, published_after='',
            max_results=10)`.
        db: has `is_video_processed(video_id) -> bool`.
        niche: niche name; its config is read from `config.get_niche_config`.
        max_videos: how many videos to keep for the run.
        lookback: how many recent videos to pull per channel before filtering
            (must be >= max_videos so dedup can't starve the result).
    """
    from .config import config

    cfg = config.get_niche_config(niche)
    channels = [c for c in (cfg.get('channels') or [])
                if c and not str(c).startswith('UCXXXXX')]

    result = DiscoveryResult()
    lookback = max(lookback, max_videos)

    for channel in channels:
        result.channels_queried.append(channel)
        try:
            found = downloader.search_videos_by_channel(
                channel, published_after='', max_results=lookback
            )
        except Exception:
            continue
        for entry in (found or []):
            vid = (entry or {}).get('id')
            if not vid:
                continue
            if db.is_video_processed(vid):
                result.skipped_already_processed.append(vid)
                continue

            duration = entry.get('duration') or 0
            min_dur = int(cfg.get('min_duration') or 0)
            max_dur = int(cfg.get('max_duration') or 0)
            if min_dur and duration and duration < min_dur:
                result.skipped_duration.append(vid)
                continue
            if max_dur and duration and duration > max_dur:
                result.skipped_duration.append(vid)
                continue

            title = str(entry.get('title') or '').lower()
            neg = [str(k).lower() for k in (cfg.get('negative_keywords') or []) if k]
            if any(k in title for k in neg):
                result.skipped_negative_keywords.append(vid)
                continue

            result.candidates.append(entry)

    return result
