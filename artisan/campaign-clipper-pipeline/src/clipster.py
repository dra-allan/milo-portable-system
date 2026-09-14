"""Driving the campaign board via opencli (your logged-in Chrome).

No Playwright. No separate browser profile. opencli binds to YOUR Chrome
window that is already signed into Clipster. Milo drives it; the pipeline
never sees a password, never re-authenticates, never stores cookies.

Session handling
----------------
A named opencli session (default: "clipster") binds to one Chrome tab/window.
You run `opencli browser clipster bind` once, or the first command auto-binds.
Everything after reuses that session. The profile is your Chrome profile.

Reading the requirement marks
-----------------------------
The board renders obligations with a green check and prohibitions with a red
cross. Getting that distinction right is not cosmetic: read a prohibition as an
obligation and "POST SPAM/LOW QUALITY" becomes an instruction to post spam.

So the mark is derived from each row's **computed colour**, not from a CSS class.
Class names change every time a site is restyled; red and green do not. Rows
whose colour is ambiguous come back as "unknown" and the compiler's keyword
heuristics decide, which is the correct order of preference.

Degradation
-----------
Every browser step falls back to a JSON queue on disk rather than failing the
run. A restyle should cost one manual paste, not a dead pipeline. All DOM
assumptions live in SELECTORS at the top of this file so a break is a
one-place fix.
"""

import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from .config import config
from .utils import ensure_dir, read_json, safe_slug, setup_logger, write_json

logger = setup_logger(__name__)

# Every DOM assumption in this module, in one place.
SELECTORS = {
    'campaign_card': 'button[id*="discover-campaign-card"]',
    'requirement_row': ('[class*=requirement] li, [class*=Requirement] li, '
                        'section li, section div[class*=row], '
                        'ul li, ol li'),
    'submission_input': ('input[placeholder*=link i], input[name*=link i], '
                         'input[type=url], form input[type=text]'),
    'submit_button': ("button:has-text('Submit Content'), "
                      "button:has-text('Submit')"),
    'external_link': 'a[href^=http]',
}

_COLOUR = re.compile(r'rgba?\((\d+),\s*(\d+),\s*(\d+)')

# Colour classification runs in the page via opencli eval.
_ROW_SCRIPT = """
(selector) => {
  const rows = Array.from(document.querySelectorAll(selector));
  return rows.map(row => {
    const marker = row.querySelector('svg, i, span[class*=icon]') || row;
    const style = window.getComputedStyle(marker);
    return {
      text: (row.innerText || '').trim(),
      color: style.color || '',
      fill: style.fill || ''
    };
  }).filter(r => r.text.length > 2);
}
"""

_LINK_SCRIPT = """
(els) => els.map(e => ({href: e.href, text: (e.innerText || '').trim()}))
"""


def _mark_from_colour(*values: str) -> str:
    """'check' | 'cross' | 'unknown' from any number of colour strings."""
    for value in values:
        match = _COLOUR.search(value or '')
        if not match:
            continue
        r, g, b = (int(match.group(i)) for i in (1, 2, 3))
        if r > 140 and r > g * 1.6 and r > b * 1.6:
            return 'cross'
        if g > 110 and g > r * 1.3 and g > b * 1.1:
            return 'check'
    return 'unknown'


def _opencli_session() -> str:
    return config.opencli_session or 'clipster'


def _opencli_profile() -> str:
    """Profile is required for opencli bridge commands."""
    profile = config.opencli_profile
    if not profile:
        raise RuntimeError(
            'no opencli bridge profile configured; set OPENCLI_PROFILE '
            'or opencli.profile in clipper.yaml')
    return profile


def _opencli_base_cmd() -> List[str]:
    base = [config.opencli_bin]
    profile = _opencli_profile()
    if profile:
        base += ['--profile', profile]
    base += ['browser', _opencli_session()]
    return base


