"""
voice/audio.py — audio capture and playback (optional deps, graceful fallbacks).

Keeps Milo's "no wheels required" promise: sounddevice is used when installed,
otherwise on Windows we fall back to ``ffmpeg`` (found on PATH or via
``MILO_FFMPEG``) for both recording and playback. Everything here degrades
gracefully with a clear install hint.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Optional

from ..env import get as env_get

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000  # Whisper-native capture rate.


def has_sounddevice() -> bool:
    try:
        import importlib.util  # noqa: F401

        return importlib.util.find_spec("sounddevice") is not None
    except Exception:
        return False


def has_ffmpeg() -> bool:
    if env_get("MILO_FFMPEG"):
        return Path(env_get("MILO_FFMPEG")).exists()
    return shutil.which("ffmpeg") is not None


def ffmpeg_path() -> Optional[str]:
    env_path = env_get("MILO_FFMPEG").strip()
    if env_path and Path(env_path).exists():
        return env_path
    found = shutil.which("ffmpeg")
    return found


def install_hint() -> str:
    return "pip install sounddevice numpy   (or install ffmpeg and set MILO_FFMPEG)"


def audio_available() -> dict:
    """Report what capture/playback backends exist on this machine."""
    return {
        "sounddevice": has_sounddevice(),
        "ffmpeg": has_ffmpeg(),
        "ffmpeg_path": ffmpeg_path() or "",
        "capture": has_sounddevice() or has_ffmpeg(),
        "playback": has_sounddevice() or has_ffmpeg(),
    }


def _run_ffmpeg(args: list) -> bytes:
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found. " + install_hint())
    proc = subprocess.run([exe, *args], capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed: {proc.stderr.decode('utf-8', 'replace')[-400:]}"
        )
    return proc.stdout


def record_ffmpeg(duration: float, out_wav: str, *, sample_rate: int = SAMPLE_RATE) -> str:
    """Record *duration* seconds from the default microphone via ffmpeg."""
    args = [
        "-y",
        "-f", "dshow",
        "-i", "audio=" + _windows_mic_name(),
        "-t", f"{duration:.2f}",
        "-ar", str(sample_rate),
        "-ac", "1",
        "-c:a", "pcm_s16le",
        out_wav,
    ]
    _run_ffmpeg(args)
    return out_wav


def _windows_mic_name() -> str:
    name = os.getenv("MILO_MIC_NAME", "").strip()
    if name:
        return name
    # Probe the default input device via ffmpeg; dshow enumerates on first call.
    return "default"


def play_ffmpeg(wav_path: str) -> None:
    """Play a WAV via ffmpeg (writes to default output device)."""
    args = ["-hide_banner", "-loglevel", "error", "-i", wav_path, "-f", "dshow"]
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found. " + install_hint())
    subprocess.run(
        [exe, *args, "-"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600,
    )


# ---------------------------------------------------------------------------
# sounddevice path (optional, preferred)
# ---------------------------------------------------------------------------

def _import_sounddevice():
    import numpy as np
    import sounddevice as sd

    return sd, np


def record_sounddevice(duration: float, out_wav: str, *, sample_rate: int = SAMPLE_RATE) -> str:
    sd, np = _import_sounddevice()
    data = sd.rec(int(duration * sample_rate), samplerate=sample_rate, channels=1, dtype="int16")
    sd.wait()
    with wave.open(out_wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(data.tobytes())
    return out_wav


def play_sounddevice(wav_path: str) -> None:
    sd, np = _import_sounddevice()
    with wave.open(wav_path, "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
        rate = wf.getframerate()
    sd.play(np.frombuffer(pcm, dtype=np.int16), rate)
    sd.wait()


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def record(duration: float, out_wav: str, *, sample_rate: int = SAMPLE_RATE) -> str:
    """Record *duration* seconds of mono audio from the microphone."""
    if has_sounddevice():
        return record_sounddevice(duration, out_wav, sample_rate=sample_rate)
    if has_ffmpeg():
        return record_ffmpeg(duration, out_wav, sample_rate=sample_rate)
    raise RuntimeError("No audio capture backend. " + install_hint())


def play(wav_path: str) -> None:
    """Play a WAV file through the default output device."""
    if has_sounddevice():
        play_sounddevice(wav_path)
    elif has_ffmpeg():
        play_ffmpeg(wav_path)
    else:
        raise RuntimeError("No audio playback backend. " + install_hint())
