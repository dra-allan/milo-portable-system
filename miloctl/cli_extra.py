"""
cli_extra.py — the agent-facing half of the CLI.
================================================

``cli.py`` owns the machine (install, doctor, backup, restore, paths). This
module owns the *mind*: the persona sync, procedural memory, the learning loop,
the user model, session history and the vault.

Split for one practical reason — a token-truncated edit to one half can never
break the other. ``cli.py`` imports this defensively; if this file is missing
or broken, ``milo doctor`` and ``milo backup`` still run.

Commands registered here::

    milo sync [harness...]      write the persona into your agent tools
    milo prompt                 print the assembled system prompt
    milo skills <action>        list / show / new / edit / lint / archive
    milo learn <request>        turn work into a reusable skill
    milo curate                 age out unused skills, find duplicates
    milo profile <action>       what Milo believes about you
    milo sessions <action>      history, search, insights
    milo vault <action>         the Obsidian cold tier
    milo run <prompt>           run a prompt through the live agent
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import naming, paths, ui


# ── shared helpers ────────────────────────────────────────────────────────────


def _emit(data: Any, as_json: bool) -> bool:
    if not as_json:
        return False
    print(json.dumps(data, indent=2, default=str))
    return True


def _fail(msg: str) -> int:
    ui.err(msg)
    return 1


def _joined(value) -> str:
    """argparse ``nargs='*'`` gives a list; users think in sentences."""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value).strip()
    return str(value or "").strip()


def _open_in_editor(path: Path) -> bool:
    """Open ``path`` in $EDITOR / $VISUAL, falling back sensibly per platform."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        plat = paths.platform_id()
        editor = "notepad" if plat == "windows" else "nano"
    try:
        subprocess.call([*editor.split(), str(path)])
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        ui.warn(f"couldn't launch {editor}: {exc}")
        ui.say(ui.dim(f"  edit it yourself: {path}"))
        return False


# ── sync ──────────────────────────────────────────────────────────────────────


def cmd_sync(args: argparse.Namespace) -> int:
    """Push the assembled persona into every agent tool on this machine."""
    from . import harness, persona

    ctx = persona.build(include_memory=not args.lean,
                        include_vault=not args.lean)
    only = [n for n in (args.harness or []) if n]
    # Pass ctx through: sync_all builds its own default otherwise, which
    # silently discarded --lean while the banner still reported the lean size.
    results = harness.sync_all(only or None, ctx=ctx)

    if not results:
        ui.warn("no agent tools detected on this machine")
        ui.say(ui.dim("  install opencode or claude, then run: milo sync"))
        return 0

    if _emit([{"harness": r.harness, "ok": r.ok,
               "written": [str(p) for p in r.written],
               "skipped": r.skipped, "error": r.error}
              for r in results], args.json):
        return 0

    ui.banner("sync", f"~{ctx.approx_tokens()} tokens of persona")
    for r in results:
        ui.say("  " + r.render())
    failed = [r for r in results if not r.ok]
    ui.say()
    if failed:
        ui.warn(f"{len(failed)} harness(es) had problems")
        return 1
    ui.ok(f"{len(results)} harness(es) in sync")
    return 0


def cmd_prompt(args: argparse.Namespace) -> int:
    """Print the system prompt Milo would use. The debugging window."""
    from . import persona

    ctx = persona.build(
        query=_joined(args.query),
        include_memory=not args.lean,
        include_vault=not args.lean,
    )
    if args.sections:
        for name, body in ctx.sections():
            mark = "•" if body.strip() else "·"
            ui.say(f"  {mark} {name:<12} {len(body):>6} chars")
        ui.say()
        ui.say(ui.dim(f"  ~{ctx.approx_tokens()} tokens total"))
        return 0
    only = args.only.split(",") if args.only else None
    print(ctx.render(include=only))
    return 0


# ── skills ────────────────────────────────────────────────────────────────────


