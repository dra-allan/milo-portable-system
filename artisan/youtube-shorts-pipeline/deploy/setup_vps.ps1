# Provision the Shorts pipeline on a fresh Windows VPS (Windows Server 2025).
#
# Run AFTER cloning the repo and copying the state bundle into this box:
#   git clone https://github.com/dra-allan/milo-portable-system.git
#   cd milo-portable-system\artisan\youtube-shorts-pipeline
#   (drag state_bundle.tar.gz into this machine, e.g. Desktop)
#   powershell -ExecutionPolicy Bypass -File deploy\setup_vps.ps1 -Bundle <path>\state_bundle.tar.gz
#
# Idempotent: safe to re-run.

param(
    [string]$Bundle = ""
)

$ErrorActionPreference = 'Stop'
$ROOT = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$TASK = 'YouTube Shorts Pipeline Daemon'

function Step($n, $msg) { Write-Host "==> [$n/6] $msg" -ForegroundColor Cyan }

# [1/6] ffmpeg (best-effort; already installed via winget on our VPS)
Step 1 'ffmpeg check'
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host '   ffmpeg not found on PATH; installing via winget...'
    try { winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements | Out-Null }
    catch { Write-Warning '   winget install failed; install ffmpeg manually and add to PATH.' }
} else {
    Write-Host "   ffmpeg: $((Get-Command ffmpeg).Source)"
}

# [2/6] Python venv + requirements
Step 2 'Python venv + requirements'
if (-not (Test-Path "$ROOT\venv")) { python -m venv "$ROOT\venv" }
& "$ROOT\venv\Scripts\python.exe" -m pip install --upgrade -q pip
& "$ROOT\venv\Scripts\python.exe" -m pip install -q -r "$ROOT\requirements.txt"

# [3/6] Restore state bundle
Step 3 'Restore state bundle'
if ($Bundle -and (Test-Path $Bundle)) {
    tar -xzf $Bundle -C $ROOT
    Write-Host '   restored tokens / niches / .env / db / transcripts / clip plans'
} else {
    Write-Host '   !! no bundle given -- continuing with empty state (uploads will fail without tokens)'
}

# [4/6] Fix .env paths for this machine
Step 4 'Fix .env paths for this machine'
$ENVFILE = "$ROOT\.env"
# Old bundles restored the env as config/.env; promote it so the pipeline
# (which reads $ROOT/.env) picks it up even without re-transferring.
if (-not (Test-Path $ENVFILE) -and (Test-Path "$ROOT\config\.env")) {
    Copy-Item "$ROOT\config\.env" $ENVFILE
    Write-Host '   promoted config/.env -> .env'
}
if (Test-Path $ENVFILE) {
    $content = Get-Content $ENVFILE -Raw
    # Unconditionally point the four output dirs at this box's data\ dirs.
    # The old value could be a Windows path, a *nix path, or the
    # {{VIDEO_FACTORY_ROOT}} placeholder from a template — all are wrong here.
    $replaced = 0
    $map = @{ 'SHORTS_DIR' = 'data\shorts'; 'TEMP_DIR' = 'data\temp'; 'LOG_DIR' = 'data\logs'; 'MUSIC_DIR' = 'data\music' }
    foreach ($key in $map.Keys) {
        if ($content -match "(?m)^$key=") {
            $content = $content -replace "(?m)^$key=.*", "$key=$ROOT\$($map[$key])"
            $replaced++
        } else {
            $content += "`r`n$key=$ROOT\$($map[$key])"
            $replaced++
        }
    }
    if ($replaced) {
        Set-Content $ENVFILE $content -Encoding UTF8
        Write-Host "   rewrote $replaced Windows path(s) to $ROOT\data\..."
    }
    # 2 GB box: keep the memory-hungry stages single-threaded.
    $tune = @{
        'RENDER_WORKERS'           = '1'
        'TRANSCRIBE_WORKERS'       = '1'
        'TRANSCRIBE_MODEL'         = 'tiny'
        'SMART_MAX_PEOPLE'         = '2'
        'SCHEDULE_MAX_TOTAL'       = '6'
        'SCHEDULE_MAX_VIDEOS'      = '2'
    }
    foreach ($k in $tune.Keys) {
        if ($content -match "(?m)^$k=") { $content = $content -replace "(?m)^$k=.*", "$k=$($tune[$k])" }
        else { $content += "`r`n$k=$($tune[$k])" }
    }
    Set-Content $ENVFILE $content -Encoding UTF8
    Write-Host '   tuned memory/pace settings for a 2 GB box'
} else {
    Copy-Item "$ROOT\config\.env.template" $ENVFILE
    Write-Host '   no .env present; copied template (edit it: channels, paths)'
}

# [5/6] Environment check
Step 5 'Environment check'
Push-Location $ROOT
try {
    & "$ROOT\venv\Scripts\python.exe" -m src.main --mode test
    if ($LASTEXITCODE -ne 0) { throw '--mode test reported problems. Fix before scheduling.' }
} finally { Pop-Location }

# [6/6] Install Task Scheduler daemon (runs `--mode schedule` at every boot,
#      as SYSTEM, so a reboot never strands the pipeline waiting for an RDP
#      login. All pipeline paths are read from .env / anchored to the repo, so
#      SYSTEM is safe here.)
Step 6 'Install Task Scheduler daemon'
$action = New-ScheduledTaskAction -Execute "$ROOT\venv\Scripts\python.exe" `
    -Argument "-m src.main --mode schedule" -WorkingDirectory $ROOT
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
try {
    Unregister-ScheduledTask -TaskName $TASK -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $TASK -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings `
        -Description 'YouTube Shorts pipeline scheduled sweeps (runs at boot)' | Out-Null
    Write-Host "   scheduled task '$TASK' installed (runs at boot as SYSTEM)"
} catch {
    Write-Warning "   could not register task: $($_.Exception.Message)"
}

Write-Host ''
Write-Host 'Done. Commands:' -ForegroundColor Green
Write-Host "  run once now:   & '$ROOT\venv\Scripts\python.exe' -m src.main --mode once"
Write-Host "  daemon log:     Get-Content '$ROOT\data\logs\pipeline.log' -Tail 50 -Wait"
Write-Host "  test again:     & '$ROOT\venv\Scripts\python.exe' -m src.main --mode test"