"""Render one campaign clip: full crop, captions, logo, encode."""

import shutil
from pathlib import Path
from typing import Dict, List, Optional

from . import overlay as ov
from . import smart_crop
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
SILENT_INPUT = 'anullsrc=channel_layout=stereo:sample_rate=48000'


def _probe_encoder(name: str) -> bool:
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
    requested = (config.encoder or 'auto').lower()
    if requested in _ENCODER_CACHE:
        return _ENCODER_CACHE[requested] or 'libx264'
    chosen = 'libx264'
    if requested in ('off', 'none', 'libx264', 'cpu'):
        chosen = 'libx264'
    elif requested == 'auto':
        for candidate in ('h264_nvenc', 'h264_qsv', 'h264_videotoolbox', 'h264_amf'):
            if _probe_encoder(candidate):
                chosen = candidate
                break
    else:
        candidate = requested if requested.startswith('h264_') else f'h264_{requested}'
        if _probe_encoder(candidate):
            chosen = candidate
        else:
            logger.warning('ENCODER_UNUSABLE requested=%s falling back to libx264', requested)
    _ENCODER_CACHE[requested] = chosen
    logger.info('ENCODER_RESOLVED requested=%s using=%s', requested, chosen)
    return chosen


def _encode_args() -> List[str]:
    encoder = resolve_encoder()
    if encoder == 'libx264':
        return ['-c:v', 'libx264', '-preset', config.preset, '-crf', str(config.crf)]
    args = ['-c:v', encoder]
    for item in _HW_ARGS.get(encoder, ['-q:v', None]):
        args.append(str(config.crf) if item is None else item)
    return args


def render_clip(spec: CampaignSpec, plan: Dict, copy: Dict,
                logo_path: Optional[Path] = None,
                stamp_logo: bool = True) -> Optional[Dict]:
    """Smart/full crop the source, then composite caption/logo layers."""
    source = Path(plan['source_path'])
    if not source.exists():
        logger.error('RENDER_SKIP missing_source=%s', source)
        return None

    work = ensure_dir(config.campaign_temp_dir(spec.id) /
                      f"{safe_slug(plan['source_name'])}_{int(plan['start'] * 1000)}")
    out_path = config.campaign_output_dir(spec.id) / (
        f"{spec.id}_{safe_slug(plan['source_name'], 28)}_{int(plan['start'])}s.mp4")

    sheets: List[Path] = []
    sheet_reports: List[Dict] = []
    if spec.render.own_text_required or copy.get('overlay_text'):
        sheet = ov.text_sheet(copy.get('overlay_text', ''), work / 'text.png',
                              highlight=copy.get('highlight', ''))
        if sheet:
            ink = ov.sheet_ink(sheet)
            sheet_reports.append({'path': str(sheet), 'ink': ink})
            if ink <= 0:
                logger.error('TEXT_SHEET_EMPTY campaign=%s text=%r', spec.id,
                             copy.get('overlay_text'))
                return None
            sheets.append(sheet)

    crop = smart_crop.plan_crop(
        str(source), float(plan['start']),
        float(plan['start']) + float(plan['duration']),
        target_w=config.width, target_h=config.height)
    if crop is None:
        logger.info('SMART_CROP_FALLBACK file=%s using centre crop', source.name)
    else:
        logger.info('SMART_CROP_APPLIED file=%s crop=%s', source.name, crop)

    chains = ov.crop_chain('0:v', 'framed', crop=crop)
    label = 'framed'
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
    has_audio = bool(plan.get('has_audio'))
    args = ['-ss', f"{float(plan['start']):.3f}", '-t', f'{duration:.3f}',
            '-i', str(source)]
    if not has_audio:
        args += ['-f', 'lavfi', '-t', f'{duration:.3f}', '-i', SILENT_INPUT]
    args += ['-filter_complex', ';'.join(chains), '-map', '[vout]']
    if has_audio:
        args += ['-map', '0:a:0?', '-c:a', 'aac', '-b:a', config.audio_bitrate,
                 '-ar', '48000', '-ac', '2']
    else:
        args += ['-map', '1:a', '-c:a', 'aac', '-b:a', '128k', '-shortest']
    args += ['-color_primaries', 'bt709', '-color_trc', 'bt709',
             '-colorspace', 'bt709', '-movflags', '+faststart']
    args += _encode_args() + ['-t', f'{duration:.3f}', str(out_path)]

    if config.dry_run:
        logger.info('DRY_RUN would render %s', out_path.name)
        return {'path': str(out_path), 'dry_run': True}
    if not run_ffmpeg(args, timeout=config.render_timeout):
        logger.error('RENDER_FAILED campaign=%s source=%s start=%.2f',
                     spec.id, plan['source_name'], plan['start'])
        return None

    media = probe_media(str(out_path))
    logger.info('RENDER_OK file=%s %dx%d %.2fs audio=%s logo=%s encoder=%s',
                out_path.name, media['width'], media['height'], media['duration'],
                media['has_audio'], logo_used, resolve_encoder())
    return {'path': str(out_path), 'work_dir': str(work),
            'sheets': sheet_reports, 'logo_stamped': logo_used,
            'logo_path': str(logo_path) if logo_path else '',
            'smart_crop': crop is not None, 'crop': crop,
            'encoder': resolve_encoder(), 'media': media}


def cleanup_work(report: Dict) -> None:
    work = report.get('work_dir')
    if work and Path(work).exists():
        shutil.rmtree(work, ignore_errors=True)
