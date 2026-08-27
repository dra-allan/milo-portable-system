<#
.SYNOPSIS
    One-screen health check for the Milo VPS: daemons, today's runs, logs.

.DESCRIPTION
    Answers the only question that matters after a deploy - "is it actually
    posting and would I hear about it if it stopped" - without opening five
    windows. Read-only: it fixes nothing, the watchdog does that.
#>
[CmdletBinding()]
param([int]$Tail = 8)

$Here  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo  = (Resolve-Path (Join-Path $Here '..')).Path
$State = if ($env:MILO_HOME) { $env:MILO_HOME } else { Join-Path $env:LOCALAPPDATA 'milo' }

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " MILO VPS HEALTH CHECK " -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

$tasks = @(
    @{ Name = 'MiloTelegramBot';    Want = 'Running' },
    @{ Name = 'MiloOpencodeServer'; Want = 'Running' },
    @{ Name = 'MiloShortsPipeline'; Want = 'Ready'   },
    @{ Name = 'MiloRankingPipeline';Want = 'Ready'   },
    @{ Name = 'MiloDaemonWatchdog'; Want = 'Ready'   },
    @{ Name = 'MiloRoutines';       Want = 'Ready'   }
)

Write-Host "`n[1] SCHEDULED TASKS" -ForegroundColor Yellow
$healthy = $true
foreach ($entry in $tasks) {
    $task = Get-ScheduledTask -TaskName $entry.Name -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host ("  x  {0}: NOT REGISTERED" -f $entry.Name) -ForegroundColor Red
        $healthy = $false
        continue
    }
    $info = Get-ScheduledTaskInfo -TaskName $entry.Name
    $ok = ($task.State -eq $entry.Want) -or ($task.State -eq 'Running')
    if (-not $ok) { $healthy = $false }
    Write-Host ("  {0}  {1}: {2} | last {3} | rc {4} | next {5}" -f `
        $(if ($ok) { 'ok' } else { 'x ' }), $entry.Name, $task.State,
        $info.LastRunTime, $info.LastTaskResult, $info.NextRunTime) `
        -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' })
}

Write-Host "`n[2] OPENCODE SERVER" -ForegroundColor Yellow
try {
    $client = New-Object Net.Sockets.TcpClient
    $up = $client.ConnectAsync('127.0.0.1', 4096).Wait(3000) -and $client.Connected
    $client.Close()
} catch { $up = $false }
if ($up) { Write-Host "  ok  127.0.0.1:4096 accepting connections (warm attach available)" -ForegroundColor Green }
else { Write-Host "  x   127.0.0.1:4096 not answering - Telegram /do will cold start" -ForegroundColor Red; $healthy = $false }

Write-Host "`n[3] TODAY'S PIPELINE RUNS" -ForegroundColor Yellow
$today = (Get-Date).ToString('yyyy-MM-dd')
foreach ($key in @('shorts', 'ranking')) {
    $path = Join-Path $State "pipeline_runs\$key-last.json"
    if (-not (Test-Path $path)) {
        Write-Host ("  --  {0}: no run ever recorded" -f $key) -ForegroundColor DarkGray
        continue
    }
    $run = Get-Content $path -Raw | ConvertFrom-Json
    $isToday = ([string]$run.started).StartsWith($today)
    $colour = if ($run.status -eq 'ok' -and $isToday) { 'Green' } elseif ($isToday) { 'Yellow' } else { 'Red' }
    Write-Host ("  {0}: {1} | started {2} | {3} | uploads {4}" -f `
        $key, $run.status, $run.started, $run.duration, @($run.uploads).Count) -ForegroundColor $colour
    foreach ($u in @($run.uploads) | Select-Object -First 5) { Write-Host "        $u" -ForegroundColor Gray }
    foreach ($e in @($run.errors) | Select-Object -First 3) { Write-Host "        ! $e" -ForegroundColor DarkYellow }
}

Write-Host "`n[4] LOG TAILS" -ForegroundColor Yellow
$logs = @(
    (Join-Path $Repo 'milo-bot\bot.log'),
    (Join-Path $Repo 'milo-bot\opencode-server.log'),
    (Get-ChildItem (Join-Path $Repo 'artisan\youtube-shorts-pipeline\data\logs') -Filter 'daemon-*.log' -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1).FullName,
    (Get-ChildItem (Join-Path $Repo 'artisan\ranking-shorts-pipeline\data\logs') -Filter 'daemon-*.log' -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1).FullName
)
foreach ($log in $logs) {
    if (-not $log -or -not (Test-Path $log)) { continue }
    Write-Host ("--- {0} ---" -f (Split-Path -Leaf $log)) -ForegroundColor Gray
    Get-Content $log -Tail $Tail | ForEach-Object { Write-Host "  $_" }
}

Write-Host "`n[5] DISK" -ForegroundColor Yellow
$drive = Get-PSDrive -Name ((Split-Path -Qualifier $Repo).TrimEnd(':'))
Write-Host ("  {0}GB free of {1}GB" -f [math]::Round($drive.Free/1GB,1), [math]::Round(($drive.Free+$drive.Used)/1GB,1))

Write-Host "`n=============================================" -ForegroundColor Cyan
if ($healthy) { Write-Host " HEALTHY - daemons up, reporting wired " -ForegroundColor Green }
else { Write-Host " ATTENTION NEEDED - see red lines above " -ForegroundColor Red }
Write-Host "=============================================" -ForegroundColor Cyan
