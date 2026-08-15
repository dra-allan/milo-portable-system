# Provision the ranking-shorts pipeline on a fresh Windows VPS.
#
# Run AFTER cloning the repo and copying the state bundle into this box:
#   git clone https://github.com/dra-allan/milo-portable-system.git
#   cd milo-portable-system\artisan\ranking-shorts-pipeline
#   powershell -ExecutionPolicy Bypass -File deploy\setup_vps.ps1 -Bundle <path>\ranking_state_bundle.tar.gz
#
# Idempotent: safe to re-run.

param(
    [string]$Bundle = "",
    # Where the shorts pipeline's credentials.json lives on this box. The
    # ranking pipeline reuses it for OAuth client secrets.
    [string]$ShortsRoot = "C:\milo-portable-system\artisan\youtube-shorts-pipeline"
)

$ErrorActionPreference = 'Stop'
$ROOT = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$TASK = 'Ranking Shorts Pipeline Daemon'

function Step($n, $msg) { Write-Host "==> [$n/6] $msg" -ForegroundColor Cyan }

# [1/6] ffmpeg check
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

# [3/6] Restore state bundle (config/.env + data/ranking.db + data/plans/)
Step 3 'Restore state bundle'
if ($Bundle -and (Test-Path $Bundle)) {
    New-Item -ItemType Directory -Force -Path "$ROOT\data\plans" | Out-Null
    tar -xzf $Bundle -C $ROOT
    Write-Host '   restored config/.env, ranking.db, plans/'
} else {
    Write-Host '   !! no bundle given -- continuing with empty state (Gemini keys and caps missing)'
}

# [4/6] Rewrite machine-specific env values
Step 4 'Rewrite env for this machine'
$ENVFILE = "$ROOT\config\.env"
if (Test-Path $ENVFILE) {
    $content = Get-Content $ENVFILE -Raw
    $changed = 0
    # Factory root -> this box. NOTE: config derives runtime_root as
    # <VIDEO_FACTORY_ROOT>/ranking-shorts-pipeline, so point this at the
    # PARENT of the repo (artisan/), not the repo itself.
    $factory = Split-Path $ROOT -Parent
    if ($content -match "(?m)^VIDEO_FACTORY_ROOT=.*") {
        $content = $content -replace "(?m)^VIDEO_FACTORY_ROOT=.*", "VIDEO_FACTORY_ROOT=$factory"
        $changed++
    }
    # OAuth client secrets -> the shorts pipeline's credentials.json on this box.
    if ($content -match "(?m)^RANKING_OAUTH_CLIENT_SECRETS=.*") {
        $content = $content -replace "(?m)^RANKING_OAUTH_CLIENT_SECRETS=.*", "RANKING_OAUTH_CLIENT_SECRETS=$ShortsRoot\credentials.json"
        $changed++
    }
    # 2 GB box tuning.
    $tune = @{ 'RENDER_WORKERS' = '1'; 'RANKING_DOWNLOAD_CONCURRENCY' = '2'; 'SCHEDULE_JITTER_MINUTES' = '15' }
    foreach ($k in $tune.Keys) {
        if ($content -match "(?m)^$k=") { $content = $content -replace "(?m)^$k=.*", "$k=$($tune[$k])" }
        else { $content += "`r`n$k=$($tune[$k])" }
    }
    Set-Content $ENVFILE $content -Encoding UTF8
    Write-Host "   rewrote $changed path(s) + tuned for 2 GB"
} else {
    Copy-Item "$ROOT\config\.env.template" $ENVFILE
    Write-Host '   no config/.env; copied template (fill in Gemini keys, OAuth path)'
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
#      login. All pipeline paths come from config/.env and are machine-
#      absolute after the rewrite above, so SYSTEM is safe here.)
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
        -Description 'Ranking shorts pipeline scheduled sweeps (runs at boot)' | Out-Null
    Write-Host "   scheduled task '$TASK' installed (runs at boot as SYSTEM)"
} catch {
    Write-Warning "   could not register task: $($_.Exception.Message)"
}

Write-Host ''
Write-Host 'Done. Commands:' -ForegroundColor Green
Write-Host "  run once now:   & '$ROOT\venv\Scripts\python.exe' -m src.main --mode once --topic fishing_moments"
Write-Host "  full auto:      & '$ROOT\venv\Scripts\python.exe' -m src.main --mode auto"
Write-Host "  sweep now:      & '$ROOT\venv\Scripts\python.exe' -m src.main --mode sweep"