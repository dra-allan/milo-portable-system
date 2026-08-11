#!/usr/bin/env python3
"""
uploader.py - POV upload stage (YouTube Data API v3, resumable).
================================================================

Adapted from ``artisan/youtube-shorts-pipeline/src/uploader.py``. Same OAuth
model and the same ``config/youtube_token_<channel>.json`` layout, with three
changes for POV:

* **The channel is a parameter**, not config state, so the stage is testable
  and reusable (``--channel explaination``).
* **Long-form defaults**: category 27 (Education), no ``#Shorts`` tag, and
  the source file is never deleted after upload (the shorts uploader unlinks
  it; a POV project is an archive).
* **A stdlib fallback**. When ``google-api-python-client`` is not installed,
  the upload runs on ``urllib`` alone using the refresh token already in the
  token file. That keeps the VPS dependency-free after the one-time auth.

One-time auth (needs ``google-auth-oauthlib``, do it on the dev machine)::

    python -m uploader auth --channel explaination

That writes ``config/youtube_token_explaination.json``. Copy that file to the
VPS and uploads work there with the standard library alone.

Inputs for a project::

    07_METADATA.txt              title / description / tags
    04_THUMBNAIL/thumbnail.png   optional - a missing one only warns
    output_pro/*.mp4             the assembled video (single match expected)
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import povconfig
from povconfig import eprint

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
CHUNK_SIZE = 8 * 1024 * 1024          # 8 MB, must be a multiple of 256 KB
DEFAULT_CATEGORY_ID = "27"            # Education. 24 = Entertainment.
MAX_TAGS_CHARS = 500                  # YouTube's hard limit across all tags
HTTP_TIMEOUT = 120

Notify = Callable[[str, str], None]


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


@dataclass
class VideoMeta:
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def valid(self) -> bool:
        return bool(self.title.strip())


_LABEL_RE = re.compile(
    r"^\s*(?:#+\s*)?(?:\*\*)?(TITLE|DESCRIPTION|TAGS|HOOK|TIMESTAMPS|CHAPTERS)"
    r"(?:\*\*)?\s*[:\-]?\s*(.*)$", re.IGNORECASE)


def parse_metadata(path: Path) -> VideoMeta:
    """Parse ``07_METADATA.txt`` into title / description / tags.

    ``agents/POV-seo-specialist.md`` specifies plain text (explicitly no
    JSON, no markdown) with a title, a hook line, chapter timestamps and at
    least 25 tags. Agents drift, so this parser is deliberately tolerant:

    * a labelled ``TITLE:`` line wins; otherwise the first non-empty line is
      the title
    * everything between the description/hook label and the tags label is the
      description, with chapter timestamps kept (they belong in the
      description on YouTube)
    * tags are comma-separated, or one per line, with bullets and ``#``
      prefixes stripped
    """
    meta = VideoMeta()
    if not path.exists():
        return meta

    text = path.read_text(encoding="utf-8-sig", errors="replace")
    section = ""
    desc: list[str] = []
    tag_lines: list[str] = []
    loose: list[str] = []

    for line in text.splitlines():
        m = _LABEL_RE.match(line)
        if m:
            label = m.group(1).upper()
            rest = m.group(2).strip()
            if label == "TITLE":
                section = "title"
                if rest:
                    meta.title = rest
                continue
            if label in ("DESCRIPTION", "HOOK"):
                section = "description"
                if rest:
                    desc.append(rest)
                continue
            if label in ("TIMESTAMPS", "CHAPTERS"):
                section = "description"
                if rest:
                    desc.append(rest)
                continue
            if label == "TAGS":
                section = "tags"
                if rest:
                    tag_lines.append(rest)
                continue

        stripped = line.strip()
        if section == "title" and stripped and not meta.title:
            meta.title = stripped
            section = ""
        elif section == "description":
            desc.append(line.rstrip())
        elif section == "tags":
            tag_lines.append(stripped)
        elif stripped:
            loose.append(stripped)

    if not meta.title and loose:
        meta.title = loose[0]
    if not desc and len(loose) > 1:
        desc = loose[1:]

    meta.description = "\n".join(desc).strip()
    meta.tags = clean_tags(tag_lines)
    return meta


def clean_tags(lines: list[str]) -> list[str]:
    """Normalise tag lines and enforce YouTube's 500-char total."""
    raw: list[str] = []
    for line in lines:
        for piece in re.split(r"[,\n]", line):
            tag = piece.strip().lstrip("-*#").strip().strip('"').strip()
            if tag and len(tag) <= 100:
                raw.append(tag)

    out: list[str] = []
    seen: set[str] = set()
    used = 0
    for tag in raw:
        key = tag.lower()
        if key in seen:
            continue
        # YouTube counts the characters of every tag plus separators.
        cost = len(tag) + (1 if out else 0)
        if used + cost > MAX_TAGS_CHARS:
            break
        seen.add(key)
        out.append(tag)
        used += cost
    return out


