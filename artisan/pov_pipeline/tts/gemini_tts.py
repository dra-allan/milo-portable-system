#!/usr/bin/env python3
"""
Gemini TTS — Manifest-driven (v3.1)

Changes vs v3.0:
  * Strict round-robin key rotation (no key used twice in a row while others
    are available). Pointer advances by exactly 1 on each acquisition.
  * Network errors (DNS, connection reset, timeouts) now use exponential
    backoff AND do NOT consume the retry budget the same way quota errors do.
    A DNS blip no longer kills a segment in 3 seconds.
  * Retry budget raised 6 → 10, with separate counters for transient vs
    permanent failures.
  * End-of-run "sweep" pass: any segment that failed gets one more attempt
    after a 30s pause, eliminating the "skipped segments" problem.
  * Per-request hard timeout retained (default 90s).
"""

import os
import re
import sys
import json
import time
import hashlib
import threading
from pathlib import Path
from collections import deque

# ─── CONFIG ────────────────────────────────────────────────────────────────
CONFIG = {
    "default_voice": "Charon",
    "output_format": "mp3",
    "model":         "gemini-2.5-flash-preview-tts",

    # Concurrency
    "max_workers":   3,

    # Per-key rate limiting (token bucket)
    "rpm_per_key":   3,
    "max_concurrent_per_key": 1,

    # Per-request timeout (seconds)
    "request_timeout": 90,

    # Backoff ladder (seconds) when ALL keys are exhausted
    "backoff_sequence": [15, 30, 60, 120],

    # Cooldown (seconds) before retrying a 429'd key
    "key_cooldown": 35,

    # Retries
    "max_quota_attempts":   10,   # attempts that hit 429/quota
    "max_network_attempts": 8,    # attempts that hit DNS/conn/timeout

    # Network-error backoff ladder (seconds) — exponential
    "network_backoff": [2, 4, 8, 15, 30, 45, 60, 90],

    # End-of-run sweep — try failed segments one more time after this pause
    "final_sweep_pause": 30,
    "final_sweep_attempts": 2,

    # Tiny courtesy pause after a successful call (per worker)
    "base_delay": 0.0,
}
CONFIG_PATH = Path("config.json")
if CONFIG_PATH.exists():
    try:
        CONFIG.update(json.loads(CONFIG_PATH.read_text()))
    except Exception as e:
        print(f"[warn] config.json unreadable: {e}", file=sys.stderr)

# ─── MANIFEST PARSING ──────────────────────────────────────────────────────
MANIFEST_OPEN  = "=== SEGMENT MANIFEST ==="
MANIFEST_COLS  = "=== COLUMNS ==="
MANIFEST_CLOSE = "=== END MANIFEST ==="

SEG_ID_RE = re.compile(r"^\[([A-Z]{2,5}-\d{3,})\]\s*$")


