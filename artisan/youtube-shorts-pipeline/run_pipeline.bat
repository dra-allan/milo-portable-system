@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title YouTube Shorts Pipeline Control Panel
set "PIPE_DIR=%~dp0"
set "REPO_DIR=%~dp0..\.."
set "PY=%REPO_DIR%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%PIPE_DIR%venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "SCHEDULE_MAX_VIDEOS=1"
set "SCHEDULE_MAX_TOTAL=0"
set "BACKGROUND_MODE=smart"
set "CAPTION_STYLE=hormozi"
call :load_env

:menu
cls
echo ============================================================================
echo                  YOUTUBE SHORTS PIPELINE CONTROL PANEL
echo ============================================================================
echo Repo: %REPO_DIR%
echo Python: %PY%
echo Background: %BACKGROUND_MODE%   Captions: %CAPTION_STYLE%
echo Source cap: %SCHEDULE_MAX_VIDEOS% video per niche per sweep
 echo.
echo  1. Full sweep
 echo 2. Process YouTube URL or ID
 echo 3. Scheduled mode
 echo 4. Upload existing Shorts
 echo 5. Process library
 echo 6. Test environment
 echo 7. Stats report
 echo 8. Delete uploaded local videos
 echo 9. Reset caps
 echo 10. Compile Python
 echo 11. Open runtime folders
 echo 12. Exit
 echo.
set "choice="
set /p "choice=Select: "
if "%choice%"=="1" goto full_sweep
if "%choice%"=="2" goto url
if "%choice%"=="3" goto schedule
if "%choice%"=="4" goto upload
if "%choice%"=="5" goto library
if "%choice%"=="6" goto test
if "%choice%"=="7" goto stats
if "%choice%"=="8" goto cleanup_uploaded
if "%choice%"=="9" goto reset_caps
if "%choice%"=="10" goto compile_check
if "%choice%"=="11" goto folders
if "%choice%"=="12" goto done
goto menu

:load_env
if exist "%REPO_DIR%\.env" for /f "usebackq tokens=1,* delims== eol=#" %%A in ("%REPO_DIR%\.env") do if not "%%A"=="" set "%%A=%%B"
if exist ".env" for /f "usebackq tokens=1,* delims== eol=#" %%A in (".env") do if not "%%A"=="" set "%%A=%%B"
exit /b 0
:ensure_python
"%PY%" --version >nul 2>&1
if errorlevel 1 (echo [ERROR] Python not found: %PY%&pause&exit /b 1)
"%PY%" -c "import yt_dlp" >nul 2>&1
if errorlevel 1 (
 echo Installing Shorts dependencies...
 "%PY%" -m pip install -r requirements.txt
 if errorlevel 1 (echo [ERROR] Dependency install failed.&pause&exit /b 1)
)
exit /b 0
:start_timer
set "RUN_START=%TIME: =0%"
echo.
echo [START] %~1 at %RUN_START%
exit /b 0
:stop_timer
set "RUN_END=%TIME: =0%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[datetime]::ParseExact('%RUN_START%','HH:mm:ss.ff',$null);$e=[datetime]::ParseExact('%RUN_END%','HH:mm:ss.ff',$null);if($e -lt $s){$e=$e.AddDays(1)};$d=$e-$s;Write-Host ('[DONE] '+('%~1')+' | elapsed '+('{0:00}:{1:00}:{2:00}' -f [int]$d.TotalHours,$d.Minutes,$d.Seconds)) -ForegroundColor Green"
exit /b 0
:run
call :ensure_python
if errorlevel 1 exit /b 1
"%PY%" -m src.main %*
set "RC=%ERRORLEVEL%"
echo [EXIT CODE] %RC%
exit /b %RC%
:full_sweep
call :start_timer "full sweep"
call :run --mode once --videos 1
if exist cleanup_runtime.py "%PY%" cleanup_runtime.py
call :stop_timer "full sweep"
pause
goto menu
:url
set "url="
set /p "url=YouTube URL or ID: "
if not defined url goto menu
call :start_timer "process URL"
call :run --mode once "%url%"
if exist cleanup_runtime.py "%PY%" cleanup_runtime.py
call :stop_timer "process URL"
pause
goto menu
:schedule
call :start_timer "scheduled mode"
call :run --mode schedule
call :stop_timer "scheduled mode"
pause
goto menu
:upload
call :start_timer "upload existing"
call :run -m src.safe_upload
call :stop_timer "upload existing"
pause
goto menu
:library
call :start_timer "process library"
call :run --mode library
if exist cleanup_runtime.py "%PY%" cleanup_runtime.py
call :stop_timer "process library"
pause
goto menu
:test
call :start_timer "environment test"
call :run --mode test
call :stop_timer "environment test"
pause
goto menu
:stats
call :start_timer "stats report"
call :run --mode stats --stats-age-hours 0
call :stop_timer "stats report"
pause
goto menu
:cleanup_uploaded
call :start_timer "delete uploaded local videos"
call :ensure_python
if not errorlevel 1 "%PY%" cleanup_uploaded.py
call :stop_timer "delete uploaded local videos"
pause
goto menu
:reset_caps
call :start_timer "reset caps"
call :ensure_python
if not errorlevel 1 "%PY%" reset_caps.py
call :stop_timer "reset caps"
pause
goto menu
:compile_check
call :ensure_python
if not errorlevel 1 "%PY%" -m compileall -q src
if errorlevel 1 (echo [FAIL] Python compile check failed.) else echo [OK] Python compile check passed.
pause
goto menu
:folders
if defined DATA_DIR start "Shorts data" "%DATA_DIR%"
if defined SHORTS_DIR start "Shorts output" "%SHORTS_DIR%"
if defined LOG_DIR start "Shorts logs" "%LOG_DIR%"
goto menu
:done
endlocal
exit /b 0