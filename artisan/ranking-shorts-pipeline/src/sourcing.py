"""YouTube clip sourcing using the same yt-dlp pattern as the Shorts pipeline.

Discovery is metadata-first: list YouTube search results and configured channel
video feeds with yt-dlp, filter them, then download only candidates that can
actually be vetted. Runtime files are written through config.clips_dir, which
is outside the repository.
"""

import re
from pathlib import Path
from typing import Dict, List, Optional

from .config import config
from .utils import ensure_dir, safe_slug, setup_logger

logger = setup_logger(__name__)


def _ydl(opts: Dict):
    from yt_dlp import YoutubeDL
    base = {'quiet': True, 'no_warnings': True, 'noprogress': True,
            'ignoreerrors': True, 'retries': 3, 'socket_timeout': 30}
    base.update(opts)
    return YoutubeDL(base)


def _youtube_target(value: str) -> str:
    """Turn a channel handle/URL into its videos feed for yt-dlp."""
    value = str(value or '').strip()
    if not value:
        return ''
    if value.startswith('@'):
        return f'https://www.youtube.com/{value}/videos'
    if value.startswith('UC') and '/' not in value:
        return f'https://www.youtube.com/channel/{value}/videos'
    return value


def _matches_negative(title: str, negatives: List[str]) -> Optional[str]:
    haystack = (title or '').lower()
    for negative in negatives:
        needle = (negative or '').strip().lower()
        if needle and re.search(r'(?<!\w)' + re.escape(needle) + r'(?!\w)', haystack):
            return needle
    return None


def discover(topic_cfg: Dict, db, limit: Optional[int] = None) -> List[Dict]:
    """Return filtered YouTube candidates, newest first."""
    limit = limit or int(config.get('candidates_per_topic', 40))
    max_duration = float(config.get('max_source_duration', 900))
    min_views = int(config.get('min_source_views', 500))

    targets: List[str] = []
    queries = topic_cfg.get('queries') or []
    per_query = max(5, limit // max(1, len(queries)))
    targets.extend(f'ytsearchdate{per_query}:{query}' for query in queries)
    targets.extend(_youtube_target(ch) for ch in (topic_cfg.get('channels') or []))
    # Keep this escape hatch for future sources, but the shipped config is
    # YouTube-only and the autonomous path never invents non-YouTube URLs.
    targets.extend(topic_cfg.get('extra_sources') or [])
    targets = [t for t in targets if t]

    found, seen_urls = [], set()
    with _ydl({'extract_flat': 'in_playlist', 'skip_download': True}) as ydl:
        for target in targets:
            logger.info('discovering YouTube source: %s', target)
            try:
                info = ydl.extract_info(target, download=False)
            except Exception as exc:
                logger.warning('source failed (%s): %s', target, exc)
                continue
            if not info:
                continue
            entries = info.get('entries') or ([info] if info.get('id') else [])
            for entry in entries:
                if not entry:
                    continue
                url = entry.get('webpage_url') or entry.get('url') or ''
                if url and not url.startswith('http'):
                    url = f"https://www.youtube.com/watch?v={entry.get('id')}"
                if not url or url in seen_urls or 'youtube.com' not in url:
                    continue
                seen_urls.add(url)
                title = entry.get('title') or ''
                duration = float(entry.get('duration') or 0.0)
                views = int(entry.get('view_count') or 0)
                if db.is_used(url) or db.is_rejected(url):
                    continue
                hit = _matches_negative(title, topic_cfg.get('negative_keywords') or [])
                if hit:
                    db.mark_rejected(url, topic_cfg['name'], f'negative:{hit}')
                    continue
                if duration and duration > max_duration:
                    db.mark_rejected(url, topic_cfg['name'], 'too_long')
                    continue
                if views and views < min_views:
                    continue
                found.append({'url': url, 'source_id': entry.get('id') or '',
                              'title': title, 'duration': duration, 'views': views,
                              'uploader': entry.get('uploader') or entry.get('channel') or '',
                              'extractor': entry.get('ie_key') or info.get('extractor') or ''})
                if len(found) >= limit:
                    return found
    logger.info('%d YouTube candidate(s) after metadata filtering', len(found))
    return found


def download(candidate: Dict, dest_dir: Optional[Path] = None) -> Optional[Path]:
    """Download one YouTube candidate into the external clips directory."""
    dest_dir = ensure_dir(dest_dir or config.clips_dir)
    stem = safe_slug(f"{candidate.get('source_id') or ''}_{candidate.get('title') or 'clip'}")
    template = str(dest_dir / f'{stem}.%(ext)s')
    opts = {'outtmpl': template,
            'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]/best',
            'merge_output_format': 'mp4', 'noplaylist': True,
            'concurrent_fragment_downloads': 4}
    try:
        with _ydl(opts) as ydl:
            info = ydl.extract_info(candidate['url'], download=True)
    except Exception as exc:
        logger.warning('download failed for %s: %s', candidate['url'], exc)
        return None
    if not info:
        return None
    for path in sorted(dest_dir.glob(f'{stem}.*')):
        if path.suffix.lower() in ('.mp4', '.mkv', '.webm', '.mov'):
            candidate['local_path'] = str(path)
            candidate['title'] = info.get('title') or candidate.get('title')
            return path
    logger.warning('download reported success but no file matched %s', stem)
    return None
