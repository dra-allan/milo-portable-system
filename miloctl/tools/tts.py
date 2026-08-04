"""
Text-to-Speech tool — synthesizes speech via the vendored streaming TTS stack.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..voice.tts_streaming import stream_tts_to_wav
from .base import Tool


class TextToSpeechTool(Tool):
    """Convert text to speech audio using the vendored streaming TTS engine."""

    name = "tts"
    description = "Convert text to speech audio using the vendored streaming TTS engine."

    def run(self, text: str, output_path: str = "", voice: str = "", provider: str = "") -> dict[str, Any]:
        """Convert text to speech.

        Args:
            text: Text to convert to speech
            output_path: Path to save the WAV file (optional)
            voice: Voice name (provider-dependent; e.g. Charon for Gemini)
            provider: TTS provider (gemini|openai|elevenlabs), auto by default

        Returns:
            Dictionary with conversion status and file information
        """
        if not text or not text.strip():
            return {"status": "error", "message": "text is empty"}

        if output_path:
            output_file = Path(output_path)
        else:
            output_file = Path(f"tts_output_{int(time.time())}.wav")

        # Ensure directory exists
        output_file.parent.mkdir(parents=True, exist_ok=True)

        config = {}
        if provider:
            config = {"streaming": {"provider": provider}, provider: {}}
        if voice:
            config.setdefault(provider or "gemini", {})["voice"] = voice

        try:
            result = stream_tts_to_wav(text, str(output_file), tts_config=config or None, provider=provider or None)
            result["text_length"] = len(text)
            result["voice"] = voice or "default"
            return result
        except Exception as exc:
            return {
                "status": "error",
                "message": str(exc),
                "text": text,
            }
