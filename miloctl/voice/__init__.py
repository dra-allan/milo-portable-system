"""
voice — Milo's voice stack (STT, streaming TTS, wake word, voice mode).

Vendored and adapted from Hermes Agent (Nous Research, MIT). Each module is
self-contained on the Python stdlib so Milo keeps its "installs on a fresh
machine with no wheels" promise; audio capture/playback and wake-word engines
are optional extras (sounddevice, faster-whisper, openwakeword, ...).
"""

from __future__ import annotations

from . import audio, stt, tts_streaming, wake
from .mode import VoiceSession, identity_verified, run_cli
from .stt import transcribe_audio
from .tts_streaming import (
    SentenceChunker,
    mark_speech_interrupted,
    resolve_streaming_provider,
    stream_tts_to_wav,
    take_speech_interrupted,
)

__all__ = [
    "audio",
    "stt",
    "tts_streaming",
    "wake",
    "VoiceSession",
    "SentenceChunker",
    "identity_verified",
    "mark_speech_interrupted",
    "resolve_streaming_provider",
    "run_cli",
    "stream_tts_to_wav",
    "take_speech_interrupted",
    "transcribe_audio",
]
