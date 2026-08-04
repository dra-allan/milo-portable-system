"""
Voice stack tests — chunker, provider resolution, identity gate, tool wiring.

Network is never touched: providers are resolved with fake keys and the HTTP
layer is not called. The sentence chunker and WAV round-trip are pure stdlib.
"""

from __future__ import annotations

import wave

from miloctl.voice import stt, tts_streaming
from miloctl.voice.tts_streaming import SentenceChunker

# ── SentenceChunker ──────────────────────────────────────────────────────────

def test_chunker_splits_at_sentence_boundary():
    c = SentenceChunker(min_len=5)
    # "Hello there. " (13 chars) clears min_len, so it ships immediately.
    assert c.feed("Hello there. This is a test.") == ["Hello there. "]
    assert c.flush() == ["This is a test."]


def test_chunker_merges_short_fragments():
    c = SentenceChunker(min_len=8)
    # "Hi." (4 chars) is too short to ship alone; it must ride along with the
    # next sentence, so feed returns nothing and flush drains the whole tail.
    assert c.feed("Hi. This next sentence is plenty long enough to carry it.") == []
    assert c.flush() == ["Hi. This next sentence is plenty long enough to carry it."]


def test_chunker_strips_think_blocks():
    c = SentenceChunker()
    assert c.feed("<think>let me reason here</think> Here is the actual answer.") == []
    assert c.flush() == ["Here is the actual answer."]


def test_chunker_flush_drains_tail():
    c = SentenceChunker()
    assert c.feed("No punctuation yet") == []
    assert c.flush() == ["No punctuation yet"]


def test_chunker_handles_think_split_across_deltas():
    c = SentenceChunker()
    assert c.feed("<think") == []
    assert c.feed(" reasoning</think> Final answer sentence right here.") == []
    assert c.flush() == ["Final answer sentence right here."]


def test_chunker_keep_think_when_split_mid_tag():
    # A delta splitting "<think" from the rest with no whitespace cannot be
    # stripped — the open-tag guard returns [] and the tail carries it. This
    # matches the upstream Hermes behavior faithfully.
    c = SentenceChunker()
    assert c.feed("<think") == []
    assert c.feed("hidden reasoning</think> The final answer.") == []
    assert c.flush() == ["<thinkhidden reasoning</think> The final answer."]


# ── WAV round trip ───────────────────────────────────────────────────────────

def test_write_read_wav_roundtrip(tmp_path):
    pcm = (b"\x00\x00" * 4000) + (b"\x01\x01" * 4000)
    path = str(tmp_path / "out.wav")
    stt.write_pcm_wav(pcm, path, sample_rate=16000)
    with wave.open(path, "rb") as wf:
        assert wf.getframerate() == 16000
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getnframes() == 8000
    read_back, rate = stt.read_wav_to_pcm(path)
    assert rate == 16000
    assert read_back == pcm


# ── Provider resolution ──────────────────────────────────────────────────────

def test_no_provider_without_keys(monkeypatch, milo_home):
    for k in ("GEMINI_API_KEY", "GEMINI_API_KEYS", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    cfg = {"streaming": {"provider": "auto"}}
    assert tts_streaming.resolve_streaming_provider(cfg) is None


def test_gemini_selected_when_key_present(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    cfg = {"streaming": {"provider": "auto"}}
    streamer = tts_streaming.resolve_streaming_provider(cfg)
    assert streamer is not None
    assert streamer.__class__.__name__ == "GeminiStreamer"
    assert streamer.sample_rate == 24000


def test_pinned_provider_wins(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    cfg = {"streaming": {"provider": "openai"}}
    streamer = tts_streaming.resolve_streaming_provider(cfg)
    assert streamer.__class__.__name__ == "OpenAIStreamer"


def test_unknown_provider_returns_none(monkeypatch):
    cfg = {"streaming": {"provider": "does-not-exist"}}
    assert tts_streaming.resolve_streaming_provider(cfg) is None


def test_available_reflects_keys(monkeypatch, milo_home):
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert tts_streaming.GeminiStreamer.available() is False
    monkeypatch.setenv("GEMINI_API_KEYS", "a,b,c")
    assert tts_streaming.GeminiStreamer.available() is True


# ── Identity gate ────────────────────────────────────────────────────────────

def test_identity_gate_open_when_no_passphrase(monkeypatch):
    monkeypatch.delenv("MILO_IDENTITY_PASSPHRASE", raising=False)
    from miloctl.voice.mode import identity_verified

    assert identity_verified("anything") is True


def test_identity_gate_rejects_wrong_phrase(monkeypatch):
    monkeypatch.setenv("MILO_IDENTITY_PASSPHRASE", "correct horse")
    from miloctl.voice.mode import identity_verified

    assert identity_verified("wrong phrase") is False


def test_identity_gate_accepts_correct_phrase(monkeypatch):
    monkeypatch.setenv("MILO_IDENTITY_PASSPHRASE", "correct horse")
    from miloctl.voice.mode import identity_verified

    assert identity_verified("correct horse") is True


# ── Tool wiring ──────────────────────────────────────────────────────────────

def test_stt_tool_reports_missing_file():
    from miloctl.tools.stt import SpeechToTextTool

    result = SpeechToTextTool().run(audio_path="/no/such/file.wav")
    assert result["status"] == "error"


def test_tts_tool_requires_text():
    from miloctl.tools.tts import TextToSpeechTool

    result = TextToSpeechTool().run(text="")
    assert result["status"] == "error"


# ── VoiceSession turn plumbing ──────────────────────────────────────────────

def test_capture_turn_passes_duration_once(monkeypatch, tmp_path):
    """Regression: record() got duration twice (positional + **record_kwargs)."""
    from miloctl.voice import audio
    from miloctl.voice.mode import VoiceSession
    from miloctl.voice import stt as stt_mod

    calls = []

    def fake_record(duration, out_wav, **kwargs):
        calls.append((duration, kwargs))

    def fake_transcribe(path, **kwargs):
        return {"status": "success", "transcript": "hello world"}

    monkeypatch.setattr(audio, "record", fake_record)
    monkeypatch.setattr(stt_mod, "transcribe_audio", fake_transcribe)

    session = VoiceSession(record_kwargs={"duration": 5.0, "device": 3})
    text = session._capture_turn()

    assert text == "hello world"
    assert len(calls) == 1
    duration, kwargs = calls[0]
    assert duration == 5.0
    assert kwargs == {"device": 3}  # duration popped, not duplicated
