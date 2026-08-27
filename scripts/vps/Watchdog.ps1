<#
.SYNOPSIS
  Keep the Milo daemons alive and shout when they are not. Runs every 10 min.

.DESCRIPTION
  Checks, in order of "what actually breaks on this box":

    1. opencode server answering on /global/health - the agent path dies with it
    2. Telegram bot process alive - no bot, no control plane
    3. pipeline status files fresh (< 26h) and last exit code 0
    4. disk headroom - ffmpeg renders fail ugly at low free space
    5. YouTube auth rot (invalid_grant) and quota walls in today's logs

  Anything dead gets restarted once, then alerted. Alerts are de-duplicated for
  six hours per issue: a watchdog that pages you every ten minutes gets muted,
  and a muted watchdog is worse than none.
#>
[CmdletBinding()]
param(
    [string]$Repo = '',
    [string]$State = '',
    [int]$AlertCooldownHours = 6
)

$ErrorActionPreference = 'Continue'
if (-not $Repo)  { $Repo  = if ($env:MILO_REPO) { $env:MILO_REPO } else { (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path } }
if (-not $State) { $State = if ($env:MILO_HOME) { $env:MILO_HOME } else { Join-Path $env:LOCALAPPDATA 'milo' } }

$notify   = Join-Path $PSScriptRoot 'Send-Telegram.ps1'
$logFile  = Join-Path $State 'logs\watchdog.log'
$seenFile = Join-Path $State 'watchdog-alerts.json'
New-Item -ItemType Directory -Force -Path (Split-Path $logFile) | Out-Null

function Log([string]$m) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m"
    Add-Content -Path $logFile -Value $line -Encoding UTF8
    Write-Host $line
}

$seen = @{}
if (Test-Path $seenFile) {
    try { (Get-Content $seenFile -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $seen[$_.Name] = $_.Value } } catch { $seen = @{} }
}

function Alert([string]$key, [string]$message) {
    $now = Get-Date
    if ($seen.ContainsKey($key)) {
        $last = [datetime]$seen[$key]
        if (($now - $last).TotalHours -lt $AlertCooldownHours) { Log "suppressed ($key)"; return }
    }
    $seen[$key] = $now.ToString('s')
    Log "ALERT $key :: $message"
    try { & $notify -Text "[watchdog] $message" | Out-Null } catch { Log "notify failed: $($_.Exception.Message)" }
}

function Restart-Daemon([string]$task) {
    $t = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    if (-not $t) { return $false }
    try {
        if ($t.State -eq 'Running') { Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue; Start-Sleep 3 }
        Start-ScheduledTask -TaskName $task -ErrorAction Stop
        Log "restarted $task"
        return $true
    } catch { Log "could not restart ${task}: $($_.Exception.Message)"; return $false }
}

# 1. opencode server -----------------------------------------------------------
$server = if ($env:OPENCODE_SERVER_URL) { $env:OPENCODE_SERVER_URL } else { 'http://127.0.0.1:4096' }
$ocOk = $false
try { $ocOk = [bool](Invoke-RestMethod -Uri "$server/global/health" -TimeoutSec 6).healthy } catch { $ocOk = $false }
if (-not $ocOk) {
    Log "opencode server down at $server"
    if (Restart-Daemon 'Milo-OpencodeServer') {
        Start-Sleep 20
        try { $ocOk = [bool](Invoke-RestMethod -Uri "$server/global/health" -TimeoutSec 10).healthy } catch { $ocOk = $false }
    }
    if (-not $ocOk) { Alert 'opencode-down' "opencode server is down at $server and would not restart. /ask is dead until it is back." }
} else { Log 'opencode server ok' }

# 2. telegram bot --------------------------------------------------------------
$botTask = Get-ScheduledTask -TaskName 'Milo-TelegramBot' -ErrorAction SilentlyContinue
$lockPort = if ($env:MILO_BOT_LOCK_PORT) { [int]$env:MILO_BOT_LOCK_PORT } else { 47431 }
$botListening = [bool](Get-NetTCPConnection -LocalPort $lockPort -State Listen -ErrorAction SilentlyContinue)
if (-not $botTask) {
    Alert 'bot-missing' 'Milo-TelegramBot scheduled task does not exist. Re-run Install-MiloDaemons.ps1.'
} elseif ($botTask.State -ne 'Running' -or -not $botListening) {
    Log "bot not healthy (state=$($botTask.State) listening=$botListening)"
    if (Restart-Daemon 'Milo-TelegramBot') { Start-Sleep 15 }
    $botListening = [bool](Get-NetTCPConnection -LocalPort $lockPort -State Listen -ErrorAction SilentlyContinue)
    if (-not $botListening) { Alert 'bot-down' 'Telegram bot was not running and did not come back. Check milo-bot\bot.log.' }
} else { Log 'telegram bot ok' }

# 3. pipeline freshness --------------------------------------------------------
$taskFor = @{ shorts = 'Milo-ShortsPipeline'; ranking = 'Milo-RankingPipeline'; pov = 'Milo-PovPipeline' }
foreach ($p in @('shorts', 'ranking')) {
    $f = Join-Path $State "pipeline-status\$p.json"
    if (-not (Test-Path $f)) { Alert "$p-never" "$p pipeline has never recorded a run. Check the $($taskFor[$p]) task."; continue }
    $ageH = ((Get-Date) - (Get-Item $f).LastWriteTime).TotalHours
    if ($ageH -gt 26) {
        Alert "$p-stale" ("$p pipeline has not swept in {0:N1}h. Task never fired or died silently." -f $ageH)
        continue
    }
    try { $s = Get-Content $f -Raw | ConvertFrom-Json } catch { continue }
    if ($s.exit_code -ne 0) { Alert "$p-failing" "$p last sweep failed rc=$($s.exit_code) at $($s.finished). /logs $p 60" }
}

# 4. disk ----------------------------------------------------------------------
$freeGB = [math]::Round((Get-PSDrive C).Free / 1GB, 1)
if ($freeGB -lt 12) { Alert 'low-disk' "only ${freeGB}GB free on C:. Renders and downloads will start failing." }
Log "disk free ${freeGB}GB"

# 5. auth rot / quota walls ----------------------------------------------------
$pipeLogs = Get-ChildItem (Join-Path $State 'logs\pipelines') -Filter '*-latest.log' -ErrorAction SilentlyContinue
foreach ($lf in $pipeLogs) {
    $tail = Get-Content $lf.FullName -Tail 400 -ErrorAction SilentlyContinue
    if ($tail -match 'invalid_grant') {
        Alert "authrot-$($lf.BaseName)" "YouTube token rejected (invalid_grant) in $($lf.Name). Run: reauth_all_channels.bat --doctor then reauth the channel."
    }
    if ($tail -match 'quotaExceeded|dailyLimitExceeded') {
        Alert "quota-$($lf.BaseName)" "YouTube API quota wall hit in $($lf.Name). Uploads are blocked until the quota resets."
    }
}

$seen | ConvertTo-Json -Depth 3 | Set-Content -Path $seenFile -Encoding UTF8
Log 'watchdog pass complete'
