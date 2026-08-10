"""Clip sourcing.

yt-dlp does all the work; this module is about *what to ask it for* and what to
throw away before spending bandwidth.

Discovery is two-phase on purpose. A flat extract (metadata only, no formats)
costs one request per result and lets duration, view count, title and the used/
rejected history filter the list first. Downloading before filtering means
pulling tens of megabytes per candidate to discover it is a compilation with a
music bed - and the vetting pass rejects most candidates.

On platform coverage: YouTube exposes a search endpoint yt-dlp can drive
(``ytsearchdate``), so that path is fully autonomous. TikTok and Instagram do
not - there is no stable search - so those are configured as explicit creator or
hashtag pages in ``extra_sources`` and walked for new uploads each run. That is
a real limitation, not an oversight.
"""

import re
from pathlib import Path
from typing import Dict, List, Optional

from .config import config
from .utils import ensure_dir, safe_slug, setup_logger

logger = setup_logger(__name__)


def _ydl(opts: Dict):
    from yt_dlp import YoutubeDL
    base = {
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
        'ignoreerrors': True,
        'retries': 3,
        'socket_timeout': 30,
    }
    base.update(opts)
    return YoutubeDL(base)


def _matches_negative(title: str, negatives: List[str]) -> Optional[str]:
    """Word-boundary matching, not substring.

    Substring matching is what makes negative keyword lists quietly useless:
    ``live`` rejects "deLIVEred", ``vs`` rejects "reVerSal". Multi-word phrases
    still work because the pattern is built from the escaped phrase.
    """
    haystack = (title or '').lower()
    for negative in negatives:
        needle = (negative or '').strip().lower()
        if not needle:
            continue
        pattern = r'(?<!\w)' + re.escape(needle) + r'(?!\w)'
        if re.search(pattern, haystack):
            return needle
    return None


def discover(topic_cfg: Dict, db, limit: Optional[int] = None) -> List[Dict]:
    """Return filtered clip candidates for a topic, newest first."""
    limit = limit or int(config.get('candidates_per_topic', 40))
    max_duration = float(config.get('max_source_duration', 900))
    min_views = int(config.get('min_source_views', 500))
    negatives = topic_cfg.get('negative_keywords') or []

    targets: List[str] = []
    per_query = max(5, limit // max(1, len(topic_cfg.get('queries') or [1])))
    for query in topic_cfg.get('queries') or []:
        targets.append(f'ytsearchdate{per_query}:{query}')
    targets.extend(topic_cfg.get('extra_sources') or [])

    found: List[Dict] = []
    seen_urls = set()

    with _ydl({'extract_flat': 'in_playlist', 'skip_download': True}) as ydl:
        for target in targets:
            logger.info('discovering: %s', target)
            try:
                info = ydl.extract_info(target, download=False)
            except Exception as exc:  # noqa: BLE001 - one dead source must not
                logger.warning('source failed (%s): %s', target, exc)
                continue
            if not info:
                continue
            entries = info.get('entries') or ([info] if info.get('id') else [])
            for entry in entries:
                if not entry:
                    continue
                url = (entry.get('url') or entry.get('webpage_url') or '')
                if url and not url.startswith('http'):
                    url = f"https://www.youtube.com/watch?v={entry.get('id')}"
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                title = entry.get('title') or ''
                duration = float(entry.get('duration') or 0.0)
                views = int(entry.get('view_count') or 0)

                if db.is_used(url):
                    continue
                if db.is_rejected(url):
                    continue
                hit = _matches_negative(title, negatives)
                if hit:
                    db.mark_rejected(url, topic_cfg['name'],
                                     f'negative:{hit}')
                    continue
                if duration and duration > max_duration:
                    db.mark_rejected(url, topic_cfg['name'], 'too_long')
                    continue
                if views and views < min_views:
                    continue

                found.append({
                    'url': url,
                    'source_id': entry.get('id') or '',
                    'title': title,
                    'duration': duration,
                    'views': views,
                    'uploader': entry.get('uploader') or entry.get('channel')
                    or '',
                    'extractor': entry.get('ie_key') or info.get('extractor')
                    or '',
                })
                if len(found) >= limit:
                    break
            if len(found) >= limit:
                break

    logger.info('%d candidate(s) after metadata filtering', len(found))
    return found


def download(candidate: Dict, dest_dir: Optional[Path] = None) -> Optional[Path]:
    """Fetch one candidate. Returns the local file, or None.

    Capped at 1080p: the output is 1080x1920 with the source letterboxed into
    it, so a 4K download is bandwidth spent on pixels that get scaled away.
    """
    dest_dir = ensure_dir(dest_dir or config.clips_dir)
    stem = safe_slug(f"{candidate.get('source_id') or ''}_"
                     f"{candidate.get('title') or 'clip'}")
    template = str(dest_dir / f'{stem}.%(ext)s')

    opts = {
        'outtmpl': template,
        'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]/best',
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'concurrent_fragment_downloads': 4,
    }
    try:
        with _ydl(opts) as ydl:
            info = ydl.extract_info(candidate['url'], download=True)
    except Exception as exc:  # noqa: BLE001
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
