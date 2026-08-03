#!/usr/bin/env python3
"""
milo.py — Universal Milo System Launcher
=========================================
Works on Windows, Linux, and Android (Termux).
Requires: python3 + git (nothing else to bootstrap).

Commands:
  python milo.py install    → Full setup on a fresh machine
  python milo.py start      → Start all Milo services
  python milo.py stop       → Stop all Milo services
  python milo.py status     → Health check
  python milo.py backup     → Push all repos to GitHub
  python milo.py restore    → Pull all repos from GitHub
"""

import sys, os, platform, subprocess, shutil, json, textwrap
from pathlib import Path

# ── Platform detection ────────────────────────────────────────────────────────

IS_WINDOWS = platform.system() == "Windows"
IS_TERMUX  = "com.termux" in os.environ.get("PREFIX", "")
IS_LINUX   = platform.system() == "Linux" and not IS_TERMUX

HOME       = Path.home()
MILO_HOME  = HOME / ".milo"
LOGS_DIR   = MILO_HOME / "logs"
ENV_FILE   = MILO_HOME / ".env"

# Repo destinations
REPOS = {
    "milo":            {"url": "https://github.com/dra-allan/milo.git",             "dest": MILO_HOME / "milo"},
    "agentmemory":     {"url": "https://github.com/dra-allan/agentmemory-milo.git", "dest": MILO_HOME / "agentmemory-milo"},
    "dra-brains":      {"url": "https://github.com/dra-allan/dra-brains.git",       "dest": MILO_HOME / "dra-brains"},
    "milo-portable":   {"url": "https://github.com/dra-allan/milo-portable-system.git", "dest": MILO_HOME / "milo-portable-system"},
}

# Services definition
SERVICES = {
    "MiloServe": {
        "cmd_win":    ["opencode", "serve", "--port", "4096"],
        "cmd_unix":   ["opencode", "serve", "--port", "4096"],
        "workdir":    MILO_HOME,
        "log":        LOGS_DIR / "opencode-serve.log",
    },
    "MiloBot": {
        "cmd_win":    [sys.executable, str(MILO_HOME / "agentmemory-milo" / "milo-bot" / "src" / "bot.py")],
        "cmd_unix":   [sys.executable, str(MILO_HOME / "agentmemory-milo" / "milo-bot" / "src" / "bot.py")],
        "workdir":    MILO_HOME / "agentmemory-milo" / "milo-bot",
        "log":        LOGS_DIR / "milo-bot.log",
    },
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def banner(msg):
    print(f"\n{'='*54}")
    print(f"  {msg}")
    print(f"{'='*54}")

def ok(msg):   print(f"  ✓ {msg}")
def warn(msg): print(f"  ⚠ {msg}")
def err(msg):  print(f"  ✗ {msg}")
def step(msg): print(f"\n── {msg}")

def run(cmd, cwd=None, capture=False, fatal=False):
    """Run a shell command. Returns output string or None on failure."""
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=capture,
            text=True, timeout=300
        )
        if capture:
            return result.stdout.strip()
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        if fatal:
            err(f"Fatal: {e}")
            sys.exit(1)
        return None

def which(cmd):
    return shutil.which(cmd)

def ensure_dir(p):
    Path(p).mkdir(parents=True, exist_ok=True)

def load_env():
    """Load .env file into a dict."""
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env

