$task = Get-ScheduledTask -TaskName 'MiloBot' -ErrorAction SilentlyContinue
if ($task) {
    $task.Settings.StartWhenAvailable = $true
    $task.Settings.StopIfGoingOnBatteries = $false
    $task.Settings.AllowStartIfOnBatteries = $true
    $task.Settings.ExecutionTimeLimit = 'PT0S'
    $task.Settings.MultipleInstances = [Microsoft.Win32.TaskScheduler.TaskInstancesPolicy]::IgnoreNew
    $task.Principal.LogonType = [Microsoft.Win32.TaskScheduler.TaskLogonType]::Interactive
    $task.Principal.RunLevel = [Microsoft.Win32.TaskScheduler.TaskRunLevel]::Highest
    $task | Set-ScheduledTask -ErrorAction Stop
    Write-Host 'Updated MiloBot settings'
} else {
    Write-Host 'MiloBot task not found'
}