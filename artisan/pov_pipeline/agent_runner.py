#!/usr/bin/env python3
"""
agent_runner.py - headless execution of the 7 POV agents.
=========================================================

This module replaces the old ``run_agents()`` stub in ``run_pov_pipeline.py``,
which only printed "WAITING for agent run".

What it does
------------
For each agent in the chain it:

1. Refreshes ``<project>/state/manifest.json`` so the headless run can read
   the current pipeline state (project path, source URL, stage, output
   sizes, timestamps).
2. Builds a structured brief containing the agent's ``.md`` prompt, the
   project directory, the exact output file to write, the previous stage's
   output path, and the manifest path.
3. Dispatches the brief to ``opencode run`` (non-interactive).
4. Verifies the expected output file landed and is non-empty before
   advancing.
5. Records the outcome to ``state/pipeline.log`` and to Milo's memory.

Design notes
------------
* **One place knows the opencode syntax.** :func:`build_opencode_command`
  is the single source of truth; everything else calls it. Flag support is
  probed once from ``opencode run --help`` and cached, so the runner adapts
  to the installed version instead of guessing.
* **The gate loop lives here, the gate does not.** ``script_gate`` is
  injected by the caller (``gate_fn``). The runner never touches the gate
  thresholds - it only reads PASS/FAIL and feeds the failure report back
  into the next scriptwriter brief.
* **Notifications are injected too.** ``notify`` defaults to a no-op, so M5
  can wire Telegram in without editing this file.
* **Nothing crashes the batch.** A hard agent failure marks the project
  ``NEEDS_REVIEW``, notifies, and returns a failed ChainResult. The daemon
  keeps going.
* **Resume-safe.** An existing, non-empty output file means the agent is
  skipped - same behaviour the old stub had.

Portability
-----------
No new third-party dependencies. Every external tool is resolved through
``shutil.which`` (so the Windows ``.CMD`` shims for ``opencode`` / ``milo``
work as ``cmd[0]``), and every path is a :class:`~pathlib.Path` derived from
the project directory or an environment variable.

Environment overrides
---------------------
``POV_OPENCODE_BIN``      explicit path to the opencode executable
``POV_OPENCODE_MODEL``    model to pass as ``--model`` (provider/model)
``POV_OPENCODE_AGENT``    force a specific opencode agent for every stage
``POV_AGENT_TIMEOUT``     per-agent timeout override, seconds
``POV_GATE_MAX_RETRIES``  scriptwriter retries after a gate FAIL (default 3)
``POV_AGENT_MAX_RETRIES`` re-dispatches when an agent exits without writing
                          its output file (missing/empty artifact), default 2
``POV_MILO_BIN``          explicit path to the bundled milo CLI
``POV_MEMORY_PROJECT``    Milo memory project key (default ``pov-pipeline``)
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]          # artisan/pov_pipeline -> artisan -> repo root
AGENTS_DIR = HERE / "agents"

# The opencode runs log UTF-8 + ANSI. Printing their tails to a cp1252
# Windows console would crash on any non-ASCII char (→, em-dashes). Force
# UTF-8 with replacement so log-tail echoing never takes the pipeline down.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

MANIFEST_SCHEMA = 1
MEMORY_PROJECT = os.environ.get("POV_MEMORY_PROJECT", "").strip() or "pov-pipeline"

# Per-agent wall-clock budget. The researcher and the scriptwriter do the
# heavy thinking; the rest are formatting passes.
DEFAULT_AGENT_TIMEOUT = 1800  # 30 min
AGENT_TIMEOUTS: dict[str, int] = {
    "POV-researcher": 2400,
    "POV-scriptwriter": 2400,
    "POV-image-director": 1800,
    "POV-thumbnail-artist": 900,
    "POV-voice-engineer": 1200,
    "POV-seo-specialist": 900,
    "POV-archive-manager": 900,
}

DEFAULT_GATE_RETRIES = 3
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_KEEP = 3

Notify = Callable[[str, str], None]
GateFn = Callable[[Path], bool]


def eprint(*a, **kw) -> None:
    print(*a, **kw, file=sys.stderr)


def _stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class AgentResult:
    """Outcome of a single headless agent dispatch."""

    agent: str
    outfile: str
    status: str = "pending"      # done | skipped | failed | timeout | dry-run
    attempts: int = 0
    bytes_written: int = 0
    duration_s: float = 0.0
    error: str = ""
    finished_at: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("done", "skipped", "dry-run")


@dataclass
class ChainResult:
    """Outcome of the whole 7-agent chain."""

    project: str
    ok: bool = False
    needs_review: bool = False
    reason: str = ""
    gate_attempts: int = 0
    gate_passed: bool = False
    agents: list[AgentResult] = field(default_factory=list)

    def summary(self) -> str:
        done = sum(1 for a in self.agents if a.status == "done")
        skipped = sum(1 for a in self.agents if a.status == "skipped")
        failed = [a.agent for a in self.agents if not a.ok]
        bits = [f"{done} run", f"{skipped} skipped"]
        if failed:
            bits.append("failed: " + ", ".join(failed))
        if self.gate_attempts:
            bits.append("gate " + ("PASS" if self.gate_passed else "FAIL")
                        + f" after {self.gate_attempts} attempt(s)")
        return " | ".join(bits)


# ---------------------------------------------------------------------------
# Logging + Milo memory
# ---------------------------------------------------------------------------


def state_dir(project_dir: Path) -> Path:
    d = project_dir / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path(project_dir: Path) -> Path:
    return state_dir(project_dir) / "pipeline.log"


def manifest_path(project_dir: Path) -> Path:
    return state_dir(project_dir) / "manifest.json"


def _rotate_log(path: Path) -> None:
    """Keep pipeline.log bounded. The daemon (M4) runs for weeks."""
    try:
        if not path.exists() or path.stat().st_size < LOG_MAX_BYTES:
            return
        oldest = path.with_name(f"{path.name}.{LOG_KEEP}")
        if oldest.exists():
            oldest.unlink()
        for i in range(LOG_KEEP - 1, 0, -1):
            src = path.with_name(f"{path.name}.{i}")
            if src.exists():
                src.replace(path.with_name(f"{path.name}.{i + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
    except OSError:
        pass  # logging must never take the pipeline down


def log_event(project_dir: Path, event: str, message: str = "", *,
              echo: bool = True, level: str = "info") -> None:
    """Append one lifecycle line to state/pipeline.log (and stdout)."""
    line = f"{_stamp()} [{level}] {event}"
    if message:
        line += f" - {message}"
    path = log_path(project_dir)
    _rotate_log(path)
    try:
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        eprint(f"[log] could not write {path}: {exc}")
    if echo:
        text = f"[{event}] {message}" if message else f"[{event}]"
        if level == "error":
            eprint(text)
        else:
            print(text)


_MILO_BIN: list[str] | None = None


def milo_command() -> list[str]:
    """Resolve the bundled milo CLI. Cached.

    Order: ``POV_MILO_BIN`` -> ``milo`` on PATH -> ``mylo`` on PATH ->
    ``python -m miloctl.cli`` from this repo. The last one always exists,
    because miloctl is vendored here.
    """
    global _MILO_BIN
    if _MILO_BIN is not None:
        return list(_MILO_BIN)
    explicit = os.environ.get("POV_MILO_BIN", "").strip()
    if explicit and Path(explicit).exists():
        _MILO_BIN = [explicit]
    else:
        # shutil.which returns the resolved .CMD shim on Windows, which is
        # the only form subprocess accepts as cmd[0].
        found = shutil.which("milo") or shutil.which("mylo")
        _MILO_BIN = [found] if found else [sys.executable, "-m", "miloctl.cli"]
    return list(_MILO_BIN)


def milo_remember(text: str, *, category: str = "context", importance: int = 2,
                  tags: Sequence[str] = ("pov", "pipeline"),
                  enabled: bool = True) -> bool:
    """Write one memory to Milo's brain so he stays aware of the pipeline.

    Never raises. A missing milo CLI degrades to a warning - the pipeline is
    not allowed to die because the note-taking failed.
    """
    if not enabled or not text.strip():
        return False
    cmd = milo_command() + [
        "remember", text.strip(),
        "--project", MEMORY_PROJECT,
        "-c", category,
        "-i", str(max(1, min(5, importance))),
    ]
    if tags:
        cmd += ["-t", *tags]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60,
                             cwd=str(REPO_ROOT))
    except (OSError, subprocess.SubprocessError) as exc:
        eprint(f"[memory] milo remember unavailable: {exc}")
        return False
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()[:200]
        eprint(f"[memory] milo remember failed ({res.returncode}): {detail}")
        return False
    return True


# ---------------------------------------------------------------------------
# opencode invocation - the ONE place that knows the syntax
# ---------------------------------------------------------------------------


_OPENCODE_BIN: str | None = None
_OPENCODE_FLAGS: set[str] | None = None
_OPENCODE_AGENTS: set[str] | None = None


def resolve_opencode() -> str | None:
    """Absolute path to the opencode executable, or None.

    On Windows ``opencode`` is a ``.CMD`` shim; ``shutil.which`` returns the
    resolved shim path, which is the only form subprocess accepts as cmd[0].
    Same pattern the images stage already uses for ``opencli``.
    """
    global _OPENCODE_BIN
    if _OPENCODE_BIN is not None:
        return _OPENCODE_BIN or None
    explicit = os.environ.get("POV_OPENCODE_BIN", "").strip()
    if explicit and Path(explicit).exists():
        _OPENCODE_BIN = explicit
    else:
        _OPENCODE_BIN = shutil.which("opencode") or ""
    return _OPENCODE_BIN or None


def opencode_flags() -> set[str]:
    """Flags supported by the installed ``opencode run``, probed once.

    Verified on the dev machine with ``opencode run --help``. The documented
    non-interactive surface is::

        opencode run [--agent <name>] [--model <provider/model>] "<prompt>"

    Probing instead of hardcoding means a version that drops or renames a
    flag degrades to a plain ``opencode run "<brief>"`` rather than dying
    with "unknown option".
    """
    global _OPENCODE_FLAGS
    if _OPENCODE_FLAGS is not None:
        return _OPENCODE_FLAGS
    _OPENCODE_FLAGS = set()
    exe = resolve_opencode()
    if not exe:
        return _OPENCODE_FLAGS
    try:
        res = subprocess.run([exe, "run", "--help"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=60)
        help_text = (res.stdout or "") + (res.stderr or "")
        for flag in ("--agent", "--model", "--session", "--continue",
                     "--print-logs", "--log-level", "--format", "--auto",
                     "--file"):
            if flag in help_text:
                _OPENCODE_FLAGS.add(flag)
    except (OSError, subprocess.SubprocessError) as exc:
        eprint(f"[opencode] --help probe failed, using bare syntax: {exc}")
    return _OPENCODE_FLAGS


def opencode_agents() -> set[str]:
    """Agent names registered with opencode, probed once (best effort).

    Used to decide whether ``--agent pov-scriptwriter`` is meaningful. If the
    agent is not registered we still get the right behaviour, because the
    full ``.md`` contract is embedded in the brief either way.
    """
    global _OPENCODE_AGENTS
    if _OPENCODE_AGENTS is not None:
        return _OPENCODE_AGENTS
    _OPENCODE_AGENTS = set()
    exe = resolve_opencode()
    if not exe:
        return _OPENCODE_AGENTS
    for argv in ([exe, "agent", "list"], [exe, "agent", "ls"]):
        try:
            res = subprocess.run(argv, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        if res.returncode != 0:
            continue
        for token in re.findall(r"[A-Za-z0-9_.-]+", res.stdout or ""):
            _OPENCODE_AGENTS.add(token.lower())
        if _OPENCODE_AGENTS:
            break
    return _OPENCODE_AGENTS


def agent_slug(prompt_path: Path, fallback: str) -> str:
    """The ``name:`` from the agent .md frontmatter (e.g. ``pov-scriptwriter``)."""
    try:
        head = prompt_path.read_text(encoding="utf-8", errors="replace")[:800]
    except OSError:
        return fallback.lower()
    m = re.search(r"^name:\s*(\S+)\s*$", head, re.MULTILINE)
    return (m.group(1) if m else fallback).strip().lower()


def build_opencode_command(prompt: str, *, agent: str | None = None,
                           model: str | None = None,
                           brief_file: str | None = None) -> list[str]:
    """Build the exact non-interactive opencode invocation.

    THIS IS THE ONLY FUNCTION THAT KNOWS THE OPENCODE CLI SYNTAX.

    The prompt is written to a file and passed with ``--file`` when the
    installed opencode supports it. That keeps the command line short (briefs
    are 12-20 KB, which overflows the 8191-char Windows ``CreateProcess``
    limit when passed inline) and leaves an auditable copy of every brief on
    disk. The inline message is then a short pointer to the file. Falls back
    to passing the full prompt as a single argv element when ``--file`` is
    unavailable (older versions / some Linux builds), in which case quotes,
    newlines and Windows backslashes in project paths survive untouched.

    Produces::

        <opencode> run [--agent <slug>] [--model <provider/model>]
                       [--file <brief-file>] "<short message>"

    Raises:
        FileNotFoundError: opencode is not installed / not on PATH.
    """
    exe = resolve_opencode()
    if not exe:
        raise FileNotFoundError(
            "'opencode' not on PATH - the headless agent chain needs it. "
            "Install it, or set POV_OPENCODE_BIN to the executable."
        )
    flags = opencode_flags()
    cmd: list[str] = [exe, "run"]
    if brief_file and "--file" in flags:
        # Positional message MUST come before --file (opencode treats a bare
        # trailing positional as a file path when --file is present).
        cmd.append(
            "Follow the brief in the attached file exactly: do the work, "
            "write the output file it specifies, and do not ask questions."
        )
        cmd += ["--file", brief_file]
    else:
        cmd.append(prompt)
    if agent and "--agent" in flags:
        cmd += ["--agent", agent]
    if model and "--model" in flags:
        cmd += ["--model", model]
    return cmd


# ---------------------------------------------------------------------------
# Manifest - what makes the headless run Milo-aware
# ---------------------------------------------------------------------------


def read_manifest(project_dir: Path) -> dict:
    path = manifest_path(project_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _file_stat(path: Path) -> dict | None:
    if not path.exists():
        return None
    st = path.stat()
    modified = datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds")
    return {"bytes": st.st_size, "modified": modified}


def write_manifest(project_dir: Path, *, agents: Sequence[tuple[str, str]],
                   stage: str = "agents", status: str = "RUNNING",
                   source_url: str = "", results: Sequence[AgentResult] = (),
                   gate: dict | None = None, extra: dict | None = None) -> Path:
    """Write/refresh ``state/manifest.json``.

    Called before EVERY agent dispatch so a headless run always reads the
    current truth: where the project is, what produced what, and how big
    each output was when it landed.
    """
    prev = read_manifest(project_dir)
    outputs: dict[str, dict | None] = {}
    for _agent, outfile in agents:
        outputs[outfile] = _file_stat(project_dir / outfile)
    for name in ("00_SOURCE_SCRIPT.txt", "00_SOURCE_URL.txt"):
        stat = _file_stat(project_dir / name)
        if stat:
            outputs[name] = stat

    doc = {
        "schema": MANIFEST_SCHEMA,
        "project": project_dir.name,
        "project_dir": str(project_dir),
        "source_url": source_url or prev.get("source_url", ""),
        "stage": stage,
        "status": status,
        "memory_project": MEMORY_PROJECT,
        "created_at": prev.get("created_at") or _stamp(),
        "updated_at": _stamp(),
        "agent_order": [a for a, _ in agents],
        "outputs": outputs,
        "agents": {**(prev.get("agents") or {}),
                   **{r.agent: asdict(r) for r in results}},
        "gate": gate if gate is not None else prev.get("gate", {}),
        "log": str(log_path(project_dir)),
    }
    if extra:
        doc.update(extra)
    path = manifest_path(project_dir)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def mark_needs_review(project_dir: Path, reason: str) -> None:
    """Flag the project for a human and stop advancing it."""
    marker = project_dir / "NEEDS_REVIEW.txt"
    try:
        marker.write_text(f"{_stamp()}\n{reason}\n", encoding="utf-8")
    except OSError as exc:
        eprint(f"[review] could not write {marker}: {exc}")
    doc = read_manifest(project_dir)
    doc["status"] = "NEEDS_REVIEW"
    doc["needs_review_reason"] = reason
    doc["updated_at"] = _stamp()
    try:
        manifest_path(project_dir).write_text(
            json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def read_source_url(project_dir: Path) -> str:
    path = project_dir / "00_SOURCE_URL.txt"
    if path.exists():
        # utf-8-sig: LLM agents and editors can leave a BOM that would
        # otherwise be printed verbatim (and crash cp1252 consoles).
        return path.read_text(encoding="utf-8-sig", errors="replace").strip()
    return str(read_manifest(project_dir).get("source_url", "") or "")


# ---------------------------------------------------------------------------
# Brief construction
# ---------------------------------------------------------------------------


def build_brief(*, agent: str, prompt_text: str, project_dir: Path, outfile: str,
                previous_output: str | None, attempt: int = 1,
                gate_report: str = "", use_memory: bool = True) -> str:
    """The structured brief handed to one headless run.

    opencode already loads the Milo persona from AGENTS.md, but a headless
    run has no conversation history, so everything it needs is stated here:
    the agent contract, where the project lives, the exact file to write,
    what the previous stage produced, and the manifest to read for state.
    """
    target = project_dir / outfile
    prev_line = str(project_dir / previous_output) if previous_output else \
        "(none - you are the first stage)"

    memory_block = ""
    if use_memory:
        memory_block = (
            "5. WHEN YOU ARE DONE, record the outcome to Milo's memory so Milo\n"
            "   stays aware of this session and can answer \"what is the pipeline\n"
            "   doing\". Run this exactly once, after the file is written:\n\n"
            f"     milo remember \"POV {project_dir.name}: {agent} wrote {outfile} "
            f"(<N> bytes, <one-line note>)\" --project {MEMORY_PROJECT} "
            "-c context -t pov agent\n"
        )

    retry_block = ""
    if attempt > 1:
        report = gate_report.strip() or "(no report captured)"
        retry_block = (
            f"\nRETRY {attempt}. YOUR PREVIOUS ATTEMPT WAS REJECTED.\n"
            f"Produce {outfile} from scratch and fix every point below. Do not\n"
            "submit a light edit of the rejected draft.\n\n"
            "--- FAILURE REPORT ---\n"
            f"{report}\n"
            "--- END FAILURE REPORT ---\n"
        )

    return (
        f"You are running headless as the POV pipeline agent `{agent}`.\n"
        "There is no human watching. Do the work and write the file. Do not ask\n"
        "questions, do not summarise instead of writing, do not stop early.\n"
        f"{retry_block}\n"
        "PROJECT CONTEXT\n"
        f"  project name    : {project_dir.name}\n"
        f"  project dir     : {project_dir}\n"
        f"  manifest        : {manifest_path(project_dir)}  (read this for state)\n"
        f"  pipeline log    : {log_path(project_dir)}\n"
        f"  previous output : {prev_line}\n"
        f"  YOUR OUTPUT FILE: {target}\n\n"
        "RULES\n"
        "1. Read the manifest and the previous stage's output before you start.\n"
        f"2. `%PROJECT_DIR%` in the agent contract below means: {project_dir}\n"
        f"3. Write your output to EXACTLY this path, nothing else: {target}\n"
        "4. Plain text. No JSON, no markdown fences around the file contents.\n"
        f"{memory_block}\n"
        f"=== AGENT CONTRACT ({agent}.md) ===\n"
        f"{prompt_text}\n"
        "=== END AGENT CONTRACT ===\n\n"
        f"Write {outfile} now.\n"
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill the whole tree under ``proc``.

    On Windows the opencode .CMD shim spawns node/git children; killing just
    the shim orphans them (and, worse, leaves the stdout pipe open so a
    timed-out ``communicate()`` hangs forever). ``taskkill /T`` walks the
    tree. POSIX uses the process group.
    """
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=20)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass


