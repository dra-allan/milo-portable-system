"""Scheduled discovery with a hard one-source-video-per-niche daily guard.

TITLE GATING
------------
Two independent filters run against the source title, and it matters that they
are not symmetric:

* ``negative_keywords`` -- always on. Rejects a title on any match. Every niche
  uses it, and it should carry FORMAT exclusions ("#shorts", "music video",
  "fan edit"), not topic words.
* ``require_keywords`` -- opt-in. When present, a title must match at least one
  entry or the video is dropped as off-topic.

``require_keywords`` exists because ``keywords`` does NOT filter anything here.
It is only read later by ``processor.py`` to score highlight windows inside an
already-accepted video. That is fine for a niche sourced from single-subject
channels, where every upload is on-topic by construction. It is wrong for a
niche sourced from broad channels: a GTA niche listing @IGN would ingest their
Nintendo upload and publish it, because nothing in this function ever asks
whether the video is about GTA.

The filter is skipped entirely when the key is absent, so all pre-existing
niches are unaffected.
"""
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List

@lru_cache(maxsize=2048)
def _keyword_pattern(keyword: str):
    parts=[re.escape(p) for p in str(keyword).lower().split() if p]
    if not parts:return None
    body=r'\s+'.join(parts)
    return re.compile((r'\b' if str(keyword).lower().lstrip()[:1].isalnum() else '')+body+(r'\b' if str(keyword).lower().rstrip()[-1:].isalnum() else ''))

def matches_keyword(text: str, keyword: str) -> bool:
    p=_keyword_pattern(keyword); return bool(p and p.search((text or '').lower()))
def matched_keywords(text: str, keywords) -> List[str]: return [str(k) for k in (keywords or []) if k and matches_keyword(text,k)]

@dataclass
class DiscoveryResult:
    candidates: List[Dict]=field(default_factory=list)
    skipped_already_processed: List[str]=field(default_factory=list)
    skipped_duration: List[str]=field(default_factory=list)
    skipped_negative_keywords: List[str]=field(default_factory=list)
    # Titles that matched no entry in a niche's `require_keywords`. Kept as its
    # own bucket rather than folded into skipped_negative_keywords: an empty
    # candidate list plus a full off-topic bucket means the gate is too tight,
    # which is a completely different fix from a negative-keyword collision.
    skipped_off_topic: List[str]=field(default_factory=list)
    skipped_min_views: List[str]=field(default_factory=list)
    channels_queried: List[str]=field(default_factory=list)

def _source_rank(channels,source_performance):
    def key(c):
        info=(source_performance or {}).get(c) or {}
        if not info.get('recorded'): return (1,0,c)
        avg=float(info.get('avg_views') or 0); return (0 if avg>=200 else 2,-avg,c)
    return sorted(channels,key=key)

def discover_candidates(downloader,db,niche,max_videos:int,lookback:int,source_performance:Dict[str,Dict]=None)->DiscoveryResult:
    from .config import config
    result=DiscoveryResult()
    cfg=config.get_niche_config(niche)
    channels=_source_rank([c for c in (cfg.get('channels') or []) if c and not str(c).startswith('UCXXXXX')],source_performance or {})
    lookback=max(lookback,max_videos)
    result.channels_queried.extend(channels)
    # Opt-in topic gate. Absent key -> empty list -> filter never runs, so the
    # behaviour of every existing niche is byte-for-byte unchanged.
    require=[k for k in (cfg.get('require_keywords') or []) if k]
    listings={}
    batch=getattr(downloader,'search_videos_by_channels',None)
    if callable(batch) and len(channels)>1:
        try:listings=batch(channels,max_results=lookback) or {}
        except Exception:listings={}
    for channel in channels:
        try: found=listings.get(channel) if channel in listings else downloader.search_videos_by_channel(channel,published_after='',max_results=lookback)
        except Exception: continue
        for entry in (found or []):
            vid=(entry or {}).get('id')
            if not vid: continue
            if db.is_video_processed(vid): result.skipped_already_processed.append(vid); continue
            duration=entry.get('duration') or 0; min_dur=int(cfg.get('min_duration') or 0); max_dur=int(cfg.get('max_duration') or 0)
            if min_dur and duration and duration<min_dur: result.skipped_duration.append(vid); continue
            if max_dur and duration and duration>max_dur: result.skipped_duration.append(vid); continue
            title=str(entry.get('title') or '')
            if any(matches_keyword(title,k) for k in (cfg.get('negative_keywords') or [])): result.skipped_negative_keywords.append(vid); continue
            if require and not any(matches_keyword(title,k) for k in require): result.skipped_off_topic.append(vid); continue
            if int(cfg.get('min_views') or 0) and (entry.get('view_count') or 0)<int(cfg.get('min_views') or 0): result.skipped_min_views.append(vid); continue
            item=dict(entry); item['_source_channel']=channel; result.candidates.append(item)
    return result
