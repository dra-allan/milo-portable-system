"""Orchestrator and CLI for the campaign clipper.

The stage order is the safety model
----------------------------------
``preflight -> sources -> plan -> render -> validate -> upload -> submit``

No stage is skipped because the previous one looked fine, and each of the last
two is a separate opt-in. That is not excessive caution, it reflects how the
costs differ: a bad render costs minutes, a bad upload is deletable, and a bad
*submission* spends one of a handful of daily slots and moves you toward losing
the linked account, which is the only asset here that cannot be rebuilt from a
git branch.

So with default config ``--mode run`` builds, validates, and stops, printing
exactly what it would have published.
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import captions, cleanup, clipster, compiler, overlay as ov
from . import renderer, sources
from . import validator as validation
from .config import config
from .database import ClipperDatabase
from .spec import CampaignSpec, load_all
from .utils import setup_logger, write_json

logger = setup_logger(__name__, config.log_dir / 'clipper.log')


def _db() -> ClipperDatabase:
    return ClipperDatabase(config.db_path)


def _spec(campaign_id: str) -> Optional[CampaignSpec]:
    path = config.campaign_spec_dir / f'{campaign_id}.yaml'
    if not path.exists():
        logger.error('SPEC_MISSING id=%s expected=%s', campaign_id, path)
        return None
    try:
        return CampaignSpec.load(path)
    except Exception as exc:
        logger.error('SPEC_INVALID id=%s error=%s', campaign_id, exc)
        return None


# -- environment -------------------------------------------------------
def mode_test() -> int:
    """Check everything the pipeline needs before you rely on it.

    Split into hard and soft failures. A missing font stops all rendering; a
    missing Playwright only stops the browser convenience layer, and the manual
    paste path still works without it.
    """
    from .utils import which_ffmpeg, which_ffprobe
    hard, soft = [], []

    for label, probe in (('ffmpeg', which_ffmpeg), ('ffprobe', which_ffprobe)):
        try:
            print(f'[ OK ] {label}: {probe()}')
        except Exception as exc:
            hard.append(f'{label}: {exc}')
    try:
        print(f'[ OK ] font: {config.resolve_font()}')
    except Exception as exc:
        hard.append(f'font: {exc}')
    try:
        import PIL
        print(f'[ OK ] Pillow {PIL.__version__}')
    except ImportError:
        hard.append('Pillow missing (pip install pillow)')
    try:
        print(f'[ OK ] encoder: {renderer.resolve_encoder()}')
    except Exception as exc:
        hard.append(f'encoder probe: {exc}')

    for module, note in (('yaml', 'campaign specs cannot be loaded'),
                        ('cv2', 'logo detection reports unverifiable'),
                        ('playwright', 'browse/submit unavailable, manual '
                                       'paste still works'),
                        ('googleapiclient', 'uploads unavailable'),
                        ('gdown', 'Drive folder download unavailable')):
        try:
            __import__(module)
            print(f'[ OK ] {module}')
        except ImportError:
            soft.append(f'{module} missing: {note}')

    if not config.script_api_key:
        soft.append('GEMINI_API_KEY unset: copy falls back to templates')

    print(f'\nruntime root : {config.runtime_root}')
    print(f'spec dir     : {config.campaign_spec_dir}')
    specs = load_all(config.campaign_spec_dir)
    print(f'campaigns    : {len(specs)}')
    for spec in specs:
        print(f'  - {spec.describe()}')
    cleanup.disk_report()

    for item in soft:
        print(f'[WARN] {item}')
    for item in hard:
        print(f'[FAIL] {item}')
    return 1 if hard else 0


# -- campaign intake ----------------------------------------------------
def mode_campaigns(platform: str) -> int:
    cards = clipster.list_campaigns(platform)
    if not cards:
        print('No campaigns scraped. Run --mode login first, or use '
              '--mode add to paste requirements by hand.')
        return 1
    for card in cards:
        rate = card.get('rate_per_1m')
        rate_text = f'${rate:g}/1M' if rate else ''
        print(f"{card['id']:<32} {card.get('type', '?'):<9} "
              f"{rate_text:<12} {card['url']}")
    write_json(config.data_dir / f'campaigns_{platform}.json', cards)
    return 0


def mode_pull(url: str, campaign_id: str, use_model: bool) -> int:
    """Read a campaign page and compile its requirements into a spec."""
    page = clipster.read_campaign(url)
    if not page:
        print('Could not read the campaign page. Copy the Requirements block '
              'into a text file and use: --mode add --id <id> --file <path>')
        return 1
    spec, path = compiler.compile_to_file(
        page['requirements'], campaign_id=campaign_id, name=campaign_id,
        url=url, card=page.get('card'), use_model=use_model)
    _db().upsert_campaign(spec.id, spec.name, url, spec.to_dict(),
                          page['requirements'])
    _report_spec(spec, path)
    return 0


def mode_add(campaign_id: str, file_path: str, name: str, url: str,
             use_model: bool) -> int:
    """Compile a requirements block you pasted into a file.

    The path that always works: no browser, no selectors, no login.
    """
    raw = Path(file_path).expanduser().read_text(encoding='utf-8')
    spec, path = compiler.compile_to_file(
        raw, campaign_id=campaign_id, name=name or campaign_id, url=url,
        use_model=use_model)
    _db().upsert_campaign(spec.id, spec.name, url, spec.to_dict(), raw)
    _report_spec(spec, path)
    return 0


def _report_spec(spec: CampaignSpec, path: Path) -> None:
    print(f'\nWrote {path}')
    print(f'  {spec.describe()}')
    for label, items in (('CONFLICT', spec.conflicts),
                        ('MANUAL', spec.manual_steps),
                        ('UNPARSED', spec.unparsed)):
        for item in items:
            print(f'  [{label}] {item}')
    for item in spec.blocking_problems():
        print(f'  [BLOCK] {item}')
    print('\nRead the YAML before building. The compiler is good, not '
          'psychic.')


def mode_specs() -> int:
    for spec in load_all(config.campaign_spec_dir):
        flag = '' if not spec.blocking_problems() else '  [BLOCKED]'
        print(f'{spec.id:<32} {spec.describe()}{flag}')
    return 0


# -- build -------------------------------------------------------------
def mode_sources(campaign_id: str, refresh: bool) -> int:
    spec = _spec(campaign_id)
    if not spec:
        return 1
    rows = sources.sync_sources(spec, _db(), refresh=refresh)
    logo = sources.sync_logo(spec, refresh=refresh)
    print(f'{len(rows)} usable source files in '
          f'{config.campaign_source_dir(spec.id)}')
    print(f'logo: {logo or "none"}')
    return 0 if rows else 1


def build(spec: CampaignSpec, db, count: Optional[int] = None,
          use_model: bool = True, refresh: bool = False) -> List[Dict]:
    """Source, plan, render and validate a run's worth of clips."""
    from . import segmenter

    if not validation.preflight(spec)['ok']:
        return []

    rows = sources.sync_sources(spec, db, refresh=refresh)
    if not rows:
        logger.error('BUILD_ABORT campaign=%s no usable sources', spec.id)
        return []
    logo = sources.sync_logo(spec, refresh=refresh)
    if spec.assets.logo_required and not logo:
        logger.error('BUILD_ABORT campaign=%s logo required but unavailable',
                     spec.id)
        return []

    plans = segmenter.plan_clips(rows, spec, db, wanted=count)
    results: List[Dict] = []
    for plan in plans:
        copy = captions.build_copy(spec, plan, use_model=use_model)

        # 'if-absent' exists because these content folders mix branded and
        # unbranded clips, and a second logo on an already-branded clip is the
        # sloppy look the campaigns reject by name.
        stamp = bool(logo)
        if logo and spec.assets.logo_mode == 'if-absent':
            detected = ov.logo_present(plan['source_path'], logo)
            stamp = detected is not True
            logger.info('LOGO_MODE campaign=%s source=%s already=%s stamp=%s',
                        spec.id, plan['source_name'], detected, stamp)

        report = renderer.render_clip(spec, plan, copy, logo_path=logo,
                                     stamp_logo=stamp)
        if not report or report.get('dry_run'):
            continue

        plan_row = {**plan, 'caption': copy['caption'],
                    'overlay_text': copy['overlay_text'], 'niche': spec.niche}
        clip_id = db.record_clip(spec.id, plan_row, report['path'])
        title = captions.build_title(spec, copy, clip_id=clip_id)
        db.update_title(clip_id, title)

        verdict = validation.validate(spec, report['path'], copy, report)
        db.record_validation(clip_id, {**verdict, 'render': report},
                             verdict['passed'])
        if verdict['passed']:
            # The window is only claimed once the clip is actually shippable, so
            # a failed render does not permanently burn those seconds of source.
            db.mark_window_used(spec.id, plan['fingerprint'], plan['start'],
                                plan['end'])
            renderer.cleanup_work(report)
        results.append({'clip_id': clip_id, 'path': report['path'],
                        'copy': copy, 'verdict': verdict, 'title': title})
    cleanup.after_build(spec.id)
    return results


