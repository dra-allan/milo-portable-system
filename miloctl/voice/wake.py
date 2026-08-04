"""
voice/wake.py — wake-word ("Hey Milo") detection.

Vendored and adapted from Hermes Agent (Nous Research, MIT) —
``tools/wake_word.py``. Lightweight always-on hotword listener that fires a
callback when the wake phrase is spoken.

Engines, all fully on-device (no audio leaves the machine for detection):

* ``openwakeword`` (default, free, no API key) — loads an ONNX model. Supports
  built-in model names (``hey_jarvis``, ``alexa``, ...) or a custom ``.onnx``
  you train for your own phrase. Default phrase: ``hey_milo``; if that model
  isn't bundled, falls back to ``hey_jarvis`` (closest openWakeWord ships with).
* ``sherpa`` (free, open vocabulary) — sherpa-onnx keyword spotting for ANY
  typed phrase, no training. Needs the (small) streaming model download.
* ``porcupine`` (premium) — Picovoice engine; needs ``PORCUPINE_ACCESS_KEY``.

The detector runs on its own thread; callers ``pause()`` it while a voice turn
holds the microphone and ``resume()`` when idle (two input streams on one
device is unreliable cross-platform).

MIT — original copyright Nous Research. See ATTRIBUTION in the package README.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000  # mono int16 — what both engines expect.

# Minimum gap between two consecutive wake fires.
_FIRE_COOLDOWN_SECONDS = 2.0

# Require N-in-a-row frames above threshold before firing — the main lever
# against stray phonemes in ambient conversation.
_DEFAULT_CONFIRMATION_FRAMES = 3


class WakeWordInUse(RuntimeError):
    """Raised when a second detector tries to own the same device."""


def _has_module(name: str) -> bool:
    try:
        import importlib.util  # noqa: F401

        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def detect_engine(preferred: Optional[str] = None) -> str:
    """Return the usable engine for the requested (or configured) engine."""
    wanted = (preferred or os.getenv("MILO_WAKE_ENGINE", "").strip() or "openwakeword").lower()
    if wanted == "openwakeword" and _has_module("openwakeword"):
        return "openwakeword"
    if wanted == "sherpa" and _has_module("sherpa_onnx"):
        return "sherpa"
    if wanted == "porcupine" and _has_module("pvporcupine"):
        return "porcupine"
    # Fallback chain.
    for candidate in ("openwakeword", "sherpa", "porcupine"):
        if candidate == "openwakeword" and _has_module("openwakeword"):
            return candidate
        if candidate == "sherpa" and _has_module("sherpa_onnx"):
            return candidate
        if candidate == "porcupine" and _has_module("pvporcupine"):
            return candidate
    return ""


def install_hint(engine: str = "") -> str:
    hints = {
        "openwakeword": "pip install openwakeword",
        "sherpa": "pip install sherpa-onnx",
        "porcupine": "pip install pvporcupine   # needs PORCUPINE_ACCESS_KEY",
    }
    return hints.get(engine) or (
        "pip install openwakeword   # or: sherpa-onnx (any phrase), pvporcupine (premium)"
    )


class WakeWordDetector:
    """Tiny wrapper around one on-device wake-word engine.

    Usage::

        det = WakeWordDetector("hey milo", on_wake=lambda: print("woke!"))
        det.start()
        ...
        det.pause()    # while a voice turn owns the mic
        det.resume()
        det.stop()
    """

    def __init__(
        self,
        phrase: str = "hey milo",
        *,
        engine: Optional[str] = None,
        on_wake: Optional[Callable[[], None]] = None,
        sample_rate: int = SAMPLE_RATE,
        device: Optional[int] = None,
    ):
        self.phrase = phrase
        self.engine = detect_engine(engine) or (engine or "openwakeword")
        if not self.engine:
            raise RuntimeError("No wake-word engine installed. " + install_hint(engine))
        self.on_wake = on_wake
        self.sample_rate = sample_rate
        self.device = device
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._paused.set()
        self._last_fire = 0.0
        self._impl: Any = None

    # -- engine construction ------------------------------------------------

    def _build_openwakeword(self):
        import numpy as np  # openwakeword requires numpy anyway
        import openwakeword.model as _oww_model

        # Prefer a bundled "hey_milo" model; else use the built-in jarvis.
        bundled = Path(__file__).parent / "models" / "hey_milo.onnx"
        if bundled.exists():
            try:
                return {
                    "model": _oww_model.WakeWordModel(wakeword_model_path=str(bundled)),
                    "np": np,
                }
            except Exception as exc:  # pragma: no cover - fallback is fine
                logger.debug("bundled hey_milo model unusable: %s", exc)
        return {"model": _oww_model.WakeWordModel(wakeword_models=["hey_jarvis"]), "np": np}

    def _build_impl(self) -> Any:
        import sounddevice as sd  # optional audio lib

        if self.engine == "openwakeword":
            return self._build_openwakeword()
        if self.engine == "sherpa":
            import sherpa_onnx

            kwargs = {
                "keywords": {self.phrase: 0.6},
                "model": os.getenv("MILO_SHERTA_KWS_MODEL", ""),
                "tokens": os.getenv("MILO_SHERTA_KWS_TOKENS", ""),
            }
            if not kwargs["model"] or not kwargs["tokens"]:
                raise RuntimeError(
                    "sherpa needs MILO_SHERTA_KWS_MODEL and MILO_SHERTA_KWS_TOKENS env vars"
                )
            recognizer = sherpa_onnx.KeywordSpotter(**kwargs)
            return {"recognizer": recognizer, "sd": sd}
        if self.engine == "porcupine":
            import pvporcupine

            access_key = os.getenv("PORCUPINE_ACCESS_KEY", "")
            if not access_key:
                raise RuntimeError("porcupine needs PORCUPINE_ACCESS_KEY env var")
            porcupine = pvporcupine.create(access_key=access_key)
            return {"porcupine": porcupine, "sd": sd}
        raise RuntimeError(f"Unknown wake engine: {self.engine}")

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._impl = self._build_impl()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="milo-wake")
        self._thread.start()

    def pause(self) -> None:
        self._paused.clear()

    def resume(self) -> None:
        self._paused.set()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._thread = None

    # -- engine loop --------------------------------------------------------

    def _run(self) -> None:
        import sounddevice as sd

        engine = self.engine
        try:
            if engine == "openwakeword":
                self._run_openwakeword(sd)
            elif engine == "sherpa":
                self._run_sherpa(sd)
            elif engine == "porcupine":
                self._run_porcupine(sd)
        except Exception as exc:  # pragma: no cover - engine-agnostic
            logger.error("wake detector crashed: %s", exc)

    def _maybe_fire(self) -> None:
        now = time.monotonic()
        if now - self._last_fire < _FIRE_COOLDOWN_SECONDS:
            return
        self._last_fire = now
        if self.on_wake:
            try:
                self.on_wake()
            except Exception as exc:  # pragma: no cover - callback safety
                logger.warning("on_wake callback failed: %s", exc)

    def _confirmation_frames(self) -> int:
        try:
            return int(os.getenv("MILO_WAKE_CONFIRM_FRAMES", str(_DEFAULT_CONFIRMATION_FRAMES)))
        except ValueError:
            return _DEFAULT_CONFIRMATION_FRAMES

    def _run_openwakeword(self, sd) -> None:
        np = self._impl["np"]
        model = self._impl["model"]
        need = _DEFAULT_CONFIRMATION_FRAMES
        hits = 0
        with sd.InputStream(
            samplerate=self.sample_rate, device=self.device, channels=1, dtype="int16"
        ) as stream:
            while not self._stop.is_set():
                self._paused.wait()
                frames, overflowed = stream.read(1024)
                if overflowed:
                    logger.warning("wake: input overflow")
                scores = model.predict(np.frombuffer(frames, dtype=np.int16).astype(np.float32))
                best = max(scores.values()) if scores else 0.0
                if best >= 0.5:
                    hits += 1
                else:
                    hits = 0
                if hits >= need:
                    self._maybe_fire()
                    hits = 0

    def _run_sherpa(self, sd) -> None:
        recognizer = self._impl["recognizer"]
        sample_rate = recognizer.sample_rate or self.sample_rate
        with sd.InputStream(
            samplerate=sample_rate, device=self.device, channels=1, dtype="int16"
        ) as stream:
            while not self._stop.is_set():
                self._paused.wait()
                frames, _overflowed = stream.read(640)
                result = recognizer.accept_waveform(frames.tobytes())
                if result:
                    self._maybe_fire()

    def _run_porcupine(self, sd) -> None:
        porcupine = self._impl["porcupine"]
        with sd.InputStream(
            samplerate=porcupine.sample_rate,
            device=self.device,
            channels=1,
            dtype="int16",
        ) as stream:
            while not self._stop.is_set():
                self._paused.wait()
                pcm, _overflowed = stream.read(porcupine.frame_length)
                if porcupine.process(pcm.tobytes()) >= 0:
                    self._maybe_fire()
