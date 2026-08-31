# register_interactive_tasks.ps1 — Register Interactive OpenCode Visible Scheduled Tasks
$Principal = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType Interactive -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)

# 1. Morning Briefing (Daily at 07:00 AM)
$ActionMB = New-ScheduledTaskAction -Execute "cmd.exe" -Argument '/c start "" "C:\milo-portable-system\scripts\launchers\run_opencode_morning_brief.bat"' -WorkingDirectory "C:\milo-portable-system\scripts\launchers"
$TriggerMB = New-ScheduledTaskTrigger -Daily -At "07:00AM"
Unregister-ScheduledTask -TaskName "MiloMorningBrief" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName "MiloMorningBrief" -Action $ActionMB -Trigger $TriggerMB -Principal $Principal -Settings $Settings

# 2. YouTube Shorts Pipeline (Daily at 09:00 AM, 02:00 PM, 07:00 PM)
$ActionYT = New-ScheduledTaskAction -Execute "cmd.exe" -Argument '/c start "" "C:\milo-portable-system\scripts\launchers\run_opencode_youtube_shorts.bat"' -WorkingDirectory "C:\milo-portable-system\scripts\launchers"
$TriggerYT1 = New-ScheduledTaskTrigger -Daily -At "09:00AM"
$TriggerYT2 = New-ScheduledTaskTrigger -Daily -At "02:00PM"
$TriggerYT3 = New-ScheduledTaskTrigger -Daily -At "07:00PM"
Unregister-ScheduledTask -TaskName "MiloYouTubeShorts" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName "MiloYouTubeShorts" -Action $ActionYT -Trigger @($TriggerYT1, $TriggerYT2, $TriggerYT3) -Principal $Principal -Settings $Settings

# 3. Ranking Shorts Pipeline (Daily at 10:00 AM, 04:00 PM)
$ActionRS = New-ScheduledTaskAction -Execute "cmd.exe" -Argument '/c start "" "C:\milo-portable-system\scripts\launchers\run_opencode_ranking_shorts.bat"' -WorkingDirectory "C:\milo-portable-system\scripts\launchers"
$TriggerRS1 = New-ScheduledTaskTrigger -Daily -At "10:00AM"
$TriggerRS2 = New-ScheduledTaskTrigger -Daily -At "04:00PM"
Unregister-ScheduledTask -TaskName "MiloRankingShorts" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName "MiloRankingShorts" -Action $ActionRS -Trigger @($TriggerRS1, $TriggerRS2) -Principal $Principal -Settings $Settings

Write-Host "All physical visible terminal scheduled tasks registered successfully!"