def run_cmd_timed(cmd: list[str], *, cwd: str, timeout: int,
                  logfile: Path) -> tuple[int | None, str | None]:
    """Run ``cmd`` with a hard wall-clock timeout, logging output to a file.

    Output goes to ``logfile`` (never a pipe) so nothing can block waiting on
    a reader thread - the opencode tree writes to the file and we poll the
    process instead. On timeout the entire process tree is killed.

    Returns:
        (returncode, None) on completion, or (None, "timeout") if the budget
        expired. The caller reads ``logfile`` for the tail of the output.
    """
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    start_new_session = os.name != "nt"
    with logfile.open("ab") as out:
        proc = subprocess.Popen(
            cmd, cwd=cwd,
            stdout=out, stderr=subprocess.STDOUT,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )
        deadline = time.time() + timeout
        try:
            while proc.poll() is None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    _kill_process_tree(proc)
                    proc.wait()
                    return None, "timeout"
                time.sleep(min(2.0, max(0.1, remaining)))
        except (OSError, subprocess.SubprocessError) as exc:
            _kill_process_tree(proc)
            return None, f"{type(exc).__name__}: {exc}"
    return proc.returncode, None


def dispatch_agent(project_dir: Path, agent: str, outfile: str, *,
                   agents_dir: Path = AGENTS_DIR,
                   previous_output: str | None = None,
                   model: str | None = None,
                   agent_override: str | None = None,
                   timeout: int | None = None,
                   attempt: int = 1,
                   gate_report: str = "",
                   use_memory: bool = True,
                   dry_run: bool = False) -> AgentResult:
    """Run ONE agent headless and verify its output file landed."""
    result = AgentResult(agent=agent, outfile=outfile, attempts=attempt)
    target = project_dir / outfile
    prompt_path = agents_dir / f"{agent}.md"

    if not prompt_path.exists():
        result.status = "failed"
        result.error = f"agent prompt missing: {prompt_path}"
        return result

    # Agents write into subdirectories (05_IMAGES/, 04_THUMBNAIL/). Create
    # them up front so a headless run never fails on a missing parent.
    target.parent.mkdir(parents=True, exist_ok=True)

    prompt_text = prompt_path.read_text(encoding="utf-8", errors="replace")
    brief = build_brief(agent=agent, prompt_text=prompt_text,
                        project_dir=project_dir, outfile=outfile,
                        previous_output=previous_output, attempt=attempt,
                        gate_report=gate_report, use_memory=use_memory)

    # Persist the brief so the command line stays short and every dispatch is
    # auditable. opencode's --file then feeds it back in via the message.
    brief_dir = state_dir(project_dir) / "briefs"
    brief_dir.mkdir(parents=True, exist_ok=True)
    brief_file = brief_dir / f"{agent}.attempt{attempt}.brief.md"
    try:
        brief_file.write_text(brief, encoding="utf-8")
    except OSError as exc:
        result.status = "failed"
        result.error = f"could not write brief: {brief_file} ({exc})"
        return result

    slug: str | None = agent_override or agent_slug(prompt_path, agent)
    if not agent_override and slug not in opencode_agents():
        slug = None  # not registered - the .md in the brief carries the contract

    try:
        cmd = build_opencode_command(brief, agent=slug, model=model,
                                     brief_file=str(brief_file))
    except FileNotFoundError as exc:
        result.status = "failed"
        result.error = str(exc)
        return result

    budget = timeout or AGENT_TIMEOUTS.get(agent, DEFAULT_AGENT_TIMEOUT)
    print("      cmd: " + " ".join(cmd[:-1]) + ' "<brief>"')
    print(f"      timeout: {budget}s")

    if dry_run:
        result.status = "dry-run"
        result.finished_at = _stamp()
        return result

    # Output goes to a file, not a pipe: the opencode tree is killed by PID
    # tree on timeout, and a file handle cannot block a reader thread.
    run_dir = state_dir(project_dir) / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    logfile = run_dir / f"{agent}.attempt{attempt}.log"

    started = time.time()
    retcode, err = run_cmd_timed(cmd, cwd=str(project_dir), timeout=budget,
                                 logfile=logfile)
    result.duration_s = round(time.time() - started, 1)
    result.finished_at = _stamp()

    # Surface the tail of the run log (UTF-8; opencode emits ANSI + non-ASCII).
    if logfile.exists() and logfile.stat().st_size:
        try:
            tail = logfile.read_text(encoding="utf-8", errors="replace")[-2000:]
            if tail.strip():
                print(tail)
        except OSError:
            pass

    if err:
        result.status = "timeout" if err == "timeout" else "failed"
        result.error = err
        return result

    if not target.exists():
        result.status = "failed"
        result.error = (f"agent exited {retcode} but {outfile} was "
                        "never written")
        return result
    size = target.stat().st_size
    if size == 0:
        result.status = "failed"
        result.error = f"{outfile} was written but is empty"
        return result

    result.bytes_written = size
    result.status = "done"
    if retcode != 0:
        # File landed, exit code grumbled. Trust the artifact, note the noise.
        result.error = f"exit {retcode} (output present, continuing)"
    return result


