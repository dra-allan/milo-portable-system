@echo off
title YouTube Shorts Pipeline - Easy Runner

:main
cls
echo.
echo ================================================================
echo           YouTube Shorts Pipeline - Easy Reader
echo ================================================================
echo.
echo 1. Run in Test Mode (check components)
echo 2. Process a YouTube URL/Video ID
echo 3. Run in Scheduled Mode (9AM, 2PM, 7PM daily)
echo 4. Process from Library (downloaded videos)
echo 5. Exit
echo.
set /p choice="Select an option (1-5): "

if "%choice%"=="1" goto test
if "%choice%"=="2" goto url
if "%choice%"=="3" goto schedule
if "%choice%"=="4" goto library
if "%choice%"=="5" goto exit

echo Invalid choice! Please try again.
timeout /t 2 > nul
goto main

:test
cls
echo.
echo Running in Test Mode...
echo.
call venv\Scripts\activate
python -m src.main --mode test
pause
goto main

:url
cls
echo.
echo Process YouTube URL/Video ID
echo.
set /p url="Enter YouTube URL or Video ID: "
if "%url%"=="" goto url
echo.
set /p niche="Enter niche (optional, leave blank for auto): "
if "%niche%"=="" (
    call venv\Scripts\activate
    python -m src.main --mode once "%url%"
) else (
    call venv\Scripts\activate
    python -m src.main --mode once "%url%" --niche "%niche%"
)
pause
goto main

:schedule
cls
echo.
echo Starting Scheduled Pipeline (runs at 9AM, 2PM, 7PM daily)
echo.
echo Press Ctrl+C to stop the scheduler
echo.
call venv\Scripts\activate
python -m src.main --mode schedule
pause
goto main

:library
cls
echo.
echo Select a downloaded video from the library:
echo.
rem Find all .info.json files in data\temp
if not exist "%~dp0data\temp\*.info.json" (
    echo No downloaded videos found in data\temp.
    echo Please download a video first using option 2.
    pause
    goto main
)
rem Use PowerShell to list and select
for /f "delims=" %%v in ('powershell -NoProfile -Command "& {
    $files = Get-ChildItem -Path '%~dp0data\temp' -Filter '*.info.json' -File |
             Sort-Object Name
    if ($files.Count -eq 0) { exit 1 }
    $selection = 0
    while ($selection -lt 1 -or $selection -gt $files.Count) {
        Write-Host 'Available videos:' -ForegroundColor Cyan
        for ($i = 0; $i -lt $files.Count; $i++) {
            try {
                $json = Get-Content -Raw -Path $($files[$i].FullName) | ConvertFrom-Json
                $title = $json.title
                if (-not $title) { $title = $j.id }
            } catch {
                $title = $($files[$i].BaseName)
            }
            Write-Host  ($i+1). ') ' $title
        }
        Write-Host ''
        $choice = Read-Host 'Enter number to select (or 0 to cancel)'
        if ($choice -eq 0) { exit 0 }
        $selection = [int]$choice
    }
    $selectedFile = $files[$selection-1].FullName
    $json = Get-Content -Raw -Path $selectedFile | ConvertFrom-Json
    $videoId = $json.id
    $title = $json.title
    if (-not $videoId) { $videoId = $json.id } # ensure
    # Output selected ID for batch to capture
    Write-Host 'SELECTED_ID:' $videoId
    Write-Host 'SELECTED_TITLE:' $title
}" 2^>nul') do (
    if "%%v"=="SELECTED_ID:" set "VIDEOFIXED=%%w"
    if "%%v"=="SELECTED_TITLE:" set "VIDEOTITLE=%%w"
)
if not defined VIDEOFIXED (
    echo.
    echo Selection cancelled or error.
    pause
    goto main
)

echo.
echo Selected video ID: %VIDEOFIXED%
echo.
set /p niche="Enter niche (optional, leave blank for auto): "
if "%niche%"=="" (
    call venv\Scripts\activate
    python -m src.main --mode once "%VIDEOFIXED%"
) else (
    call venv\Scripts\activate
    python -m src.main --mode once "%VIDEOFIXED%" --niche "%niche%"
)
pause
goto main

:exit
cls
echo.
echo Goodbye!
echo.
timeout /t 2 > nul
exit