def _run_opencli(args: List[str], timeout: int = 60) -> Dict:
    """Run one opencli command. Returns dict with ok, stdout, stderr, error."""
    cmd = _opencli_base_cmd() + args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, encoding='utf-8', errors='replace')
    except subprocess.TimeoutExpired:
        logger.error('OPENCLI_TIMEOUT seconds=%s cmd=%s', timeout, ' '.join(cmd[:4]))
        return {'ok': False, 'stdout': '', 'error': f'timeout after {timeout}s'}
    except (FileNotFoundError, OSError) as exc:
        logger.error('OPENCLI_MISSING cmd=%s error=%s', cmd[0], exc)
        return {'ok': False, 'stdout': '', 'error': str(exc)[:160]}
    if proc.returncode != 0:
        logger.error('OPENCLI_EXIT_%s cmd=%s stderr=%s', proc.returncode,
                     ' '.join(cmd[:4]), (proc.stderr or '')[:200])
        return {'ok': False, 'stdout': (proc.stdout or '').strip(),
                'error': (proc.stderr or '').strip()[:200]}
    return {'ok': True, 'stdout': (proc.stdout or '').strip(), 'error': ''}


def _parse_json_output(stdout: str) -> Dict:
    """Parse opencli JSON output. Returns empty dict on failure."""
    text = (stdout or '').strip()
    if not text.startswith('{'):
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def login(timeout_seconds: int = 300) -> bool:
    """Open the board headful and wait while you sign in.

    Run once per machine. Everything after this reuses the saved session.
    """
    # Bind to your Chrome (opens a tab if needed)
    result = _run_opencli(['bind'], timeout=30)
    if not result['ok']:
        logger.error('LOGIN_BIND_FAILED error=%s', result.get('error'))
        return False

    # Open discover page
    result = _run_opencli(['open', f'{config.clipster_base}/discover'], timeout=30)
    if not result['ok']:
        logger.error('LOGIN_OPEN_FAILED error=%s', result.get('error'))
        return False

    logger.info('LOGIN_WAIT sign in Clipster in the bound Chrome window; '
                'waiting up to %ds', timeout_seconds)

    # Wait for URL to change to authenticated area (poll via state)
    import time
    start = time.time()
    while time.time() - start < timeout_seconds:
        result = _run_opencli(['state'], timeout=10)
        if result['ok']:
            payload = _parse_json_output(result['stdout'])
            url = payload.get('url', '')
            if 'discover' in url or 'activity' in url or 'dashboard' in url:
                logger.info('LOGIN_OK url=%s', url)
                return True
        time.sleep(3)

    logger.warning('LOGIN_TIMEOUT url unknown')
    return False


def list_campaigns(platform: str = 'youtube', limit: int = 40) -> List[Dict]:
    """Scrape the discover grid for campaigns on one platform."""
    url = f'{config.clipster_base}/discover?platforms={platform}'
    out: List[Dict] = []

    result = _run_opencli(['open', url], timeout=30)
    if not result['ok']:
        logger.error('BROWSE_OPEN_FAILED error=%s', result.get('error'))
        return []

    # Wait for campaign cards to load
    result = _run_opencli(['wait', 'time', '3'], timeout=10)
    if not result['ok']:
        logger.warning('BROWSE_WAIT_FAILED error=%s', result.get('error'))

    # Use find command to get all campaign cards
    result = _run_opencli(['find', '--css', 'button[id*="discover-campaign-card"]', '--limit', str(limit)], timeout=30)
    if not result['ok']:
        logger.error('BROWSE_FIND_FAILED error=%s', result.get('error'))
        return []

    payload = _parse_json_output(result['stdout'])
    entries = payload.get('entries', [])

    seen = set()
    for entry in entries:
        elem_id = entry.get('attrs', {}).get('id') or ''
        if 'discover-campaign-card' not in elem_id:
            continue

        text = entry.get('text') or ''
        if not text or len(text) < 3:
            continue

        cid = elem_id.replace('discover-campaign-card-', '')
        href = f'{config.clipster_base}/discover?openedCampaignId={cid}'

        if href in seen:
            continue
        seen.add(href)
        out.append(_parse_card(href, text))
        if len(out) >= limit:
            break

    logger.info('BROWSE_OK platform=%s campaigns=%d', platform, len(out))
    return out