def mode_build(campaign_id: str, count: Optional[int], use_model: bool,
               refresh: bool) -> int:
    spec = _spec(campaign_id)
    if not spec:
        return 1
    results = build(spec, _db(), count=count, use_model=use_model,
                    refresh=refresh)
    if not results:
        print('Nothing built. The blocking reason is in the log above.')
        return 1
    _print_results(results)
    return 0


def _print_results(results: List[Dict]) -> None:
    print()
    for item in results:
        verdict = item['verdict']
        state = 'PASS' if verdict['passed'] else 'FAIL'
        print(f"[{state}] clip {item['clip_id']}  {Path(item['path']).name}")
        print(f"       title  : {item['title']}")
        print(f"       text   : {item['copy']['overlay_text']}")
        print(f"       caption: {item['copy']['caption']}")
        for problem in verdict['errors']:
            print(f'       BLOCK  : {problem}')
        for problem in verdict['warnings']:
            print(f'       warn   : {problem}')
        for problem in verdict['manual']:
            print(f'       MANUAL : {problem}')


# -- publish -----------------------------------------------------------
def _channel_for(spec: CampaignSpec) -> str:
    """The channel key a campaign's clips upload to.

    Explicit spec field wins, then the niche map, then the global env value.
    """
    if spec.upload_channel:
        return spec.upload_channel
    mapped = config.channel_for_niche(spec.niche)
    if mapped:
        return mapped
    return config.upload_channel


