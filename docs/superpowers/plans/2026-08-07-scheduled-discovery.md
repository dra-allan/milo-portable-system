# Scheduled Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `--mode schedule` actually discover videos on the configured cron times, pick the right niche for each, and respect a per-run budget so it doesn't blow YouTube quota or burn hours of transcription.

**Architecture:** Channel discovery already works (`YouTubeDownloader.search_videos_by_channel`, yt-dlp flat playlist — no API key). The gaps are all in the orchestration layer: `run_niche()` still claims discovery is a stub, there is no dedup against already-processed videos at discovery time, no duration/negative-keyword filtering, no global per-run budget across niches, and no way to preview a run without doing it. We add a discovery module (candidate fetch → dedup → filter → pick), rewire `run_niche()` and the scheduler job to use it, add a `--mode discover` dry-run, and add `SCHEDULE_MAX_VIDEOS` (global cap per run) + `DISCOVERY_LOOKBACK` (candidates per channel).

**Tech Stack:** Python 3.13, yt-dlp (already vendored), APScheduler (already vendored), stdlib only beyond that. No new dependencies. No API key needed for discovery.

## Global Constraints

- **No new dependencies.** yt-dlp + APScheduler are already in the repo. Everything else must be stdlib.
- **Windows paths.** Run from `artisan\youtube-shorts-pipeline`; config paths resolve against project root, never CWD.
- **One fix = one commit = one push** (Allan's standing rule). Commit and push after each task's tests pass.
- **No secrets in docs.** Never write channel tokens, YOUTUBE_API_KEY, or OAuth paths into the plan or code comments.
- **Self-contained.** No imports from outside the repo. Vendored code only.
- **`published_after` argument to `search_videos_by_channel` is ignored by yt-dlp** (it only lists recent N). Don't build logic on it.
- **DB dedup is the source of truth.** A video already in `processed_videos` must never be re-downloaded or re-transcribed unless `--force`.
- **Quota reality:** ~10,000 units/day, one upload ~1,600 → ~6 uploads/day. The upload cap (`UPLOAD_MAX_PER_RUN=5`) already exists; this plan adds the *discovery* budget on top so we don't download 22 videos when we can only upload 5.

---

### Task 1: Discovery module — candidates, dedup, filters, pick

**Files:**
- Create: `artisan/youtube-shorts-pipeline/src/discovery.py`
- Test: `artisan/youtube-shorts-pipeline/tests/test_discovery.py`

**Interfaces:**
- Consumes: `config` (from `.config`), `PipelineDatabase` (from `.database`), `YouTubeDownloader.search_videos_by_channel(channel_id, max_results)` (from `.downloader`).
- Produces: `DiscoveryResult` dataclass and two functions used by Tasks 2-4:
  - `discover_candidates(pipeline, niche, max_videos, lookback) -> DiscoveryResult`
  - `pick_videos(result, max_videos) -> List[Dict]` — may be folded into Task 2 if discovery and picking naturally merge.

**Context for the engineer:**
- `search_videos_by_channel(channel_id, published_after='', max_results=10)` returns a list of dicts `{'id', 'title', 'duration', 'url', 'channel_id'}`. Channel values in `niches.yaml` are either `UC...` IDs, `@handle` strings, or full URLs — the downloader handles all three. A placeholder `UCXXXXX` means "no real channel yet"; those must be skipped.
- `config.get_niche_config(niche)` returns dict with keys `channels` (list), `keywords`, `negative_keywords`, `min_duration`, `max_duration`, `min_score`, `min_views`. `config.get_niche_channel(niche)` returns the bound upload channel ('' if unbound) — NOT used at discovery time, only the source channels in `niche_config['channels']` are.
- `PipelineDatabase` already has `is_video_processed(video_id) -> bool`.
- Existing test conventions: `tests/test_downloader_fetch.py` fakes yt-dlp and isolates config with `_isolate_config(tmp)` setting `TEMP_DIR/DATA_DIR/LOG_DIR/SHORTS_DIR/DB_PATH` env vars before import. Mirror that.

- [ ] **Step 1: Write the failing test**

`artisan/youtube-shorts-pipeline/tests/test_discovery.py`:

```python
"""Tests for scheduled discovery: dedup, filtering, and pick logic."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _isolate_config(tmp: Path):
    os.environ['TEMP_DIR'] = str(tmp / 'temp')
    os.environ['DATA_DIR'] = str(tmp / 'data')
    os.environ['LOG_DIR'] = str(tmp / 'logs')
    os.environ['SHORTS_DIR'] = str(tmp / 'shorts')
    os.environ['DB_PATH'] = str(tmp / 'data' / 'test.db')


class FakeDownloader:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search_videos_by_channel(self, channel_id, published_after='', max_results=10):
        self.calls.append({'channel': channel_id, 'max_results': max_results})
        return [r for r in self.results if r['channel_id'] == channel_id][:max_results]


class FakeDB:
    def __init__(self, processed_ids=()):
        self.processed = set(processed_ids)

    def is_video_processed(self, video_id):
        return video_id in self.processed


class TestDiscovery(unittest.TestCase):
    def test_skips_already_processed_videos(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            from src.config import config
            from src.discovery import discover_candidates

            vids = [
                {'id': 'aaa11111111', 'title': 'Old one', 'duration': 3600,
                 'channel_id': '@ch1'},
                {'id': 'bbb22222222', 'title': 'Fresh one', 'duration': 1800,
                 'channel_id': '@ch1'},
            ]
            dl = FakeDownloader(vids)
            db = FakeDB(processed_ids=['aaa11111111'])
            config.niches = {'test_niche': {'channels': ['@ch1']}}

            result = discover_candidates(dl, db, 'test_niche', max_videos=5, lookback=10)

            ids = [c['id'] for c in result.candidates]
            self.assertNotIn('aaa11111111', ids)
            self.assertIn('bbb22222222', ids)
            self.assertEqual(result.skipped_already_processed, ['aaa11111111'])

    def test_filters_by_duration_band(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            from src.discovery import discover_candidates

            vids = [
                {'id': 'ccc33333333', 'title': 'Too short', 'duration': 120,
                 'channel_id': '@ch1'},
                {'id': 'ddd44444444', 'title': 'Just right', 'duration': 900,
                 'channel_id': '@ch1'},
                {'id': 'eee55555555', 'title': 'Too long', 'duration': 9000,
                 'channel_id': '@ch1'},
            ]
            dl = FakeDownloader(vids)
            db = FakeDB()
            config.niches = {'test_niche': {'channels': ['@ch1'],
                                            'min_duration': 300, 'max_duration': 7200}}

            result = discover_candidates(dl, db, 'test_niche', max_videos=5, lookback=10)

            ids = [c['id'] for c in result.candidates]
            self.assertEqual(ids, ['ddd44444444'])

    def test_filters_by_negative_keywords(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            from src.discovery import discover_candidates

            vids = [
                {'id': 'fff66666666', 'title': 'Live stream: full show', 'duration': 3600,
                 'channel_id': '@ch1'},
                {'id': 'ggg77777777', 'title': 'The real interview', 'duration': 3600,
                 'channel_id': '@ch1'},
            ]
            dl = FakeDownloader(vids)
            db = FakeDB()
            config.niches = {'test_niche': {'channels': ['@ch1'],
                                            'negative_keywords': ['livestream', 'live stream', 'clip']}}

            result = discover_candidates(dl, db, 'test_niche', max_videos=5, lookback=10)

            ids = [c['id'] for c in result.candidates]
            self.assertEqual(ids, ['ggg77777777'])

    def test_skips_placeholder_channels(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            from src.discovery import discover_candidates

            dl = FakeDownloader([])
            db = FakeDB()
            config.niches = {'test_niche': {'channels': ['UCXXXXX', '@realchannel']}}

            result = discover_candidates(dl, db, 'test_niche', max_videos=5, lookback=10)

            queried = [c['channel'] for c in dl.calls]
            self.assertNotIn('UCXXXXX', queried)
            self.assertIn('@realchannel', queried)

    def test_returns_empty_when_niche_has_no_channels(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            from src.discovery import discover_candidates

            dl = FakeDownloader([])
            db = FakeDB()
            config.niches = {'empty_niche': {}}

            result = discover_candidates(dl, db, 'empty_niche', max_videos=5, lookback=10)

            self.assertEqual(result.candidates, [])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_discovery.py -q`
Expected: FAIL with `ModuleNotFoundError: src.discovery` (module doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

`artisan/youtube-shorts-pipeline/src/discovery.py`:

```python
"""Scheduled discovery: which videos should this run pick up?

The channel listing is cheap (yt-dlp flat playlist, metadata only). The
expensive parts -- download, transcription, rendering -- are downstream and
must never run on a video we already processed or that can't produce a usable
clip. So discovery is a pure filter pipeline: fetch candidates per channel,
drop placeholders, drop already-processed IDs, drop out-of-band durations,
drop negative-keyword titles, then rank and slice to the budget.
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class DiscoveryResult:
    candidates: List[Dict] = field(default_factory=list)
    skipped_already_processed: List[str] = field(default_factory=list)
    skipped_duration: List[str] = field(default_factory=list)
    skipped_negative_keywords: List[str] = field(default_factory=list)
    channels_queried: List[str] = field(default_factory=list)


def discover_candidates(downloader, db, niche, max_videos: int, lookback: int) -> DiscoveryResult:
    """Return the videos a scheduled run should process for `niche`.

    Args:
        downloader: has `search_videos_by_channel(channel_id, published_after='',
            max_results=10)`.
        db: has `is_video_processed(video_id) -> bool`.
        niche: niche name; its config is read from `config.get_niche_config`.
        max_videos: how many videos to keep for the run.
        lookback: how many recent videos to pull per channel before filtering
            (must be >= max_videos so dedup can't starve the result).
    """
    from .config import config

    cfg = config.get_niche_config(niche)
    channels = [c for c in (cfg.get('channels') or [])
                if c and not str(c).startswith('UCXXXXX')]

    result = DiscoveryResult()
    lookback = max(lookback, max_videos)

    for channel in channels:
        result.channels_queried.append(channel)
        try:
            found = downloader.search_videos_by_channel(
                channel, published_after='', max_results=lookback
            )
        except Exception:
            continue
        for entry in (found or []):
            vid = (entry or {}).get('id')
            if not vid:
                continue
            if db.is_video_processed(vid):
                result.skipped_already_processed.append(vid)
                continue

            duration = entry.get('duration') or 0
            min_dur = int(cfg.get('min_duration') or 0)
            max_dur = int(cfg.get('max_duration') or 0)
            if min_dur and duration and duration < min_dur:
                result.skipped_duration.append(vid)
                continue
            if max_dur and duration and duration > max_dur:
                result.skipped_duration.append(vid)
                continue

            title = str(entry.get('title') or '').lower()
            neg = [str(k).lower() for k in (cfg.get('negative_keywords') or []) if k]
            if any(k in title for k in neg):
                result.skipped_negative_keywords.append(vid)
                continue

            result.candidates.append(entry)

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_discovery.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add artisan/youtube-shorts-pipeline/src/discovery.py artisan/youtube-shorts-pipeline/tests/test_discovery.py
git commit -m "feat(shorts): scheduled discovery filter pipeline (dedup, duration, negative keywords)"
git push origin main:main
```

---

### Task 2: Wire discovery into `run_niche` + add `--mode discover` dry-run

**Files:**
- Modify: `artisan/youtube-shorts-pipeline/src/main.py:683-719` (`run_niche`), `src/main.py:913-952` (`build_parser`, add `discover` to `--mode` choices), `src/main.py:955-1022` (`main`, add `discover` branch).
- Test: `artisan/youtube-shorts-pipeline/tests/test_schedule_mode.py`

**Interfaces:**
- Consumes: `discover_candidates` from `.discovery` (Task 1).
- Produces: `ShortsPipeline.run_niche(niche, max_videos=1, lookback=None) -> int` returning the count of videos actually started; `run_discover_mode(pipeline, args) -> int` printing a dry-run report.

**Context for the engineer:**
- `run_niche` currently logs "Channel discovery needs the YouTube Data API ... still a stub" and "No videos discovered ... Channel discovery is not implemented yet". Those messages are wrong — delete the misleading text and log the real per-channel/query/skip counts.
- `run_niche` currently loops channels and calls `search_videos_by_channel` directly. Replace that block with `discover_candidates`, then call `process_video_for_shorts(video_id, niche)` for the top `max_videos` candidates.
- `process_video_for_shorts` already returns True/False and logs `Video ... was already processed` when deduped — that is fine to keep as a second guard, but discovery already filters, so it should rarely trigger.
- New mode `discover`: for each niche (or `--niche`), print channels queried, candidates found, and the skip reasons, WITHOUT downloading or processing anything. Exit 0.
- Default `--mode` stays `once`; `discover` is a new value in the choices list.

- [ ] **Step 1: Write the failing test**

`artisan/youtube-shorts-pipeline/tests/test_schedule_mode.py`:

```python
"""Tests for scheduled mode wiring: run_niche uses discovery, discover is a dry run."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _isolate_config(tmp: Path):
    os.environ['TEMP_DIR'] = str(tmp / 'temp')
    os.environ['DATA_DIR'] = str(tmp / 'data')
    os.environ['LOG_DIR'] = str(tmp / 'logs')
    os.environ['SHORTS_DIR'] = str(tmp / 'shorts')
    os.environ['DB_PATH'] = str(tmp / 'data' / 'test.db')


class FakeDownloader:
    def __init__(self, results):
        self.results = results

    def search_videos_by_channel(self, channel_id, published_after='', max_results=10):
        return [r for r in self.results if r['channel_id'] == channel_id][:max_results]


class FakeDB:
    def __init__(self):
        self.processed = set()
        self.recorded = []

    def is_video_processed(self, video_id):
        return video_id in self.processed

    def record_video(self, video_id, title, niche, duration=0,
                     channel_id='', published_at=None):
        self.recorded.append((video_id, niche))


class FakeProcessor:
    def find_highlight_segments(self, *a, **k):
        return []


class TestRunNicheUsesDiscovery(unittest.TestCase):
    def test_run_niche_returns_processed_count_and_calls_process(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            from src.config import config
            config.niches = {'test_niche': {'channels': ['@ch1']}}

            from src.main import ShortsPipeline

            pipeline = ShortsPipeline.__new__(ShortsPipeline)
            pipeline.config = config
            pipeline.processor = FakeProcessor()
            pipeline.db = FakeDB()
            pipeline._uploaders = {}
            pipeline.upload_enabled = False
            pipeline.transcript_dir = Path(td) / 'transcripts'
            pipeline.transcript_dir.mkdir(parents=True, exist_ok=True)
            pipeline.clip_plan_dir = Path(td) / 'clip_plans'
            pipeline.clip_plan_dir.mkdir(parents=True, exist_ok=True)
            pipeline.stats = {'videos_processed': 0, 'shorts_created': 0,
                              'shorts_uploaded': 0, 'errors': 0}
            pipeline._whisper_model = 'tiny'
            pipeline._downloader = FakeDownloader([
                {'id': 'hhh88888888', 'title': 'Pick me', 'duration': 900,
                 'channel_id': '@ch1'},
            ])

            seen = []
            pipeline.process_video_for_shorts = lambda vid, niche, force=False, local_only=False: (seen.append(vid) or True)

            from src.discovery import discover_candidates
            import src.main as main_mod
            pipeline.run_niche('test_niche', max_videos=1)

            self.assertEqual(seen, ['hhh88888888'])


class TestDiscoverModeIsDryRun(unittest.TestCase):
    def test_discover_prints_but_never_processes(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            from src.config import config
            config.niches = {'test_niche': {'channels': ['@ch1']}}

            from src.main import run_discover_mode

            class Args:
                niche = 'test_niche'
                all = False

            class FakePipeline:
                def __init__(self):
                    self._downloader = FakeDownloader([
                        {'id': 'iii99999999', 'title': 'Candidate', 'duration': 900,
                         'channel_id': '@ch1'},
                    ])
                    self.db = FakeDB()

            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = run_discover_mode(FakePipeline(), Args())

            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn('iii99999999', out)
            self.assertIn('test_niche', out)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_schedule_mode.py -q`
Expected: FAIL — `run_niche` still uses the old channel-loop body and doesn't return a count; `run_discover_mode` doesn't exist.

- [ ] **Step 3: Write minimal implementation**

Replace the body of `run_niche` in `src/main.py`:

```python
def run_niche(self, niche: str, max_videos: int = 1,
              lookback: Optional[int] = None) -> int:
    """Process the best `max_videos` videos discovered for a niche.

    Discovery pulls the latest N per channel, drops placeholders, already-
    processed IDs, out-of-band durations and negative-keyword titles, then
    ranks and slices to the budget. Returns how many videos were started.
    """
    from .discovery import discover_candidates

    lookback = lookback or getattr(self.config, 'discovery_lookback', 10)
    result = discover_candidates(self.downloader, self.db, niche,
                                 max_videos=max_videos, lookback=lookback)

    for skip in result.skipped_already_processed:
        logger.info("Niche '%s': %s already processed -- skipping", niche, skip)
    if result.skipped_duration:
        logger.info("Niche '%s': %d outside duration band -- skipping",
                    niche, len(result.skipped_duration))
    if result.skipped_negative_keywords:
        logger.info("Niche '%s': %d negative-keyword titles -- skipping",
                    niche, len(result.skipped_negative_keywords))

    if not result.candidates:
        logger.info("Niche '%s': no new discoverable videos (queried %d channel(s))",
                    niche, len(result.channels_queried))
        return 0

    candidates = result.candidates[:max_videos]
    logger.info("Niche '%s': processing %d of %d discovered",
                niche, len(candidates), len(result.candidates))
    started = 0
    for entry in candidates:
        vid = entry['id']
        ok = self.process_video_for_shorts(vid, niche)
        if ok:
            started += 1
    return started
```

Add `run_discover_mode` as a module-level function in `src/main.py` (place near `run_stats_mode`):

```python
def run_discover_mode(pipeline: 'ShortsPipeline', args) -> int:
    """Dry-run discovery: print what a scheduled run would pick, do nothing else."""
    from .discovery import discover_candidates

    niches = [args.niche] if args.niche else config.niche_names()
    if not niches:
        print("No niches configured.")
        return 1

    lookback = getattr(config, 'discovery_lookback', 10)
    for niche in niches:
        result = discover_candidates(pipeline.downloader, pipeline.db, niche,
                                     max_videos=1, lookback=lookback)
        print(f"\n[{niche}] channels queried: {len(result.channels_queried)}")
        if result.skipped_already_processed:
            print(f"  skipped (already processed): {len(result.skipped_already_processed)}")
        if result.skipped_duration:
            print(f"  skipped (duration band):     {len(result.skipped_duration)}")
        if result.skipped_negative_keywords:
            print(f"  skipped (negative keywords): {len(result.skipped_negative_keywords)}")
        for c in result.candidates[:10]:
            dur = c.get('duration') or 0
            print(f"  CANDIDATE {c['id']}  {dur:>7.0f}s  {str(c.get('title'))[:50]}")
        if not result.candidates:
            print("  (no new candidates)")
    return 0
```

Update `build_parser`: change `--mode` choices to `['once', 'schedule', 'test', 'library', 'stats', 'discover']` and add help text for `discover`.

Update `main()`: after the `args.mode == 'stats'` branch add:

```python
    if args.mode == 'discover':
        return run_discover_mode(pipeline, args)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_schedule_mode.py tests/test_discovery.py -q`
Expected: all pass.

Also run the full suite to catch regressions:
`python -m pytest -q` — expect 42 existing + new all green.

- [ ] **Step 5: Commit**

```bash
git add artisan/youtube-shorts-pipeline/src/main.py artisan/youtube-shorts-pipeline/tests/test_schedule_mode.py
git commit -m "feat(shorts): run_niche uses discovery filter; add --mode discover dry-run"
git push origin main:main
```

---

### Task 3: Per-run global budget + discovery config knobs

**Files:**
- Modify: `artisan/youtube-shorts-pipeline/src/config.py:156-172` (add `discovery_lookback`, `schedule_max_videos`), `src/main.py:1097-1147` (`_run_schedule`), `config/.env.template` (new knobs), `artisan/youtube-shorts-pipeline/.env` (same knobs).
- Test: `artisan/youtube-shorts-pipeline/tests/test_schedule_mode.py` (extend)

**Interfaces:**
- Consumes: `run_niche(niche, max_videos, lookback) -> int` (Task 2).
- Produces: `config.discovery_lookback` (int, default 10), `config.schedule_max_videos` (int, default 3). `_run_schedule` returns the run's summary.

**Context for the engineer:**
- Today the scheduled job does `for niche in niches: pipeline.run_niche(niche, max_videos=args.videos)`. With 22 niches and `--videos 1` that's up to 22 full download+transcribe+render cycles per scheduled run — while `UPLOAD_MAX_PER_RUN=5` means at most ~5 get published. That's the quota/CPU blow-up this task fixes.
- Add `SCHEDULE_MAX_VIDEOS` (default 3): the maximum number of videos the whole scheduled run may start across ALL niches. `run_niche` already returns a count; the scheduler accumulates it and stops iterating niches once the budget is exhausted.
- Add `DISCOVERY_LOOKBACK` (default 10): candidates pulled per channel before filtering. Must stay >= `SCHEDULE_MAX_VIDEOS` so dedup can't starve the pick.

- [ ] **Step 1: Write the failing test**

Extend `tests/test_schedule_mode.py`:

```python
class TestScheduleBudget(unittest.TestCase):
    def test_scheduler_stops_after_global_budget(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            from src.config import config
            config.niches = {'a': {'channels': ['@ch1']}, 'b': {'channels': ['@ch2']}}
            config.schedule_max_videos = 1

            from src.main import _run_scheduled_sweep

            class Args:
                niche = None
                videos = 5

            calls = []
            class FakePipeline:
                def run_niche(self, niche, max_videos=1, lookback=None):
                    calls.append(niche)
                    return 1 if niche == 'a' else 0

            _run_scheduled_sweep(FakePipeline(), Args())
            self.assertEqual(calls, ['a'])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_schedule_mode.py::TestScheduleBudget -q`
Expected: FAIL — `_run_scheduled_sweep` doesn't exist.

- [ ] **Step 3: Write minimal implementation**

In `config.py`, inside `__init__` after the upload block (around line 172), add:

```python
        # --- Scheduled discovery -----------------------------------------
        # Candidates pulled per channel before dedup/filtering. Must be >=
        # schedule_max_videos so already-processed videos can't starve a run.
        self.discovery_lookback = self._int('DISCOVERY_LOOKBACK', 10, minimum=1)
        # Global cap on videos STARTED per scheduled run across all niches.
        # Quota: ~10k units/day, one upload ~1600 -> ~6 uploads/day. Discovery
        # and transcription cost real time/money, so default to 3 videos/run.
        self.schedule_max_videos = self._int('SCHEDULE_MAX_VIDEOS', 3, minimum=1)
```

Add a module-level helper in `src/main.py`:

```python
def _run_scheduled_sweep(pipeline, args, budget: Optional[int] = None) -> int:
    """Run every niche up to a global per-run video budget.

    Each run_niche returns how many videos it started; the sweep stops once
    the budget is exhausted so 22 niches can't trigger 22 download cycles
    when only a handful will be uploaded.
    """
    budget = budget if budget is not None else config.schedule_max_videos
    niches = [args.niche] if args.niche else config.niche_names()
    started_total = 0
    for niche in niches:
        if started_total >= budget:
            logger.info("Scheduled sweep budget (%d videos) exhausted", budget)
            break
        remaining = budget - started_total
        started = pipeline.run_niche(niche, max_videos=remaining)
        started_total += started
    logger.info("Scheduled sweep started %d video(s)", started_total)
    return started_total
```

Replace the `job()` body in `_run_schedule`:

```python
    def job():
        try:
            _run_scheduled_sweep(pipeline, args)
            pipeline.report()
        except Exception as exc:
            logger.error("Scheduled sweep failed: %s", exc, exc_info=True)
```

(The per-niche `run_niche` already catches discovery errors internally, and `process_video_for_shorts` catches processing errors, so the job-level try/except is a last resort.)

In `config/.env.template`, under the Scheduling block (line ~73-75), add:

```
# Videos started per scheduled run across ALL niches (quota guard).
SCHEDULE_MAX_VIDEOS=3
# Candidates pulled per channel before dedup/filtering (>= SCHEDULE_MAX_VIDEOS).
DISCOVERY_LOOKBACK=10
```

In `artisan/youtube-shorts-pipeline/.env`, append the same two lines.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_schedule_mode.py tests/test_discovery.py -q`
Expected: all pass. Then full suite `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add artisan/youtube-shorts-pipeline/src/config.py artisan/youtube-shorts-pipeline/src/main.py artisan/youtube-shorts-pipeline/config/.env.template artisan/youtube-shorts-pipeline/.env artisan/youtube-shorts-pipeline/tests/test_schedule_mode.py
git commit -m "feat(shorts): global scheduled-run video budget + discovery lookback"
git push origin main:main
```

---

### Task 4: `--mode test` reports discovery readiness + live dry-run smoke check

**Files:**
- Modify: `artisan/youtube-shorts-pipeline/src/main.py:799-904` (`run_test_mode`).
- No new test file; extend `tests/test_schedule_mode.py` if useful (config-only assertions are enough).

**Interfaces:**
- Consumes: `config.discovery_lookback`, `config.schedule_max_videos`, `config.niche_names()`.
- Produces: additional `[ok]/[warn]` lines in the test-mode output.

**Context for the engineer:**
- The user runs `--mode test` first to see if the box is ready. It currently prints channels + unbound niches but says nothing about whether the scheduler will find anything. Add:
  - number of niches with real (non-placeholder) channels,
  - the scheduled-run budget (`SCHEDULE_MAX_VIDEOS`) and lookback,
  - the scheduled cron times from `RUN_TIMES`.
- This is a reporting-only change; no new logic.

- [ ] **Step 1: Write the failing assertion**

This task is pure reporting. The verification is a manual run rather than a unit test. Add a tiny smoke test to `tests/test_schedule_mode.py` anyway so the config keys are locked in:

```python
class TestDiscoveryConfig(unittest.TestCase):
    def test_discovery_config_keys_exist(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            from src.config import config
            self.assertTrue(hasattr(config, 'discovery_lookback'))
            self.assertTrue(hasattr(config, 'schedule_max_videos'))
            self.assertGreaterEqual(config.discovery_lookback, config.schedule_max_videos)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_schedule_mode.py::TestDiscoveryConfig -q`
Expected: FAIL — `config.discovery_lookback` missing (before Task 3). Run this step BEFORE Task 3 lands, or reorder so this test is written first.

- [ ] **Step 3: Write implementation**

In `run_test_mode`, after the upload/channels block (around line 876), add:

```python
    # Scheduled discovery readiness
    real = [n for n in config.niche_names()
            if any(c and not str(c).startswith('UCXXXXX')
                   for c in (config.get_niche_config(n).get('channels') or []))]
    print(f"  [{'ok' if real else 'warn'}] discovery: {len(real)}/{len(config.niche_names())} "
          f"niches have real channels (lookback {config.discovery_lookback}, "
          f"budget {config.schedule_max_videos} videos/run)")
    run_times = os.getenv('RUN_TIMES', '0 9 * * *,0 14 * * *,0 19 * * *')
    print(f"  [ok]   schedule: {run_times}")
```

- [ ] **Step 4: Run test + smoke check to verify**

Run: `python -m pytest tests/test_schedule_mode.py -q`
Run: `python src/main.py --mode test` — expect the two new lines, everything else unchanged.

- [ ] **Step 5: Commit**

```bash
git add artisan/youtube-shorts-pipeline/src/main.py artisan/youtube-shorts-pipeline/tests/test_schedule_mode.py
git commit -m "feat(shorts): test mode reports discovery + schedule readiness"
git push origin main:main
```

---

### Task 5: End-to-end verification on one real niche

**Files:** none (verification only).

**Context for the engineer:**
- The unit tests fake the network. The final proof is a real discovery run. `flick_shorts` and `capital_mindset` have real channel lists (Diary of a CEO, Joe Rogan, Hormozi, etc.) and upload bindings. Run the dry-run first (network metadata only, ~no cost), then a bounded real run with upload disabled.

- [ ] **Step 1: Dry-run discovery on a bound niche**

Run: `python src/main.py --mode discover --niche flick_shorts`
Expected: prints channels queried, skip counts, and candidate IDs/titles. No downloads, no transcription. If yt-dlp rate-limits, the per-channel try/except in `discover_candidates` logs and continues — verify a handful of channels still yield candidates.

- [ ] **Step 2: Bounded real run, no upload**

Run: `python src/main.py --mode once --niche flick_shorts --videos 1 --no-upload --max-source-minutes 15`
Expected: picks the top candidate, downloads (audio-only, ~40MB), transcribes first 15 min, renders clips, keeps them local. Verify `data/shorts/<title>/` has MP4s and the DB has 1 new processed video.

- [ ] **Step 3: Confirm dedup on a second run**

Run: `python src/main.py --mode once --niche flick_shorts --videos 1 --no-upload`
Expected: logs "already processed -- skipping" and starts 0 videos (the same top candidate is filtered by discovery before any download).

- [ ] **Step 4: Report results and stop**

Report what worked, the real per-run wall-clock for one video (download + 15-min transcribe + render), and whether the scheduled budget feels right. Do NOT run `--mode schedule` unattended without Allan's go-ahead — it now actually does work.

---

## Self-Review

**1. Spec coverage:**
- Stale "stub" messaging → Task 2 removes the misleading logs in `run_niche`. ✓
- Real dedup at discovery → Task 1 `skipped_already_processed` + DB check. ✓
- Duration + negative-keyword filtering → Task 1. ✓
- Global per-run budget → Task 3 `_run_scheduled_sweep` + `SCHEDULE_MAX_VIDEOS`. ✓
- Preview without running → Task 2 `--mode discover`. ✓
- Test-mode readiness signal → Task 4. ✓
- Real-network proof → Task 5. ✓

**2. Placeholder scan:** All code blocks are concrete. The only human step is Task 5 which is inherently manual. The `published_after` argument is passed explicitly in Task 1's call and documented as ignored — no logic depends on it. ✓

**3. Type consistency:** `discover_candidates(downloader, db, niche, max_videos, lookback)` — same signature in Task 1 (definition), Task 2 (both call sites), Task 3 (via `run_niche`), Task 4 (reporting only). `run_niche(niche, max_videos=1, lookback=None) -> int` defined in Task 2, consumed identically in Task 3. `config.discovery_lookback` / `config.schedule_max_videos` defined in Task 3, consumed in Tasks 2 (lookback default), 3, 4. Fake downloader in Task 1 and Task 2 has the same `search_videos_by_channel(channel_id, published_after='', max_results=10)` signature. ✓

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-07-scheduled-discovery.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
