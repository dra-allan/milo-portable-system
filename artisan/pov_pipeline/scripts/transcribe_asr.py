"""Whisper ASR fallback for the POV pipeline.

Downloads a YouTube video's audio and transcribes it locally with
faster-whisper. Used when a video has no captions (e.g. Hypothetically)
or when the transcript scraper is bot-blocked.

Contract (mirrors youtube-transcript.cjs):
  - transcript goes to stdout
  - diagnostics to stderr
  - exit 0 on success

Environment:
  YT_COOKIES          cookies file path (Netscape format). Defaults to
                      $MILO_PORTABLE_SYSTEM/../../cookies.txt resolution:
                      this script lives in artisan/pov_pipeline/scripts so
                      the repo root is three parents up.
  YTDLP_COOKIES_FILE  alias for YT_COOKIES (shorts-pipeline convention).
  WHISPER_MODEL       faster-whisper model name (default "base").
  WHISPER_LANG        language code (default "en").
  WHISPER_BEAM        beam size (default 3).
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
COOKIES_CANDIDATES = [
    *(Path(v) for v in (os.environ.get("YT_COOKIES", "").strip(),
                        os.environ.get("YTDLP_COOKIES_FILE", "").strip()) if v),
    REPO_ROOT / "cookies.txt",
]


def eprint(*a, **kw):
    print(*a, **kw, file=sys.stderr)


def resolve_cookies() -> Path:
    for c in COOKIES_CANDIDATES:
        try:
            if c and c.exists():
                return c
        except OSError:
            continue
    return None


def run_ytdlp(url: str, out_path: Path, cookies: Path) -> bool:
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        eprint("[asr] yt-dlp not on PATH")
        return False
    cmd = [
        ytdlp,
        "--remote-components", "ejs:github",
        "--js-runtimes", "node",
        "--extractor-args", "youtube:player_client=tv,web",
        "--no-playlist",
        "-f", "18/best[ext=mp4]/best",
        "-o", str(out_path),
    ]
    if cookies:
        cmd += ["--cookies", str(cookies)]
    cmd.append(url)
    eprint(f"[asr] yt-dlp download -> {out_path.name}")
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if p.returncode != 0:
        eprint("[asr] yt-dlp failed:")
        eprint((p.stderr or p.stdout or "no output")[:800])
        return False
    eprint(f"[asr] downloaded in {time.time()-t0:.0f}s")
    return True


def extract_audio(video: Path, wav: Path) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        eprint("[asr] ffmpeg not on PATH")
        return False
    cmd = [ffmpeg, "-y", "-i", str(video), "-vn",
           "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(wav)]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if p.returncode != 0 or not wav.exists():
        eprint("[asr] ffmpeg extraction failed")
        eprint((p.stderr or "")[:600])
        return False
    return True


def transcribe(wav: Path, model_name: str, lang: str, beam: int) -> str:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        eprint("[asr] faster-whisper not installed. Run: "
               "python -m pip install faster-whisper")
        raise
    eprint(f"[asr] loading model '{model_name}' (CPU, int8)...")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    eprint("[asr] transcribing...")
    t0 = time.time()
    segments, _info = model.transcribe(
        str(wav), language=lang or None, vad_filter=True, beam_size=beam)
    parts = []
    for i, seg in enumerate(segments):
        parts.append(seg.text)
        if i % 40 == 0:
            eprint(f"[asr] seg {i} @ {seg.end:.0f}s "
                   f"({time.time()-t0:.0f}s elapsed)")
    eprint(f"[asr] done in {time.time()-t0:.0f}s")
    return "".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="YouTube video URL")
    ap.add_argument("--model", default=os.environ.get("WHISPER_MODEL", "base"))
    ap.add_argument("--lang", default=os.environ.get("WHISPER_LANG", "en"))
    ap.add_argument("--beam", type=int,
                    default=int(os.environ.get("WHISPER_BEAM", "3")))
    ap.add_argument("--keep", action="store_true",
                    help="keep temp audio files (for debugging)")
    args = ap.parse_args()

    cookies = resolve_cookies()
    if cookies:
        eprint(f"[asr] using cookies: {cookies}")
    else:
        eprint("[asr] WARNING: no cookies file found; download may be "
               "bot-blocked")

    tmp = Path(tempfile.mkdtemp(prefix="pov_asr_"))
    video = tmp / "src.mp4"
    wav = tmp / "src.wav"
    try:
        if not run_ytdlp(args.url, video, cookies):
            return 1
        if not extract_audio(video, wav):
            return 1
        try:
            text = transcribe(wav, args.model, args.lang, args.beam)
        except ImportError:
            return 1
        text = text.strip()
        if not text:
            eprint("[asr] transcription empty")
            return 1
        print(text)
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())