def upload_clip(spec: CampaignSpec, db, clip_id: int,
                privacy: Optional[str] = None) -> Optional[Dict]:
    row = db.clip_row(clip_id)
    if not row:
        logger.error('UPLOAD_NO_CLIP id=%s', clip_id)
        return None
    if row['status'] != 'validated':
        logger.error('UPLOAD_REFUSED id=%s status=%s (must be validated)',
                     clip_id, row['status'])
        return None
    if db.uploads_since(86400) >= config.upload_max_per_day:
        logger.error('UPLOAD_CAP_DAY reached=%d', config.upload_max_per_day)
        return None
    if (db.uploads_since(86400, spec.id)
            >= config.upload_max_per_campaign_per_day):
        logger.error('UPLOAD_CAP_CAMPAIGN campaign=%s reached=%d', spec.id,
                     config.upload_max_per_campaign_per_day)
        return None

    from .publisher import ClipperPublisher
    channel = _channel_for(spec)
    publisher = ClipperPublisher(channel=channel, privacy_status=privacy)
    title = (row.get('title') or '').strip() or captions.build_title(
        spec, {'overlay_text': row['overlay_text']}, clip_id=clip_id)
    result = publisher.upload(row['local_path'], title, row['caption'] or '',
                              tags=spec.caption.all_required())
    if not result:
        db.mark_failed(clip_id, 'upload')
        return None
    db.mark_uploaded(clip_id, result['id'], result['url'],
                     account=publisher.channel)
    if result['privacy'] != 'public':
        logger.warning('UPLOAD_PRIVATE id=%s views only count once public; '
                       'flip it before submitting', clip_id)
    return result


def submit_clip(spec: CampaignSpec, db, clip_id: int,
                fill_only: bool = False) -> bool:
    row = db.clip_row(clip_id)
    if not row or row['status'] != 'uploaded':
        logger.error('SUBMIT_REFUSED id=%s status=%s (must be uploaded)',
                     clip_id, row['status'] if row else 'missing')
        return False
    if not spec.url:
        clipster.queue_manual(spec.id, '', row['video_url'],
                              row['caption'] or '')
        return False
    ok = clipster.submit_link(spec.url, row['video_url'],
                             confirm=not fill_only)
    if ok:
        db.mark_submitted(clip_id)
    else:
        clipster.queue_manual(spec.id, spec.url, row['video_url'],
                              row['caption'] or '')
    return ok