# ---------------------------------------------------------------------------
# Gate loop
# ---------------------------------------------------------------------------


def run_gate_capture(gate_fn: GateFn, project_dir: Path) -> tuple[bool, str]:
    """Run the injected gate and capture its report for the retry brief.

    The gate prints PASS/WARN to stdout and FAIL detail to stderr. Both are
    teed into a string so the scriptwriter can be told exactly what broke,
    then echoed so a human watching still sees it.
    """
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            passed = bool(gate_fn(project_dir))
    except Exception as exc:  # a broken gate must not kill the batch
        passed = False
        err.write(f"[gate] EXCEPTION: {type(exc).__name__}: {exc}\n")
    report = (out.getvalue() + err.getvalue()).strip()
    if report:
        print(report)
    return passed, report


def _previous_of(agents: Sequence[tuple[str, str]], agent: str) -> str | None:
    """The output file of the stage before ``agent`` (for retry briefs)."""
    prev: str | None = None
    for name, outfile in agents:
        if name == agent:
            return prev
        prev = outfile
    return prev


def _missing_output_error(res: AgentResult) -> bool:
    """True when an agent exited but left no usable artifact.

    These are the flaky-model failures: opencode exits 0 (or 1) having only
    read files and walked away, so the expected output file never landed.
    Retrying helps because the model gets a fresh context with a retry note.
    Timeouts, missing prompts or brief-write errors will not fix themselves.
    """
    return (res.status == "failed" and res.error
            and ("never written" in res.error or "is empty" in res.error))


