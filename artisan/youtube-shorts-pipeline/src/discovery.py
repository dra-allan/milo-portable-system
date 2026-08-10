"""Scheduled discovery with a hard one-source-video-per-niche daily guard."""
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
    skipped_min_views: List[str]=field(default_factory=list)
    channels_queried: List[str]=field(default_factory=list)
    daily_cap_hit: bool=False

def _source_rank(channels,source_performance):
    def key(c):
        info=(source_performance or {}).get(c) or {}
        if not info.get('recorded'): return (1,0,c)
        avg=float(info.get('avg_views') or 0); return (0 if avg>=200 else 2,-avg,c)
    return sorted(channels,key=key)

def _niche_processed_today(db,niche):
    """Return the most recent processed source for this niche in 24h.

    This is deliberately checked before listing any YouTube channels. The DB,
    not a batch-file variable or an in-memory counter, is the source of truth,
    so direct `python -m src.main` runs obey the same cap as scheduled runs.
    """
    try:
        with db._connect() as conn:
            return conn.execute("SELECT youtube_video_id FROM processed_videos WHERE niche=? AND processed_at >= datetime('now','-24 hours') ORDER BY processed_at DESC LIMIT 1",(niche,)).fetchone()
    except Exception:
        return None

def discover_candidates(downloader,db,niche,max_videos:int,lookback:int,source_performance:Dict[str,Dict]=None)->DiscoveryResult:
    from .config import config
    result=DiscoveryResult()
    recent=_niche_processed_today(db,niche)
    if recent:
        result.daily_cap_hit=True
        result.skipped_already_processed.append(recent[0])
        logger=getattr(downloader,'logger',None)
        if logger: logger.info("Niche '%s': daily sourcing cap hit; last source=%s",niche,recent[0])
        return result
    cfg=config.get_niche_config(niche)
    channels=_source_rank([c for c in (cfg.get('channels') or []) if c and not str(c).startswith('UCXXXXX')],source_performance or {})
    lookback=max(lookback,max_videos)
    result.channels_queried.extend(channels)
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
            if int(cfg.get('min_views') or 0) and (entry.get('view_count') or 0)<int(cfg.get('min_views') or 0): result.skipped_min_views.append(vid); continue
            item=dict(entry); item['_source_channel']=channel; result.candidates.append(item)
    return result
