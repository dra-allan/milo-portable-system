<#
.SYNOPSIS
    Keep Milo's daemons honest: restart what died, shout about what silently
    did not happen.

.DESCRIPTION
    Runs every 10 minutes from the MiloDaemonWatchdog task. Four checks:

      1. The two long-lived daemons (bot, opencode server) are in state
         Running. A crash loop that exhausted its restarts leaves the task
         'Ready', which looks healthy in every UI and answers no messages.
      2. The opencode server port actually accepts connections. The process can
         be alive and the socket dead after a network stack reset.
      3. Today's pipeline runs happened. This is the check that matters most:
         a task can report success while the pipeline uploaded nothing, and a
         task that never fired reports nothing at all.
      4. Disk headroom. Rendering fills a t3.small fast, and every failure
         downstream of a full disk looks like something else.

    Alerts are deduplicated for 6 hours per message so a broken thing does not
    send 144 identical Telegram messages a day.
#>
[CmdletBinding()]
param(
    [int]$Port = 4096,
    [int]$MinFreeGB = 5,
    [switch]$Quiet
)

$ErrorActionPreference = 'Continue'
$Here  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo  = (Resolve-Path (Join-Path $Here '..\..')).Path
$State = if ($env:MILO_HOME) { $env:MILO_HOME } else { Join-Path $env:LOCALAPPDATA 'milo' }

function Get-DotEnv {
    $values = @{}
    foreach ($path in @((Join-Path $State '.env'), (Join-Path $Repo '.env'),
                        (Join-Path $Repo 'milo-bot\.env'))) {
        if (-not (Test-Path $path)) { continue }
        foreach ($line in Get-Content $path -ErrorAction SilentlyContinue) {
            $line = $line.Trim()
            if (-not $line -or $line.StartsWith('#') -or ($line -notmatch '=')) { continue }
            $key, $value = $line.Split('=', 2)
            $key = $key.Trim(); $value = $value.Trim().Trim('"').Trim("'")
            if ($key -and -not $values.ContainsKey($key)) { $values[$key] = $value }
        }
    }
    return $values
}

$env_ = Get-DotEnv
$token = if ($env:TELEGRAM_BOT_TOKEN) { $env:TELEGRAM_BOT_TOKEN } else { $env_['TELEGRAM_BOT_TOKEN'] }
$chat  = if ($env:TELEGRAM_CHAT_ID)   { $env:TELEGRAM_CHAT_ID }   else { $env_['TELEGRAM_CHAT_ID'] }

$alertFile = Join-Path $State 'watchdog-alerts.json'
$sent = @{}
if (Test-Path $alertFile) {
    try { (Get-Content $alertFile -Raw | ConvertFrom-Json).PSObject.Properties |
            ForEach-Object { $sent[$_.Name] = [datetime]$_.Value } } catch { $sent = @{} }
}

function Send-Alert([string]$Message) {
    Write-Host "[alert] $Message"
    $key = ($Message -replace '[^a-zA-Z ]', '').Substring(0, [Math]::Min(60, ($Message -replace '[^a-zA-Z ]', '').Length))
    if ($sent.ContainsKey($key) -and ((Get-Date) - $sent[$key]).TotalHours -lt 6) { return }
    $sent[$key] = Get-Date
    if (-not $token -or -not $chat) { Write-Host '[warn] no telegram creds; alert not sent'; return }
    try {
        Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$token/sendMessage" `
            -ContentType 'application/json' `
            -Body (@{ chat_id = $chat; text = "[watchdog] $Message"; disable_web_page_preview = $true } | ConvertTo-Json) `
            -TimeoutSec 30 | Out-Null
    } catch { Write-Host "[warn] telegram send failed: $($_.Exception.Message)" }
}

# 1 + 2 - long-lived daemons
foreach ($name in @('MiloTelegramBot', 'MiloOpencodeServer')) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) { Send-Alert "$name is not registered. Run install_milo_daemons.ps1."; continue }
    if ($task.State -ne 'Running') {
        Send-Alert "$name was $($task.State) - restarting it."
        schtasks /Run /TN $name 2>$null | Out-Null
        Start-Sleep -Seconds 5
    }
}

$listening = $false
try {
    $client = New-Object Net.Sockets.TcpClient
    $listening = $client.ConnectAsync('127.0.0.1', $Port).Wait(3000) -and $client.Connected
    $client.Close()
} catch { $listening = $false }
if (-not $listening) {
    Send-Alert "opencode server not answering on 127.0.0.1:$Port - bouncing MiloOpencodeServer (Telegram falls back to cold starts meanwhile)."
    schtasks /End /TN MiloOpencodeServer 2>$null | Out-Null
    Start-Sleep -Seconds 3
    schtasks /Run /TN MiloOpencodeServer 2>$null | Out-Null
}

# 3 - did today's sweeps actually happen?
$today = (Get-Date).ToString('yyyy-MM-dd')
$deadlines = @{ shorts = 12; ranking = 13 }   # hour by which a daily run must exist
foreach ($key in $deadlines.Keys) {
    $summaryPath = Join-Path $State "pipeline_runs\$key-last.json"
    $ran = $false
    $status = 'never'
    if (Test-Path $summaryPath) {
        try {
            $summary = Get-Content $summaryPath -Raw | ConvertFrom-Json
            $ran = ([string]$summary.started).StartsWith($today)
            $status = $summary.status
        } catch { $ran = $false }
    }
    if (-not $ran -and (Get-Date).Hour -ge $deadlines[$key]) {
        Send-Alert "$key pipeline has no run recorded for $today (last status: $status). Check MiloShortsPipeline/MiloRankingPipeline history, or fire it with /run $key."
    } elseif ($ran -and $status -ne 'ok') {
        Send-Alert "$key pipeline finished today with status '$status'. Tail it with /logs $key 40."
    }
}

# 4 - disk
try {
    $drive = Get-PSDrive -Name ((Split-Path -Qualifier $Repo).TrimEnd(':')) -ErrorAction Stop
    $freeGB = [math]::Round($drive.Free / 1GB, 1)
    if ($freeGB -lt $MinFreeGB) {
        Send-Alert "Only $freeGB GB free on $($drive.Name): - renders will start failing. Run cleanup_runtime.py / cleanup_uploaded.py."
    }
} catch { }

# Persist the dedupe window so the next pass does not repeat today's alerts.
try {
    New-Item -ItemType Directory -Force -Path $State | Out-Null
    $flat = @{}
    foreach ($k in $sent.Keys) { $flat[$k] = $sent[$k].ToString('o') }
    $flat | ConvertTo-Json | Set-Content -Path $alertFile -Encoding UTF8
} catch { }

if (-not $Quiet) { Write-Host "watchdog pass complete $(Get-Date -Format 'HH:mm:ss')" }
