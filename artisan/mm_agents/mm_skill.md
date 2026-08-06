# Money Matrix — Full Content Production Pipeline

## Overview
7 specialized agents produce one Money Matrix YouTube video end-to-end.
Each agent reads the previous agent's output and writes to a numbered file.
No agent proceeds until the previous one passes its quality gate.

## Pipeline Sequence

```
00_RESEARCH_NOTES.txt   ← Researcher
01_SCRIPT_RAW.txt       ← Scriptwriter
02_SCRIPT_TTS.txt       ← VoiceEngineer
03_VISUALS.txt          ← VisualDirector
04_THUMBNAIL_PROMPT.txt ← ThumbnailArtist
05_METADATA.txt         ← SEOSpecialist
COMPLETENESS_REPORT.txt ← ArchiveManager (quality gate)
```

## Setup

```powershell
$env:TOPIC = "INDEX_FUNDS"
$env:PROJECT_DIR = "C:\Users\user\Desktop\milo\command\milo\artisan\mm_pipeline\$env:TOPIC"
New-Item -ItemType Directory -Path $env:PROJECT_DIR -Force
```

## Step 1 — Researcher
Load `mm_researcher.md`. Read the topic. Write `00_RESEARCH_NOTES.txt`.

Quality gate:
- [ ] 8+ data points with specific numbers + sources
- [ ] 2+ case studies or analogies
- [ ] Hook anxiety identified
- [ ] Sources tier-labeled (Tier 1-3)
- [ ] 3-5 actionable strategies
- [ ] 3-5 common mistakes with costs

## Step 2 — Scriptwriter
Load `mm_scriptwriter.md`. Read `00_RESEARCH_NOTES.txt`. Write `01_SCRIPT_RAW.txt`.

Quality gate:
- [ ] Wordcount: 1,500-2,000
- [ ] 6-part structure (Cold Open → Why → Mechanism → Strategy → Mistakes → CTA)
- [ ] Manifest format correct (HEADER/NARRATOR segments, AUD markers)
- [ ] Anti-AI lexicon scan: no banned terms
- [ ] Number rule: every financial claim has a real number
- [ ] Viewer rule: direct "you" address throughout

## Step 3 — VoiceEngineer
Load `mm_voice_engineer.md`. Read `01_SCRIPT_RAW.txt`. Write `02_SCRIPT_TTS.txt`.

Quality gate:
- [ ] Each AUD=YES segment: 3-8 sentences
- [ ] [pause] markers inserted: 12-20 total, strategic placements
- [ ] Emphasis caps on numbers and contrast words
- [ ] No mid-sentence pauses
- [ ] Manifest TOTAL_SEGMENTS matches body count

## Step 4 — VisualDirector
Load `mm_visual_director.md`. Read `01_SCRIPT_RAW.txt`. Write `03_VISUALS.txt`.

Quality gate:
- [ ] Every IMG=YES segment has at least one visual assignment
- [ ] Visual types used appropriately (Stock/Chart/Illustration/Text)
- [ ] Brand colors referenced where needed
- [ ] Search queries specific (5-10 words)

## Step 5 — ThumbnailArtist
Load `mm_thumbnail_artist.md`. Write `04_THUMBNAIL_PROMPT.txt`.

Quality gate:
- [ ] All 4 elements present: background, character, big number, text
- [ ] Number is specific dollar amount or percentage
- [ ] No text-in-image (post-production overlay)
- [ ] Power words used: Losing / Wasting / Do This / The Truth

## Step 6 — SEOSpecialist
Load `mm_seo_specialist.md`. Write `05_METADATA.txt`.

Quality gate:
- [ ] Title has specific number or dollar amount
- [ ] Description: hook line + 3-5 bullets + timestamps + disclaimer
- [ ] 25+ tags across 4 categories
- [ ] No generic or unrelated tags

## Step 7 — ArchiveManager
Load `mm_archive_manager.md`. Run all 9 checks.

## TTS Rendering
After pipeline passes, run the TTS pipeline:
```powershell
python milo/artisan/gemini_tts_pipeline/run_mm_pipeline.py --topic INDEX_FUNDS
```

Output: `mm_pipeline/INDEX_FUNDS/INDEX_FUNDS_FINAL.wav`

## Rendering
- Visuals: DaVinci Resolve or local editor with asset library
- Thumbnail: Canva or Photoshop from prompt
- Upload: YouTube with metadata from Step 6