def cmd_skills(args: argparse.Namespace) -> int:
    from .skills import registry

    reg = registry()
    action = args.action
    name = _joined(getattr(args, "name", "")) or ""

    if action == "list":
        skills = reg.all(include_archived=args.all)
        if args.query:
            skills = reg.search(_joined(args.query))
        if _emit([s.to_dict() for s in skills], args.json):
            return 0
        if not skills:
            ui.warn("no skills yet")
            ui.say(ui.dim('  teach one: milo learn "how we deploy the bot"'))
            return 0
        ui.banner("skills", f"{len(skills)} found")
        rows = []
        for s in skills:
            used = reg.usage().get(s.name, {})
            rows.append([
                s.name,
                s.description[:52],
                s.origin,
                s.lifecycle if s.lifecycle != "active" else "",
                str(used.get("count", 0) or ""),
            ])
        ui.table(rows, headers=["skill", "what it does", "from", "state", "uses"])
        return 0

    if action == "index":
        print(reg.index())
        return 0

    if action == "search":
        # The prompt index tells the model to run exactly this when it cannot
        # see a skill it needs, so it has to search everything installed —
        # including packs that are deliberately kept out of the index.
        q = _joined(args.query) or name
        if not q:
            return _fail('what are you looking for? milo skills search "deploy a bot"')
        hits = reg.search(q, limit=args.limit)
        if _emit([s.to_dict() for s in hits], args.json):
            return 0
        if not hits:
            ui.warn(f"nothing matches {q!r}")
            ui.say(ui.dim("  browse what's installed: milo packs list"))
            ui.say(ui.dim(f'  or write it: milo learn "{q}"'))
            return 0
        ui.banner("search", f"{len(hits)} match{'es' if len(hits) != 1 else ''} for {q!r}")
        ui.table(
            [[s.name, s.description[:50], s.origin,
              ", ".join(t for t in s.tags if not t.startswith("pack:"))[:22]]
             for s in hits],
            headers=["skill", "what it does", "from", "tags"],
        )
        ui.say()
        ui.say(ui.dim(f"  read one: milo skills show {hits[0].name}"))
        # A pack skill that is findable but not in the index is only one step
        # from being useful; say so rather than making them go looking.
        off = [s for s in hits if s.origin == "pack"]
        if off:
            ui.say(ui.dim("  keep it in the prompt: "
                          f"milo packs enable {off[0].name}"))
        return 0

    if action == "show":
        if not name:
            return _fail("which skill? milo skills show <name>")
        skill = reg.get(name)
        if not skill:
            close = [s.name for s in reg.search(name, limit=3)]
            return _fail(f"no skill {name!r}"
                         + (f". Close: {', '.join(close)}" if close else ""))
        if _emit(skill.to_dict(), args.json):
            return 0
        print(skill.skill_file.read_text(encoding="utf-8"))
        return 0

    if action == "new":
        if not name:
            return _fail('name it: milo skills new deploy-bot -d "..."')
        desc = args.description or ui.ask("one line: what does it do?")
        skill = reg.create(name, desc, tags=args.tags or [])
        ui.ok(f"created {skill.name}")
        ui.say(ui.dim(f"  {skill.skill_file}"))
        for _n, level, msg in reg.lint(skill.name):
            (ui.err if level == "error" else ui.warn)(f"  {msg}")
        if not args.no_edit:
            _open_in_editor(skill.skill_file)
        return 0

    if action == "edit":
        if not name:
            return _fail("which skill? milo skills edit <name>")
        skill = reg.get(name)
        if not skill:
            return _fail(f"no skill {name!r}")
        if not skill.editable:
            skill = reg.fork(skill.name) or skill
            ui.info(f"forked the bundled skill into {skill.skill_file.parent}")
        _open_in_editor(skill.skill_file)
        for _n, level, msg in reg.lint(skill.name):
            (ui.err if level == "error" else ui.warn)(f"  {msg}")
        return 0

    if action == "lint":
        problems = reg.lint(name or None)
        if _emit([{"skill": n, "level": lv, "message": m}
                  for n, lv, m in problems], args.json):
            return 0
        if not problems:
            ui.ok("all skills lint clean")
            return 0
        for n, level, msg in problems:
            (ui.err if level == "error" else ui.warn)(f"  {n}: {msg}")
        errors = sum(1 for _, lv, _ in problems if lv == "error")
        return 1 if errors else 0

    if action in ("archive", "restore", "remove"):
        if not name:
            return _fail(f"which skill? milo skills {action} <name>")
        if action == "archive":
            s = reg.archive(name)
            return ui.ok(f"archived {name}") or 0 if s else _fail(f"no skill {name!r}")
        if action == "restore":
            s = reg.restore(name)
            return ui.ok(f"restored {name}") or 0 if s else _fail(f"no skill {name!r}")
        if not args.yes and not ui.confirm(f"permanently delete {name}?"):
            return 0
        return 0 if reg.remove(name, hard=True) else _fail(f"no skill {name!r}")

    if action == "stats":
        stats = reg.stats()
        if _emit(stats, args.json):
            return 0
        ui.banner("skills", "")
        for k, v in stats.items():
            ui.kv(k, v, width=12)
        return 0

    if action == "used":
        if not name:
            return _fail("which skill? milo skills used <name>")
        reg.record_use(name, args.outcome or "used")
        ui.ok(f"recorded use of {name}")
        return 0

    return _fail(f"unknown action {action!r}")


# ── learning loop ─────────────────────────────────────────────────────────────


def cmd_learn(args: argparse.Namespace) -> int:
    """Turn what just happened into a durable, reusable skill.

    Prints a prompt for the live agent rather than calling a model itself —
    the agent already has the tools and the context. ``--run`` hands it
    straight to the detected harness.
    """
    from .learning import build_learn_prompt
    from .skills import registry

    request = _joined(args.request)
    if not request:
        return _fail('what should Milo learn? milo learn "how we deploy the bot"')

    reg = registry()
    prompt = build_learn_prompt(request, existing=[s.name for s in reg.all()])

    if args.run:
        return _run_through_harness(prompt, args)
    print(prompt)
    ui.say()
    ui.say(ui.dim("  paste that into Milo, or re-run with --run"))
    return 0


def cmd_improve(args: argparse.Namespace) -> int:
    """Improve a skill while the friction is still fresh."""
    from .learning import build_improve_prompt
    from .skills import registry

    name = _joined(args.name)
    skill = registry().get(name)
    if not skill:
        return _fail(f"no skill {name!r}")
    prompt = build_improve_prompt(skill, _joined(args.note))
    if args.run:
        return _run_through_harness(prompt, args)
    print(prompt)
    return 0


