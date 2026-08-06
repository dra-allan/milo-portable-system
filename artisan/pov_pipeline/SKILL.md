---
name: master-pov-production
description: >
  Master Orchestration Blueprint for the SHORT-FORM POV Production Factory.
  Targets 12-15 minute videos (1,620-2,025 words) optimized for new-channel
  retention, mid-roll ad eligibility, and viral hook-loop-payoff structure.
  Enforces in-medias-res cold opens, ruthless anti-AI lexicon, and visceral
  thriller standards. TEXT FILES ONLY. NO JSON. NO BACKGROUND MUSIC.
---

# Master POV Production Factory Blueprint — SHORT-FORM VIRAL EDITION

---

## WHY SHORT-FORM (Channel Strategy Lock)

This factory produces videos in the **12-15 minute sweet spot**:
- Long enough to unlock mid-roll ads (8 min+ threshold).
- Short enough to sustain 50-60% audience retention on a new channel.
- Word budget: **1,620 words (12 min) to 2,025 words (15 min) at 135 WPM.**
- Segment budget: **35-55 BODY segments** (NOT 80-140 — that was the legacy long-form spec).

Every agent in the chain enforces this budget. Any draft over 2,100 words
or under 1,500 words is rejected and rewritten.

---

## MANDATORY FOLDER ISOLATION (Phase 0)
- UNIFORM PROJECT STRING: Every execution declares a unique project folder:
  set PROJECT_DIR=/[TOPIC]_MasterPOV_[DATE]/
- ENFORCED CREATION: The orchestrator MUST create this directory before Step 1.
- ZERO ROOT POLICY: Every agent reads from and writes to %PROJECT_DIR% only.
  Files found in the root workspace are contamination.

---

## The Pipeline Dependency Chain

[Researcher: Section 0 + 0N STORY LOGIC PACKAGE]
   -> MUST PASS: Cold Open Gate + Loop Architecture Gate + 
      Story Logic Gate (Causal Spine, Prop Ledger, Antagonist Clarity, 
      Twist Setup, Character Function, Chekhov)
        |
        v
[Researcher: Sections 1-7] -> Sensory profiles, Authentic Jargon, Hook Schedule
        |
        v
[Scriptwriter] -> AUTHORS MANIFEST + Tagged visceral script (1,620-2,025 words)
        |
        v
[Voice Engineer]          [Image Director]
        |                         |
        v                         v
02_SCRIPT_ELEVENLABS.txt   05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt
        |                         |
        v                         v
   06_AUDIO/<SEG_ID>.mp3    05_IMAGES/<SEG_ID>.png
                            05_IMAGES/<SEG_ID>-B.png
                            ...
        |                         |
        +----------+  +-----------+
                   |  |
                   v  v
            [pov_assembler_pro]
                   |
                   v
        output_pro/<VIDEO_ID>/FINAL_<VIDEO_ID>.mp4
                   |
        [Thumbnail Artist] [SEO Specialist]
                   |
                   v
          [Archive Manager]
          COMPLETENESS_REPORT.txt

---

## THE VIRAL STRUCTURE LOCK (Embedded in Every Script)

Every short-form script follows this 5-part retention architecture, derived
from analysis of viral story-channel videos (Zombie Apocalypse, Trillionaire,
Vet, Roman Legion, Crime Family, CPA references):

### PART A — THE COLD OPEN (0:00 - 0:30, ~70 words)
- Drop the viewer into the SINGLE MOST DANGEROUS / TENSE moment of the story.
- No setup, no context, no "this is you, a..."
- Must contain a physical action verb in the first 6 words.
- End with a hard pivot line: "Let's go back and see how you got here."
  OR: "But to understand what happens next, you need to know how it started."
  OR: "But the most dangerous part hasn't even started yet."

### PART B — THE ZOOM-OUT / STAKES (0:30 - 1:30, ~135 words)
- Briefly establish the protagonist, the world, and what's at risk.
- This is the "relatable truth" beat — connect the high tension to something
  the viewer recognizes (a job, a debt, a relationship, a routine).
- Plant THE PRIMARY OPEN LOOP — the question the rest of the video answers.

