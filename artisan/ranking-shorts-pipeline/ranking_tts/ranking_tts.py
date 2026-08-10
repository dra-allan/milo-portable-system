#!/usr/bin/env python3
"""Ranking-pipeline TTS - a fork of ``artisan/gemini_tts_pipeline/gemini_tts.py``.

This is a copy, not an import. The original is in production for long-form
narration and its behaviour must not shift because this pipeline needed
something different.

What was kept, because it is the hard-won part:
  * strict round-robin key rotation with a per-key token bucket, so no key is
    used twice in a row while another is free;
  * error *classification* - a DNS blip and a 429 need different backoff, and
    treating them the same is what used to kill segments in three seconds;
  * the end-of-run sweep, which re-attempts anything still missing on disk.

What changed:
  * **Input.** The original parses a segment manifest for a long-form script.
    Here the unit is one short line per rank, so the input is a flat JSON list
    ``[{"id": "R5", "text": "That fish has brain damage now."}, ...]``.
    A manifest for five one-liners is pure ceremony.
  * **Delivery.** The read prompt is a configurable ``style_prompt``: these
    lines are comedic punchlines over a clip, not narration, and the original's
    "clear, natural voice" instruction flattens them.
  * **Output naming.** One file per rank ID in a flat directory, which is what
    the assembler maps as a per-clip input.

Usage:
    python -m ranking_tts.ranking_tts --lines lines.json --out-dir data/vo/xyz
    python -m ranking_tts.ranking_tts --lines lines.json --out-dir ... --probe
"""

import json
import os
import sys
import threading
import time
import wave
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CONFIG = {
    'default_voice': 'Puck',
    'output_format': 'mp3',
    'model': 'gemini-2.5-flash-preview-tts',
    'style_prompt': ('Read this as a punchy, deadpan short-form video '
                     'narrator. Casual, confident, slightly amused. Do not '
                     'add words:'),
    'max_workers': 3,
    'rpm_per_key': 3,
    'max_concurrent_per_key': 1,
    'request_timeout': 90,
    'backoff_sequence': [15, 30, 60, 120],
    'key_cooldown': 35,
    'max_quota_attempts': 10,
    'max_network_attempts': 8,
    'network_backoff': [2, 4, 8, 15, 30, 45, 60, 90],
    'final_sweep_pause': 30,
    'final_sweep_attempts': 2,
    'base_delay': 0.0,
}

_CONFIG_PATH = Path(__file__).with_name('config.json')
if _CONFIG_PATH.exists():
    try:
        CONFIG.update(json.loads(_CONFIG_PATH.read_text(encoding='utf-8')))
    except Exception as exc:  # noqa: BLE001
        print(f'[warn] config.json unreadable: {exc}', file=sys.stderr)


# ---------------------------------------------------------------------------
# Lines
# ---------------------------------------------------------------------------
def load_lines(path: Path) -> List[Dict[str, str]]:
    """Read the lines file. Accepts a list or ``{"lines": [...]}``."""
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if isinstance(data, dict):
        data = data.get('lines') or []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        line_id = str(item.get('id') or '').strip()
        text = str(item.get('text') or '').strip()
        if line_id and text:
            out.append({'id': line_id, 'text': text,
                        'voice': item.get('voice') or ''})
    return out


def existing_file(out_dir: Path, line_id: str) -> Optional[Path]:
    for ext in ('mp3', 'wav'):
        candidate = out_dir / f'{line_id}.{ext}'
        # A truncated download is worse than a missing one: it renders as a
        # click and the clip ships broken. Anything under 1 KB is not audio.
        if candidate.exists() and candidate.stat().st_size > 1024:
            return candidate
    return None


