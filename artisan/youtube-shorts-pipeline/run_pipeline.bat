@echo off
title YouTube Shorts Pipeline - Easy Runner

:: Source cap: one source video per niche per sweep/day. Keep this explicit so
:: niche max_videos overrides cannot create a backlog from the launcher.
set "SCHEDULE_MAX_VIDEOS=1"
set "SCHEDULE_MAX_TOTAL=0"

:: Load persistent settings from .env file
set "BACKGROUND_MODE=smart"
set "CAPTION_STYLE=hormozi"
if exist .env (
    for /f "tokens=1,* delims==" %%a in ('findstr /b /i "BACKGROUND_MODE=" .env') do set "BACKGROUND_MODE=%%b"
    for /f "tokens=1,* delims==" %%a in ('findstr /b /i "CAPTION_STYLE=" .env') do set "CAPTION_STYLE=%%b"
)

:: If arguments are provided, skip the menu
if not "%~1"=="" (
    if "%~1"=="1" goto full_sweep
    if "%~1"=="2" goto url
    if "%~1"=="3" goto schedule
    if "%~1"=="4" goto upload_existing
    if "%~1"=="5" goto library
    if "%~1"=="6" goto test
    if "%~1"=="7" goto set_background
    if "%~1"=="8" goto stats_report
    if "%~1"=="9" goto set_caption
    if "%~1"=="10" goto reset_caps
    if "%~1"=="11" goto exit_script
    echo Invalid option: %~1
    pause
    goto main
)

:main
cls
echo.
echo ================================================================
echo           YouTube Shorts Pipeline - Easy Runner
echo ================================================================
echo.
echo  Current BackgroundMode : %BACKGROUND_MODE%
echo  Current CaptionStyle   : %CAPTION_STYLE%
echo  Source cap             : 1 video per niche per sweep/day
echo.
echo   1. Run Full Sweep Now (auto-discover ^& process all niches)
echo   2. Process a YouTube URL/Video ID
echo   3. Run in Scheduled Mode (9AM Daily)
echo   4. Upload Existing Local Shorts
echo   5. Process from Library (downloaded videos)
echo   6. Run in Test Mode (check components)
echo   7. Set BackgroundMode
echo   8. View Channel Performance Report (Live Stats)
echo   9. Set CaptionStyle
echo  10. Reset Daily Upload Caps ^& Dead Channels
echo  11. Exit
echo.
set "choice="
set /p choice="Select an option: "
set "choice=%choice: =%"
if "%choice%"=="1" goto full_sweep
if "%choice%"=="2" goto url
if "%choice%"=="3" goto schedule
if "%choice%"=="4" goto upload_existing
if "%choice%"=="5" goto library
if "%choice%"=="6" goto test
if "%choice%"=="7" goto set_background
if "%choice%"=="8" goto stats_report
if "%choice%"=="9" goto set_caption
if "%choice%"=="10" goto reset_caps
if "%choice%"=="11" goto exit_script
echo Invalid choice! Please try again.
timeout /t 2 > nul
goto main

:full_sweep
cls
echo.
echo ================================================================
echo           Run Full Sweep Now
echo ================================================================
echo.
echo This runs one complete automated sweep across all niches:
echo  - Sources at most ONE new source video per niche
echo  - Downloads, transcribes, finds highlights, renders Shorts
echo  - Uploads to YouTube if enabled
echo  - Existing backlog is handled separately by upload controls
echo.
echo Press Ctrl+C at any time to cancel.
echo.
call :start_timer
call venv\Scripts\activate
python -m src.main --mode once --videos 1
call :stop_timer
echo.
echo Pipeline execution completed.
pause
goto main

:test
cls
echo.
echo ================================================================
echo           Test Mode
echo ================================================================
echo.
echo Running component checks...
echo.
call :start_timer
call venv\Scripts\activate
python -m src.main --mode test
call :stop_timer
echo.
echo Test mode completed.
pause
goto main