def mode_upload(campaign_id: str, clip_id: int, privacy: str) -> int:
    spec = _spec(campaign_id)
    if not spec:
        return 1
    result = upload_clip(spec, _db(), clip_id, privacy=privacy or None)
    if not result:
        return 1
    print(f"uploaded: {result['url']} ({result['privacy']})")
    return 0


def mode_submit(campaign_id: str, clip_id: int, fill_only: bool) -> int:
    spec = _spec(campaign_id)
    if not spec:
        return 1
    return 0 if submit_clip(spec, _db(), clip_id, fill_only=fill_only) else 1


def mode_run(campaign_id: str, count: Optional[int], use_model: bool,
             refresh: bool) -> int:
    """End to end, stopping at the first gate that config has not opened."""
    spec = _spec(campaign_id)
    if not spec:
        return 1
    db = _db()
    results = build(spec, db, count=count, use_model=use_model,
                   refresh=refresh)
    _print_results(results)
    passed = [r for r in results if r['verdict']['passed']]
    if not passed:
        print('\nNothing passed validation. Nothing will be published.')
        return 1
    if not config.auto_upload:
        print(f'\n{len(passed)} clip(s) ready. Auto-upload is off. Watch them, '
              'then:')
        for item in passed:
            print(f"  --mode upload --id {spec.id} --clip {item['clip_id']}")
        return 0
    for item in passed:
        result = upload_clip(spec, db, item['clip_id'])
        if not result:
            continue
        if config.auto_submit:
            submit_clip(spec, db, item['clip_id'])
        else:
            print(f"uploaded {result['url']}; submit with --mode submit "
                  f"--id {spec.id} --clip {item['clip_id']}")
    return 0


# -- housekeeping -------------------------------------------------------
def mode_status() -> int:
    db = _db()
    print('clips by status:')
    for status, count in sorted(db.stats().items()):
        print(f'  {status:<24} {count}')
    print(f'uploads in last 24h: {db.uploads_since(86400)} '
          f'(cap {config.upload_max_per_day})')
    pending = clipster.manual_queue()
    if pending:
        print(f'\n{len(pending)} submission(s) waiting for a human:')
        for item in pending:
            print(f"  {item['campaign_id']}: {item['video_url']}")
    for status in ('validated', 'uploaded', 'submitted', 'rejected'):
        for row in db.clips_by_status(status, limit=10):
            name = Path(row['local_path'] or '').name
            url = row.get('video_url') or ''
            print(f"  [{status}] clip {row['id']} {row['campaign_id']} {name}"
                  + (f'  {url}' if url else ''))
    cleanup.disk_report()
    return 0


def mode_links(campaign_id: str) -> int:
    """Print and write out every clip that has a published link.

    This is the tracking list: campaign, niche, account, title and the URL you
    submit to the board. Written to ``data/clip_links.csv`` and printed.
    """
    db = _db()
    rows = db.clips_export(campaign_id=campaign_id or None)
    if not rows:
        print('No published links recorded yet.')
        return 0
    header = ('clip_id,campaign,niche,account,title,video_url,uploaded_at,'
              'status')
    lines = [header]
    print(f'{"clip":>6}  {"campaign":<24} {"niche":<14} {"account":<22} '
          f'{"title":<44} url')
    for row in rows:
        title = (row.get('title') or row.get('overlay_text') or '').replace(
            ',', ' ').strip()
        uploaded = time.strftime('%Y-%m-%d %H:%M',
                                 time.localtime(row.get('uploaded_at') or 0))
        print(f'{row["id"]:>6}  {(row["campaign_id"] or "")[:24]:<24} '
              f'{(row.get("niche") or ""):<14} '
              f'{(row.get("account") or ""):<22} '
              f'{title[:44]:<44} {row.get("video_url") or ""}')
        lines.append(','.join(str(row.get(k) or '')
                              for k in ('id', 'campaign_id', 'niche',
                                        'account', 'video_url'))
                    + f',{uploaded},{row.get("status") or ""}')
    path = config.data_dir / 'clip_links.csv'
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'\nWrote {path} ({len(rows)} link(s))')
    return 0