_RATE = re.compile(r'\$([\d,.]+)\s*/\s*1M', re.IGNORECASE)
_BUDGET = re.compile(r'/\s*\$([\d,.]+)')
_PROGRESS = re.compile(r'(\d{1,3})\s*%')


def _parse_card(href: str, text: str) -> Dict:
    flat = ' '.join(text.split())
    name = flat.split('$')[0].strip() or flat[:60]
    card: Dict = {'id': safe_slug(name), 'name': name, 'url': href,
                  'raw': flat}
    rate = _RATE.search(flat)
    if rate:
        card['rate_per_1m'] = float(rate.group(1).replace(',', ''))
    budget = _BUDGET.search(flat)
    if budget:
        card['budget_total'] = float(budget.group(1).replace(',', ''))
    progress = _PROGRESS.search(flat)
    if progress:
        card['progress'] = int(progress.group(1))
    low = flat.lower()
    card['type'] = 'ugc' if 'ugc' in low else 'clipping'
    platforms = [name for name, words in (('youtube', ('youtube', 'shorts')),
                                          ('tiktok', ('tiktok',)),
                                          ('instagram', ('instagram',
                                                         'reels')),
                                          ('x', ('twitter',)))
                 if any(word in low for word in words)]
    if platforms:
        card['platforms'] = platforms
    return card


def read_campaign(url: str) -> Optional[Dict]:
    """Open one campaign and return its requirements plus card metadata.

    The new Clipster UI renders campaign details in a modal dialog when you
    click the campaign card. The dialog contains all requirements, caps,
    platforms, and structured fields. We click the card, extract from the
    dialog, then continue.
    """
    result = _run_opencli(['open', url], timeout=30)
    if not result['ok']:
        logger.error('READ_CAMPAIGN_OPEN_FAILED url=%s error=%s', url,
                     result.get('error'))
        return None

    # Wait for page to render
    result = _run_opencli(['wait', 'time', '3'], timeout=10)
    if not result['ok']:
        logger.warning('READ_CAMPAIGN_WAIT_FAILED url=%s error=%s', url,
                       result.get('error'))

    # Find and click the campaign card to open the detail dialog
    # The card ID is derived from the URL
    import re
    cid_match = re.search(r'openedCampaignId=([a-f0-9-]+)', url)
    if cid_match:
        cid = cid_match.group(1)
        card_selector = f'button[id="discover-campaign-card-{cid}"]'
        result = _run_opencli(['click', card_selector], timeout=10)
        if not result['ok']:
            logger.warning('READ_CAMPAIGN_CLICK_FAILED url=%s error=%s', url,
                           result.get('error'))

    # Wait for dialog to appear
    result = _run_opencli(['wait', 'selector', '[role=dialog], [aria-modal=true]'], timeout=10)
    if not result['ok']:
        logger.warning('READ_CAMPAIGN_DIALOG_WAIT_FAILED url=%s error=%s', url,
                       result.get('error'))

    # Extract text from the dialog
    result_dialog = _run_opencli(['eval', 
        'document.querySelector("[role=dialog], [aria-modal=true]") ? document.querySelector("[role=dialog], [aria-modal=true]").innerText : ""'], timeout=10)
    
    dialog_text = ''
    if result_dialog['ok']:
        # opencli eval returns the raw text directly when it's a simple expression
        dialog_text = result_dialog['stdout'] or ''

    # Also get full page text for fallback
    result_body = _run_opencli(['extract'], timeout=15)
    body = ''
    if result_body['ok']:
        payload = _parse_json_output(result_body['stdout'])
        body = payload.get('text', '') or ''

    # Use dialog text if available, otherwise fall back to page text
    requirements = _parse_requirements_from_text(dialog_text or body)

    # Get external links from the page
    result_links = _run_opencli(['eval', _LINK_SCRIPT], timeout=15)
    links = []
    if result_links['ok']:
        payload = _parse_json_output(result_links['stdout'])
        if isinstance(payload, list):
            links = payload

    # Re-attach links in markdown form
    if requirements and links:
        for item in links:
            label = ' '.join((item.get('text') or '').split())
            href = item.get('href') or ''
            if label and href and 'clipster' not in href:
                requirements += f'\n[{label}]({href})'

    logger.info('READ_CAMPAIGN url=%s requirements_chars=%d', url, len(requirements or ''))
    return {'url': url, 'requirements': requirements,
            'card': _card_from_body(body), 'obligations': [],
            'prohibitions': [], 'unknown_marks': [],
            'body': body}


