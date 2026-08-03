# Milo Porting Plan — Checkpoint

## Last updated: 2026-08-03

## What's Done

### Phase 1: Skills System & Learning Loop — COMPLETE
- Skill class already had `origin` field with "learned" support
- Skill already had `pinned` field
- `milo learn` command fully functional
- Linter and curator handle learned skills
- No code changes needed

### Phase 2: Persona / User-Model — COMPLETE
- `Trait.source` field: observed | stated | inferred | imported | learned
- `observe()` method accepts `source` param
- `build_extract_prompt()` and `run_extraction()` in profile.py
- **NEW**: `sessions.py` — auto-extraction hook in `log_turn()` (every 5 turns)
- **NEW**: `_trigger_profile_extraction()` grabs last 5 transcript turns and runs extraction
- Also fixed `cli_extra.py` media generate arg parsing with shlex

## What's Left

### Phase 3: Tool Framework
- Create `miloctl/tools/base.py` — Tool base class with JSON schema generation
- Create `miloctl/tools/__init__.py` — auto-discovery
- Implement `milo tools list` and `milo tools run`

### Phase 4: Memory Enhancements
- Add embedding column (BLOB) to memories table
- Hybrid search: FTS5 (0.7) + vector cosine similarity (0.3)
- `milo memory compress` command (summarize old/low-importance memories)

### Phase 5: Jinja2 Prompt System
- Replace string persona building in `persona.py` with Jinja2 Environment
- Default template `templates/system.j2`
- User overrides at `~/.milo/prompts/`

### Phase 6: Packs & Media
- Verify `milo packs` works for skill packs
- Multimodal tool placeholders: image_generate, tts, stt
- Complete `milo media` subcommands

### Phase 7: Observability
- Structured JSON logging with `loguru` or `structlog`
- Optional Prometheus metrics endpoint on port 9090

### Phase 8: Vault Linking & Routines
- Store vault note path in memory metadata via `milo vault promote`
- `milo vault link <mem-id> <note-path>` command
- `milo routines log --follow` for real-time tailing

## Key Files for Resume
- `miloctl/sessions.py` — just modified, added extraction hook
- `miloctl/profile.py` — profile model with source tracking
- `miloctl/learning.py` — learning loop and NudgeEngine
- `miloctl/cli_extra.py` — agent-facing CLI commands
- `miloctl/persona.py` — persona assembly (needs Jinja2 in Phase 5)
- `miloctl/memory.py` — memory store (needs embeddings in Phase 4)
- `miloctl/harness.py` — agent harness adapters
- `miloctl/mcp.py` — MCP server tools

## Gotchas to remember
- `from . import profile` (with space) not `from .import` — circular import avoidance
- `PRAGMA journal_mode=WAL` in `sessions.py` `__init__` — don't accidentally drop it
- LF/CRLF warnings on Windows are cosmetic, ignore them