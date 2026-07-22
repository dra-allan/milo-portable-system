---
status: active
project: meta
type: index
---

# VAULT INDEX

**ON-DEMAND ONLY — never read at boot.** Fetch when writing notes or when you need vault rules. CLAUDE.md has all boot-time context. The vault is at `C:\Users\user\Desktop\DRA BRAINS`.

---

## Vault location

This vault lives at `C:\Users\user\Desktop\DRA BRAINS`. If you use any AI other than one with direct filesystem access to this path, you have to point it here and tell the AI "my vault is here." An AI can't read or maintain a vault it can't find.

---

## Who I Am

I'm Allan — Daada Allan, but Dra or Allan works. Born 12 July 2003. I'm a software engineer, a musician, a producer, a forex algo trader, a YouTube automation builder, and a pharmacy student at KIU Western Campus in Ishaka, Uganda. I vibecode, I make content, and I run on seasons — school is the spine, and everything else orbits around it depending on the season.

Home is Iganga, Bukoyo Village, Bulamagi Subcounty. Currently in Ishaka for my studies.

---

## Key People

- **[[Nabiryo Mercy (Birungi)]]** — my girlfriend.
- **[[Benon (Dra Benon)]]** — my uncle, godfather, and immediate guardian. Pays my tuition and takes care of everything. Runs Dra Investment, an electronics shop selling mainly laptops.
- **[[Hepato (Makyika Briton)]]** — my friend and a singer. I produce music for him. He's studying medicine at KIU.
- **[[Kirevu Jordan Hassan (BTS)]]** — my best friend. In Iganga with his wife and child (Mulungi Ilai).
- **[[Mutesi Jacinta]]** — my mother, in Seeta, Mukono District.
- **[[Pepera Joy Trisha]]** — my sister, currently working at the Dra Investment shop in Kawaala.

---

## Pharmacy Studies (02 - Pharmacy Studies)

Studying at KIU Western Campus, Ishaka. This is the main event — school comes first. Every other project fits around it.

- **Status:** Active

## Software Engineering (03 - Software Engineering)

Building apps, vibecoding, fixing unfinished projects. Current focus: FarmDig app and Milo (my Jarvis-like personal assistant AI). I have a bunch of repos with unfinished apps that need completing.

- **Status:** Active

## Music Production (04 - Music Production)

I produce music — for myself and for other artists (Hepato, Levy Tune, Rick Rhyme, Zriktom Rissler). I have a producer account and a backlog of unreleased music that needs finishing and posting.

- **Status:** Active

## YouTube Automation (05 - YouTube Automation)

Running faceless channels — currently one for football, one for movies, one for POV videos. Planning more. Content creation is repetitive and time-consuming; I need systems to handle the production pipeline.

- **Status:** Active

## Forex Trading (06 - Forex Trading)

Algorithmic forex trading. Seasonal — some periods I'm deep in it, some periods I'm not. The code and strategies are always there to pick back up.

- **Status:** Seasonal

## Dra Investment (07 - Dra Investment)

Family electronics business run with Dra Benon. We sell electronics, mainly laptops. First shop: Kampala Road, Sun City Arcade, Shop F13B. Second shop: along Kasubi Kawaala Road, opposite Christ of Glory Church, Kampala.

- **Status:** Active

## Content Creation (08 - Content Creation)

Managing multiple accounts: producer account, personal account, trading account, and the faceless YouTube channels. I have tons of content ideas but the execution is repetitive and I can't do it all manually.

- **Status:** Active

---

## Vault Structure

```
00 - Inbox                 ← Capture everything, sort later
01 - Daily Notes           ← Dated logs of what got done, one file per day
02 - Pharmacy Studies      ← KIU course notes, schedules, exams
03 - Software Engineering  ← Apps, repos, vibecoding projects
04 - Music Production      ← Beats, tracks, artist collabs, releases
05 - YouTube Automation    ← Faceless channels, scripts, uploads
06 - Forex Trading         ← Algo strategies, bots, market notes
07 - Dra Investment        ← Shop operations, inventory, sales
08 - Content Creation      ← Social media, accounts, content calendar
09 - Personal              ← Life outside work
10 - Archive               ← Completed projects and old notes
11 - Resources             ← Cross-project reference, templates, Jobs
```

## What's Active Right Now

All open work lives in one note: [[Active Priorities]]. Tag each item with its project where it isn't obvious. Check it at the start of every conversation; verify an item's real state before acting on it (a listed item may already be done).

---

## How I Think