def mode_record_link(campaign_id: str, clip_id: int, url: str) -> int:
    """Record a link for a clip that was uploaded by hand.

    Exists because not every post goes through the pipeline: the first two clips
    were posted manually. Attaching their URLs keeps the ledger complete so the
    campaign submission and tracking are one list, not two.
    """
    if not url:
        print('--mode record-link needs --url')
        return 1
    spec = _spec(campaign_id)
    if not spec:
        return 1
    if not _db().record_manual_link(clip_id, url):
        row = _db().clip_row(clip_id)
        print('Could not record the link. clip row status must be '
              f'"validated" or "built" (got: {row["status"] if row else "none"}).')
        return 1
    print(f'recorded: clip {clip_id} -> {url}')
    return 0


def mode_cleanup(campaign_id: str, drop_sources: bool,
                 drop_submitted: bool) -> int:
    cleanup.purge_temp(campaign_id or None)
    if drop_sources and campaign_id:
        cleanup.purge_sources(campaign_id)
    if drop_submitted:
        cleanup.purge_submitted(_db())
    cleanup.disk_report()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog='campaign-clipper',
        description='Clip campaign content into compliant vertical shorts.')
    parser.add_argument('--mode', required=True, choices=[
        'test', 'login', 'campaigns', 'pull', 'add', 'specs', 'sources',
        'build', 'upload', 'submit', 'run', 'links', 'record-link',
        'status', 'cleanup'])
    parser.add_argument('--id', default='',
                        help='campaign id (the spec filename)')
    parser.add_argument('--url', default='', help='campaign page URL')
    parser.add_argument('--file', default='',
                        help='text file holding a pasted Requirements block')
    parser.add_argument('--name', default='')
    parser.add_argument('--platform', default='youtube')
    parser.add_argument('--count', type=int, default=None,
                        help='clips to build this run')
    parser.add_argument('--clip', type=int, default=0, help='clip id')
    parser.add_argument('--privacy', default='',
                        choices=['', 'private', 'unlisted', 'public'])
    parser.add_argument('--refresh', action='store_true',
                        help='re-download the content folder')
    parser.add_argument('--no-model', action='store_true',
                        help='deterministic copy only')
    parser.add_argument('--fill-only', action='store_true',
                        help='fill the submission form but do not click')
    parser.add_argument('--drop-sources', action='store_true')
    parser.add_argument('--drop-submitted', action='store_true')
    args = parser.parse_args(argv)

    use_model = not args.no_model

    if args.mode == 'test':
        return mode_test()
    if args.mode == 'login':
        return 0 if clipster.login() else 1
    if args.mode == 'campaigns':
        return mode_campaigns(args.platform)
    if args.mode == 'specs':
        return mode_specs()
    if args.mode == 'pull':
        if not args.url:
            parser.error('--mode pull needs --url')
        return mode_pull(args.url, args.id or 'campaign', use_model)
    if args.mode == 'add':
        if not (args.id and args.file):
            parser.error('--mode add needs --id and --file')
        return mode_add(args.id, args.file, args.name, args.url, use_model)

    if args.mode in ('sources', 'build', 'run', 'upload', 'submit') \
            and not args.id:
        parser.error(f'--mode {args.mode} needs --id')

    if args.mode == 'sources':
        return mode_sources(args.id, args.refresh)
    if args.mode == 'build':
        return mode_build(args.id, args.count, use_model, args.refresh)
    if args.mode == 'run':
        return mode_run(args.id, args.count, use_model, args.refresh)
    if args.mode == 'upload':
        if not args.clip:
            parser.error('--mode upload needs --clip')
        return mode_upload(args.id, args.clip, args.privacy)
    if args.mode == 'submit':
        if not args.clip:
            parser.error('--mode submit needs --clip')
        return mode_submit(args.id, args.clip, args.fill_only)
    if args.mode == 'status':
        return mode_status()
    if args.mode == 'links':
        return mode_links(args.id)
    if args.mode == 'record-link':
        if not (args.id and args.clip):
            parser.error('--mode record-link needs --id and --clip')
        return mode_record_link(args.id, args.clip, args.url)
    if args.mode == 'cleanup':
        return mode_cleanup(args.id, args.drop_sources, args.drop_submitted)
    return 1


if __name__ == '__main__':
    sys.exit(main())
