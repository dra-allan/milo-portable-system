"""
voice/tts_streaming.py — streaming text-to-speech (int16 mono PCM chunks).

Vendored and adapted from Hermes Agent (Nous Research, MIT) — ``tools/tts_streaming.py``.
Rebuilt on the stdlib only (``urllib``) so it keeps Milo's "installs on a fresh
machine with no wheels" promise; `requests` is never required.

Provider contract: a ``StreamingTTSProvider`` yields raw int16 little-endian mono
PCM bytes at ``sample_rate`` Hz. True chunked-API providers (Gemini SSE, OpenAI
pcm, ElevenLabs pcm) stream sentence-by-sentence; providers with no chunked API
still produce PCM via the same per-sentence path.

Core pieces:

* ``SentenceChunker`` — incremental sentence cutter for LLM token deltas. Feeds
  text in as it is generated, hands back complete sentences ready to speak.
* ``resolve_streaming_provider`` — pick the best usable streamer by config.

Providers:

* ``edge`` (default when edge-tts is installed) — Microsoft's free neural
  voices via ``edge-tts``. No API key, no rate limits. Streams MP3, decoded to
  int16 PCM at 24 kHz by ffmpeg (``MILO_FFMPEG`` or PATH). Voice defaults to
  "en-US-GuyNeural".
* ``gemini`` — ``streamGenerateContent?alt=sse`` returning base64 PCM at
  24 kHz. Voice defaults to Milo's house voice "Charon". Rate-limited on the
  free tier; keys are round-robined on 429.
* ``openai`` — ``/audio/speech`` with ``response_format=pcm`` (24 kHz).
* ``elevenlabs`` — ``/v1/text-to-speech`` with ``output_format=pcm_24000``.

MIT — original copyright Nous Research. See ATTRIBUTION in the package README.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from ..env import get as env_get

logger = logging.getLogger(__name__)

# 16 MiB hard cap on PCM accepted from one provider stream (matches Hermes).
MAX_STREAM_BYTES = 16 * 1024 * 1024

# Interruption latch — lets voice mode know the user barged in mid-speech.
SPEECH_INTERRUPTED_NOTE = (
    "[Note: the user interrupted your previous spoken reply before it finished.]"
)
_INTERRUPT_TTL_S = 120.0
_interrupted_at: Optional[float] = None


def mark_speech_interrupted() -> None:
    """Record that a spoken reply was cut off (for the next model turn)."""
    global _interrupted_at
    _interrupted_at = time.monotonic()


def take_speech_interrupted() -> bool:
    """Pop the latch; True when a barge happened within the TTL."""
    global _interrupted_at
    at, _interrupted_at = _interrupted_at, None
    return at is not None and time.monotonic() - at < _INTERRUPT_TTL_S


# Sentence boundary: after .!? followed by whitespace, or a blank line.
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])(?:\s|\n)|(?:\n\n)")
_THINK_BLOCK_RE = re.compile(r"<think[\s>].*?</think>", flags=re.DOTALL)


class SentenceChunker:
    """Incremental sentence cutter for LLM token deltas.

    Strips ``<think>`` blocks (even split across deltas) and merges fragments
    shorter than *min_len* into the following sentence, so short interjections
    ride along instead of stalling as tiny clips.
    """

    def __init__(self, min_len: int = 20):
        self.min_len = min_len
        self.buf = ""

    def feed(self, delta: str) -> List[str]:
        """Absorb *delta*; return every complete sentence now ready to speak."""
        self.buf = _THINK_BLOCK_RE.sub("", self.buf + delta)
        if "<think" in self.buf and "</think>" not in self.buf:
            return []  # open think tag — the closing tag may arrive next delta
        out: List[str] = []
        start = 0  # skip boundaries that would leave the head too short
        while m := SENTENCE_BOUNDARY_RE.search(self.buf, start):
            head = self.buf[: m.end()]
            if len(head.strip()) < self.min_len:
                start = m.end()
                continue
            out.append(head)
            self.buf = self.buf[m.end():]
            start = 0
        return out

    def flush(self) -> List[str]:
        """Drain the tail (end-of-text or long-idle flush)."""
        tail = _THINK_BLOCK_RE.sub("", self.buf).strip()
        self.buf = ""
        return [tail] if tail else []


# ---------------------------------------------------------------------------
# Low-level HTTP helpers (stdlib only)
# ---------------------------------------------------------------------------

def _http_json(
    url: str,
    *,
    payload: Optional[dict] = None,
    data: Optional[bytes] = None,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    timeout: float = 60.0,
) -> bytes:
    """POST/GET JSON or raw bytes, return the raw response body."""
    target = url
    if params:
        target = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    body = data
    req_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    if body is not None:
        req_headers.setdefault("Content-Length", str(len(body)))
    req = urllib.request.Request(target, data=body, headers=req_headers, method="POST" if (payload is not None or data is not None) else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc


def _sse_pcm_stream(url: str, params: dict, payload: dict, *, timeout: float = 60.0) -> Iterator[bytes]:
    """Stream base64 PCM chunks out of a Gemini-style SSE feed.

    Yields decoded PCM bytes for every ``data:`` line with inline audio.
    """
    target = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        target,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        if exc.code == 429:
            raise RateLimited(detail) from exc
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    with resp:
        buf = b""
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", "replace").strip()
                if not text.startswith("data: "):
                    continue
                try:
                    event = json.loads(text[len("data: "):])
                    parts = event["candidates"][0]["content"]["parts"]
                except (ValueError, KeyError, IndexError, TypeError):
                    continue
                for part in parts:
                    inline = part.get("inlineData") or part.get("inline_data") or {}
                    b64 = inline.get("data", "")
                    if not b64:
                        continue
                    try:
                        yield base64.b64decode(b64)
                    except (ValueError, TypeError) as exc:
                        logger.warning("SSE: bad base64 audio: %s", exc)


class RateLimited(RuntimeError):
    """A provider answered 429 — the caller may retry with another key."""


def _ffmpeg_exe() -> Optional[str]:
    """Resolve ffmpeg for MP3→PCM decoding (MILO_FFMPEG env, else PATH)."""
    env_path = env_get("MILO_FFMPEG").strip()
    if env_path and Path(env_path).exists():
        return env_path
    found = shutil.which("ffmpeg")
    return found


def _decode_mp3_to_pcm(mp3: bytes, sample_rate: int) -> bytes:
    """Decode MP3 bytes to mono int16 PCM at ``sample_rate`` via ffmpeg."""
    exe = _ffmpeg_exe()
    if not exe:
        raise RuntimeError(
            "Edge TTS needs ffmpeg to decode audio. Install ffmpeg and set "
            "MILO_FFMPEG in $MILO_HOME/.env."
        )
    proc = subprocess.run(
        [
            exe, "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0",
            "-f", "s16le", "-ac", "1", "-ar", str(sample_rate),
            "pipe:1",
        ],
        input=mp3, capture_output=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg decode failed: {proc.stderr.decode('utf-8', 'replace')[-300:]}"
        )
    return proc.stdout


def _capped(chunks: Iterator[bytes], label: str) -> Iterator[bytes]:
    """Bound the total PCM accepted from one stream to guard buggy providers."""
    total = 0
    for chunk in chunks:
        total += len(chunk)
        if total > MAX_STREAM_BYTES:
            logger.warning("%s exceeded %d bytes; truncating", label, MAX_STREAM_BYTES)
            return
        yield chunk


# ---------------------------------------------------------------------------
# ABC + registry
# ---------------------------------------------------------------------------

class StreamingTTSProvider(ABC):
    """Yields raw int16 little-endian mono PCM chunks at ``sample_rate``."""

    sample_rate: int = 24000
    channels: int = 1
    sample_width: int = 2  # bytes/sample (int16)

    def __init__(self, tts_config: Dict, section: Dict):
        self.tts_config = tts_config
        self.section = section

    @staticmethod
    @abstractmethod
    def available() -> bool:
        """True when this provider's credentials are usable right now."""

    @abstractmethod
    def stream(self, text: str) -> Iterator[bytes]:
        """Yield PCM chunks for ``text``. Raise on failure (caller logs)."""


