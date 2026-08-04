"""
Speech-to-Text tool — transcribes audio to text via the vendored voice stack.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..voice.stt import transcribe_audio as _transcribe
from .base import Tool


class SpeechToTextTool(Tool):
    """Transcribe audio to text using the vendored STT engine."""

    name = "stt"
    description = "Transcribe audio to text using the vendored STT engine."

    def run(self, audio_path: str, language: str = "") -> dict[str, Any]:
        """Transcribe audio to text.

        Args:
            audio_path: Path to the audio file to transcribe
            language: Language code hint (default: auto-detect)

        Returns:
            Dictionary with transcription results
        """
        audio_file = Path(audio_path)

        if not audio_file.exists():
            return {
                "status": "error",
                "message": f"Audio file not found: {audio_path}"
            }

        result = _transcribe(audio_path, language=language or None)
        return result
