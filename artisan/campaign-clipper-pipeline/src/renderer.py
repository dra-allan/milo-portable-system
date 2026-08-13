"""Rendering one compliant vertical clip with FFmpeg.

Stage order, and why it is this order
-------------------------------------
``trim -> 9:16 fill -> text sheets -> logo -> encode``

The logo goes on *after* the text so a wide text line can never sit on top of
the campaign's branding. Several campaigns require the logo to be visible;
none of them require the text to be able to cover it.

Encoder probing is load-bearing
-------------------------------
The encoder is confirmed with a one-frame null encode, not by reading
``-encoders``. This was learned the expensive way in the Shorts lane: a machine
can list ``h264_nvenc`` and ``h264_qsv`` while neither actually runs (no GPU or
no driver), and trusting the list fails *every* clip in a run with "Unknown
encoder". The probe result is cached at module level because renders run in
parallel and the answer cannot change mid-process.

Quality settings are inherited, not re-chosen
---------------------------------------------
CRF 18 / preset medium comes from a measured finding in the Shorts lane:
``veryfast`` reintroduced blocking artefacts in flat regions beside hard caption
edges, and the platform re-encode preserves those blocks rather than hiding
them. Speed here comes from parallel renders and hardware offload, not from a
degraded picture.
"""

import shutil
from pathlib import Path
from typing import Dict, List, Optional

from . import overlay as ov
from .config import config
from .spec import CampaignSpec
from .utils import (ensure_dir, probe_media, run_ffmpeg, safe_slug,
                   setup_logger, which_ffmpeg)

logger = setup_logger(__name__)

_ENCODER_CACHE: Dict[str, Optional[str]] = {}

_HW_ARGS = {
    'h264_nvenc': ['-rc', 'vbr', '-cq', None, '-preset', 'p5'],
    'h264_qsv': ['-global_quality', None, '-preset', 'medium'],
    'h264_amf': ['-rc', 'cqp', '-qp_i', None, '-qp_p', None],
    'h264_videotoolbox': ['-q:v', None],
}


def _probe_encoder(name: str) -> bool:
    """Can this encoder actually encode one frame right now?"""
    import subprocess
    cmd = [which_ffmpeg(), '-hide_banner', '-nostdin', '-y',
           '-f', 'lavfi', '-i', 'color=c=black:s=128x128:d=0.04',
           '-frames:v', '1', '-c:v', name, '-f', 'null', '-']
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=60)
        return proc.returncode == 0
    except Exception:
        return False


def resolve_encoder() -> str:
    """The encoder this machine will actually use."""
    requested = (config.encoder or 'auto').lower()
    if requested in _ENCODER_CACHE:
        return _ENCODER_CACHE[requested] or 'libx264'
    chosen = 'libx264'
    if requested in ('off', 'none', 'libx264', 'cpu'):
        chosen = 'libx264'
    elif requested == 'auto':
        for candidate in ('h264_nvenc', 'h264_qsv', 'h264_videotoolbox',
                          'h264_amf'):
            if _probe_encoder(candidate):
                chosen = candidate
                break
    else:
        candidate = requested if requested.startswith('h264_') \
            else f'h264_{requested}'
        if _probe_encoder(candidate):
            chosen = candidate
        else:
            logger.warning('ENCODER_UNUSABLE requested=%s falling back to '
                           'libx264', requested)
    _ENCODER_CACHE[requested] = chosen
    logger.info('ENCODER_RESOLVED requested=%s using=%s', requested, chosen)
    return chosen


def _encode_args() -> List[str]:
    """Constant-quality flags for the resolved encoder.

    The hardware encoders do not share libx264's ``-crf``/``-preset``
    vocabulary, so each gets its own mapping. VIDEO_CRF therefore keeps meaning
    roughly the same thing regardless of which encoder was picked.
    """
    encoder = resolve_encoder()
    if encoder == 'libx264':
        return ['-c:v', 'libx264', '-preset', config.preset,
                '-crf', str(config.crf)]
    args = ['-c:v', encoder]
    for item in _HW_ARGS.get(encoder, ['-q:v', None]):
        args.append(str(config.crf) if item is None else item)
    return args


def _audio_args(spec: CampaignSpec, has_audio: bool) -> List[str]:
    """Audio handling.

    Source audio is kept whenever it exists. These are clipping campaigns; the
    speech *is* the content, and muting it would make the clip meaningless. When
    a campaign wants trending audio it is added inside the platform composer at
    publish time (a manual step recorded on the spec), so nothing is mixed here.

    A silent-but-present audio track is generated when the source has none:
    several platforms treat a video with no audio stream as broken.
    """
    if not has_audio:
        return ['-shortest', '-c:a', 'aac', '-b:a', '128k']
    if not spec.render.keep_source_audio:
        return ['-an']
    return ['-c:a', 'aac', '-b:a', config.audio_bitrate, '-ar', '48000',
            '-ac', '2']


