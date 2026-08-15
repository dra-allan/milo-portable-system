# One sync command for both Milo machines.
#
# - Rebase-first so remote work is never clobbered.
# - state/ is single-writer per machine (backup/<machine> branch); main carries
#   portable code only, so pull --rebase should fast-forward cleanly.
# - On source-file conflict: reconcile, keep the union, add a WORK_CLAIMS.md DONE
#   row. On state/ conflict: last-writer-wins, take remote (your local memory.db
#   is authoritative; the repo copy is only the backup snapshot).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/git-sync.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/git-sync.ps1 -message "msg"
#   (omit -message to reuse the last commit message)

param([string]$message = '')

$ErrorActionPreference = 'Stop'
$ROOT = (Split-Path $PSScriptRoot -Parent)
Set-Location $ROOT

$env:GIT_TERMINAL_PROMPT = '0'
$env:GCM_INTERACTIVE = 'Never'

function Info($text) { Write-Host "[sync] $text" -ForegroundColor Cyan }
function Fail($text) {
    Write-Host "[sync] FAILED: $text" -ForegroundColor Red
    exit 1
}

# Machine tag for the commit message.
$machine = if ($env:COMPUTERNAME -match 'EC2') { 'brain' } else { 'pc' }

# 0. Report identity so we can tell which box is pushing.
$name = git config user.name
$email = git config user.email
if (-not $name -or -not $email) {
    Fail "git identity not set. Run: git config user.name 'Milo ($machine)' ; git config user.email 'milo.$machine@milo.local'"
}
Info "pushing as $name <$email>"

# 1. Fetch + rebase. Never merge, never clobber.
git fetch origin main
if ($LASTEXITCODE -ne 0) { Fail 'fetch' }
$dirty = git status --porcelain
if ($dirty) {
    Info 'working tree dirty; stashing'
    git stash push -u -m 'git-sync autostash'
    if ($LASTEXITCODE -ne 0) { Fail 'stash' }
}
$rebased = $false
if (git rev-parse --verify origin/main) {
    try {
        git rebase origin/main
        if ($LASTEXITCODE -ne 0) {
            # Conflict. Try last-writer-wins on state/ only, then report the rest.
            git checkout --theirs -- state/ 2>$null
            git add state/ 2>$null
            if ($LASTEXITCODE -ne 0) {
                git rebase --abort
                Fail "rebase conflict outside state/. Reconcile manually, keep the union, log it in WORK_CLAIMS.md."
            }
            Info 'state/ conflict resolved last-writer-wins (took remote)'
        }
        $rebased = $true
    } catch {
        Fail "rebase failed: $($_.Exception.Message)"
    }
}
if ($dirty) {
    git stash pop
    if ($LASTEXITCODE -ne 0) { Fail 'stash pop' }
}

# 2. Stage portable code + ledger. state/ is never added by this script.
git add -A -- ':!state/'
$staged = git diff --cached --name-only
if (-not $staged) {
    Info 'nothing to commit'
} else {
    if (-not $message) {
        $message = git log -1 --format=%s
        if (-not $message) { $message = 'milo sync' }
    }
    $full = "$message [$machine]"
    git commit -m $full
    if ($LASTEXITCODE -ne 0) { Fail "commit" }
    Info "committed: $full"
}

# 3. Push.
git push origin main
if ($LASTEXITCODE -ne 0) { Fail 'push' }

# 4. Verify remote.
$head = git rev-parse HEAD
$remote = git ls-remote origin -h refs/heads/main
if ($remote -notmatch "^$head") { Fail 'remote not at HEAD after push' }

Info 'DONE'