def _dispatch_with_retry(project_dir: Path, agent: str, outfile: str, *,
                         agents_dir: Path, previous_output: str | None,
                         model: str | None, agent_override: str | None,
                         timeout: int | None, use_memory: bool, dry_run: bool,
                         notify: Notify, results: list[AgentResult],
                         max_retries: int) -> AgentResult:
    """Dispatch an agent, re-running it when it exits without its output file.

    Mirrors the gate loop's retry behaviour but for missing output: an agent
    that walks away after reading its brief gets up to ``max_retries`` extra
    fresh-context attempts before the project is parked. Every attempt is
    appended to ``results`` so the run history stays complete.
    """
    attempt = 1
    res = dispatch_agent(project_dir, agent, outfile, agents_dir=agents_dir,
                         previous_output=previous_output, model=model,
                         agent_override=agent_override, timeout=timeout,
                         attempt=attempt, use_memory=use_memory,
                         dry_run=dry_run)
    results.append(res)
    while not res.ok and _missing_output_error(res) and attempt <= max_retries:
        attempt += 1
        reason = f"{agent} produced no {outfile} (attempt {attempt - 1})"
        log_event(project_dir, "agent.retry", reason, level="error")
        milo_remember(
            f"POV pipeline: {project_dir.name} - {reason}. Re-dispatching "
            f"{agent} with a retry brief.", importance=3, enabled=use_memory)
        notify("agent.retry", f"POV {project_dir.name}: {reason}, re-dispatching")
        res = dispatch_agent(project_dir, agent, outfile, agents_dir=agents_dir,
                             previous_output=previous_output, model=model,
                             agent_override=agent_override, timeout=timeout,
                             attempt=attempt,
                             gate_report=f"Previous run: {res.error}",
                             use_memory=use_memory, dry_run=dry_run)
        results.append(res)
    return res


