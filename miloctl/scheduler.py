"""
scheduler.py — hand the tick to whatever the OS already runs.
=============================================================

Milo does not ship a daemon. Every platform already has something that wakes
processes up on a timer, and using it means there is no Milo process to crash,
no service to babysit, and nothing to reinstall after a reboot.

    Windows   Task Scheduler   (schtasks)
    Linux     systemd --user timer, else crontab
    macOS     launchd plist, else crontab
    Termux    termux-job-scheduler, else crond, else a boot script

All of them run exactly one command::

    milo --quiet routines tick

Everything about *what* runs and *when* lives in ``state/cron.json``, which
travels in the backup. The OS entry is a dumb five-minute heartbeat, so it
never needs changing when routines do — install it once per machine and
forget it exists.

Every installer is paired with an uninstaller and a status check, because a
scheduler you can't remove is worse than one you never installed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from . import paths

#: How often the OS pokes Milo. Routines have their own schedules; this is
#: only the resolution at which they can fire.
TICK_MINUTES = 5

TASK_NAME = "MiloRoutines"

#: schtasks' CLI cannot express battery settings, and its defaults
#: (DisallowStartIfOnBatteries/StopIfGoingOnBatteries = true) silently kill
#: the tick on any laptop running on battery. The XML export of a task is
#: the only reliable way to set them, so install via XML.
_WINDOWS_NEVER_RAN = re.compile(r"Last (Run Time|Result):\s*(11/30/1999|267011)")


@dataclass
class SchedulerResult:
    backend: str
    ok: bool
    detail: str = ""
    command: str = ""

    def render(self, verb: str = "ok") -> str:
        # The verb is passed in because the same dataclass reports installs,
        # removals and status checks; "installed" on an uninstall is a lie.
        return f"{self.backend}: {verb if self.ok else 'failed'}" + (
            f" — {self.detail}" if self.detail else "")


# ── the command every backend runs ────────────────────────────────────────────


def tick_command() -> List[str]:
    """The argv the scheduler should execute.

    Uses this interpreter explicitly. A console script on PATH is not a safe
    assumption inside cron, launchd or Task Scheduler, all of which run with a
    minimal environment — and 'it worked in my shell' is how scheduled jobs
    silently stop running.

    ``--quiet`` must come *before* the subcommand: it is a top-level flag, so
    argparse rejects ``routines tick --quiet`` and the scheduled run would
    exit 2 every time — a registered-but-failing heartbeat.
    """
    return [sys.executable, "-m", "miloctl.cli", "--quiet", "routines", "tick"]


def tick_command_string() -> str:
    argv = tick_command()
    return " ".join(f'"{a}"' if " " in a else a for a in argv)


def _env_prefix() -> str:
    """Re-export MILO_HOME if it isn't the default — cron won't have it."""
    home = str(paths.milo_home())
    default = str(Path.home() / ".milo")
    return "" if home == default else f'MILO_HOME="{home}" '


def _run(argv: List[str], timeout: int = 30) -> Tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, f"{argv[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as exc:
        return 1, str(exc)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


# ── Windows: Task Scheduler ───────────────────────────────────────────────────


def _windows_task_xml() -> str:
    """Task XML with laptop-safe settings.

    The schtasks CLI can't express these, and its defaults block the task on
    battery power — a silent failure on any laptop. ``DisallowStartIfOnBatteries
    false`` + ``StopIfGoingOnBatteries false`` mean the heartbeat survives
    being unplugged; ``StartWhenAvailable`` catches up after sleep/hibernate.
    """
    start = (datetime.now() + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
    argv = tick_command()
    command, *rest = argv
    arguments = " ".join(f'"{a}"' if " " in a else a for a in rest)
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-16"?>
        <Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
          <RegistrationInfo>
            <Date>{start}</Date>
            <Author>{os.environ.get("USERNAME", "milo")}</Author>
            <URI>\\{TASK_NAME}</URI>
          </RegistrationInfo>
          <Triggers>
            <TimeTrigger>
              <StartBoundary>{start}</StartBoundary>
              <Enabled>true</Enabled>
              <Repetition>
                <Interval>PT{TICK_MINUTES}M</Interval>
                <StopAtDurationEnd>false</StopAtDurationEnd>
              </Repetition>
            </TimeTrigger>
          </Triggers>
          <Principals>
            <Principal id="Author">
              <LogonType>InteractiveToken</LogonType>
              <RunLevel>LeastPrivilege</RunLevel>
            </Principal>
          </Principals>
          <Settings>
            <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
            <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
            <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
            <AllowHardTerminate>true</AllowHardTerminate>
            <StartWhenAvailable>true</StartWhenAvailable>
            <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
            <IdleSettings>
              <StopOnIdleEnd>true</StopOnIdleEnd>
              <RestartOnIdle>false</RestartOnIdle>
            </IdleSettings>
            <AllowStartOnDemand>true</AllowStartOnDemand>
            <Enabled>true</Enabled>
            <Hidden>false</Hidden>
            <RunOnlyIfIdle>false</RunOnlyIfIdle>
            <WakeToRun>false</WakeToRun>
            <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
            <Priority>7</Priority>
          </Settings>
          <Actions Context="Author">
            <Exec>
              <Command>{command}</Command>
              <Arguments>{arguments}</Arguments>
            </Exec>
          </Actions>
        </Task>
        """)


