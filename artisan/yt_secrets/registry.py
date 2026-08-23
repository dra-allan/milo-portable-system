"""Comment-preserving edits to ``artisan/yt-secrets/channels.yaml``.

WHY THIS EXISTS
---------------
The auth flow used to end with an instruction to a human:

    Paste  channel_id: UC...  into channels.yaml under <key>

A step that a person has to retype is a step that gets skipped, which is why
every single ``channel_id`` in the registry was still ``''``. An empty value
means the identity guard falls back to the machine-written ledger, and the
ledger in ``learn`` mode binds to whatever channel it is shown first -- so a
wrong-account mint could still launder itself into looking correct. The fix is
to write the id automatically, at the one moment we have proven what it is.

WHY IT IS LINE-BASED AND NOT ``yaml.dump``
------------------------------------------
``safe_load`` + ``dump`` would round-trip the data and silently delete every
comment in ``channels.yaml``. Those comments are the incident history (the
8/16 wrong-channel uploads, the deleted flick_shorts OAuth client) and they are
the reason the next person does not repeat it. So we edit only the line that
needs editing, keep a ``.bak``, and re-parse the result before accepting it --
if the edit produced anything YAML cannot read, the backup goes back.

Depends on nothing but the standard library and PyYAML, same constraint as
:mod:`yt_secrets.identity`.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:  # package-relative first (python -m yt_secrets)
    from .identity import (LEGACY_DIR, client_source, expected_channel_id,
                           load_ledger, load_registry, registry_path)
except ImportError:  # pragma: no cover - direct script execution
    from identity import (LEGACY_DIR, client_source, expected_channel_id,
                          load_ledger, load_registry, registry_path)

REPO_ROOT = LEGACY_DIR.parent.parent

KEY_RE = re.compile(r'^[A-Za-z0-9_]+$')
ANCHOR_RE = re.compile(r'^channels:\s*(#.*)?$')
CHANNEL_ID_RE = re.compile(r"^(\s*)channel_id:\s*(.*?)\s*(#.*)?$")
ACTIVE_RE = re.compile(r"^(\s*)active:\s*(.*?)\s*(#.*)?$")

# Which config dir a pipeline reads its youtube_token_<key>.json from.
# clipper has no config dir of its own: it publishes through the shorts lane's
# tokens, which is why every shorts+clipper channel points at the shorts config.
PIPELINE_TOKEN_DIRS: Dict[str, str] = {
    'shorts': 'artisan/youtube-shorts-pipeline/config',
    'clipper': 'artisan/youtube-shorts-pipeline/config',
    'ranking': 'artisan/ranking-shorts-pipeline/config',
    'pov': 'artisan/pov_pipeline/config',
}


class RegistryError(RuntimeError):
    """channels.yaml cannot be edited the way it was asked to be edited."""


@dataclass
class Block:
    key: str
    start: int      # index of the ``  <key>:`` line
    end: int        # first index NOT in the block
    indent: int     # indent of the key line
    last_data: int  # last non-blank, non-comment line in the block

    @property
    def field_indent(self) -> int:
        return self.indent + 2


# ---------------------------------------------------------------------------
# Reading / writing the file
# ---------------------------------------------------------------------------
def _path(path: Optional[Path] = None) -> Path:
    return Path(path) if path else registry_path()


def _read(path: Path) -> List[str]:
    if not path.exists():
        raise RegistryError(f'channel registry not found: {path}')
    return path.read_text(encoding='utf-8').splitlines()


def _write(path: Path, lines: Sequence[str]) -> None:
    """Atomically replace the registry, keeping a .bak and validating the result."""
    backup = path.with_suffix(path.suffix + '.bak')
    if path.exists():
        shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    tmp.replace(path)
    try:
        _assert_parses(path)
    except Exception:
        if backup.exists():
            shutil.copy2(backup, path)
        raise


def _assert_parses(path: Path) -> None:
    try:
        import yaml
    except ImportError:  # pragma: no cover - PyYAML is a hard dep of the CLI
        return
    data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    if not isinstance(data.get('channels'), dict):
        raise RegistryError(
            f'the edit left {path.name} unreadable (no channels mapping); '
            'the previous version has been restored from .bak')


def _anchor(lines: Sequence[str]) -> int:
    for index, line in enumerate(lines):
        if ANCHOR_RE.match(line):
            return index
    raise RegistryError("channels.yaml has no top-level 'channels:' mapping")


def find_block(lines: Sequence[str], key: str) -> Optional[Block]:
    """Locate ``key``'s block by indentation. Comments stay where they are."""
    anchor = _anchor(lines)
    pattern = re.compile(r'^(\s+)' + re.escape(key) + r':\s*(#.*)?$')
    for index in range(anchor + 1, len(lines)):
        match = pattern.match(lines[index])
        if not match:
            continue
        indent = len(match.group(1))
        end = len(lines)
        last_data = index
        for probe in range(index + 1, len(lines)):
            stripped = lines[probe].strip()
            if not stripped:
                continue
            if len(lines[probe]) - len(lines[probe].lstrip()) <= indent:
                end = probe
                break
            if not stripped.startswith('#'):
                last_data = probe
        return Block(key=key, start=index, end=end, indent=indent,
                     last_data=last_data)
    return None