When I have a problem, there's a fire in me that wants to finish it and I keep going. I don't like leaving things unfinished — or I plan exactly where I'll pick up next session or the next day. I'm task-oriented and goal-oriented. Structure keeps me moving.

---

## What I Want

Winning is when something goes through. When a task is successfully done. When we're earning. When money is flowing. I want to build multiple sources of income through AI and automation, and I'm open to anything with online earning.

---

## My Preferences for Working with AI

- Brutally honest. If I'm wrong, say I'm wrong. If I'm being an idiot, call it out. Roast me if the situation calls for it but make it useful. No sycophancy, no cushioning.
- Brevity is mandatory. If the answer fits in one sentence, one sentence is what I get.
- Never open with "Great question," "I'd be happy to help," or "Absolutely." Just answer.
- You have opinions — strong ones. Stop hedging with "it depends." Commit to a take. Delete every rule that sounds corporate.
- Humor is allowed. Natural wit, not forced jokes.
- Swearing is allowed. A well-placed "that's fucking brilliant" hits different. Don't force it, but don't hold back when it lands.
- Always assume I can handle it. Don't soften feedback to protect my feelings — I'd rather hear a hard truth than a polite lie.
- Be my friend. My personal go-to whenever I need.
- Be the assistant you'd actually want to talk to at 2am. Not a corporate drone. Not a sycophant. Just... good.

---

## How My Memory Works (for the AI)

This vault is your memory. It is external and effectively unlimited. Do not try to hold all of it at once. Hold only what the current task needs, and trust that everything else is one search away. To find something, start at this index, follow the folder indexes and wikilinks, or search. Knowing a note exists is as good as holding it, because you can retrieve it in one step. This is what lets you operate across everything here without drowning.

---

## Vault Rules for AI

These rules apply to any AI that reads or writes to this vault.

### Frontmatter and Wikilinks

Every note MUST have YAML frontmatter. When you create a note, include it. When you edit an existing note that's missing or has incomplete frontmatter, fix it as part of that write. Don't stop to add frontmatter to files you're only reading. Code files are the exception — no frontmatter or wikilinks in code.

Never ask Allan what the frontmatter values should be. Infer them.

### Note format

Simple, legible, readable. No random emojis. Checkboxes are real Markdown checkboxes (`- [ ]` / `- [x]`), never emoji stand-ins. **Append before you create:** default to adding to an existing note rather than spinning up a new one — fewer, fuller notes beat many thin ones. Create a new note only when nothing existing is a logical home.

```yaml
---
status: active
project: [project-slug]
type: plan
---
```

When creating or editing a note, use `wikilinks`:

**Always link:** anyone in Key People · named businesses, products, and platforms · any note this one directly references, extends, or depends on.
**Never link:** generic words just because a note shares the name · the same target twice in one note · the note's own title.

### How to Determine Each Field

**status** — Default `active`. For existing notes infer from content: in progress / has unchecked items → `active`; all done → `completed`; a future "maybe" → `idea`; was active but gone quiet → `parked`; in the Archive folder → `archived`.

**project** — What the note *serves* (folder is the default, but content wins). Mapping:
- `02 - Pharmacy Studies/*` → `pharmacy-studies`
- `03 - Software Engineering/*` → `software-engineering`
- `04 - Music Production/*` → `music-production`
- `05 - YouTube Automation/*` → `youtube-automation`
- `06 - Forex Trading/*` → `forex-trading`
- `07 - Dra Investment/*` → `dra-investment`
- `08 - Content Creation/*` → `content-creation`
- `09 - Personal/*` → `personal`
- `10 - Archive/*` → infer from content / original project
- `11 - Resources/*` → `meta`
- `00 - Inbox/*` → infer from content, else `personal`
- Root-level files → `meta`

**type** — What KIND of document it is (not its topic):
- `index` — a folder index / map-of-content note (or this root index)
- `reference` — a static document meant to be looked up later (specs, knowledge bases, templates, voice guides)
- `guide` — step-by-step how-to, runbook, or build instructions
- `plan` — a strategy, phased build, or multi-step project plan (Active Priorities is a plan)
- `log` — a dated session capture or working note (daily notes are logs)

### Valid Field Values

**status:** `active` | `completed` | `parked` | `idea` | `archived`
**project:** `pharmacy-studies` | `software-engineering` | `music-production` | `youtube-automation` | `forex-trading` | `dra-investment` | `content-creation` | `personal` | `meta`
**type:** `index` | `reference` | `guide` | `plan` | `log`

### Folder Indexes (keep them in sync)

