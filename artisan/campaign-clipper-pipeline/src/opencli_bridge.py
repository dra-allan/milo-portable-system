"""Submit to the Clipster board through the opencli browser bridge.

opencli drives the operator's own Chrome, already signed into Clipster, so this
module never sees a password and never re-authenticates. Milo calls it; the
pipeline's own stages do not.

Three things this module refuses to do, each one a real failure mode:

**It never guesses the bridge profile.** With more than one Browser Bridge
profile connected, opencli resolves ambiguously, which means submitting through
whichever Chrome answered first. No profile configured is a hard error, not a
default.

**It never treats an opencli failure as a submission.** A dead bridge, a missing
binary and a timeout all return ``status='error'``. Only a status badge read back
from the board counts as ``submitted``, and rejection words are checked before
acceptance words so ``Submitted - Ineligible`` resolves to a rejection.

**It stops at the first failed step.** Typing a URL into a dialog that never
opened, then clicking a send button that is not there, produces a confident log
line and no submission.

Selectors were verified live on 2026-08-18 and live here in one block, matching
the convention in ``clipster.py``: a board restyle is a one-place fix.

Note the submit flow is a *sequence of separate opencli commands*. There is no
single compound subcommand to lean on, and building one inside opencli would put
the board's DOM assumptions in two repos instead of one.
"""

import json
import os
import subprocess
from typing import Dict, List, Optional

from .config import config
from .utils import setup_logger

logger = setup_logger(__name__)

CARD = 'button[id^=discover-campaign-card-]'
OPEN_BUTTON = '#submit-content-button'
INPUT = '#content-url'
SEND_BUTTON = '#submit-content-send-button'
ACTIVITY_PATH = '/activity/submissions'

# Rejection words are tested first on purpose: a badge reading
# 'Submitted - Ineligible' is a rejection, and reading it as a success is how a
# campaign silently keeps eating submission slots.
_REJECTED_WORDS = ('ineligible', 'rejected', 'invalid', 'disapproved',
                   'declined', 'not eligible')
_ACCEPTED_WORDS = ('submitted', 'approved', 'pending')


def _base_cmd(session: str, profile: str) -> List[str]:
    return [config.opencli_bin, 'browser', session]


def _with_profile(cmd: List[str], profile: str) -> List[str]:
    return cmd + ['--profile', profile]


def build_submit_steps(session: str, profile: str, campaign_url: str,
                       video_url: str) -> List[List[str]]:
    """The ordered opencli commands that put one link on the board.

    Returned as separate steps rather than one compound command so a failure is
    attributable to the exact click that broke.
    """
    base = _base_cmd(session, profile)
    steps = [
        base + ['open', campaign_url],
        base + ['click', '--css', OPEN_BUTTON],
        base + ['type', '--css', INPUT, '--text', video_url],
        base + ['click', '--css', SEND_BUTTON],
    ]
    return [_with_profile(step, profile) for step in steps]


def build_check_command(session: str, profile: str) -> List[str]:
    url = f'{config.clipster_base}{ACTIVITY_PATH}'
    return _with_profile(_base_cmd(session, profile) + ['open', url], profile)


def classify_submission(text: Optional[str]) -> str:
    """``'submitted' | 'rejected' | 'unknown'`` from a status badge."""
    low = (text or '').lower()
    if any(word in low for word in _REJECTED_WORDS):
        return 'rejected'
    if any(word in low for word in _ACCEPTED_WORDS):
        return 'submitted'
    return 'unknown'


def run_step(step: List[str], timeout: int = 0) -> Dict:
    """Run one opencli command. Never raises.

    opencli writes an extension-update notice to stderr on most invocations, so
    stderr is only read when the exit code already says the step failed.
    """
    timeout = timeout or config.opencli_timeout
    env = dict(os.environ)
    env['OPENCLI_PROFILE'] = config.opencli_profile or env.get(
        'OPENCLI_PROFILE', '')
    try:
        proc = subprocess.run(step, capture_output=True, text=True,
                              timeout=timeout, env=env, encoding='utf-8',
                              errors='replace')
    except subprocess.TimeoutExpired:
        logger.error('OPENCLI_TIMEOUT seconds=%s cmd=%s', timeout,
                     ' '.join(step[:4]))
        return {'ok': False, 'stdout': '',
                'error': f'timeout after {timeout}s'}
    except (FileNotFoundError, OSError) as exc:
        logger.error('OPENCLI_MISSING cmd=%s error=%s', step[0], exc)
        return {'ok': False, 'stdout': '', 'error': str(exc)[:160]}
    if proc.returncode != 0:
        logger.error('OPENCLI_EXIT_%s cmd=%s stderr=%s', proc.returncode,
                     ' '.join(step[:4]), (proc.stderr or '')[:200])
        return {'ok': False, 'stdout': (proc.stdout or '').strip(),
                'error': (proc.stderr or '').strip()[:200]}
    return {'ok': True, 'stdout': (proc.stdout or '').strip(), 'error': ''}


def _payload(stdout: str) -> Dict:
    text = (stdout or '').strip()
    if not text.startswith('{'):
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def submit(campaign_url: str, video_url: str, session: str = '',
           profile: str = '') -> Dict:
    """Submit one published short URL to a campaign board.

    Returns ``{'ok', 'status', 'detail'}`` where status is ``submitted``,
    ``rejected``, ``unknown`` or ``error``. Only ``submitted`` is a success, and
    the caller must not mark a clip submitted on anything else.
    """
    session = session or config.opencli_session
    profile = profile or config.opencli_profile
    if not profile:
        detail = ('no opencli bridge profile configured; set OPENCLI_PROFILE '
                  'or opencli.profile in clipper.yaml')
        logger.error('SUBMIT_NO_PROFILE campaign=%s', campaign_url)
        return {'ok': False, 'status': 'error', 'detail': detail}
    if not (campaign_url and video_url):
        return {'ok': False, 'status': 'error',
                'detail': 'campaign_url and video_url are both required'}

    last: Dict = {}
    for index, step in enumerate(build_submit_steps(session, profile,
                                                   campaign_url, video_url)):
        last = run_step(step)
        if not last.get('ok'):
            detail = f'step {index + 1} failed: {last.get("error") or "?"}'
            logger.error('SUBMIT_ERROR campaign=%s link=%s %s', campaign_url,
                         video_url, detail)
            return {'ok': False, 'status': 'error', 'detail': detail[:200]}

    payload = _payload(last.get('stdout', ''))
    text = str(payload.get('status') or payload.get('detail')
               or last.get('stdout') or '')
    status = classify_submission(text)
    logger.info('SUBMIT_%s campaign=%s link=%s', status.upper(), campaign_url,
                video_url)
    return {'ok': status == 'submitted', 'status': status,
            'detail': text[:200]}


def check_submissions(session: str = '', profile: str = '') -> List[Dict]:
    """Open the activity page and return whatever rows opencli reports."""
    session = session or config.opencli_session
    profile = profile or config.opencli_profile
    if not profile:
        logger.error('CHECK_NO_PROFILE')
        return []
    result = run_step(build_check_command(session, profile))
    if not result.get('ok'):
        return []
    payload = _payload(result.get('stdout', ''))
    rows = payload.get('rows') or payload.get('submissions') or []
    for row in rows:
        if isinstance(row, dict):
            row['classified'] = classify_submission(
                str(row.get('status') or row.get('text') or ''))
    logger.info('CHECK_SUBMISSIONS rows=%d', len(rows))
    return rows
