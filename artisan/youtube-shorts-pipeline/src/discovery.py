"""Scheduled discovery: which videos should this run pick up?

The channel listing is cheap (yt-dlp flat playlist, metadata only). The
expensive parts -- download, transcription, rendering -- are downstream and
must never run on a video we already processed or that can't produce a usable
clip. So discovery is a pure filter pipeline: fetch candidates per channel,
drop placeholders, drop already-processed IDs, drop out-of-band durations,
drop negative-keyword titles, drop below-threshold view counts, then rank and
slice to the budget.

Sources are also ranked from the performance feedback loop: a channel whose
recent clips perform well is queried first, one that consistently underperforms
is deprioritised (soft demotion) rather than dropped, so a slow start never
kills a source outright but a proven winner gets fed first.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List


@lru_cache(maxsize=2048)
def _keyword_pattern(keyword: str):
    """Compile a word-boundary matcher for one keyword or phrase.

    Plain substring matching (``keyword in title``) silently rejects good
    sources because short negative keywords appear *inside* ordinary words:

        "live"    matches "De-liver-ed", "Lives", "Olive"
        "dance"   matches "Abun-dance-", "Guidance"
        "guide"   matches "Guide-d Missiles"
        "concert" matches "Concert-ed"
        "meme"    matches "Meme-nto"

    On a ranking/top-10 niche that misfire rejected 5 of 6 realistic titles.
    Matching on word boundaries keeps multi-word phrases working ("live
    stream" still matches "Live stream: full show") while requiring whole
    words, so "Deadliest Animals That Live" is only rejected by "live"
    because the word really is present -- not by an accident of spelling.
    """
    # Collapse internal whitespace so "live  stream" and "live stream" agree,
    # and allow any whitespace run between words of a phrase.
    parts = [re.escape(p) for p in str(keyword).lower().split() if p]
    if not parts:
        return None
    body = r'\s+'.join(parts)
    # \b is wrong next to non-word chars (e.g. "#shorts", "vs."), so only
    # anchor the edges that actually start/end with a word character.
    left = r'\b' if re.match(r'\w', parts[0][0] if parts[0] else '') else ''
    prefix = left if str(keyword).lower().lstrip()[:1].isalnum() else ''
    suffix = r'\b' if str(keyword).lower().rstrip()[-1:].isalnum() else ''
    return re.compile(prefix + body + suffix)


def matches_keyword(text: str, keyword: str) -> bool:
    """True if ``keyword`` occurs in ``text`` as a whole word/phrase."""
    pattern = _keyword_pattern(keyword)
    if pattern is None:
        return False
    return bool(pattern.search((text or '').lower()))


def matched_keywords(text: str, keywords) -> List[str]:
    """Every keyword from ``keywords`` present in ``text`` as a whole word."""
    low = (text or '').lower()
    hits = []
    for kw in (keywords or []):
        if not kw:
            continue
        pattern = _keyword_pattern(kw)
        if pattern is not None and pattern.search(low):
            hits.append(str(kw))
    return hits


@dataclass
class DiscoveryResult:
    candidates: List[Dict] = field(default_factory=list)
    skipped_already_processed: List[str] = field(default_factory=list)
    skipped_duration: List[str] = field(default_factory=list)
    skipped_negative_keywords: List[str] = field(default_factory=list)
    skipped_min_views: List[str] = field(default_factory=list)
    channels_queried: List[str] = field(default_factory=list)


def _source_rank(channels: List[str], source_performance: Dict[str, Dict]) -> List[str]:
    """Order source channels: proven performers first, then untested, then
    confirmed underperformers last (soft demotion).

    ``source_performance`` maps a channel key to stats gathered from the
    feedback loop (``recorded`` clips, ``avg_views``, ``last_views``). A
    channel with no recorded clips yet keeps its natural position so new
    sources still get discovered.
    """
    def order_key(channel: str):
        info = (source_performance or {}).get(channel) or {}
        if not info.get('recorded'):
            # Untested: middle band, stable within group order.
            return (1, 0, channel)
        avg = float(info.get('avg_views') or 0)
        return (0 if avg >= 200 else 2, -avg, channel)

    return sorted(channels, key=order_key)


def discover_candidates(downloader, db, niche, max_videos: int, lookback: int,
                        source_performance: Dict[str, Dict] = None) -> DiscoveryResult:
    """Return the videos a scheduled run should process for `niche`.

    Args:
        downloader: has `search_videos_by_channel(channel_id, published_after='',
            max_results=10)`.
        db: has `is_video_processed(video_id) -> bool`.
        niche: niche name; its config is read from `config.get_niche_config`.
        max_videos: how many videos to keep for the run.
        lookback: how many recent videos to pull per channel before filtering
            (must be >= max_videos so dedup can't starve the result).
        source_performance: optional dict of channel key -> stats from the
            feedback loop; used to order source channels (winners first).
    """
    from .config import config

    cfg = config.get_niche_config(niche)
    channels = [c for c in (cfg.get('channels') or [])
                if c and not str(c).startswith('UCXXXXX')]
    channels = _source_rank(channels, source_performance or {})

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

            # Word-boundary matching, not substring: see _keyword_pattern.
            title = str(entry.get('title') or '')
            neg = [k for k in (cfg.get('negative_keywords') or []) if k]
            if any(matches_keyword(title, k) for k in neg):
                result.skipped_negative_keywords.append(vid)
                continue

            # View-count gate: only clip from sources the algorithm already
            # proved. view_count comes back from the flat listing, so this
            # costs nothing extra. 0 = gate disabled.
            min_views = int(cfg.get('min_views') or 0)
            if min_views and (entry.get('view_count') or 0) < min_views:
                result.skipped_min_views.append(vid)
                continue

            entry = dict(entry)
            entry['_source_channel'] = channel
            result.candidates.append(entry)

    return result