:schedule
cls
echo.
echo ================================================================
echo           Scheduled Mode
echo ================================================================
echo.
echo Running at 9AM daily with a one-source-video-per-niche cap.
echo Press Ctrl+C to stop.
echo.
set "SCHEDULE_MAX_VIDEOS=1"
call venv\Scripts\activate
python -m src.main --mode schedule
 echo.
echo Scheduler stopped.
pause
goto main

:upload_existing
cls
echo.
echo ================================================================
echo           Upload Existing Local Shorts
echo ================================================================
echo.
echo  This uploads rendered-but-unpublished shorts to YouTube.
echo.
echo  1. Upload by Niche
echo  2. Upload by Channel Override
echo  3. Upload All Pending
echo  4. Review and Pick Clips (interactive)
echo  0. Back to Main Menu
echo.
set "ue_choice="
set /p ue_choice="Select an option: "
set "ue_choice=%ue_choice: =%"
if "%ue_choice%"=="0" goto main
if "%ue_choice%"=="1" goto upload_by_niche
if "%ue_choice%"=="2" goto upload_by_channel
if "%ue_choice%"=="3" goto upload_all
if "%ue_choice%"=="4" goto upload_interactive
echo Invalid choice!
timeout /t 2 > nul
goto upload_existing

:upload_interactive
cls
echo.
echo ================================================================
echo           Review and Pick Clips
echo ================================================================
echo.
echo Lists every rendered-but-unpublished clip grouped by niche.
echo Pick exactly which clips to post (e.g. "1,2,4-6" or "all").
echo.
call :start_timer
call venv\Scripts\activate
python -m src.main --mode upload-existing --interactive
call :stop_timer
echo.
echo Interactive upload completed.
pause
goto upload_existing

:upload_by_niche
cls
echo.
echo ================================================================
echo           Upload by Niche
echo ================================================================
echo.
echo Type 'back' to return.
echo.
set "niche="
set /p niche="Niche name (e.g., flick_shorts): "
set "niche=%niche: =%"
if /i "%niche%"=="back" goto upload_existing
if "%niche%"=="" (
    echo Niche name is required.
    pause
    goto upload_by_niche
)
call :start_timer
call venv\Scripts\activate
python -m src.main --mode upload-existing --niche "%niche%"
call :stop_timer
echo.
echo Niche upload completed.
pause
goto upload_existing

:upload_by_channel
cls
echo.
echo ================================================================
echo           Upload by Channel Override
echo ================================================================
echo.
echo Type 'back' to return.
echo.
set "channel="
set /p channel="Channel key (e.g., flick_shorts): "
set "channel=%channel: =%"
if /i "%channel%"=="back" goto upload_existing
if "%channel%"=="" (
    echo Channel key is required.
    pause
    goto upload_by_channel
)
call :start_timer
call venv\Scripts\activate
python -m src.main --mode upload-existing --channel "%channel%"
call :stop_timer
echo.
echo Channel upload completed.
pause
goto upload_existing

:upload_all
cls
echo.
echo ================================================================
echo           Upload All Pending
echo ================================================================
echo.
echo Uploading all pending shorts (respects UPLOAD_MAX_PER_RUN limit)...
echo.
call :start_timer
call venv\Scripts\activate
python -m src.main --mode upload-existing
call :stop_timer
echo.
echo All pending uploads completed.
pause
goto upload_existing

:stats_report
cls
echo.
echo ================================================================
echo           Channel Performance Report (Live Stats)
echo ================================================================
echo.
echo Pulling latest YouTube metrics for all connected channels...
echo.
call :start_timer
call venv\Scripts\activate
python -m src.main --mode stats --stats-age-hours 0
call :stop_timer
echo.
echo Report completed.
pause
goto main

