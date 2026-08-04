"""
voice/mode.py — voice mode: push-to-talk conversational loop.

Ties the vendored voice stack together: capture → STT → agent → streaming TTS →
playback, plus the optional wake-word trigger and the identity gate Allan asked
for (challenge-question verification before sensitive actions).

Flow (push-to-talk)::

    press Enter to start recording
    [mic open] ... say your line ... [Enter or silence to stop]
    STT → prompt → agent reply → streamed speech playback

Two flags worth knowing: ``--once`` records a single turn and exits (great for
headless/automation tests), and ``--identity`` prompts for the passphrase before
the first sensitive action in a session.

The agent reply is produced by the *harness* (OpenCode/Claude Code/Codex) via
``miloctl.harness`` — the same engine that powers normal chat. When no harness
is configured we fall back to a bare echo so the voice loop still works.
"""

from __future__ import annotations

import argparse
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..env import get as env_get
from . import audio, stt, tts_streaming
from .tts_streaming import SentenceChunker

logger = logging.getLogger(__name__)

#: Kept short so "charon" etc. is a convenient one-word alias.
IDENTITY_PASSPHRASE_ENV = "MILO_IDENTITY_PASSPHRASE"

#: Speech continued past this many seconds of silence counts as the end of a turn.
_TURN_END_SILENCE_S = 1.5

DEFAULT_REPLY_BEGIN = (
    "Hello. This is Milo. I'm listening. Press enter to talk, press Ctrl+C to stop."
)


# ---------------------------------------------------------------------------
# Identity gate
# ---------------------------------------------------------------------------

def identity_verified(phrase: Optional[str] = None) -> bool:
    """Verify Allan's identity via the passphrase (env: MILO_IDENTITY_PASSPHRASE).

    Returns True when a passphrase is set *and* the user supplies it. When no
    passphrase is configured, the gate is open (nothing to check) — this is a
    configurable deterrent, not a hard lock.
    """
    expected = env_get(IDENTITY_PASSPHRASE_ENV).strip()
    if not expected:
        return True
    if phrase is None:
        phrase = input("Identity check: what is the passphrase? ").strip()
    return _constant_time_eq(phrase, expected)


def _constant_time_eq(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a.encode("utf-8"), b.encode("utf-8")):
        result |= x ^ y
    return result == 0


# ---------------------------------------------------------------------------
# The conversational loop
# ---------------------------------------------------------------------------