def save_env(env):
    """Write env dict back to .env file."""
    ensure_dir(MILO_HOME)
    lines = [f"{k}={v}" for k, v in env.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n")
    ok(f".env saved → {ENV_FILE}")

def ask(prompt, default=""):
    """Interactive prompt with optional default."""
    hint = f" [{default}]" if default else ""
    try:
        val = input(f"  {prompt}{hint}: ").strip()
        return val or default
    except (KeyboardInterrupt, EOFError):
        print()
        return default

# ── NSSM (Windows services) ───────────────────────────────────────────────────

NSSM_EXE = MILO_HOME / "nssm.exe"
NSSM_URL  = "https://nssm.cc/ci/nssm-2.24-101-g897c7ad.zip"

def ensure_nssm():
    if NSSM_EXE.exists():
        return True
    step("Downloading NSSM...")
    zip_path = MILO_HOME / "nssm.zip"
    tmp_dir  = MILO_HOME / "nssm-tmp"
    try:
        import urllib.request, zipfile
        urllib.request.urlretrieve(NSSM_URL, zip_path)
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(tmp_dir)
        # Find nssm.exe in win64/
        for p in tmp_dir.rglob("win64/nssm.exe"):
            shutil.copy(p, NSSM_EXE)
            ok(f"NSSM installed → {NSSM_EXE}")
            break
        else:
            err("nssm.exe not found in zip")
            return False
    except Exception as e:
        err(f"NSSM download failed: {e}")
        return False
    finally:
        if zip_path.exists(): zip_path.unlink()
        if tmp_dir.exists(): shutil.rmtree(tmp_dir, ignore_errors=True)
    return NSSM_EXE.exists()

def nssm(*args):
    return run([str(NSSM_EXE)] + list(args), capture=True)

# ── systemd (Linux) ───────────────────────────────────────────────────────────

SYSTEMD_DIR = HOME / ".config" / "systemd" / "user"

def write_service_unit(name, cmd, workdir, log_file, env_file=None):
    ensure_dir(SYSTEMD_DIR)
    env_line = f"EnvironmentFile={env_file}" if env_file else ""
    unit = textwrap.dedent(f"""\
        [Unit]
        Description=Milo — {name}
        After=network.target

        [Service]
        Type=simple
        WorkingDirectory={workdir}
        ExecStart={' '.join(str(c) for c in cmd)}
        Restart=always
        RestartSec=10
        StandardOutput=append:{log_file}
        StandardError=append:{log_file}
        {env_line}

        [Install]
        WantedBy=default.target
    """)
    unit_file = SYSTEMD_DIR / f"milo-{name.lower()}.service"
    unit_file.write_text(unit)
    ok(f"systemd unit → {unit_file}")
    return unit_file

# ── Screen/Tmux (Termux fallback) ─────────────────────────────────────────────

def start_screen(session_name, cmd, workdir, log_file):
    """Start a detached screen session."""
    if run(["screen", "-list"], capture=True) and session_name in (run(["screen", "-list"], capture=True) or ""):
        warn(f"{session_name} already running")
        return
    ensure_dir(log_file.parent)
    full_cmd = ["screen", "-dmS", session_name,
                "bash", "-c",
                f"cd {workdir} && {' '.join(str(c) for c in cmd)} >> {log_file} 2>&1"]
    if run(full_cmd):
        ok(f"screen session {session_name} started")
    else:
        err(f"Failed to start {session_name}")

# ── Clone repos ───────────────────────────────────────────────────────────────

def clone_repos():
    banner("Step 1: Clone Repos")
    ensure_dir(MILO_HOME)
    env = load_env()
    gh_pat = env.get("GITHUB_PAT", "")

    for name, r in REPOS.items():
        dest = Path(r["dest"])
        if (dest / ".git").exists():
            ok(f"{name} already at {dest}")
            continue
        ensure_dir(dest.parent)
        url = r["url"]
        if gh_pat:
            url = url.replace("https://", f"https://{gh_pat}@")
        step(f"Cloning {name}...")
        if run(["git", "clone", "--depth", "1", url, str(dest)]):
            ok(f"{name} cloned")
        else:
            warn(f"{name} clone failed — add GITHUB_PAT to .env and retry")

# ── Install Python deps ───────────────────────────────────────────────────────

def install_deps():
    banner("Step 2: Install Dependencies")
    pip = "pip" if IS_TERMUX else sys.executable + " -m pip"
    pkgs = ["python-telegram-bot", "httpx", "requests"]
    for pkg in pkgs:
        step(f"Installing {pkg}...")
        cmd = (["pip", "install", pkg] if IS_TERMUX
               else [sys.executable, "-m", "pip", "install", pkg, "--break-system-packages"])
        if run(cmd):
            ok(pkg)
        else:
            warn(f"{pkg} failed — install manually")

# ── Configure secrets ─────────────────────────────────────────────────────────

def configure_secrets():
    banner("Step 3: Configure Secrets")
    env = load_env()

    fields = [
        ("TELEGRAM_BOT_TOKEN",      "Telegram bot token (from @BotFather)"),
        ("TELEGRAM_CHAT_ID",        "Your Telegram chat ID (from @userinfobot)"),
        ("ALLOWED_USER_IDS",        "Allowed Telegram user IDs (comma-separated)"),
        ("GITHUB_PAT",              "GitHub Personal Access Token"),
        ("SUPABASE_URL",            "Supabase project URL"),
        ("SUPABASE_SERVICE_ROLE_KEY","Supabase service role key"),
        ("SUPABASE_ANON_KEY",       "Supabase anon key"),
        ("OPENAI_API_KEY",          "OpenAI API key (optional)"),
        ("ANTHROPIC_API_KEY",       "Anthropic API key (optional)"),
    ]

    print("\n  Fill in secrets. Press Enter to keep existing value.\n")
    changed = False
    for key, label in fields:
        existing = env.get(key, "")
        hint = existing[:8] + "..." if existing else ""
        val = ask(f"{label}", hint)
        if val and val != hint:
            env[key] = val
            changed = True
        elif not val and not existing:
            env[key] = ""

    if changed:
        save_env(env)
    else:
        ok("No changes")

    # Also write bot .env from same secrets
    bot_env_dir = MILO_HOME / "agentmemory-milo" / "milo-bot"
    if bot_env_dir.exists():
        bot_env = bot_env_dir / ".env"
        lines = []
        for key in ["TELEGRAM_BOT_TOKEN", "ALLOWED_USER_IDS", "SUPABASE_URL", "SUPABASE_ANON_KEY"]:
            if env.get(key):
                lines.append(f"{key}={env[key]}")
        bot_env.write_text("\n".join(lines) + "\n")
        ok(f"Bot .env → {bot_env}")

# ── Register services ─────────────────────────────────────────────────────────

def register_services():
    banner("Step 4: Register Services")

    if IS_WINDOWS:
        _register_windows()
    elif IS_LINUX:
        _register_linux()
    elif IS_TERMUX:
        _register_termux()

def _register_windows():
    if not ensure_nssm():
        err("NSSM unavailable — register services manually")
        return

    env = load_env()
    env_file = str(ENV_FILE)

    for svc_name, svc in SERVICES.items():
        cmd = " ".join(f'"{c}"' if " " in str(c) else str(c) for c in svc["cmd_win"])
        ensure_dir(svc["log"].parent)

        nssm("install", svc_name, svc["cmd_win"][0], *[str(c) for c in svc["cmd_win"][1:]])
        nssm("set", svc_name, "AppDirectory", str(svc["workdir"]))
        nssm("set", svc_name, "AppStdout", str(svc["log"]))
        nssm("set", svc_name, "AppStderr", str(svc["log"]))
        nssm("set", svc_name, "AppEnvironmentExtra", f"MILO_ENV_FILE={env_file}")
        ok(f"NSSM: {svc_name} registered")

def _register_linux():
    env_file = str(ENV_FILE)
    for svc_name, svc in SERVICES.items():
        ensure_dir(svc["log"].parent)
        write_service_unit(svc_name, svc["cmd_unix"], svc["workdir"], svc["log"], env_file)
    run(["systemctl", "--user", "daemon-reload"])
    ok("systemd units reloaded")

def _register_termux():
    # Write startup script
    startup = MILO_HOME / "start-milo.sh"
    lines = ["#!/data/data/com.termux/files/usr/bin/bash", ""]
    for svc_name, svc in SERVICES.items():
        ensure_dir(svc["log"].parent)
        cmd_str = " ".join(str(c) for c in svc["cmd_unix"])
        lines.append(f"# {svc_name}")
        lines.append(f"screen -dmS {svc_name} bash -c '{cmd_str} >> {svc['log']} 2>&1'")
        lines.append("")
    startup.write_text("\n".join(lines))
    startup.chmod(0o755)
    ok(f"Termux startup script → {startup}")
    ok("Run: ~/.milo/start-milo.sh")

# ── Start ──────────────────────────────────────────────────────────────────────

def start_services():
    banner("Starting Milo Services")
    if IS_WINDOWS:
        for svc_name in SERVICES:
            result = nssm("start", svc_name)
            ok(f"{svc_name}: started") if result else warn(f"{svc_name}: may already be running")
    elif IS_LINUX:
        for svc_name in SERVICES:
            unit = f"milo-{svc_name.lower()}.service"
            if run(["systemctl", "--user", "start", unit]):
                ok(f"{unit} started")
            else:
                warn(f"{unit} failed — run: journalctl --user -u {unit}")
    elif IS_TERMUX:
        for svc_name, svc in SERVICES.items():
            start_screen(svc_name, svc["cmd_unix"], svc["workdir"], svc["log"])

# ── Stop ──────────────────────────────────────────────────────────────────────

def stop_services():
    banner("Stopping Milo Services")
    if IS_WINDOWS:
        for svc_name in SERVICES:
            nssm("stop", svc_name)
            ok(f"{svc_name}: stopped")
    elif IS_LINUX:
        for svc_name in SERVICES:
            unit = f"milo-{svc_name.lower()}.service"
            run(["systemctl", "--user", "stop", unit])
            ok(f"{unit} stopped")
    elif IS_TERMUX:
        for svc_name in SERVICES:
            run(["screen", "-S", svc_name, "-X", "quit"])
            ok(f"screen {svc_name} killed")

# ── Status ─────────────────────────────────────────────────────────────────────

def status():
    banner("Milo System Status")

    # Platform
    print(f"  Platform : {platform.system()} ({'Termux' if IS_TERMUX else platform.machine()})")
    print(f"  Python   : {sys.version.split()[0]}")
    print(f"  Milo home: {MILO_HOME}")
    print()

    # Repos
    print("  Repos:")
    for name, r in REPOS.items():
        dest = Path(r["dest"])
        if (dest / ".git").exists():
            branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=dest, capture=True) or "?"
            commit = run(["git", "log", "-1", "--format=%h %s"], cwd=dest, capture=True) or "?"
            print(f"    ✓ {name:<20} [{branch}] {commit[:60]}")
        else:
            print(f"    ✗ {name:<20} NOT CLONED")

    print()
    print("  Services:")
    if IS_WINDOWS:
        for svc_name in SERVICES:
            result = nssm("status", svc_name) or ""
            state = "RUNNING" if "SERVICE_RUNNING" in result else result.strip() or "NOT INSTALLED"
            icon = "✓" if state == "RUNNING" else "✗"
            print(f"    {icon} {svc_name:<20} {state}")
    elif IS_LINUX:
        for svc_name in SERVICES:
            unit = f"milo-{svc_name.lower()}.service"
            result = run(["systemctl", "--user", "is-active", unit], capture=True) or "inactive"
            icon = "✓" if result == "active" else "✗"
            print(f"    {icon} {unit:<30} {result}")
    elif IS_TERMUX:
        screens = run(["screen", "-list"], capture=True) or ""
        for svc_name in SERVICES:
            icon = "✓" if svc_name in screens else "✗"
            state = "RUNNING" if svc_name in screens else "STOPPED"
            print(f"    {icon} {svc_name:<20} {state}")

    print()
    # .env check
    env = load_env()
    missing = [k for k in ["TELEGRAM_BOT_TOKEN", "GITHUB_PAT"] if not env.get(k)]
    if missing:
        warn(f"Missing secrets: {', '.join(missing)}")
    else:
        ok(".env configured")

    # Log tails
    for svc_name, svc in SERVICES.items():
        log = svc["log"]
        if log.exists():
            lines = log.read_text().splitlines()
            last = lines[-1] if lines else "(empty)"
            print(f"\n  {svc_name} last log: {last[:80]}")