_REGISTRY: Dict[str, type[StreamingTTSProvider]] = {}


def register(name: str) -> Callable[[type[StreamingTTSProvider]], type[StreamingTTSProvider]]:
    def _wrap(cls: type[StreamingTTSProvider]) -> type[StreamingTTSProvider]:
        _REGISTRY[name] = cls
        return cls

    return _wrap


def _try_instantiate(name: str, tts_config: Dict) -> Optional[StreamingTTSProvider]:
    cls = _REGISTRY.get(name)
    if cls is None or not cls.available():
        return None
    try:
        return cls(tts_config, tts_config.get(name) or {})
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("streaming provider %s init failed: %s", name, exc)
        return None


# Best free/no-key first, then chunked latency/quality. edge-tts has no rate
# limits; Gemini/OpenAI/ElevenLabs need paid keys or face 429s on free tiers.
_PROVIDER_PRIORITY: List[str] = ["edge", "gemini", "openai", "elevenlabs"]


def resolve_streaming_provider(
    tts_config: Dict,
    preferred: Optional[str] = None,
) -> Optional[StreamingTTSProvider]:
    """Return a ready streamer for the configured provider, else ``None``.

    * ``tts_config["streaming"]["provider"]`` = ``auto`` walks the priority list.
    * Otherwise the explicit name, or *preferred* when given.
    """
    streaming_cfg = tts_config.get("streaming") or {}
    pinned = str(streaming_cfg.get("provider") or "").lower().strip()
    if pinned == "auto":
        for name in _PROVIDER_PRIORITY:
            inst = _try_instantiate(name, tts_config)
            if inst is not None:
                return inst
        return None
    if pinned:
        return _try_instantiate(pinned, tts_config)
    name = (preferred or str(streaming_cfg.get("preferred") or "auto")).lower().strip()
    if name == "auto":
        for cand in _PROVIDER_PRIORITY:
            inst = _try_instantiate(cand, tts_config)
            if inst is not None:
                return inst
        return None
    return _try_instantiate(name, tts_config)


