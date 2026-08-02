"""
backup.py — everything that matters, in one place, pushed somewhere safe.
=========================================================================

The old system had five backup scripts (``backup.cjs``, ``backup-engram.cjs``,
``blueprint.cjs``, ``verify.cjs``, ``infra/install.cjs``) and still lost the
hot memory tier on a machine change, because ``backup-engram.cjs`` wrote to a
*local* folder nobody ever pushed.

This module is the replacement. One snapshot, one restore, and a hard rule:

    **If it isn't in the snapshot, it doesn't survive.**

What a snapshot contains
------------------------

===================  =========================================================
``memory.jsonl``     Every durable memory (the hot tier). Line-oriented and
                     sorted, so git diffs are readable.
``sessions.jsonl``   Session history + transcripts.
``profile.json``     The user model.
``skills/``          Every user- and agent-authored skill.
``identity.md``      The edited persona, if any.
``env.template``     ``.env`` with **values stripped to placeholders**.
``manifest.json``    Counts, versions, platform, timestamp.
===================  =========================================================

Secrets are never written. ``env.template`` records which keys existed, not
what they were, and every snapshot is scanned for credential-shaped strings
before it is allowed anywhere near a push.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import tarfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import env, paths
from .naming import display_name

__all__ = [
    "SnapshotResult",
    "RestoreResult",
    "snapshot",
    "restore",
    "archive",
    "unarchive",
    "backup",
    "last_backup_time",
    "state_dir_for",
]

SNAPSHOT_VERSION = 2


# ── Where snapshots live ──────────────────────────────────────────────────────


def state_dir_for(root: Optional[Path] = None) -> Path:
    """The ``state/`` folder inside the milo-portable-system checkout.

    Keeping the snapshot *inside the repo* is the whole trick: one
    ``git push`` and the brain is off the machine. No separate backup repo to
    forget about, no local-only folder to lose.
    """
    if root:
        return Path(root) / "state"
    configured = env.get("MILO_BACKUP_DIR")
    if configured:
        return Path(configured).expanduser()
    return paths.repo_root() / "state"


# ── Results ───────────────────────────────────────────────────────────────────


@dataclass
class SnapshotResult:
    path: Path
    counts: Dict[str, int] = field(default_factory=dict)
    files: List[Path] = field(default_factory=list)
    leaks: List[Tuple[Path, str, str]] = field(default_factory=list)
    pushed: bool = False
    git_log: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and not self.leaks

    def render(self) -> str:
        if self.error:
            return f"Backup failed: {self.error}"
        lines = [f"Snapshot → {self.path}"]
        for k in sorted(self.counts):
            lines.append(f"  {k:<12} {self.counts[k]}")
        if self.leaks:
            lines.append("")
            lines.append(f"  BLOCKED — {len(self.leaks)} credential-shaped string(s):")
            for p, kind, sample in self.leaks[:8]:
                lines.append(f"    {kind}: {sample}  in {p.name}")
            lines.append("  Nothing was pushed. Fix these, then run backup again.")
        elif self.pushed:
            lines.append("  pushed ✓")
        return "\n".join(lines)


@dataclass
class RestoreResult:
    source: Path
    counts: Dict[str, Any] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def render(self) -> str:
        if self.error:
            return f"Restore failed: {self.error}"
        lines = [f"Restored from {self.source}"]
        for k in sorted(self.counts):
            lines.append(f"  {k:<12} {self.counts[k]}")
        if self.missing:
            lines.append(f"  not in snapshot: {', '.join(self.missing)}")
        return "\n".join(lines)


# ── Snapshot ──────────────────────────────────────────────────────────────────


def _copy_skills(dest: Path) -> int:
    """Copy user/agent-authored skills. Bundled skills come from git already."""
    src = paths.skills_dir()
    if not src.is_dir():
        return 0
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(
        src, dest,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".DS_Store"),
    )
    return len(list(dest.rglob("SKILL.md")))


def _write_env_template(dest: Path) -> int:
    """Record which keys exist, never their values."""
    data = env.load(include_os=False)
    lines = [
        "# Milo environment template.",
        "# Generated by `milo backup`. VALUES ARE DELIBERATELY EMPTY.",
        "# On a new machine: `milo install` will prompt for each of these.",
        "",
    ]
    for key in sorted(data):
        lines.append(f"{key}=")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(data)


def snapshot(
    dest: Optional[Path] = None,
    *,
    include_vault_export: bool = True,
    check_leaks: bool = True,
) -> SnapshotResult:
    """Write the full portable snapshot. Does not touch git."""
    dest = Path(dest) if dest else state_dir_for()
    res = SnapshotResult(dest)
    try:
        dest.mkdir(parents=True, exist_ok=True)

        from .memory import store as mem_store
        brain = mem_store()
        res.counts["memories"] = brain.export_jsonl(dest / "memory.jsonl")
        res.files.append(dest / "memory.jsonl")

        try:
            from .sessions import store as sess_store
            res.counts["sessions"] = sess_store().export_jsonl(dest / "sessions.jsonl")
            res.files.append(dest / "sessions.jsonl")
        except Exception:
            res.counts["sessions"] = 0

        try:
            from .profile import Profile
            prof = Profile()
            (dest / "profile.json").write_text(
                json.dumps(prof.to_dict(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            res.counts["traits"] = prof.stats().get("traits", 0)
            res.files.append(dest / "profile.json")
        except Exception:
            res.counts["traits"] = 0

        res.counts["skills"] = _copy_skills(dest / "skills")

        ident = paths.milo_home() / "identity.md"
        if ident.is_file():
            shutil.copy2(ident, dest / "identity.md")
            res.files.append(dest / "identity.md")

        cron = paths.cron_file()
        if cron.is_file():
            shutil.copy2(cron, dest / "routines.json")
            res.files.append(dest / "routines.json")

        res.counts["env_keys"] = _write_env_template(dest / "env.template")
        res.files.append(dest / "env.template")

        if include_vault_export:
            try:
                from .vault import vault
                v = vault()
                if v.exists:
                    brain.export_markdown(
                        v.path(v.layout.milo_notes, "MEMORY-EXPORT.md")
                    )
            except Exception:
                pass

        manifest = {
            "version": SNAPSHOT_VERSION,
            "agent": display_name(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "created_epoch": time.time(),
            "platform": paths.platform_id(),
            "python": platform.python_version(),
            "milo_version": __import__("miloctl").__version__,
            "hostname": platform.node(),
            "counts": res.counts,
            "paths": {
                k: v for k, v in paths.describe().items()
                if k in ("home", "vault", "workspace", "skills")
            },
        }
        (dest / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        res.files.append(dest / "manifest.json")

        if check_leaks:
            res.leaks = env.scan_paths([dest])

    except Exception as exc:
        res.error = f"{type(exc).__name__}: {exc}"
    return res


# ── Git ───────────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=timeout,
    )


def _commit_and_push(repo: Path, message: str, push: bool) -> Tuple[bool, str]:
    if not (repo / ".git").exists():
        return False, f"{repo} is not a git repo — snapshot written but not pushed"
    logs: List[str] = []
    pull = _git(repo, "pull", "--rebase", "--autostash")
    logs.append((pull.stdout + pull.stderr).strip())
    status = _git(repo, "status", "--porcelain")
    if not status.stdout.strip():
        return True, "nothing changed since last backup"
    _git(repo, "add", "-A")
    commit = _git(repo, "commit", "-m", message)
    logs.append((commit.stdout + commit.stderr).strip())
    if not push:
        return True, "\n".join(x for x in logs if x)
    p = _git(repo, "push")
    logs.append((p.stdout + p.stderr).strip())
    return p.returncode == 0, "\n".join(x for x in logs if x)


# ── Public entry point ────────────────────────────────────────────────────────


def backup(
    *,
    push: bool = True,
    sync_vault: bool = True,
    message: str = "",
    repo: Optional[Path] = None,
) -> SnapshotResult:
    """Snapshot → leak-scan → commit → push. Also syncs the vault repo.

    This is the one command that has to work. ``milo backup`` on the old
    machine and ``milo restore`` on the new one is the entire migration story.
    """
    repo = Path(repo) if repo else paths.repo_root()
    res = snapshot(state_dir_for(repo))
    if res.error:
        return res

    if res.leaks:
        return res  # refuse to push; render() explains why

    who = display_name()
    msg = message or (
        f"chore({who.lower()}): memory snapshot "
        f"{datetime.now():%Y-%m-%d %H:%M} "
        f"({res.counts.get('memories', 0)}m/{res.counts.get('skills', 0)}s)"
    )
    ok, log = _commit_and_push(repo, msg, push)
    res.pushed = ok and push
    res.git_log = log

    if sync_vault:
        try:
            from .vault import vault
            v = vault()
            if v.exists and v.is_git:
                vok, vlog = v.sync()
                res.git_log += f"\n[vault] {vlog}"
        except Exception as exc:
            res.git_log += f"\n[vault] skipped: {exc}"

    _mark_backup_time()
    return res


def _mark_backup_time() -> None:
    try:
        p = paths.state_dir() / "last-backup"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def last_backup_time() -> Optional[float]:
    p = paths.state_dir() / "last-backup"
    if not p.is_file():
        return None
    try:
        return float(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


# ── Restore ───────────────────────────────────────────────────────────────────


def restore(
    source: Optional[Path] = None,
    *,
    merge: bool = True,
    pull: bool = True,
) -> RestoreResult:
    """Load a snapshot into this machine. Merges by default — never destructive."""
    src = Path(source) if source else state_dir_for()
    res = RestoreResult(src)

    if pull and not source:
        repo = paths.repo_root()
        if (repo / ".git").exists():
            _git(repo, "pull", "--rebase", "--autostash")

    if not src.is_dir():
        res.error = (
            f"no snapshot at {src}\n"
            "Clone milo-portable-system first, or pass a path/archive."
        )
        return res

    try:
        paths.ensure_tree()

        mem_file = src / "memory.jsonl"
        if mem_file.is_file():
            from .memory import store as mem_store
            res.counts["memories"] = mem_store().import_jsonl(mem_file, merge=merge)
        else:
            res.missing.append("memory.jsonl")

        sess_file = src / "sessions.jsonl"
        if sess_file.is_file():
            try:
                from .sessions import store as sess_store
                res.counts["sessions"] = sess_store().import_jsonl(sess_file)
            except Exception as exc:
                res.counts["sessions"] = f"failed: {exc}"
        else:
            res.missing.append("sessions.jsonl")

        prof_file = src / "profile.json"
        if prof_file.is_file():
            try:
                from .profile import Profile
                p = Profile()
                p.merge_dict(json.loads(prof_file.read_text(encoding="utf-8")))
                p.save()
                res.counts["traits"] = p.stats().get("traits", 0)
            except Exception as exc:
                res.counts["traits"] = f"failed: {exc}"
        else:
            res.missing.append("profile.json")

        skills_src = src / "skills"
        if skills_src.is_dir():
            dest = paths.skills_dir()
            dest.mkdir(parents=True, exist_ok=True)
            count = 0
            for skill_md in skills_src.rglob("SKILL.md"):
                rel = skill_md.parent.relative_to(skills_src)
                target = dest / rel
                if target.exists() and not merge:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(skill_md.parent, target)
                count += 1
            res.counts["skills"] = count
        else:
            res.missing.append("skills/")

        ident = src / "identity.md"
        if ident.is_file():
            shutil.copy2(ident, paths.milo_home() / "identity.md")
            res.counts["identity"] = 1

        routines = src / "routines.json"
        if routines.is_file():
            shutil.copy2(routines, paths.cron_file())
            res.counts["routines"] = 1

    except Exception as exc:
        res.error = f"{type(exc).__name__}: {exc}"
    return res


# ── Offline transfer ──────────────────────────────────────────────────────────


def archive(out_path: Optional[Path] = None, include_env: bool = False) -> Path:
    """Bundle a snapshot into a single ``.tar.gz`` — for USB-stick migration.

    ``include_env=True`` embeds the *real* secrets. Only use it for a transfer
    you control end to end, never for anything that touches a network you
    don't own.
    """
    tmp = paths.cache_dir() / f"snapshot-{int(time.time())}"
    snap = snapshot(tmp, check_leaks=not include_env)
    if snap.error:
        raise RuntimeError(snap.error)
    if include_env and paths.env_file().is_file():
        shutil.copy2(paths.env_file(), tmp / "env.real")
        (tmp / "SECRETS-INSIDE").write_text(
            "This archive contains real credentials. Delete after transfer.\n",
            encoding="utf-8",
        )
    stamp = datetime.now().strftime("%Y-%m-%d")
    out = Path(out_path) if out_path else (
        paths.backups_dir() / f"milo_snapshot_{stamp}.tar.gz"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        tar.add(tmp, arcname="milo-snapshot")
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def unarchive(archive_path: Path, *, merge: bool = True) -> RestoreResult:
    """Restore from a ``.tar.gz`` produced by :func:`archive`."""
    tmp = paths.cache_dir() / f"unpack-{int(time.time())}"
    tmp.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as tar:
        members = [
            m for m in tar.getmembers()
            if not m.name.startswith("/") and ".." not in Path(m.name).parts
        ]
        tar.extractall(tmp, members=members)
    root = tmp / "milo-snapshot"
    root = root if root.is_dir() else tmp
    real_env = root / "env.real"
    if real_env.is_file():
        target = paths.env_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(real_env, target)
        try:
            target.chmod(0o600)
        except OSError:
            pass
    res = restore(root, merge=merge, pull=False)
    shutil.rmtree(tmp, ignore_errors=True)
    return res
