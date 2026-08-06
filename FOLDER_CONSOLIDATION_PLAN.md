# Milo Output Folder Consolidation Plan

## Goal
Single, visible, Desktop-accessible output folder for **all** Milo pipelines:
- **Shorts Pipeline** → rendered Shorts
- **POV Pipeline** → scripts, TTS audio, assembled videos
- **MM Pipeline** (long-form) → final videos
- **Milo Vault/Brain** → notes (already in `dra-brains`)

**Target:** `C:\Users\user\Desktop\Milo Video Factory` — already exists, already used by POV/MM.

**Eliminate:** Hidden folders (`milo-workspace`, `milo-projects` outside Desktop) that bloat the milo-portable-system repo and are hard to find.

---

## Current State

| Folder | Location | Contents | Problem |
|--------|----------|----------|---------|
| `milo-workspace` | `C:\Users\user\milo-workspace` | `shorts-data/`, `website-flip/`, `docs/`, `.cursor/` | Hidden in user root, not on Desktop |
| `milo-projects` | `C:\Users\user\milo_projects` | `dra-brains/`, `milo/`, `milo-portable-system/`, `agentmemory-milo/` | Hidden, contains repo clones (bloat) |
| `Milo Video Factory` | `C:\Users\user\Desktop\Milo Video Factory` | `projects/`, `audio/`, `images/`, `video/` | **Good** — on Desktop, already used by POV/MM |
| `shorts-data` | `C:\Users\user\milo-workspace\shorts-data` | `temp/`, `shorts/`, `data/`, `logs/` | Should move under Video Factory |
| `dra-brains` (Vault) | `C:\Users\user\milo_projects\dra-brains` | Obsidian vault | Keep separate — it's notes, not pipeline output |

---

## Target Structure

```
C:\Users\user\Desktop\
├── Milo Video Factory\           # <-- SINGLE OUTPUT ROOT (all pipelines)
│   ├── projects\                 # POV/MM project folders (already here)
│   ├── audio\                    # TTS output (already here)
│   ├── images\                   # Generated/fetched images (already here)
│   ├── video\                    # Final renders (already here)
│   ├── shorts\                   # <-- NEW: Shorts pipeline output
│   │   ├── temp\                 # Downloads, audio extractions
│   │   ├── shorts\               # Rendered Shorts (1080x1920)
│   │   ├── data\                 # library.json, processed_videos.db, transcripts/
│   │   └── logs\
│   ├── pov\                      # <-- NEW: POV pipeline intermediates
│   │   ├── scripts\              # Generated scripts
│   │   ├── tts\                  # TTS audio segments
│   │   └── assembler\            # Assembler intermediates
│   └── mm\                       # <-- NEW: MM pipeline intermediates (if needed)
└── Milo Workspace\               # <-- SINGLE WORKSPACE ROOT (config, repos, docs)
    ├── repos\                    # Cloned repos (milo-portable-system, etc.)
    ├── config\                   # Shared config files (.env, credentials)
    ├── docs\                     # Project docs
    └── .cursor\                  # Cursor IDE settings
```

---

## Migration Steps

### 1. Move `shorts-data` → `Milo Video Factory\shorts`
```powershell
Move-Item "C:\Users\user\milo-workspace\shorts-data" "C:\Users\user\Desktop\Milo Video Factory\shorts"
```

### 2. Update Shorts Pipeline `.env` (gitignored, machine-specific)
```ini
# C:\Users\user\Desktop\milo-portable-system\artisan\youtube-shorts-pipeline\config\.env
DATA_DIR=C:\Users\user\Desktop\Milo Video Factory\shorts\data
TEMP_DIR=C:\Users\user\Desktop\Milo Video Factory\shorts\temp
SHORTS_DIR=C:\Users\user\Desktop\Milo Video Factory\shorts\shorts
LOG_DIR=C:\Users\user\Desktop\Milo Video Factory\shorts\logs
DB_PATH=C:\Users\user\Desktop\Milo Video Factory\shorts\data\processed_videos.db
```

### 3. Create POV/MM output dirs under Video Factory
```powershell
New-Item -ItemType Directory -Force -Path "C:\Users\user\Desktop\Milo Video Factory\pov\scripts"
New-Item -ItemType Directory -Force -Path "C:\Users\user\Desktop\Milo Video Factory\pov\tts"
New-Item -ItemType Directory -Force -Path "C:\Users\user\Desktop\Milo Video Factory\pov\assembler"
```

### 4. Update POV Pipeline to use Video Factory
- **Assembler** (`pov_assembler_pro.py`): Default `--output` to `C:\Users\user\Desktop\Milo Video Factory\projects\<project_name>`
- **TTS** (`gemini_tts.py`): Default `--audio-dir` to `C:\Users\user\Desktop\Milo Video Factory\pov\tts\<project_name>`
- **Scripts**: Write generated scripts to `C:\Users\user\Desktop\Milo Video Factory\pov\scripts\<project_name>`

### 5. Move `milo-workspace` → Desktop as `Milo Workspace`
```powershell
Move-Item "C:\Users\user\milo-workspace" "C:\Users\user\Desktop\Milo Workspace"
```
Now contains: `repos/`, `docs/`, `.cursor/`, `website-flip/` — no pipeline data.

### 6. Handle `milo-projects`
- **`dra-brains`** (Vault): Keep as-is at `C:\Users\user\milo_projects\dra-brains` OR move to `C:\Users\user\Desktop\Milo Vault` for visibility. It's an Obsidian vault — separate from pipeline output.
- **`milo-portable-system`**: This is the **code repo** — should live in `Milo Workspace\repos\milo-portable-system` (clone there, remove from milo-projects).
- **Other folders** (`milo/`, `agentmemory-milo/`): Evaluate — likely old copies, can be removed if repo is the source of truth.

