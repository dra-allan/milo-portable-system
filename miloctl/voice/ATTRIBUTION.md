# Attribution

`miloctl/voice/` is vendored and adapted from **Hermes Agent** by Nous Research
(MIT License). The following modules trace directly to Hermes source files:

| Milo module | Hermes source | What changed |
|---|---|---|
| `tts_streaming.py` | `tools/tts_streaming.py` | Removed Hermes config/registry imports; rebuilt on stdlib `urllib` instead of `requests`; `xai` provider dropped (WebSocket-only, needs async); kept Gemini/OpenAI/ElevenLabs, SentenceChunker, interruption latch, 16 MiB cap |
| `stt.py` | `tools/transcription_tools.py` | Kept OpenAI-compatible multipart flow + local faster-whisper; dropped Hermes config/credential-pool plumbing; stdlib-only |
| `wake.py` | `tools/wake_word.py` | Kept engine abstraction (openwakeword/sherpa/porcupine) + cooldown/confirmation-frame anti-trigger logic; default phrase "hey milo" |
| `mode.py` | `tools/voice_mode.py` | Rebuilt orchestrator around Milo's harness + identity gate; push-to-talk + `--once` |

Additional attribution:
- Audio capture/playback uses optional `sounddevice`/`numpy` (BSD/Python Software
  Foundation) or system `ffmpeg` (GPL, when the user provides it).
- `openwakeword`, `sherpa-onnx`, `pvporcupine`, `faster-whisper` are optional
  runtime extras, never bundled or required by Milo core.

Hermes Agent is MIT licensed; copying with attribution is permitted. See:
https://github.com/NousResearch/hermes-agent