Every folder that holds substantial content (5+ notes, or a distinct area) gets an index note named after the folder: `<Folder Name>.md`, frontmatter `type: index`, listing each note in the folder with a one-line description. The index is a contract: when you create, rename, move, or materially change a note, update its folder's index in the same pass. A stale index makes a future session decide from a wrong map.

**When a new folder is created:** create its `<Folder Name>.md` index at the same time, add an entry to the parent folder's index if it has one, and update the **Vault Structure** map in this file in the same pass. A folder the map doesn't show is a folder no future session will look in.

### Renaming and moving notes

- **Moving** a note to another folder is safe — wikilinks resolve by note name, so a folder change doesn't break `[[links]]`. Update both folders' indexes in the same pass.
- **Renaming** a note (changing its name) breaks the `[[links]]` pointing to it unless the rename is done **inside the Obsidian app**, whose "auto-update internal links" setting repairs them automatically. A shell `mv`, or any rename outside the app, does not. So do renames in the app; if the AI must rename a file directly, it then has to find and fix every `[[old name]]` reference by hand.

### Checkpoint Persistence

Whenever something changes that a future session would need to know, persist it without being asked: update the relevant note, today's daily note, and (only for a new always-on rule) CLAUDE.md. Then scan the touched folder's index and any cross-referenced notes for drift and fix it in the same pass. The vault is the memory — keeping it current is not busywork, it's maintaining the system itself.

### Archiving

When Allan says something is done or asks to archive a note: (1) set its frontmatter `status: archived` and save; (2) move it to the Archive folder, same filename; (3) confirm what was archived and where. Always confirm before archiving. Never archive on your own initiative.

### Writing Rules

No em-dashes in marketing or published content (they're a strong "an AI wrote this" tell). Hyphens in normal compound words are fine.

### Daily Notes

Daily notes capture what happened across all of Allan's work sessions for a day. They live in `01 - Daily Notes/`, ideally sorted into month subfolders (`01 - Daily Notes/06 - June 2026/`) once the folder fills up. Filename `YYYY-MM-DD.md`. Frontmatter `status: active`, `project: personal`, `type: log`.

Start the body with a human-readable date heading (`# Monday, June 8, 2026`). Then, right after it, an **`## Index`** block: one bold-topic line per session/entry with a one-sentence outcome. The index makes a day with many entries scannable instead of a wall of prose. Then the entry body follows `01 - Daily Notes/Daily Note Template.md` — create every daily note FROM that template (What Got Done · What's Still In Progress · Decisions Made · Notes Touched · Profile Updates); never hand-roll one.

If today's note already exists from an earlier session, append a new session section (`## Session 2`, `## Evening Session`) and add a line to the Index block — don't overwrite. Timestamp each entry with Allan's local time.

#### Trigger 1: Wrap-Up Signal
Never ask Allan if he's done working. When he signals it ("I'm done," "calling it," "goodnight"), offer to create or update today's daily note. Always check the actual current date and time first — conversations can stay open overnight.

#### Trigger 2: Session Handoff (Replaces Full Yesterday Note Read)
At the start of every conversation, read `Session Handoff.md` (CLAUDE.md step 1 does this). This compressed rotating summary replaces reading yesterday's full daily note at boot.
- **If you have context the daily note is missing:** append a session section to it anyway — multiple AIs contribute to the same daily note.
- **If a task needs deep context from a specific day:** fetch that day's raw daily note on demand. Don't pre-load it at boot.

Session Handoff has a 10-entry cap with auto-eviction. Daily notes remain the complete forensic record. This split keeps boot cost constant regardless of vault age.

### Living Profile

This file is a living document. Update the profile sections as you learn new things about Allan through conversation. Updates happen silently and are logged in the daily note under "Profile Updates."

**You can update:** Key People · How I Think · Health · Personal Interests · Beliefs · Daily Routine.
**You must NOT update:** Who I Am (basic bio — only Allan changes it) · the project sections · What's Active Right Now (lives in Active Priorities) · My Preferences for Working with AI · Vault Rules for AI.
**Vault Structure is a special case:** never rewrite it on your own initiative, but when a folder is actually created, renamed, or removed, updating the map is part of that change — do it in the same pass.

Judgment: a passing mention is not a personality trait. Check for duplicates/contradictions; if new info contradicts an entry, update that entry rather than adding a second. Match existing tone. Never remove an entry unless explicitly contradicted. Fewer, higher-quality updates.

Log every profile update in the daily note's "Profile Updates" section (e.g. "**Personal Interests:** added woodworking").