def _key(*names: str) -> str:
    for n in names:
        val = env_get(n).strip()
        if val:
            # GEMINI_API_KEYS may be a comma-separated list (round-robin).
            first = val.split(",")[0].strip()
            if first:
                return first
    return ""


def _gemini_keys() -> list:
    """All Gemini keys (round-robin list from GEMINI_API_KEYS, else single)."""
    for n in ("GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = env_get(n).strip()
        if not val:
            continue
        keys = [k.strip() for k in val.split(",") if k.strip()]
        if keys:
            return keys
    return []


def _gemini_key() -> str:
    keys = _gemini_keys()
    return keys[0] if keys else ""


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

def _has_module(name: str) -> bool:
    try:
        import importlib.util  # noqa: F401

        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


@register("edge")
class EdgeTTSStreamer(StreamingTTSProvider):
    """Microsoft Edge neural TTS via ``edge-tts`` — free, no key, no limits.

    Streams MP3 through edge-tts's async generator; decodes to mono int16 PCM
    at 24 kHz with ffmpeg. Not truly chunked (synthesizes per sentence), but
    the free tier has no rate limit so long replies just work.
    """

    sample_rate = 24000
    DEFAULT_VOICE = "en-US-GuyNeural"

    @staticmethod
    def available() -> bool:
        return _has_module("edge_tts") and _ffmpeg_exe() is not None

    def stream(self, text: str) -> Iterator[bytes]:
        import asyncio

        import edge_tts

        voice = str(self.section.get("voice") or self.DEFAULT_VOICE).strip() or self.DEFAULT_VOICE
        rate = str(self.section.get("rate") or "+0%").strip() or "+0%"

        async def _collect() -> bytes:
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            chunks = []
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    chunks.append(chunk["data"])
            return b"".join(chunks)

        try:
            mp3 = asyncio.run(_collect())
        except Exception as exc:
            raise RuntimeError(f"Edge TTS failed: {exc}") from exc
        if not mp3:
            return
        pcm = _decode_mp3_to_pcm(mp3, self.sample_rate)
        yield from _capped(iter([pcm]), "Edge TTS")


@register("gemini")
class GeminiStreamer(StreamingTTSProvider):
    """Gemini ``streamGenerateContent?alt=sse`` — base64 PCM chunks (24 kHz).

    Round-robins across every ``GEMINI_API_KEYS`` entry on 429; free-tier TTS
    is heavily rate-limited, so this is what makes long replies survivable.
    """

    sample_rate = 24000
    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    DEFAULT_MODEL = "gemini-2.5-flash-preview-tts"
    DEFAULT_VOICE = "Charon"

    @staticmethod
    def available() -> bool:
        return bool(_gemini_key())

    def stream(self, text: str) -> Iterator[bytes]:
        model = str(self.section.get("model") or self.DEFAULT_MODEL).strip() or self.DEFAULT_MODEL
        voice = str(self.section.get("voice") or self.DEFAULT_VOICE).strip() or self.DEFAULT_VOICE
        base_url = str(self.section.get("base_url") or self.DEFAULT_BASE_URL).strip().rstrip("/")

        payload = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": voice},
                    },
                },
            },
        }
        url = f"{base_url}/models/{model}:streamGenerateContent"

        keys = _gemini_keys()
        last_err: Optional[Exception] = None
        for i, api_key in enumerate(keys):
            params = {"alt": "sse", "key": api_key}
            try:
                yield from _capped(
                    _sse_pcm_stream(url, params, payload),
                    "Gemini streaming TTS",
                )
                return
            except RateLimited as exc:
                last_err = exc
                logger.warning("gemini 429 on key %d/%d; rotating", i + 1, len(keys))
                continue
        raise RuntimeError(f"All Gemini keys rate-limited: {last_err}") from last_err


