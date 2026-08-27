<#
.SYNOPSIS
  Install the full Milo daemon set on a Windows VPS. Idempotent.

.DESCRIPTION
  One script, one canonical naming scheme, no duplicates. It registers:

    Milo-OpencodeServer   AtStartup + AtLogOn, restarts   `opencode serve` kept warm
    Milo-TelegramBot      AtStartup + AtLogOn, restarts   the control plane
    Milo-ShortsPipeline   daily (default 08:45)           Run-Pipeline.ps1 -Pipeline shorts
    Milo-RankingPipeline  daily (default 09:15)           Run-Pipeline.ps1 -Pipeline ranking
    Milo-PipelineDriver   daily (default 09:40)           Send-Report.ps1 (daily digest)
    Milo-Routines         every 5 minutes                 miloctl routines tick
    Milo-Watchdog         every 10 minutes                Watchdog.ps1

  It also unregisters the legacy tasks. That matters more than it sounds: the
  old "Ranking Shorts Pipeline Daemon" (AtStartup, --mode schedule) and
  "MiloRankingPipeline" (daily) were both installed, so two schedulers raced the
  same workspace and the same daily upload cap. Duplicate names are why "it ran
  but nothing posted" was a coin flip.

.PARAMETER RunAsUser
  Account the daemons run as. Must be the account that holds the opencode auth
  (~\.local\share\opencode\auth.json) and the YouTube tokens. SYSTEM is
  deliberately NOT the default: with SYSTEM the profile is wrong and every OAuth
  token lookup silently misses.

.PARAMETER UseStoredPassword
  Prompt for the account password and store it in the task (LogonType Password).
  Use this if S4U tasks fail to load the user profile on your box.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\Install-MiloDaemons.ps1 -Verbose
  powershell -ExecutionPolicy Bypass -File .\Install-MiloDaemons.ps1 -ShortsAt 07:30 -RankingAt 08:10
#>
[CmdletBinding()]
param(
    [string]$Repo = '',
    [string]$State = '',
    [string]$RunAsUser = "$env:USERDOMAIN\$env:USERNAME",
    [switch]$UseStoredPassword,
    [string]$ShortsAt = '08:45',
    [string]$RankingAt = '09:15',
    [string]$DriverAt = '09:40',
    [switch]$SkipPov
)

$ErrorActionPreference = 'Stop'
if (-not $Repo)  { $Repo  = if ($env:MILO_REPO) { $env:MILO_REPO } else { (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path } }
if (-not $State) { $State = if ($env:MILO_HOME) { $env:MILO_HOME } else { Join-Path $env:LOCALAPPDATA 'milo' } }

$vps = $PSScriptRoot
$ps  = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

Write-Host "repo : $Repo"
Write-Host "state: $State"
Write-Host "user : $RunAsUser"
Write-Host ''

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this in an elevated PowerShell (Run as Administrator).'
}

New-Item -ItemType Directory -Force -Path (Join-Path $State 'logs\pipelines') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $State 'pipeline-status') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $State 'locks') | Out-Null

# --- principal ---------------------------------------------------------------
$cred = $null
if ($UseStoredPassword) {
    $cred = Get-Credential -UserName $RunAsUser -Message 'Password for the Milo daemon account'
    $principal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType Password -RunLevel Highest
} else {
    # S4U: "run whether logged on or not" with no stored password. Keeps the
    # user profile (and therefore every token path) correct.
    $principal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType S4U -RunLevel Highest
}