def classify_error(err: str) -> str:
    upper = err.upper()
    if '429' in upper or 'RESOURCE_EXHAUSTED' in upper or 'QUOTA' in upper:
        return 'quota'
    network_markers = (
        'GETADDRINFO', '11001', '10060', 'TEMPORARY FAILURE',
        'NAME RESOLUTION', 'CONNECTION RESET', 'CONNECTIONRESET',
        'CONNECTION ABORTED', 'REMOTE END CLOSED', 'BROKENPIPE',
        'NEWCONNECTIONERROR', 'MAXRETRYERROR', 'SSLERROR', 'TIMEOUT',
        'DEADLINE',
    )
    if any(marker in upper for marker in network_markers):
        return 'network'
    if any(code in upper for code in ('500', '502', '503', '504')) \
            or 'INTERNAL' in upper or 'UNAVAILABLE' in upper:
        return 'server'
    return 'other'


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
def generate(lines_path: Path, out_dir: Path, voice: Optional[str] = None,
             fmt: Optional[str] = None, force: bool = False) -> bool:
    import warnings
    from concurrent.futures import (ThreadPoolExecutor, as_completed,
                                    TimeoutError as FuturesTimeout)
    warnings.filterwarnings('ignore', category=RuntimeWarning, module='pydub')

    from google import genai
    from google.genai import types

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    try:
        from pydub import AudioSegment
        has_pydub = True
    except ImportError:
        has_pydub = False

    keys_raw = os.getenv('GEMINI_API_KEYS') or os.getenv('GEMINI_API_KEY')
    if not keys_raw:
        print('[error] GEMINI_API_KEY(S) not set', file=sys.stderr)
        return False
    api_keys = [k.strip() for k in keys_raw.split(',') if k.strip()]

    voice = voice or CONFIG['default_voice']
    fmt = fmt or CONFIG['output_format']
    model = CONFIG['model']

    lines = load_lines(lines_path)
    if not lines:
        print('[error] no usable lines in %s' % lines_path, file=sys.stderr)
        return False

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    todo = [ln for ln in lines
            if force or not existing_file(out_dir, ln['id'])]
    print(f'[tts] voice={voice} fmt={fmt} keys={len(api_keys)} '
          f'lines={len(lines)} to-generate={len(todo)}')
    if not todo:
        print('[tts] nothing to do')
        return True

    class KeyState:
        def __init__(self, index: int, key: str):
            self.index = index
            self.client = genai.Client(api_key=key)
            self.rpm = CONFIG['rpm_per_key']
            self.max_concurrent = CONFIG['max_concurrent_per_key']
            self.window: deque = deque()
            self.in_flight = 0
            self.cooldown_until = 0.0
            self.lock = threading.Lock()
            self.ok = 0
            self.quota = 0
            self.other = 0

        def available_in(self, now: float) -> float:
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

        def acquire(self, now: float) -> None:
            with self.lock:
                self.window.append(now)
                self.in_flight += 1

        def release(self, ok: bool, is_quota: bool = False) -> None:
            with self.lock:
                self.in_flight = max(0, self.in_flight - 1)
                if ok:
                    self.ok += 1
                elif is_quota:
                    self.quota += 1
                    self.cooldown_until = time.time() + CONFIG['key_cooldown']
                else:
                    self.other += 1

    keys = [KeyState(i, k) for i, k in enumerate(api_keys)]
    keys_lock = threading.Lock()
    pointer = [0]

    def pick_key(timeout: float = 300.0) -> 'KeyState':
        """Strict round-robin: take the first available key from the pointer
        and advance by exactly one, so load spreads instead of hammering key 1.
        """
        start = time.time()
        backoff = 0
        while True:
            now = time.time()
            best_wait = float('inf')
            chosen = -1
            with keys_lock:
                count = len(keys)
                for offset in range(count):
                    state = keys[(pointer[0] + offset) % count]
                    wait = state.available_in(now)
                    if wait == 0.0:
                        chosen = offset
                        break
                    best_wait = min(best_wait, wait)
                if chosen >= 0:
                    state = keys[(pointer[0] + chosen) % count]
                    pointer[0] = (pointer[0] + chosen + 1) % count
                    state.acquire(now)
                    return state
            if time.time() - start > timeout:
                ladder = CONFIG['backoff_sequence']
                wait = ladder[min(backoff, len(ladder) - 1)]
                backoff += 1
                print(f'  all keys saturated - waiting {wait}s')
                time.sleep(wait)
                start = time.time()
                continue
            time.sleep(min(best_wait, 5.0))

    def save_audio(pcm: bytes, base: Path, sample_rate: int) -> Path:
        temp_wav = base.with_suffix('.temp.wav')
        with wave.open(str(temp_wav), 'wb') as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(pcm)
        if fmt == 'mp3' and has_pydub:
            try:
                target = base.with_suffix('.mp3')
                AudioSegment.from_wav(str(temp_wav)).export(str(target),
                                                            format='mp3')
                temp_wav.unlink()
                return target
            except Exception as exc:  # noqa: BLE001
                print(f'  mp3 export failed, keeping wav: {exc}')
        final = base.with_suffix('.wav')
        if final.exists():
            final.unlink()
        os.rename(temp_wav, final)
        return final

    io_pool = ThreadPoolExecutor(
        max_workers=max(CONFIG['max_workers'] * 2, 6),
        thread_name_prefix='tts-io')

    def call_api(client, text: str, line_voice: str):
        prompt = f"{CONFIG['style_prompt']} {text}"
        return client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=['AUDIO'],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=line_voice)))))

    def generate_one(line: Dict[str, str]) -> Tuple[bool, str]:
        line_voice = line.get('voice') or voice
        base = out_dir / line['id']
        quota_attempts = net_attempts = other_attempts = 0
        last_error = '?'
        while True:
            if (quota_attempts >= CONFIG['max_quota_attempts']
                    and net_attempts >= CONFIG['max_network_attempts']):
                return False, f'gave up: {last_error}'
            if other_attempts >= 4:
                return False, f'gave up after {other_attempts} errors: ' \
                              f'{last_error}'
            state = pick_key()
            try:
                future = io_pool.submit(call_api, state.client, line['text'],
                                        line_voice)
                try:
                    response = future.result(
                        timeout=CONFIG['request_timeout'])
                except FuturesTimeout:
                    future.cancel()
                    state.release(ok=False)
                    net_attempts += 1
                    last_error = f"timeout>{CONFIG['request_timeout']}s"
                    ladder = CONFIG['network_backoff']
                    time.sleep(ladder[min(net_attempts - 1,
                                          len(ladder) - 1)])
                    continue

                part = None
                if response.candidates and response.candidates[0].content.parts:
                    for candidate in response.candidates[0].content.parts:
                        if candidate.inline_data:
                            part = candidate
                            break
                if not part:
                    state.release(ok=False)
                    other_attempts += 1
                    last_error = 'text-only response, no audio'
                    time.sleep(1.0)
                    continue

                sample_rate = 24000
                mime = part.inline_data.mime_type or ''
                if 'rate=' in mime:
                    try:
                        sample_rate = int(mime.split('rate=')[1].split(';')[0])
                    except (IndexError, ValueError):
                        pass
                saved = save_audio(part.inline_data.data, base, sample_rate)
                state.release(ok=True)
                if CONFIG['base_delay'] > 0:
                    time.sleep(CONFIG['base_delay'])
                return True, saved.name
            except Exception as exc:  # noqa: BLE001
                kind = classify_error(str(exc))
                last_error = f'[{kind}] {str(exc)[:140]}'
                if kind == 'quota':
                    state.release(ok=False, is_quota=True)
                    quota_attempts += 1
                    time.sleep(0.5)
                elif kind in ('network', 'server'):
                    state.release(ok=False)
                    net_attempts += 1
                    ladder = CONFIG['network_backoff']
                    time.sleep(ladder[min(net_attempts - 1, len(ladder) - 1)])
                else:
                    state.release(ok=False)
                    other_attempts += 1
                    time.sleep(1.0 + other_attempts * 0.5)

    failures: Dict[str, str] = {}

    def run_batch(batch: List[Dict[str, str]], label: str) -> None:
        with ThreadPoolExecutor(max_workers=CONFIG['max_workers'],
                               thread_name_prefix=f'tts-{label}') as pool:
            futures = {pool.submit(generate_one, ln): ln for ln in batch}
            for future in as_completed(futures):
                line = futures[future]
                try:
                    ok, message = future.result()
                except Exception as exc:  # noqa: BLE001
                    ok, message = False, str(exc)
                if ok:
                    failures.pop(line['id'], None)
                    print(f"  ok {line['id']}: {message}")
                else:
                    failures[line['id']] = message
                    print(f"  FAIL {line['id']}: {message}")

    started = time.time()
    run_batch(todo, 'main')

    # Sweep: one more pass on whatever is still missing on disk. Transient
    # quota exhaustion is the usual cause and it clears on its own.
    for attempt in range(1, CONFIG['final_sweep_attempts'] + 1):
        missing = [ln for ln in todo if not existing_file(out_dir, ln['id'])]
        if not missing:
            break
        pause = CONFIG['final_sweep_pause']
        print(f'[tts] sweep {attempt}: {len(missing)} missing, pausing '
              f'{pause}s')
        time.sleep(pause)
        run_batch(missing, f'sweep{attempt}')

    io_pool.shutdown(wait=False, cancel_futures=True)

    missing = [ln for ln in todo if not existing_file(out_dir, ln['id'])]
    print(f'[tts] {len(todo) - len(missing)}/{len(todo)} generated in '
          f'{(time.time() - started):.0f}s')
    for state in keys:
        print(f'   key#{state.index + 1}: ok={state.ok} '
              f'quota={state.quota} other={state.other}')
    for line in missing:
        print(f"   missing {line['id']}: "
              f"{failures.get(line['id'], 'unknown')}")
    return not missing


def probe(lines_path: Path, out_dir: Path) -> bool:
    """Print a machine-readable status line and exit. Mirrors the original's
    ``--probe`` so a launcher can poll progress without parsing the log."""
    try:
        lines = load_lines(lines_path)
        done = [ln['id'] for ln in lines if existing_file(Path(out_dir), ln['id'])]
        pending = [ln['id'] for ln in lines if ln['id'] not in done]
        nxt = pending[0] if pending else 'DONE'
        print(f'DATA|{len(done)}|{len(lines)}|{nxt}')
        return True
    except Exception as exc:  # noqa: BLE001
        print('DATA|0|0|PROBE_ERROR')
        print(f'[probe] {exc}', file=sys.stderr)
        return False


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description='Ranking pipeline TTS')
    parser.add_argument('--lines', required=True,
                        help='JSON file of {id, text} voice-over lines')
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--voice', default=None)
    parser.add_argument('--format', default=None, choices=['mp3', 'wav'])
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--probe', action='store_true')
    args = parser.parse_args(argv)

    if args.probe:
        return 0 if probe(Path(args.lines), Path(args.out_dir)) else 1
    ok = generate(Path(args.lines), Path(args.out_dir), voice=args.voice,
                  fmt=args.format, force=args.force)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