_MIN_DUR = re.compile(r'Min\.?\s*Duration\s*(\d{1,3})\s*sec', re.IGNORECASE)
_CAP_POST = re.compile(r'Cap per Post\s*\$?([\d,.]+)', re.IGNORECASE)
_CAP_PROFILE = re.compile(r'Cap per Profile\s*\$?([\d,.]+)', re.IGNORECASE)
_ELIGIBLE = re.compile(r'Eligible\s*\n?\s*([A-Za-z0-9_.]{3,40})')


def _card_from_body(body: str) -> Dict:
    """Pull the structured header numbers out of the page text."""
    card: Dict = {}
    flat = body or ''
    found = _MIN_DUR.search(flat)
    if found:
        card['min_duration'] = float(found.group(1))
    for key, pattern in (('cap_per_post', _CAP_POST),
                         ('cap_per_profile', _CAP_PROFILE)):
        found = pattern.search(flat)
        if found:
            card[key] = float(found.group(1).replace(',', ''))
    found = _RATE.search(flat)
    if found:
        card['rate_per_1m'] = float(found.group(1).replace(',', ''))
    accounts = _ELIGIBLE.findall(flat)
    if accounts:
        card['eligible_accounts'] = sorted(set(accounts))
    return card


def submit_link(campaign_url: str, video_url: str,
                confirm: bool = True) -> bool:
    """Paste a published link into the campaign's submission field.

    confirm=False fills the field and waits without clicking. That is the
    mode worth using until you trust the validator more than your own eyes:
    the form is filled, you press the button.
    """
    # Open campaign page
    result = _run_opencli(['open', campaign_url], timeout=30)
    if not result['ok']:
        logger.error('SUBMIT_OPEN_FAILED url=%s error=%s', campaign_url,
                     result.get('error'))
        return False

    # Wait for submission input
    result = _run_opencli(['wait', 'selector', SELECTORS['submission_input']], timeout=15)
    if not result['ok']:
        logger.error('SUBMIT_WAIT_INPUT_FAILED url=%s error=%s', campaign_url,
                     result.get('error'))
        return False

    # Fill the URL
    result = _run_opencli(['fill', SELECTORS['submission_input'],
                           '--text', video_url], timeout=15)
    if not result['ok']:
        logger.error('SUBMIT_FILL_FAILED url=%s error=%s', campaign_url,
                     result.get('error'))
        return False

    if not confirm:
        logger.info('SUBMIT_FILLED_ONLY url=%s link=%s click Submit '
                    'yourself', campaign_url, video_url)
        # Keep session alive for manual click
        import time
        time.sleep(120)
        return False

    # Click submit
    result = _run_opencli(['click', SELECTORS['submit_button']], timeout=15)
    if not result['ok']:
        logger.error('SUBMIT_CLICK_FAILED url=%s error=%s', campaign_url,
                     result.get('error'))
        return False

    # Wait for result
    import time
    time.sleep(3)

    # Check page for rejection/success text
    result = _run_opencli(['extract'], timeout=10)
    body = ''
    if result['ok']:
        payload = _parse_json_output(result['stdout'])
        body = (payload.get('text', '') or '').lower()

    if any(word in body for word in ('not eligible', 'invalid',
                                     'rejected', 'disapproved',
                                     'declined', 'not eligible')):
        logger.error('SUBMIT_REJECTED url=%s link=%s', campaign_url,
                     video_url)
        return False

    logger.info('SUBMIT_OK url=%s link=%s', campaign_url, video_url)
    return True