def parse_manifest(script_text: str):
    if MANIFEST_OPEN not in script_text:
        raise ValueError("Manifest OPEN marker not found in script.")

    start = script_text.index(MANIFEST_OPEN)

    if MANIFEST_CLOSE in script_text:
        end = script_text.index(MANIFEST_CLOSE) + len(MANIFEST_CLOSE)
        block = script_text[start:end]
    else:
        after = script_text[start:]
        body_match = re.search(r"(?m)^\[[A-Z]{3}-\d{3,}\]\s*$", after)
        if not body_match:
            raise ValueError("Manifest CLOSE marker missing AND no segment body marker found.")
        block = after[:body_match.start()]

    header = {}
    rows = []
    in_rows = False
    for line in block.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if line == MANIFEST_OPEN or line == MANIFEST_CLOSE:
            continue
        if line == MANIFEST_COLS:
            in_rows = True
            continue
        if not in_rows:
            if ":" in line:
                k, v = line.split(":", 1)
                header[k.strip()] = v.strip()
            continue
        if line.upper().startswith("ID |"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 6:
            continue
        rows.append({
            "ID":      parts[0],
            "ROLE":    parts[1].upper(),
            "IMG":     parts[2].upper(),
            "AUD":     parts[3].upper(),
            "DUR":     parts[4].lower(),
            "SUMMARY": "|".join(parts[5:]).strip(),
        })

    return header, rows, block


def parse_segment_bodies(script_text: str, manifest_block: str):
    if MANIFEST_CLOSE in script_text:
        body = script_text.split(MANIFEST_CLOSE, 1)[1]
    else:
        block_end = script_text.index(manifest_block) + len(manifest_block)
        body = script_text[block_end:]

    bodies = {}
    current_id = None
    buf = []
    for line in body.splitlines():
        m = SEG_ID_RE.match(line.strip())
        if m:
            if current_id:
                bodies[current_id] = "\n".join(buf).strip()
            current_id = m.group(1)
            buf = []
        else:
            if current_id:
                buf.append(line)
    if current_id:
        bodies[current_id] = "\n".join(buf).strip()
    return bodies


def compute_manifest_hash(manifest_block: str) -> str:
    return hashlib.sha1(manifest_block.encode("utf-8")).hexdigest()[:12]


# ─── LIGHTWEIGHT PROBE ─────────────────────────────────────────────────────
def run_probe(script_path: Path, audio_dir: Path, fmt: str):
    try:
        if not script_path.exists():
            print("DATA|NONE|0|0|SCRIPT_MISSING|0")
            print(f"[probe] script not found: {script_path}", file=sys.stderr)
            return False

        script_text = script_path.read_text(encoding="utf-8")
        header, rows, manifest_block = parse_manifest(script_text)
        video_id = header.get("VIDEO_ID", "UNKNOWN").strip() or "UNKNOWN"

        if video_id and video_id != "UNKNOWN":
            audio_dir = audio_dir / video_id

        targets = [r for r in rows if r["AUD"] == "YES"]

        def exists_any(seg_id: str) -> bool:
            for ext in ("mp3", "wav"):
                p = audio_dir / f"{seg_id}.{ext}"
                if p.exists() and p.stat().st_size > 1024:
                    return True
            return False

        done_ids = {r["ID"] for r in targets if exists_any(r["ID"])}
        last_done_idx = -1
        for i, r in enumerate(targets):
            if r["ID"] in done_ids:
                last_done_idx = i

        gaps = [r["ID"] for i, r in enumerate(targets)
                if i < last_done_idx and r["ID"] not in done_ids]

        next_id = "DONE"
        for r in targets:
            if r["ID"] not in done_ids:
                next_id = r["ID"]
                break

        print(f"DATA|{video_id}|{len(done_ids)}|{len(targets)}|{next_id}|{len(gaps)}")
        return True
    except Exception as e:
        print("DATA|NONE|0|0|PROBE_ERROR|0")
        print(f"[probe] error: {e}", file=sys.stderr)
        return False


# ─── FULL PIPELINE ─────────────────────────────────────────────────────────
def run_generate(script_path: Path, audio_dir: Path,
                 voice=None, fmt=None, force=False, start_at=None):
    import wave
    import warnings
    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
    warnings.filterwarnings("ignore", category=RuntimeWarning, module="pydub")

    from google import genai
    from google.genai import types
    from dotenv import load_dotenv

    try:
        from tqdm import tqdm
        HAS_TQDM = True
    except ImportError:
        HAS_TQDM = False

    try:
        from pydub import AudioSegment
        HAS_PYDUB = True
    except ImportError:
        HAS_PYDUB = False

    load_dotenv()
    api_keys_str = os.getenv("GEMINI_API_KEYS") or os.getenv("GEMINI_API_KEY")
    if not api_keys_str:
        sys.exit("[error] GEMINI_API_KEY(S) not in .env")
    API_KEYS = [k.strip() for k in api_keys_str.split(",") if k.strip()]

    voice  = voice or CONFIG["default_voice"]
    fmt    = fmt   or CONFIG["output_format"]
    model  = CONFIG["model"]

    if not script_path.exists():
        sys.exit(f"[error] Script not found: {script_path}")

    script_text = script_path.read_text(encoding="utf-8")
    header, rows, manifest_block = parse_manifest(script_text)
    bodies = parse_segment_bodies(script_text, manifest_block)
    computed_hash = compute_manifest_hash(manifest_block)

    video_id = header.get("VIDEO_ID", "UNKNOWN").strip()
    if video_id and video_id != "UNKNOWN":
        audio_dir = audio_dir / video_id

    audio_dir.mkdir(parents=True, exist_ok=True)

    sidecar = audio_dir.parent / f"MANIFEST_HASH_{video_id}.txt"
    sidecar.write_text(
        f"VIDEO_ID: {video_id}\nMANIFEST_HASH: {computed_hash}\n",
        encoding="utf-8")

    targets = [r for r in rows if r["AUD"] == "YES"]

    print(f"[tts] VIDEO_ID:      {video_id}")
    print(f"[tts] CONTENT_MODE:  {header.get('CONTENT_MODE')}")
    print(f"[tts] MANIFEST_HASH: {computed_hash}")
    print(f"[tts] audio-dir:     {audio_dir}")
    print(f"[tts] model:         {model}")
    print(f"[tts] keys:          {len(API_KEYS)} × {CONFIG['rpm_per_key']} RPM each (strict round-robin)")
    print(f"[tts] audio-req:     {len(targets)}")

    if start_at:
        found = False
        for i, r in enumerate(targets):
            if r["ID"] == start_at:
                targets = targets[i:]
                print(f"[tts] Resuming from {start_at}")
                found = True
                break
        if not found:
            print(f"[warn] start-at ID '{start_at}' not found. Starting from beginning.")

    def already_present(seg_id):
        for ext in ("mp3", "wav"):
            p = audio_dir / f"{seg_id}.{ext}"
            if p.exists() and p.stat().st_size > 1024:
                return p
        return None

    todo = []
    skipped_existing = 0
    for r in targets:
        if already_present(r["ID"]) and not force:
            skipped_existing += 1
            continue
        todo.append(r)

    print(f"[tts] {skipped_existing} already present, {len(todo)} to generate.")

    missing_text = [r["ID"] for r in todo if not bodies.get(r["ID"], "").strip()]
    if missing_text:
        print("[error] AUD=YES segments with no body text:")
        for mid in missing_text:
            print(f"   - {mid}")
        sys.exit(1)

    if not todo:
        print("[tts] Nothing to do — all segments already present.")
        return True

    # ── Per-key state with token-bucket rate limiter ─────────────────────
    class KeyState:
        def __init__(self, idx, key, rpm, max_concurrent):
            self.idx = idx
            self.key = key
            self.client = genai.Client(api_key=key)
            self.rpm = rpm
            self.max_concurrent = max_concurrent
            self.window = deque()
            self.in_flight = 0
            self.cooldown_until = 0.0
            self.lock = threading.Lock()
            self.success = 0
            self.fail_429 = 0
            self.fail_other = 0

        def available_in(self, now):
            with self.lock:
                if now < self.cooldown_until:
                    return self.cooldown_until - now
                if self.in_flight >= self.max_concurrent:
                    return 0.5
                cutoff = now - 60.0
                while self.window and self.window[0] < cutoff:
                    self.window.popleft()
                if len(self.window) < self.rpm:
                    return 0.0
                return max(0.0, 60.0 - (now - self.window[0]))

        def acquire(self, now):
            with self.lock:
                self.window.append(now)
                self.in_flight += 1

        def release(self, ok, is_429=False):
            with self.lock:
                self.in_flight = max(0, self.in_flight - 1)
                if ok:
                    self.success += 1
                elif is_429:
                    self.fail_429 += 1
                    self.cooldown_until = time.time() + CONFIG["key_cooldown"]
                else:
                    self.fail_other += 1

    keys = [KeyState(i, k, CONFIG["rpm_per_key"], CONFIG["max_concurrent_per_key"])
            for i, k in enumerate(API_KEYS)]
    keys_lock = threading.Lock()
    rr_idx = [0]   # strict round-robin pointer — advances by exactly 1 per acquire

    def pick_key(timeout=300.0):
        """
        Strict round-robin: starting from rr_idx, find the next key whose
        rate-limit/cooldown allows it. Advances rr_idx by exactly 1 so the
        next caller naturally moves to the following key.
        """
        start = time.time()
        backoff_idx = 0
        while True:
            now = time.time()
            best = None
            best_wait = float("inf")
            chosen_offset = -1
            with keys_lock:
                n = len(keys)
                # Scan starting at rr_idx, in order, taking the FIRST available key.
                for i in range(n):
                    ks = keys[(rr_idx[0] + i) % n]
                    w = ks.available_in(now)
                    if w == 0.0:
                        chosen_offset = i
                        break
                    if w < best_wait:
                        best_wait = w
                        best = ks
                if chosen_offset >= 0:
                    ks = keys[(rr_idx[0] + chosen_offset) % n]
                    # Advance pointer by exactly ONE past the key we just took.
                    rr_idx[0] = (rr_idx[0] + chosen_offset + 1) % n
                    ks.acquire(now)
                    return ks

            if time.time() - start > timeout:
                wait = CONFIG["backoff_sequence"][min(backoff_idx, len(CONFIG["backoff_sequence"])-1)]
                backoff_idx += 1
                print(f"  ⏳ All keys saturated — global wait {wait}s")
                time.sleep(wait)
                start = time.time()
                continue
            time.sleep(min(best_wait, 5.0))

    # ── Audio save ────────────────────────────────────────────────────────
    def save_audio(pcm_data, out_path: Path, fmt="mp3", sample_rate=24000):
        temp_wav = out_path.with_suffix(".temp.wav")
        with wave.open(str(temp_wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sample_rate)
            w.writeframes(pcm_data)
        if fmt == "mp3" and HAS_PYDUB:
            try:
                AudioSegment.from_wav(str(temp_wav)).export(
                    str(out_path.with_suffix(".mp3")), format="mp3")
                temp_wav.unlink()
                return out_path.with_suffix(".mp3")
            except Exception as e:
                print(f"  ⚠️ MP3 export failed, keeping WAV: {e}")
        final = out_path.with_suffix(".wav")
        if final.exists(): final.unlink()
        os.rename(temp_wav, final)
        return final

    # ── One TTS call with HARD timeout ────────────────────────────────────
    _io_executor = ThreadPoolExecutor(max_workers=max(CONFIG["max_workers"] * 2, 6),
                                       thread_name_prefix="tts-io")

    def _do_api_call(client, text, voice):
        return client.models.generate_content(
            model=model,
            contents=f"Read aloud in a clear, natural voice: {text}",
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice)))))

    def classify_error(err_str: str):
        """Return one of: 'quota', 'network', 'server', 'other'."""
        u = err_str.upper()
        if "429" in u or "RESOURCE_EXHAUSTED" in u or "QUOTA" in u:
            return "quota"
        if ("GETADDRINFO" in u or "11001" in u or "10060" in u or
            "TEMPORARY FAILURE" in u or "NAME RESOLUTION" in u or
            "CONNECTION RESET" in u or "CONNECTIONRESET" in u or
            "CONNECTION ABORTED" in u or "REMOTE END CLOSED" in u or
            "BROKENPIPE" in u or "NEWCONNECTIONERROR" in u or
            "MAXRETRYERROR" in u or "SSLERROR" in u):
            return "network"
        if "TIMEOUT" in u or "DEADLINE" in u:
            return "network"
        if "500" in u or "502" in u or "503" in u or "504" in u or "INTERNAL" in u or "UNAVAILABLE" in u:
            return "server"
        return "other"

    def generate_one(seg_id, text, out_path, voice, fmt, max_quota=None, max_net=None):
        max_quota = max_quota or CONFIG["max_quota_attempts"]
        max_net   = max_net   or CONFIG["max_network_attempts"]
        net_attempts   = 0
        quota_attempts = 0
        other_attempts = 0
        last_err = "?"

        while True:
            # Stop if we've blown either budget
            if quota_attempts >= max_quota and net_attempts >= max_net:
                return False, f"giving up — quota={quota_attempts} net={net_attempts} last: {last_err}"
            if other_attempts >= 4:
                return False, f"giving up — other-errors={other_attempts} last: {last_err}"

            ks = pick_key()
            t0 = time.time()
            try:
                fut = _io_executor.submit(_do_api_call, ks.client, text, voice)
                try:
                    resp = fut.result(timeout=CONFIG["request_timeout"])
                except FuturesTimeout:
                    fut.cancel()
                    last_err = f"timeout>{CONFIG['request_timeout']}s"
                    ks.release(ok=False, is_429=False)
                    net_attempts += 1
                    if net_attempts < max_net:
                        wait = CONFIG["network_backoff"][min(net_attempts-1, len(CONFIG["network_backoff"])-1)]
                        time.sleep(wait)
                    continue

                part = None
                if resp.candidates and resp.candidates[0].content.parts:
                    for p in resp.candidates[0].content.parts:
                        if p.inline_data:
                            part = p; break
                if not part:
                    last_err = "no audio returned (text-only response)"
                    ks.release(ok=False, is_429=False)
                    other_attempts += 1
                    time.sleep(1.0)
                    continue

                sr = 24000
                mt = part.inline_data.mime_type or ""
                if "rate=" in mt:
                    try: sr = int(mt.split("rate=")[1].split(";")[0])
                    except Exception: pass

                saved = save_audio(part.inline_data.data, out_path, fmt=fmt, sample_rate=sr)
                ks.release(ok=True)
                dt = time.time() - t0
                if CONFIG["base_delay"] > 0:
                    time.sleep(CONFIG["base_delay"])
                return True, f"{saved.name} via key#{ks.idx+1} in {dt:.1f}s"

            except Exception as e:
                err = str(e)
                cls = classify_error(err)
                last_err = f"[{cls}] {err[:140]}"

                if cls == "quota":
                    ks.release(ok=False, is_429=True)
                    quota_attempts += 1
                    time.sleep(0.5)
                elif cls == "network":
                    ks.release(ok=False, is_429=False)
                    net_attempts += 1
                    if net_attempts < max_net:
                        wait = CONFIG["network_backoff"][min(net_attempts-1, len(CONFIG["network_backoff"])-1)]
                        time.sleep(wait)
                elif cls == "server":
                    ks.release(ok=False, is_429=False)
                    net_attempts += 1
                    if net_attempts < max_net:
                        wait = CONFIG["network_backoff"][min(net_attempts-1, len(CONFIG["network_backoff"])-1)]
                        time.sleep(wait)
                else:
                    ks.release(ok=False, is_429=False)
                    other_attempts += 1
                    time.sleep(1.0 + other_attempts * 0.5)
                continue

    # ── Concurrent generation ─────────────────────────────────────────────
    max_workers = CONFIG.get("max_workers", 3)
    pbar      = tqdm(total=len(todo), desc="TTS", unit="seg") if HAS_TQDM else None
    pbar_lock = threading.Lock()
    failures  = {}   # seg_id -> (row, msg)

    print(f"[tts] Launching {max_workers} concurrent worker(s).")
    t_start = time.time()

    def run_batch(batch_rows, label="main"):
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"tts-{label}") as executor:
            future_to_row = {
                executor.submit(
                    generate_one,
                    r["ID"],
                    bodies[r["ID"]].strip(),
                    audio_dir / r["ID"],
                    voice,
                    fmt,
                ): r
                for r in batch_rows
            }
            for future in as_completed(future_to_row):
                r = future_to_row[future]
                try:
                    ok, msg = future.result()
                except Exception as exc:
                    ok, msg = False, str(exc)
                with pbar_lock:
                    if pbar: pbar.update(1)
                    if not ok:
                        failures[r["ID"]] = (r, msg)
                        print(f"\n  ❌ {r['ID']}: {msg}")
                    else:
                        # remove from failures if it was a retry
                        failures.pop(r["ID"], None)

    # Main pass
    run_batch(todo, label="main")

    # ── Final sweep — retry anything that failed but only if file is missing
    sweep_attempts = CONFIG.get("final_sweep_attempts", 2)
    for sweep_i in range(1, sweep_attempts + 1):
        missing = [r for r in todo if not already_present(r["ID"])]
        if not missing:
            break
        pause = CONFIG.get("final_sweep_pause", 30)
        print(f"\n[tts] Sweep {sweep_i}/{sweep_attempts}: {len(missing)} segments still missing. "
              f"Pausing {pause}s then retrying…")
        time.sleep(pause)
        # Reset bar for sweep so user can see progress
        if pbar:
            try:
                pbar.total = (pbar.total or 0) + len(missing)
                pbar.refresh()
            except Exception:
                pass
        run_batch(missing, label=f"sweep{sweep_i}")

    if pbar: pbar.close()
    _io_executor.shutdown(wait=False, cancel_futures=True)

    # Re-compute true status from disk
    true_missing = [r for r in todo if not already_present(r["ID"])]

    elapsed = time.time() - t_start
    done_ok = len(todo) - len(true_missing)
    rate    = (done_ok / elapsed * 60.0) if elapsed > 0 else 0.0
    print(f"\n[tts] done. {done_ok}/{len(todo)} ready in {elapsed/60:.1f} min "
          f"({rate:.1f} seg/min).")
    print("[tts] per-key stats:")
    for ks in keys:
        print(f"   key#{ks.idx+1:>2}: ok={ks.success}  429={ks.fail_429}  other={ks.fail_other}")

    if true_missing:
        print(f"[tts] {len(true_missing)} segments still missing after sweep:")
        for r in true_missing[:30]:
            _, msg = failures.get(r["ID"], (None, "unknown"))
            print(f"   - {r['ID']}: {msg}")
    return len(true_missing) == 0


# ─── ENTRY POINT ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--audio-dir", required=True)
    ap.add_argument("--voice", default=None)
    ap.add_argument("--format", default=None, choices=["mp3", "wav"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--start-at", help="Segment ID to start from (e.g. NAR-042)")
    ap.add_argument("--probe", action="store_true",
                    help="Fast status check; prints DATA| line and exits.")
    a = ap.parse_args()

    script_path = Path(a.script)
    audio_dir   = Path(a.audio_dir)
    fmt_for_probe = a.format or CONFIG["output_format"]

    if a.probe:
        ok = run_probe(script_path, audio_dir, fmt_for_probe)
        sys.exit(0 if ok else 1)

    ok = run_generate(script_path, audio_dir,
                      voice=a.voice, fmt=a.format,
                      force=a.force, start_at=a.start_at)
    sys.exit(0 if ok else 1)
