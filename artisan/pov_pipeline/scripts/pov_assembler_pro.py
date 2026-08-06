                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 #!/usr/bin/env python3
"""
POV Assembler Pro — Manifest-driven (v7.3.4)

v7.3.4 — CPU throttling actually works now
------------------------------------------
  * FIX: Windows priority class is now applied to EVERY ffmpeg subprocess
    (render AND merge), not just to the Python parent process.
  * FIX: Merge step now respects CPU preset. Previously the merge spawned
    one giant ffmpeg with unrestricted threads → PC freeze.
  * NEW: --merge-cpu-preset (defaults to 'light') so merge is always
    gentle on the system regardless of render preset.
  * FIX: 'idle' and 'light' now map to IDLE_PRIORITY_CLASS on Windows
    (was BELOW_NORMAL, which wasn't gentle enough).
  * FIX: Filter threads are now hard-capped on idle/light presets.
  * FIX: Workers reduced on light preset (was cpus//3, now max 2).
  * FIX: Small stagger between worker submissions on idle preset to
    avoid simultaneous ffmpeg startup spikes.
"""

import os, re, sys, json, time, shutil, hashlib, argparse, subprocess, traceback, platform
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(it, **kw): return it

try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False

# ─── DEFAULTS ──────────────────────────────────────────────────────────────
WIDTH, HEIGHT, FPS = 1920, 1080, 30
CRF, PRESET        = 23, "veryfast"
V_CODEC, A_CODEC   = "libx264", "aac"
A_BITRATE, PIX_FMT = "192k", "yuv420p"
DUR_TOLERANCE      = 1.5
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
AUD_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}

# ─── SUB-IMAGE CONFIG ──────────────────────────────────────────────────────
SUB_SUFFIXES        = ["B", "C", "D", "E"]
MAX_SUB_IMAGES      = 1 + len(SUB_SUFFIXES)
DEFAULT_SUB_THRESH  = -35.0
DEFAULT_SUB_MIN     = 0.18
RETRY_SUB_THRESH    = -30.0
RETRY_SUB_MIN       = 0.12
SUB_CUT_OFFSET      = 0.06

# ─── PROCEDURAL CARD COLORS ────────────────────────────────────────────────
CARD_BG = {
    "TITLE":      "0x0a0a0a",
    "TRANSITION": "0x000000",
}
HEADER_LEVEL_COLORS = [
    "0x1e1e1e", "0x2a2520", "0x322218", "0x3a1f12",
    "0x142428", "0x1a1f2a", "0x202830", "0x101418",
    "0x080a0c", "0x1e1e1e",
]

# ─── LOUDNESS NORMALISATION DEFAULTS ───────────────────────────────────────
LUFS_TARGET = -16.0
LRA_TARGET  = 11.0
TP_TARGET   = -1.5