def render_clip(spec: CampaignSpec, plan: Dict, copy: Dict,
                logo_path: Optional[Path] = None,
                stamp_logo: bool = True) -> Optional[Dict]:
    """Render one clip. Returns a render report, or None on failure.

    The report carries the artefacts the validator needs (sheet paths, whether
    the logo stage ran) so validation can check what actually happened rather
    than re-deriving intent from the spec.
    """
    source = Path(plan['source_path'])
    if not source.exists():
        logger.error('RENDER_SKIP missing_source=%s', source)
        return None

    work = ensure_dir(config.campaign_temp_dir(spec.id)
                      / f"{safe_slug(plan['source_name'])}_"
                        f"{int(plan['start'] * 1000)}")
    out_dir = config.campaign_output_dir(spec.id)
    out_path = out_dir / (f"{spec.id}_{safe_slug(plan['source_name'], 28)}_"
                          f"{int(plan['start'])}s.mp4")

    sheets: List[Path] = []
    sheet_reports: List[Dict] = []
    if spec.render.own_text_required or copy.get('overlay_text'):
        sheet = ov.text_sheet(copy.get('overlay_text', ''),
                              work / 'text.png',
                              highlight=copy.get('highlight', ''))
        if sheet:
            ink = ov.sheet_ink(sheet)
            sheet_reports.append({'path': str(sheet), 'ink': ink})
            if ink <= 0:
                # An empty sheet is the silent failure this whole design exists
                # to prevent. Refuse rather than ship a text-less clip to a
                # campaign that requires text.
                logger.error('TEXT_SHEET_EMPTY campaign=%s text=%r', spec.id,
                             copy.get('overlay_text'))
                return None
            sheets.append(sheet)

    chains: List[str] = []
    chains += ov.fill_chain('0:v', 'filled')
    label = 'filled'
    if sheets:
        chains += ov.sheet_chain(label, 'texted', sheets)
        label = 'texted'
    logo_used = False
    if logo_path and stamp_logo:
        chains += ov.logo_chain(label, 'branded', logo_path,
                               position=spec.assets.logo_position,
                               scale=spec.assets.logo_scale,
                               margin=spec.assets.logo_margin,
                               opacity=spec.assets.logo_opacity)
        label = 'branded'
        logo_used = True
    chains.append(f'[{label}]fps={config.fps},format=yuv420p[vout]')

    duration = float(plan['duration'])
    args = [
        # -ss before -i so decoding starts at the window instead of frame zero;
        # on a 40-minute source the difference is minutes per clip.
        '-ss', f"{float(plan['start']):.3f}",
        '-t', f'{duration:.3f}',
        '-i', str(source),
        '-filter_complex', ';'.join(chains),
        '-map', '[vout]',
    ]
    if plan.get('has_audio'):
        args += ['-map', '0:a:0?']
    args += _encode_args()
    args += _audio_args(spec, bool(plan.get('has_audio')))
    args += [
        '-color_primaries', 'bt709', '-color_trc', 'bt709',
        '-colorspace', 'bt709',
        '-movflags', '+faststart',
        # Pin the output duration. Container-level rounding on a clip rendered
        # near the campaign minimum is the difference between compliant and
        # rejected.
        '-t', f'{duration:.3f}',
        str(out_path),
    ]

    if config.dry_run:
        logger.info('DRY_RUN would render %s', out_path.name)
        return {'path': str(out_path), 'dry_run': True}

    if not run_ffmpeg(args, timeout=config.render_timeout):
        logger.error('RENDER_FAILED campaign=%s source=%s start=%.2f',
                     spec.id, plan['source_name'], plan['start'])
        return None

    media = probe_media(str(out_path))
    logger.info('RENDER_OK file=%s %dx%d %.2fs audio=%s logo=%s',
                out_path.name, media['width'], media['height'],
                media['duration'], media['has_audio'], logo_used)
    return {'path': str(out_path), 'work_dir': str(work),
            'sheets': sheet_reports, 'logo_stamped': logo_used,
            'logo_path': str(logo_path) if logo_path else '',
            'encoder': resolve_encoder(), 'media': media}


def cleanup_work(report: Dict) -> None:
    """Drop the per-clip temp directory once the clip is validated.

    Only the temp dir. The output MP4 has to survive until the link is actually
    submitted to the campaign board, which is a separate step that can fail.
    """
    work = report.get('work_dir')
    if work and Path(work).exists():
        shutil.rmtree(work, ignore_errors=True)