function Register-Milo {
    param(
        [string]$Name,
        [string]$Execute,
        [string]$Arguments,
        [string]$WorkDir,
        $Triggers,
        [string]$Description,
        [switch]$LongRunning
    )
    $action = New-ScheduledTaskAction -Execute $Execute -Argument $Arguments -WorkingDirectory $WorkDir
    $settings = if ($LongRunning) {
        New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries `
            -AllowStartIfOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -Hidden
    } else {
        New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries `
            -AllowStartIfOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -Hidden
    }
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    # NB: not $args - that is an automatic variable and clobbering it bites later.
    $reg = @{
        TaskName = $Name; Action = $action; Trigger = $Triggers
        Settings = $settings; Description = $Description
    }
    if ($cred) {
        Register-ScheduledTask @reg -User $cred.UserName `
            -Password ($cred.GetNetworkCredential().Password) -RunLevel Highest | Out-Null
    } else {
        Register-ScheduledTask @reg -Principal $principal | Out-Null
    }
    Write-Host "  [+] $Name"
}

function New-RepeatingTrigger([int]$Minutes) {
    $t = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes $Minutes)
    try { $t.Repetition.Duration = 'P3650D' } catch { }
    return $t
}

# --- kill the legacy zoo ------------------------------------------------------
Write-Host 'removing legacy / duplicate tasks'
$legacy = @(
    'MiloTelegramBot', 'Milo-TelegramBot-Old', 'MiloBot',
    'YouTube Shorts Pipeline Daemon', 'Ranking Shorts Pipeline Daemon',
    'MiloShortsPipeline', 'MiloRankingPipeline', 'MiloPipelineDriver', 'MiloRoutines',
    'POV Pipeline Daemon', 'MiloPovPipeline'
)
foreach ($t in $legacy) {
    if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $t -Confirm:$false
        Write-Host "  [-] $t"
    }
}

# --- opencode server ----------------------------------------------------------
Write-Host 'registering daemons'
$oc = Get-Command opencode -ErrorAction SilentlyContinue
if (-not $oc) { throw 'opencode is not on PATH for this account. Install it, or add it, then re-run.' }
$ocPath = $oc.Source
$ocPort = if ($env:OPENCODE_PORT) { $env:OPENCODE_PORT } else { '4096' }
if ($ocPath -match '\.(cmd|bat)$') {
    $ocExec = "$env:SystemRoot\System32\cmd.exe"
    $ocArgs = "/c `"$ocPath`" serve --port $ocPort --hostname 127.0.0.1"
} else {
    $ocExec = $ocPath
    $ocArgs = "serve --port $ocPort --hostname 127.0.0.1"
}
Register-Milo -Name 'Milo-OpencodeServer' -Execute $ocExec -Arguments $ocArgs -WorkDir $Repo `
    -Triggers @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn)) `
    -Description 'Warm opencode HTTP server. The Telegram agent path lives on this.' -LongRunning

# --- telegram bot -------------------------------------------------------------
$botDir = Join-Path $Repo 'milo-bot'
$botPy = Join-Path $botDir 'venv\Scripts\python.exe'
if (-not (Test-Path $botPy)) {
    Write-Warning "no venv at $botPy - creating one"
    python -m venv (Join-Path $botDir 'venv')
    & $botPy -m pip install --upgrade pip -q
    & $botPy -m pip install -q -r (Join-Path $botDir 'requirements.txt')
}
Register-Milo -Name 'Milo-TelegramBot' -Execute $botPy `
    -Arguments ('"' + (Join-Path $botDir 'src\bot.py') + '"') -WorkDir $botDir `
    -Triggers @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn)) `
    -Description 'Milo Telegram bot: notifications + remote command plane.' -LongRunning

# --- pipelines ----------------------------------------------------------------
Register-Milo -Name 'Milo-ShortsPipeline' -Execute $ps `
    -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$vps\Run-Pipeline.ps1`" -Pipeline shorts" `
    -WorkDir $vps -Triggers @((New-ScheduledTaskTrigger -Daily -At $ShortsAt)) `
    -Description 'Daily YouTube Shorts sweep + post, reports to Telegram.'

Register-Milo -Name 'Milo-RankingPipeline' -Execute $ps `
    -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$vps\Run-Pipeline.ps1`" -Pipeline ranking" `
    -WorkDir $vps -Triggers @((New-ScheduledTaskTrigger -Daily -At $RankingAt)) `
    -Description 'Daily Ranking Shorts sweep + post, reports to Telegram.'

if (-not $SkipPov -and (Test-Path (Join-Path $Repo 'artisan\pov_pipeline\run_pov_pipeline.py'))) {
    Register-Milo -Name 'Milo-PovPipeline' -Execute $ps `
        -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$vps\Run-Pipeline.ps1`" -Pipeline pov" `
        -WorkDir $vps -Triggers @((New-ScheduledTaskTrigger -Daily -At '10:10')) `
        -Description 'Daily POV pipeline sweep, reports to Telegram.'
}

# --- report + routines + watchdog --------------------------------------------
Register-Milo -Name 'Milo-PipelineDriver' -Execute $ps `
    -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$vps\Send-Report.ps1`"" `
    -WorkDir $vps -Triggers @((New-ScheduledTaskTrigger -Daily -At $DriverAt)) `
    -Description 'Daily digest of every pipeline to Telegram.'

$miloPy = Get-Command python -ErrorAction SilentlyContinue
if ($miloPy) {
    Register-Milo -Name 'Milo-Routines' -Execute $miloPy.Source `
        -Arguments '-m miloctl.cli --quiet routines tick' -WorkDir $Repo `
        -Triggers @((New-RepeatingTrigger 5)) `
        -Description 'Milo routine tick: backups, vault sync, memory hygiene.'
} else {
    Write-Warning 'python not on PATH; skipped Milo-Routines'
}

Register-Milo -Name 'Milo-Watchdog' -Execute $ps `
    -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$vps\Watchdog.ps1`"" `
    -WorkDir $vps -Triggers @((New-RepeatingTrigger 10)) `
    -Description 'Restarts dead Milo daemons and alerts on stale pipelines.'

# --- start the long-running ones now -----------------------------------------
foreach ($t in @('Milo-OpencodeServer', 'Milo-TelegramBot')) {
    try { Start-ScheduledTask -TaskName $t; Write-Host "  [>] started $t" } catch { Write-Warning "could not start ${t}: $($_.Exception.Message)" }
}

Write-Host ''
Write-Host 'Installed. Verify with:' -ForegroundColor Green
Write-Host "  powershell -File `"$vps\Health-Check.ps1`""
Write-Host '  then message the bot: /ping  and  /status'