def cmd_curate(args: argparse.Namespace) -> int:
    """Age out unused skills, surface duplicates. Safe to run on a cron."""
    from .learning import Curator
    from .skills import registry

    cur = Curator(registry())
    if args.if_due and not cur.due():
        ui.say(ui.dim("  curator not due yet"))
        return 0

    report = cur.run(dry_run=args.dry_run)
    if _emit(report, args.json):
        return 0

    ui.banner("curator", "dry run" if args.dry_run else "")
    ui.say("  " + report.get("summary", ""))
    for kind, names in (report.get("transitions") or {}).items():
        if names:
            ui.say(f"    {kind}: {', '.join(names)}")
    for problem in (report.get("lint") or [])[:10]:
        ui.warn(f"  {problem}")
    dupes = report.get("duplicates") or []
    if dupes:
        ui.say()
        ui.warn(f"{len(dupes)} possible duplicate pair(s)")
        for d in dupes[:5]:
            ui.say(f"    {d.get('a')} ~ {d.get('b')}")
        ui.say(ui.dim("  merge them: milo curate --merge"))
        if args.merge:
            print()
            print(cur.consolidation_prompt(dupes))
    return 0


def _run_through_harness(prompt: str, args: argparse.Namespace) -> int:
    """Send a prompt to the first available agent runtime."""
    from . import harness

    name = getattr(args, "with_harness", "") or ""
    h = harness.get_harness(name) if name else None
    if h is None:
        installed = harness.detect_installed()
        runnable = [x for x in installed if x.which()]
        if not runnable:
            ui.warn("no agent runtime found — printing the prompt instead")
            print(prompt)
            return 0
        h = runnable[0]
    ui.info(f"running through {h.name}")
    code, out = h.run(prompt, model=getattr(args, "model", "") or "")
    print(out)
    return code


# ── profile ───────────────────────────────────────────────────────────────────


def cmd_profile(args: argparse.Namespace) -> int:
    from .profile import Profile, extraction_prompt

    prof = Profile()
    action = args.action

    if action == "show":
        if _emit(prof.to_dict(), args.json):
            return 0
        md = prof.markdown()
        print(md if md.strip() else "  (nothing learned yet)")
        return 0

    if action == "prompt":
        print(prof.prompt_block())
        return 0

    if action == "set":
        if not args.key or not args.value:
            return _fail('milo profile set tone "direct, no preamble"')
        prof.observe(args.key, _joined(args.value),
                     section=args.section or "general", source="stated")
        prof.save()
        ui.ok(f"noted: {args.key} = {_joined(args.value)}")
        return 0

    if action == "forget":
        if not args.key:
            return _fail("milo profile forget <key>")
        if prof.forget(args.key):
            prof.save()
            ui.ok(f"forgot {args.key}")
            return 0
        return _fail(f"nothing known about {args.key!r}")

    if action == "extract":
        print(extraction_prompt(_joined(args.value)))
        return 0

    if action == "stats":
        stats = prof.stats()
        if _emit(stats, args.json):
            return 0
        for k, v in stats.items():
            ui.kv(k, v, width=14)
        return 0

    if action == "export":
        out = prof.export_markdown(Path(args.out) if args.out else None)
        ui.ok(f"wrote {out}")
        return 0

    return _fail(f"unknown action {action!r}")


# ── sessions ──────────────────────────────────────────────────────────────────


def cmd_sessions(args: argparse.Namespace) -> int:
    from .sessions import store

    st = store()
    action = args.action

    if action == "list":
        rows = st.recent(args.limit)
        if _emit([r.__dict__ for r in rows], args.json):
            return 0
        if not rows:
            ui.warn("no sessions recorded yet")
            return 0
        ui.table(
            [[r.age_label, r.surface, (r.task or "")[:48],
              "active" if r.ended_at is None else "done"] for r in rows],
            headers=["when", "surface", "task", "state"],
        )
        return 0

    if action == "active":
        rows = st.active(include_stale=args.all)
        if _emit([r.__dict__ for r in rows], args.json):
            return 0
        if not rows:
            ui.say(ui.dim("  nothing running"))
            return 0
        for s in rows:
            ui.say(f"  {s.surface:<12} {s.age_label:<10} {(s.task or '')[:50]}")
        return 0

    if action == "search":
        query = _joined(args.query)
        if not query:
            return _fail('milo sessions search "trade copier"')
        hits = st.search(query, limit=args.limit)
        if _emit(hits, args.json):
            return 0
        if not hits:
            ui.warn(f"nothing matching {query!r}")
            return 0
        for h in hits:
            ui.say(f"  {ui.dim(h['when'])}  {h['excerpt'][:88]}")
        return 0

    if action == "insights":
        data = st.insights(args.days)
        if _emit(data, args.json):
            return 0
        ui.banner("insights", f"last {args.days} days")
        for k, v in data.items():
            if isinstance(v, (int, float, str)):
                ui.kv(k, v, width=16)
        top = data.get("top_tools") or []
        if top:
            ui.say()
            ui.say("  most used tools:")
            for name, count in top[:8]:
                ui.say(f"    {name:<24} {count}")
        return 0

    if action == "reap":
        n = st.reap()
        ui.ok(f"closed {n} dead session(s)")
        return 0

    if action == "stats":
        if _emit(st.stats(), args.json):
            return 0
        for k, v in st.stats().items():
            ui.kv(k, v, width=14)
        return 0

    return _fail(f"unknown action {action!r}")