def find_video(project_dir: Path) -> Path | None:
    """The assembled MP4. Globs ``output_pro/`` and takes the largest match."""
    out_dir = project_dir / "output_pro"
    if not out_dir.is_dir():
        return None
    videos = sorted(out_dir.glob("*.mp4"), key=lambda p: p.stat().st_size,
                    reverse=True)
    return videos[0] if videos else None


def find_thumbnail(project_dir: Path) -> Path | None:
    for name in ("thumbnail.png", "thumbnail.jpg", "thumbnail.jpeg"):
        candidate = project_dir / "04_THUMBNAIL" / name
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def token_path(channel: str) -> Path:
    override = os.environ.get("POV_YOUTUBE_TOKEN", "").strip()
    if override:
        return Path(override).expanduser()
    return povconfig.secrets_dir() / f"youtube_token_{channel}.json"


def client_secrets_path() -> Path:
    override = os.environ.get("POV_OAUTH_CLIENT_SECRETS", "").strip()
    if override:
        return Path(override).expanduser()
    local = povconfig.secrets_dir() / "credentials.json"
    if local.exists():
        return local
    # The shorts pipeline already holds a client-secrets file; reuse it
    # rather than making the operator register a second OAuth client.
    return povconfig.HERE.parent / "youtube-shorts-pipeline" / "credentials.json"