def _unquote(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
        return value[1:-1].strip()
    return value


# ---------------------------------------------------------------------------
# The write that matters
# ---------------------------------------------------------------------------
def set_channel_id(key: str, channel_id: str, *, force: bool = False,
                   path: Optional[Path] = None) -> bool:
    """Record ``key``'s YouTube channel id in channels.yaml.

    Returns True when the file changed, False when it already said this. Raises
    :class:`RegistryError` when a *different* id is already written and ``force``
    is not set -- overwriting a declared binding without being asked to is how
    the registry would stop being trustworthy.
    """
    channel_id = str(channel_id or '').strip()
    if not channel_id:
        raise RegistryError('refusing to write an empty channel id')
    target = _path(path)
    lines = _read(target)
    block = find_block(lines, key)
    if block is None:
        raise RegistryError(
            f'{key!r} is not in {target.name}. Add it first:  cd artisan && '
            f'python -m yt_secrets add --channel {key} --email <owner> '
            f'--slug <project> --pipeline <lane>')

    for index in range(block.start + 1, block.end):
        match = CHANNEL_ID_RE.match(lines[index])
        if not match:
            continue
        current = _unquote(match.group(2))
        if current == channel_id:
            return False
        if current and not force:
            raise RegistryError(
                f'{key} already declares channel_id {current!r} but this token '
                f'resolves to {channel_id!r}. Nothing was changed. If the '
                f'channel genuinely moved, re-run with --rebind; otherwise you '
                f'signed in as the wrong Google account.')
        comment = (' ' + match.group(3)) if match.group(3) else ''
        lines[index] = f"{match.group(1)}channel_id: '{channel_id}'{comment}"
        _write(target, lines)
        return True

    # No channel_id line at all: insert one after the block's last real field.
    anchor_line = lines[block.last_data]
    indent = ' ' * (len(anchor_line) - len(anchor_line.lstrip()))
    lines.insert(block.last_data + 1, f"{indent}channel_id: '{channel_id}'")
    _write(target, lines)
    return True


def set_active(key: str, active: bool, *, path: Optional[Path] = None) -> bool:
    """Flip ``active:`` for a channel so the default auth run picks it up."""
    target = _path(path)
    lines = _read(target)
    block = find_block(lines, key)
    if block is None:
        raise RegistryError(f'{key!r} is not in {target.name}')
    wanted = 'true' if active else 'false'
    for index in range(block.start + 1, block.end):
        match = ACTIVE_RE.match(lines[index])
        if not match:
            continue
        if _unquote(match.group(2)).lower() == wanted:
            return False
        comment = (' ' + match.group(3)) if match.group(3) else ''
        lines[index] = f'{match.group(1)}active: {wanted}{comment}'
        _write(target, lines)
        return True
    anchor_line = lines[block.last_data]
    indent = ' ' * (len(anchor_line) - len(anchor_line.lstrip()))
    lines.insert(block.last_data + 1, f'{indent}active: {wanted}')
    _write(target, lines)
    return True


# ---------------------------------------------------------------------------
# Adding a channel to a pipeline
# ---------------------------------------------------------------------------
def token_dir_for(pipelines: Sequence[str]) -> str:
    for pipeline in pipelines:
        if pipeline in PIPELINE_TOKEN_DIRS:
            return PIPELINE_TOKEN_DIRS[pipeline]
    raise RegistryError(
        f'no token_dir known for pipelines {list(pipelines)!r}; known lanes are '
        + ', '.join(sorted(set(PIPELINE_TOKEN_DIRS))))


def add_channel(key: str, *, email: str, slug: str, pipelines: Sequence[str],
                token_dir: str = '', active: bool = True,
                chrome_profile: str = '', client_from: str = '',
                channel_id: str = '', path: Optional[Path] = None) -> str:
    """Append a new channel block to channels.yaml and return its token_dir.

    Deliberately strict: a channel added with a typo'd key or a token_dir that
    does not match its pipeline is a channel that authenticates fine and then
    publishes nowhere, which is a much more annoying bug to find later.
    """
    key = str(key or '').strip()
    if not KEY_RE.match(key):
        raise RegistryError(
            f'{key!r} is not a usable registry key; use letters, digits and '
            'underscores only (it becomes part of youtube_token_<key>.json)')
    email = str(email or '').strip()
    slug = str(slug or '').strip()
    if not email or '@' not in email:
        raise RegistryError('a channel needs the owning Google account email')
    if not slug:
        raise RegistryError(
            'a channel needs the OAuth project slug, i.e. the folder under '
            'artisan/yt-secrets/ that holds its credentials.json')
    pipelines = [str(p).strip() for p in pipelines if str(p).strip()]
    if not pipelines:
        raise RegistryError('a channel needs at least one pipeline')
    unknown = [p for p in pipelines if p not in PIPELINE_TOKEN_DIRS]
    if unknown:
        raise RegistryError(
            f'unknown pipeline(s) {unknown}; known lanes are '
            + ', '.join(sorted(set(PIPELINE_TOKEN_DIRS))))
    token_dir = str(token_dir or '').strip() or token_dir_for(pipelines)

    target = _path(path)
    lines = _read(target)
    if find_block(lines, key) is not None:
        raise RegistryError(
            f'{key!r} is already in {target.name}. To re-authenticate it:  '
            f'reauth_all_channels.bat --channel {key}')
    if client_from:
        existing = load_registry()
        if client_from not in existing:
            raise RegistryError(
                f'client_from: {client_from} refers to a channel that is not in '
                'the registry')

    anchor = _anchor(lines)
    insert_at = anchor + 1
    for index in range(anchor + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith('#'):
            continue
        if len(lines[index]) - len(lines[index].lstrip()) == 0:
            break
        insert_at = index + 1

    pad, inner = '  ', '    '
    block = [f'{pad}{key}:',
             f'{inner}email: {email}',
             f'{inner}slug: {slug}',
             f'{inner}active: {"true" if active else "false"}',
             f'{inner}pipelines: [{", ".join(pipelines)}]',
             f'{inner}token_dir: {token_dir}']
    if chrome_profile:
        block.append(f'{inner}chrome_profile: {chrome_profile}')
    block.append(f"{inner}channel_id: '{str(channel_id or '').strip()}'")
    if client_from:
        block.append(f'{inner}client_from: {client_from}')
    lines[insert_at:insert_at] = [''] + block
    _write(target, lines)
    return token_dir


# ---------------------------------------------------------------------------
# The anti-mismatch audit
# ---------------------------------------------------------------------------
def token_path(key: str, info: Dict[str, Any]) -> Path:
    return REPO_ROOT / str(info.get('token_dir') or '') / f'youtube_token_{key}.json'


def credentials_for(key: str, channels: Dict[str, Dict[str, Any]]) -> Path:
    source = client_source(key)
    info = channels.get(source) or channels.get(key) or {}
    return LEGACY_DIR / str(info.get('slug') or '') / 'credentials.json'


def audit() -> List[Tuple[str, str, str]]:
    """Every way channels.yaml can disagree with itself, the ledger, or disk.

    Cheap, offline, and safe to run before anything destructive -- which is why
    the batch file runs it first. Network identity checks live in ``status``.
    """
    findings: List[Tuple[str, str, str]] = []
    channels = load_registry()
    if not channels:
        return [('ERROR', '-', 'channels.yaml has no channels; nothing to do')]
    ledger = load_ledger()
    seen: Dict[str, str] = {}

    for key, info in channels.items():
        info = info or {}
        if not KEY_RE.match(key):
            findings.append(('ERROR', key,
                             'key has characters that will break the '
                             'youtube_token_<key>.json filename'))
        for field in ('email', 'slug', 'token_dir'):
            if not str(info.get(field) or '').strip():
                findings.append(('ERROR', key, f'missing {field}'))
        if not isinstance(info.get('active'), bool):
            findings.append(('WARN', key, "active should be true or false"))

        pipelines = info.get('pipelines') or []
        if not pipelines:
            findings.append(('ERROR', key, 'no pipelines: this channel is '
                                           'attached to nothing'))
        unknown = [p for p in pipelines if p not in PIPELINE_TOKEN_DIRS]
        if unknown:
            findings.append(('WARN', key, f'unknown pipeline(s) {unknown}'))
        elif pipelines:
            expected_dir = token_dir_for(pipelines)
            actual_dir = str(info.get('token_dir') or '').strip()
            if actual_dir and actual_dir != expected_dir:
                findings.append((
                    'ERROR', key,
                    f'token_dir {actual_dir} does not match pipelines '
                    f'{list(pipelines)} (expected {expected_dir}); the token '
                    'will be minted where the pipeline will not look for it'))

        borrowed = str(info.get('client_from') or '').strip()
        if borrowed and borrowed not in channels:
            findings.append(('ERROR', key,
                             f'client_from: {borrowed} is not a known channel'))

        declared = str(info.get('channel_id') or '').strip()
        recorded = str((ledger.get(key) or {}).get('channel_id') or '').strip()
        if declared and recorded and declared != recorded:
            findings.append((
                'ERROR', key,
                f'channels.yaml says {declared} but channel_identity.json says '
                f'{recorded}. The YAML wins at runtime, so one of these is a '
                'lie -- confirm with status before authenticating anything'))
        elif not declared and recorded:
            findings.append(('WARN', key,
                             f'channel_id is empty in YAML but the ledger has '
                             f'{recorded}; run --sync to write it in'))
        elif not declared and not recorded:
            findings.append(('WARN', key, 'unbound: no channel_id anywhere, so '
                                          'the first auth defines it'))

        if declared:
            if declared in seen and seen[declared] != key:
                findings.append((
                    'ERROR', key,
                    f'channel_id {declared} is also claimed by '
                    f'{seen[declared]}; two keys cannot be the same channel'))
            seen.setdefault(declared, key)

        creds = credentials_for(key, channels)
        if not creds.exists():
            level = 'ERROR' if info.get('active') else 'WARN'
            findings.append((level, key, f'credentials.json missing at {creds}'))
        if str(info.get('client_status') or '').startswith('deleted'):
            findings.append(('WARN', key,
                             'its own OAuth client is deleted in Google Cloud; '
                             'auth only works via client_from'))
        if str(info.get('token_dir') or '').strip():
            token = token_path(key, info)
            if not token.exists():
                level = 'WARN' if info.get('active') else 'INFO'
                findings.append((level, key, 'no token on this machine yet'))

    return findings


def print_audit(findings: Sequence[Tuple[str, str, str]],
                show_info: bool = False) -> int:
    """Print an audit and return the number of ERROR-level findings."""
    errors = 0
    for level, key, message in findings:
        if level == 'INFO' and not show_info:
            continue
        if level == 'ERROR':
            errors += 1
        print(f'{level:5} {key}: {message}')
    if not findings:
        print('registry clean: every channel has an owner, a lane, a token dir '
              'and a binding')
    return errors


__all__ = ['RegistryError', 'PIPELINE_TOKEN_DIRS', 'add_channel', 'audit',
           'credentials_for', 'find_block', 'print_audit', 'set_active',
           'set_channel_id', 'token_dir_for', 'token_path']