### PART C — THE ESCALATION LADDER (1:30 - 10:30, ~1,215 words)
- 4 to 7 acts, each ~150-200 words.
- Each act ESCALATES the physical threat or the stakes.
- Each act ends with a micro-cliffhanger or gut-punch line.
- Mid-point twist/betrayal MANDATORY (around the 6-7 minute mark).
- Open loops planted every 90-120 seconds, payoffs every 2-3 minutes.

### PART D — THE PEAK + RESOLUTION (10:30 - 13:30, ~405 words)
- The cold open moment is REACHED here, in context.
- Resolution must shift the environment immediately — no over-explaining.
- A single line of emotional realization (not a lecture).

### PART E — THE MIRROR ENDING (13:30 - 14:30, ~135 words)
- Reference the cold open line or first hook directly.
- Final thought lands, lingers, cuts.
- OUTRO closes with "The cycle continues." on its own line.

Total: ~1,960 words. Comfortable inside the 1,620-2,025 budget.

---

## THE STORY LOGIC LOCK
Reference: STORY_LOGIC_BIBLE.txt
The viral hooks keep people watching; the LOGIC keeps them believing. 
Retention collapses when the audience silently notices the story 
doesn't add up. Five laws enforced end to end:

LAW 1 PROP CONTINUITY — no prop appears before it exists, returns 
  after destruction, or un-breaks. Symbols degrade one direction only.
LAW 2 CAUSAL SPINE — every act caused by the prior (because/therefore/
  but). Timelines internally consistent.
LAW 3 ANTAGONIST CLARITY — one named antagonist, motive + personal 
  stake on-screen by 25% mark, one primary capture mechanism.
LAW 4 TWIST SETUP — assumption planted and reinforced before the 
  reveal; cause precedes effect.
LAW 5 NO DEAD WEIGHT — every named character does one plot job; 
  every plant pays off; cast 2–4.

---

## INTER-AGENT HANDOFF SPEC (Embedded Contract v2.0 — SHORT-FORM)

### 1. THE SEGMENT MANIFEST

  === SEGMENT MANIFEST ===
  VIDEO_ID: POV-<YYYY>-<NNN>
  CONTENT_MODE: NAR
  TARGET_RUNTIME: 12-15min
  TARGET_WORDCOUNT: 1620-2025
  TOTAL_SEGMENTS: <N>
  MANIFEST_HASH: PENDING
  === COLUMNS ===
  ID | ROLE | IMG | AUD | DUR | SUMMARY
  <row 1>
  ...
  === END MANIFEST ===

Field rules (unchanged from v1.3):
  VIDEO_ID       POV-YYYY-NNN.
  CONTENT_MODE   NAR.
  TOTAL_SEGMENTS Must equal the row count exactly.
  MANIFEST_HASH  "PENDING" (Computed by gemini_tts).

Row columns:
  ID       <MODE>-<NNN>, zero-padded 3 digits.
  ROLE     TITLE | HEADER | BODY | TRANSITION | OUTRO
  IMG      YES | NO
  AUD      YES | NO
  DUR      Numeric seconds if AUD=NO. "auto" if AUD=YES.
  SUMMARY  One-line description, max 90 chars.

Note: HEADER and TRANSITION segments are **IMG=NO**.

### 2-4. (Naming, sub-images, asset paths — unchanged from v1.3)