# ── Backup ─────────────────────────────────────────────────────────────────────

def backup():
    banner("Backup: Push All Repos")
    for name, r in REPOS.items():
        dest = Path(r["dest"])
        if not (dest / ".git").exists():
            warn(f"{name}: not cloned, skipping")
            continue
        step(f"Backing up {name}...")
        run(["git", "add", "-A"], cwd=dest)
        run(["git", "commit", "-m", "auto-backup: milo portable sync"], cwd=dest)
        if run(["git", "push"], cwd=dest):
            ok(f"{name}: pushed")
        else:
            warn(f"{name}: push failed (may have nothing new, or check PAT)")

# ── Restore ────────────────────────────────────────────────────────────────────

def restore():
    banner("Restore: Pull All Repos")
    for name, r in REPOS.items():
        dest = Path(r["dest"])
        if not (dest / ".git").exists():
            warn(f"{name}: not cloned yet — run 'install' first")
            continue
        if run(["git", "pull", "--rebase"], cwd=dest):
            ok(f"{name}: up to date")
        else:
            warn(f"{name}: pull failed")

# ── Install ────────────────────────────────────────────────────────────────────

def install():
    banner("Milo Portable — Full Install")
    print(f"\n  Target platform : {'Windows' if IS_WINDOWS else 'Termux/Android' if IS_TERMUX else 'Linux'}")
    print(f"  Milo home       : {MILO_HOME}\n")

    ensure_dir(MILO_HOME)
    ensure_dir(LOGS_DIR)

    configure_secrets()
    clone_repos()
    install_deps()
    register_services()

    banner("Install Complete")
    print(f"""
  Next steps:
    python milo.py start    → start all services
    python milo.py status   → verify everything is running
    python milo.py backup   → push repos to GitHub

  Logs: {LOGS_DIR}
""")

# ── Main ───────────────────────────────────────────────────────────────────────

COMMANDS = {
    "install": install,
    "start":   start_services,
    "stop":    stop_services,
    "status":  status,
    "backup":  backup,
    "restore": restore,
}

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        print("  Available commands:", ", ".join(COMMANDS))
        sys.exit(1)
    COMMANDS[sys.argv[1]]()

if __name__ == "__main__":
    main()