def _windows_install() -> SchedulerResult:
    xml = _windows_task_xml()
    tmp = paths.state_dir() / f"{TASK_NAME}.xml"
    try:
        paths.ensure(paths.state_dir())
        tmp.write_text(xml, encoding="utf-16")
        code, out = _run([
            "schtasks", "/Create", "/TN", TASK_NAME,
            "/XML", str(tmp), "/F",
        ])
    except OSError as exc:
        return SchedulerResult("Task Scheduler", False, str(exc))
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return SchedulerResult("Task Scheduler", code == 0, out[:300],
                           tick_command_string())


def _windows_uninstall() -> SchedulerResult:
    code, out = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    return SchedulerResult("Task Scheduler", code == 0, out[:200])


def _windows_status() -> SchedulerResult:
    code, out = _run(["schtasks", "/Query", "/TN", TASK_NAME])
    if code != 0:
        return SchedulerResult("Task Scheduler", False, "not registered")
    return SchedulerResult("Task Scheduler", True, "registered")


def _windows_healthy() -> SchedulerResult:
    """Registered AND actually executing.

    "Last Result: 267011" / "Last Run Time: 11/30/1999" = the task has
    never executed once. A registered-but-dead heartbeat is the silent
    failure that kills backups; this is the check that catches it.
    """
    code, out = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"])
    if code != 0:
        return SchedulerResult("Task Scheduler", False, "not registered")
    if _WINDOWS_NEVER_RAN.search(out):
        return SchedulerResult(
            "Task Scheduler", False,
            "registered but never fired — battery/power settings likely block it; "
            "run: milo routines install")
    m = re.search(r"Last Run Time:\s*(.+)", out)
    return SchedulerResult("Task Scheduler", True,
                           "firing" + (f", last ran {m.group(1).strip()}" if m else ""))


# ── systemd user timer ────────────────────────────────────────────────────────


def _systemd_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user"


def _has_systemd() -> bool:
    """True only if a *user* systemd manager is actually reachable.

    Containers and CI images routinely ship systemctl with no running user
    bus. Trusting `which systemctl` there means installing a timer that can
    never fire — a silent failure, which is the whole category of bug this
    module exists to avoid.
    """
    if not shutil.which("systemctl"):
        return False
    if not os.environ.get("XDG_RUNTIME_DIR") and not os.environ.get(
            "DBUS_SESSION_BUS_ADDRESS"):
        return False
    code, out = _run(["systemctl", "--user", "is-system-running"], timeout=8)
    if "Failed to connect" in out or "not been booted" in out:
        return False
    return code in (0, 1)  # 1 = "degraded", still usable


