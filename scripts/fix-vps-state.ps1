# One-time fix: stop the VPS checkout from fighting the local machine over
# the tracked state/ snapshot files.
#
# Problem: the 'frequent backup' routine runs `milo backup` on BOTH machines.
# Both snapshot their own live state into repo's state/ and push. Every pull
# on the VPS then collides with the local machine's pushed state edits.
#
# Fix: mark every tracked file under state/ as skip-worktree on THIS machine.
# Git then ignores local edits to those files here (no add -A, no commit, no
# conflict), while the other machine keeps publishing snapshots as before.
# Milo itself is unaffected - it reads/writes those files via the filesystem,
# not git.
#
# Idempotent and safe to re-run. Run on the VPS:
#   powershell -ExecutionPolicy Bypass -File fix-vps-state.ps1

$ErrorActionPreference = 'Stop'
$ROOT = (Split-Path $PSScriptRoot -Parent)

Set-Location $ROOT

# 1. Discard any local edits to state/ from the failed merges.
git checkout -- state/ 2>&1 | Out-Null

# 2. Mark every tracked file under state/ as skip-worktree.
$tracked = git ls-files -z state/
if (-not $tracked) {
    Write-Host 'No tracked files under state/ - nothing to do.'
    exit 0
}
$paths = $tracked -split "`0" | Where-Object { $_ }
git update-index --skip-worktree -- $paths
$skipped = git ls-files -v state/ | Where-Object { $_ -match '^S' } | Measure-Object
Write-Host "Marked $($skipped.Count) state files as skip-worktree."

# 3. Point the VPS's own backup routine at local-only commits so it never
#    publishes a divergent snapshot again.
$routines = Join-Path $ROOT 'state\routines.json'
if (Test-Path $routines) {
    $json = Get-Content $routines -Raw
    if ($json -match "milo backup -m 'routine: frequent backup'") {
        $json = $json -replace "milo backup -m 'routine: frequent backup'", "milo backup --no-push -m 'routine: frequent backup'"
        Set-Content -Path $routines -Value $json -Encoding utf8
        Write-Host "Updated frequent-backup routine to --no-push."
    } else {
        Write-Host 'frequent-backup routine already uses --no-push (or command differs).'
    }
}

# 4. Pull cleanly - state/ edits on this machine are now invisible to git.
git pull --rebase --autostash

Write-Host ''
Write-Host 'Done. From now on, plain `git pull` on this machine will not fight' -ForegroundColor Green
Write-Host 'over state/. The local machine remains the publisher of snapshots.'