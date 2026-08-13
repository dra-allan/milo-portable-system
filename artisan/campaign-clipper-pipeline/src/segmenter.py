"""Choosing which seconds of a source file to ship.

Cut on detected scene boundaries, not on a fixed grid
-----------------------------------------------------
A grid cut (``0-20s``, ``20-40s``, ...) lands mid-sentence and mid-action. That
is visibly the low-effort output every one of these campaigns rejects by name,
and it is free to avoid: the scene timestamps are already computed for the
source, so snapping each window's start to the nearest boundary costs nothing
and the clip opens on a cut instead of halfway through a movement.

Order of work matters for cost
------------------------------
Windows that overlap something already published for this campaign are dropped
*before* scoring. The loudness probe spawns an FFmpeg process per window, so
scoring first and filtering second would spend real time on windows that can
never ship.

What the score is and is not
----------------------------
It is a proxy for "something is happening here": scene density plus audio RMS.
It is not a compliance check and not a virality prediction. Nothing in the
campaign requirements can be satisfied or violated by this ranking, which is
why it is allowed to be a crude heuristic.
"""

from typing import Dict, List, Optional

from .config import config
from .spec import CampaignSpec
from .utils import scene_times, setup_logger, window_loudness

logger = setup_logger(__name__)


def _target_duration(spec: CampaignSpec) -> float:
    """Clip length to aim for, clamped into the campaign's legal band.

    Deliberately not the campaign minimum. A clip rendered at exactly the
    minimum has no margin: a keyframe-aligned seek or a re-encode rounding down
    by a few frames turns a compliant 10.0s clip into a rejected 9.97s one.
    """
    low = spec.render.min_duration + 1.5
    high = spec.render.max_duration
    return max(low, min(config.target_duration, high))


def _nearest_boundary(value: float, boundaries: List[float],
                      window: float = 2.5) -> float:
    """Snap to the closest scene boundary within ``window`` seconds."""
    if not boundaries:
        return value
    best = min(boundaries, key=lambda b: abs(b - value))
    return best if abs(best - value) <= window else value


def candidate_windows(source: Dict, spec: CampaignSpec, db,
                      limit: Optional[int] = None,
                      use_scenes: bool = True) -> List[Dict]:
    """Ranked, non-overlapping, never-before-published windows for one source.

    Returns ``[{start, duration, end, score, scenes, loudness}]`` best first.
    """
    duration = float(source.get('duration') or 0.0)
    fingerprint = source.get('fingerprint') or ''
    target = _target_duration(spec)
    limit = limit or config.clips_per_source

    usable_start = config.head_trim
    usable_end = max(0.0, duration - config.tail_trim)
    if usable_end - usable_start < spec.render.min_duration:
        logger.info('SOURCE_TOO_SHORT file=%s duration=%.1f min=%.1f',
                    source.get('filename'), duration,
                    spec.render.min_duration)
        return []

    boundaries = []
    if use_scenes:
        boundaries = [t for t in scene_times(source['local_path'],
                                            config.scene_threshold)
                      if usable_start <= t <= usable_end - target]
        logger.info('SCENES file=%s count=%d', source.get('filename'),
                    len(boundaries))

    # Stride by half a window so adjacent candidates can be considered without
    # producing near-duplicate clips; the overlap filter below prunes the rest.
    stride = max(2.0, target / 2.0)
    raw: List[Dict] = []
    cursor = usable_start
    while cursor + target <= usable_end:
        start = _nearest_boundary(cursor, boundaries)
        start = max(usable_start, min(start, usable_end - target))
        end = start + target
        if not db.window_overlaps(spec.id, fingerprint, start, end):
            raw.append({'start': round(start, 3),
                        'duration': round(target, 3),
                        'end': round(end, 3)})
        cursor += stride

    # Deduplicate windows that snapped onto the same boundary.
    seen, unique = set(), []
    for window in raw:
        key = round(window['start'], 1)
        if key not in seen:
            seen.add(key)
            unique.append(window)

    if not unique:
        logger.info('NO_FRESH_WINDOWS file=%s (all already published)',
                    source.get('filename'))
        return []

    for window in unique:
        window['scenes'] = sum(1 for b in boundaries
                              if window['start'] <= b <= window['end'])
        if config.score_audio and source.get('has_audio'):
            window['loudness'] = window_loudness(
                source['local_path'], window['start'], window['duration'])
        else:
            window['loudness'] = -99.0
        window['score'] = _score(window)

    unique.sort(key=lambda w: w['score'], reverse=True)
    picked = _spread(unique, limit, target)
    for window in picked:
        logger.info('WINDOW file=%s start=%.2f dur=%.2f score=%.3f '
                    'scenes=%d rms=%.1f', source.get('filename'),
                    window['start'], window['duration'], window['score'],
                    window['scenes'], window['loudness'])
    return picked


def _score(window: Dict) -> float:
    """Scene density plus normalised loudness.

    Both terms are capped. An uncapped scene term makes a rapid-cut montage beat
    every real moment in the folder, and an uncapped audio term makes the
    loudest few seconds win regardless of what is on screen.
    """
    per_second = window['scenes'] / max(1.0, window['duration'])
    motion = min(1.0, per_second / 0.6)
    loud = window['loudness']
    audio = 0.0 if loud <= -60 else min(1.0, (loud + 60.0) / 45.0)
    return round(0.6 * motion + 0.4 * audio, 4)


def _spread(windows: List[Dict], limit: int, target: float) -> List[Dict]:
    """Take the best ``limit`` windows that do not overlap each other.

    Two high-scoring windows a second apart are the same clip twice, which is
    the reuse problem the database guards across runs, applied within one run.
    """
    picked: List[Dict] = []
    for window in windows:
        if len(picked) >= limit:
            break
        clash = any(min(window['end'], other['end'])
                    - max(window['start'], other['start']) > target * 0.25
                    for other in picked)
        if not clash:
            picked.append(window)
    return picked


def plan_clips(sources: List[Dict], spec: CampaignSpec, db,
               wanted: Optional[int] = None) -> List[Dict]:
    """Build a run's worth of clip plans across the whole source pool.

    Round-robins across source files rather than draining the best file first.
    Three clips from one video posted back to back look like a repost of each
    other even when the windows are disjoint.
    """
    wanted = wanted or config.clips_per_run
    per_source: List[List[Dict]] = []
    for source in sources:
        windows = candidate_windows(source, spec, db)
        per_source.append([{**window,
                            'fingerprint': source['fingerprint'],
                            'source_name': source['filename'],
                            'source_path': source['local_path'],
                            'has_audio': source.get('has_audio', False),
                            'source_width': source.get('width', 0),
                            'source_height': source.get('height', 0)}
                           for window in windows])

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