def _systemd_install() -> SchedulerResult:
    unit_dir = _systemd_dir()
    try:
        unit_dir.mkdir(parents=True, exist_ok=True)
        argv = tick_command()
        exec_start = " ".join(argv)
        (unit_dir / "milo-routines.service").write_text(textwrap.dedent(f"""\
            [Unit]
            Description=Milo routines tick
            After=network-online.target

            [Service]
            Type=oneshot
            Environment=MILO_HOME={paths.milo_home()}
            ExecStart={exec_start}
            """), encoding="utf-8")
        (unit_dir / "milo-routines.timer").write_text(textwrap.dedent(f"""\
            [Unit]
            Description=Run Milo routines every {TICK_MINUTES} minutes

            [Timer]
            OnBootSec=2min
            OnUnitActiveSec={TICK_MINUTES}min
            # Fire immediately if the machine was asleep through a window.
            Persistent=true

            [Install]
            WantedBy=timers.target
            """), encoding="utf-8")
    except OSError as exc:
        return SchedulerResult("systemd", False, str(exc))

    _run(["systemctl", "--user", "daemon-reload"])
    code, out = _run(["systemctl", "--user", "enable", "--now", "milo-routines.timer"])
    return SchedulerResult("systemd", code == 0, out[:300] or str(unit_dir),
                           tick_command_string())


def _systemd_uninstall() -> SchedulerResult:
    _run(["systemctl", "--user", "disable", "--now", "milo-routines.timer"])
    removed = []
    for name in ("milo-routines.timer", "milo-routines.service"):
        p = _systemd_dir() / name
        if p.exists():
            try:
                p.unlink()
                removed.append(name)
            except OSError:
                pass
    _run(["systemctl", "--user", "daemon-reload"])
    return SchedulerResult("systemd", True, ", ".join(removed) or "nothing to remove")


def _systemd_status() -> SchedulerResult:
    code, out = _run(["systemctl", "--user", "is-active", "milo-routines.timer"])
    return SchedulerResult("systemd", code == 0, (out or "inactive").strip())


# ── crontab (Linux/macOS/Termux fallback) ─────────────────────────────────────


_CRON_MARK = "# milo-routines (managed by 'milo routines install')"


def _read_crontab() -> str:
    code, out = _run(["crontab", "-l"])
    return out if code == 0 else ""


def _write_crontab(text: str) -> Tuple[int, str]:
    try:
        p = subprocess.run(["crontab", "-"], input=text, capture_output=True,
                           text=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return 1, str(exc)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def _strip_milo_lines(text: str) -> str:
    keep, skip_next = [], False
    for line in text.splitlines():
        if line.strip() == _CRON_MARK:
            skip_next = True
            continue
        if skip_next:
            skip_next = False
            continue
        keep.append(line)
    return "\n".join(keep).strip()


def _cron_install() -> SchedulerResult:
    if not shutil.which("crontab"):
        return SchedulerResult("crontab", False, "crontab not installed")
    current = _strip_milo_lines(_read_crontab())
    entry = f"*/{TICK_MINUTES} * * * * {_env_prefix()}{tick_command_string()} >/dev/null 2>&1"
    new = (current + "\n" if current else "") + f"{_CRON_MARK}\n{entry}\n"
    code, out = _write_crontab(new)
    return SchedulerResult("crontab", code == 0, out[:200] or entry, entry)


def _cron_uninstall() -> SchedulerResult:
    if not shutil.which("crontab"):
        return SchedulerResult("crontab", True, "crontab not installed")
    stripped = _strip_milo_lines(_read_crontab())
    code, out = _write_crontab(stripped + "\n" if stripped else "\n")
    return SchedulerResult("crontab", code == 0, out[:200] or "removed")


def _cron_status() -> SchedulerResult:
    present = _CRON_MARK in _read_crontab()
    return SchedulerResult("crontab", present,
                           "registered" if present else "not registered")


# ── macOS launchd ─────────────────────────────────────────────────────────────


_LAUNCH_LABEL = "ai.milo.routines"


def _launch_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCH_LABEL}.plist"


