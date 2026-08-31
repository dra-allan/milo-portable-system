<# 
.SYNOPSIS
    Register Task Scheduler tasks for visible OpenCode pipeline sessions

.DESCRIPTION
    Creates three scheduled tasks that spawn visible Windows Terminal/cmd windows
    running OpenCode with Milo to execute the pipelines and morning briefing.
    Replaces the headless MiloRoutines approach.
#>

param(
    [string]$RepoDir = "C:\milo-portable-system",
    [string]$ScriptsDir = "C:\milo-portable-system\scripts"
)

# Requires admin for some task settings
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning "Not running as Administrator. Some task settings may not apply."
}

$scripts = @{
    "MiloShortsPipeline"     = "$ScriptsDir\run_shorts_pipeline_visible.bat"
    "MiloRankingPipeline"    = "$ScriptsDir\run_ranking_pipeline_visible.bat"
    "MiloMorningBriefing"    = "$ScriptsDir\run_morning_briefing_visible.bat"
}

$times = @{
    "MiloShortsPipeline"     = "08:45"
    "MiloRankingPipeline"    = "08:49"
    "MiloMorningBriefing"    = "07:00"
}

foreach ($name in $scripts.Keys) {
    $scriptPath = $scripts[$name]
    $runTime = $times[$name]
    
    if (-not (Test-Path $scriptPath)) {
        Write-Error "Script not found: $scriptPath"
        continue
    }
    
    # Delete existing task if present
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Removing existing task: $name"
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
    
    # Create action: run the bat file
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$scriptPath`""
    
    # Trigger: daily at specified time
    $trigger = New-ScheduledTaskTrigger -Daily -At $runTime
    
    # Settings: run whether user is logged on or not, but with visible window
    # RunLevel Highest for admin rights if needed
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
        -MultipleInstances IgnoreNew
    
    # Principal: run as current user (for visible window), highest privileges
    $principal = New-ScheduledTaskPrincipal -UserId (whoami) -LogonType Interactive -RunLevel Highest
    
    # Register the task
    try {
        Register-ScheduledTask `
            -TaskName $name `
            -Action $action `
            -Trigger $trigger `
            -Settings $settings `
            -Principal $principal `
            -Description "Visible OpenCode session for $name - runs at $runTime daily" `
            -Force
        Write-Host "Registered task: $name (daily at $runTime)"
    } catch {
        $errMsg = $error[0].ToString()
        Write-Host "Failed to register $name: $errMsg" -ForegroundColor Red
    }
}

Write-Host "`nAll tasks registered. Verifying..."
Get-ScheduledTask -TaskName "MiloShortsPipeline", "MiloRankingPipeline", "MiloMorningBriefing" -ErrorAction SilentlyContinue | Format-Table TaskName, State, @{Name="NextRun";Expression={$_.Triggers[0].StartBoundary}}, @{Name="Actions";Expression={$_.Actions.Execute + " " + $_.Actions.Arguments}}