# ─── KEN BURNS MOTION MAP ──────────────────────────────────────────────────
KB_MOTION = {
    "ZOOM-IN":             ("1+0.15*on/{frames}",
                            "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    "ZOOM-OUT":            ("1.15-0.15*on/{frames}",
                            "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    "PAN-LEFT":            ("1.08",
                            "iw*0.04*on/{frames}", "ih/2-(ih/zoom/2)"),
    "PAN-RIGHT":           ("1.08",
                            "iw*0.04*(1-on/{frames})", "ih/2-(ih/zoom/2)"),
    "PAN-UP":              ("1.08",
                            "iw/2-(iw/zoom/2)", "ih*0.04*on/{frames}"),
    "PAN-DOWN":            ("1.08",
                            "iw/2-(iw/zoom/2)", "ih*0.04*(1-on/{frames})"),
    "DIAGONAL-UP-LEFT":    ("1.10",
                            "iw*0.04*on/{frames}", "ih*0.04*on/{frames}"),
    "DIAGONAL-UP-RIGHT":   ("1.10",
                            "iw*0.04*(1-on/{frames})", "ih*0.04*on/{frames}"),
    "DIAGONAL-DOWN-LEFT":  ("1.10",
                            "iw*0.04*on/{frames}", "ih*0.04*(1-on/{frames})"),
    "DIAGONAL-DOWN-RIGHT": ("1.10",
                            "iw*0.04*(1-on/{frames})", "ih*0.04*(1-on/{frames})"),
    "STATIC":              None,
    "STATIC-BREATHE":      ("1+0.003*on/{frames}",
                            "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
}

SCRIPT_DIR = Path(__file__).resolve().parent
MANIFEST_OPEN  = "=== SEGMENT MANIFEST ==="
MANIFEST_CLOSE = "=== END MANIFEST ==="
MANIFEST_COLS  = "=== COLUMNS ==="


# ─── WINDOWS PRIORITY FLAGS ───────────────────────────────────────────────
# CREATE_NO_WINDOW = 0x08000000 (avoid spawning console windows for children)
# IDLE_PRIORITY_CLASS     = 0x00000040
# BELOW_NORMAL_PRIORITY   = 0x00004000
# NORMAL_PRIORITY_CLASS   = 0x00000020
_WIN_IDLE         = 0x00000040
_WIN_BELOW_NORMAL = 0x00004000
_WIN_NORMAL       = 0x00000020
_WIN_NO_WINDOW    = 0x08000000


# ─── CPU PRESETS ───────────────────────────────────────────────────────────
def resolve_cpu_preset(name: str):
    """
    Returns (workers, ffmpeg_threads_per_proc, nice_level, filter_threads_cap)
    filter_threads_cap: hard cap for -filter_threads / -filter_complex_threads.
                       0 = let ffmpeg auto-pick.
    """
    cpus = os.cpu_count() or 4
    name = (name or "balanced").lower()
    if name == "idle":
        # Truly background — one tiny worker, single thread, IDLE priority.
        w, t, n, ft = 1, 1, 19, 1
    elif name == "light":
        # You can work in other apps. Max 2 workers, 1 thread each.
        w, t, n, ft = min(2, max(1, cpus // 4)), 1, 15, 1
    elif name == "balanced":
        w = max(1, cpus // 2)
        t = max(1, min(2, cpus // max(1, w)))
        n, ft = 5, 2
    elif name == "performance":
        w, t, n, ft = max(1, cpus - 1), 2, 0, 0
    elif name == "max":
        w, t, n, ft = max(1, cpus - 1), 0, 0, 0
    else:
        raise ValueError(f"unknown --cpu-preset '{name}'")
    return w, t, n, ft


# Module-level CPU state (read by ff_cmd / run_ff / _popen_kwargs)
FFMPEG_THREADS = 2
FILTER_THREADS = 2     # NEW: hard cap for filter graph threads
NICE_LEVEL     = 5


def _win_priority_flag(nice_level: int) -> int:
    """Map a POSIX-style nice level to a Windows priority class flag."""
    if nice_level >= 15:
        return _WIN_IDLE
    if nice_level >= 5:
        return _WIN_BELOW_NORMAL
    return _WIN_NORMAL


def _popen_kwargs(nice_override: int = None):
    """
    Build kwargs to pass to subprocess so the *child* ffmpeg runs at the
    correct OS priority. This is the critical bit — previously the child
    ffmpeg ran at NORMAL even if Python was IDLE, which is why your PC
    still froze.
    """
    kw = {}
    nl = NICE_LEVEL if nice_override is None else nice_override
    if platform.system() == "Windows":
        kw["creationflags"] = _win_priority_flag(nl) | _WIN_NO_WINDOW
    return kw


def _posix_preexec(nice_override: int = None):
    if platform.system() == "Windows":
        return None
    nl = NICE_LEVEL if nice_override is None else nice_override
    def _set():
        try:
            os.nice(nl)
        except Exception:
            pass
    return _set


def _apply_self_priority(nice_level: int):
    """Apply priority to THIS Python process."""
    try:
        if platform.system() == "Windows":
            if HAVE_PSUTIL:
                p = psutil.Process()
                if nice_level >= 15:
                    p.nice(psutil.IDLE_PRIORITY_CLASS)
                elif nice_level >= 5:
                    p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                else:
                    p.nice(psutil.NORMAL_PRIORITY_CLASS)
        else:
            os.nice(nice_level)
    except Exception:
        pass


# ─── FFMPEG LOCATOR ────────────────────────────────────────────────────────
def get_ffmpeg_paths():
    ff = shutil.which("ffmpeg"); fp = shutil.which("ffprobe")
    if ff and fp: return ff, fp
    for p in Path.cwd().rglob("ffmpeg.exe"):
        if (p.parent / "ffprobe.exe").exists():
            return str(p), str(p.parent / "ffprobe.exe")
    sys.exit("[error] ffmpeg/ffprobe not found.")
FFMPEG, FFPROBE = get_ffmpeg_paths()


def ff_cmd(args, with_filter_threads: bool = False,
           filter_threads_override: int = None):
    threads = FFMPEG_THREADS
    prefix = [FFMPEG]
    if threads and threads > 0:
        prefix += ["-threads", str(threads)]
    if with_filter_threads:
        ft = (filter_threads_override
              if filter_threads_override is not None
              else FILTER_THREADS)
        if ft and ft > 0:
            prefix += ["-filter_threads", str(ft),
                       "-filter_complex_threads", str(ft)]
    return prefix + list(args)


def run_ff(cmd_args, with_filter_threads: bool = False,
           filter_threads_override: int = None,
           nice_override: int = None, **kw):
    cmd = ff_cmd(cmd_args,
                 with_filter_threads=with_filter_threads,
                 filter_threads_override=filter_threads_override)
    popen_kw = _popen_kwargs(nice_override=nice_override)
    preexec = _posix_preexec(nice_override=nice_override)
    if preexec is not None and "preexec_fn" not in kw:
        popen_kw["preexec_fn"] = preexec
    return subprocess.run(cmd, capture_output=True, text=True,
                          **popen_kw, **kw)


def run(cmd, **kw):
    # Also de-prioritize ffprobe etc. on Windows.
    popen_kw = {}
    if platform.system() == "Windows":
        popen_kw["creationflags"] = _win_priority_flag(NICE_LEVEL) | _WIN_NO_WINDOW
    return subprocess.run(cmd, capture_output=True, text=True, **popen_kw, **kw)


def probe_duration(path):
    r = run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path)])
    try: return float(r.stdout.strip())
    except ValueError: return 0.0


# ─── AUDIO HELPERS ─────────────────────────────────────────────────────────
def trim_audio(src, tmp_dir, threshold_db=-35.0, pad=0.3):
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dst = tmp_dir / f"_trim_{src.name}"
    af = (
        f"silenceremove=start_periods=1:start_duration=0.05:start_threshold={threshold_db}dB,"
        f"areverse,"
        f"silenceremove=start_periods=1:start_duration=0.05:start_threshold={threshold_db}dB,"
        f"areverse,"
        f"apad=pad_dur={pad}"
    )
    args = ["-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src), "-af", af, str(dst)]
    r = run_ff(args, with_filter_threads=True)
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 100:
        return src, probe_duration(src)
    new_dur = probe_duration(dst)
    if new_dur <= 0:
        return src, probe_duration(src)
    return dst, new_dur


def detect_silences(path, threshold_db, min_duration):
    args = ["-i", str(path),
            "-af", f"silencedetect=n={threshold_db}dB:d={min_duration}",
            "-f", "null", "-"]
    r = run_ff(args, with_filter_threads=True)
    output = r.stderr
    starts = [float(x) for x in re.findall(r"silence_start:\s*([\d.]+)", output)]
    ends   = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)",   output)]
    silences = list(zip(starts, ends))
    if len(starts) > len(ends):
        silences.append((starts[-1], probe_duration(path)))
    return silences


def compress_audio_silences(src, tmp_dir, threshold_db=-35.0,
                             min_pause=0.8, keep_pause=0.35):
    silences = detect_silences(src, threshold_db, min_pause)
    if not silences:
        return src, probe_duration(src)

    total_dur = probe_duration(src)
    keep, cursor = [], 0.0
    for s_start, s_end in silences:
        if s_start > cursor:
            keep.append((cursor, s_start + keep_pause))
        cursor = s_end
    if cursor < total_dur:
        keep.append((cursor, total_dur))
    if not keep:
        return src, total_dur

    FADE = 0.015
    n_segs = len(keep)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    seg_files = []
    for i, (t_start, t_end) in enumerate(keep):
        seg_path = tmp_dir / f"_iseg_{src.stem}_{i:04d}.m4a"
        dur_seg  = max(0.05, t_end - t_start)
        fade_parts = []
        if i > 0:
            fade_parts.append(f"afade=t=in:st=0:d={FADE}")
        if i < n_segs - 1:
            fade_out_st = max(0.0, dur_seg - FADE)
            fade_parts.append(f"afade=t=out:st={fade_out_st:.6f}:d={FADE}")
        af_flag = (["-af", ",".join(fade_parts)] if fade_parts else [])
        args = (["-y", "-hide_banner", "-loglevel", "error",
                 "-ss", f"{t_start:.6f}", "-t", f"{dur_seg:.6f}",
                 "-i", str(src)]
                + af_flag
                + ["-c:a", "aac", "-b:a", A_BITRATE, str(seg_path)])
        r = run_ff(args, with_filter_threads=True)
        if r.returncode == 0 and seg_path.exists() and seg_path.stat().st_size > 100:
            seg_files.append(seg_path)

    if not seg_files:
        return src, total_dur

    if len(seg_files) == 1:
        dst = tmp_dir / f"_icomp_{src.stem}.m4a"
        seg_files[0].rename(dst)
        return dst, probe_duration(dst)

    list_path = tmp_dir / f"_icomp_list_{src.stem}.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for sf in seg_files:
            safe = str(sf).replace("\\", "/").replace("'", r"'\''")
            f.write("file '" + safe + "'\n")

    dst = tmp_dir / f"_icomp_{src.stem}.m4a"
    args = ["-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-c", "copy", str(dst)]
    r = run_ff(args)

    for sf in seg_files:
        try: sf.unlink()
        except Exception: pass
    try: list_path.unlink()
    except Exception: pass

    if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 100:
        return src, total_dur
    new_dur = probe_duration(dst)
    if new_dur <= 0:
        return src, total_dur
    return dst, new_dur


def apply_loudnorm(src, tmp_dir, lufs=LUFS_TARGET, lra=LRA_TARGET,
                   tp=TP_TARGET, two_pass=True):
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dst = tmp_dir / f"_norm_{src.stem}.m4a"

    measured = None
    if two_pass:
        af_measure = f"loudnorm=I={lufs}:LRA={lra}:TP={tp}:print_format=json"
        args = ["-hide_banner", "-i", str(src), "-af", af_measure, "-f", "null", "-"]
        r = run_ff(args, with_filter_threads=True)
        m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.DOTALL)
        if m:
            try:
                measured = json.loads(m.group(0))
            except json.JSONDecodeError:
                measured = None

    if measured:
        af = (f"loudnorm=I={lufs}:LRA={lra}:TP={tp}:"
              f"measured_I={measured['input_i']}:"
              f"measured_LRA={measured['input_lra']}:"
              f"measured_TP={measured['input_tp']}:"
              f"measured_thresh={measured['input_thresh']}:"
              f"offset={measured['target_offset']}:"
              f"linear=true:print_format=none")
    else:
        af = f"loudnorm=I={lufs}:LRA={lra}:TP={tp}:print_format=none"

    args = ["-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src), "-af", af,
            "-ar", "44100", "-c:a", "aac", "-b:a", A_BITRATE, str(dst)]
    r = run_ff(args, with_filter_threads=True)
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 100:
        return src, probe_duration(src)
    return dst, probe_duration(dst)


# ─── MANIFEST PARSING ──────────────────────────────────────────────────────
def parse_manifest(script_path):
    text = script_path.read_text(encoding="utf-8")
    if MANIFEST_OPEN not in text or MANIFEST_CLOSE not in text:
        sys.exit(f"[error] Manifest block not found in {script_path}")
    start = text.index(MANIFEST_OPEN)
    end   = text.index(MANIFEST_CLOSE) + len(MANIFEST_CLOSE)
    block = text[start:end]
    header, rows = {}, []
    in_rows = False
    for line in block.splitlines():
        line = line.rstrip()
        if not line: continue
        if line in (MANIFEST_OPEN, MANIFEST_CLOSE): continue
        if line == MANIFEST_COLS: in_rows = True; continue
        if not in_rows:
            if ":" in line:
                k, v = line.split(":", 1)
                header[k.strip()] = v.strip()
            continue
        if line.upper().startswith("ID |"): continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 6: continue

        seg_id = parts[0]
        mode_prefix = header.get("CONTENT_MODE", "NAR").upper()
        if "-" not in seg_id and len(seg_id) >= 3:
            seg_id = f"{mode_prefix}-{seg_id}"

        rows.append({
            "ID":      seg_id,
            "ROLE":    parts[1].upper(),
            "IMG":     parts[2].upper(),
            "AUD":     parts[3].upper(),
            "DUR":     parts[4].lower(),
            "SUMMARY": "|".join(parts[5:]).strip(),
        })
    manifest_hash = hashlib.sha1(block.encode("utf-8")).hexdigest()[:12]
    return header, rows, manifest_hash


# ─── ASSET LOOKUP ─────────────────────────────────────────────────────────
_SPEAKER_TOKENS = ("NAR", "VO", "SFX", "MUS")


def _id_variants(seg_id: str):
    sid = seg_id.strip()
    seen = []
    def _push(v):
        if v and v not in seen:
            seen.append(v)
    _push(sid)
    upper = sid.upper()
    for tok in _SPEAKER_TOKENS:
        if upper.startswith(tok + "-"):
            _push(sid[len(tok) + 1:])
            break
    if not any(upper.startswith(tok + "-") for tok in _SPEAKER_TOKENS):
        _push(f"NAR-{sid}")
    return seen


def find_audio(audio_dir, seg_id):
    for variant in _id_variants(seg_id):
        for ext in (".wav", ".mp3", ".m4a", ".ogg", ".flac"):
            p = audio_dir / f"{variant}{ext}"
            if p.exists() and p.stat().st_size > 0:
                return p
    for variant in _id_variants(seg_id):
        pattern = re.compile(rf"^{re.escape(variant)}(?![0-9A-Za-z\-])", re.IGNORECASE)
        try:
            for p in sorted(audio_dir.iterdir()):
                if p.is_file() and p.suffix.lower() in AUD_EXT and pattern.match(p.name):
                    return p
        except FileNotFoundError:
            return None
    return None


def _build_image_id_pattern(target_id):
    esc = re.escape(target_id)
    boundary = r"(?:\.|_|\]|\)|\s|-(?=[^A-Za-z])|$)"
    return re.compile(rf"^(?:\[{esc}\]|{esc}{boundary})", re.IGNORECASE)


def _find_image_for_stem(images_dir, target_id):
    for variant in _id_variants(target_id):
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            p = images_dir / f"{variant}{ext}"
            if p.exists() and p.stat().st_size > 0:
                return p
        pattern = _build_image_id_pattern(variant)
        try:
            candidates = sorted(
                p for p in images_dir.iterdir()
                if p.is_file() and p.suffix.lower() in IMG_EXT and pattern.match(p.name)
            )
        except FileNotFoundError:
            return None
        if candidates:
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return candidates[0]
    return None


def find_image(images_dir, seg_id):
    return _find_image_for_stem(images_dir, seg_id)


def find_sub_images(images_dir, seg_id):
    primary = _find_image_for_stem(images_dir, seg_id)
    if primary is None:
        return []
    images = [primary]
    gap_found = False
    for suffix in SUB_SUFFIXES:
        sub = _find_image_for_stem(images_dir, f"{seg_id}-{suffix}")
        if sub is None:
            gap_found = True
            continue
        if gap_found:
            raise ValueError(
                f"E-SUBIMAGE-ORPHAN: {seg_id}-{suffix} exists but a "
                f"preceding sub-image in the sequence is missing."
            )
        images.append(sub)

    overflow = []
    for ch in ("F", "G", "H", "I", "J", "K"):
        if _find_image_for_stem(images_dir, f"{seg_id}-{ch}") is not None:
            overflow.append(f"{seg_id}-{ch}")
    if overflow:
        raise ValueError(
            f"E-SUBIMAGE-OVERFLOW: sub-images beyond -E are not allowed. "
            f"Offending files: {', '.join(overflow)}. "
            f"Re-segment upstream — max {MAX_SUB_IMAGES} images per segment."
        )
    return images


def _debug_dump_image_dir(images_dir, missing_ids, limit=60):
    try:
        all_files = sorted(p.name for p in images_dir.iterdir()
                           if p.is_file() and p.suffix.lower() in IMG_EXT)
    except FileNotFoundError:
        print(f"  [debug] images_dir does not exist: {images_dir}")
        return
    print(f"\n[debug] Image folder contains {len(all_files)} files. "
          f"Checking {min(len(missing_ids), limit)} missing IDs for loose substring matches…")
    for mid in missing_ids[:limit]:
        hits = [n for n in all_files if mid.lower() in n.lower()]
        if hits:
            primaries = [h for h in hits
                         if not re.search(rf"{re.escape(mid)}-[A-E](?![0-9])", h, re.IGNORECASE)]
            if primaries:
                print(f"  [debug] {mid}: possible primary candidates:")
                for h in primaries[:4]:
                    print(f"           - {h}")
            else:
                subs = [h for h in hits if h not in primaries]
                if subs:
                    print(f"  [debug] {mid}: ONLY sub-image(s) present, no primary: {subs[0]}"
                          + (f" (+{len(subs)-1} more)" if len(subs) > 1 else ""))
        else:
            print(f"  [debug] {mid}: no files contain this ID — truly absent.")
    if len(missing_ids) > limit:
        print(f"  [debug] … {len(missing_ids) - limit} more missing IDs not shown.")


# ─── PREFLIGHT ────────────────────────────────────────────────────────────
PROCEDURAL_ROLES = ("HEADER", "TRANSITION", "TITLE")


def preflight(rows, audio_dir, images_dir, expected_asset_ids):
    plan, warnings, missing_img_ids, missing_aud_ids, structural = [], [], [], [], []

    for r in rows:
        seg_id = r["ID"]
        entry = {
            "ID": seg_id, "ROLE": r["ROLE"], "AUD_PATH": None,
            "IMAGES": [], "DURATION": None,
            "IMG_FLAG": r["IMG"], "AUD_FLAG": r["AUD"],
            "SKIP": False, "SKIP_REASON": None,
        }

        if r["IMG"] == "YES":
            try:
                images = find_sub_images(images_dir, seg_id)
            except ValueError as e:
                structural.append(f"  - {e}")
                entry["SKIP"] = True
                entry["SKIP_REASON"] = str(e)
                plan.append(entry)
                continue

            if not images:
                was_included_for_img = (seg_id.upper() in expected_asset_ids)
                if not was_included_for_img or r["ROLE"] in PROCEDURAL_ROLES:
                    entry["IMG_FLAG"] = "NO"
                else:
                    warnings.append(
                        f"  - CRITICAL IMAGE MISSING: {seg_id} ({r['ROLE']}) — "
                        f"found in batch EXPECTED FILES but file not found."
                    )
                    missing_img_ids.append(seg_id)
                    entry["SKIP"] = True
                    entry["SKIP_REASON"] = "image missing"
            else:
                entry["IMAGES"] = images

        if r["AUD"] == "YES":
            aud = find_audio(audio_dir, r["ID"])
            if aud is None:
                warnings.append(
                    f"  - AUDIO MISSING: {r['ID']} ({r['ROLE']}) — "
                    f"expected {r['ID']}.wav in {audio_dir}"
                )
                missing_aud_ids.append(r["ID"])
                entry["SKIP"] = True
                entry["SKIP_REASON"] = (
                    "audio missing" if not entry["SKIP_REASON"]
                    else entry["SKIP_REASON"] + " + audio missing"
                )
            else:
                entry["AUD_PATH"] = aud
                entry["DURATION"] = probe_duration(aud)
        else:
            try:
                entry["DURATION"] = float(r["DUR"])
            except (ValueError, TypeError):
                warnings.append(
                    f"  - DURATION INVALID: {r['ID']} ({r['ROLE']}) — "
                    f"AUD=NO but DUR='{r['DUR']}' is not a number"
                )
                entry["SKIP"] = True
                entry["SKIP_REASON"] = (
                    "bad duration" if not entry["SKIP_REASON"]
                    else entry["SKIP_REASON"] + " + bad duration"
                )
        plan.append(entry)

    if structural:
        print("\n[preflight] STRUCTURAL ISSUES (sub-image ordering):")
        for s in structural: print(s)

    if warnings:
        print(f"\n[preflight] {len(warnings)} asset issue(s) — these segments will be SKIPPED:")
        for w in warnings[:40]: print(w)
        if len(warnings) > 40:
            print(f"  … and {len(warnings) - 40} more.")
        if missing_img_ids:
            _debug_dump_image_dir(images_dir, missing_img_ids)
        print("\n[preflight] Continuing with available segments.\n")
    else:
        print("[preflight] All assets present.")

    return plan


# ─── KB DIRECTIVES ────────────────────────────────────────────────────────
_KB_RE = re.compile(r"\[KB:\s*([A-Z0-9\-]+)\]", re.IGNORECASE)
_SEG_ID_RE = re.compile(r"^\[([A-Z]+-\d+(?:-[A-E])?)\]", re.IGNORECASE)


def parse_kb_directives(project_dir):
    kb_map = {}
    expected_asset_ids = set()
    prompts_dir = project_dir / "05_IMAGES"
    if not prompts_dir.exists():
        return kb_map, expected_asset_ids

    final_file = prompts_dir / "IMAGE_PROMPTS_BATCH_FINAL.txt"
    if final_file.exists():
        batch_files = [final_file]
        print(f"[init] Master manifest found: {final_file.name}")
        try:
            content = final_file.read_text(encoding="utf-8", errors="replace")
            m_expected = re.search(r"EXPECTED FILES:\s*(?:\[)?(.*?)(?:\])?(?:\r?\n|$)", content, re.IGNORECASE)
            if m_expected:
                raw_list = m_expected.group(1)
                ids = {s.strip().upper() for s in raw_list.split(",") if s.strip()}
                expected_asset_ids = ids
                print(f"[init] Metadata loaded: {len(expected_asset_ids)} expected asset(s).")
        except Exception as e:
            print(f"[warn] Failed to parse metadata from {final_file.name}: {e}")
    else:
        seen = set()
        batch_files = []
        for p in (list(prompts_dir.glob("IMAGE_PROMPTS_BATCH_*.TXT")) +
                  list(prompts_dir.glob("IMAGE_PROMPTS_BATCH_*.txt"))):
            key = str(p.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            batch_files.append(p)
        batch_files.sort(key=lambda p: p.name.upper())

    for bf in batch_files:
        try:
            text = bf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        current_id = None
        for line in text.splitlines():
            m = _SEG_ID_RE.match(line.strip())
            if m:
                current_id = m.group(1).upper()
            if current_id:
                kb_m = _KB_RE.search(line)
                if kb_m:
                    directive = kb_m.group(1).upper()
                    if directive in KB_MOTION:
                        kb_map[current_id] = directive
    return kb_map, expected_asset_ids


# ─── RENDERING ────────────────────────────────────────────────────────────
def build_vf(dur, kb_directive):
    motion = None
    if kb_directive:
        motion = KB_MOTION.get(kb_directive.upper())
    if motion is None:
        return (
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={FPS},format={PIX_FMT}"
        )
    frames = max(1, int(dur * FPS))
    z_expr, x_expr, y_expr = motion
    z_expr = z_expr.replace("{frames}", str(frames))
    x_expr = x_expr.replace("{frames}", str(frames))
    y_expr = y_expr.replace("{frames}", str(frames))
    return (
        f"scale={WIDTH*2}:{HEIGHT*2}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={WIDTH*2}:{HEIGHT*2}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"zoompan=z='{z_expr}':d={frames}:x='{x_expr}':y='{y_expr}':"
        f"s={WIDTH}x{HEIGHT}:fps={FPS},setsar=1,format={PIX_FMT}"
    )


def header_card_color(role, header_index=0):
    if role == "HEADER":
        return HEADER_LEVEL_COLORS[header_index % len(HEADER_LEVEL_COLORS)]
    return CARD_BG.get(role, "0x000000")


def render_procedural_card(entry, out_path, header_index=0):
    dur = entry["DURATION"]
    if dur is None or dur <= 0:
        return False, f"{entry['ID']}: bad duration {dur}", None
    color = header_card_color(entry["ROLE"], header_index)
    part = out_path.with_name(out_path.stem + ".part.mp4")
    args = ["-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-t", f"{dur:.3f}",
            "-i", f"color=c={color}:s={WIDTH}x{HEIGHT}:r={FPS}",
            "-f", "lavfi", "-t", f"{dur:.3f}",
            "-i", "anullsrc=r=44100:cl=stereo",
            "-c:v", V_CODEC, "-preset", PRESET, "-crf", str(CRF),
            "-pix_fmt", PIX_FMT,
            "-c:a", A_CODEC, "-b:a", A_BITRATE,
            "-shortest", "-movflags", "+faststart", str(part)]
    r = run_ff(args)
    if r.returncode != 0 or not part.exists() or part.stat().st_size < 1024:
        if part.exists():
            try: part.unlink()
            except Exception: pass
        return False, f"{entry['ID']}: procedural card failed: {r.stderr[-400:]}", None
    out_dur = probe_duration(part)
    if abs(out_dur - dur) > DUR_TOLERANCE:
        part.unlink(missing_ok=True)
        return False, (f"{entry['ID']}: card duration mismatch "
                       f"(expected {dur:.2f}, got {out_dur:.2f})"), None
    part.replace(out_path)
    return True, f"{entry['ID']}: card ok ({out_dur:.2f}s, {entry['ROLE']})", out_dur


def _score_silences_balanced(silences, total_dur, needed):
    if not silences or needed <= 0:
        return []
    cands = [(s, e, s + (e - s) / 2.0, e - s) for s, e in silences]
    chosen = []
    cands_sorted = sorted(cands, key=lambda c: c[3], reverse=True)
    chosen.append(cands_sorted[0])
    while len(chosen) < needed and len(chosen) < len(cands):
        best, best_score = None, -1.0
        chosen_mids = [c[2] for c in chosen]
        for c in cands:
            if c in chosen:
                continue
            mid = c[2]
            nearest = min(abs(mid - cm) for cm in chosen_mids)
            balance = nearest / max(total_dur, 1.0)
            length  = c[3]
            score = length * (0.5 + balance)
            if score > best_score:
                best_score, best = score, c
        if best is None:
            break
        chosen.append(best)
    chosen.sort(key=lambda c: c[0])
    return [(c[0], c[1]) for c in chosen]


def plan_sub_image_cuts(audio_path, n_images,
                        threshold_db=DEFAULT_SUB_THRESH,
                        min_dur=DEFAULT_SUB_MIN):
    total = probe_duration(audio_path)
    if total <= 0 or n_images < 1:
        return [(0.0, total)], 1, "single"
    if n_images == 1:
        return [(0.0, total)], 1, "silence"

    needed = n_images - 1
    silences = detect_silences(audio_path, threshold_db, min_dur)
    silences = [(s, e) for s, e in silences if s > 0.5 and e < total - 0.2]

    if len(silences) < needed:
        silences2 = detect_silences(audio_path, RETRY_SUB_THRESH, RETRY_SUB_MIN)
        silences2 = [(s, e) for s, e in silences2 if s > 0.5 and e < total - 0.2]
        if len(silences2) > len(silences):
            silences = silences2

    if not silences:
        return [(0.0, total)], 1, "single"

    if len(silences) >= needed:
        chosen = _score_silences_balanced(silences, total, needed)
        mode = "silence"
    else:
        chosen = sorted(silences, key=lambda x: x[0])
        mode = "partial"

    cuts = []
    for s_start, s_end in chosen:
        s_dur = s_end - s_start
        offset = min(SUB_CUT_OFFSET, s_dur * 0.4)
        cuts.append(s_start + offset)

    slices, prev = [], 0.0
    for c in cuts:
        slices.append((prev, c))
        prev = c
    slices.append((prev, total))
    return slices, len(slices), mode


def render_mini_clip(image_path, audio_path, start, end, out_path, kb_directive):
    dur = max(0.05, end - start)
    vf = build_vf(dur, kb_directive)
    args = ["-y", "-hide_banner", "-loglevel", "error",
            "-loop", "1", "-t", f"{dur:.6f}", "-i", str(image_path),
            "-ss", f"{start:.6f}", "-t", f"{dur:.6f}", "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", vf, "-r", str(FPS),
            "-c:v", V_CODEC, "-preset", PRESET, "-crf", str(CRF),
            "-pix_fmt", PIX_FMT,
            "-c:a", A_CODEC, "-b:a", A_BITRATE,
            "-shortest", "-movflags", "+faststart", str(out_path)]
    r = run_ff(args, with_filter_threads=True)
    if r.returncode != 0 or not out_path.exists() or out_path.stat().st_size < 512:
        return False, f"mini-clip failed: {r.stderr[-300:]}"
    return True, "ok"


def concat_mini_clips(clip_paths, out_path, sub_crossfade=0.0):
    if len(clip_paths) == 1:
        clip_paths[0].replace(out_path)
        return True, "ok"

    if sub_crossfade <= 0:
        list_path = out_path.parent / f"_subclip_list_{out_path.stem}.txt"
        try:
            with open(list_path, "w", encoding="utf-8") as f:
                for p in clip_paths:
                    safe = str(p).replace("\\", "/").replace("'", r"'\''")
                    f.write(f"file '{safe}'\n")
            args = ["-y", "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", str(list_path),
                    "-c", "copy", "-movflags", "+faststart", str(out_path)]
            r = run_ff(args)
            if r.returncode != 0:
                args = ["-y", "-hide_banner", "-loglevel", "error",
                        "-f", "concat", "-safe", "0", "-i", str(list_path),
                        "-c:v", V_CODEC, "-preset", PRESET, "-crf", str(CRF),
                        "-pix_fmt", PIX_FMT,
                        "-c:a", A_CODEC, "-b:a", A_BITRATE,
                        "-movflags", "+faststart", str(out_path)]
                r = run_ff(args)
        finally:
            try: list_path.unlink()
            except Exception: pass
        for p in clip_paths:
            try: p.unlink()
            except Exception: pass
        if r.returncode != 0:
            return False, f"concat failed: {r.stderr[-300:]}"
        return True, "ok"

    xf = sub_crossfade
    durations = [probe_duration(p) for p in clip_paths]
    n = len(clip_paths)
    input_args = []
    for p in clip_paths:
        input_args += ["-i", str(p)]

    filters = []
    cur_v, cur_a = "[0:v]", "[0:a]"
    cumulative = durations[0]
    for i in range(1, n):
        seg_dur = durations[i]
        out_v, out_a = f"[xv{i}]", f"[xa{i}]"
        if durations[i-1] <= xf or seg_dur <= xf:
            filters.append(f"{cur_v}[{i}:v]concat=n=2:v=1:a=0{out_v}")
            filters.append(f"{cur_a}[{i}:a]concat=n=2:v=0:a=1{out_a}")
            cumulative += seg_dur
        else:
            offset = max(0.001, cumulative - xf)
            filters.append(
                f"{cur_v}[{i}:v]xfade=transition=fade:duration={xf}:offset={offset:.6f}{out_v}"
            )
            filters.append(f"{cur_a}[{i}:a]acrossfade=d={xf}:c1=tri:c2=tri{out_a}")
            cumulative += seg_dur - xf
        cur_v, cur_a = out_v, out_a

    args = (["-y", "-hide_banner", "-loglevel", "error"]
            + input_args
            + ["-filter_complex", ";".join(filters),
               "-map", cur_v, "-map", cur_a,
               "-c:v", V_CODEC, "-preset", PRESET, "-crf", str(CRF),
               "-pix_fmt", PIX_FMT,
               "-c:a", A_CODEC, "-b:a", A_BITRATE,
               "-movflags", "+faststart", str(out_path)])
    r = run_ff(args, with_filter_threads=True)
    for p in clip_paths:
        try: p.unlink()
        except Exception: pass
    if r.returncode != 0:
        return False, f"sub-xfade concat failed: {r.stderr[-300:]}"
    return True, "ok"


def render_segment(entry, out_path,
                   kb_directive=None, kb_map=None, header_index=0,
                   trim_silence=False, silence_threshold=-35.0,
                   silence_pad=0.3, max_internal_pause=0.0,
                   loudnorm=True, loudnorm_two_pass=True,
                   sub_silence_threshold=DEFAULT_SUB_THRESH,
                   sub_silence_min=DEFAULT_SUB_MIN, sub_crossfade=0.0):
    images   = entry.get("IMAGES", []) or []
    aud      = entry.get("AUD_PATH")
    dur      = entry["DURATION"]
    img_flag = entry.get("IMG_FLAG", "YES")

    if img_flag == "NO":
        return render_procedural_card(entry, out_path, header_index=header_index)
    if not images:
        return False, f"{entry['ID']}: no images (preflight bug)", None
    if dur is None or dur <= 0:
        return False, f"{entry['ID']}: bad duration {dur}", None

    trimmed_tmp = None
    if aud is not None:
        trim_dir = out_path.parent / "_trim"
        if trim_silence:
            aud, dur = trim_audio(aud, trim_dir, silence_threshold, silence_pad)
            trimmed_tmp = aud
        if max_internal_pause > 0:
            aud, dur = compress_audio_silences(
                aud, trim_dir,
                threshold_db=silence_threshold,
                min_pause=max_internal_pause,
                keep_pause=silence_pad,
            )
            trimmed_tmp = aud
        if loudnorm:
            aud_norm, dur = apply_loudnorm(aud, trim_dir, two_pass=loudnorm_two_pass)
            if (trimmed_tmp is not None and trimmed_tmp != aud_norm
                and trimmed_tmp.exists()
                and trimmed_tmp.parent == trim_dir):
                try: trimmed_tmp.unlink()
                except Exception: pass
            aud = aud_norm
            trimmed_tmp = aud_norm

    if len(images) == 1 or aud is None:
        img  = images[0]
        vf   = build_vf(dur, kb_directive)
        part = out_path.with_name(out_path.stem + ".part.mp4")
        if aud is None:
            args = ["-y", "-hide_banner", "-loglevel", "error",
                    "-loop", "1", "-t", f"{dur:.3f}", "-i", str(img),
                    "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo",
                    "-vf", vf, "-r", str(FPS),
                    "-c:v", V_CODEC, "-preset", PRESET, "-crf", str(CRF),
                    "-pix_fmt", PIX_FMT,
                    "-c:a", A_CODEC, "-b:a", A_BITRATE,
                    "-shortest", "-movflags", "+faststart", str(part)]
        else:
            args = ["-y", "-hide_banner", "-loglevel", "error",
                    "-loop", "1", "-i", str(img), "-i", str(aud),
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-vf", vf, "-r", str(FPS),
                    "-c:v", V_CODEC, "-preset", PRESET, "-crf", str(CRF),
                    "-pix_fmt", PIX_FMT,
                    "-c:a", A_CODEC, "-b:a", A_BITRATE,
                    "-shortest", "-movflags", "+faststart", str(part)]
        r = run_ff(args, with_filter_threads=True)
        if r.returncode != 0 or not part.exists() or part.stat().st_size < 1024:
            if part.exists():
                try: part.unlink()
                except Exception: pass
            return False, f"{entry['ID']}: ffmpeg failed: {r.stderr[-400:]}", None
        out_dur = probe_duration(part)
        if abs(out_dur - dur) > DUR_TOLERANCE:
            part.unlink(missing_ok=True)
            if trimmed_tmp and trimmed_tmp.exists():
                try: trimmed_tmp.unlink()
                except Exception: pass
            return False, (f"{entry['ID']}: duration mismatch "
                           f"(expected {dur:.2f}, got {out_dur:.2f})"), None
        part.replace(out_path)
        if trimmed_tmp and trimmed_tmp.exists():
            try: trimmed_tmp.unlink()
            except Exception: pass
        notes = []
        if trim_silence:               notes.append(f"trimmed→{out_dur:.2f}s")
        if kb_directive:               notes.append(f"KB:{kb_directive}")
        if loudnorm and aud is not None:
            notes.append("loudnorm-2pass" if loudnorm_two_pass else "loudnorm")
        note_str = f" [{', '.join(notes)}]" if notes else ""
        return True, f"{entry['ID']}: ok ({out_dur:.2f}s){note_str}", out_dur

    n_imgs = len(images)
    slices, used_n, mode = plan_sub_image_cuts(
        aud, n_imgs,
        threshold_db=sub_silence_threshold,
        min_dur=sub_silence_min,
    )
    images_to_use = images[:used_n] if used_n < n_imgs else images

    if mode == "single":
        print(f"  [warn] {entry['ID']}: no silences detected — primary image only.")
        saved = entry["IMAGES"]
        entry["IMAGES"] = [images[0]]
        result = render_segment(entry, out_path,
                                kb_directive=kb_directive, kb_map=kb_map,
                                header_index=header_index,
                                trim_silence=False,
                                silence_threshold=silence_threshold,
                                silence_pad=silence_pad,
                                max_internal_pause=0,
                                loudnorm=False,
                                loudnorm_two_pass=loudnorm_two_pass,
                                sub_silence_threshold=sub_silence_threshold,
                                sub_silence_min=sub_silence_min,
                                sub_crossfade=sub_crossfade)
        entry["IMAGES"] = saved
        if trimmed_tmp and trimmed_tmp.exists() and trimmed_tmp != entry["AUD_PATH"]:
            try: trimmed_tmp.unlink()
            except Exception: pass
        return result

    clip_dir = out_path.parent / "_subclips" / entry["ID"]
    clip_dir.mkdir(parents=True, exist_ok=True)
    clip_paths, failures = [], []

    for i, ((s_start, s_end), img) in enumerate(zip(slices, images_to_use)):
        sub_id = entry["ID"] if i == 0 else f"{entry['ID']}-{SUB_SUFFIXES[i-1]}"
        sub_kb = (kb_map or {}).get(sub_id.upper(), kb_directive)
        clip_path = clip_dir / f"{sub_id}.mp4"
        ok, msg = render_mini_clip(img, aud, s_start, s_end, clip_path, sub_kb)
        if not ok:
            failures.append(f"{sub_id}: {msg}")
            break
        clip_paths.append(clip_path)

    if failures or not clip_paths:
        for p in clip_paths:
            try: p.unlink()
            except Exception: pass
        try: clip_dir.rmdir()
        except Exception: pass
        if trimmed_tmp and trimmed_tmp.exists():
            try: trimmed_tmp.unlink()
            except Exception: pass
        return False, f"{entry['ID']}: sub-image render failed: {'; '.join(failures)}", None

    ok, msg = concat_mini_clips(clip_paths, out_path, sub_crossfade=sub_crossfade)
    try: clip_dir.rmdir()
    except Exception: pass
    if trimmed_tmp and trimmed_tmp.exists():
        try: trimmed_tmp.unlink()
        except Exception: pass

    if not ok:
        return False, f"{entry['ID']}: sub-image concat failed: {msg}", None

    out_dur = probe_duration(out_path)
    if abs(out_dur - dur) > DUR_TOLERANCE:
        out_path.unlink(missing_ok=True)
        return False, (f"{entry['ID']}: sub-image duration mismatch "
                       f"(expected {dur:.2f}, got {out_dur:.2f})"), None

    notes = [f"sub×{used_n}", f"mode={mode}"]
    if mode == "partial":
        notes.append(f"only {used_n}/{n_imgs} silences found")
    if sub_crossfade > 0:
        notes.append(f"xfade={sub_crossfade}s")
    if loudnorm:
        notes.append("loudnorm-2pass" if loudnorm_two_pass else "loudnorm")
    return True, f"{entry['ID']}: ok ({out_dur:.2f}s) [{', '.join(notes)}]", out_dur


def render_worker(task):
    (entry, out_path_str, kb_directive, kb_map, header_index,
     trim_silence, silence_threshold, silence_pad, max_internal_pause,
     loudnorm, loudnorm_two_pass, sub_thresh, sub_min, sub_xf,
     ff_threads, nice_lvl, filter_threads) = task
    global FFMPEG_THREADS, NICE_LEVEL, FILTER_THREADS
    FFMPEG_THREADS = ff_threads
    NICE_LEVEL     = nice_lvl
    FILTER_THREADS = filter_threads
    # Apply priority to this worker process BEFORE any ffmpeg call.
    _apply_self_priority(nice_lvl)
    try:
        return render_segment(
            entry, Path(out_path_str),
            kb_directive=kb_directive, kb_map=kb_map,
            header_index=header_index,
            trim_silence=trim_silence,
            silence_threshold=silence_threshold,
            silence_pad=silence_pad,
            max_internal_pause=max_internal_pause,
            loudnorm=loudnorm,
            loudnorm_two_pass=loudnorm_two_pass,
            sub_silence_threshold=sub_thresh,
            sub_silence_min=sub_min,
            sub_crossfade=sub_xf,
        )
    except Exception as e:
        return False, f"{entry['ID']}: exception {e}\n{traceback.format_exc()}", None


def is_segment_valid(seg_path, expected_dur, trim_active=False):
    if not seg_path.exists() or seg_path.stat().st_size < 1024:
        return False
    actual = probe_duration(seg_path)
    if trim_active:
        return actual > 0.1
    return abs(actual - expected_dur) <= DUR_TOLERANCE


def merge_segments(segment_paths, final_path, crossfade_dur=0.0,
                   merge_threads=1, merge_filter_threads=1,
                   merge_nice=15):
    """
    Heavy ffmpeg merge step. Now respects CPU throttling — previously this
    was the #1 reason the PC froze, because the xfade graph spawned an
    unconstrained ffmpeg.
    """
    if crossfade_dur <= 0:
        list_path = final_path.parent / "_final_concat.txt"
        try:
            with open(list_path, "w", encoding="utf-8") as f:
                for p in segment_paths:
                    safe = str(p).replace("\\", "/").replace("'", r"'\''")
                    f.write(f"file '{safe}'\n")
            # stream-copy first — barely uses CPU.
            args = ["-y", "-hide_banner", "-loglevel", "error",
                    "-threads", str(merge_threads),
                    "-f", "concat", "-safe", "0", "-i", str(list_path),
                    "-map", "0:v:0", "-map", "0:a:0",
                    "-c", "copy", "-movflags", "+faststart", str(final_path)]
            # bypass ff_cmd's auto-thread injection by calling subprocess directly
            popen_kw = _popen_kwargs(nice_override=merge_nice)
            preexec = _posix_preexec(nice_override=merge_nice)
            if preexec is not None:
                popen_kw["preexec_fn"] = preexec
            r = subprocess.run([FFMPEG] + args, capture_output=True, text=True, **popen_kw)
            if r.returncode != 0:
                print("[merge] stream-copy failed, re-encoding (throttled)...")
                args = ["-y", "-hide_banner", "-loglevel", "error",
                        "-threads", str(merge_threads),
                        "-filter_threads", str(merge_filter_threads),
                        "-filter_complex_threads", str(merge_filter_threads),
                        "-f", "concat", "-safe", "0", "-i", str(list_path),
                        "-map", "0:v:0", "-map", "0:a:0",
                        "-c:v", V_CODEC, "-preset", PRESET, "-crf", str(CRF),
                        "-c:a", A_CODEC, "-b:a", A_BITRATE,
                        "-movflags", "+faststart", str(final_path)]
                r = subprocess.run([FFMPEG] + args, capture_output=True, text=True, **popen_kw)
                if r.returncode != 0:
                    return False, r.stderr[-600:]
            return True, "ok"
        finally:
            try: list_path.unlink()
            except Exception: pass

    xf, n = crossfade_dur, len(segment_paths)
    if n == 1:
        try:
            shutil.copy2(str(segment_paths[0]), str(final_path))
        except Exception as e:
            return False, f"single-segment copy failed: {e}"
        return True, "ok (single segment)"

    durations = [probe_duration(p) for p in segment_paths]
    input_args = []
    for p in segment_paths:
        rel_p = os.path.relpath(str(p), start=str(final_path.parent))
        input_args += ["-i", rel_p]

    v_labels, a_labels, filters = [], [], []
    for i in range(n):
        v_norm, a_norm = f"[v{i}]", f"[a{i}]"
        filters.append(f"[{i}:v]fps={FPS},settb=AVTB,setpts=PTS-STARTPTS{v_norm}")
        filters.append(f"[{i}:a]aresample=44100,asettb=AVTB,asetpts=PTS-STARTPTS{a_norm}")
        v_labels.append(v_norm)
        a_labels.append(a_norm)

    cur_v, cur_a = v_labels[0], a_labels[0]
    cumulative = durations[0]

    for i in range(1, n):
        seg_dur = durations[i]
        out_v, out_a = f"[xv{i}]", f"[xa{i}]"
        if durations[i-1] <= xf or seg_dur <= xf:
            filters.append(f"{cur_v}{v_labels[i]}concat=n=2:v=1:a=0{out_v}")
            filters.append(f"{cur_a}{a_labels[i]}concat=n=2:v=0:a=1{out_a}")
            cumulative += seg_dur
        else:
            offset = max(0.001, cumulative - xf)
            filters.append(
                f"{cur_v}{v_labels[i]}xfade=transition=fade:duration={xf}:offset={offset:.6f}{out_v}"
            )
            filters.append(f"{cur_a}{a_labels[i]}acrossfade=d={xf}:c1=tri:c2=tri{out_a}")
            cumulative += seg_dur - xf
        cur_v, cur_a = out_v, out_a

    filter_complex = ";".join(filters)
    fc_script_path = Path(final_path).parent / "_fc_script.txt"
    try:
        fc_script_path.write_text(filter_complex, encoding="utf-8")
        args = (["-y", "-hide_banner", "-loglevel", "error",
                 "-threads", str(merge_threads),
                 "-filter_threads", str(merge_filter_threads),
                 "-filter_complex_threads", str(merge_filter_threads)]
                + input_args
                + ["-filter_complex_script", "_fc_script.txt",
                   "-map", cur_v, "-map", cur_a,
                   "-c:v", V_CODEC, "-preset", PRESET, "-crf", str(CRF),
                   "-pix_fmt", PIX_FMT,
                   "-c:a", A_CODEC, "-b:a", A_BITRATE,
                   "-movflags", "+faststart", str(final_path.name)])
        popen_kw = _popen_kwargs(nice_override=merge_nice)
        preexec = _posix_preexec(nice_override=merge_nice)
        if preexec is not None:
            popen_kw["preexec_fn"] = preexec
        r = subprocess.run([FFMPEG] + args, capture_output=True, text=True,
                           cwd=str(final_path.parent), **popen_kw)
        if r.returncode != 0:
            return False, r.stderr[-1200:]
        return True, f"ok (crossfade {xf}s × {n-1} transitions, throttled)"
    finally:
        try: fc_script_path.unlink()
        except Exception: pass


# ─── MAIN ──────────────────────────────────────────────────────────────────
def main():
    global FFMPEG_THREADS, NICE_LEVEL, FILTER_THREADS

    ap = argparse.ArgumentParser(description="POV Assembler v7.3.4")
    ap.add_argument("--script",   required=True)
    ap.add_argument("--audio",    required=True)
    ap.add_argument("--images",   required=True)
    ap.add_argument("--output", default=os.getenv("POV_OUTPUT_DIR") or None)
    ap.add_argument("--cpu-preset",
                    choices=["idle", "light", "balanced", "performance", "max"],
                    default="balanced",
                    help="CPU usage during per-segment rendering.")
    ap.add_argument("--merge-cpu-preset",
                    choices=["idle", "light", "balanced", "performance", "max"],
                    default="light",
                    help="CPU usage during the final merge step. Defaults to "
                         "'light' to prevent the system from freezing during "
                         "the heavy crossfade pass.")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--ffmpeg-threads", type=int, default=None)
    ap.add_argument("--nice", type=int, default=None)
    ap.add_argument("--merge",    action="store_true")
    ap.add_argument("--force",    action="store_true")
    ap.add_argument("--kenburns", action="store_true")
    ap.add_argument("--segments", default=None)
    ap.add_argument("--skip-hash-check", action="store_true")
    ap.add_argument("--trim-silence", action="store_true")
    ap.add_argument("--silence-threshold", type=float, default=-35.0)
    ap.add_argument("--silence-pad", type=float, default=0.3)
    ap.add_argument("--max-internal-pause", type=float, default=0.0)
    ap.add_argument("--no-loudnorm", action="store_true")
    ap.add_argument("--loudnorm-single-pass", action="store_true")
    ap.add_argument("--crossfade", action="store_true")
    ap.add_argument("--crossfade-dur", type=float, default=0.5)
    ap.add_argument("--max-retries", type=int, default=3)
    ap.add_argument("--sub-silence-threshold", type=float, default=DEFAULT_SUB_THRESH)
    ap.add_argument("--sub-silence-min", type=float, default=DEFAULT_SUB_MIN)
    ap.add_argument("--sub-crossfade", type=float, default=0.0)
    ap.add_argument("--strict-assets", action="store_true")
    ap.add_argument("--stagger-ms", type=int, default=None,
                    help="Milliseconds to wait between launching workers. "
                         "Helps avoid simultaneous ffmpeg startup spikes.")
    args = ap.parse_args()

    preset_w, preset_t, preset_n, preset_ft = resolve_cpu_preset(args.cpu_preset)
    merge_w, merge_t, merge_n, merge_ft     = resolve_cpu_preset(args.merge_cpu_preset)

    workers     = args.workers         if args.workers         is not None else preset_w
    ff_threads  = args.ffmpeg_threads  if args.ffmpeg_threads  is not None else preset_t
    nice_level  = args.nice            if args.nice            is not None else preset_n
    filter_threads = preset_ft

    # Auto stagger: on idle/light, space worker launches by 250ms.
    if args.stagger_ms is not None:
        stagger_ms = max(0, args.stagger_ms)
    else:
        if args.cpu_preset == "idle":
            stagger_ms = 400
        elif args.cpu_preset == "light":
            stagger_ms = 250
        else:
            stagger_ms = 0

    FFMPEG_THREADS = ff_threads
    NICE_LEVEL     = nice_level
    FILTER_THREADS = filter_threads
    _apply_self_priority(nice_level)

    script_path = Path(args.script).resolve()
    audio_dir   = Path(args.audio).resolve()
    images_dir  = Path(args.images).resolve()

    if not script_path.exists():
        sys.exit(f"[error] Script not found: {script_path}")
    if not audio_dir.exists():
        sys.exit(f"[error] Audio dir not found: {audio_dir}")
    if not images_dir.exists():
        sys.exit(f"[error] Images dir not found: {images_dir}")

    header, rows, manifest_hash = parse_manifest(script_path)
    video_id    = header.get("VIDEO_ID", "POV-UNKNOWN")
    project_dir = script_path.parent

    print(f"[init] VIDEO_ID:      {video_id}")
    print(f"[init] Manifest hash: {manifest_hash}")
    print(f"[init] Segments:      {len(rows)}")
    print(f"[init] Render preset: {args.cpu_preset}  "
          f"(workers={workers}, ffmpeg-threads={ff_threads}, "
          f"filter-threads={filter_threads}, nice={nice_level}, "
          f"stagger={stagger_ms}ms)")
    print(f"[init] Merge preset:  {args.merge_cpu_preset}  "
          f"(threads={merge_t}, filter-threads={merge_ft}, nice={merge_n})")

    if args.output:
        out_base = Path(args.output).resolve()
    else:
        out_base = project_dir / "output_pro"
    out_dir = out_base / video_id
    seg_dir = out_dir / "segments"
    out_dir.mkdir(parents=True, exist_ok=True)
    seg_dir.mkdir(parents=True, exist_ok=True)
    print(f"[init] Output dir:    {out_dir}")
    print(f"[init] Segments dir:  {seg_dir}")

    hash_file = out_dir / "_manifest_hash.txt"
    if hash_file.exists() and not args.skip_hash_check:
        try:
            prev = hash_file.read_text(encoding="utf-8").strip()
        except Exception:
            prev = ""
        if prev and prev != manifest_hash:
            print(f"[warn] Manifest hash changed ({prev} → {manifest_hash}).")
            if not args.force and not args.segments and not args.merge:
                print("       Cached segments may be stale. Re-run with --force, "
                      "--segments, or --skip-hash-check.")
            else:
                print("       Continuing (overridden by --force / --segments / --merge).")

    kb_map, expected_asset_ids = parse_kb_directives(project_dir)
    if kb_map or expected_asset_ids:
        print(f"[init] KB directives loaded for {len(kb_map)} segment(s).")
    else:
        print("[init] No KB directives found.")

    plan = preflight(rows, audio_dir, images_dir, expected_asset_ids)

    if args.strict_assets:
        skipped = [e for e in plan if e["SKIP"]]
        if skipped:
            sys.exit(f"[error] --strict-assets: {len(skipped)} segment(s) "
                     f"have missing assets. Aborting.")

    specific_ids = None
    if args.segments:
        requested = {s.strip().upper() for s in args.segments.split(",") if s.strip()}
        all_manifest_ids = {r["ID"].upper() for r in rows}
        unknown = requested - all_manifest_ids
        if unknown:
            print(f"[warn] --segments contained ID(s) not found in manifest:")
            for uid in sorted(unknown):
                print(f"  - {uid}")
        specific_ids = requested - unknown
        if specific_ids:
            print(f"[init] --segments filter: {sorted(specific_ids)}")
        else:
            print("[warn] No valid segment IDs remain — nothing to render.")

    trim_active = (args.trim_silence or args.max_internal_pause > 0)

    user_forced_merge = args.merge
    full_render_mode  = (specific_ids is None)
    merge_only = user_forced_merge and full_render_mode and not args.force
    if merge_only:
        print("[mode] Merge-only: skipping render loop, proceeding to merge.")

    tasks = []
    skipped_existing = 0
    skipped_missing  = 0
    header_index = 0
    role_header_index_for_id = {}

    for entry in plan:
        if entry["ROLE"] == "HEADER":
            role_header_index_for_id[entry["ID"]] = header_index
            header_index += 1
        else:
            role_header_index_for_id[entry["ID"]] = 0

    if not merge_only:
        for entry in plan:
            seg_id = entry["ID"]
            if entry["SKIP"]:
                skipped_missing += 1
                continue
            if specific_ids is not None and seg_id.upper() not in specific_ids:
                continue
            seg_out = seg_dir / f"{seg_id}.mp4"
            if (not args.force and specific_ids is None
                and is_segment_valid(seg_out, entry["DURATION"], trim_active=trim_active)):
                skipped_existing += 1
                continue
            kb_directive = kb_map.get(seg_id.upper())
            if kb_directive is None and args.kenburns and entry["IMG_FLAG"] == "YES":
                kb_directive = "ZOOM-IN"
            task = (
                entry, str(seg_out), kb_directive, kb_map,
                role_header_index_for_id.get(seg_id, 0),
                args.trim_silence, args.silence_threshold, args.silence_pad,
                args.max_internal_pause,
                (not args.no_loudnorm), (not args.loudnorm_single_pass),
                args.sub_silence_threshold, args.sub_silence_min, args.sub_crossfade,
                ff_threads, nice_level, filter_threads,
            )
            tasks.append(task)

    print(f"[init] To render:     {len(tasks)}")
    print(f"[init] Already done:  {skipped_existing}")
    print(f"[init] Skipped (missing assets): {skipped_missing}")

    failures, successes = [], []
    if tasks:
        attempt = 0
        remaining = list(tasks)
        while remaining and attempt < args.max_retries:
            attempt += 1
            print(f"\n[render] Pass {attempt}/{args.max_retries} — "
                  f"{len(remaining)} segment(s), {workers} worker(s)…")
            this_round_failed = []
            if workers <= 1:
                for t in tqdm(remaining, desc=f"pass {attempt}"):
                    ok, msg, _dur = render_worker(t)
                    if ok:
                        print(f"  ✓ {msg}")
                        successes.append(t[0]["ID"])
                    else:
                        print(f"  ✗ {msg}")
                        this_round_failed.append(t)
                    if stagger_ms > 0:
                        time.sleep(stagger_ms / 1000.0)
            else:
                with ProcessPoolExecutor(max_workers=workers) as ex:
                    fut_map = {}
                    for t in remaining:
                        fut_map[ex.submit(render_worker, t)] = t
                        if stagger_ms > 0:
                            time.sleep(stagger_ms / 1000.0)
                    for fut in tqdm(as_completed(fut_map), total=len(fut_map),
                                    desc=f"pass {attempt}"):
                        t = fut_map[fut]
                        try:
                            ok, msg, _dur = fut.result()
                        except Exception as e:
                            ok, msg = False, f"{t[0]['ID']}: worker crash: {e}"
                        if ok:
                            print(f"  ✓ {msg}")
                            successes.append(t[0]["ID"])
                        else:
                            print(f"  ✗ {msg}")
                            this_round_failed.append(t)
            remaining = this_round_failed
            if remaining and attempt < args.max_retries:
                print(f"[render] {len(remaining)} failed — retrying…")
                time.sleep(1.0)
        failures = remaining

    active_plan     = [e for e in plan if not e["SKIP"]]
    skipped_assets  = [e["ID"] for e in plan if e["SKIP"]]
    ready_paths     = {}
    render_failures = []

    for entry in active_plan:
        seg_out = seg_dir / f"{entry['ID']}.mp4"
        if is_segment_valid(seg_out, entry["DURATION"], trim_active=trim_active):
            ready_paths[entry["ID"]] = seg_out
        else:
            render_failures.append(entry["ID"])

    ordered_segments = [ready_paths[e["ID"]] for e in plan if e["ID"] in ready_paths]

    aud_planned = [e for e in plan if e["AUD_FLAG"] == "YES"]
    aud_ready   = [e for e in plan if e["AUD_FLAG"] == "YES" and e["ID"] in ready_paths]

    project_complete = (
        len(render_failures) == 0
        and len(skipped_assets) == 0
        and len(failures) == 0
    )

    print(f"\n[summary] Segments ready: {len(ordered_segments)} of {len(plan)} total planned.")
    print(f"[summary] Audio segments: {len(aud_ready)} of {len(aud_planned)} expected.")
    if failures:
        print(f"[summary] Render failures after retries: {len(failures)}")
        for t in failures[:10]:
            print(f"  - {t[0]['ID']}")
        if len(failures) > 10:
            print(f"  … and {len(failures) - 10} more.")
    if skipped_assets:
        print(f"[summary] Skipped (missing assets): {len(skipped_assets)}")
        for sid in skipped_assets[:10]:
            print(f"  - {sid}")
        if len(skipped_assets) > 10:
            print(f"  … and {len(skipped_assets) - 10} more.")

    if project_complete and full_render_mode and not merge_only:
        try:
            hash_file.write_text(manifest_hash, encoding="utf-8")
        except Exception:
            pass

    if not ordered_segments:
        print("\n[merge] Nothing to merge. Done.")
        sys.exit(2 if (failures or skipped_assets) else 0)

    crossfade_dur = args.crossfade_dur if args.crossfade else 0.0

    if user_forced_merge:
        should_merge = True
    elif project_complete:
        should_merge = True
        print("[merge] All segments accounted for — auto-merging.")
    else:
        should_merge = False

    if not should_merge:
        print(f"\n[merge] Project incomplete — merge skipped.")
        print(f"        {len(render_failures)} segment(s) not yet rendered, "
              f"{len(skipped_assets)} skipped (missing assets).")
        print("        Pass --merge to force a PARTIAL_*.mp4.")
        sys.exit(2)

    xf_note    = f" (crossfade {crossfade_dur}s)" if crossfade_dur > 0 else ""
    final_name = (f"FINAL_{video_id}.mp4" if project_complete
                  else f"PARTIAL_{video_id}.mp4")
    final_path = out_dir / final_name

    print(f"\n[merge] Concatenating {len(ordered_segments)} segment(s){xf_note} → {final_path.name}")
    print(f"        Merge throttling: nice={merge_n}, threads={merge_t}, "
          f"filter-threads={merge_ft}")
    if not project_complete:
        print(f"        NOTE: incomplete — {len(skipped_assets)} skipped, "
              f"{len(render_failures)} failed. Output is PARTIAL.")

    # Switch THIS process to the merge nice level too — so any spawned
    # ffmpeg inherits the right priority on Windows.
    _apply_self_priority(merge_n)
    NICE_LEVEL = merge_n

    ok, msg = merge_segments(
        ordered_segments, final_path,
        crossfade_dur=crossfade_dur,
        merge_threads=max(1, merge_t) if merge_t > 0 else 1,
        merge_filter_threads=max(1, merge_ft) if merge_ft > 0 else 1,
        merge_nice=merge_n,
    )
    if not ok:
        print(f"[merge] FAILED: {msg}")
        sys.exit(3)

    size_mb = final_path.stat().st_size / (1024 * 1024)
    dur     = probe_duration(final_path)
    print(f"\n[done]  {final_path}")
    print(f"        {size_mb:.1f} MB / {dur/60:.1f} min  ({msg})")
    sys.exit(0 if project_complete else 2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[abort] Interrupted by user.")
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:
        print(f"\n[fatal] {e}")
        traceback.print_exc()
        sys.exit(1)
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            