def _launchd_install() -> SchedulerResult:
    plist = _launch_plist()
    args = "".join(f"        <string>{a}</string>\n" for a in tick_command())
    body = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key><string>{_LAUNCH_LABEL}</string>
            <key>ProgramArguments</key>
            <array>
        {args}    </array>
            <key>StartInterval</key><integer>{TICK_MINUTES * 60}</integer>
            <key>RunAtLoad</key><true/>
            <key>EnvironmentVariables</key>
            <dict>
                <key>MILO_HOME</key><string>{paths.milo_home()}</string>
            </dict>
            <key>StandardErrorPath</key>
            <string>{paths.logs_dir() / 'routines-launchd.log'}</string>
        </dict>
        </plist>
        """)
    try:
        plist.parent.mkdir(parents=True, exist_ok=True)
        paths.ensure(paths.logs_dir())
        plist.write_text(body, encoding="utf-8")
    except OSError as exc:
        return SchedulerResult("launchd", False, str(exc))
    _run(["launchctl", "unload", str(plist)])
    code, out = _run(["launchctl", "load", str(plist)])
    return SchedulerResult("launchd", code == 0, out[:200] or str(plist),
                           tick_command_string())


def _launchd_uninstall() -> SchedulerResult:
    plist = _launch_plist()
    _run(["launchctl", "unload", str(plist)])
    try:
        if plist.exists():
            plist.unlink()
    except OSError as exc:
        return SchedulerResult("launchd", False, str(exc))
    return SchedulerResult("launchd", True, "removed")


def _launchd_status() -> SchedulerResult:
    ok = _launch_plist().is_file()
    return SchedulerResult("launchd", ok, "registered" if ok else "not registered")


# ── Termux ────────────────────────────────────────────────────────────────────


def _termux_install() -> SchedulerResult:
    """Termux:Boot script + job scheduler.

    Android aggressively kills background work, so we register both: the job
    scheduler for while the phone is awake, and a boot script so a reboot
    doesn't quietly end Milo's scheduling forever.
    """
    notes: List[str] = []

    boot_dir = Path.home() / ".termux" / "boot"
    try:
        boot_dir.mkdir(parents=True, exist_ok=True)
        script = boot_dir / "milo-routines.sh"
        script.write_text(
            "#!/data/data/com.termux/files/usr/bin/sh\n"
            "termux-wake-lock\n"
            f'export MILO_HOME="{paths.milo_home()}"\n'
            f"while true; do {tick_command_string()} >/dev/null 2>&1; "
            f"sleep {TICK_MINUTES * 60}; done\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        notes.append(f"boot script {script}")
    except OSError as exc:
        notes.append(f"boot script failed: {exc}")

    if shutil.which("termux-job-scheduler"):
        code, out = _run([
            "termux-job-scheduler",
            "--script", str(boot_dir / "milo-routines.sh"),
            "--period-ms", str(TICK_MINUTES * 60 * 1000),
            "--persisted", "true",
        ])
        notes.append("job-scheduler ok" if code == 0 else f"job-scheduler: {out[:80]}")
    else:
        notes.append("termux-job-scheduler not installed (pkg install termux-api)")

    return SchedulerResult("termux", True, "; ".join(notes), tick_command_string())


def _termux_uninstall() -> SchedulerResult:
    script = Path.home() / ".termux" / "boot" / "milo-routines.sh"
    try:
        if script.exists():
            script.unlink()
    except OSError:
        pass
    if shutil.which("termux-job-scheduler"):
        _run(["termux-job-scheduler", "--cancel-all"])
    return SchedulerResult("termux", True, "removed")


def _termux_status() -> SchedulerResult:
    ok = (Path.home() / ".termux" / "boot" / "milo-routines.sh").is_file()
    return SchedulerResult("termux", ok, "boot script present" if ok else "not registered")


# ── dispatch ──────────────────────────────────────────────────────────────────


# ── loop fallback ─────────────────────────────────────────────────────────────
#
# Containers, locked-down corporate laptops and minimal Docker images have no
# usable scheduler at all. Rather than report failure and leave routines dead,
# Milo supervises itself: a detached process that ticks and sleeps.


def _pid_file() -> Path:
    return paths.state_dir() / "routines-loop.pid"


def _loop_alive() -> bool:
    pf = _pid_file()
    if not pf.is_file():
        return False
    try:
        pid = int(pf.read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)          # signal 0 = existence check
    except ProcessLookupError:
        return False
    except PermissionError:
        return True              # exists, owned by someone else
    except OSError:
        return False
    return True


def _loop_install() -> SchedulerResult:
    if _loop_alive():
        return SchedulerResult("loop", True, "already running")
    paths.ensure(paths.state_dir())
    paths.ensure(paths.logs_dir())
    log = paths.logs_dir() / "routines-loop.log"
    argv = [sys.executable, "-m", "miloctl.cli", "routines", "watch",
            "--interval", str(TICK_MINUTES * 60)]
    try:
        kwargs = {}
        if os.name == "nt":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            kwargs["creationflags"] = 0x00000008 | 0x00000200
        else:
            kwargs["start_new_session"] = True
        with open(log, "a", encoding="utf-8") as fh:
            proc = subprocess.Popen(argv, stdout=fh, stderr=fh,
                                    stdin=subprocess.DEVNULL, **kwargs)
        _pid_file().write_text(str(proc.pid), encoding="utf-8")
    except (OSError, ValueError) as exc:
        return SchedulerResult("loop", False, str(exc))
    return SchedulerResult("loop", True, f"pid {proc.pid}, log {log}",
                           " ".join(argv))


def _loop_uninstall() -> SchedulerResult:
    pf = _pid_file()
    if not pf.is_file():
        return SchedulerResult("loop", True, "not running")
    try:
        pid = int(pf.read_text().strip())
        os.kill(pid, 15)
    except (OSError, ValueError):
        pass
    try:
        pf.unlink()
    except OSError:
        pass
    return SchedulerResult("loop", True, "stopped")


def _loop_status() -> SchedulerResult:
    alive = _loop_alive()
    return SchedulerResult("loop", alive, "running" if alive else "not running")


def backends() -> List[str]:
    """Backends worth trying on this machine, best first.

    ``loop`` is always last: it works everywhere, so it must never shadow a
    real OS scheduler that survives reboots.
    """
    plat = paths.platform_id()
    if plat == "windows":
        return ["schtasks", "loop"]
    if plat == "termux":
        return ["termux", "crontab", "loop"]
    if plat == "macos":
        return ["launchd", "crontab", "loop"]
    return (["systemd"] if _has_systemd() else []) + ["crontab", "loop"]


_INSTALL = {
    "schtasks": _windows_install, "systemd": _systemd_install,
    "crontab": _cron_install, "launchd": _launchd_install,
    "termux": _termux_install, "loop": _loop_install,
}
_UNINSTALL = {
    "schtasks": _windows_uninstall, "systemd": _systemd_uninstall,
    "crontab": _cron_uninstall, "launchd": _launchd_uninstall,
    "termux": _termux_uninstall, "loop": _loop_uninstall,
}
_STATUS = {
    "schtasks": _windows_status, "systemd": _systemd_status,
    "crontab": _cron_status, "launchd": _launchd_status,
    "termux": _termux_status, "loop": _loop_status,
}

#: Firing checks, one per backend that can tell "registered" from "running".
#: Backends without a real health probe fall back to their status check.
_HEALTH = {
    "schtasks": _windows_healthy, "systemd": _systemd_status,
    "crontab": _cron_status, "launchd": _launchd_status,
    "termux": _termux_status, "loop": _loop_status,
}


def install(backend: str = "") -> SchedulerResult:
    """Register the tick. Falls through the candidate list until one sticks."""
    candidates = [backend] if backend else backends()
    last = SchedulerResult("none", False, "no scheduler backend available")
    for name in candidates:
        fn = _INSTALL.get(name)
        if not fn:
            last = SchedulerResult(name, False, f"unknown backend {name!r}")
            continue
        result = fn()
        if result.ok:
            return result
        last = result
    return last


def uninstall(backend: str = "") -> List[SchedulerResult]:
    """Remove every registration we might have made. Always safe to re-run."""
    names = [backend] if backend else list(_UNINSTALL)
    out = []
    for name in names:
        fn = _UNINSTALL.get(name)
        if fn:
            try:
                out.append(fn())
            except Exception as exc:  # never let cleanup explode
                out.append(SchedulerResult(name, False, str(exc)))
    return out


def status() -> List[SchedulerResult]:
    out = []
    for name in backends():
        fn = _STATUS.get(name)
        if fn:
            try:
                out.append(fn())
            except Exception as exc:
                out.append(SchedulerResult(name, False, str(exc)))
    return out


def healthy() -> List[SchedulerResult]:
    """Per-backend "is it actually firing" checks.

    Registration is not health: a task that is registered but never
    executes (battery settings, wrong logon type, systemd without a user
    bus) silently loses every backup. ``healthy`` exists for doctor-style
    checks; ``status`` keeps answering "is it registered".
    """
    out = []
    for name in backends():
        fn = _HEALTH.get(name)
        if fn:
            try:
                out.append(fn())
            except Exception as exc:
                out.append(SchedulerResult(name, False, str(exc)))
    return out


def is_registered() -> bool:
    return any(r.ok for r in status())