# ── vault ─────────────────────────────────────────────────────────────────────


def cmd_vault(args: argparse.Namespace) -> int:
    from .vault import vault

    v = vault()
    action = args.action

    if action == "status":
        stats = v.stats()
        if _emit(stats, args.json):
            return 0
        ui.banner("vault", str(v.root))
        for k, val in stats.items():
            ui.kv(k, val, width=14)
        if not v.exists:
            ui.say()
            ui.warn("not cloned here yet")
            ui.say(ui.dim(f"  git clone <dra-brains> {v.root}"))
        return 0

    if not v.exists:
        return _fail(f"no vault at {v.root} — clone dra-brains or set MILO_VAULT_DIR")

    if action == "search":
        query = _joined(args.text)
        if not query:
            return _fail('milo vault search "trade copier"')
        hits = v.search(query, limit=args.limit, ignore_case=True)
        if _emit([h.__dict__ for h in hits], args.json):
            return 0
        if not hits:
            ui.warn(f"nothing matching {query!r}")
            return 0
        for h in hits:
            ui.say("  " + h.render())
        return 0

    if action == "note":
        text = _joined(args.text)
        if not text:
            return _fail('milo vault note "shipped the memory rewrite"')
        p = v.append_daily(text, heading=args.heading or "Milo")
        ui.ok(f"appended to {p.name}")
        return 0

    if action == "capture":
        text = _joined(args.text)
        if not text:
            return _fail('milo vault capture "look into MT5 partial fills"')
        p = v.capture(text, title=args.title or "")
        ui.ok(f"captured → {p}")
        return 0

    if action == "promote":
        from .memory import store
        mems = store().recent(args.limit)
        p = v.promote(mems, label=args.title or "session")
        if not p:
            ui.warn("nothing to promote")
            return 0
        ui.ok(f"promoted {len(mems)} memories → {p}")
        return 0

    if action == "sync":
        ok, log = v.sync(args.title or "")
        (ui.ok if ok else ui.err)(log.strip().splitlines()[-1] if log.strip() else "synced")
        return 0 if ok else 1

    if action == "boot":
        ctx = v.boot_context()
        if _emit(ctx, args.json):
            return 0
        for name, body in ctx.items():
            ui.say(ui.bold(f"--- {name} ---"))
            ui.say(body[:1200])
            ui.say()
        return 0

    return _fail(f"unknown action {action!r}")


# ── run ───────────────────────────────────────────────────────────────────────


def cmd_run(args: argparse.Namespace) -> int:
    """Talk to Milo through whichever agent runtime is installed."""
    prompt = _joined(args.prompt)
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    if not prompt:
        return _fail('say something: milo run "what did we decide about the copier?"')
    return _run_through_harness(prompt, args)


def cmd_harness(args: argparse.Namespace) -> int:
    from . import harness

    rows = [h.status() for h in harness.all_harnesses()]
    if _emit(rows, args.json):
        return 0
    ui.table(
        [[r.get("name"), "yes" if r.get("installed") else "no",
          "yes" if r.get("synced") else "no", str(r.get("config_dir"))[:44]]
         for r in rows],
        headers=["tool", "installed", "synced", "config"],
    )
    return 0


# ── registration ──────────────────────────────────────────────────────────────


