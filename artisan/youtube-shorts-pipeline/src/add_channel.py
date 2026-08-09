"""One-shot helper: add + authenticate a brand-new YouTube upload channel.

Self-serve so Allan (or anyone) can bring a channel online without Milo:

    python -m src.add_channel my_new_channel [--niche capital_mindset]

Steps:
  1. Runs the one-time OAuth login for ``<name>`` (opens the browser, writes
     ``config/youtube_token_<name>.json``, returns the channel ID).
  2. If ``--niche`` is given, binds the channel to that niche in
     ``config/niches.yaml`` surgically (line-based insert that preserves
     comments -- never a yaml round-trip dump).
  3. Prints the channel ID and what changed.

The nicest way to run it on Windows: double-click ``add_channel.bat`` in the
project root. It will ask for the channel name (and optionally a niche) and do
the rest.
"""

import argparse
import sys
from pathlib import Path

try:
    from .config import config, PROJECT_ROOT
    from .uploader import YouTubeUploader
except ImportError:  # pragma: no cover - direct script execution
    from config import config, PROJECT_ROOT
    from uploader import YouTubeUploader


def _niche_block_lines(lines: list, niche: str):
    """Return the line span (start, end) of a niche's YAML block.

    ``end`` is exclusive: the block runs [start, end). Detection stops at the
    next top-level key (a non-comment line with no leading whitespace and a
    colon), so a niche that isn't last in the file still gets only its own
    lines.
    """
    start = end = None
    for i, line in enumerate(lines):
        if not line.strip() or line.strip().startswith('#') or line[0].isspace():
            continue
        if ':' not in line:
            continue
        key = line.split(':', 1)[0].strip()
        if key == niche:
            start = i
            continue
        if start is not None:
            end = i
            break
    if start is None:
        return None
    return start, end if end is not None else len(lines)


def bind_channel_to_niche(channel: str, niche: str) -> bool:
    """Add ``channel`` to ``niche.upload_channels`` in niches.yaml.

    Uses a line-based insert so YAML comments and formatting survive. If the
    niche has an ``upload_channels`` list we append to it; if it only has a
    legacy ``channel:`` we add a fresh ``upload_channels:`` line right after
    it. No-op when already bound. Returns True if the file changed.
    """
    path = Path(config.niches_file)
    lines = path.read_text(encoding='utf-8').splitlines()

    span = _niche_block_lines(lines, niche)
    if span is None:
        print(f"Error: niche '{niche}' not found in {path.name}", file=sys.stderr)
        return False

    start, end = span
    body = lines[start:end]
    indent = '  '
    list_indent = None
    for i, line in enumerate(body):
        if line.lstrip().startswith('-'):
            list_indent = line[:len(line) - len(line.lstrip())]
            break
    if list_indent is None:
        list_indent = '    '
    for i, line in enumerate(body):
        stripped = line.strip()
        if stripped.startswith('upload_channels:'):
            # Append to the existing list (skip comments/blank lines).
            last = i
            while last + 1 < len(body) and (body[last + 1].strip() == '' or
                                            body[last + 1].lstrip().startswith('#')):
                last += 1
            if last + 1 < len(body) and body[last + 1].lstrip().startswith('-'):
                target = last + 1
                while target + 1 < len(body) and body[target + 1].lstrip().startswith('-'):
                    target += 1
                if any(f'- {channel}' == body[j].strip() for j in range(last + 1, target + 1)):
                    print(f"Channel '{channel}' already bound to niche '{niche}'.")
                    return False
                body.insert(target + 1, f"{list_indent}- {channel}")
                print(f"Bound '{channel}' to niche '{niche}' (upload_channels).")
            else:
                body.insert(last + 1, f"{list_indent}- {channel}")
                print(f"Bound '{channel}' to niche '{niche}' (upload_channels).")
            break
        if stripped.startswith('channel:'):
            # Legacy binding only -- add upload_channels above it.
            key_indent = line[:len(line) - len(line.lstrip())]
            item_indent = key_indent + '  '
            body.insert(i, f"{key_indent}upload_channels:")
            body.insert(i + 1, f"{item_indent}- {channel}")
            print(f"Bound '{channel}' to niche '{niche}' (added upload_channels).")
            break
    else:
        # Niche exists but has neither key; append both after the header.
        body.append('')
        body.append(f"{indent}upload_channels:")
        body.append(f"{indent}  - {channel}")
        print(f"Bound '{channel}' to niche '{niche}' (created upload_channels).")

    lines[start:end] = body
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return True


def add_channel(channel: str, niche: str = None) -> int:
    channel_id = YouTubeUploader.auth_for_channel(channel)
    if not channel_id:
        print("Error: authentication returned no channel ID.", file=sys.stderr)
        return 1

    print(f"Channel '{channel}' authenticated: {channel_id}")

    if niche:
        bind_channel_to_niche(channel, niche)
    else:
        print("No niche given -- channel is authenticated but not bound. "
              "Rerun with --niche <name> to bind it to a niche, or edit "
              "config/niches.yaml manually.")

    authed = config.authenticated_channels()
    print(f"Authenticated channels now: {', '.join(authed) or '(none)'}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Add + authenticate a new upload channel')
    parser.add_argument('channel', help='Channel key, e.g. wealth_mindset')
    parser.add_argument('--niche', default=None,
                        help='Niche to bind the channel to, e.g. capital_mindset')
    args = parser.parse_args(argv)
    return add_channel(args.channel, args.niche)


if __name__ == '__main__':
    sys.exit(main())
