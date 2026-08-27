<#
.SYNOPSIS
  Daily pipeline digest to Telegram. Deterministic first, AI commentary second.

.DESCRIPTION
  The old pipeline-driver asked an agent to go and look at everything, then
  report. When the agent was down, or slow, or hallucinating, the daily report
  simply did not arrive - and a missing report is indistinguishable from a
  healthy quiet day.

  So this script builds the facts itself from the status files that
  Run-Pipeline.ps1 writes (always cheap, always available), sends them, and only
  then - if the opencode server is up - appends a short agent commentary. The
  numbers never depend on the AI being alive.
#>
[CmdletBinding()]
param(
    [string]$Repo = '',
    [string]$State = '',
    [switch]$NoAgent
)

$ErrorActionPreference = 'Continue'
if (-not $Repo)  { $Repo  = if ($env:MILO_REPO) { $env:MILO_REPO } else { (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path } }
if (-not $State) { $State = if ($env:MILO_HOME) { $env:MILO_HOME } else { Join-Path $env:LOCALAPPDATA 'milo' } }

$notify = Join-Path $PSScriptRoot 'Send-Telegram.ps1'
$statusDir = Join-Path $State 'pipeline-status'
$lines = @("MILO DAILY REPORT - $(Get-Date -Format 'ddd dd MMM yyyy HH:mm')", "host $env:COMPUTERNAME", "")

$anyFail = $false
foreach ($p in @('shorts', 'ranking', 'pov')) {
    $f = Join-Path $statusDir "$p.json"
    if (-not (Test-Path $f)) { $lines += "$p : NO RUN EVER RECORDED"; $anyFail = $true; continue }
    try { $s = Get-Content $f -Raw | ConvertFrom-Json } catch { $lines += "$p : status unreadable"; $anyFail = $true; continue }
    $ageH = [math]::Round(((Get-Date) - (Get-Item $f).LastWriteTime).TotalHours, 1)
    $mark = if ($s.exit_code -eq 0) { 'OK' } else { "FAIL rc=$($s.exit_code)" }
    if ($s.exit_code -ne 0 -or $ageH -gt 26) { $anyFail = $true }
    $stale = if ($ageH -gt 26) { "  <-- STALE, no sweep in ${ageH}h" } else { '' }
    $lines += "$p : $mark - $($s.finished) - $($s.duration_min)m - uploads $($s.uploads)$stale"
    if ($s.summary) { $lines += "   $($s.summary -replace "`r?`n", ' | ')" }
}

# Daemon roll-call: a pipeline that never fired reports nothing at all, so the
# task states are part of the daily facts.
$lines += "", "daemons:"
foreach ($t in @('Milo-OpencodeServer','Milo-TelegramBot','Milo-ShortsPipeline','Milo-RankingPipeline','Milo-Routines','Milo-Watchdog')) {
    $task = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    if (-not $task) { $lines += "  $t : MISSING"; $anyFail = $true; continue }
    $info = $task | Get-ScheduledTaskInfo
    $rc = if ($info.LastTaskResult -in @(0, 267009, 267011)) { '' } else { " rc=$($info.LastTaskResult)" }
    $lines += "  $t : $($task.State)$rc (last $($info.LastRunTime))"
}

$disk = Get-PSDrive C
$lines += "", ("disk C: {0} GB free" -f [math]::Round($disk.Free / 1GB, 1))
if (($disk.Free / 1GB) -lt 15) { $lines += "  LOW DISK - renders will start failing"; $anyFail = $true }

$header = if ($anyFail) { '[!] attention needed' } else { '[OK] all green' }
$lines = @($header) + $lines

# --- optional agent commentary ----------------------------------------------
if (-not $NoAgent) {
    $server = if ($env:OPENCODE_SERVER_URL) { $env:OPENCODE_SERVER_URL } else { 'http://127.0.0.1:4096' }
    try {
        $health = Invoke-RestMethod -Uri "$server/global/health" -TimeoutSec 5
        if ($health.healthy) {
            $session = Invoke-RestMethod -Uri "$server/session" -Method Post -ContentType 'application/json' `
                -Body (@{ title = "daily-report $(Get-Date -Format yyyy-MM-dd)" } | ConvertTo-Json) -TimeoutSec 20
            $prompt = @"
You are the Milo pipeline driver. Here are today's raw facts:

$($lines -join "`n")

Read the newest pipeline logs under $State\logs\pipelines if you need detail.
Reply with at most 6 lines: what actually shipped, what is broken, and the one
thing Allan should do today. No preamble, no restating the numbers.
"@
            $body = @{ agent = 'milo'; parts = @(@{ type = 'text'; text = $prompt }) } | ConvertTo-Json -Depth 5
            $reply = Invoke-RestMethod -Uri "$server/session/$($session.id)/message" -Method Post `
                -ContentType 'application/json' -Body $body -TimeoutSec 600
            $text = ($reply.parts | Where-Object { $_.type -eq 'text' } | ForEach-Object { $_.text }) -join "`n"
            if ($text) { $lines += "", "--- milo ---", $text.Trim() }
        }
    } catch {
        $lines += "", "(agent commentary skipped: $($_.Exception.Message))"
    }
}

& $notify -Text ($lines -join "`n") | Out-Null
$lines -join "`n" | Write-Host