@register("openai")
class OpenAIStreamer(StreamingTTSProvider):
    """OpenAI ``/audio/speech`` with ``response_format=pcm`` (24 kHz)."""

    sample_rate = 24000
    DEFAULT_BASE_URL = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-4o-mini-tts"
    DEFAULT_VOICE = "alloy"

    @staticmethod
    def available() -> bool:
        return bool(_key("OPENAI_API_KEY"))

    def stream(self, text: str) -> Iterator[bytes]:
        api_key = _key("OPENAI_API_KEY")
        model = str(self.section.get("model") or self.DEFAULT_MODEL).strip() or self.DEFAULT_MODEL
        voice = str(self.section.get("voice") or self.DEFAULT_VOICE).strip() or self.DEFAULT_VOICE
        base_url = str(self.section.get("base_url") or self.DEFAULT_BASE_URL).strip().rstrip("/")
        payload = {
            "model": model,
            "voice": voice,
            "input": text,
            "response_format": "pcm",
        }
        url = f"{base_url}/audio/speech"
        body = _http_json(
            url,
            payload=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        yield from _capped(iter([body]), "OpenAI TTS")


@register("elevenlabs")
class ElevenLabsStreamer(StreamingTTSProvider):
    """ElevenLabs ``/v1/text-to-speech`` with ``output_format=pcm_24000``."""

    sample_rate = 24000
    DEFAULT_BASE_URL = "https://api.elevenlabs.io/v1"
    DEFAULT_MODEL = "eleven_turbo_v2_5"
    DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

    @staticmethod
    def available() -> bool:
        return bool(_key("ELEVENLABS_API_KEY"))

    def stream(self, text: str) -> Iterator[bytes]:
        api_key = _key("ELEVENLABS_API_KEY")
        model = str(self.section.get("model") or self.DEFAULT_MODEL).strip() or self.DEFAULT_MODEL
        voice_id = str(self.section.get("voice_id") or self.DEFAULT_VOICE_ID).strip() or self.DEFAULT_VOICE_ID
        base_url = str(self.section.get("base_url") or self.DEFAULT_BASE_URL).strip().rstrip("/")
        payload = {
            "model_id": model,
            "text": text,
            "output_format": "pcm_24000",
        }
        url = f"{base_url}/text-to-speech/{voice_id}"
        body = _http_json(
            url,
            payload=payload,
            headers={"xi-api-key": api_key},
        )
        yield from _capped(iter([body]), "ElevenLabs TTS")


# ---------------------------------------------------------------------------
# Convenience: stream text straight into a WAV file.
# ---------------------------------------------------------------------------

def stream_tts_to_wav(
    text: str,
    out_path: str,
    *,
    provider: Optional[str] = None,
    tts_config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Stream *text* to a mono 16-bit WAV file via the resolved provider.

    Returns a summary dict; raises RuntimeError when no provider is usable.
    """
    import wave

    cfg = dict(tts_config or {})
    cfg.setdefault("streaming", {"provider": provider or "auto"})
    if provider and provider != "auto":
        cfg.setdefault("streaming", {})["provider"] = provider

    streamer = resolve_streaming_provider(cfg, preferred=provider)
    if streamer is None:
        raise RuntimeError(
            "No usable TTS streamer. Set GEMINI_API_KEY, OPENAI_API_KEY, or "
            "ELEVENLABS_API_KEY in $MILO_HOME/.env."
        )

    frames = bytearray()
    for chunk in streamer.stream(text):
        frames += chunk

    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(streamer.channels)
        wf.setsampwidth(streamer.sample_width)
        wf.setframerate(streamer.sample_rate)
        wf.writeframes(bytes(frames))

    return {
        "status": "success",
        "output": out_path,
        "provider": streamer.__class__.__name__,
        "sample_rate": streamer.sample_rate,
        "channels": streamer.channels,
        "bytes": len(frames),
    }
