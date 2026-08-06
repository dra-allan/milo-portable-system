"""
cli.py — the one command.
=========================

``milo`` (and ``mylo`` — same binary, same everything) is the whole interface.
Six scripts collapsed into one dispatcher.

Design rules:

* **Every command works before install.** ``milo doctor`` on a bare machine
  tells you what's missing instead of crashing.
* **Nothing is interactive unless it has to be.** Every prompt has a flag.
* **Typos resolve.** ``milo remembr``, ``milo bakcup``, ``mylo skils`` all hit
  the right command via :func:`miloctl.naming.match_command`.
* **Machine-readable on request.** ``--json`` on anything that lists.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import __version__, naming, paths, ui


# ── helpers ───────────────────────────────────────────────────────────────────


def _emit(data: Any, as_json: bool) -> bool:
    """Print JSON and return True if we handled the output."""
    if not as_json:
        return False
    print(json.dumps(data, indent=2, default=str))
    return True


def _brain():
    from .memory import store
    return store()


def _stranded_routines() -> List[tuple]:
    """Enabled routines routed at a channel that cannot actually deliver.

    Returns ``(routine_name, channel_name)`` pairs. This is the check that
    would have caught the original bug: a routine shipping its output to
    Telegram on a machine where Telegram was never configured looks perfectly
    healthy from every other angle.
    """
    from . import channels

    out: List[tuple] = []
    try:
        from .routines import store as routine_store

        for r in routine_store().all(include_disabled=False):
            for target in str(getattr(r, "output", "")).replace(",", " ").split():
                if target in {"log", "vault", "memory", "none", ""}:
                    continue
                ch = channels.get(target)
                if ch is None or not ch.configured():
                    out.append((r.name, target))
    except Exception:
        # Doctor must never crash; a missing routine store is its own check.
        pass
    return out


def _fail(msg: str) -> int:
    ui.err(msg)
    return 1


# ── install ───────────────────────────────────────────────────────────────────


def cmd_install(args: argparse.Namespace) -> int:
    from . import env, harness, migrate, persona
    from .naming import display_name

    who = display_name()
    ui.banner(f"{who} — install", f"v{__version__} on {paths.platform_id()}")

    paths.ensure_tree()
    ui.ok(f"state tree ready at {paths.milo_home()}")

    # 1. Secrets
    if args.no_prompt:
        ui.info("skipping credential prompts (--no-prompt)")
    else:
        ui.say()
        ui.step("Credentials — press Enter to skip any of these.")
        ui.say(ui.dim("Stored only in " + str(paths.env_file()) + " (chmod 600)."))
        ui.say()
        existing = env.load(include_os=False)
        updates: Dict[str, str] = {}
        for key, label, default, secret in env.FIELDS:
            if args.only and key not in args.only:
                continue
            current = existing.get(key, "")
            shown = env.mask(current) if (secret and current) else current
            value = ui.ask(label, default=shown or default,
                           secret_hint="hidden" if secret else "")
            if value and value != shown:
                updates[key] = value
        if updates:
            env.update(updates)
            ui.ok(f"saved {len(updates)} setting(s)")

    # 2. Paths sanity
    ui.say()
    ui.step("Locations")
    for label, key in (("Milo home", "home"), ("Vault", "vault"),
                       ("Workspace", "workspace"), ("Skills", "skills")):
        ui.kv(label, paths.describe().get(key, "?"))
    v = paths.vault_dir()
    if not v.is_dir():
        ui.warn(f"vault not found at {v}")
        repo = env.get("MILO_VAULT_REPO") or "https://github.com/dra-allan/dra-brains"
        if not args.no_prompt and ui.confirm(f"clone {repo} there now?", True):
            import subprocess
            v.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["git", "clone", repo, str(v)],
                               capture_output=True, text=True)
            ui.ok("vault cloned") if r.returncode == 0 else ui.err(
                (r.stderr or "clone failed").strip().split("\n")[-1]
            )

    # 3. Legacy memory
    ui.say()
    ui.step("Legacy memory")
    found = migrate.discover_legacy()
    if found:
        total = sum(len(v) for v in found.values())
        ui.info(f"found {total} legacy store(s): {', '.join(found)}")
        if args.no_prompt or ui.confirm("import them into the unified brain?", True):
            report = migrate.migrate_all()
            ui.say(report.render())
    else:
        ui.info("none found — nothing to import")

    # 4. Restore a snapshot if one is sitting in the repo
    from .backup import restore, state_dir_for
    snap = state_dir_for()
    if snap.is_dir() and (snap / "memory.jsonl").is_file():
        ui.say()
        ui.step("Snapshot")
        if args.no_prompt or ui.confirm(f"restore the snapshot in {snap}?", True):
            ui.say(restore(snap, pull=False).render())

    # 5. Bundled skills + persona + harnesses
    ui.say()
    ui.step("Skills & persona")
    from .skills import registry
    reg = registry()
    ui.info(f"{len(reg.all())} skill(s) available")
    persona.write_identity()

    results = harness.sync_all(args.harness or None)
    for r in results:
        ui.say(r.render())

    ui.say()
    ui.ok(f"{who} is installed.")
    ui.say()
    ui.say("  Next:")
    ui.say("    milo doctor          check everything")
    ui.say("    milo remember \"...\"  save your first memory")
    ui.say("    milo backup          push the brain somewhere safe")
    ui.say()
    ui.say(ui.dim("  Both spellings work: milo and mylo."))
    return 0


# ── doctor ────────────────────────────────────────────────────────────────────


def cmd_doctor(args: argparse.Namespace) -> int:
    from . import backup, env, harness
    from .naming import display_name
    import shutil as _sh
    import time as _time

    checks: List[Dict[str, Any]] = []

    def check(name: str, ok_: bool, detail: str = "", fix: str = "",
              level: str = "error") -> None:
        checks.append({"name": name, "ok": bool(ok_), "detail": detail,
                       "fix": fix, "level": "ok" if ok_ else level})

    check("python", sys.version_info >= (3, 8),
          platform_detail := f"{sys.version_info.major}.{sys.version_info.minor}",
          "Milo needs Python 3.8+")
    check("git", bool(_sh.which("git")), _sh.which("git") or "not found",
          "install git — backup and restore depend on it")

    home = paths.milo_home()
    check("state tree", home.is_dir(), str(home), "run: milo install")
    check(".env", paths.env_file().is_file(), str(paths.env_file()),
          "run: milo install", level="warn")

    try:
        stats = _brain().stats()
        check("memory db", True,
              f"{stats.get('live', stats.get('total_rows', 0))} memories, "
              f"fts={stats.get('fts')}")
    except Exception as exc:
        check("memory db", False, str(exc), "run: milo install")

    v = paths.vault_dir()
    if v.is_dir():
        from .vault import vault
        vs = vault().stats()
        check("vault", True,
              f"{vs.get('notes', 0)} notes, git={vs.get('git')}"
              + (", uncommitted changes" if vs.get("dirty") else ""))
    else:
        check("vault", False, f"missing at {v}",
              "clone dra-brains there, or set MILO_VAULT_DIR", level="warn")

    from .skills import registry
    sk = registry().stats()
    check("skills", True, f"{sk.get('active', 0)} active, {sk.get('archived', 0)} archived")

    installed = [h for h in harness.detect_installed() if h.name != "generic"]
    check("harness", bool(installed),
          ", ".join(h.label for h in installed) or "none detected",
          "install OpenCode or Claude Code", level="warn")
    for h in harness.detect_installed():
        st = h.status()
        check(f"  {h.name} synced", bool(st["synced"]), st["config_dir"],
              f"run: milo sync {h.name}", level="warn")

    # Channels are checked here because their failure mode is silence: a
    # routine with output:telegram reports success whether or not anything
    # was delivered, so nothing else in the system would ever mention it.
    from . import channels

    ready = [c.name for c in channels.configured_channels() if c.name != "log"]
    check("channels", bool(ready), ", ".join(ready) or "none — log only",
          "set up Telegram or ntfy: milo channels", level="warn")

    # A routine pointed at a channel that is not configured delivers nothing,
    # forever, without complaining. Name the exact routines.
    stranded = _stranded_routines()
    check("  routine delivery", not stranded,
          "all routines can reach their channel" if not stranded
          else "; ".join(f"{name} → {ch}" for name, ch in stranded),
          "run: milo channels --test", level="warn")

    # The OS scheduler is the heartbeat that makes every routine real.
    # Registration is not health — a battery-blocked task never fires, which
    # is the exact silent failure that loses backups.
    from . import scheduler
    healthy = scheduler.healthy()
    ok_sched = any(h.ok for h in healthy)
    check("scheduler", ok_sched,
          "; ".join(f"{h.backend}: {h.detail}" for h in healthy)
          or "no scheduler backend",
          "run: milo routines install", level="error")

    last = backup.last_backup_time()
    if last:
        days = (_time.time() - last) / 86400
        check("backup", days < 7, f"{days:.1f} days ago",
              "run: milo backup", level="warn")
    else:
        check("backup", False, "never", "run: milo backup", level="warn")

    missing = env.missing(required_only=True)
    check("credentials", not missing,
          "all required present" if not missing else f"missing: {', '.join(missing)}",
          "run: milo install", level="warn")

    leaks = env.scan_paths([backup.state_dir_for()]) if backup.state_dir_for().is_dir() else []
    check("no leaked secrets", not leaks,
          "clean" if not leaks else f"{len(leaks)} finding(s) in the snapshot",
          "remove the secret, then: milo backup")

    if _emit({"checks": checks}, args.json):
        return 0 if all(c["ok"] or c["level"] == "warn" for c in checks) else 1

    ui.banner(f"{display_name()} — doctor", f"v{__version__} · {paths.platform_id()}")
    failed = warned = 0
    for c in checks:
        if c["ok"]:
            ui.ok(f"{c['name']:<20} {ui.dim(c['detail'])}")
        elif c["level"] == "warn":
            warned += 1
            ui.warn(f"{c['name']:<20} {c['detail']}")
            if c["fix"]:
                ui.say(f"    {ui.dim(ui.SYM['arrow'] + ' ' + c['fix'])}")
        else:
            failed += 1
            ui.err(f"{c['name']:<20} {c['detail']}")
            if c["fix"]:
                ui.say(f"    {ui.dim(ui.SYM['arrow'] + ' ' + c['fix'])}")
    ui.say()
    if failed:
        ui.err(f"{failed} problem(s), {warned} warning(s)")
    elif warned:
        ui.warn(f"healthy, {warned} warning(s)")
    else:
        ui.ok("everything healthy")
    return 1 if failed else 0


# ── memory ────────────────────────────────────────────────────────────────────


def cmd_remember(args: argparse.Namespace) -> int:
    content = " ".join(args.text).strip()
    if not content:
        if sys.stdin.isatty():
            return _fail('nothing to remember — try: milo remember "..."')
        content = sys.stdin.read().strip()
    if not content:
        return _fail("nothing to remember")
    mem, created = _brain().save(
        content,
        title=args.title or "",
        category=args.category,
        project=args.project,
        tags=args.tags or [],
        importance=args.importance,
        pinned=args.pin,
        source="cli",
    )
    if _emit({"created": created, **mem.to_dict()}, args.json):
        return 0
    ui.ok(f"{'saved' if created else 'already knew that, refreshed'}: "
          f"[{mem.category}] {mem.summary_line()}")
    ui.say(ui.dim(f"  id {mem.id}"))
    return 0


def cmd_recall(args: argparse.Namespace) -> int:
    query = " ".join(args.query).strip()
    brain = _brain()
    rows = brain.search(query, limit=args.limit) if query else brain.recent(args.limit)
    if _emit([m.to_dict() for m in rows], args.json):
        return 0
    if not rows:
        ui.warn(f"nothing matches {query!r}" if query else "no memories yet")
        return 0
    for m in rows:
        marker = ui.yellow("*") if m.pinned else " "
        ui.say(f"{marker} {ui.dim('[' + m.category + ']')} {m.content.strip()}")
        if args.verbose:
            ui.say(ui.dim(f"    {m.id} · importance {m.importance} · "
                          f"{', '.join(m.tags) or 'no tags'}"))
    ui.say()
    ui.say(ui.dim(f"  {len(rows)} result(s)"))
    return 0


def cmd_about(args: argparse.Namespace) -> int:
    name = " ".join(args.name).strip()
    data = _brain().about(name)
    if _emit(data, args.json):
        return 0
    ent = data.get("entity")
    ui.banner(name, (ent or {}).get("kind", "") if ent else "no entity record")
    if ent and ent.get("summary"):
        ui.say(ent["summary"])
        ui.say()
    for m in data.get("memories", []):
        ui.say(f"  - {m.get('content', '').strip()}")
    rel = data.get("relations", [])
    if rel:
        ui.say()
        ui.step("Relations")
        for r in rel:
            ui.say(f"  {r.get('subject')} {ui.dim(r.get('predicate',''))} {r.get('object')}")
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    ok_ = _brain().forget(args.id, hard=args.hard)
    ui.ok(f"{'deleted' if args.hard else 'archived'} {args.id}") if ok_ else ui.warn(
        f"no memory with id {args.id}"
    )
    return 0 if ok_ else 1


def cmd_memory(args: argparse.Namespace) -> int:
    brain = _brain()
    if args.action == "stats":
        s = brain.stats()
        if _emit(s, args.json):
            return 0
        ui.banner("Memory")
        for k, v in s.items():
            ui.kv(k, str(v))
        return 0
    if args.action == "export":
        out = Path(args.out) if args.out else paths.milo_home() / "memory-export.jsonl"
        n = brain.export_jsonl(out)
        ui.ok(f"exported {n} memories → {out}")
        return 0
    if args.action == "import":
        if not args.path:
            return _fail("need a path: milo memory import <file.jsonl>")
        counts = brain.import_jsonl(Path(args.path))
        ui.ok(f"imported {counts}")
        return 0
    if args.action == "dedupe":
        ui.ok(f"merged {brain.dedupe()} duplicate(s)")
        return 0
    if args.action == "expire":
        ui.ok(f"expired {brain.expire()} memory(ies)")
        return 0
    if args.action == "pin":
        return 0 if brain.pin(args.path or "", True) else _fail("no such memory")
    if args.action == "unpin":
        return 0 if brain.pin(args.path or "", False) else _fail("no such memory")
    if args.action == "compress":
        # Optional args: --days, --importance-threshold
        days = getattr(args, 'days', 30)
        importance_threshold = getattr(args, 'importance-threshold', 2)
        result = brain.compress(
            days=days,
            importance_threshold=importance_threshold
        )
        if _emit(result, args.json):
            return 0
        if result.get("compressed_count", 0) > 0:
            ui.ok(f"Compressed {result['compressed_count']} memories into {result.get('summary_count', 0)} summary memories")
            if result.get("archived_count", 0) > 0:
                ui.info(f"Archived {result['archived_count']} original memories")
        else:
            ui.info("No memories were compressed (no candidates found)")
        return 0
    if args.action == "reflect":
        # Optional arg: --days (for reflection period)
        days = getattr(args, 'reflect_days', 7)
        result = brain.reflect(days=days)
        if _emit(result, args.json):
            return 0
        if result.get("reflections_generated", 0) > 0:
            ui.ok(f"Generated {result['reflections_generated']} reflections from the last {days} day(s)")
        else:
            ui.info("No reflections generated")
        return 0
    return _fail(f"unknown action {args.action}")


# ── backup / restore / migrate ────────────────────────────────────────────────


def cmd_backup(args: argparse.Namespace) -> int:
    from .backup import backup as do_backup
    res = do_backup(push=not args.no_push, sync_vault=not args.no_vault,
                    message=args.message or "")
    if _emit({"ok": res.ok, "counts": res.counts, "pushed": res.pushed,
              "leaks": [[str(p), k, s] for p, k, s in res.leaks],
              "error": res.error}, args.json):
        return 0 if res.ok else 1
    ui.say(res.render())
    if args.verbose and res.git_log:
        ui.say(ui.dim(res.git_log))
    return 0 if res.ok else 1


def cmd_restore(args: argparse.Namespace) -> int:
    from .backup import restore as do_restore, unarchive
    src = Path(args.source) if args.source else None
    if src and src.is_file() and src.suffix in (".gz", ".tgz"):
        res = unarchive(src, merge=not args.replace)
    else:
        res = do_restore(src, merge=not args.replace, pull=not args.no_pull)
    if _emit({"ok": res.ok, "counts": res.counts, "missing": res.missing,
              "error": res.error}, args.json):
        return 0 if res.ok else 1
    ui.say(res.render())
    if res.ok:
        ui.say()
        ui.say(ui.dim("  Next: milo sync   (write the persona into your tools)"))
    return 0 if res.ok else 1


def cmd_archive(args: argparse.Namespace) -> int:
    from .backup import archive as do_archive
    try:
        out = do_archive(Path(args.out) if args.out else None,
                         include_env=args.include_secrets)
    except RuntimeError as exc:
        return _fail(str(exc))
    ui.ok(f"archive written → {out}")
    if args.include_secrets:
        ui.warn("this archive contains real credentials — delete it after transfer")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    from . import migrate as mig
    if args.list:
        found = mig.discover_legacy()
        if _emit({k: [str(p) for p in v] for k, v in found.items()}, args.json):
            return 0
        if not found:
            ui.info("no legacy memory stores found")
            return 0
        for kind, items in found.items():
            ui.step(kind)
            for p in items:
                ui.say(f"  {p}")
        return 0
    report = mig.migrate_all(snapshot=not args.no_snapshot)
    if _emit(report.to_dict(), args.json):
        return 0
    ui.say(report.render())
    return 0


# ── paths / version / serve ───────────────────────────────────────────────────


def cmd_path(args: argparse.Namespace) -> int:
    table = paths.describe()
    if args.name:
        # 'milo path home', 'milo path MILO_HOME' and 'milo path hoem' all work.
        aliases = {
            "home": "MILO_HOME", "milo_home": "MILO_HOME", "root": "MILO_HOME",
            "env": "env_file", "dotenv": "env_file",
            "memory": "memory_db", "brain": "memory_db", "db": "memory_db",
            "sessions": "sessions_db",
            "brains": "vault", "dra_brains": "vault", "obsidian": "vault",
            "opencode": "opencode_config", "claude": "claude_config",
        }
        raw = args.name.strip().replace("-", "_")
        key = aliases.get(raw.lower(), raw)
        if key not in table:
            # Fuzzy against real keys *and* alias names, so 'sessons' finds
            # the 'sessions' alias which then resolves to sessions_db.
            hit = naming.match_command(raw, [*table, *aliases])
            key = aliases.get(hit, hit) if hit else ""
        if key not in table:
            return _fail(f"unknown path {args.name!r}. "
                         f"Known: {', '.join(sorted(table))}")
        print(table[key])
        return 0
    if _emit(table, args.json):
        return 0
    for k in sorted(table):
        ui.kv(k, table[k], width=16)
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    if _emit({"version": __version__, "agent": naming.display_name(),
              "platform": paths.platform_id(),
              "python": sys.version.split()[0]}, args.json):
        return 0
    ui.say(f"{naming.display_name()} {__version__} "
           f"({paths.platform_id()}, python {sys.version.split()[0]})")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .mcp import serve
    serve()
    return 0


# ── parser ────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="milo",
        description=f"{naming.display_name()} — Allan's assistant. "
                    f"'mylo' works too; same command.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
common:
  milo install                 set up on a new machine
  milo doctor                  is everything healthy?
  milo remember "..."          save something durable
  milo recall "query"          search everything Milo knows
  milo backup                  snapshot + push the brain
  milo restore                 pull the brain onto this machine
  milo sync                    write the persona into your agent tools
""",
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    s = sub.add_parser("install", help="set up Milo on this machine")
    s.add_argument("--no-prompt", action="store_true", help="accept defaults, ask nothing")
    s.add_argument("--harness", nargs="*", help="only sync these harnesses")
    s.add_argument("--only", nargs="*", help="only prompt for these .env keys")
    s.set_defaults(func=cmd_install)

    s = sub.add_parser("doctor", help="health check")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("remember", help="save something durable")
    s.add_argument("text", nargs="*")
    s.add_argument("-c", "--category", default="note",
                   help="fact | decision | preference | procedure | note")
    s.add_argument("-t", "--tags", nargs="*")
    s.add_argument("-i", "--importance", type=int, default=3, choices=range(1, 6))
    s.add_argument("--title", default="")
    s.add_argument("--project", default="milo")
    s.add_argument("--pin", action="store_true", help="always keep in context")
    s.set_defaults(func=cmd_remember)

    s = sub.add_parser("recall", help="search memory")
    s.add_argument("query", nargs="*")
    s.add_argument("-n", "--limit", type=int, default=15)
    s.set_defaults(func=cmd_recall)

    s = sub.add_parser("about", help="everything known about a person/project/thing")
    s.add_argument("name", nargs="+")
    s.set_defaults(func=cmd_about)

    s = sub.add_parser("forget", help="archive a memory by id")
    s.add_argument("id")
    s.add_argument("--hard", action="store_true", help="really delete it")
    s.set_defaults(func=cmd_forget)

    s = sub.add_parser("memory", help="memory maintenance")
    s.add_argument("action", choices=["stats", "export", "import", "dedupe",
                                      "expire", "pin", "unpin", "compress", "reflect"])
    s.add_argument("path", nargs="?")
    s.add_argument("-o", "--out")
    # Compression-specific arguments
    s.add_argument("--days", type=int, default=30,
                   help="compress memories older than this many days (default: 30)")
    s.add_argument("--importance-threshold", type=int, default=2,
                   help="compress memories with importance at or below this level (default: 2)")
    # Reflection-specific arguments
    s.add_argument("--reflect-days", dest="reflect_days", type=int, default=7,
                   help="reflect on memories from the last N days (default: 7)")
    s.set_defaults(func=cmd_memory)

    s = sub.add_parser("backup", help="snapshot the brain and push it")
    s.add_argument("-m", "--message", help="commit message")
    s.add_argument("--no-push", action="store_true", help="commit locally only")
    s.add_argument("--no-vault", action="store_true", help="skip the vault repo")
    s.set_defaults(func=cmd_backup)

    s = sub.add_parser("restore", help="load the brain onto this machine")
    s.add_argument("source", nargs="?", help="snapshot dir or .tar.gz")
    s.add_argument("--replace", action="store_true", help="overwrite instead of merge")
    s.add_argument("--no-pull", action="store_true")
    s.set_defaults(func=cmd_restore)

    s = sub.add_parser("archive", help="bundle a snapshot into one .tar.gz")
    s.add_argument("-o", "--out")
    s.add_argument("--include-secrets", action="store_true",
                   help="embed the real .env (offline transfer only)")
    s.set_defaults(func=cmd_archive)

    s = sub.add_parser("migrate", help="import legacy Engram / bot / AgentMemory stores")
    s.add_argument("--list", action="store_true", help="show what would be imported")
    s.add_argument("--no-snapshot", action="store_true")
    s.set_defaults(func=cmd_migrate)

    s = sub.add_parser("path", help="where things live on this machine")
    s.add_argument("name", nargs="?")
    s.set_defaults(func=cmd_path)

    s = sub.add_parser("version", help="version info")
    s.set_defaults(func=cmd_version)

    s = sub.add_parser("serve", help="run the MCP memory server on stdio")
    s.set_defaults(func=cmd_serve)

    register_extra_commands(sub)
    return p


