@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title YouTube Shorts Pipeline - Easy Runner
set "SCHEDULE_MAX_VIDEOS=1"
set "SCHEDULE_MAX_TOTAL=0"
set "BACKGROUND_MODE=smart"
set "CAPTION_STYLE=hormozi"
rem YouTube is currently rate-limiting subtitle requests. Audio discovery must not depend on them.
set "USE_YOUTUBE_SUBS=false"
if exist .env (
 for /f "tokens=1,* delims==" %%a in ('findstr /b /i "BACKGROUND_MODE=" .env') do set "BACKGROUND_MODE=%%b"
 for /f "tokens=1,* delims==" %%a in ('findstr /b /i "CAPTION_STYLE=" .env') do set "CAPTION_STYLE=%%b"
 for /f "tokens=1,* delims==" %%a in ('findstr /b /i "USE_YOUTUBE_SUBS=" .env') do set "USE_YOUTUBE_SUBS=%%b"
)
:main
cls
echo ================================================================
echo           YouTube Shorts Pipeline - Easy Runner
echo ================================================================
echo Background: %BACKGROUND_MODE%   Captions: %CAPTION_STYLE%
echo Source cap: 1 video per niche per sweep/day
echo.
echo 1. Full sweep
echo 2. Process YouTube URL / ID
echo 3. Scheduled mode
echo 4. Upload existing Shorts
echo 5. Process library
echo 6. Test mode
echo 7. Stats report
echo 8. Delete already-uploaded local videos
echo 9. Exit
echo.
set /p choice="Select: "
if "%choice%"=="1" goto full_sweep
if "%choice%"=="2" goto url
if "%choice%"=="3" goto schedule
if "%choice%"=="4" goto upload
if "%choice%"=="5" goto library
if "%choice%"=="6" goto test
if "%choice%"=="7" goto stats
if "%choice%"=="8" goto cleanup_uploaded
if "%choice%"=="9" goto done
goto main
:start_timer
set "RUN_START=%TIME%"
echo.
echo [START] %~1 at %RUN_START%
exit /b 0
:stop_timer
set "RUN_END=%TIME%"
rem TIME has a leading space before single-digit hours. Parse as TimeSpan after trimming.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[timespan]::Parse($env:RUN_START.Trim());$e=[timespan]::Parse($env:RUN_END.Trim());if($e -lt $s){$e=$e.Add([timespan]::FromDays(1))};$d=$e-$s;Write-Host ('[DONE] '+('%~1')+' | elapsed '+('{0:00}:{1:00}:{2:00}' -f [int]$d.TotalHours,$d.Minutes,$d.Seconds)) -ForegroundColor Green"
exit /b 0
:activate
if not exist venv\Scripts\activate.bat (echo Missing venv. Create it and install requirements.&pause&exit /b 1)
call venv\Scripts\activate.bat
exit /b 0
:cleanup
if exist cleanup_runtime.py python cleanup_runtime.py
exit /b 0
:full_sweep
call :start_timer "full sweep"
call :activate
python -m src.main --mode once --videos 1
call :cleanup
call :stop_timer "full sweep"
pause
goto main
:url
set /p url="YouTube URL or ID: "
if "%url%"=="" goto main
call :start_timer "process URL"
call :activate
python -m src.main --mode once "%url%"
call :cleanup
call :stop_timer "process URL"
pause
goto main
:schedule
call :start_timer "scheduled mode"
call :activate
python -m src.main --mode schedule
call :cleanup
call :stop_timer "scheduled mode"
pause
goto main
:upload
call :start_timer "upload existing"
call :activate
python -m src.safe_upload
call :stop_timer "upload existing"
pause
goto main
:library
call :start_timer "process library"
call :activate
python -m src.main --mode library
call :cleanup
call :stop_timer "process library"
pause
goto main
:test
call :start_timer "environment test"
call :activate
python -m src.main --mode test
call :stop_timer "environment test"
pause
goto main
:stats
call :start_timer "stats report"
call :activate
python -m src.main --mode stats --stats-age-hours 0
call :stop_timer "stats report"
pause
goto main
:cleanup_uploaded
call :start_timer "delete uploaded local videos"
call :activate
python cleanup_uploaded.py
call :stop_timer "delete uploaded local videos"
pause
goto main
:done
endlocal
exit /b 0
