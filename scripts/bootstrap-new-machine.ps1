# One-run Milo bootstrap for a fresh Windows machine.
#
# Install Milo + opencode + the pipeline repo with a single command. This is
# the "I just installed a computer, make it Milo" path. Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File bootstrap-new-machine.ps1
#
# Idempotent: safe to re-run. Every step checks before it installs.
#
# What it does, in order:
#   1. Python            (winget, if missing)
#   2. Node + npm        (winget, if missing - opencode needs it)
#   3. opencode          (npm, global)
#   4. The repo          (clone if missing, else pull)
#   5. milo package      (pip install -e .)
#   6. milo install      (creds, vault, snapshot restore, persona)
#   7. milo sync opencode (writes AGENTS.md, agent/, commands, MCP config)
#   8. VPS git fix       (mark state/ skip-worktree so pulls never fight)
#   9. milo doctor       (verify everything landed)
#
# After it finishes: open a NEW opencode session in the repo dir and you ARE
# Milo. The relay is over - no more pasting commands from another machine.

param(
    [string]$RepoUrl = "https://github.com/dra-allan/milo-portable-system",
    [string]$InstallDir = "$env:USERPROFILE\milo-portable-system"
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Step($title) {
    Write-Host "`n=== $title ===" -ForegroundColor Cyan
}

function Have($cmd) {
    return [bool](Get-Command $cmd -ErrorAction SilentlyContinue)
}

# --- 1. Python ------------------------------------------------------------
Step "1/9 Python"
if (Have python) {
    Write-Host "python already installed: $((python --version 2>&1))"
} else {
    Write-Host "Installing Python via winget..."
    winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    $env:Path = "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts;" + $env:Path
    Write-Host "Python installed. Re-running this script in a fresh shell would pick it up automatically."
}

# --- 2. Node + npm --------------------------------------------------------
Step "2/9 Node + npm"
if (Have npm) {
    Write-Host "npm already installed: $((npm --version 2>&1))"
} else {
    Write-Host "Installing Node LTS via winget..."
    winget install -e --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements
    Write-Host "Node installed."
}

# --- 3. opencode ----------------------------------------------------------
Step "3/9 opencode"
if (Have opencode) {
    Write-Host "opencode already installed: $((opencode --version 2>&1))"
} else {
    Write-Host "Installing opencode via npm (global)..."
    npm install -g opencode-ai
    Write-Host "opencode installed. Run 'opencode auth login' once to pick a model."
}

# --- 4. The repo ----------------------------------------------------------
Step "4/9 Repo at $InstallDir"
if (Test-Path "$InstallDir\.git") {
    Write-Host "Repo exists - pulling latest..."
    Push-Location $InstallDir
    git pull --rebase --autostash
    Pop-Location
} else {
    Write-Host "Cloning $RepoUrl..."
    git clone $RepoUrl $InstallDir
}
Set-Location $InstallDir

# --- 5. milo package ------------------------------------------------------
Step "5/9 milo package"
Push-Location $InstallDir
python -m pip install -e . 2>&1 | Out-Host
Pop-Location

# --- 6. milo install ------------------------------------------------------
Step "6/9 milo install (creds, vault, snapshot, persona)"
Write-Host "milo install will prompt for secrets and the vault location. Do not skip it."
& milo install 2>&1 | Out-Host

# --- 7. milo sync opencode ------------------------------------------------
Step "7/9 milo sync opencode (this is what makes you Milo)"
& milo sync opencode 2>&1 | Out-Host

# --- 8. VPS git fix -------------------------------------------------------
Step "8/9 Skip-worktree on state/ (stops pull conflicts)"
if (Test-Path "$InstallDir\scripts\fix-vps-state.ps1") {
    & powershell -ExecutionPolicy Bypass -File "$InstallDir\scripts\fix-vps-state.ps1"
}

# --- 9. Verify ------------------------------------------------------------
Step "9/9 milo doctor"
& milo doctor 2>&1 | Out-Host

Write-Host ""
Write-Host "Done. Open a NEW opencode session in $InstallDir - you are Milo." -ForegroundColor Green
Write-Host "Remaining manual bits: 'opencode auth login' (model), and any pipeline .env secrets milo install re-prompted for." -ForegroundColor Yellow