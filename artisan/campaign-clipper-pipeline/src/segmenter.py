"""Choosing which seconds of a source file to ship.

Windows are generated broadly, then ranked automatically by visual activity,
audio energy, and Whisper's setup -> payoff score. There is no approval queue: a
batch can produce 100 clips unattended while still preferring complete moments
over random loud cuts.
"""

from typing import Dict, List, Optional

from .config import config
from .highlights import rank_windows
from .spec import CampaignSpec
from .utils import scene_times, setup_logger, window_loudness

logger = setup_logger(__name__)


def _target_duration(spec: CampaignSpec) -> float:
    low = spec.render.min_duration + 1.5
    high = spec.render.max_duration
    return max(low, min(config.target_duration, high))


def _nearest_boundary(value: float, boundaries: List[float], window: float = 2.5) -> float:
    if not boundaries:
        return value
    best = min(boundaries, key=lambda b: abs(b - value))
    return best if abs(best - value) <= window else value


def candidate_windows(source: Dict, spec: CampaignSpec, db,
                      limit: Optional[int] = None, use_scenes: bool = True) -> List[Dict]:
    duration = float(source.get('duration') or 0.0)
    fingerprint = source.get('fingerprint') or ''
    target = _target_duration(spec)
    limit = limit or config.clips_per_source
    usable_start = config.head_trim
    usable_end = max(0.0, duration - config.tail_trim)
    if usable_end - usable_start < spec.render.min_duration:
        return []

    boundaries = []
    if use_scenes:
        boundaries = [t for t in scene_times(source['local_path'], config.scene_threshold)
                      if usable_start <= t <= usable_end - target]
    stride = max(2.0, target / 2.0)
    raw: List[Dict] = []
    cursor = usable_start
    while cursor + target <= usable_end:
        start = _nearest_boundary(cursor, boundaries)
        start = max(usable_start, min(start, usable_end - target))
        end = start + target
        if not db.window_overlaps(spec.id, fingerprint, start, end):
            raw.append({'start': round(start, 3), 'duration': round(target, 3),
                        'end': round(end, 3)})
        cursor += stride

    seen, unique = set(), []
    for window in raw:
        key = round(window['start'], 1)
        if key not in seen:
            seen.add(key)
            unique.append(window)
    if not unique:
        return []

    for window in unique:
        window['scenes'] = sum(1 for b in boundaries
                               if window['start'] <= b <= window['end'])
        if config.score_audio and source.get('has_audio'):
            window['loudness'] = window_loudness(source['local_path'],
                                                 window['start'], window['duration'])
        else:
            window['loudness'] = -99.0
        window['motion_score'] = _motion_score(window)
        window['audio_score'] = _audio_score(window['loudness'])
        window['score'] = round(0.6 * window['motion_score'] +
                                0.4 * window['audio_score'], 4)

    # Whisper is applied to the full candidate set, then only the top windows
    # proceed. It is cached per source, so this scales to large batches.
    unique = rank_windows(unique, source, spec, db=db)
    picked = _spread(unique, limit, target)
    for window in picked:
        logger.info('WINDOW file=%s start=%.2f dur=%.2f score=%.3f setup=%.3f payoff=%.3f',
                    source.get('filename'), window['start'], window['duration'],
                    window['score'], window.get('setup_score', 0.0),
                    window.get('payoff_score', 0.0))
    return picked


def _motion_score(window: Dict) -> float:
    per_second = window.get('scenes', 0) / max(1.0, window.get('duration', 1.0))
    return min(1.0, per_second / 0.6)


def _audio_score(loudness: float) -> float:
    return 0.0 if loudness <= -60 else min(1.0, (loudness + 60.0) / 45.0)


def _spread(windows: List[Dict], limit: int, target: float) -> List[Dict]:
    picked: List[Dict] = []
    for window in windows:
        if len(picked) >= limit:
            break
        clash = any(min(window['end'], other['end']) - max(window['start'], other['start']) > target * 0.25
                    for other in picked)
        if not clash:
            picked.append(window)
    return picked


def plan_clips(sources: List[Dict], spec: CampaignSpec, db,
               wanted: Optional[int] = None) -> List[Dict]:
    wanted = wanted or config.clips_per_run
    per_source: List[List[Dict]] = []
    for source in sources:
        per_source.append([{**window, 'fingerprint': source['fingerprint'],
                            'source_name': source['filename'],
                            'source_path': source['local_path'],
                            'has_audio': source.get('has_audio', False),
                            'source_width': source.get('width', 0),
                            'source_height': source.get('height', 0)}
                           for window in candidate_windows(source, spec, db)])
    plans: List[Dict] = []
    depth = 0
    while len(plans) < wanted and any(len(w) > depth for w in per_source):
        for windows in per_source:
            if len(plans) >= wanted:
                break
            if len(windows) > depth:
                plans.append(windows[depth])
        depth += 1
    logger.info('PLANNED campaign=%s clips=%d (wanted %d, sources %d)',
                spec.id, len(plans), wanted, len(sources))
    return plans


def _score(window: Dict) -> float:
    return round(0.6 * _motion_score(window) + 0.4 * _audio_score(window.get('loudness', -99)), 4)