def _gate_loop(project_dir: Path, agents: Sequence[tuple[str, str]], agent: str,
               outfile: str, chain: ChainResult, *, gate_fn: GateFn, retries: int,
               agents_dir: Path, model: str | None, agent_override: str | None,
               timeout: int | None, use_memory: bool, notify: Notify,
               source_url: str, dry_run: bool) -> bool:
    """Gate the scriptwriter output, re-dispatching on FAIL up to ``retries``.

    Returns True to continue the chain, False if the project was parked as
    NEEDS_REVIEW (the caller returns immediately in that case).
    """
    target = project_dir / outfile
    previous_output = _previous_of(agents, agent)
    attempt = 1
    passed, report = run_gate_capture(gate_fn, project_dir)
    chain.gate_attempts = attempt

    while not passed and attempt <= retries:
        attempt += 1
        log_event(project_dir, "gate.fail",
                  f"attempt {attempt - 1}/{retries + 1} - re-dispatching {agent}",
                  level="error")
        notify("gate.fail",
               f"POV {project_dir.name}: script gate FAIL "
               f"(attempt {attempt - 1}/{retries + 1}), rewriting")
        milo_remember(
            f"POV pipeline: {project_dir.name} script gate FAILED "
            f"(attempt {attempt - 1}). Re-dispatching {agent} with the report.",
            importance=3, enabled=use_memory,
        )

        # The rejected draft is archived, not deleted - it is evidence, and
        # moving it aside is what lets the retry brief honestly say
        # "rewrite from scratch" (and keeps the resume-skip correct).
        if target.exists() and not dry_run:
            reject_dir = state_dir(project_dir) / "rejected"
            reject_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            try:
                target.replace(reject_dir / f"{Path(outfile).name}.{stamp}.rejected")
            except OSError as exc:
                eprint(f"[gate] could not archive the rejected draft: {exc}")

        write_manifest(project_dir, agents=agents, stage=f"agents:{agent}",
                       status="RUNNING", source_url=source_url,
                       results=chain.agents,
                       gate={"attempts": attempt - 1, "passed": False,
                             "last_report": report[-4000:]})

        res = dispatch_agent(project_dir, agent, outfile, agents_dir=agents_dir,
                             previous_output=previous_output, model=model,
                             agent_override=agent_override, timeout=timeout,
                             attempt=attempt, gate_report=report,
                             use_memory=use_memory, dry_run=dry_run)
        chain.agents.append(res)
        if not res.ok:
            reason = f"{agent} retry {attempt} failed ({res.status}): {res.error}"
            log_event(project_dir, "agent.fail", reason, level="error")
            mark_needs_review(project_dir, reason)
            notify("agent.failed", f"POV {project_dir.name}: {reason}")
            chain.reason = reason
            chain.needs_review = True
            return False

        passed, report = run_gate_capture(gate_fn, project_dir)
        chain.gate_attempts = attempt

    chain.gate_passed = passed
    write_manifest(project_dir, agents=agents, stage=f"agents:{agent}",
                   status="RUNNING" if passed else "NEEDS_REVIEW",
                   source_url=source_url, results=chain.agents,
                   gate={"attempts": chain.gate_attempts, "passed": passed,
                         "last_report": report[-4000:]})

    if not passed:
        reason = (f"script gate still failing after {chain.gate_attempts} "
                  "attempt(s) - parked for review")
        log_event(project_dir, "gate.exhausted", reason, level="error")
        mark_needs_review(project_dir, f"{reason}\n\n{report}")
        milo_remember(f"POV pipeline: {project_dir.name} NEEDS_REVIEW - {reason}.",
                      category="project", importance=4, enabled=use_memory)
        notify("gate.needs_review", f"POV {project_dir.name}: {reason}")
        chain.reason = reason
        chain.needs_review = True
        return False

    log_event(project_dir, "gate.pass", f"passed on attempt {chain.gate_attempts}")
    milo_remember(
        f"POV pipeline: {project_dir.name} script gate PASSED on attempt "
        f"{chain.gate_attempts}.", enabled=use_memory)
    return True


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------


