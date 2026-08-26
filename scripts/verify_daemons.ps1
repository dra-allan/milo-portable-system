# Verification script for Milo VPS 24/7 Daemons
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " MILO VPS DAEMON HEALTH CHECK " -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

$tasks = @(
    "MiloTelegramBot",
    "YouTube Shorts Pipeline Daemon",
    "Ranking Shorts Pipeline Daemon"
)

Write-Host "`n[1] SCHEDULED TASKS STATUS:" -ForegroundColor Yellow
$allRunning = $true
foreach ($t in $tasks) {
    $task = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    if ($task) {
        $state = $task.State
        $color = if ($state -eq "Running") { "Green" } else { "Red"; $allRunning = $false }
        Write-Host "  - $t : $state" -ForegroundColor $color
    } else {
        Write-Host "  - $t : NOT FOUND" -ForegroundColor Red
        $allRunning = $false
    }
}

Write-Host "`n[2] PROCESSES STATUS:" -ForegroundColor Yellow
$pyProcesses = Get-Process python*, pythonw* -ErrorAction SilentlyContinue | Format-Table Id, ProcessName, Path, StartTime -AutoSize | Out-String
Write-Host $pyProcesses -ForegroundColor Green

Write-Host "`n[3] LOG ACTIVITY:" -ForegroundColor Yellow

# Shorts Log
$shortsLog = "C:\milo-portable-system\artisan\youtube-shorts-pipeline\data\logs\pipeline.log"
if (Test-Path $shortsLog) {
    Write-Host "--- YouTube Shorts Pipeline Log (Tail 5) ---" -ForegroundColor Gray
    Get-Content $shortsLog -Tail 5 | ForEach-Object { Write-Host "  $_" }
}

# Ranking Log
$rankingLog = "C:\milo-portable-system\artisan\ranking-shorts-pipeline\data\logs\ranking.log"
if (Test-Path $rankingLog) {
    Write-Host "`n--- Ranking Shorts Pipeline Log (Tail 5) ---" -ForegroundColor Gray
    Get-Content $rankingLog -Tail 5 | ForEach-Object { Write-Host "  $_" }
}

# Bot Log
$botLog = "C:\milo-portable-system\milo-bot\bot.log"
if (Test-Path $botLog) {
    Write-Host "`n--- Milo Telegram Bot Log (Tail 5) ---" -ForegroundColor Gray
    Get-Content $botLog -Tail 5 | ForEach-Object { Write-Host "  $_" }
}

Write-Host "`n=============================================" -ForegroundColor Cyan
if ($allRunning) {
    Write-Host " ALL DAEMONS ARE ACTIVE AND HEALTHY (24/7) " -ForegroundColor Green
} else {
    Write-Host " WARNING: ONE OR MORE DAEMONS NOT RUNNING " -ForegroundColor Red
}
Write-Host "=============================================" -ForegroundColor Cyan