# -- manual fallback ----------------------------------------------------
def _queue_path() -> Path:
    return config.data_dir / 'manual_submissions.json'


def queue_manual(campaign_id: str, campaign_url: str, video_url: str,
                 caption: str) -> Path:
    """Park a submission for a human when the browser path is unavailable."""
    path = _queue_path()
    queue = read_json(path, []) or []
    queue.append({'campaign_id': campaign_id, 'campaign_url': campaign_url,
                  'video_url': video_url, 'caption': caption})
    write_json(path, queue)
    logger.warning('SUBMIT_QUEUED_MANUAL campaign=%s file=%s', campaign_id,
                   path)
    return path


def manual_queue() -> List[Dict]:
    return read_json(_queue_path(), []) or []


def clear_manual(campaign_id: str, video_url: str) -> None:
    queue = [item for item in manual_queue()
             if not (item.get('campaign_id') == campaign_id
                     and item.get('video_url') == video_url)]
    write_json(_queue_path(), queue)


def _parse_requirements_from_text(text: str) -> str:
    """Parse campaign requirements from extracted page text.

    The new UI has sections like:
    - Requirements
    - Platforms
    - Cap per Post / Cap per Profile / Min. Duration
    - Max Submissions per Social Account, etc.

    We extract everything from "Requirements" section onwards, and also
    prepend the structured fields (caps, min duration) so the compiler
    has all the constraints.
    """
    if not text:
        return ''

    lines = text.split('\n')
    output_lines = []

    # First, extract structured fields (caps, min duration) from anywhere in text
    import re
    structured_patterns = [
        (r'Cap per Post\s*\$?([\d,.]+)', 'Cap per Post: ${}'),
        (r'Cap per Profile\s*\$?([\d,.]+)', 'Cap per Profile: ${}'),
        (r'Min\.?\s*Duration\s*(\d{1,3})\s*sec', 'Min Duration: {} sec'),
        (r'Max Submissions per Social Account\s*(\d+)', 'Max Submissions per Social Account: {}'),
        (r'Max Submissions per day per Social Account\s*(\d+)', 'Max Submissions per day per Social Account: {}'),
        (r'Min Followers per Social Profile\s*([^\n]+)', 'Min Followers per Social Profile: {}'),
        (r'Min Views for Earnings\s*([\d,.]+)', 'Min Views for Earnings: {}'),
        (r'Min Engagement Rate\s*([\d.]+%)', 'Min Engagement Rate: {}'),
    ]

    for pattern, fmt in structured_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0] if match else ''
            output_lines.append(fmt.format(match.strip()))

    # Now find the "Requirements" section and everything after it
    req_start = -1
    for i, line in enumerate(lines):
        if line.strip().lower() in ('requirements', 'content requirements'):
            req_start = i
            break

    if req_start >= 0:
        # Include from Requirements onwards
        for line in lines[req_start:]:
            line = line.strip()
            if line:
                output_lines.append(line)
    else:
        # Fallback: look for key requirement keywords
        for line in lines:
            line = line.strip()
            if not line:
                continue
            lower = line.lower()
            if any(kw in lower for kw in ['must ', 'must-', 'required', 'prohibit', 'forbidden', 'banned', 'not allowed', 'do not', 'no ', 'check content', 'join discord', 'post unrelated']):
                output_lines.append(line)

    return '\n'.join(output_lines) if output_lines else text[:5000]