def register(sub) -> None:
    """Attach every command in this module to the top-level subparsers."""

    s = sub.add_parser("sync", help="write the persona into your agent tools")
    s.add_argument("harness", nargs="*", help="opencode, claude-code, codex, cursor…")
    s.add_argument("--lean", action="store_true",
                   help="identity + skills only, no memory or vault")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("prompt", help="print the assembled system prompt")
    s.add_argument("query", nargs="*", help="bias memory selection toward this")
    s.add_argument("--only", help="comma-separated sections")
    s.add_argument("--sections", action="store_true", help="show the size budget")
    s.add_argument("--lean", action="store_true")
    s.set_defaults(func=cmd_prompt)

    s = sub.add_parser("skills", help="procedural memory — what Milo knows how to do")
    s.add_argument("action", nargs="?", default="list",
                   choices=["list", "index", "search", "show", "new", "edit",
                            "lint", "archive", "restore", "remove", "stats",
                            "used"])
    s.add_argument("name", nargs="?")
    s.add_argument("-d", "--description", default="")
    s.add_argument("-t", "--tags", nargs="*")
    s.add_argument("-q", "--query", nargs="*")
    s.add_argument("-n", "--limit", type=int, default=20)
    s.add_argument("--all", action="store_true", help="include archived")
    s.add_argument("--no-edit", action="store_true")
    s.add_argument("--outcome", default="", help="used | success | failure")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(func=cmd_skills)

    s = sub.add_parser("learn", help="turn what just happened into a skill")
    s.add_argument("request", nargs="*")
    s.add_argument("--run", action="store_true", help="execute via the agent now")
    s.add_argument("--with-harness", default="", dest="with_harness")
    s.add_argument("--model", default="")
    s.set_defaults(func=cmd_learn)

    s = sub.add_parser("improve", help="refine a skill you just used")
    s.add_argument("name")
    s.add_argument("note", nargs="*", help="what was awkward about it")
    s.add_argument("--run", action="store_true")
    s.add_argument("--with-harness", default="", dest="with_harness")
    s.add_argument("--model", default="")
    s.set_defaults(func=cmd_improve)

    s = sub.add_parser("curate", help="age out unused skills, find duplicates")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--if-due", action="store_true", help="no-op unless due (for cron)")
    s.add_argument("--merge", action="store_true", help="print a merge prompt")
    s.set_defaults(func=cmd_curate)

    s = sub.add_parser("profile", help="what Milo believes about you")
    s.add_argument("action", nargs="?", default="show",
                   choices=["show", "prompt", "set", "forget", "extract",
                            "stats", "export"])
    s.add_argument("key", nargs="?")
    s.add_argument("value", nargs="*")
    s.add_argument("--section", default="")
    s.add_argument("-o", "--out")
    s.set_defaults(func=cmd_profile)

    s = sub.add_parser("sessions", help="history, search and usage insights")
    s.add_argument("action", nargs="?", default="list",
                   choices=["list", "active", "search", "insights", "reap", "stats"])
    s.add_argument("query", nargs="*")
    s.add_argument("-n", "--limit", type=int, default=20)
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--all", action="store_true")
    s.set_defaults(func=cmd_sessions)

    s = sub.add_parser("vault", help="the Obsidian cold tier (dra-brains)")
    s.add_argument("action", nargs="?", default="status",
                   choices=["status", "search", "note", "capture", "promote",
                            "sync", "boot"])
    s.add_argument("text", nargs="*")
    s.add_argument("--heading", default="")
    s.add_argument("--title", default="")
    s.add_argument("-n", "--limit", type=int, default=25)
    s.set_defaults(func=cmd_vault)

    s = sub.add_parser("run", help="send a prompt to the live agent")
    s.add_argument("prompt", nargs="*")
    s.add_argument("--with-harness", default="", dest="with_harness")
    s.add_argument("--model", default="")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("harness", help="which agent tools are installed and synced")
    s.set_defaults(func=cmd_harness)

    s = sub.add_parser("packs", aliases=["pack"],
                       help="skill and agent libraries from other people")
    s.add_argument("action", nargs="?", default="list",
                   choices=["list", "ls", "add", "install", "remove", "rm",
                            "uninstall", "update", "search", "enable",
                            "disable", "show"])
    s.add_argument("name", nargs="?", default="")
    s.add_argument("-q", "--query", nargs="*")
    s.add_argument("--rename", default="", help="install under a different name")
    s.add_argument("--enable", nargs="*",
                   help="names, categories or kinds to put in the prompt index")
    s.add_argument("-n", "--limit", type=int, default=20)
    s.add_argument("--yes", action="store_true")
    s.set_defaults(func=cmd_packs)

    s = sub.add_parser("note", aliases=["notes"],
                       help="the small memory Milo carries into every session")
    s.add_argument("action", nargs="?", default="view",
                   choices=["view", "show", "list", "add", "replace", "remove",
                            "rm", "forget", "edit", "clear", "path"])
    s.add_argument("text", nargs="*")
    s.add_argument("--match", default="",
                   help="unique substring of the entry to replace/remove")
    # --user / --memory rather than a positional target: the store you mean is
    # nearly always 'memory', and typing it every time is friction.
    s.add_argument("--user", "--me", dest="target", action="store_const",
                   const="user", help="operate on USER.md instead of MEMORY.md")
    s.add_argument("--memory", dest="target", action="store_const", const="memory")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(func=cmd_note, target=None)

    # ``routines`` is one parser with a free-form action rather than a nest of
    # sub-subparsers, because ``milo routines run backup`` reads the way people
    # already talk about it, and the fuzzy matcher can then fix typos in the
    # action the same way it does for top-level commands.
    s = sub.add_parser("routines", aliases=["routine", "cron"],
                       help="scheduled work Milo does on its own")
    s.add_argument("action", nargs="?", default="list",
                   choices=["list", "init", "add", "remove", "delete",
                            "enable", "disable", "schedule", "run", "tick",
                            "watch", "show", "logs", "install", "uninstall",
                            "status"])
    s.add_argument("name", nargs="?", default="")
    s.add_argument("--prompt", nargs="*", default=[],
                   help="what to ask the agent when this fires")
    # dest is NOT 'command': the top-level subparser already owns that dest,
    # and a flag defaulting to "" would blank it out and make every
    # 'milo routines ...' call fall through to the help text.
    s.add_argument("--command", "--shell", default="", dest="shell_command",
                   help="shell command to run instead of prompting a model")
    s.add_argument("--every", default="",
                   help='"every 30m" | "daily at 07:30" | "weekly on mon at 9" | "cron ..."')
    s.add_argument("--output", default="log",
                   choices=["log", "vault", "memory", "telegram", "none"])
    s.add_argument("--with-harness", default="", dest="with_harness")
    s.add_argument("--model", default="")
    s.add_argument("-t", "--tags", nargs="*")
    s.add_argument("--skip-missed", action="store_true", dest="skip_missed",
                   help="a run missed while the machine was off is dropped, not caught up")
    s.add_argument("--dry-run", action="store_true", dest="dry_run")
    s.add_argument("--force", action="store_true", help="overwrite an existing routine")
    s.add_argument("--backend", default="",
                   help="systemd | crontab | launchd | schtasks | termux | loop")
    s.add_argument("--interval", type=int, default=300, help="seconds, for 'watch'")
    s.add_argument("-n", "--limit", type=int, default=40, help="log lines to show")
    s.set_defaults(func=cmd_routines)


