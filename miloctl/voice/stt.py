"""
voice/stt.py — speech-to-text transcription.

Vendored and adapted from Hermes Agent (Nous Research, MIT) —
``tools/transcription_tools.py``. Rebuilt on the stdlib only; the one optional
extra is ``faster-whisper`` for fully local, free transcription.

Providers (chosen by ``stt.provider`` config or ``MILO_STT_PROVIDER`` env):

* ``local`` (default, free) — faster-whisper running locally. Auto-downloads
  the model (~150 MB for ``base``) on first use. Needs ``pip install faster-whisper``.
* ``openai`` — OpenAI Whisper API, requires ``OPENAI_API_KEY``.
* ``groq`` (free tier) — Groq Whisper API, requires ``GROQ_API_KEY``.
* ``xai`` — xAI Grok STT API, requires ``XAI_API_KEY``.

All cloud providers use the OpenAI-compatible ``/audio/transcriptions`` shape.

MIT — original copyright Nous Research. See ATTRIBUTION in the package README.
"""

from __future__ import annotations

import json
import logging
import uuid
import wave
from pathlib import Path
from typing import Any, Dict, Optional

from ..env import get as env_get

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000  # Whisper-native.
SUPPORTED_SUFFIXES = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".ogg", ".aac"}

PROVIDERS = ("local", "openai", "groq", "xai")


def _key(*names: str) -> str:
    for n in names:
        val = env_get(n).strip()
        if val:
            return val
    return ""


def _has_faster_whisper() -> bool:
    try:
        import importlib.util  # noqa: F401

        return importlib.util.find_spec("faster_whisper") is not None
    except Exception:
        return False


def _resolve_provider(preferred: Optional[str] = None) -> str:
    name = (preferred or env_get("MILO_STT_PROVIDER").strip() or "local").lower().strip()
    if name not in PROVIDERS:
        raise ValueError(f"Unknown STT provider: {name!r} (expected one of {', '.join(PROVIDERS)})")
    return name


def _cloud_transcribe(
    provider: str,
    audio_path: str,
    model: str,
    base_url: str,
    api_key: str,
    language: Optional[str],
) -> str:
    """POST a multipart form to an OpenAI-compatible /audio/transcriptions."""
    import urllib.request

    boundary = f"----Milo{uuid.uuid4().hex}"
    with open(audio_path, "rb") as fh:
        audio = fh.read()
    suffix = Path(audio_path).suffix.lstrip(".") or "wav"

    fields = [
        ("model", model.encode()),
        ("file", (b"audio." + suffix.encode(), audio)),
    ]
    if language:
        fields.append(("language", language.encode()))

    body = bytearray()
    for name, value in fields:
        body += f"--{boundary}\r\n".encode()
        if isinstance(value, tuple):
            filename, data = value
            body += f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
            body += b"Content-Type: application/octet-stream\r\n\r\n"
            body += data
        else:
            body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            body += value
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        base_url.rstrip("/") + "/audio/transcriptions",
        data=bytes(body),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
    except Exception as exc:  # HTTPError, URLError, timeout
        raise RuntimeError(f"{provider} STT failed: {exc}") from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError(f"{provider} STT returned non-JSON: {raw[:300]!r}") from None
    text = data.get("text") or ""
    return text.strip()


def _local_transcribe(audio_path: str, model: str, language: Optional[str]) -> str:
    if not _has_faster_whisper():
        raise RuntimeError(
            "Local STT needs 'faster-whisper'. Install it with: "
            "pip install faster-whisper   (or set MILO_STT_PROVIDER=openai/groq/xai)"
        )
    from faster_whisper import WhisperModel  # lazy, optional dep

    size = "base" if model in (None, "", "whisper-1") else model
    whisper = WhisperModel(size, device="cpu", compute_type="int8")
    segments, _info = whisper.transcribe(audio_path, language=language)
    return " ".join(seg.text.strip() for seg in segments).strip()


def transcribe_audio(
    audio_path: str,
    *,
    provider: Optional[str] = None,
    language: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Transcribe an audio file to text. Returns a result dict, never raises
    for transcription failures (the caller reads ``status``)."""
    path = Path(audio_path)
    if not path.exists():
        return {"status": "error", "message": f"Audio file not found: {audio_path}"}

    try:
        name = _resolve_provider(provider)
        lang = language or env_get("MILO_STT_LANGUAGE").strip() or None

        if name == "local":
            text = _local_transcribe(str(path), model or "", lang)
        elif name == "groq":
            text = _cloud_transcribe(
                name,
                str(path),
                model or env_get("GROQ_STT_MODEL").strip() or "whisper-large-v3-turbo",
                env_get("GROQ_BASE_URL").strip() or "https://api.groq.com/openai/v1",
                _key("GROQ_API_KEY"),
                lang,
            )
        elif name == "xai":
            text = _cloud_transcribe(
                name,
                str(path),
                model or env_get("XAI_STT_MODEL").strip() or "whisper-large-v3",
                env_get("XAI_STT_BASE_URL").strip() or "https://api.x.ai/v1",
                _key("XAI_API_KEY"),
                lang,
            )
        else:  # openai
            text = _cloud_transcribe(
                name,
                str(path),
                model or env_get("OPENAI_STT_MODEL").strip() or "whisper-1",
                env_get("OPENAI_STT_BASE_URL").strip() or "https://api.openai.com/v1",
                _key("OPENAI_API_KEY"),
                lang,
            )

        return {
            "status": "success",
            "provider": name,
            "transcript": text,
            "audio_path": str(path.absolute()),
            "language": lang or "auto",
        }
    except Exception as exc:
        logger.debug("STT failure", exc_info=True)
        return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# WAV helpers (stdlib) — used by wake-word / voice mode for capture.
# ---------------------------------------------------------------------------

def write_pcm_wav(pcm: bytes, out_path: str, sample_rate: int = SAMPLE_RATE) -> str:
    """Write raw mono int16 PCM bytes to a WAV file (``wave`` stdlib)."""
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return out_path


def read_wav_to_pcm(wav_path: str) -> tuple:
    """Read a WAV file back to (pcm_bytes, sample_rate)."""
    with wave.open(wav_path, "rb") as wf:
        rate = wf.getframerate()
        pcm = wf.readframes(wf.getnframes())
    return pcm, rate
