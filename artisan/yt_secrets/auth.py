#!/usr/bin/env python3
"""Human-run, foreground OAuth for every configured YouTube channel.

Run from the artisan directory:
    python -m yt_secrets auth                      # every active channel
    python -m yt_secrets auth --all                # every channel in the registry
    python -m yt_secrets auth --channel NXS --channel rankdrop
    python -m yt_secrets auth --pipeline ranking
    python -m yt_secrets status --all
    python -m yt_secrets list --plain
    python -m yt_secrets doctor
    python -m yt_secrets sync
    python -m yt_secrets add --channel new_key --email a@b.com --slug draallan0 \\
        --pipeline shorts --pipeline clipper
    python -m yt_secrets bind --channel NXS --channel-id UC...

Or just double-click ``reauth_all_channels.bat`` in the repo root, which does the
whole registry one channel at a time with the right account named on screen.

This process owns its callback server for its whole lifetime. Do not launch it
through a daemon, scheduler, agent shell, or redirected background process.

WHAT CHANGED 2026-08-19
-----------------------
This flow used to resolve the channel, print its name, and write the token
regardless of whether it was the *right* channel. That is how the
``wealth_mindset`` token ended up authenticated against **Chop UG** and four
clips were published to the wrong channel on 8/16 -- an operator signed into the
wrong Google account, and the tooling agreed with them.

Now the resolved channel is compared against the key's binding
(:mod:`yt_secrets.identity`) *before* the token file is written. A mismatch
writes nothing and explains which account to use instead. An unbound key binds
on first auth and is enforced from then on.

The other operational sharp edge handled here is ``deleted_client``: when a
channel's Google Cloud OAuth client has been deleted (flick_shorts'
``929304292327-aggfh...``), Google returns an opaque error that no retry fixes.
That now prints the runbook, and ``client_from:`` in channels.yaml lets a
channel borrow a live client as configuration instead of as folklore.

WHAT CHANGED 2026-08-23
-----------------------
Three things that were left to the operator, and therefore did not happen:

1. **The channel_id was printed, not written.** Every ``channel_id`` in
   channels.yaml was still ``''``, so the guard was relying entirely on the
   machine-written ledger, which learns whatever it is shown first. The verified
   id is now written straight into the registry by
   :mod:`yt_secrets.registry` (comment-preserving), at the one moment we have
   proof of what it is. ``--no-write-registry`` opts out.
2. **Picking the right Google account was a table in a markdown file.** Signing
   in as the wrong account is *the* failure mode this module exists to catch, so
   ``chrome_profile:`` in channels.yaml now launches the consent page directly
   in that account's Chrome profile. Prevention beats detection.
3. **A batch of channels died on the first failure.** Re-authenticating twelve
   channels means twelve browser consents; aborting the remaining eleven because
   one popped a ``deleted_client`` wasted the whole sitting. ``--keep-going``
   (the default for multi-channel runs) collects failures and prints a summary
   at the end instead. An identity REFUSAL is still always reported loudly.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Install PyYAML first: python -m pip install PyYAML") from exc

try:  # package-relative first (python -m yt_secrets)
    from . import registry
    from .identity import (DELETED_CLIENT_RUNBOOK, ChannelIdentityError,
                           assert_identity, bind as bind_identity,
                           client_source, expected_channel_id,
                           looks_like_deleted_client)
except ImportError:  # pragma: no cover - direct script execution
    import registry  # type: ignore
    from identity import (DELETED_CLIENT_RUNBOOK, ChannelIdentityError,
                          assert_identity, bind as bind_identity,
                          client_source, expected_channel_id,
                          looks_like_deleted_client)

HERE = Path(__file__).resolve().parent
LEGACY_DIR = HERE.parent / "yt-secrets"
REPO_ROOT = HERE.parent.parent
REGISTRY = LEGACY_DIR / "channels.yaml"
TOKEN_URI = "https://oauth2.googleapis.com/token"
YT_CHANNELS_URI = "https://youtube.googleapis.com/youtube/v3/channels"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def load_channels() -> Dict[str, Dict[str, Any]]:
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {}
    return data.get("channels", {})


def token_path(key: str, info: Dict[str, Any]) -> Path:
    return REPO_ROOT / info["token_dir"] / f"youtube_token_{key}.json"


def credentials_path(key: str, info: Dict[str, Any],
                     channels: Dict[str, Dict[str, Any]],
                     override: Optional[str] = None) -> Path:
    """Where this channel's OAuth client JSON lives.

    Honours ``client_from:`` so a channel whose own Google Cloud client was
    deleted can borrow a live one. The grant is per-Google-account, so borrowing
    a client does not change which channel the resulting token controls -- only
    which project's quota it spends.
    """
    if override:
        return Path(override).expanduser().resolve()
    source_key = client_source(key)
    source = channels.get(source_key, info) if source_key != key else info
    if source_key != key:
        print(f"[{key}] borrowing the {source_key} OAuth client "
              f"(client_from in channels.yaml)")
    return LEGACY_DIR / source["slug"] / "credentials.json"


def installed_client(doc: Dict[str, Any]) -> Dict[str, Any]:
    client = doc.get("installed") or doc.get("web")
    if not client or not client.get("client_id") or not client.get("client_secret"):
        raise ValueError("credentials.json must contain an installed or web OAuth client")
    return client


def build_auth_url(client: Dict[str, Any], redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client["client_id"], "redirect_uri": redirect_uri,
        "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true", "state": state,
    }
    return client.get("auth_uri", "https://accounts.google.com/o/oauth2/auth") + "?" + urllib.parse.urlencode(params)


# ---------------------------------------------------------------------------
# Opening the consent page in the RIGHT account
# ---------------------------------------------------------------------------
def chrome_executable() -> Optional[Path]:
    override = (os.getenv("MILO_CHROME_EXE") or "").strip()
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.exists() else None
    for root in (os.getenv("PROGRAMFILES"), os.getenv("PROGRAMFILES(X86)"),
                 os.getenv("LOCALAPPDATA")):
        if not root:
            continue
        candidate = Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if candidate.exists():
            return candidate
    return None


def open_consent(url: str, profile: str = "") -> None:
    """Open the consent URL, in the channel's own Chrome profile when known.

    The default browser opens whichever Google account was last used, which is
    exactly how the 8/16 mix-up happened: the operator was signed in as the
    wrong account and clicked approve. When channels.yaml gives a
    ``chrome_profile``, we launch that profile directly so the account shown is
    already the right one. The identity gate still has the final say.
    """
    if profile and os.name == "nt":
        exe = chrome_executable()
        if exe:
            try:
                subprocess.Popen([str(exe), f"--profile-directory={profile}", url])
                print(f"    opened Chrome profile: {profile}")
                return
            except OSError as exc:
                print(f"    could not launch Chrome ({exc}); using the default browser")
        else:
            print("    chrome.exe not found; using the default browser "
                  "(set MILO_CHROME_EXE to point at it)")
    webbrowser.open(url)


def post_form(url: str, values: Dict[str, str]) -> Dict[str, Any]:
    """POST a form to Google, surfacing the error BODY rather than just 400.

    Google puts the useful part (``deleted_client``, ``invalid_grant``) in the
    response body, which urllib hides behind ``HTTP Error 400: Bad Request``.
    Reading it is the difference between a five-minute fix and an afternoon.
    """
    request = urllib.request.Request(url, data=urllib.parse.urlencode(values).encode(), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = ''
        try:
            body = exc.read().decode('utf-8', 'replace')
        except Exception:
            pass
        if looks_like_deleted_client(body):
            raise RuntimeError('deleted_client: ' + DELETED_CLIENT_RUNBOOK) from exc
        raise RuntimeError(f'{exc.code} {exc.reason}: {body[:400]}') from exc


def exchange(client: Dict[str, Any], code: str, redirect_uri: str) -> Dict[str, Any]:
    return post_form(TOKEN_URI, {
        "client_id": client["client_id"], "client_secret": client["client_secret"],
        "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri,
    })


def refresh(doc: Dict[str, Any]) -> Dict[str, Any]:
    return post_form(TOKEN_URI, {
        "client_id": doc["client_id"], "client_secret": doc["client_secret"],
        "refresh_token": doc["refresh_token"], "grant_type": "refresh_token",
    })


def verify(doc: Dict[str, Any]) -> Tuple[str, str]:
    """Refresh the token and report ``(channel_title, channel_id)``."""
    refreshed = refresh(doc)
    access_token = refreshed.get("access_token")
    if not access_token:
        raise RuntimeError("Google returned no access token during refresh")
    request = urllib.request.Request(YT_CHANNELS_URI + "?part=snippet&mine=true")
    request.add_header("Authorization", "Bearer " + access_token)
    with urllib.request.urlopen(request, timeout=60) as response:
        items = json.load(response).get("items", [])
    if not items:
        raise RuntimeError("OAuth succeeded, but this Google account owns no YouTube channel")
    snippet = items[0].get("snippet", {})
    return str(snippet.get("title") or "(unnamed channel)"), str(items[0].get("id") or "")


def token_document(client: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    expires = int(values.get("expires_in", 3600))
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires)).replace(microsecond=0)
    doc = {
        "token": values.get("access_token"), "refresh_token": values.get("refresh_token"),
        "token_uri": TOKEN_URI, "client_id": client["client_id"],
        "client_secret": client["client_secret"], "scopes": SCOPES,
        "universe_domain": "googleapis.com", "expiry": expiry.isoformat().replace("+00:00", "Z"),
    }
    if not doc["refresh_token"]:
        raise RuntimeError("Google did not return a refresh token; retry with consent prompt")
    return doc


def foreground_flow(key: str, info: Dict[str, Any], client: Dict[str, Any],
                    timeout: int, use_profile: bool = True) -> Dict[str, Any]:
    result: Dict[str, str] = {}
    nonce = secrets.token_urlsafe(12)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            return
        def do_GET(self) -> None:  # noqa: N802
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state", [""])[0] != nonce:
                result["error"] = "state mismatch"
            else:
                result["code"] = query.get("code", [""])[0]
                result["error"] = query.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h2>Authentication received. You can close this tab.</h2>")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/oauth2callback"
    url = build_auth_url(client, redirect_uri, nonce)
    wanted, source = expected_channel_id(key)
    profile = str(info.get("chrome_profile") or "").strip() if use_profile else ""
    print(f"\n[{key}] Sign in as {info['email']} and approve access.")
    if wanted:
        print(f"[{key}] This key is bound to channel {wanted} (per {source}). "
              "Signing in as any other account will be REJECTED and no token "
              "will be written.")
    else:
        print(f"[{key}] This key has no channel binding yet; the channel you "
              "approve becomes its binding. Make sure it is the right one.")
    print("Opening the URL in your browser. If it does not open, copy this exact URL:\n")
    print(url)
    open_consent(url, profile)
    server.timeout = 1
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline and not result:
            server.handle_request()
    finally:
        server.server_close()
    if result.get("error"):
        raise RuntimeError(f"OAuth error: {result['error']}")
    if not result.get("code"):
        raise TimeoutError(f"no callback received after {timeout // 60} minutes")
    return exchange(client, result["code"], redirect_uri)


def authenticate(key: str, info: Dict[str, Any], channels: Dict[str, Dict[str, Any]],
                 override: Optional[str], timeout: int,
                 rebind: bool = False, write_registry: bool = True,
                 force_registry: bool = False,
                 use_profile: bool = True) -> Tuple[str, str]:
    """Mint, VERIFY THE IDENTITY, write the token, then record the binding.

    The order is the fix. Writing first and verifying second is what let a
    wrong-account token become the live credential for a channel key. Recording
    the binding last means channels.yaml only ever learns ids that survived the
    gate.
    """
    creds = credentials_path(key, info, channels, override)
    if not creds.exists():
        raise FileNotFoundError(
            f"credentials missing: {creds} (download the {info['slug']} project "
            "OAuth JSON, or set client_from: in channels.yaml to borrow a live "
            "client)")
    client = installed_client(json.loads(creds.read_text(encoding="utf-8")))
    doc = token_document(client, foreground_flow(key, info, client, timeout,
                                                use_profile=use_profile))
    title, channel_id = verify(doc)

    # THE GATE. Nothing is written until the channel is proven to be this key's.
    if rebind and channel_id:
        bind_identity(key, channel_id, title, rebind=True)
    assert_identity(key, channel_id, title, context=f'auth --channel {key}')

    out = token_path(key, info)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    try:
        tmp.replace(out)
    finally:
        if tmp.exists():
            tmp.unlink()
    print(f"OK  {key}: {title} ({channel_id}), refreshed successfully -> {out}")
    if write_registry:
        record_binding(key, channel_id, force=force_registry or rebind)
    return title, channel_id


def record_binding(key: str, channel_id: str, force: bool = False) -> None:
    """Write the proven channel id into channels.yaml, or say why we did not.

    A failure here is a warning rather than an error: the token on disk is
    already correct and the ledger already knows the binding. But it is printed
    every time, because an unfilled registry is what made the 8/16 incident
    silent in the first place.
    """
    try:
        if registry.set_channel_id(key, channel_id, force=force):
            print(f"    channels.yaml updated: {key}.channel_id = {channel_id}")
        else:
            print(f"    channels.yaml already records {key} = {channel_id}")
    except registry.RegistryError as exc:
        print(f"    WARNING: channel_id not written to channels.yaml: {exc}")


def select_keys(channels: Dict[str, Dict[str, Any]],
                wanted: Optional[Sequence[str]] = None,
                pipeline: str = "", include_all: bool = False,
                unbound_only: bool = False) -> List[str]:
    """Resolve a channel selection, complaining about unknown keys immediately.

    Default (no flags) stays what it always was: every ``active: true`` channel.
    ``--all`` is the whole registry, which is what a token-expiry sweep wants,
    since inactive channels expire too and a dead token is discovered at the
    worst possible moment -- when you activate the lane.
    """
    if wanted:
        unknown = [key for key in wanted if key not in channels]
        if unknown:
            raise KeyError(", ".join(unknown))
        keys = list(dict.fromkeys(wanted))
    elif include_all:
        keys = list(channels)
    else:
        keys = [k for k, v in channels.items() if (v or {}).get("active", False)]
    if pipeline:
        keys = [k for k in keys
                if pipeline in ((channels[k] or {}).get("pipelines") or [])]
    if unbound_only:
        keys = [k for k in keys if not expected_channel_id(k)[0]]
    return keys


def list_channels(channels: Dict[str, Dict[str, Any]], keys: Sequence[str],
                  plain: bool = False, keys_only: bool = False) -> int:
    """Print the selection. ``--plain`` is the machine-readable form the .bat reads."""
    for key in keys:
        info = channels[key] or {}
        bound, source = expected_channel_id(key)
        if keys_only:
            print(key)
            continue
        fields = [key,
                  str(info.get("email") or "-"),
                  "active" if info.get("active") else "inactive",
                  str(info.get("chrome_profile") or "-"),
                  bound or "-",
                  ",".join(info.get("pipelines") or []) or "-"]
        if plain:
            print("|".join(fields))
        else:
            print(f"{key:22} {fields[2]:8} {fields[1]:26} "
                  f"{fields[3]:10} {fields[5]:16} {bound or 'UNBOUND'}"
                  + (f" ({source})" if bound else ""))
    return 0


def status(keys: Iterable[str]) -> int:
    """Refresh-check every token AND report whether it is the right channel.

    A token that refreshes cleanly against the wrong channel is the dangerous
    case, so a mismatch is reported as BAD rather than OK-with-a-note.
    """
    channels = load_channels()
    failures = 0
    for key in keys:
        info = channels[key]
        path = token_path(key, info)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            title, channel_id = verify(doc)
        except Exception as exc:
            failures += 1
            print(f"BAD {key}: {exc}")
            continue
        wanted, source = expected_channel_id(key)
        if wanted and channel_id != wanted:
            failures += 1
            print(f"BAD {key}: WRONG CHANNEL -- token is {title} ({channel_id}) "
                  f"but {key} is bound to {wanted} per {source}")
        elif wanted:
            print(f"OK  {key}: {title} ({channel_id}), refresh works, identity "
                  f"matches {source}")
        else:
            print(f"OK  {key}: {title} ({channel_id}), refresh works, "
                  f"UNBOUND -- add  channel_id: {channel_id}  to channels.yaml")
    return 1 if failures else 0


def sync(channels: Dict[str, Dict[str, Any]], keys: Sequence[str],
         force: bool = False) -> int:
    """Fill channels.yaml from tokens that already work. No browser, no consent.

    This is the backfill for the twelve empty ``channel_id: ''`` values: on a
    machine that already holds good tokens, every id can be resolved and written
    in one pass, which upgrades the guard from ledger-trust to reviewable-in-git.
    """
    failures = 0
    for key in keys:
        info = channels[key] or {}
        path = token_path(key, info)
        if not path.exists():
            print(f"--  {key}: no token on this machine, nothing to sync")
            continue
        try:
            title, channel_id = verify(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            failures += 1
            print(f"BAD {key}: {exc}")
            continue
        wanted, source = expected_channel_id(key)
        if wanted and channel_id != wanted and not force:
            failures += 1
            print(f"BAD {key}: token is {title} ({channel_id}) but {key} is "
                  f"bound to {wanted} per {source}. Nothing written -- this is a "
                  f"real mismatch, re-auth the channel instead.")
            continue
        print(f"OK  {key}: {title} ({channel_id})")
        record_binding(key, channel_id, force=force)
    return 1 if failures else 0


def run_batch(channels: Dict[str, Dict[str, Any]], keys: Sequence[str],
              args: argparse.Namespace) -> int:
    """Authenticate a selection and summarise, instead of dying on channel one."""
    keep_going = args.keep_going or (len(keys) > 1 and not args.stop_on_error)
    results: List[Tuple[str, str]] = []
    for position, key in enumerate(keys, start=1):
        if len(keys) > 1:
            print(f"\n=== [{position}/{len(keys)}] {key} "
                  f"({channels[key].get('email', 'unknown account')}) ===")
        try:
            authenticate(key, channels[key], channels, args.credentials,
                         args.timeout_minutes * 60, rebind=args.rebind,
                         write_registry=not args.no_write_registry,
                         force_registry=args.force_registry,
                         use_profile=not args.no_chrome_profile)
            results.append((key, "OK"))
        except ChannelIdentityError as exc:
            # Not a crash: a refusal. Never let it scroll past unlabelled.
            print(f"\nREFUSED {key}: {exc}\n", file=sys.stderr)
            results.append((key, "REFUSED (wrong Google account)"))
            if not keep_going:
                break
        except KeyboardInterrupt:
            print(f"\nSKIPPED {key}: interrupted by you", file=sys.stderr)
            results.append((key, "SKIPPED"))
            if not keep_going:
                break
        except Exception as exc:
            print(f"FAIL {key}: {exc}", file=sys.stderr)
            results.append((key, f"FAIL ({type(exc).__name__})"))
            if not keep_going:
                break

    done = [k for k, outcome in results if outcome == "OK"]
    broken = [(k, outcome) for k, outcome in results if outcome != "OK"]
    if len(keys) > 1:
        print(f"\n--- {len(done)}/{len(keys)} channels authenticated ---")
        for key, outcome in broken:
            print(f"    {key}: {outcome}")
        skipped = [k for k in keys if k not in dict(results)]
        for key in skipped:
            print(f"    {key}: not attempted")
    return 1 if broken or len(done) != len(keys) else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m yt_secrets")
    sub = parser.add_subparsers(dest="command", required=True)

    def selection_args(target: argparse.ArgumentParser) -> None:
        target.add_argument("--channel", action="append", default=[],
                            help="registry key; repeatable. Omit to use every "
                                 "active channel")
        target.add_argument("--all", action="store_true",
                            help="every channel in channels.yaml, not just active ones")
        target.add_argument("--pipeline", default="",
                            help="restrict to one lane: shorts, ranking, pov, clipper")
        target.add_argument("--unbound", action="store_true",
                            help="only channels with no channel_id yet")

    auth = sub.add_parser("auth", help="authenticate one channel, a lane, or everything")
    selection_args(auth)
    auth.add_argument("--credentials", help="override credentials.json for this run")
    auth.add_argument("--timeout-minutes", type=int, default=60)
    auth.add_argument("--rebind", action="store_true",
                      help="allow this key to move to a different YouTube "
                           "channel (required for a genuine migration)")
    auth.add_argument("--no-write-registry", action="store_true",
                      help="do not write the resolved channel_id into channels.yaml")
    auth.add_argument("--force-registry", action="store_true",
                      help="overwrite a different channel_id already in channels.yaml")
    auth.add_argument("--no-chrome-profile", action="store_true",
                      help="use the default browser instead of the channel's "
                           "chrome_profile")
    auth.add_argument("--keep-going", action="store_true",
                      help="carry on after a failure (default for multi-channel runs)")
    auth.add_argument("--stop-on-error", action="store_true",
                      help="stop at the first failure even in a multi-channel run")

    check = sub.add_parser("status", help="refresh-check tokens, channel names and identity bindings")
    selection_args(check)

    lister = sub.add_parser("list", help="show the registry selection")
    selection_args(lister)
    lister.add_argument("--plain", action="store_true",
                        help="pipe-delimited: key|email|active|chrome_profile|channel_id|pipelines")
    lister.add_argument("--keys-only", action="store_true")

    doctor = sub.add_parser("doctor", help="audit channels.yaml for mismatches, offline")
    doctor.add_argument("--verbose", action="store_true", help="include INFO findings")

    syncer = sub.add_parser("sync", help="write channel_id into channels.yaml from existing tokens")
    selection_args(syncer)
    syncer.add_argument("--force", action="store_true",
                        help="overwrite a conflicting channel_id (dangerous)")

    adder = sub.add_parser("add", help="register a NEW channel on a pipeline")
    adder.add_argument("--channel", required=True, help="new registry key")
    adder.add_argument("--email", required=True, help="owning Google account")
    adder.add_argument("--slug", required=True,
                       help="OAuth project folder under artisan/yt-secrets/")
    adder.add_argument("--pipeline", action="append", required=True,
                       help="lane this channel publishes to; repeatable")
    adder.add_argument("--token-dir", default="",
                       help="override the token dir implied by the pipeline")
    adder.add_argument("--chrome-profile", default="",
                       help='Chrome profile that is signed into --email, e.g. "Profile 3"')
    adder.add_argument("--client-from", default="",
                       help="borrow another channel's OAuth client")
    adder.add_argument("--inactive", action="store_true",
                       help="add it but leave active: false")
    adder.add_argument("--no-auth", action="store_true",
                       help="only edit channels.yaml, do not authenticate now")
    adder.add_argument("--timeout-minutes", type=int, default=60)

    binder = sub.add_parser("bind", help="record which YouTube channel a key means")
    binder.add_argument("--channel", required=True)
    binder.add_argument("--channel-id", required=True)
    binder.add_argument("--title", default="")
    binder.add_argument("--rebind", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "doctor":
        errors = registry.print_audit(registry.audit(), show_info=args.verbose)
        if errors:
            print(f"\n{errors} error-level finding(s): fix these before minting "
                  "tokens, they are the ones that publish to the wrong place.")
        return 1 if errors else 0

    channels = load_channels()

    if args.command == "bind":
        if args.channel not in channels:
            parser.error(f"unknown channel: {args.channel}")
        try:
            bind_identity(args.channel, args.channel_id, args.title,
                          rebind=args.rebind)
            registry.set_channel_id(args.channel, args.channel_id,
                                    force=args.rebind)
        except (ChannelIdentityError, registry.RegistryError) as exc:
            print(f"FAIL {args.channel}: {exc}", file=sys.stderr)
            return 1
        print(f"bound {args.channel} -> {args.channel_id}")
        return 0

    if args.command == "add":
        try:
            token_dir = registry.add_channel(
                args.channel, email=args.email, slug=args.slug,
                pipelines=args.pipeline, token_dir=args.token_dir,
                active=not args.inactive, chrome_profile=args.chrome_profile,
                client_from=args.client_from)
        except registry.RegistryError as exc:
            print(f"FAIL {args.channel}: {exc}", file=sys.stderr)
            return 1
        print(f"added {args.channel} to channels.yaml "
              f"(pipelines: {', '.join(args.pipeline)}, tokens: {token_dir})")
        if args.no_auth:
            print("Authenticate it when ready:  reauth_all_channels.bat "
                  f"--channel {args.channel}")
            return 0
        channels = load_channels()
        args.credentials = None
        args.rebind = False
        args.no_write_registry = False
        args.force_registry = False
        args.no_chrome_profile = False
        args.keep_going = False
        args.stop_on_error = True
        return run_batch(channels, [args.channel], args)

    try:
        keys = select_keys(channels, args.channel, args.pipeline, args.all,
                          args.unbound)
    except KeyError as exc:
        parser.error(f"unknown channel(s): {exc.args[0]}")
    if not keys:
        parser.error("that selection matched no channels in channels.yaml")

    if args.command == "list":
        return list_channels(channels, keys, plain=args.plain,
                             keys_only=args.keys_only)
    if args.command == "status":
        return status(keys)
    if args.command == "sync":
        return sync(channels, keys, force=args.force)
    return run_batch(channels, keys, args)


if __name__ == "__main__":
    raise SystemExit(main())
