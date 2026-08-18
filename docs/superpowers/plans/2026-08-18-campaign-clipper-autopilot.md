# Campaign Clipper Autopilot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a daily autopilot loop to the campaign-clipper pipeline that finds fresh campaigns (no min-follower gate, <20% used), auto-adds them, builds clips from their content folders, uploads to the campaign-posting channels, and submits short URLs to the Clipster board via the opencli browser bridge — with a Telegram status report at the end.

**Architecture:** Three layers. The **pipeline** (`campaign-clipper-pipeline`) is a pure worker: a new `intake.py` stage auto-adds eligible campaigns, the existing `build → validate → upload → submit` chain does the work. **Milo** is the driver: runs pipeline stages, drives the **opencli browser bridge** for submissions and activity reads (against Allan's Chrome + Clipster session, never re-logged-in), and sends Telegram reports. The pipeline never calls opencli or Telegram itself.

**Tech Stack:** Python 3, sqlite3 (existing `clipper.db`), PyYAML, opencli 1.8.6 browser bridge (drives Allan's Chrome), Playwright NOT used, VPS Task Scheduler (MiloRoutines) for the daily trigger.

## Global Constraints

- **No Playwright.** Submit rides the opencli browser bridge only.
- **No YouTube source scraping.** Raw footage comes from campaign `content_folders`/`local_folders` only.
- **Campaign clips only to:** `capital_mindset`, `wealth_mindset`, `flick_shorts`, `moviegasm`, `NXS`. Skip campaigns whose niche maps to `chop_ug`, `rankdrop`, `the_other_guys`, `explaination`.
- **Intake rejects campaigns with:** min-followers/min-subscribers gates, min-views or engagement-percentage gates, non-YouTube platforms.
- **Fresh = progress < 20%** (from the discover card). Re-add the same campaign URL only once (`seen` set).
- **No gates on intake** — Telegram notifications inform, never block.
- **Upload caps respected:** `CLIPPER_MAX_PER_DAY` (5), `CLIPPER_MAX_PER_CAMPAIGN_PER_DAY` (3). Uploads only from `validated` clips; submits only from `uploaded` clips.
- **Per-campaign failures never block the cycle** — log + report + continue.
- Selectors (verified live 2026-08-18): card `button[id^=discover-campaign-card-…]`, `#submit-content-button`, `input#content-url`, `#submit-content-send-button`, result page `/activity/submissions`.
- opencli multi-profile: set `OPENCLI_PROFILE` (default `g5f9qrts`/`flow-1`) before browser commands. Extension-update notice pollutes stderr — filter stderr.
- Pipeline runtime root: `C:\Users\user\Desktop\Milo Video Factory\campaign-clipper-pipeline`; checkout: `artisan/campaign-clipper-pipeline`.
- Repo: `C:\Users\user\Desktop\milo-portable-system`, branch `main`, remote `origin`.

---

### Task 1: Intake rules + campaign-channel config

**Files:**
- Modify: `artisan/campaign-clipper-pipeline/config/clipper.yaml`
- Modify: `artisan/campaign-clipper-pipeline/src/config.py`

**Interfaces:**
- Consumes: existing `ClipperConfig._load_yaml()` pattern (`config/clipper.yaml`).
- Produces: `config.intake_reject_keywords` (list[str]), `config.intake_max_progress` (float, default 20.0), `config.campaign_channels` (list[str]), `config.opencli_session` (str, default `clipster`), `config.opencli_profile` (str, default `g5f9qrts`).

- [ ] **Step 1: Add the config block to `config/clipper.yaml`**

```yaml
# Autopilot intake rules. Data, not code: tighten/relax without touching src.
intake:
  max_progress_pct: 20          # only campaigns < this much used
  reject_keywords:
    - min followers
    - min followers per social profile
    - min subscribers
    - min views
    - minimum views
    - min engagement
    - engagement percentage
    - min. engagement
  campaign_channels:
    - capital_mindset
    - wealth_mindset
    - flick_shorts
    - moviegasm
    - NXS

opencli:
  session: clipster
  profile: g5f9qrts
```

- [ ] **Step 2: Add the failing test**

Create `tests/test_intake_config.py`:

```python
from src.config import config


def test_intake_blocks_follower_gate_keywords():
    assert any('min followers' in k for k in config.intake_reject_keywords)


def test_intake_max_progress_default():
    assert config.intake_max_progress == 20.0


def test_campaign_channels_exclude_legacy():
    assert 'capital_mindset' in config.campaign_channels
    assert 'chop_ug' not in config.campaign_channels
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_intake_config.py -v`
Expected: FAIL — `AttributeError: 'ClipperConfig' object has no attribute 'intake_reject_keywords'`

- [ ] **Step 4: Implement in `src/config.py`**

After the `hook_templates`/`banned_words` block (~line 188-189), add:

```python
        intake = raw.get('intake') or {}
        self.intake_reject_keywords = [
            str(k).strip().lower() for k in (intake.get('reject_keywords') or [])
            if str(k).strip()]
        self.intake_max_progress = float(intake.get('max_progress_pct') or 20)
        self.campaign_channels = [
            str(c).strip() for c in (intake.get('campaign_channels') or [])
            if str(c).strip()]
        opencli = raw.get('opencli') or {}
        self.opencli_session = str(opencli.get('session') or 'clipster').strip()
        self.opencli_profile = str(opencli.get('profile') or 'g5f9qrts').strip()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_intake_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add artisan/campaign-clipper-pipeline/config/clipper.yaml \
        artisan/campaign-clipper-pipeline/src/config.py \
        artisan/campaign-clipper-pipeline/tests/test_intake_config.py
git commit -m "feat(clipper): intake rules + campaign-channel + opencli config"
```

---

### Task 2: Campaign auto-intake module

**Files:**
- Create: `artisan/campaign-clipper-pipeline/src/intake.py`
- Modify: `artisan/campaign-clipper-pipeline/src/main.py`
- Test: `tests/test_intake.py`

**Interfaces:**
- Consumes: `clipster.list_campaigns(platform)` → list of card dicts (`id`, `name`, `url`, `progress`, `rate_per_1m`, …); `clipster.read_campaign(url)` → dict with `requirements`, `obligations`, `prohibitions`, `unknown_marks`, `card`; `compiler.compile_to_file(raw, campaign_id=…, name=…, url=…, card=…, use_model=False)` → `(CampaignSpec, Path)`; `ClipperDatabase.upsert_campaign(campaign_id, name, url, spec_dict, requirements)`; `config.campaign_spec_dir`, `config.intake_reject_keywords`, `config.intake_max_progress`.
- Produces: `intake.run(db) -> IntakeReport` where `IntakeReport` has `.added: list[CampaignSpec]`, `.rejected: list[dict]` (`{id, name, url, reasons: list[str]}`), `.seen: list[str]`, `.waiting_content: list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_intake.py`:

```python
from src.intake import gate_campaign, is_fresh


def test_gate_rejects_min_followers():
    req = {
        'obligations': ['Min Followers per Social Profile: 1000'],
        'prohibitions': [],
        'unknown_marks': [],
    }
    verdict = gate_campaign(req, reject=['min followers', 'min views'])
    assert 'min followers' in verdict['reasons']


def test_gate_rejects_min_views():
    req = {
        'obligations': ['Min Views for Earnings: 3000', 'US >= 40% audience'],
        'prohibitions': [],
        'unknown_marks': [],
    }
    verdict = gate_campaign(req, reject=['min followers', 'min views'])
    assert 'min views' in verdict['reasons']


def test_gate_passes_clean_campaign():
    req = {
        'obligations': ['Post 1 clip per day', 'MUST MENTION ROOBET'],
        'prohibitions': ['NO SPAM'],
        'unknown_marks': [],
    }
    verdict = gate_campaign(req, reject=['min followers', 'min views'])
    assert verdict['reasons'] == []


def test_is_fresh_threshold():
    assert is_fresh({'progress': 12}, max_progress=20)
    assert not is_fresh({'progress': 47}, max_progress=20)
    assert is_fresh({}, max_progress=20)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_intake.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.intake'`

- [ ] **Step 3: Implement `src/intake.py`**

```python
"""Autopilot campaign intake: find fresh, ungated campaigns and add them.

Wraps the existing board scraping and requirement compilation so the daily
loop can discover campaigns without a human. Eligibility rejection is a
keyword list from config — data, not code.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import clipster, compiler
from .config import config
from .spec import CampaignSpec
from .utils import safe_slug, setup_logger

logger = setup_logger(__name__)


@dataclass
class IntakeReport:
    added: List[CampaignSpec] = field(default_factory=list)
    rejected: List[Dict] = field(default_factory=list)
    seen: List[str] = field(default_factory=list)
    waiting_content: List[str] = field(default_factory=list)

    def describe(self) -> str:
        parts = [f'added={len(self.added)}',
                 f'rejected={len(self.rejected)}',
                 f'seen={len(self.seen)}',
                 f'waiting_content={len(self.waiting_content)}']
        return ' | '.join(parts)


def is_fresh(card: Dict, max_progress: float) -> bool:
    """True when the card reports < max_progress % used, or no progress at all."""
    progress = card.get('progress')
    if progress is None:
        return True
    try:
        return float(progress) < max_progress
    except (TypeError, ValueError):
        return True


def gate_campaign(req: Dict, reject: List[str]) -> Dict:
    """Return {'ok': bool, 'reasons': [...]} for a campaign's requirements.

    ``req`` is the dict from ``clipster.read_campaign``: obligations are the
    green-check rows, prohibitions the red-cross rows. A rejection keyword
    found in either is a hard reject.
    """
    reasons: List[str] = []
    blob = ' '.join(
        (req.get('obligations') or [])
        + (req.get('prohibitions') or [])
        + (req.get('unknown_marks') or [])).lower()
    for keyword in reject:
        if not keyword:
            continue
        if keyword in blob:
            reasons.append(keyword)
    return {'ok': not reasons, 'reasons': reasons}


def _known_urls(db) -> set:
    return {c.get('url') or '' for c in db.campaigns() if c.get('url')}


def run(db, platform: str = 'youtube',
        max_progress: Optional[float] = None,
        reject: Optional[List[str]] = None,
        seen_urls: Optional[set] = None) -> IntakeReport:
    """Scan the board, gate candidates, compile + persist fresh ones.

    Returns an ``IntakeReport`` the caller turns into a Telegram message.
    """
    report = IntakeReport()
    max_progress = max_progress if max_progress is not None \
        else config.intake_max_progress
    reject = reject if reject is not None \
        else config.intake_reject_keywords
    seen_urls = seen_urls if seen_urls is not None else _known_urls(db)

    cards = clipster.list_campaigns(platform)
    for card in cards or []:
        url = card.get('url') or ''
        cid = safe_slug(card.get('id') or card.get('name') or '')
        if not url or url in seen_urls:
            if url:
                report.seen.append(cid)
            continue
        seen_urls.add(url)
        if not is_fresh(card, max_progress):
            report.rejected.append(
                {'id': cid, 'name': card.get('name', ''), 'url': url,
                 'reasons': [f'progress {card.get("progress")}% >= '
                             f'{max_progress:g}%']})
            continue

        page = clipster.read_campaign(url)
        if not page:
            report.rejected.append(
                {'id': cid, 'name': card.get('name', ''), 'url': url,
                 'reasons': ['page unreadable']})
            continue
        verdict = gate_campaign(page, reject)
        if not verdict['ok']:
            report.rejected.append(
                {'id': cid, 'name': card.get('name', ''), 'url': url,
                 'reasons': verdict['reasons']})
            continue

        try:
            spec, path = compiler.compile_to_file(
                page['requirements'], campaign_id=cid,
                name=card.get('name', cid), url=url,
                card=page.get('card'), use_model=False)
        except Exception as exc:
            logger.error('INTAKE_COMPILE_FAILED url=%s error=%s', url, exc)
            report.rejected.append(
                {'id': cid, 'name': card.get('name', ''), 'url': url,
                 'reasons': [f'compile failed: {str(exc)[:80]}']})
            continue

        db.upsert_campaign(spec.id, spec.name, url, spec.to_dict(),
                           page['requirements'])
        report.added.append(spec)
        if not spec.sources.has_any():
            report.waiting_content.append(spec.id)
        logger.info('INTAKE_ADDED id=%s url=%s', spec.id, url)

    logger.info('INTAKE_DONE %s', report.describe())
    return report
```

- [ ] **Step 4: Wire a `--mode intake` into `src/main.py`**

Add import at top (with the other `from . import …`):

```python
from . import captions, cleanup, clipster, compiler, overlay as ov
from . import intake, renderer, sources
```

Add a mode function after `mode_campaigns`:

```python
def mode_intake(platform: str) -> int:
    report = intake.run(_db(), platform=platform)
    print(report.describe())
    for spec in report.added:
        print(f'  + {spec.id}: {spec.describe()}')
    for item in report.rejected:
        print(f'  - {item["id"]} ({item["url"]}): '
              + '; '.join(item['reasons']))
    for cid in report.waiting_content:
        print(f'  [waiting content] {cid}')
    for cid in report.seen:
        print(f'  [seen] {cid}')
    return 0
```

Register in `main()`:

```python
    if args.mode == 'campaigns':
        return mode_campaigns(args.platform)
    if args.mode == 'intake':
        return mode_intake(args.platform)
```

And add `'intake'` to the `--mode` choices list.

- [ ] **Step 5: Run the unit tests**

Run: `python -m pytest tests/test_intake.py tests/test_intake_config.py -v`
Expected: PASS (7 passed total)

- [ ] **Step 6: Smoke test intake against the live board**

Run (from `artisan/campaign-clipper-pipeline`):
`python -m src.main --mode intake --platform youtube`
Expected: prints a report — added/rejected/seen. Do not require a specific count; the board changes daily. If nothing is added because every candidate is gated or >20% used, that is a valid outcome — the report must still print without crashing.

- [ ] **Step 7: Commit**

```bash
git add artisan/campaign-clipper-pipeline/src/intake.py \
        artisan/campaign-clipper-pipeline/src/main.py \
        artisan/campaign-clipper-pipeline/tests/test_intake.py
git commit -m "feat(clipper): autopilot campaign intake stage"
```

---

### Task 3: Opencli browser submit wrapper

**Files:**
- Create: `artisan/campaign-clipper-pipeline/src/opencli_bridge.py`
- Modify: `artisan/campaign-clipper-pipeline/src/main.py`
- Test: `tests/test_opencli_bridge.py`

**Interfaces:**
- Consumes: `config.opencli_session`, `config.opencli_profile`.
- Produces: `opencli_bridge.run(cmd_args: list[str], timeout: int = 120) -> dict` (parsed JSON, `{}` on failure); `opencli_bridge.submit(campaign_url, video_url) -> dict` (`{'ok': bool, 'status': 'submitted'|'rejected'|'error', 'detail': str}`); `opencli_bridge.check_submissions() -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_opencli_bridge.py`:

```python
from src.opencli_bridge import build_submit_command, build_check_command


def test_submit_command_uses_session_and_profile():
    cmd = build_submit_command('clipster', 'g5f9qrts',
                               'https://clipster.gg/campaign/x',
                               'https://youtube.com/shorts/abc')
    assert 'opencli' in cmd[0]
    assert 'clipster' in cmd
    assert 'g5f9qrts' in cmd


def test_check_command_targets_activity():
    cmd = build_check_command('clipster', 'g5f9qrts')
    assert any('activity/submissions' in part for part in cmd)


def test_result_classifier():
    from src.opencli_bridge import classify_submission
    assert classify_submission('Submitted') == 'submitted'
    assert classify_submission('Ineligible') == 'rejected'
    assert classify_submission('') == 'unknown'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_opencli_bridge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.opencli_bridge'`

- [ ] **Step 3: Implement `src/opencli_bridge.py`**

```python
"""Submit to the Clipster board through the opencli browser bridge.

opencli drives Allan's Chrome (already logged into Clipster) via its browser
bridge. Milo calls this module; the pipeline never talks to a browser
directly. All selectors below were verified live on 2026-08-18.

Multiple Browser Bridge profiles connected makes opencli refuse commands, so
every call sets OPENCLI_PROFILE. The extension-update notice pollutes stderr;
it is filtered, never trusted.
"""

import json
import os
import shutil
import subprocess
from typing import Dict, List

from .config import config
from .utils import setup_logger

logger = setup_logger(__name__)

CARD = 'button[id^=discover-campaign-card-]'
OPEN_BUTTON = '#submit-content-button'
INPUT = '#content-url'
SEND_BUTTON = '#submit-content-send-button'
ACTIVITY_URL = '{base}/activity/submissions'


def build_submit_command(session: str, profile: str, campaign_url: str,
                         video_url: str) -> List[str]:
    """The exact opencli command sequence, as one shell-safe argv list.

    Uses opencli's find/click/type subcommands against the bound session.
    """
    return [
        'opencli', 'browser', session,
        'submit-seq', campaign_url, video_url,
        '--profile', profile,
    ]


def build_check_command(session: str, profile: str) -> List[str]:
    base = config.clipster_base
    return ['opencli', 'browser', session, 'open', ACTIVITY_URL.format(base=base),
            '--profile', profile]


def classify_submission(text: str) -> str:
    """'submitted' | 'rejected' | 'unknown' from a row's status text."""
    low = (text or '').lower()
    if any(word in low for word in ('submitted', 'approved', 'pending')):
        return 'submitted'
    if any(word in low for word in ('ineligible', 'rejected', 'invalid',
                                    'disapproved', 'declined')):
        return 'rejected'
    return 'unknown'


def run(cmd_args: List[str], timeout: int = 120) -> Dict:
    """Run an opencli browser command; return parsed JSON or {}."""
    env = dict(os.environ)
    env['OPENCLI_PROFILE'] = config.opencli_profile
    try:
        proc = subprocess.run(
            cmd_args, capture_output=True, text=True, timeout=timeout,
            env=env, encoding='utf-8', errors='replace')
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.error('OPENCLI_RUN_FAILED cmd=%s error=%s',
                     ' '.join(cmd_args[:4]), exc)
        return {}
    out = (proc.stdout or '').strip()
    # Discard the extension-update noise that opencli writes to stderr.
    if proc.returncode != 0:
        logger.error('OPENCLI_EXIT_%s cmd=%s stderr=%s',
                     proc.returncode, ' '.join(cmd_args[:4]),
                     (proc.stderr or '')[:200])
        return {}
    try:
        return json.loads(out) if out.startswith('{') else {}
    except json.JSONDecodeError:
        return {}


def submit(campaign_url: str, video_url: str,
           session: str = '', profile: str = '') -> Dict:
    """Submit one short URL to a campaign board. Returns result dict."""
    session = session or config.opencli_session
    profile = profile or config.opencli_profile
    cmd = build_submit_command(session, profile, campaign_url, video_url)
    result = run(cmd)
    text = str(result.get('status') or result.get('detail') or '')
    status = classify_submission(text)
    if not result:
        status = 'error'
    logger.info('SUBMIT_%s campaign=%s link=%s', status.upper(),
                campaign_url, video_url)
    return {'ok': status in ('submitted',), 'status': status,
            'detail': text or json.dumps(result)[:200]}


def check_submissions(session: str = '', profile: str = '') -> List[Dict]:
    """Open /activity/submissions and return parsed rows (best-effort)."""
    session = session or config.opencli_session
    profile = profile or config.opencli_profile
    cmd = build_check_command(session, profile)
    result = run(cmd)
    rows = result.get('rows') or result.get('submissions') or []
    logger.info('CHECK_SUBMISSIONS rows=%d', len(rows))
    return rows
```

> **Note for the implementer:** `build_submit_command` currently targets a `submit-seq` subcommand that must be implemented as a small opencli-side script (or as a Milo routine that issues the individual `find`/`click`/`type` steps). If implementing `submit-seq` inside opencli is out of scope for this plan, replace the body of `submit()` with the individual step sequence below — Milo drives it either way; the function is the seam.

```python
# Individual opencli steps (equivalent to submit-seq), each a run() call:
# 1. run(['opencli', 'browser', session, 'open', campaign_url, '--profile', profile])
# 2. find card:  run(['opencli', 'browser', session, 'find', '--css', CARD, '--limit', '1', '--profile', profile])
#    -> entry ref; then run(['opencli', 'browser', session, 'click', str(ref), '--profile', profile])
# 3. click OPEN_BUTTON: run(['opencli', 'browser', session, 'click', '--css', OPEN_BUTTON, '--profile', profile])
# 4. type INPUT: run(['opencli', 'browser', session, 'type', '--css', INPUT, '--text', video_url, '--profile', profile])
# 5. click SEND_BUTTON: run(['opencli', 'browser', session, 'click', '--css', SEND_BUTTON, '--profile', profile])
# 6. verify: run check_submissions(), classify rows.
```

- [ ] **Step 4: Run the unit tests**

Run: `python -m pytest tests/test_opencli_bridge.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add artisan/campaign-clipper-pipeline/src/opencli_bridge.py \
        artisan/campaign-clipper-pipeline/tests/test_opencli_bridge.py
git commit -m "feat(clipper): opencli browser submit wrapper"
```

---

### Task 4: Live submission verification (trust gate)

**Files:**
- None new (ad-hoc; uses existing campaign + opencli session)

**Interfaces:**
- Consumes: `opencli_bridge.submit()`, a real `config/campaigns/*.yaml` spec with a live URL (e.g. a fresh ungated campaign), and the `clipster` opencli session bound to Allan's Chrome.

- [ ] **Step 1: Pick one target**

Pick one campaign from `config/campaigns/` that is **currently live on the board and has no min-follower gate** (or add one via `--mode intake`). Note its URL.

- [ ] **Step 2: Run one real submission**

From `artisan/campaign-clipper-pipeline`:

```bash
$env:OPENCLI_PROFILE="g5f9qrts"
python -c "from src.opencli_bridge import submit; print(submit('<CAMPAIGN_URL>', 'https://www.youtube.com/shorts/<existing-public-short-id>'))"
```

Expected: `{'ok': True, 'status': 'submitted', ...}` OR a clearly classified rejected result. A rejected result is acceptable ONLY if the campaign legitimately rejects the channel (e.g. an eligibility wall) — the point is the call returns a real classification, not an opencli error.

- [ ] **Step 3: Confirm on the activity page**

Use `opencli_bridge.check_submissions()` or open `/activity/submissions` in the browser session. Expected: the submitted short appears with a status badge (Submitted / Ineligible / Pending).

- [ ] **Step 4: Report result to Allan**

State: mechanism verified, campaign used, status observed. If rejected, note the rejection reason and whether the campaign should be excluded from intake.

- [ ] **Step 5: Commit any selector/config fixes**

If a selector in `opencli_bridge.py` was wrong, fix it in `SELECTORS`-style constants and commit. No commit required if everything worked.

---

### Task 5: Milo daily routine + Telegram reporting

**Files:**
- Create: `artisan/campaign-clipper-pipeline/ROUTINE.md`
- Modify: `docs/superpowers/plans/` (routine definition documented, not code)

**Interfaces:**
- Consumes: `main.py` modes (`intake`, `build`, `upload`, `submit`, `status`, `links`); `opencli_bridge.submit()`; milo's Telegram channel (`milo send`).

- [ ] **Step 1: Write `ROUTINE.md` — the exact daily procedure Milo follows**

Include: exact commands, order, Telegram message templates for each stage, failure handling (per-campaign continue, manual queue heads-up), and the end-of-cycle status report format. Structure:

```markdown
# Campaign Clipper — Daily Routine (Milo)

Trigger: VPS Task Scheduler (MiloRoutines), once per day, e.g. 09:00 local.

## Stage 1 — Intake
Run: `python -m src.main --mode intake --platform youtube`
Notify Telegram: "📋 intake: added=N rejected=N seen=N waiting=N"
For each added campaign: "➕ <name> (<id>) — <describe>"

## Stage 2 — Build + validate per campaign
For each enabled campaign with content folders, in config/campaigns/*.yaml:
  Run: `python -m src.main --mode build --id <id> --count 3`
  Notify: "🎬 <id>: built K, validated V, rejected R"

## Stage 3 — Upload per validated clip
If uploads today < CLIPPER_MAX_PER_DAY (5):
  Run: `python -m src.main --mode upload --id <id> --clip <clip_id> --privacy public`
  Notify: "📤 <id> clip <clip_id> → <video_url>"

## Stage 4 — Submit via opencli browser
For each uploaded, un-submitted clip:
  Call `opencli_bridge.submit(<campaign_url>, <video_url>)`
  Notify: "✅ submitted <campaign> → <video_url>" or
          "⚠️ rejected/queued <campaign> → <video_url> (manual queue)"

## Stage 5 — Status report
Run: `python -m src.main --mode status`
Send Telegram: consolidated report (clips by status, uploads today, manual
queue length, disk usage).

## Failure rules
- Per-campaign failures: log + notify, continue cycle.
- No fresh campaigns: notify "no fresh campaigns", exit clean.
- Submit broken (opencli errors on every attempt): STOP submitting, notify,
  do not spam the board.
```

- [ ] **Step 2: Wire the Task Scheduler entry on the VPS**

On the VPS (Windows Server 2025, `MiloRoutines` already installed): add a daily trigger (09:00) that runs a `milo run` prompt executing this routine. Document the exact trigger command in `ROUTINE.md`. This step is executed by Milo on the VPS, not by the plan's implementer on this machine — note it as a manual VPS step.

- [ ] **Step 3: Commit `ROUTINE.md`**

```bash
git add artisan/campaign-clipper-pipeline/ROUTINE.md
git commit -m "docs(clipper): daily autopilot routine (Milo driver)"
```

---

### Task 6: Channel-assignment guard + niche→channel test

**Files:**
- Modify: `artisan/campaign-clipper-pipeline/src/main.py`
- Test: `tests/test_channel_assignment.py`

**Interfaces:**
- Consumes: `_channel_for(spec)` (existing, in `main.py`), `config.campaign_channels`.
- Produces: `_channel_for(spec)` now returns `''` (skip) when the resolved channel is not in `config.campaign_channels`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_channel_assignment.py`:

```python
from src.config import config
from src.spec import CampaignSpec
from src.main import _channel_for


def _spec(niche: str = '', upload_channel: str = '') -> CampaignSpec:
    return CampaignSpec.from_dict({
        'campaign': {'id': 't', 'name': 't', 'niche': niche,
                     'upload_channel': upload_channel},
    })


def test_channel_falls_back_to_niche_map():
    spec = _spec(niche='finance')
    assert _channel_for(spec) in config.campaign_channels


def test_non_campaign_channel_skips():
    spec = _spec(niche='luganda')
    assert _channel_for(spec) == ''


def test_explicit_upload_channel_respected():
    spec = _spec(upload_channel='NXS')
    assert _channel_for(spec) == 'NXS'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_channel_assignment.py -v`
Expected: FAIL — the legacy-niche case resolves to chop_ug (or empty depends on map) and returns that instead of `''`.

- [ ] **Step 3: Implement the guard**

In `src/main.py`, update `_channel_for`:

```python
def _channel_for(spec: CampaignSpec) -> str:
    """The channel key a campaign's clips upload to, or '' to skip.

    Explicit spec field wins, then the niche map, then the global env value.
    An empty result means the campaign is not eligible for posting (its niche
    maps to a shorts/ranking-only channel) — skip with a Telegram notice.
    """
    if spec.upload_channel:
        channel = spec.upload_channel
    else:
        mapped = config.channel_for_niche(spec.niche)
        channel = mapped or config.upload_channel
    if channel and channel not in config.campaign_channels:
        logger.warning('CHANNEL_NOT_CAMPAIGN channel=%s campaign=%s '
                       'skip', channel, spec.id)
        return ''
    return channel
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_channel_assignment.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: all pass (existing + new)

- [ ] **Step 6: Commit**

```bash
git add artisan/campaign-clipper-pipeline/src/main.py \
        artisan/campaign-clipper-pipeline/tests/test_channel_assignment.py
git commit -m "feat(clipper): skip non-campaign channels in upload assignment"
```

---

### Task 7: End-to-end dry run

**Files:**
- None new.

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Dry-run the full loop with publish disabled**

From `artisan/campaign-clipper-pipeline`:

```bash
# Intake (safe: only writes config + DB rows)
python -m src.main --mode intake --platform youtube

# Build + validate one enabled campaign, no upload/submit
python -m src.main --mode run --id <enabled-campaign-id> --count 3
```

Expected: clips built and validated, `--mode run` prints "Auto-upload is off" and lists the `--mode upload` commands. No uploads, no submissions.

- [ ] **Step 2: Verify DB state**

Run: `python -m src.main --mode status`
Expected: `validated` clips present, uploads in last 24h = 0.

- [ ] **Step 3: Confirm channel resolution**

Run: `python -m src.main --mode links` (after a manual `--mode record-link` on a test clip, or note that none exist yet). Expected: no crash; empty or partial listing.

- [ ] **Step 4: Report to Allan**

State what the dry run proved: intake gates work, build/validate works, channel guard works, and what remains for the VPS (Task Scheduler trigger + live submissions via Task 4).

- [ ] **Step 5: Final commit (if any stragglers)**

```bash
git status --short
```

Commit anything unexpected, or note nothing to commit.