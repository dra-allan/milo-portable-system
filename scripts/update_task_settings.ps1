<#
.SYNOPSIS
    Update Task Scheduler settings for visible pipeline tasks
#>

$taskNames = @("MiloShortsPipeline", "MiloRankingPipeline", "MiloMorningBriefing")

foreach ($n in $taskNames) {
    $task = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "Task not found: $n" -ForegroundColor Yellow
        continue
    }
    
    # Update settings
    $task.Settings.StartWhenAvailable = $true
    $task.Settings.StopIfGoingOnBatteries = $false
    $task.Settings.AllowStartIfOnBatteries = $true
    $task.Settings.ExecutionTimeLimit = "PT0S"  # Unlimited
    $task.Settings.MultipleInstances = [Microsoft.Win32.TaskScheduler.TaskInstancesPolicy]::IgnoreNew
    
    # Update principal for interactive logon
    $task.Principal.LogonType = [Microsoft.Win32.TaskScheduler.TaskLogonType]::Interactive
    $task.Principal.RunLevel = [Microsoft.Win32.TaskScheduler.TaskRunLevel]::Highest
    
    # Register updated task
    try {
        $task | Set-ScheduledTask -ErrorAction Stop
        Write-Host "Updated settings for: $n" -ForegroundColor Green
    } catch {
        $err = $error[0].ToString()
        Write-Host ("Failed to update " + $n + ": " + $err) -ForegroundColor Red
    }
}

Write-Host "`nVerifying updated tasks..."
Get-ScheduledTask -TaskName $taskNames -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host ("Task: " + $_.TaskName)
    Write-Host ("  StartWhenAvailable: " + $_.Settings.StartWhenAvailable)
    Write-Host ("  ExecutionTimeLimit: " + $_.Settings.ExecutionTimeLimit)
    Write-Host ("  MultipleInstances: " + $_.Settings.MultipleInstances)
    Write-Host ("  LogonType: " + $_.Principal.LogonType)
    Write-Host ("  RunLevel: " + $_.Principal.RunLevel)
    Write-Host ""
}