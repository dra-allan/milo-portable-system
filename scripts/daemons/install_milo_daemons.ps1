<#
.SYNOPSIS
    Register Milo's VPS daemons in Task Scheduler: pipelines, Telegram bot,
    warm opencode server and a watchdog.

.DESCRIPTION
    Everything is registered from XML rather than the schtasks CLI, because the
    CLI cannot express the three settings that decide whether these tasks
    actually survive a Windows Server VPS:

      * LogonType S4U      - runs whether or not anyone is logged on, and needs
                             no stored password. The old tasks were "onlogon",
                             so closing RDP was enough to stop posting.
      * ExecutionTimeLimit - PT0S (unlimited) for the two long-lived daemons,
                             6h for a pipeline sweep. The default 72h cap and
                             the default 3-day kill are both wrong here.
      * RestartOnFailure   - 999 retries, 1 minute apart, so a crashed bot is
                             back before Allan notices.

    Idempotent: re-running replaces every task in place.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install_milo_daemons.ps1
    powershell -ExecutionPolicy Bypass -File .\install_milo_daemons.ps1 -Status
    powershell -ExecutionPolicy Bypass -File .\install_milo_daemons.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string]$RunAsUser  = "$env:USERDOMAIN\$env:USERNAME",
    [string]$ShortsTime  = "08:45",
    [string]$RankingTime = "09:15",
    [int]$WatchdogMinutes = 10,
    [switch]$Status,
    [switch]$Uninstall,
    [switch]$SkipStart
)

$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = (Resolve-Path (Join-Path $Here '..\..')).Path

$TaskNames = @(
    'MiloOpencodeServer',
    'MiloTelegramBot',
    'MiloShortsPipeline',
    'MiloRankingPipeline',
    'MiloDaemonWatchdog'
)

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this in an elevated PowerShell (Task Scheduler registration needs admin)."
    }
}

function New-TaskXml {
    param(
        [string]$Description,
        [string]$Command,
        [string]$Arguments = '',
        [string]$Triggers,
        [string]$TimeLimit = 'PT6H',
        [switch]$RestartForever
    )
    # Element order inside <Settings> is a schema sequence, not a bag: move one
    # element and schtasks rejects the whole file as "incorrectly formatted".
    # This is the order Windows itself exports.
    $restart = ''
    if ($RestartForever) {
        $restart = "    <RestartOnFailure>`n      <Interval>PT1M</Interval>`n      <Count>999</Count>`n    </RestartOnFailure>"
    }
    @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>milo</Author>
    <Description>$Description</Description>
  </RegistrationInfo>
  <Triggers>
$Triggers
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$RunAsUser</UserId>
      <LogonType>S4U</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>$TimeLimit</ExecutionTimeLimit>
    <Priority>6</Priority>
$restart
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$Command</Command>
      <Arguments>$Arguments</Arguments>
      <WorkingDirectory>$Repo</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
}

function Register-Task {
    param([string]$Name, [string]$Xml)
    $tmp = Join-Path $env:TEMP "$Name.xml"
    # Task Scheduler only accepts the XML as UTF-16.
    [System.IO.File]::WriteAllText($tmp, $Xml, [System.Text.Encoding]::Unicode)
    schtasks /Delete /TN $Name /F 2>$null | Out-Null
    $out = schtasks /Create /TN $Name /XML $tmp /F 2>&1
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) {
        Write-Host ("  [FAIL] {0}: {1}" -f $Name, ($out -join ' ')) -ForegroundColor Red
        return $false
    }
    Write-Host ("  [ok]   {0}" -f $Name) -ForegroundColor Green
    return $true
}