class VoiceSession:
    """One voice conversation: mic → STT → harness → TTS → speaker."""

    def __init__(
        self,
        *,
        harness: Optional[Callable[[str], str]] = None,
        harness_name: str = "",
        stt_provider: Optional[str] = None,
        tts_provider: Optional[str] = None,
        wake_engine: Optional[str] = None,
        require_identity: bool = False,
        record_kwargs: Optional[dict] = None,
    ):
        self.harness = harness or self._echo_harness
        self.harness_name = harness_name
        self.stt_provider = stt_provider
        self.tts_provider = tts_provider
        self.wake_engine = wake_engine
        self.require_identity = require_identity
        self.record_kwargs = dict(record_kwargs or {})
        self._chunker = SentenceChunker()

    # -- wiring -------------------------------------------------------------

    @staticmethod
    def _echo_harness(prompt: str) -> str:
        return (
            f"You said: {prompt}\n\n"
            "(Milo is running without a configured harness — install one via "
            "`milo setup`, or set MILO_PROVIDER/MILO_MODEL, to get real answers.)"
        )

    def _make_harness(self) -> Callable[[str], str]:
        """Build a real harness (OpenCode/Claude/Codex) from miloctl.harness."""
        try:
            from .. import harness as harness_mod

            if getattr(self, "harness_name", ""):
                h = harness_mod.get_harness(self.harness_name)
            else:
                h = None
                for cand in harness_mod.detect_installed():
                    if cand.which():
                        h = cand
                        break
            if h is None:
                return self._echo_harness
            return lambda prompt: h.run(prompt)[1]
        except Exception as exc:  # pragma: no cover - fall back to echo
            logger.debug("harness unavailable: %s", exc)
            return self._echo_harness

    # -- turn plumbing ------------------------------------------------------

    def _capture_turn(self) -> Optional[str]:
        """Record one utterance to a temp WAV and transcribe it."""
        fd, tmp = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            rec_kwargs = dict(self.record_kwargs)
            rec_kwargs.pop("duration", None)  # duration comes from _turn_seconds()
            audio.record(self._turn_seconds(), tmp, **rec_kwargs)
            result = stt.transcribe_audio(tmp, provider=self.stt_provider)
            if result.get("status") != "success":
                print(f"[stt] {result.get('message', 'transcription failed')}")
                return None
            return result.get("transcript") or ""
        finally:
            try:
                Path(tmp).unlink(missing_ok=True)
            except OSError:
                pass

    def _turn_seconds(self) -> float:
        """How long to record. Push-to-talk uses Enter; --once uses 6s default."""
        return float(self.record_kwargs.get("duration", 6.0))

    def _speak(self, text: str) -> None:
        """Stream *text* to audio, playing sentences as they finish."""
        if not text.strip():
            return
        if not audio.has_sounddevice() and not audio.has_ffmpeg():
            print(f"\n[tts disabled — no audio backend] {text}")
            return
        tts_cfg = {
            "streaming": {"provider": self.tts_provider or "auto"},
            self.tts_provider: {},
        } if self.tts_provider else {"streaming": {"provider": "auto"}}
        try:
            streamer = tts_streaming.resolve_streaming_provider(tts_cfg, preferred=self.tts_provider)
            if streamer is None:
                print("[tts] no usable provider. Set GEMINI_API_KEY / OPENAI_API_KEY.")
                return
            fd, tmp = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                frames = bytearray()
                for chunk in streamer.stream(text):
                    frames += chunk
                import wave as _w

                with _w.open(tmp, "wb") as wf:
                    wf.setnchannels(streamer.channels)
                    wf.setsampwidth(streamer.sample_width)
                    wf.setframerate(streamer.sample_rate)
                    wf.writeframes(bytes(frames))
                audio.play(tmp)
            finally:
                try:
                    Path(tmp).unlink(missing_ok=True)
                except OSError:
                    pass
        except Exception as exc:  # pragma: no cover - playback failures
            logger.warning("TTS playback failed: %s", exc)
            print(f"[tts] {exc}")

    # -- main loop ----------------------------------------------------------

    def run(self, *, once: bool = False, silent_wake: bool = False) -> int:
        """Run the conversation loop. ``once`` = single turn, then exit."""
        harness = self._make_harness()
        verified = not self.require_identity

        print(DEFAULT_REPLY_BEGIN if not once else "")
        try:
            while True:
                if not once:
                    try:
                        input("(press Enter to talk) ")
                    except EOFError:
                        break
                if self.require_identity and not verified:
                    if not identity_verified():
                        print("Identity check failed. Try again.")
                        if once:
                            return 1
                        continue
                    verified = True

                turn = self._capture_turn()
                if not turn or not turn.strip():
                    print("[no speech detected]")
                    if once:
                        break
                    continue

                print(f"[you] {turn}")
                reply = harness(turn)
                print(f"[milo] {reply}")
                self._speak(reply)

                if once:
                    break
        except KeyboardInterrupt:
            print("\n[voice mode stopped]")
        return 0


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def run_cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="milo voice",
        description="Talk to Milo: mic → STT → agent → spoken reply.",
    )
    parser.add_argument("--once", action="store_true", help="single turn, then exit")
    parser.add_argument("--duration", type=float, default=6.0, help="recording seconds for --once")
    parser.add_argument("--stt", help="STT provider (local|openai|groq|xai)")
    parser.add_argument("--tts", help="TTS provider (gemini|openai|elevenlabs)")
    parser.add_argument("--identity", action="store_true", help="require passphrase check")
    parser.add_argument("--wake", help="wake-word engine (openwakeword|sherpa|porcupine)")
    parser.add_argument("--harness", default="",
                        help="agent harness (opencode|claude-code|codex|cursor|gemini)")
    parser.add_argument("--test-tts", metavar="TEXT", help="synthesize TEXT to a file and exit")
    parser.add_argument("--tts-out", default="", help="output path for --test-tts")

    args = parser.parse_args(argv)

    if args.test_tts:
        out = args.tts_out or os.path.join(tempfile.gettempdir(), "milo_tts_test.wav")
        try:
            result = tts_streaming.stream_tts_to_wav(args.test_tts, out, provider=args.tts)
            print(json_dumps(result))
            return 0
        except Exception as exc:
            print(f"[tts] {exc}")
            return 1

    audio_status = audio.audio_available()
    if not audio_status["capture"]:
        print("No audio capture backend found. " + audio.install_hint())
        return 1

    session = VoiceSession(
        harness=None,
        harness_name=args.harness,
        stt_provider=args.stt,
        tts_provider=args.tts,
        wake_engine=args.wake,
        require_identity=args.identity,
        record_kwargs={"duration": args.duration},
    )
    return session.run(once=args.once)


def json_dumps(data: Dict[str, Any]) -> str:
    import json

    return json.dumps(data, indent=2, default=str)