def access_token(channel: str) -> str | None:
    """Exchange the stored refresh token for an access token (stdlib).

    Returns None (after logging) when the token file is missing or the
    refresh fails - the caller reports a clean upload failure instead of
    raising.
    """
    path = token_path(channel)
    if not path.exists():
        eprint(f"[upload] no token for channel '{channel}': {path}")
        eprint(f"[upload] run: python -m uploader auth --channel {channel}")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        eprint(f"[upload] token file unreadable ({exc}): {path}")
        return None

    payload = urllib.parse.urlencode({
        "client_id": data.get("client_id", ""),
        "client_secret": data.get("client_secret", ""),
        "refresh_token": data.get("refresh_token", ""),
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        return body.get("access_token")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300] if exc.fp else ""
        eprint(f"[upload] token refresh HTTP {exc.code}: {detail}")
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        eprint(f"[upload] token refresh failed: {exc}")
    return None


def authorize(channel: str) -> str | None:
    """One-time interactive OAuth. Writes config/youtube_token_<channel>.json.

    This is the only code path that needs ``google-auth-oauthlib``; run it
    once on a machine with a browser, then copy the token file.
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
    except ImportError:
        eprint("[auth] google-auth-oauthlib is not installed. Install it just "
               "for this one-time step:  pip install google-auth-oauthlib")
        return None

    secrets = client_secrets_path()
    if not secrets.exists():
        eprint(f"[auth] OAuth client secrets not found: {secrets}")
        eprint("[auth] set POV_OAUTH_CLIENT_SECRETS or drop credentials.json "
                f"into {povconfig.secrets_dir()}")
        return None

    creds = InstalledAppFlow.from_client_secrets_file(
        str(secrets), SCOPES).run_local_server(port=0)
    out = token_path(channel)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass
    print(f"[auth] token saved -> {out}")
    print("[auth] this file is a secret. It is gitignored; never commit it.")
    return str(out)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def _build_body(meta: VideoMeta, *, privacy: str, published_at: str | None,
                category_id: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "snippet": {
            "title": meta.title[:100],
            "description": meta.description[:5000],
            "tags": meta.tags,
            "categoryId": str(category_id),
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    if published_at:
        # A scheduled publish requires the video to start private.
        body["status"]["privacyStatus"] = "private"
        body["status"]["publishAt"] = published_at
    return body


def _upload_via_google_client(video: Path, body: dict, token_file: Path) -> str | None:
    """Preferred path: google-api-python-client, same as the shorts pipeline."""
    try:
        from google.auth.transport.requests import Request  # type: ignore
        from google.oauth2.credentials import Credentials  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
        from googleapiclient.http import MediaFileUpload  # type: ignore
    except ImportError:
        return None

    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    media = MediaFileUpload(str(video), chunksize=CHUNK_SIZE, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body,
                                      media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[upload] {int(status.progress() * 100)}%")
    return response.get("id")


def _upload_via_stdlib(video: Path, body: dict, token: str) -> str | None:
    """Fallback: resumable upload protocol over urllib, no third-party deps.

    1. POST the metadata with ``uploadType=resumable`` to get a session URL.
    2. PUT the file in ``CHUNK_SIZE`` chunks with ``Content-Range`` headers.
       308 means "keep going", 200/201 means done.
    """
    size = video.stat().st_size
    meta_bytes = json.dumps(body).encode("utf-8")
    start_url = UPLOAD_URL + "?" + urllib.parse.urlencode({
        "part": "snippet,status", "uploadType": "resumable"})
    req = urllib.request.Request(start_url, data=meta_bytes, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json; charset=UTF-8")
    req.add_header("X-Upload-Content-Length", str(size))
    req.add_header("X-Upload-Content-Type", "video/mp4")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            session = resp.headers.get("Location")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400] if exc.fp else ""
        eprint(f"[upload] session start HTTP {exc.code}: {detail}")
        return None
    except (urllib.error.URLError, OSError) as exc:
        eprint(f"[upload] session start failed: {exc}")
        return None
    if not session:
        eprint("[upload] no resumable session URL returned")
        return None

    sent = 0
    with video.open("rb") as fh:
        while sent < size:
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                break
            last = sent + len(chunk) - 1
            put = urllib.request.Request(session, data=chunk, method="PUT")
            put.add_header("Content-Length", str(len(chunk)))
            put.add_header("Content-Range", f"bytes {sent}-{last}/{size}")
            try:
                with urllib.request.urlopen(put, timeout=HTTP_TIMEOUT) as resp:
                    payload = json.loads(resp.read().decode("utf-8", "replace"))
                    return payload.get("id")
            except urllib.error.HTTPError as exc:
                if exc.code == 308:            # Resume Incomplete: expected
                    sent = last + 1
                    print(f"[upload] {int(sent / size * 100)}%")
                    continue
                detail = exc.read().decode("utf-8", "replace")[:400] if exc.fp else ""
                eprint(f"[upload] chunk HTTP {exc.code}: {detail}")
                return None
            except (urllib.error.URLError, OSError) as exc:
                eprint(f"[upload] chunk failed at byte {sent}: {exc}")
                return None
    return None


def set_thumbnail(video_id: str, thumb: Path, token: str) -> bool:
    """Attach the thumbnail. A failure here never fails the upload."""
    ctype = mimetypes.guess_type(thumb.name)[0] or "image/png"
    url = THUMBNAIL_URL + "?" + urllib.parse.urlencode({"videoId": video_id})
    try:
        data = thumb.read_bytes()
    except OSError as exc:
        eprint(f"[upload] thumbnail unreadable: {exc}")
        return False
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT):
            return True
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300] if exc.fp else ""
        eprint(f"[upload] thumbnail HTTP {exc.code}: {detail}")
    except (urllib.error.URLError, OSError) as exc:
        eprint(f"[upload] thumbnail failed: {exc}")
    return False


@dataclass
class UploadResult:
    ok: bool = False
    video_id: str = ""
    url: str = ""
    reason: str = ""
    dry_run: bool = False


def upload_project(project_dir: Path, *, channel: str = "explaination",
                   privacy: str = "unlisted", published_at: str | None = None,
                   category_id: str = DEFAULT_CATEGORY_ID,
                   dry_run: bool = False,
                   notify: Notify | None = None) -> UploadResult:
    """Upload one finished project. Never raises."""
    print("\n" + "=" * 60)
    print("  YOUTUBE UPLOAD")
    print("=" * 60)

    def _notify(event: str, message: str) -> None:
        if notify is None:
            return
        try:
            notify(event, message)
        except Exception as exc:
            eprint(f"[notify] {type(exc).__name__}: {exc}")

    def _fail(reason: str) -> UploadResult:
        eprint(f"[upload] FAIL - {reason}")
        povconfig.log_line("upload.failed", f"{project_dir.name}: {reason}",
                           level="error", echo=False,
                           path=project_dir / "state" / "pipeline.log")
        _notify("upload.failed", f"POV {project_dir.name}: upload failed - {reason}")
        return UploadResult(ok=False, reason=reason)

    video = find_video(project_dir)
    if not video:
        return _fail("no assembled video in output_pro/ (run --stage assemble)")

    meta = parse_metadata(project_dir / "07_METADATA.txt")
    if not meta.valid():
        return _fail("07_METADATA.txt missing or has no title")

    thumb = find_thumbnail(project_dir)
    if not thumb:
        # Explicitly non-fatal: a missing thumbnail costs CTR, not the batch.
        eprint("[upload] WARN - no thumbnail found; uploading without one")

    body = _build_body(meta, privacy=privacy, published_at=published_at,
                       category_id=category_id)
    size_mb = video.stat().st_size / (1024 * 1024)

    print(f"  channel     : {channel}")
    print(f"  video       : {video} ({size_mb:.1f} MB)")
    print(f"  thumbnail   : {thumb or '(none)'}")
    print(f"  title       : {body['snippet']['title']}")
    print(f"  privacy     : {body['status']['privacyStatus']}"
          + (f" (publishAt {published_at})" if published_at else ""))
    print(f"  tags        : {len(meta.tags)} "
          f"({sum(len(t) for t in meta.tags)} chars)")
    print(f"  description : {len(body['snippet']['description'])} chars")

    if dry_run:
        print("\n  --- DRY RUN: nothing was sent to YouTube ---")
        print(json.dumps(body, indent=2, ensure_ascii=False)[:4000])
        return UploadResult(ok=True, dry_run=True, reason="dry run")

    token_file = token_path(channel)
    video_id = None
    if token_file.exists():
        try:
            video_id = _upload_via_google_client(video, body, token_file)
        except Exception as exc:  # library present but unhappy - fall back
            eprint(f"[upload] google client failed ({type(exc).__name__}: {exc}); "
                   "falling back to the stdlib uploader")
            video_id = None

    token = access_token(channel)
    if not video_id:
        if not token:
            return _fail(f"no usable credentials for channel '{channel}'")
        video_id = _upload_via_stdlib(video, body, token)

    if not video_id:
        return _fail("videos.insert did not return a video id")

    url = f"https://www.youtube.com/watch?v={video_id}"
    if thumb and token:
        if set_thumbnail(video_id, thumb, token):
            print("[upload] thumbnail set")
        else:
            eprint("[upload] WARN - thumbnail could not be set (video is live)")

    record_upload(project_dir, video_id, url, privacy=body["status"]["privacyStatus"])
    print(f"[upload] OK - {url}")
    povconfig.log_line("upload.success", f"{project_dir.name}: {url}", echo=False,
                       path=project_dir / "state" / "pipeline.log")
    _notify("upload.success", f"POV {project_dir.name} is up: {url}")
    return UploadResult(ok=True, video_id=video_id, url=url)


def record_upload(project_dir: Path, video_id: str, url: str, *,
                  privacy: str = "") -> None:
    """Write the upload result into state/manifest.json (additive only)."""
    path = project_dir / "state" / "manifest.json"
    doc: dict[str, Any] = {}
    if path.exists():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            doc = {}
    doc["youtube_video_id"] = video_id
    doc["uploaded_video_url"] = url
    doc["upload_privacy"] = privacy
    doc["status"] = "UPLOADED"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    except OSError as exc:
        eprint(f"[upload] could not update the manifest: {exc}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="uploader", description="POV YouTube uploader (ExplaiNation)")
    sub = ap.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("auth", help="one-time OAuth for a channel")
    p_auth.add_argument("--channel", default="explaination")

    p_up = sub.add_parser("upload", help="upload one project")
    p_up.add_argument("--project", required=True)
    p_up.add_argument("--channel", default="explaination")
    p_up.add_argument("--privacy", default="unlisted",
                      choices=["private", "unlisted", "public"])
    p_up.add_argument("--published-at", default=None,
                      help="ISO8601 scheduled publish time")
    p_up.add_argument("--dry-run", action="store_true")

    args = ap.parse_args(argv)

    if args.command == "auth":
        return 0 if authorize(args.channel) else 1

    project_dir = povconfig.projects_dir() / args.project
    if not project_dir.is_dir():
        eprint(f"[upload] project not found: {project_dir}")
        return 1
    result = upload_project(project_dir, channel=args.channel,
                            privacy=args.privacy,
                            published_at=args.published_at,
                            dry_run=args.dry_run)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
