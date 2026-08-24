"""Scripted news short builder for NXS (GTA 6 beat).

Original narration (ranking_tts) over official-trailer b-roll, rendered with
this lane's assembler primitives, published through the shorts lane uploader
(channel NXS, niche gta_hype).

Usage (ranking venv):
    python make_gta6_video.py --build
Upload (shorts venv):
    python ..\\ranking-shorts-pipeline\\make_gta6_video.py --upload <mp4>
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SHORTS_ROOT = (ROOT.parent / 'youtube-shorts-pipeline').resolve()

SLUG = 'gta6_deadman_switch'
TARGET_TOTAL = 59.0
VO_LEAD = 0.4
VO_TAIL = 0.35

SEGMENTS = [
    {'id': 'S1',
     'text': "A GTA 6 leaker says he's armed a dead man's switch.",
     'head': 'HACKER ARMS A DEAD MAN SWITCH'},
    {'id': 'S2',
     'text': 'Reports circulating today claim the hacker known as Cyberlink '
             'has tied the full build of GTA 6 to that switch.',
     'head': 'THE FULL BUILD IS HIS INSURANCE'},
    {'id': 'S3',
     'text': 'If law enforcement ever takes him down, the entire game gets '
             'dumped on the internet for free.',
     'head': 'ARREST HIM AND IT DROPS FREE'},
    {'id': 'S4',
     'text': "Here's how a dead man's switch works. It's a failsafe that "
             'requires the user to cancel it on a fixed schedule.',
     'head': 'HOW THE SWITCH WORKS'},
    {'id': 'S5',
     'text': "If he misses a check-in because he's been arrested, detained "
             'or harmed, it fires and releases whatever is programmed into it.',
     'head': 'MISS ONE CHECK-IN AND IT FIRES'},
    {'id': 'S6',
     'text': 'Cyberlink claims he has to cancel it every single day. Miss '
             'even one, and the whole game goes live early.',
     'head': 'CANCELLED DAILY OR IT GOES LIVE'},
    {'id': 'S7',
     'text': 'So arresting him would not stop the leak. It would trigger the '
             'biggest leak in gaming history.',
     'head': 'THE BIGGEST LEAK IN GAMING HISTORY?'},
    {'id': 'S8',
     'text': 'Rockstar and Take-Two still have not commented.',
     'head': 'ROCKSTAR HAS NOT COMMENTED'},
]

SOURCES = [
    {'video_id': 'QdBZY2fkU-0', 'label': 'GTA VI Trailer 1 (Rockstar Games)'},
    {'video_id': 'VQRLujxTm3c', 'label': 'GTA VI Trailer 2 (Rockstar Games)'},
]

ANCHORS = [0.10, 0.30, 0.50, 0.68, 0.82]

OVERRIDES = {
    'S7': {'video_id': 'VQRLujxTm3c', 'anchor': 0.82},
}

TITLE = "Leaker Says GTA 6 Has a Dead Man's Switch #Shorts"
DESCRIPTION = (
    "Reports claim a hacker tied the full GTA 6 build to a dead man's switch "
    'that releases the game for free if he is ever arrested. Nothing is '
    'confirmed - Rockstar and Take-Two have not commented.\n\n'
    'Footage sources:\n'
    + '\n'.join(f"- {s['label']}: https://www.youtube.com/watch?v={s['video_id']}"
                for s in SOURCES)
    + '\n\nNarration and editing are our own. '
      'Music: in-house instrumental bed (royalty-free).'
)
TAGS = ['gta6', 'gtavi', 'gta6news', 'rockstar', 'deadmanswitch',
        'gamingnews', 'shorts']


def _load_ranking_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / 'config' / '.env')
    except ImportError:
        pass


def _find_vo(vo_dir: Path, seg_id: str, fmt: str) -> Path:
    for ext in (fmt, 'mp3', 'wav'):
        candidate = vo_dir / f'{seg_id}.{ext}'
        if candidate.exists() and candidate.stat().st_size > 1024:
            return candidate
    return None


def generate_vo(config, vo_dir: Path):
    lines = [{'id': seg['id'], 'text': seg['text']} for seg in SEGMENTS]
    lines_path = vo_dir / 'lines.json'
    lines_path.write_text(json.dumps({'lines': lines}, indent=2,
                                     ensure_ascii=False), encoding='utf-8')
    missing = [seg['id'] for seg in SEGMENTS
               if not _find_vo(vo_dir, seg['id'], config.tts_format)]
    if not missing:
        print('[tts] all voice-over files already present')
        return
    cmd = [sys.executable, '-m', 'ranking_tts.ranking_tts',
           '--lines', str(lines_path), '--out-dir', str(vo_dir),
           '--voice', config.tts_voice, '--format', config.tts_format]
    print(f'[tts] generating {len(missing)} line(s)')
    proc = subprocess.run(cmd, cwd=str(ROOT), timeout=1800)
    if proc.returncode != 0:
        print('[tts] exited nonzero; checking what landed on disk anyway')


def _source_ok(path, utils) -> str:
    from src.utils import ffprobe_json, probe_media
    media = probe_media(str(path))
    if media['duration'] < 20:
        return f"too short ({media['duration']:.0f}s)"
    if not media['has_audio']:
        return 'no audio stream'
    vdur = 0.0
    for stream in ffprobe_json(str(path)).get('streams', []):
        if stream.get('codec_type') == 'video':
            vdur = float(stream.get('duration') or 0.0)
            break
    if vdur < 0.8 * media['duration']:
        return f'truncated video stream ({vdur:.0f}s of {media["duration"]:.0f}s)'
    return ''


def download_sources(config, sources_dir: Path):
    from _ytdlp import NoWritebackYDL
    out = []
    for src in SOURCES:
        path = None
        for attempt in range(2):
            existing = sorted(p for p in sources_dir.glob(src['video_id'] + '.*')
                              if p.suffix.lower() != '.part')
            if existing:
                path = existing[0]
                reason = _source_ok(path, None)
                if not reason:
                    break
                print(f'[source] discarding cached {path.name}: {reason}')
                path.unlink()
                path = None
            opts = {
                'format': ('bv*[height<=1080][ext=mp4]+ba[ext=m4a]/'
                           'b[height<=1080][ext=mp4]/'
                           'bv*[height<=1080]+ba/b[height<=1080]'),
                'outtmpl': str(sources_dir / f"{src['video_id']}.%(ext)s"),
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'merge_output_format': 'mp4',
                'retries': 15,
                'fragment_retries': 15,
                'socket_timeout': 30,
                'continuedl': True,
            }
            url = f"https://www.youtube.com/watch?v={src['video_id']}"
            print(f'[source] downloading {src["label"]}')
            info = None
            for retry in range(3):
                try:
                    with NoWritebackYDL(opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                    break
                except Exception as exc:
                    print(f'[source] attempt {retry + 1} failed: '
                          f'{str(exc)[:160]}')
                    time.sleep(5)
            if info is None:
                continue
            path = Path(ydl.prepare_filename(info))
            if not path.exists() or path.suffix.lower() == '.part':
                candidates = sorted(p for p in
                                    sources_dir.glob(src['video_id'] + '.*')
                                    if p.suffix.lower() != '.part')
                path = candidates[0] if candidates else path
            reason = _source_ok(path, None)
            if not reason:
                break
            print(f'[source] downloaded file unusable: {reason}')
            path = None
        if path is None:
            print(f'[source] giving up on {src["label"]}')
            continue
        from src.utils import probe_media
        media = probe_media(str(path))
        out.append({**src, 'path': str(path), 'duration': media['duration']})
        print(f'[source] ready {src["label"]} ({media["duration"]:.0f}s)')
    return out


def assign_windows(clips_len, vo_durs, downloaded, seg_ids):
    by_id = {d['video_id']: d for d in downloaded}
    windows = []
    anchor_at = {d['video_id']: 0 for d in downloaded}
    for i in range(clips_len):
        seg_id = seg_ids[i]
        override = OVERRIDES.get(seg_id) or {}
        source_id = override.get('video_id')
        if source_id and source_id in by_id and len(downloaded) > 1:
            d = by_id[source_id]
            anchor = float(override.get('anchor', ANCHORS[0]))
        else:
            d = downloaded[i % len(downloaded)]
            idx = anchor_at[d['video_id']]
            anchor_at[d['video_id']] = idx + 1
            anchor = ANCHORS[idx % len(ANCHORS)]
        dur = VO_LEAD + vo_durs[i] + VO_TAIL
        start = d['duration'] * anchor
        latest = max(0.5, d['duration'] - dur - 1.0)
        start = min(max(0.5, start), latest)
        windows.append({'source': d, 'start': round(start, 3),
                        'duration': round(dur, 3)})
    return windows


def caption_sheet(config, overlays, seg, work: Path) -> str:
    from PIL import Image
    font = config.resolve_font()
    emoji_font = overlays._emoji_font_path()
    w, h = config.width, config.height
    words = seg['head'].split()
    if len(words) > 3:
        mid = (len(words) + 1) // 2
        rows = [' '.join(words[:mid]), ' '.join(words[mid:])]
    else:
        rows = [' '.join(words)]
    sizes = []
    for row in rows:
        size = overlays._fit_size(row, int(w * 0.9), 92, font)
        sizes.append(size)
    size = min(sizes)
    fill = str(config.get('rank_fill', 'white'))
    stroke_ratio = float(config.get('stroke_ratio', 0.07))
    shadow = (int(config.get('shadow_x', 6)), int(config.get('shadow_y', 6)))
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    gap = int(size * 1.22)
    y = h // 2 + int(h * 0.14)
    if len(rows) > 1:
        y -= gap // 2
    for row in rows:
        runs = overlays._group_emoji(row)
        width = overlays._line_width(runs, font, emoji_font, size)
        overlays._draw_line(img, (w - width) / 2, y, runs, font, emoji_font,
                            size, fill, stroke_ratio, shadow)
        y += gap
    sheet = work / f"cap_{seg['id']}.png"
    img.save(sheet)
    return str(sheet)


def render_segment(config, assembler, overlays, utils, index, clip,
                   out_path: Path) -> Path:
    duration = clip['duration']
    inputs = ['-ss', f"{clip['start']:.3f}", '-t', f'{duration:.3f}',
              '-i', clip['path'],
              '-f', 'lavfi', '-t', f'{duration:.3f}',
              '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000',
              '-i', clip['vo_path']]
    chains = []
    chains += overlays.fill_chain('0:v', 'filled')
    chains.append(f"movie={overlays._quote(clip['sheet'])}[cap]")
    chains.append('[filled][cap]overlay=0:0:format=auto[texted]')
    if index == 0 and config.get('hook_zoom', True):
        chains += overlays.hook_zoom_chain('texted', 'zoomed')
        last_v = 'zoomed'
    else:
        last_v = 'texted'
    chains.append(f'[{last_v}]fps={config.fps},format=yuv420p,setsar=1[vout]')
    offset_ms = int(float(config.get('vo_offset', VO_LEAD)) * 1000)
    gain = float(config.get('vo_gain', 1.6))
    chains.append(f'[2:a]aformat=sample_fmts=fltp:sample_rates=48000:'
                  f'channel_layouts=stereo,volume={gain},'
                  f'adelay={offset_ms}|{offset_ms}[vo]')
    chains.append('[1:a][vo]amix=inputs=2:duration=first:'
                  'dropout_transition=0:normalize=0,alimiter=limit=0.95[aout]')
    args = inputs + [
        '-filter_complex', ';'.join(chains),
        '-map', '[vout]', '-map', '[aout]',
        '-r', str(config.fps), '-t', f'{duration:.3f}',
    ] + assembler.video_encode_args() + [
        '-pix_fmt', 'yuv420p', '-colorspace', 'bt709',
        '-color_primaries', 'bt709', '-color_trc', 'bt709',
    ] + assembler._audio_args() + [str(out_path)]
    if not utils.run_ffmpeg(args):
        raise RuntimeError(f"stage render failed for {clip['seg']['id']}")
    print(f"[render] {clip['seg']['id']} -> {out_path.name}")
    return out_path


def build() -> int:
    _load_ranking_env()
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    from src.config import config
    from src import assembler, overlays, utils
    from src.utils import ensure_dir, probe_duration, probe_media

    vo_dir = ensure_dir(config.vo_dir / SLUG)
    work = ensure_dir(config.temp_dir / SLUG)
    sources_dir = ensure_dir(config.data_dir / 'broll_cache')
    stage_dir = ensure_dir(work / 'stage')

    generate_vo(config, vo_dir)
    vo_paths, vo_durs = [], []
    for seg in SEGMENTS:
        vo = _find_vo(vo_dir, seg['id'], config.tts_format)
        if not vo:
            print(f'[abort] missing voice-over for {seg["id"]}')
            return 2
        vo_paths.append(vo)
        vo_durs.append(probe_duration(str(vo)))
    print(f'[tts] narration total {sum(vo_durs):.1f}s')

    downloaded = download_sources(config, sources_dir)
    if not downloaded:
        print('[abort] no usable footage')
        return 2

    windows = assign_windows(len(SEGMENTS), vo_durs, downloaded,
                             [seg['id'] for seg in SEGMENTS])
    transition = float(config.get('transition_duration', 0.28))
    projected = sum(w['duration'] for w in windows) - transition * (len(windows) - 1)
    print(f'[plan] projected total {projected:.1f}s across {len(SEGMENTS)} segment(s)')
    if projected > TARGET_TOTAL + 2:
        print(f'[warn] projected total exceeds {TARGET_TOTAL}s target')

    clips = []
    for i, seg in enumerate(SEGMENTS):
        clips.append({
            'seg': seg,
            'vo_path': str(vo_paths[i]),
            'path': windows[i]['source']['path'],
            'start': windows[i]['start'],
            'duration': windows[i]['duration'],
            'source_label': windows[i]['source']['label'],
            'sheet': caption_sheet(config, overlays, seg, work),
        })

    stage_paths = [None] * len(clips)
    workers = max(1, int(config.render_workers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(render_segment, config, assembler, overlays,
                        utils, i, clip,
                        stage_dir / f'stage_{i:02d}_{clip["seg"]["id"]}.mp4'): i
            for i, clip in enumerate(clips)
        }
        for future in as_completed(futures):
            i = futures[future]
            stage_paths[i] = future.result()

    failed = [SEGMENTS[i]['id'] for i, p in enumerate(stage_paths) if not p]
    if failed:
        print(f'[abort] stage render failed: {failed}')
        return 2
    if len(stage_paths) < 2:
        print('[abort] fewer than 2 segments rendered')
        return 2

    stamp = time.strftime('%Y%m%d_%H%M%S')
    out_path = config.output_dir / f'{SLUG}_{stamp}.mp4'
    bed = None
    if config.music_dir.exists():
        tracks = sorted(p for p in config.music_dir.iterdir()
                        if p.suffix.lower() in ('.mp3', '.wav', '.m4a', '.ogg')
                        and p.stat().st_size > 1024)
        bed = tracks[0] if tracks else None
    if bed:
        config.defaults['music_enabled'] = True
        config.defaults['music_volume'] = float(os.getenv('BED_VOLUME', '0.22'))
        print(f'[bed] {bed.name}')
    result = assembler.stitch([Path(p) for p in stage_paths], out_path,
                              swoosh=config.sfx_path('swoosh'), music=bed)
    if not result:
        print('[abort] stitch failed')
        return 2

    meta = {
        'slug': SLUG,
        'built_at': stamp,
        'title': TITLE,
        'description': DESCRIPTION,
        'tags': TAGS,
        'channel': 'NXS',
        'niche': 'gta_hype',
        'segments': [
            {'id': c['seg']['id'], 'headline': c['seg']['head'],
             'vo_text': c['seg']['text'], 'source': c['source_label'],
             'start': c['start'], 'duration': c['duration']}
            for c in clips
        ],
        'local_path': str(result),
    }
    meta_path = result.with_suffix('.meta.json')
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                         encoding='utf-8')

    final = probe_media(str(result))
    print(f'[done] {result} ({final["duration"]:.1f}s, '
          f'{result.stat().st_size / 1e6:.1f} MB)')
    print(f'[done] metadata: {meta_path}')

    shutil.rmtree(stage_dir, ignore_errors=True)
    return 0


def upload(mp4: str) -> int:
    path = Path(mp4)
    if not path.exists():
        print(f'[abort] no such file: {path}')
        return 2
    meta = {}
    meta_path = path.with_suffix('.meta.json')
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
    sys.path.insert(0, str(SHORTS_ROOT))
    os.chdir(SHORTS_ROOT)
    os.environ.setdefault('MILO_PIPELINE_LANE', 'shorts')
    from src.uploader import YouTubeUploader
    uploader = YouTubeUploader(channel=meta.get('channel') or 'NXS',
                               niche=meta.get('niche') or 'gta_hype',
                               privacy_status='public')
    video_id = uploader.upload_short(str(path),
                                     meta.get('title') or TITLE,
                                     meta.get('description') or DESCRIPTION,
                                     meta.get('tags') or TAGS)
    if video_id:
        print(f'UPLOAD_OK video_id={video_id} '
              f'https://www.youtube.com/watch?v={video_id}')
        return 0
    print('UPLOAD_FAILED')
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', action='store_true',
                        help='generate VO, download footage, render')
    parser.add_argument('--upload', metavar='MP4',
                        help='upload a built mp4 to NXS')
    args = parser.parse_args()
    if args.build:
        return build()
    if args.upload:
        return upload(args.upload)
    parser.print_help()
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