# ── routines ──────────────────────────────────────────────────────────────────


def cmd_routines(args: argparse.Namespace) -> int:
    from .routines import RoutineStore, ScheduleError, describe_schedule

    st = RoutineStore()
    action = args.action
    name = _joined(getattr(args, "name", ""))

    if action == "list":
        rows = st.all()
        if _emit([r.to_dict() for r in rows], args.json):
            return 0
        if not rows:
            ui.warn("no routines yet")
            ui.say(ui.dim("  add the maintenance set: milo routines init"))
            return 0
        ui.banner("routines", f"{sum(1 for r in rows if r.enabled)} enabled")
        from datetime import datetime
        ui.table(
            [[r.name,
              r.schedule_label,
              "on" if r.enabled else "off",
              (datetime.fromtimestamp(r.next_run).strftime("%a %H:%M")
               if r.next_run else "-"),
              r.last_status or "-",
              str(r.runs)] for r in rows],
            headers=["routine", "schedule", "", "next", "last", "runs"],
        )
        return 0

    if action == "init":
        added = st.install_builtins(overwrite=args.force)
        if added:
            ui.ok(f"added {len(added)} routine(s): {', '.join(added)}")
        else:
            ui.say(ui.dim("  built-ins already present (use --force to reset)"))
        from . import scheduler
        if not scheduler.is_registered():
            ui.say()
            ui.warn("nothing is running these yet")
            ui.say(ui.dim("  wire them to the OS: milo routines install"))
        return 0

    if action == "add":
        if not name:
            return _fail('milo routines add <name> --prompt "..." --every "daily at 7"')
        try:
            r = st.add(
                name,
                prompt=_joined(args.prompt),
                command=args.shell_command or "",
                schedule=args.every or "manual",
                output=args.output or "log",
                harness=args.with_harness or "",
                model=args.model or "",
                tags=args.tags or [],
                skip_missed=args.skip_missed,
                overwrite=args.force,
            )
        except (ScheduleError, ValueError) as exc:
            return _fail(str(exc))
        ui.ok(f"{r.name} — {r.schedule_label}")
        return 0

    if action in ("remove", "delete"):
        if not name:
            return _fail("which routine? milo routines remove <name>")
        return 0 if st.remove(name) else _fail(f"no routine {name!r}")

    if action in ("enable", "disable"):
        if not name:
            return _fail(f"which routine? milo routines {action} <name>")
        r = st.set_enabled(name, action == "enable")
        if not r:
            return _fail(f"no routine {name!r}")
        ui.ok(f"{r.name} {action}d")
        return 0

    if action == "schedule":
        if not name or not args.every:
            return _fail('milo routines schedule <name> --every "daily at 07:30"')
        try:
            r = st.set_schedule(name, args.every)
        except ScheduleError as exc:
            return _fail(str(exc))
        if not r:
            return _fail(f"no routine {name!r}")
        ui.ok(f"{r.name} — {r.schedule_label}")
        return 0

    if action == "run":
        if not name:
            return _fail("which routine? milo routines run <name>")
        res = st.run(name, dry_run=args.dry_run)
        if _emit(res, args.json):
            return 0
        (ui.ok if res["status"] in ("ok", "dry-run") else ui.err)(
            f"{res['routine']}: {res['status']}")
        if res.get("output"):
            ui.say(res["output"][:2000])
        return 0 if res["status"] in ("ok", "dry-run") else 1

    if action == "tick":
        results = st.tick(dry_run=args.dry_run)
        if _emit(results, args.json):
            return 0
        if not results:
            ui.say(ui.dim("  nothing due"))
            return 0
        for res in results:
            (ui.ok if res["status"] in ("ok", "skipped", "dry-run") else ui.err)(
                f"{res['routine']}: {res['status']}")
        return 0

    if action == "watch":
        return _watch(st, args.interval)

    if action == "show":
        if not name:
            return _fail("which routine? milo routines show <name>")
        r = st.get(name)
        if not r:
            return _fail(f"no routine {name!r}")
        if _emit(r.to_dict(), args.json):
            return 0
        for k, v in r.to_dict().items():
            if k in ("prompt", "last_output"):
                continue
            ui.kv(k, describe_schedule(v) if k == "schedule" else v, width=14)
        if r.prompt:
            ui.say()
            ui.say(ui.bold("  prompt"))
            ui.say("  " + r.prompt[:1500].replace("\n", "\n  "))
        return 0

    if action == "logs":
        log = paths.logs_dir() / "routines" / f"{name}.log"
        if not name:
            return _fail("which routine? milo routines logs <name>")
        if not log.is_file():
            ui.warn(f"no log yet for {name}")
            return 0
        text = log.read_text(encoding="utf-8", errors="ignore")
        print("\n".join(text.splitlines()[-args.limit:]))
        return 0

    if action == "install":
        from . import scheduler
        res = scheduler.install(args.backend or "")
        if _emit(res.__dict__, args.json):
            return 0
        if res.ok:
            ui.ok(res.render("installed"))
            ui.say(ui.dim(f"  ticks every {scheduler.TICK_MINUTES} minutes"))
            return 0
        ui.err(res.render("installed"))
        ui.say(ui.dim("  run it yourself any time: milo routines tick"))
        return 1

    if action == "uninstall":
        from . import scheduler
        for res in scheduler.uninstall(args.backend or ""):
            if res.ok and "not " not in res.detail and "nothing" not in res.detail:
                ui.ok(res.render("removed"))
        return 0

    if action == "status":
        from . import scheduler
        rows = [r.__dict__ for r in scheduler.status()]
        if _emit({"scheduler": rows, "routines": st.stats()}, args.json):
            return 0
        ui.banner("routines", st.stats().get("file", ""))
        for k, v in st.stats().items():
            if k != "file":
                ui.kv(k, v, width=12)
        ui.say()
        ui.say("  os scheduler:")
        for r in scheduler.status():
            (ui.ok if r.ok else ui.warn)("  " + r.render("registered"))
        if not scheduler.is_registered():
            ui.say(ui.dim("  nothing registered: milo routines install"))
        return 0

    return _fail(f"unknown action {action!r}")