### 6. Update `.gitignore` in milo-portable-system
Ensure **no output paths** are tracked:
```gitignore
# All pipeline output lives outside the repo
C:\Users\user\Desktop\Milo Video Factory\
C:\Users\user\Desktop\Milo Workspace\
```

---

## Environment Variables for Code

Add to `config/.env.template` (committed, with placeholders):
```ini
# Single output root for ALL pipelines (set on each machine)
VIDEO_FACTORY_ROOT={{VIDEO_FACTORY_ROOT}}
# e.g. VIDEO_FACTORY_ROOT=C:\Users\user\Desktop\Milo Video Factory

# Shorts pipeline (derives from root)
DATA_DIR={{VIDEO_FACTORY_ROOT}}\shorts\data
TEMP_DIR={{VIDEO_FACTORY_ROOT}}\shorts\temp
SHORTS_DIR={{VIDEO_FACTORY_ROOT}}\shorts\shorts
LOG_DIR={{VIDEO_FACTORY_ROOT}}\shorts\logs
DB_PATH={{VIDEO_FACTORY_ROOT}}\shorts\data\processed_videos.db

# POV pipeline
POV_SCRIPTS_DIR={{VIDEO_FACTORY_ROOT}}\pov\scripts
POV_TTS_DIR={{VIDEO_FACTORY_ROOT}}\pov\tts
POV_ASSEMBLER_DIR={{VIDEO_FACTORY_ROOT}}\pov\assembler
POV_OUTPUT_DIR={{VIDEO_FACTORY_ROOT}}\projects

# MM pipeline
MM_OUTPUT_DIR={{VIDEO_FACTORY_ROOT}}\projects
```

Code reads `VIDEO_FACTORY_ROOT` and derives subdirs — single source of truth.

---

## Migration Commands (Run in Order)

```powershell
# 1. Move shorts-data to Video Factory
Move-Item "C:\Users\user\milo-workspace\shorts-data" "C:\Users\user\Desktop\Milo Video Factory\shorts"

# 2. Create POV dirs
New-Item -ItemType Directory -Force -Path "C:\Users\user\Desktop\Milo Video Factory\pov\scripts"
New-Item -ItemType Directory -Force -Path "C:\Users\user\Desktop\Milo Video Factory\pov\tts"
New-Item -ItemType Directory -Force -Path "C:\Users\user\Desktop\Milo Video Factory\pov\assembler"

# 3. Update Shorts .env (run from milo-portable-system root)
@"
DATA_DIR=C:\Users\user\Desktop\Milo Video Factory\shorts\data
TEMP_DIR=C:\Users\user\Desktop\Milo Video Factory\shorts\temp
SHORTS_DIR=C:\Users\user\Desktop\Milo Video Factory\shorts\shorts
LOG_DIR=C:\Users\user\Desktop\Milo Video Factory\shorts\logs
DB_PATH=C:\Users\user\Desktop\Milo Video Factory\shorts\data\processed_videos.db

YOUTUBE_API_KEY=<preserved from old .env>
"@ | Set-Content "artisan\youtube-shorts-pipeline\config\.env"

# 4. Move milo-workspace to Desktop
Move-Item "C:\Users\user\milo-workspace" "C:\Users\user\Desktop\Milo Workspace"

# 5. Verify
python -m src.main --mode test  # from artisan/youtube-shorts-pipeline
```

---

## Benefits

| Before | After |
|--------|-------|
| 4+ scattered folders (`milo-workspace`, `milo-projects`, `Milo Video Factory`, `dra-brains`) | **2 visible Desktop folders**: `Milo Video Factory` (output), `Milo Workspace` (config/repos) |
| Pipeline data bloats `milo-portable-system` repo | Repo is **code only** — all data external |
| Hard to find output | **Everything on Desktop** — visible, searchable, backup-able |
| POV/MM/Shorts each have own output logic | **Single `VIDEO_FACTORY_ROOT`** env var controls all |
| `milo-projects` contains repo clones (bloat) | Repos live in `Milo Workspace\repos\` — clean separation |

---

## Files to Update

| File | Change |
|------|--------|
| `artisan/youtube-shorts-pipeline/config/.env` | Point to `Milo Video Factory\shorts\...` |
| `artisan/youtube-shorts-pipeline/config/.env.template` | Add `VIDEO_FACTORY_ROOT` + derived paths |
| `artisan/pov_pipeline/scripts/pov_assembler_pro.py` | Default `--output` to `VIDEO_FACTORY_ROOT\projects` |
| `artisan/pov_pipeline/tts/gemini_tts.py` | Default `--audio-dir` to `VIDEO_FACTORY_ROOT\pov\tts` |
| `artisan/pov_pipeline/scripts/*.py` (script generators) | Write scripts to `VIDEO_FACTORY_ROOT\pov\scripts` |
| `.gitignore` (repo root) | Ignore `C:\Users\user\Desktop\Milo Video Factory\` and `Milo Workspace\` |

---

## Verification Checklist

- [ ] `python -m src.main --mode test` passes (shorts pipeline)
- [ ] `python -m src.main --mode once <id> --from-library` produces clips in `Milo Video Factory\shorts\shorts\`
- [ ] POV assembler writes to `Milo Video Factory\projects\`
- [ ] TTS writes to `Milo Video Factory\pov\tts\`
- [ ] `Milo Workspace` on Desktop contains only config/repos/docs
- [ ] `milo-portable-system` repo has no data files tracked
- [ ] `dra-brains` vault still accessible (unchanged or moved to `Milo Vault`)