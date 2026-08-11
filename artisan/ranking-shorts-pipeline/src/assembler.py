"""FFmpeg assembly: ranked clips in, finished vertical video out.

Two stages, on purpose.

**Stage 1** renders each ranked clip independently to a normalised 1080x1920
MP4 with its overlays, its voice-over and its sound effects already baked in.
**Stage 2** chains those together with transitions and lays the swooshes over
the seams.

Doing it in one graph is possible and it is a trap: a 5-clip build would be a
~40-node filtergraph with 12 inputs, any one of which failing loses all of the
work, and it makes the offsets impossible to reason about. Staging also means
stage 1 output can be inspected and re-used when only the stitch changes.

The duration arithmetic in :func:`fit_durations` is the part to be careful
with. xfade *overlaps* its two inputs, so an n-clip build is
``sum(durations) - (n-1) * transition_duration`` long, not ``sum(durations)``.
Budgeting on the sum leaves videos short of the target; ignoring the overlap in
the other direction pushes them past the 60s Shorts limit.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .config import config
from .overlays import fill_chain, hook_zoom_chain, mask_chain, text_chain
from .utils import (ensure_dir, probe_media, run_ffmpeg, safe_slug,
                    setup_logger, which_ffmpeg)

logger = setup_logger(__name__)


# ---------------------------------------------------------------------------
# Encoder resolution
# ---------------------------------------------------------------------------
class _Encoder:
    """Resolve a working video encoder once per process.

    ``-encoders`` is not evidence: a machine can advertise h264_nvenc with no
    usable driver behind it, in which case every render dies with "Unknown
    encoder" after the work is already done. A one-frame null encode is the
    only reliable probe. Cached at class level because renders are sequential
    but repeated, and the answer cannot change mid-run.
    """

    _resolved: Optional[str] = None

    CANDIDATES = {
        'nvenc': 'h264_nvenc',
        'qsv': 'h264_qsv',
        'amf': 'h264_amf',
        'videotoolbox': 'h264_videotoolbox',
    }

    @classmethod
    def resolve(cls) -> str:
        if cls._resolved:
            return cls._resolved
        want = (config.encoder or 'auto').lower()
        if want in ('off', 'none', 'libx264', 'cpu'):
            cls._resolved = 'libx264'
            return cls._resolved
        order = ([cls.CANDIDATES[want]] if want in cls.CANDIDATES
                 else list(cls.CANDIDATES.values()))
        for name in order:
            if cls._probe(name):
                logger.info('using hardware encoder %s', name)
                cls._resolved = name
                return name
        if want in cls.CANDIDATES:
            logger.warning('%s requested but not usable; falling back to '
                           'libx264', want)
        cls._resolved = 'libx264'
        return cls._resolved

    @staticmethod
    def _probe(name: str) -> bool:
        cmd = [which_ffmpeg(), '-hide_banner', '-loglevel', 'error', '-y',
               '-f', 'lavfi', '-i', 'color=c=black:s=320x240:r=1',
               '-frames:v', '1', '-c:v', name, '-f', 'null', '-']
        try:
            proc = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=60)
        except (subprocess.TimeoutExpired, OSError):
            return False
        return proc.returncode == 0


def video_encode_args() -> List[str]:
    """Encoder flags that mean the same visual quality on every backend.

    The hardware encoders do not speak ``-crf``/``-preset``, so VIDEO_CRF is
    translated per encoder rather than passed through. Without this, setting
    VIDEO_ENCODER=nvenc silently changes the output quality.
    """
    enc = _Encoder.resolve()
    crf = int(config.crf)
    if enc == 'libx264':
        return ['-c:v', 'libx264', '-crf', str(crf),
                '-preset', config.preset, '-profile:v', 'high',
                '-level', '4.2']
    if enc == 'h264_nvenc':
        return ['-c:v', 'h264_nvenc', '-rc', 'vbr', '-cq', str(crf),
                '-preset', 'p5', '-b:v', '0']
    if enc == 'h264_qsv':
        return ['-c:v', 'h264_qsv', '-global_quality', str(crf),
                '-look_ahead', '1']
    if enc == 'h264_amf':
        return ['-c:v', 'h264_amf', '-rc', 'cqp', '-qp_i', str(crf),
                '-qp_p', str(crf)]
    if enc == 'h264_videotoolbox':
        # videotoolbox has no CQ mode worth using; map CRF onto a bitrate.
        bitrate = max(4000, 12000 - (crf - 18) * 800)
        return ['-c:v', 'h264_videotoolbox', '-b:v', f'{bitrate}k']
    return ['-c:v', 'libx264', '-crf', str(crf), '-preset', config.preset]


def _audio_args() -> List[str]:
    return ['-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2']


# ---------------------------------------------------------------------------
# Duration fitting
# ---------------------------------------------------------------------------
def visible_total(durations: Sequence[float], transition: float) -> float:
    """Final runtime of clips joined by xfade, which overlaps each seam."""
    if not durations:
        return 0.0
    return max(0.0, sum(durations) - transition * (len(durations) - 1))


def fit_durations(durations: List[float]) -> List[float]:
    """Trim clip lengths so the finished video lands inside the Shorts window.

    Longer clips are shortened first: a 9-second clip has slack, a 3-second one
    is already at the floor and cutting it further removes the payoff.
    """
    transition = float(config.get('transition_duration', 0.28))
    hard_max = float(config.get('hard_max_total_seconds', 59))
    floor = float(config.get('min_clip_seconds', 2.5))
    out = list(durations)

    guard = 0
    while visible_total(out, transition) > hard_max and guard < 200:
        guard += 1
        longest = max(range(len(out)), key=lambda i: out[i])
        if out[longest] <= floor:
            break  # everything is at the floor; nothing left to give
        out[longest] = max(floor, out[longest] - 0.25)
    if visible_total(out, transition) > hard_max:
        logger.warning(
            'clips total %.1fs, over the %.0fs cap even at the %.1fs floor; '
            'drop a clip or lower min_clip_seconds',
            visible_total(out, transition), hard_max, floor)
    return out


# ---------------------------------------------------------------------------
# Stage 1: one ranked clip
# ---------------------------------------------------------------------------
def render_clip(clip: Dict, video_title: str, clips_total: int,
                out_path: Path,
                leaderboard: Optional[List[Dict]] = None) -> Optional[Path]:
    """Render one ranked clip: fill, text-mask, overlays, hook zoom, audio.

    ``clip`` keys:
        path        - local source file
        start       - seconds into the source
        duration    - seconds to take
        rank         - 1..N (drives the stroke colour)
        title       - clip title, e.g. "MAN OVERBOARD"
        vo_path     - optional voice-over audio
        sfx         - optional [{path, at, gain}]
        text_boxes  - optional [{x, y, w, h}] regions to blur
        hook        - True for the opening clip
    ``leaderboard`` - [{rank, title}] for every clip in the build, so each
        clip's render draws the same list with itself highlighted.
    """
    source = Path(clip['path'])
    if not source.exists():
        logger.error('clip source missing: %s', source)
        return None

    media = probe_media(str(source))
    duration = float(clip.get('duration') or 0.0)
    if duration <= 0:
        logger.error('clip %s has no duration', source.name)
        return None

    inputs: List[str] = []
    # Fast-seek before -i, then an exact -t. Placing -ss after -i decodes the
    # whole head of the file for nothing on a long source.
    start = float(clip.get('start') or 0.0)
    if start > 0:
        inputs += ['-ss', f'{start:.3f}']
    inputs += ['-t', f'{duration:.3f}', '-i', str(source)]

    next_index = 1
    silent_index = None
    if not media['has_audio']:
        # A scraped clip with no audio stream would fail the [0:a] map, so
        # substitute silence rather than branching the whole audio graph.
        inputs += ['-f', 'lavfi', '-t', f'{duration:.3f}',
                   '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000']
        silent_index = next_index
        next_index += 1

    vo_index = None
    vo_path = clip.get('vo_path')
    if vo_path and Path(vo_path).exists():
        inputs += ['-i', str(vo_path)]
        vo_index = next_index
        next_index += 1

    sfx_inputs: List[Dict] = []
    for cue in (clip.get('sfx') or []):
        cue_path = cue.get('path')
        if not cue_path or not Path(cue_path).exists():
            continue
        inputs += ['-i', str(cue_path)]
        sfx_inputs.append({'index': next_index,
                           'at': float(cue.get('at') or 0.0),
                           'gain': float(cue.get('gain')
                                         or config.get('sfx_gain', 0.9))})
        next_index += 1

    # -- video graph ---------------------------------------------------
    chains: List[str] = []
    chains += fill_chain('0:v', 'filled')
    chains += mask_chain('filled', 'masked', clip.get('text_boxes') or [])
    chains += text_chain('masked', 'texted', int(clip['rank']),
                         clip.get('title') or '', video_title, clips_total,
                         leaderboard=leaderboard)
    if clip.get('hook'):
        chains += hook_zoom_chain('texted', 'zoomed')
        last_v = 'zoomed'
    else:
        last_v = 'texted'
    chains.append(f'[{last_v}]fps={config.fps},format=yuv420p,setsar=1[vout]')

    # -- audio graph ---------------------------------------------------
    src_label = f'{silent_index}:a' if silent_index is not None else '0:a'
    chains.append(
        f'[{src_label}]aformat=sample_fmts=fltp:sample_rates=48000:'
        'channel_layouts=stereo,loudnorm=I=-16:TP=-1.5:LRA=11[src]')

    mix_labels: List[str] = []
    if vo_index is not None:
        offset_ms = int(float(config.get('vo_offset', 0.4)) * 1000)
        gain = float(config.get('vo_gain', 1.6))
        # asplit: one copy is heard, the other drives the compressor. Using a
        # single copy for both is not possible, and keying the compressor off
        # the music/source itself (the classic mistake) ducks nothing.
        chains.append(
            f'[{vo_index}:a]aformat=sample_fmts=fltp:sample_rates=48000:'
            f'channel_layouts=stereo,volume={gain},'
            f'adelay={offset_ms}|{offset_ms},asplit=2[vo][vokey]')
        threshold = float(config.get('duck_threshold', 0.05))
        ratio = float(config.get('duck_ratio', 8))
        chains.append(
            f'[src][vokey]sidechaincompress=threshold={threshold}:'
            f'ratio={ratio}:attack=15:release=350[srcduck]')
        mix_labels.append('[srcduck]')
        mix_labels.append('[vo]')
    else:
        mix_labels.append('[src]')

    for i, cue in enumerate(sfx_inputs):
        delay_ms = max(0, int(cue['at'] * 1000))
        chains.append(
            f"[{cue['index']}:a]aformat=sample_fmts=fltp:sample_rates=48000:"
            f"channel_layouts=stereo,volume={cue['gain']},"
            f'adelay={delay_ms}|{delay_ms}[sfx{i}]')
        mix_labels.append(f'[sfx{i}]')

    if len(mix_labels) == 1:
        chains.append(f'{mix_labels[0]}alimiter=limit=0.95[aout]')
    else:
        chains.append(
            ''.join(mix_labels) +
            f'amix=inputs={len(mix_labels)}:duration=first:'
            'dropout_transition=0:normalize=0,alimiter=limit=0.95[aout]')

    ensure_dir(out_path.parent)
    args = inputs + [
        '-filter_complex', ';'.join(chains),
        '-map', '[vout]', '-map', '[aout]',
        '-r', str(config.fps),
        '-t', f'{duration:.3f}',
    ] + video_encode_args() + [
        '-pix_fmt', 'yuv420p',
        '-colorspace', 'bt709', '-color_primaries', 'bt709',
        '-color_trc', 'bt709',
    ] + _audio_args() + [str(out_path)]

    if not run_ffmpeg(args):
        logger.error('stage-1 render failed for rank %s (%s)',
                     clip.get('rank'), source.name)
        return None
    logger.info('rendered #%s %s -> %s', clip.get('rank'),
                clip.get('title'), out_path.name)
    return out_path


# ---------------------------------------------------------------------------
# Stage 2: stitch
# ---------------------------------------------------------------------------
def stitch(stage_paths: List[Path], out_path: Path,
           swoosh: Optional[Path] = None,
           music: Optional[Path] = None) -> Optional[Path]:
    """Join stage-1 clips with transitions, swooshes and optional music.

    Video uses xfade, audio uses acrossfade over the same duration. They must
    match: xfade shortens the video by the overlap, and an audio track that
    was simply concatenated would drift a further ``transition_duration``
    behind the picture at every seam, so by clip 5 the voice-over would land on
    the wrong clip entirely.
    """
    stage_paths = [p for p in stage_paths if p and Path(p).exists()]
    if not stage_paths:
        logger.error('nothing to stitch')
        return None
    if len(stage_paths) == 1:
        ensure_dir(out_path.parent)
        shutil.copy2(stage_paths[0], out_path)
        return out_path

    transition = float(config.get('transition_duration', 0.28))
    name = str(config.get('transition', 'zoomin'))
    durations = [probe_media(str(p))['duration'] for p in stage_paths]
    if min(durations) <= transition * 2:
        transition = max(0.08, min(durations) / 3.0)
        logger.warning('shortest clip is %.2fs; transition reduced to %.2fs',
                       min(durations), transition)

    inputs: List[str] = []
    for path in stage_paths:
        inputs += ['-i', str(path)]
    next_index = len(stage_paths)

    swoosh_index = None
    if swoosh and Path(swoosh).exists():
        inputs += ['-i', str(swoosh)]
        swoosh_index = next_index
        next_index += 1

    music_index = None
    if music and Path(music).exists() and config.get('music_enabled', False):
        # -stream_loop is cheaper than aloop and cannot overflow a frame count
        # on a bed shorter than the video.
        inputs += ['-stream_loop', '-1', '-i', str(music)]
        music_index = next_index
        next_index += 1

    chains: List[str] = []
    for i in range(len(stage_paths)):
        # settb=AVTB: xfade needs a common timebase or it reports
        # "First input link ... timebase mismatch" and refuses the join.
        chains.append(f'[{i}:v]settb=AVTB,fps={config.fps},'
                      f'format=yuv420p,setsar=1[v{i}]')
        chains.append(f'[{i}:a]aformat=sample_fmts=fltp:sample_rates=48000:'
                      f'channel_layouts=stereo[a{i}]')

    prev_v, prev_a = '[v0]', '[a0]'
    acc = durations[0]
    offsets: List[float] = []
    for i in range(1, len(stage_paths)):
        offset = max(0.0, acc - transition)
        offsets.append(offset)
        out_v, out_a = f'[vx{i}]', f'[ax{i}]'
        chains.append(f'{prev_v}[v{i}]xfade=transition={name}:'
                      f'duration={transition}:offset={offset:.3f}{out_v}')
        chains.append(f'{prev_a}[a{i}]acrossfade=d={transition}:'
                      f'c1=tri:c2=tri{out_a}')
        prev_v, prev_a = out_v, out_a
        acc = acc - transition + durations[i]

    audio_mix = [prev_a]
    if swoosh_index is not None and offsets:
        gain = float(config.get('swoosh_gain', 0.5))
        labels = ''.join(f'[sw{i}]' for i in range(len(offsets)))
        chains.append(
            f'[{swoosh_index}:a]aformat=sample_fmts=fltp:sample_rates=48000:'
            f'channel_layouts=stereo,volume={gain},'
            f'asplit={len(offsets)}{labels}')
        for i, offset in enumerate(offsets):
            delay_ms = max(0, int(offset * 1000))
            chains.append(f'[sw{i}]adelay={delay_ms}|{delay_ms}[swd{i}]')
            audio_mix.append(f'[swd{i}]')

    if music_index is not None:
        volume = float(config.get('music_volume', 0.12))
        chains.append(
            f'[{music_index}:a]aformat=sample_fmts=fltp:sample_rates=48000:'
            f'channel_layouts=stereo,volume={volume}[bed]')
        audio_mix.append('[bed]')

    if len(audio_mix) == 1:
        chains.append(f'{audio_mix[0]}alimiter=limit=0.95[aout]')
    else:
        chains.append(
            ''.join(audio_mix) +
            f'amix=inputs={len(audio_mix)}:duration=first:'
            'dropout_transition=0:normalize=0,alimiter=limit=0.95[aout]')

    ensure_dir(out_path.parent)
    args = inputs + [
        '-filter_complex', ';'.join(chains),
        '-map', prev_v, '-map', '[aout]',
        '-r', str(config.fps),
    ] + video_encode_args() + [
        '-pix_fmt', 'yuv420p', '-colorspace', 'bt709',
        '-color_primaries', 'bt709', '-color_trc', 'bt709',
    ] + _audio_args() + ['-movflags', '+faststart', str(out_path)]

    if not run_ffmpeg(args):
        logger.error('stage-2 stitch failed')
        return None
    logger.info('stitched %d clips -> %s (%.1fs)', len(stage_paths),
                out_path.name, acc)
    return out_path


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def assemble(plan: Dict) -> Optional[Path]:
    """Build a finished video from a plan.

    ``plan`` = {topic, video_title, clips: [...]}, clips ordered as they will
    appear (rank 5 first, rank 1 last).
    """
    clips: List[Dict] = plan.get('clips') or []
    if not clips:
        logger.error('plan has no clips')
        return None

    fitted = fit_durations([float(c.get('duration') or 0.0) for c in clips])
    for clip, duration in zip(clips, fitted):
        clip['duration'] = duration

    title = plan.get('video_title') or 'TOP 5'
    work = ensure_dir(config.temp_dir / safe_slug(f"{plan.get('topic')}_{title}"))

    leaderboard = [
        {'rank': int(clip.get('rank') or 0), 'title': clip.get('title') or ''}
        for clip in clips
    ]

    stages: List[Path] = []
    for index, clip in enumerate(clips):
        clip['hook'] = index == 0
        stage_path = work / f"stage_{index:02d}_rank{clip['rank']}.mp4"
        rendered = render_clip(clip, title, len(clips), stage_path,
                               leaderboard=leaderboard)
        if not rendered:
            # One bad clip must not lose the other four; the countdown is
            # renumbered by the caller if it comes back short.
            logger.warning('dropping rank %s from the build', clip.get('rank'))
            continue
        stages.append(rendered)

    if len(stages) < 2:
        logger.error('only %d clip(s) rendered; not enough for a countdown',
                     len(stages))
        return None

    out_path = config.output_dir / f"{safe_slug(title)}_{len(stages)}.mp4"
    return stitch(stages, out_path,
                  swoosh=config.sfx_path('swoosh'),
                  music=_pick_music())


def _pick_music() -> Optional[Path]:
    if not config.get('music_enabled', False):
        return None
    if not config.music_dir.exists():
        return None
    import random
    tracks = [p for p in config.music_dir.iterdir()
              if p.suffix.lower() in ('.mp3', '.m4a', '.wav', '.ogg')
              and p.stat().st_size > 1024]
    return random.choice(tracks) if tracks else None
