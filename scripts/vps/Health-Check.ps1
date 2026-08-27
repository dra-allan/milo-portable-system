<#
.SYNOPSIS
  One-screen truth about the Milo daemons. Uses the canonical task names.

.DESCRIPTION
  Supersedes scripts/verify_daemons.ps1, which checked task names that no longer
  existed and therefore reported healthy nothing. Add -Notify to push the same
  summary to Telegram.
#>
[CmdletBinding()]
param([string]$State = '', [switch]$Notify)

$ErrorActionPreference = 'Continue'
if (-not $State) { $State = if ($env:MILO_HOME) { $env:MILO_HOME } else { Join-Path $env:LOCALAPPDATA 'milo' } }

$tasks = @('Milo-OpencodeServer','Milo-TelegramBot','Milo-ShortsPipeline','Milo-RankingPipeline',
           'Milo-PipelineDriver','Milo-Routines','Milo-Watchdog')
$out = @('=== MILO HEALTH ===', "host $env:COMPUTERNAME  $(Get-Date -Format 'yyyy-MM-dd HH:mm')", '', 'TASKS')
$bad = 0
foreach ($t in $tasks) {
    $task = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    if (-not $task) { $out += "  MISSING   $t"; $bad++; continue }
    $i = $task | Get-ScheduledTaskInfo
    $rc = $i.LastTaskResult
    $flag = if ($rc -in @(0, 267009, 267011, $null)) { '' } else { "  rc=$rc" }
    if ($flag) { $bad++ }
    $out += ("  {0,-9} {1,-22} last {2}  next {3}{4}" -f $task.State, $t, $i.LastRunTime, $i.NextRunTime, $flag)
}

$out += '', 'OPENCODE SERVER'
$server = if ($env:OPENCODE_SERVER_URL) { $env:OPENCODE_SERVER_URL } else { 'http://127.0.0.1:4096' }
try {
    $h = Invoke-RestMethod -Uri "$server/global/health" -TimeoutSec 6
    $out += "  ok - version $($h.version) at $server"
} catch { $out += "  DOWN at $server - $($_.Exception.Message)"; $bad++ }

$out += '', 'TELEGRAM BOT'
$port = if ($env:MILO_BOT_LOCK_PORT) { [int]$env:MILO_BOT_LOCK_PORT } else { 47431 }
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    $out += "  ok - holding single-instance lock on 127.0.0.1:$port"
} else { $out += '  not running (no lock held)'; $bad++ }

$out += '', 'LAST SWEEPS'
foreach ($p in @('shorts','ranking','pov')) {
    $f = Join-Path $State "pipeline-status\$p.json"
    if (-not (Test-Path $f)) { $out += "  $p : never run"; continue }
    $s = Get-Content $f -Raw | ConvertFrom-Json
    $ageH = [math]::Round(((Get-Date) - (Get-Item $f).LastWriteTime).TotalHours, 1)
    $out += "  $p : rc=$($s.exit_code) uploads=$($s.uploads) $($s.duration_min)m  ${ageH}h ago"
    if ($s.exit_code -ne 0 -or $ageH -gt 26) { $bad++ }
}

$disk = Get-PSDrive C
$out += '', ("DISK C: {0} GB free of {1} GB" -f [math]::Round($disk.Free/1GB,1), [math]::Round(($disk.Free+$disk.Used)/1GB,1))
$out += '', $(if ($bad -eq 0) { 'ALL GREEN' } else { "$bad PROBLEM(S) - see above" })

$out | ForEach-Object { Write-Host $_ }
if ($Notify) { & (Join-Path $PSScriptRoot 'Send-Telegram.ps1') -Text ($out -join "`n") | Out-Null }
exit $(if ($bad -eq 0) { 0 } else { 1 })