function Show-Status {
    Write-Host "`nMILO DAEMONS" -ForegroundColor Cyan
    foreach ($name in $TaskNames + @('MiloRoutines')) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-Host ("  [--]   {0}: not registered" -f $name) -ForegroundColor DarkGray
            continue
        }
        $info = Get-ScheduledTaskInfo -TaskName $name
        $colour = if ($task.State -eq 'Running' -or $task.State -eq 'Ready') { 'Green' } else { 'Red' }
        Write-Host ("  [{0}] {1}  last={2}  rc={3}  next={4}" -f `
            $task.State, $name, $info.LastRunTime, $info.LastTaskResult, $info.NextRunTime) -ForegroundColor $colour
    }
    Write-Host ""
}

if ($Status) { Show-Status; return }

Assert-Admin

if ($Uninstall) {
    Write-Host "Removing Milo daemons..." -ForegroundColor Yellow
    foreach ($name in $TaskNames) {
        schtasks /Delete /TN $name /F 2>$null | Out-Null
        Write-Host ("  removed {0}" -f $name)
    }
    Show-Status
    return
}

$bootTrigger = @"
    <BootTrigger>
      <Enabled>true</Enabled>
      <Delay>PT1M</Delay>
    </BootTrigger>
"@

$logonTrigger = @"
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT30S</Delay>
    </LogonTrigger>
"@

function New-DailyTrigger([string]$Time) {
    $stamp = (Get-Date).ToString('yyyy-MM-dd') + 'T' + $Time + ':00'
    @"
    <CalendarTrigger>
      <StartBoundary>$stamp</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
"@
}

$watchTrigger = @"
    <TimeTrigger>
      <StartBoundary>$((Get-Date).AddMinutes(2).ToString('yyyy-MM-ddTHH:mm:ss'))</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT$($WatchdogMinutes)M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
"@

$cmd = "$env:SystemRoot\System32\cmd.exe"
$psh = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

Write-Host "Registering Milo daemons (run-as: $RunAsUser)" -ForegroundColor Cyan

Register-Task 'MiloOpencodeServer' (New-TaskXml `
    -Description 'Warm opencode server so Telegram turns attach instead of cold-booting MCP.' `
    -Command $cmd -Arguments ('/c "' + (Join-Path $Here 'start_opencode_server.cmd') + '"') `
    -Triggers $bootTrigger -TimeLimit 'PT0S' -RestartForever) | Out-Null

Register-Task 'MiloTelegramBot' (New-TaskXml `
    -Description 'Milo Telegram bot: notifications, remote control, chat.' `
    -Command $cmd -Arguments ('/c "' + (Join-Path $Here 'start_telegram_bot.cmd') + '"') `
    -Triggers ($bootTrigger + "`n" + $logonTrigger) -TimeLimit 'PT0S' -RestartForever) | Out-Null

Register-Task 'MiloShortsPipeline' (New-TaskXml `
    -Description 'Daily YouTube Shorts sweep + upload, reports to Telegram.' `
    -Command $cmd -Arguments ('/c "' + (Join-Path $Here 'run_pipeline.cmd') + '" shorts') `
    -Triggers (New-DailyTrigger $ShortsTime) -TimeLimit 'PT6H') | Out-Null

Register-Task 'MiloRankingPipeline' (New-TaskXml `
    -Description 'Daily ranking shorts sweep + upload, reports to Telegram.' `
    -Command $cmd -Arguments ('/c "' + (Join-Path $Here 'run_pipeline.cmd') + '" ranking') `
    -Triggers (New-DailyTrigger $RankingTime) -TimeLimit 'PT6H') | Out-Null

Register-Task 'MiloDaemonWatchdog' (New-TaskXml `
    -Description 'Restarts dead Milo daemons and alerts on missed pipeline runs.' `
    -Command $psh -Arguments ('-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $Here 'watchdog.ps1') + '"') `
    -Triggers $watchTrigger -TimeLimit 'PT10M') | Out-Null

if (-not $SkipStart) {
    Write-Host "`nStarting the long-lived daemons now..." -ForegroundColor Cyan
    foreach ($name in @('MiloOpencodeServer', 'MiloTelegramBot')) {
        schtasks /Run /TN $name | Out-Null
        Start-Sleep -Seconds 3
    }
}

Show-Status
Write-Host "Pipelines: shorts $ShortsTime, ranking $RankingTime (daily, whether or not you are logged in)." -ForegroundColor Green
Write-Host "Sanity check from Telegram: /status then /pipelines" -ForegroundColor Green
