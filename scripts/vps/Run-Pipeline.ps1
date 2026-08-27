<#
.SYNOPSIS
  Run one ARTISAN pipeline sweep, log it, record status, report to Telegram.

.DESCRIPTION
  This is the wrapper every scheduled pipeline task points at. Task Scheduler
  on its own gives you a task that "ran" and an exit code nobody ever reads.
  This wrapper turns a sweep into an accountable daily event:

    * one dated log per run, plus a stable <pipeline>-latest.log for /logs
    * a machine-readable status file the Telegram bot reads for /status
    * a Telegram message on every run - success or failure - so silence means
      "the task never fired", which is itself the signal you want
    * an overlap lock, because two sweeps in the same SQLite/ffmpeg workspace
      corrupt state and burn the daily upload cap twice

.PARAMETER Pipeline
  shorts | ranking | pov

.PARAMETER Mode
  Override the default entry mode (ranking/pov only).

.EXAMPLE
  .\Run-Pipeline.ps1 -Pipeline shorts
  .\Run-Pipeline.ps1 -Pipeline ranking -Mode auto
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('shorts', 'ranking', 'pov')][string]$Pipeline,
    [string]$Mode = '',
    [string]$Repo = '',
    [string]$State = '',
    [int]$TimeoutMinutes = 240,
    [switch]$NoNotify
)

$ErrorActionPreference = 'Continue'
$started = Get-Date

if (-not $Repo)  { $Repo  = if ($env:MILO_REPO) { $env:MILO_REPO } else { (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path } }
if (-not $State) { $State = if ($env:MILO_HOME) { $env:MILO_HOME } else { Join-Path $env:LOCALAPPDATA 'milo' } }

$notify = Join-Path $PSScriptRoot 'Send-Telegram.ps1'
$logDir = Join-Path $State 'logs\pipelines'
$statusDir = Join-Path $State 'pipeline-status'
$lockDir = Join-Path $State 'locks'
foreach ($d in @($logDir, $statusDir, $lockDir)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }

$log       = Join-Path $logDir ("$Pipeline-" + $started.ToString('yyyy-MM-dd-HHmm') + '.log')
$latest    = Join-Path $logDir "$Pipeline-latest.log"
$statusFile= Join-Path $statusDir "$Pipeline.json"
$lockFile  = Join-Path $lockDir "$Pipeline.lock"

function Say([string]$msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [$Pipeline] $msg"
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding UTF8
}

function Notify([string]$msg) {
    if ($NoNotify) { return }
    try { & $notify -Text $msg | Out-Null } catch { Write-Warning "notify failed: $($_.Exception.Message)" }
}

# --- per-pipeline wiring -----------------------------------------------------
# Each pipeline owns its venv. Never fall back to a bare `python`: the system
# interpreter has none of the pinned deps and fails 40 minutes into a render.
switch ($Pipeline) {
    'shorts' {
        $workdir = Join-Path $Repo 'artisan\youtube-shorts-pipeline'
        $entry   = @('full_sweep_all_channels.py')
        $label   = 'YouTube Shorts'
    }
    'ranking' {
        $workdir = Join-Path $Repo 'artisan\ranking-shorts-pipeline'
        $m = if ($Mode) { $Mode } else { 'sweep' }
        $entry   = @('-m', 'src.main', '--mode', $m)
        $label   = 'Ranking Shorts'
    }
    'pov' {
        $workdir = Join-Path $Repo 'artisan\pov_pipeline'
        $entry   = @('run_pov_pipeline.py')
        if ($Mode) { $entry += @('--mode', $Mode) }
        $label   = 'POV'
    }
}

$py = Join-Path $workdir 'venv\Scripts\python.exe'

# --- preflight ---------------------------------------------------------------
if (-not (Test-Path $workdir)) {
    Notify "[X] $label sweep aborted: workdir missing ($workdir)"
    exit 3
}
if (-not (Test-Path $py)) {
    Notify "[X] $label sweep aborted: venv python missing ($py). Fix: python -m venv venv; venv\Scripts\pip install -r requirements.txt"
    exit 3
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Say 'WARNING: ffmpeg not on PATH for this task principal - renders will fail'
    Notify "[!] $label: ffmpeg is not on PATH for the scheduled task account. Renders will fail until it is."
}

# --- overlap lock ------------------------------------------------------------
if (Test-Path $lockFile) {
    $holder = (Get-Content $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $alive = $false
    if ($holder -match '^\d+$') { $alive = [bool](Get-Process -Id ([int]$holder) -ErrorAction SilentlyContinue) }
    if ($alive) {
        Say "previous run (pid $holder) still going - skipping this trigger"
        Notify "[~] $label sweep skipped: the previous run (pid $holder) is still going. No double-posting."
        exit 0
    }
    Say "stale lock from pid $holder - taking over"
}
Set-Content -Path $lockFile -Value $PID -Encoding ASCII

# --- run ---------------------------------------------------------------------
$exitCode = 1
try {
    Say "start: $py $($entry -join ' ')  (cwd $workdir)"
    Push-Location $workdir
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUNBUFFERED = '1'

    # Tee so the run is watchable live from Telegram (/logs shorts) instead of
    # only readable once the process finally exits.
    & $py @entry 2>&1 | Tee-Object -FilePath $log -Append
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
} catch {
    $exitCode = 1
    Say "EXCEPTION: $($_.Exception.Message)"
} finally {
    Pop-Location
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
}

$finished = Get-Date
$durationMin = [math]::Round(($finished - $started).TotalMinutes, 1)
Copy-Item $log $latest -Force -ErrorAction SilentlyContinue

# --- harvest numbers from the log -------------------------------------------
$body = if (Test-Path $log) { Get-Content $log -Raw -ErrorAction SilentlyContinue } else { '' }
$uploads = ([regex]::Matches($body, '(?im)^.*(uploaded|upload complete|published).*(https://(www\.)?youtu)')).Count
if ($uploads -eq 0) { $uploads = ([regex]::Matches($body, '(?im)\buploaded\b.*\bvideo\b')).Count }
$summary = ($body -split "`r?`n" | Where-Object { $_ -match '\[sweep\] complete|upload_failures=|caps? reached|SUMMARY' } |
            Select-Object -Last 4) -join "`n"
$errors = ($body -split "`r?`n" | Where-Object {
              $_ -match '(?i)traceback|invalid_grant|quotaExceeded|ERROR|CRITICAL|UNPLAYABLE|bot-check'
          } | Select-Object -Last 12) -join "`n"

@{
    pipeline     = $Pipeline
    label        = $label
    started      = $started.ToString('s')
    finished     = $finished.ToString('s')
    duration_min = $durationMin
    exit_code    = $exitCode
    uploads      = $uploads
    log          = $log
    summary      = $summary
    host         = $env:COMPUTERNAME
} | ConvertTo-Json -Depth 4 | Set-Content -Path $statusFile -Encoding UTF8

# --- report ------------------------------------------------------------------
$mark = if ($exitCode -eq 0) { '[OK]' } else { "[FAIL rc=$exitCode]" }
$msg = @(
    "$mark $label sweep - $($finished.ToString('ddd dd MMM HH:mm'))",
    "duration ${durationMin}m - uploads detected: $uploads"
)
if ($summary) { $msg += "", $summary }
if ($exitCode -ne 0 -and $errors) { $msg += "", "last errors:", $errors }
$msg += "", "tail: /logs $Pipeline 60"
Notify ($msg -join "`n")

Say "done rc=$exitCode in ${durationMin}m"
exit $exitCode