def register_extra_commands(sub) -> None:
    """Subcommands defined in :mod:`miloctl.cli_extra` (skills, vault, ...)."""
    try:
        from . import cli_extra
    except ImportError:
        return
    cli_extra.register(sub)


# ── entry point ───────────────────────────────────────────────────────────────


def _resolve_typo(argv: List[str], parser: argparse.ArgumentParser) -> List[str]:
    """Let ``milo remembr`` mean ``milo remember``."""
    if not argv or argv[0].startswith("-"):
        return argv
    choices: List[str] = []
    for action in parser._subparsers._group_actions if parser._subparsers else []:
        choices = list(getattr(action, "choices", {}) or {})
    if not choices or argv[0] in choices:
        return argv
    hit = naming.match_command(argv[0], choices)
    if hit:
        ui.info(f"assuming you meant '{hit}'")
        return [hit, *argv[1:]]
    return argv


def _force_utf8_stdio() -> None:
    """Windows pipes re-create stdout as cp1252/ascii, which crashes on the
    unicode glyphs the CLI prints (arrows, checkmarks). Pin UTF-8 so those
    print everywhere — same fix as mcp.py's stdio protocol."""
    for stream in (sys.stdout, sys.stderr):
        try:
            reconfigure = stream.reconfigure
        except AttributeError:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    _force_utf8_stdio()
    argv = list(argv if argv is not None else sys.argv[1:])
    parser = build_parser()
    argv = _resolve_typo(argv, parser)
    args = parser.parse_args(argv)

    ui.set_quiet(getattr(args, "quiet", False))
    ui.set_verbose(getattr(args, "verbose", False))

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        ui.say()
        ui.warn("interrupted")
        return 130
    except BrokenPipeError:
        return 0
    except Exception as exc:
        if getattr(args, "verbose", False):
            raise
        ui.err(f"{type(exc).__name__}: {exc}")
        ui.say(ui.dim("  run again with -v for the full traceback"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