def run_agent_chain(project_dir: Path, agents: Sequence[tuple[str, str]], *,
                    agents_dir: Path = AGENTS_DIR,
                    gate_fn: GateFn | None = None,
                    gate_after: str = "POV-scriptwriter",
                    gate_retries: int | None = None,
                    model: str | None = None,
                    agent_override: str | None = None,
                    timeout: int | None = None,
                    use_memory: bool = True,
                    notify: Notify | None = None,
                    source_url: str = "",
                    dry_run: bool = False) -> ChainResult:
    """Run the agents headless, in order, with the gate loop wired in.

    Args:
        project_dir: the project folder (contains 00_SOURCE_SCRIPT.txt).
        agents: ordered ``(agent_name, output_file)`` pairs.
        gate_fn: the existing ``script_gate``. Injected, never re-implemented.
        gate_after: agent whose output the gate judges.
        gate_retries: how many times to re-dispatch on FAIL (default 3).
        notify: ``fn(event, message)``. Defaults to log-only (M5 wires this).

    Returns:
        ChainResult. ``ok`` is False on any hard failure; the caller decides
        whether to stop. This function never raises for agent problems.
    """
    if gate_retries is None:
        try:
            gate_retries = int(os.environ.get("POV_GATE_MAX_RETRIES",
                                              DEFAULT_GATE_RETRIES))
        except ValueError:
            gate_retries = DEFAULT_GATE_RETRIES
    if timeout is None:
        try:
            env_timeout = int(os.environ.get("POV_AGENT_TIMEOUT", "0"))
        except ValueError:
            env_timeout = 0
        timeout = env_timeout or None
    model = model or os.environ.get("POV_OPENCODE_MODEL", "").strip() or None
    agent_override = (agent_override
                      or os.environ.get("POV_OPENCODE_AGENT", "").strip()
                      or None)
    source_url = source_url or read_source_url(project_dir)

    def _notify(event: str, message: str) -> None:
        if notify is None:
            return
        try:
            notify(event, message)
        except Exception as exc:  # notifications are never load-bearing
            eprint(f"[notify] {type(exc).__name__}: {exc}")

    chain = ChainResult(project=project_dir.name)
    state_dir(project_dir)

    print("\n" + "=" * 60)
    print(f"  POV AGENT CHAIN (headless, {len(agents)} stages)")
    print("=" * 60)

    if not resolve_opencode() and not dry_run:
        reason = ("'opencode' not on PATH - the headless agent chain cannot "
                  "run. Install it or set POV_OPENCODE_BIN.")
        log_event(project_dir, "chain.abort", reason, level="error")
        mark_needs_review(project_dir, reason)
        _notify("chain.abort", f"POV {project_dir.name}: {reason}")
        chain.reason = reason
        chain.needs_review = True
        return chain

    log_event(project_dir, "chain.start",
              f"{project_dir.name} ({len(agents)} agents, "
              f"source={source_url or 'n/a'})")
    milo_remember(
        f"POV pipeline: started headless agent chain for project "
        f"{project_dir.name} ({len(agents)} agents). "
        f"Source: {source_url or 'n/a'}.",
        enabled=use_memory,
    )
    _notify("project.started", f"POV {project_dir.name}: agent chain started")

    previous_output: str | None = None
    # The gate judges output the scriptwriter PRODUCED this run. A skipped
    # (pre-existing) script was already accepted in an earlier session - and
    # the project may be fully assembled. Re-judging it could archive a
    # completed project's script and regenerate it. Resume-safe = gated once.
    gate_agent_ran = False

    for index, (agent, outfile) in enumerate(agents, 1):
        target = project_dir / outfile
        print(f"\n[{index}/{len(agents)}] {agent} -> {outfile}")

        # Manifest is refreshed BEFORE every dispatch: the headless run reads it.
        write_manifest(project_dir, agents=agents, stage=f"agents:{agent}",
                       status="RUNNING", source_url=source_url,
                       results=chain.agents)

        # Resume-safe: finished work is never regenerated.
        if target.exists() and target.stat().st_size > 0:
            size = target.stat().st_size
            print(f"      already present ({size} bytes), skipping")
            chain.agents.append(AgentResult(agent=agent, outfile=outfile,
                                            status="skipped", bytes_written=size,
                                            finished_at=_stamp()))
            log_event(project_dir, "agent.skip", f"{agent} ({size} bytes)")
            previous_output = outfile
        else:
            log_event(project_dir, "agent.start", f"{agent} -> {outfile}")
            try:
                max_retries = int(os.environ.get("POV_AGENT_MAX_RETRIES", "2"))
            except ValueError:
                max_retries = 2
            res = _dispatch_with_retry(
                project_dir, agent, outfile, agents_dir=agents_dir,
                previous_output=previous_output, model=model,
                agent_override=agent_override, timeout=timeout,
                use_memory=use_memory, dry_run=dry_run, notify=_notify,
                results=chain.agents, max_retries=max_retries)

            if not res.ok:
                reason = f"{agent} failed ({res.status}): {res.error}"
                log_event(project_dir, "agent.fail", reason, level="error")
                write_manifest(project_dir, agents=agents,
                               stage=f"agents:{agent}", status="NEEDS_REVIEW",
                               source_url=source_url, results=chain.agents)
                mark_needs_review(project_dir, reason)
                milo_remember(
                    f"POV pipeline: {project_dir.name} NEEDS_REVIEW - {reason}",
                    category="project", importance=4, enabled=use_memory)
                _notify("agent.failed", f"POV {project_dir.name}: {reason}")
                chain.reason = reason
                chain.needs_review = True
                return chain

            print(f"      OK - {res.bytes_written} bytes in {res.duration_s}s")
            log_event(project_dir, "agent.done",
                      f"{agent} wrote {outfile} ({res.bytes_written} bytes, "
                      f"{res.duration_s}s)")
            milo_remember(
                f"POV pipeline: {project_dir.name} - {agent} wrote {outfile} "
                f"({res.bytes_written} bytes in {res.duration_s}s).",
                enabled=use_memory)
            if agent == gate_after:
                gate_agent_ran = True
            previous_output = outfile

        if gate_fn and agent == gate_after and not dry_run and gate_agent_ran:
            if not _gate_loop(project_dir, agents, agent, outfile, chain,
                              gate_fn=gate_fn, retries=gate_retries,
                              agents_dir=agents_dir, model=model,
                              agent_override=agent_override, timeout=timeout,
                              use_memory=use_memory, notify=_notify,
                              source_url=source_url, dry_run=dry_run):
                return chain

    chain.ok = True
    write_manifest(project_dir, agents=agents, stage="agents", status="OK",
                   source_url=source_url, results=chain.agents)
    log_event(project_dir, "chain.done", chain.summary())
    milo_remember(
        f"POV pipeline: agent chain COMPLETE for {project_dir.name}. "
        f"{chain.summary()}. All outputs present; ready for TTS.",
        category="project", importance=3, enabled=use_memory)
    _notify("agents.done",
            f"POV {project_dir.name}: agent chain done ({chain.summary()})")
    return chain