:set_background
cls
echo.
echo ================================================================
echo           Set Background Mode
echo ================================================================
echo.
echo  Current: %BACKGROUND_MODE%
echo.
echo  0. Back to Main Menu
echo  1. crop       - Fill frame by cropping sides
echo  2. blur       - Blurred background bars
echo  3. cheap      - Low-res blurred background (faster)
echo  4. black      - Solid black bars
echo  5. smart      - Person-aware cropping (face detection)
echo.
set "bg_choice="
set /p bg_choice="Select background mode (0-5): "
set "bg_choice=%bg_choice: =%"
if "%bg_choice%"=="0" goto main
set "new_mode="
if "%bg_choice%"=="1" set "new_mode=crop"
if "%bg_choice%"=="2" set "new_mode=blur"
if "%bg_choice%"=="3" set "new_mode=cheap"
if "%bg_choice%"=="4" set "new_mode=black"
if "%bg_choice%"=="5" set "new_mode=smart"
if not defined new_mode (
    echo Invalid choice!
    timeout /t 2 > nul
    goto set_background
)
call :update_env BACKGROUND_MODE %new_mode%
set "BACKGROUND_MODE=%new_mode%"
echo.
echo Background mode set to: %BACKGROUND_MODE%
echo.
pause
goto main

:set_caption
cls
echo.
echo ================================================================
echo           Set Caption Style
echo ================================================================
echo.
echo  Current: %CAPTION_STYLE%
echo.
echo  0. Back to Main Menu
echo  1. default    - Original Arial style
echo  2. hormozi    - Alex Hormozi style (bold, dynamic colors)
echo  3. minimalist - Clean minimalist (sans-serif, white with shadow)
echo  4. pop        - Pop ^& bounce (neon highlights, black outline)
echo  5. kinetic    - Kinetic karaoke (word-by-word highlight)
echo.
set "cap_choice="
set /p cap_choice="Select caption style (0-5): "
set "cap_choice=%cap_choice: =%"
if "%cap_choice%"=="0" goto main
set "new_style="
if "%cap_choice%"=="1" set "new_style=default"
if "%cap_choice%"=="2" set "new_style=hormozi"
if "%cap_choice%"=="3" set "new_style=minimalist"
if "%cap_choice%"=="4" set "new_style=pop"
if "%cap_choice%"=="5" set "new_style=kinetic"
if not defined new_style (
    echo Invalid choice!
    timeout /t 2 > nul
    goto set_caption
)
call :update_env CAPTION_STYLE %new_style%
set "CAPTION_STYLE=%new_style%"
echo.
echo Caption style set to: %CAPTION_STYLE%
echo.
pause
goto main

:update_env
rem %1 = key, %2 = value
if exist .env (
    findstr /v /i "%1=" .env > .env.tmp
) else (
    type nul > .env.tmp
)
echo %1=%2>> .env.tmp
move /y .env.tmp .env > nul
goto :eof

:reset_caps
cls
echo.
echo ================================================================
echo           Reset Daily Upload Caps ^& Dead Channels
echo ================================================================
echo.
echo Resetting 24-hour upload limits and dead-channel cache...
echo.
call :start_timer
call venv\Scripts\activate
python reset_caps.py
call :stop_timer
echo.
pause
goto main

:exit_script
cls
echo.
echo Goodbye!
timeout /t 2 > nul
exit

:start_timer
powershell -NoProfile -Command "[DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss')" > "%TEMP%\yt_start.txt" 2>nul
goto :eof

:stop_timer
if exist "%TEMP%\yt_start.txt" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[DateTime]::Parse((Get-Content '%TEMP%\yt_start.txt')); $e=[DateTime]::Now; $d=$e-$s; $ts='{0:D2}:{1:D2}:{2:D2}' -f $d.Hours, $d.Minutes, $d.Seconds; Write-Host ('================================================================') -ForegroundColor Cyan; Write-Host ('  Started: ' + $s.ToString('HH:mm:ss') + '  |  Finished: ' + $e.ToString('HH:mm:ss')) -ForegroundColor Yellow; Write-Host ('  Elapsed Time: ' + $ts + ' (' + [math]::Round($d.TotalMinutes, 2) + ' min)') -ForegroundColor Green; Write-Host ('================================================================') -ForegroundColor Cyan" 2>nul
    del "%TEMP%\yt_start.txt" 2>nul
)
goto :eof