def _watch(st, interval: int) -> int:
    """Foreground tick loop. The fallback when the OS offers no scheduler."""
    import time as _time

    interval = max(30, int(interval or 300))
    ui.info(f"watching — tick every {interval}s, Ctrl-C to stop")
    try:
        while True:
            try:
                for res in st.tick():
                    ui.say(f"  {res['routine']}: {res['status']}")
            except Exception as exc:
                # One bad routine must never end the loop; that would silently
                # stop every other routine on the machine.
                ui.err(f"tick failed: {type(exc).__name__}: {exc}")
            _time.sleep(interval)
            st.load()   # pick up routines added while we were sleeping
    except KeyboardInterrupt:
        ui.say()
        ui.info("stopped")
        return 0


# ── curated memory (MEMORY.md / USER.md) ──────────────────────────────────────


def cmd_note(args: argparse.Namespace) -> int:
    """The bounded tier: what Milo carries into every session, unprompted."""
    from .curated import CuratedMemory, FILENAMES

    mem = CuratedMemory()
    action = (args.action or "view").lower()
    target = args.target or ("user" if action in ("me",) else "memory")
    text = _joined(getattr(args, "text", ""))

    if action in ("view", "show", "list"):
        if _emit(mem.stats(), args.json):
            return 0
        block = mem.render_block()
        if not block:
            ui.warn("both stores are empty")
            ui.say(ui.dim('  add one: milo note add "..." [--user]'))
            return 0
        ui.say(block)
        ui.say()
        for t in ("memory", "user"):
            s = mem.stats()[t]  # type: ignore[index]
            bar = _bar(s["pct"])                     # type: ignore[index]
            ui.say(f"  {FILENAMES[t]:<10} {bar} "
                   f"{s['used']}/{s['limit']} chars, {s['entries']} entries")  # type: ignore[index]
        return 0

    if action == "add":
        if not text:
            return _fail('what should Milo remember? milo note add "..." [--user]')
        res = mem.add(target, text)
        (ui.ok if res.ok else ui.err)(res.as_text())
        if not res.ok:
            ui.say(ui.dim("  see what's in there: milo note view"))
        return 0 if res.ok else 1

    if action == "replace":
        if not args.match or not text:
            return _fail('milo note replace --match "<substring>" "<new text>"')
        res = mem.replace(target, args.match, text)
        (ui.ok if res.ok else ui.err)(res.as_text())
        return 0 if res.ok else 1

    if action in ("remove", "rm", "forget"):
        needle = args.match or text
        if not needle:
            return _fail('which entry? milo note remove "<substring>"')
        res = mem.remove(target, needle)
        (ui.ok if res.ok else ui.err)(res.as_text())
        return 0 if res.ok else 1

    if action == "edit":
        # Hand-editing is a first-class path, not a fallback: these files exist
        # precisely so a human can correct what the agent believes.
        path = mem.path_for(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("", encoding="utf-8")
        if not _open_in_editor(path):
            ui.say(str(path))
            return 0
        after = CuratedMemory()
        ui.ok(f"{FILENAMES[after._norm(target)]} — {after.summary().splitlines()[0]}")
        return 0

    if action == "clear":
        if not args.yes and not ui.confirm(f"clear {FILENAMES[mem._norm(target)]}?", False):
            return 0
        ui.ok(mem.clear(target).as_text())
        return 0

    if action == "path":
        print(mem.path_for(target))
        return 0

    return _fail(f"unknown action {action!r}")


def _bar(pct: int, width: int = 16) -> str:
    filled = max(0, min(width, round(width * pct / 100)))
    return "[" + "#" * filled + "." * (width - filled) + "]"


# ── packs (third-party skill / agent libraries) ───────────────────────────────


def cmd_packs(args: argparse.Namespace) -> int:
    """Borrow whole libraries of skills and agents from other people."""
    from . import packs

    action = args.action
    name = _joined(getattr(args, "name", "")) or ""

    if action in ("list", "ls"):
        installed = packs.installed()
        if _emit({"installed": installed, "available": packs.KNOWN_PACKS},
                 args.json):
            return 0
        if installed:
            ui.banner("packs", f"{len(installed)} installed")
            enabled = set(packs.enabled_names())
            rows = []
            for pack, entry in sorted(installed.items()):
                items = entry.get("items", {})
                on = sum(1 for n in items if n in enabled)
                bits = ", ".join(f"{v} {k}s" for k, v in
                                 sorted(entry.get("counts", {}).items()) if v)
                rows.append([pack, bits, f"{on}/{len(items)}"])
            ui.table(rows, headers=["pack", "contents", "in prompt"])
            ui.say()
        known = {k: v for k, v in packs.KNOWN_PACKS.items() if k not in installed}
        if known:
            ui.say(ui.bold("  available"))
            for k, v in sorted(known.items()):
                ui.say(f"  {k:<24} {ui.dim(v['summary'])}")
            ui.say()
            ui.say(ui.dim(f"  add one: milo packs add {sorted(known)[0]}"))
        return 0

    if action in ("add", "install"):
        if not name:
            return _fail("which pack? milo packs add superpowers "
                         "| owner/repo | <path>")
        with ui.Spinner(f"fetching {name}"):
            res = packs.install(name, name=args.rename,
                                enable=args.enable or ())
        if _emit(res.__dict__, args.json):
            return 0
        if res.error:
            return _fail(f"{res.pack}: {res.error}")
        ui.ok(res.render())
        if res.renamed and args.verbose:
            for old, new in sorted(res.renamed.items()):
                ui.say(ui.dim(f"    {old} → {new}"))
        ui.say()
        # Nothing is in the prompt yet, and that surprises people unless said
        # plainly. Explaining the why here is cheaper than a support round-trip.
        if res.enabled:
            ui.ok(f"{len(res.enabled)} added to the prompt index")
        else:
            ui.say(ui.dim("  Nothing was added to the system prompt — "
                          f"{res.total} entries would cost real tokens"))
            ui.say(ui.dim("  every turn. They are searchable now:"))
            ui.say(ui.dim('    milo skills search "code review"'))
            ui.say(ui.dim("    milo packs enable <name>"))
        return 0

    if action in ("remove", "rm", "uninstall"):
        if not name:
            return _fail("which pack? milo packs remove <name>")
        if not args.yes and not ui.confirm(f"remove pack {name!r}?", False):
            return 0
        if not packs.remove(name):
            return _fail(f"no pack {name!r} — see: milo packs list")
        ui.ok(f"removed {name}")
        return 0

    if action == "update":
        targets = [name] if name else sorted(packs.installed())
        if not targets:
            return _fail("nothing installed to update")
        for t in targets:
            with ui.Spinner(f"updating {t}"):
                res = packs.update(t)
            (ui.err if res.error else ui.ok)(res.render())
        return 0

    if action == "search":
        q = _joined(args.query) or name
        if not q:
            return _fail('what are you after? milo packs search "database"')
        hits = packs.search(q, limit=args.limit)
        if _emit(hits, args.json):
            return 0
        if not hits:
            ui.warn(f"nothing matches {q!r}")
            return 0
        ui.banner("packs", f"{len(hits)} match{'es' if len(hits) != 1 else ''}")
        ui.table(
            [[h["name"], h.get("description", "")[:46], h["pack"],
              "yes" if h["enabled"] else ""] for h in hits],
            headers=["name", "what it does", "pack", "in prompt"],
        )
        return 0

    if action in ("enable", "disable"):
        wanted = [name] + list(getattr(args, "query", None) or [])
        wanted = [w for w in wanted if w]
        if not wanted:
            return _fail(f"which one? milo packs {action} <name>")
        on = action == "enable"
        # Accept a pack name or a category as shorthand for "all of these" —
        # enabling 270 agents one at a time is not a workflow anyone will use.
        expanded: List[str] = []
        cat = packs.catalogue()
        for w in wanted:
            matches = [c["name"] for c in cat
                       if c["pack"] == w or c.get("category") == w
                       or c.get("kind") == w]
            expanded.extend(matches or [w])
        known = {c["name"] for c in cat}
        unknown = [w for w in expanded if w not in known]
        if unknown:
            return _fail(f"not installed: {', '.join(sorted(set(unknown))[:5])}"
                         f"\n  find it first: milo packs search \"{unknown[0]}\"")
        touched = packs.set_enabled(expanded, on=on)
        if not touched:
            ui.say(ui.dim(f"  already {action}d"))
            return 0
        ui.ok(f"{len(touched)} {action}d")
        if on:
            from .skills import SkillRegistry
            size = len(SkillRegistry().index()) // 4
            ui.say(ui.dim(f"  prompt index is now ~{size} tokens"))
        return 0

    if action == "show":
        if not name:
            return _fail("which pack? milo packs show <name>")
        entry = packs.installed().get(packs.slugify(name))
        if not entry:
            return _fail(f"no pack {name!r}")
        if _emit(entry, args.json):
            return 0
        ui.banner(name, entry.get("source", ""))
        enabled = set(packs.enabled_names())
        by_cat: Dict[str, List[str]] = {}
        for n, meta in sorted(entry.get("items", {}).items()):
            by_cat.setdefault(meta.get("category") or "general", []).append(
                f"{n}{' *' if n in enabled else ''}")
        for cat, names in sorted(by_cat.items()):
            ui.say(ui.bold(f"  {cat}") + ui.dim(f"  ({len(names)})"))
            ui.say("    " + ", ".join(names[:14]) +
                   (f" … +{len(names) - 14}" if len(names) > 14 else ""))
        ui.say()
        ui.say(ui.dim("  * = in the prompt index"))
        return 0

    return _fail(f"unknown action {action!r}")