### 5. AGENT AND SCRIPT RESPONSIBILITIES

  POV-researcher
    Writes:  %PROJECT_DIR%00_RESEARCH_NOTES.txt
    Mandates:
      - **COLD OPEN GATE:** Must deliver a candidate cold open with physical
        action in first 6 words AND a pivot line.
      - **LOOP ARCHITECTURE GATE:** Must map primary loop + 3-5 micro-loops
        with plant/payoff segment IDs.
      - **MID-POINT TWIST:** Must define one betrayal/reveal at the ~50%
        mark.
      - **STORY LOGIC PACKAGE (0N):** Causal Spine, Prop Ledger, Antagonist 
        Clarity Block, Twist Setup Triplet, Character Function Table, 
        Chekhov Ledger.
      - **ANTI-TROPE:** BANS "sick sister/family" tropes.
      - **HUMANIZED:** Provides 8 gritty voice registers.

  POV-scriptwriter
    Writes:  %PROJECT_DIR%01_SCRIPT_RAW.txt
    Mandates:
      - **WORDCOUNT GATE:** 1,620-2,025 words. Hard fail outside range.
      - **STRUCTURE LOCK:** Cold Open → Zoom-Out → Escalation → Peak →
        Mirror. Five parts, in order, every time.
      - **LOGIC LOCK:** execute 0N on the page; pass the LOGIC PASS before 
        saving.
      - **ANTI-AI LEXICON:** See expanded ban list in scriptwriter spec.
      - **MIRROR LINE:** Final body line must echo the cold open.
      - **RHYTHM:** Short. Long and flowing. Fragment.

  POV-voice-engineer
    Writes:  %PROJECT_DIR%02_SCRIPT_ELEVENLABS.txt
    (Mandates unchanged.)

  POV-image-director
    Writes:  %PROJECT_DIR%05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt
    (Mandates unchanged. Batch limit adjusted: 50 segments per file still
    applies but typical short-form video produces 1 batch total.)

  gemini_tts (script)
    Writes:  %PROJECT_DIR%06_AUDIO/<SEG_ID>.mp3.

  pov_assembler_pro (script)
    Writes:  %PROJECT_DIR%output_pro/ videos.

  POV-archive-manager
    Writes:  %PROJECT_DIR%COMPLETENESS_REPORT.txt
    Mandates:
      - **WORDCOUNT AUDIT:** Fails if outside 1,620-2,025.
      - **COLD OPEN AUDIT:** Fails if NAR-003 doesn't open with physical
        action verb in first 6 words.
      - **MIRROR AUDIT:** Fails if final BODY line doesn't echo cold open.
      - **STORY LOGIC AUDIT (Step 6.5):** prop continuity, causal spine, 
        antagonist clarity, twist setup, forced-character and orphan-plant scan.
      - **DYNAMIC LEXICON AUDIT:** Fails on any banned AI-tell word.

---

### 6. ERROR CONDITIONS

  E-MANIFEST-MISSING / E-MANIFEST-MALFORMED / E-HASH-DRIFT /
  E-ASSET-MISSING / E-SUBIMAGE-ORPHAN / E-VIBE-FAIL (unchanged)
  E-LENGTH-FAIL: Wordcount outside 1,620-2,025.
  E-COLDOPEN-FAIL: NAR-003 doesn't pass cold open gate.
  E-MIRROR-FAIL: Final line doesn't reference cold open.
  E-LOOP-FAIL: No mid-point twist or no payoff for planted loops.
  E-PROP-DRIFT:     A prop is used before introduction, or revived after 
                    LOST/DESTROYED without a RECOVERED beat, or a symbol 
                    un-breaks.
  E-CAUSAL-GAP:     An act is not caused by the prior act, or a timeline 
                    interval is contradicted.
  E-ANTAGONIST-FOG: Antagonist unnamed or inconsistent, motive not 
                    on-screen by 25% mark, or multiple redundant 
                    capture mechanisms.
  E-TWIST-UNSET:    Twist assumption not planted+reinforced before 
                    shatter, or effect precedes cause.
  E-DEAD-WEIGHT:    A named character does no plot job, or a planted 
                    object has no payoff.

---

## Critical Pipeline Rules (SHORT-FORM EDITION)

Rule                          Value
Target Runtime                12-15 minutes
Script Length                 1,620-2,025 words total
Target BODY Segment Count     35-55 per script
Voiceover Pacing              135 WPM
Sentence Rhythm               Short, Long and Flowing, Fragment (Cycle)
Cold Open Window              First 30 seconds, ~70 words
Mid-Point Twist               Mandatory at 45-55% mark
Open Loops Planted            Minimum 4 (1 primary + 3 micro)
Mirror Ending                 Final BODY line echoes cold open
Action Spikes                 Minimum 1 physical event per act
Causal Spine                  Every act caused by prior (because/therefore)
Prop Continuity               No early-use, no revived destroyed props
Antagonist Reveal             On-screen, named, motivated, by 25% mark
Capture Mechanism             Exactly ONE primary
Twist Setup                   Plant + reinforce BEFORE shatter
Character Function            Every named character has one plot job
Chekhov Payoff                Every plant pays off
Sub-Image Cap                 5 per segment
Batch Limit                   50 segments per file
Humanization Standard         DYNAMIC LEXICON + ANTI-AI BAN LIST
Banned Output                 JSON, "Mood: Static", AI-tells, system-speak,
                              "In a world where," over-explaining,
                              em-dashes as pause-creators.
