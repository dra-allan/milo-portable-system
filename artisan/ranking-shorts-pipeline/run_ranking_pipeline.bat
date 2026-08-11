@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Ranking Shorts Pipeline Control Panel
set "PIPE_DIR=%~dp0"
set "REPO_DIR=%~dp0..\.."
set "PY=%REPO_DIR%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%PIPE_DIR%venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "RANKING_TOPIC=auto"
set "UPLOAD_PRIVACY=private"
set "RANKING_FAST_MODE=true"
set "RANKING_RENDER_WORKERS=2"
set "RANKING_REJECT_BUDGET=20"
call :load_env
if defined VIDEO_FACTORY_ROOT set "RANKING_RUNTIME=%VIDEO_FACTORY_ROOT%\ranking-shorts-pipeline"
if not defined RANKING_RUNTIME set "RANKING_RUNTIME=%LOCALAPPDATA%\DRA\VideoFactory\ranking-shorts-pipeline"
:menu
cls
echo ============================================================================
echo                 RANKING SHORTS PIPELINE CONTROL PANEL
echo ============================================================================
echo Repo: %REPO_DIR%
echo Python: %PY%
echo Topic: %RANKING_TOPIC%   Privacy: %UPLOAD_PRIVACY%
echo Fast vetting: %RANKING_FAST_MODE%   Render workers: %RANKING_RENDER_WORKERS%
echo Runtime: %RANKING_RUNTIME%
echo.
echo  1. Build, no upload
 echo 2. Build and upload
 echo 3. Source and vet clips
 echo 4. Assemble saved plan
 echo 5. Upload pending builds
 echo 6. Run one sweep
 echo 7. Start scheduler daemon
 echo 8. Test environment
 echo 9. Compile Python
 echo 10. Set topic
 echo 11. Toggle fast vetting
 echo 12. Open runtime folders
 echo 13. Delete uploaded local videos
 echo 14. Stop ranking scheduler
 echo 15. Exit
 echo.
set "choice="
set /p "choice=Select: "
if "%choice%"=="1" goto build_private
if "%choice%"=="2" goto build_upload
if "%choice%"=="3" goto source
if "%choice%"=="4" goto assemble
if "%choice%"=="5" goto upload
if "%choice%"=="6" goto sweep
if "%choice%"=="7" goto schedule
if "%choice%"=="8" goto test
if "%choice%"=="9" goto compile_check
if "%choice%"=="10" goto set_topic
if "%choice%"=="11" goto toggle_fast
if "%choice%"=="12" goto folders
if "%choice%"=="13" goto cleanup_uploaded
if "%choice%"=="14" goto stop_daemon
if "%choice%"=="15" goto done
goto menu
:load_env
if exist "%REPO_DIR%\.env" for /f "usebackq tokens=1,* delims== eol=#" %%A in ("%REPO_DIR%\.env") do if not "%%A"=="" set "%%A=%%B"
if exist "config\.env" for /f "usebackq tokens=1,* delims== eol=#" %%A in ("config\.env") do if not "%%A"=="" set "%%A=%%B"
exit /b 0
:ensure_python
"%PY%" --version >nul 2>&1
if errorlevel 1 (echo [ERROR] Python not found: %PY%&pause&exit /b 1)
"%PY%" -c "import yaml, yt_dlp" >nul 2>&1
if errorlevel 1 (echo Installing ranking dependencies...&"%PY%" -m pip install -r requirements.txt&if errorlevel 1 (echo [ERROR] Dependency install failed.&pause&exit /b 1))
exit /b 0
:start_timer
set "RUN_START=%TIME: =0%"
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
:build_private
call :start_timer "build no-upload"
if /i "%RANKING_TOPIC%"=="auto" (call :run --mode auto --no-upload) else (call :run --mode once --topic "%RANKING_TOPIC%" --no-upload)
call :stop_timer "build no-upload"
pause
goto menu
:build_upload
call :start_timer "build and upload"
if /i "%RANKING_TOPIC%"=="auto" (call :run --mode auto) else (call :run --mode once --topic "%RANKING_TOPIC%")
call :stop_timer "build and upload"
pause
goto menu
:source
call :start_timer "source and vet"
call :run --mode source --topic "%RANKING_TOPIC%"
call :stop_timer "source and vet"
pause
goto menu
:assemble
set "plan="
set /p "plan=Plan JSON path, or BACK: "
if /i "%plan%"=="BACK" goto menu
if not defined plan goto menu
call :start_timer "assemble plan"
call :run --mode assemble --plan "%plan%"
call :stop_timer "assemble plan"
pause
goto menu
:upload
call :start_timer "upload pending"
call :run --mode upload
call :stop_timer "upload pending"
pause
goto menu
:sweep
call :start_timer "sweep"
call :run --mode sweep
call :stop_timer "sweep"
pause
goto menu
:schedule
call :start_timer "scheduler daemon"
call :run --mode schedule
call :stop_timer "scheduler daemon"
pause
goto menu
:test
call :start_timer "environment test"
call :run --mode test
call :stop_timer "environment test"
pause
goto menu
:compile_check
call :ensure_python
if not errorlevel 1 "%PY%" -m compileall -q src
if errorlevel 1 (echo [FAIL] Python compile check failed.) else echo [OK] Python compile check passed.
pause
goto menu
:set_topic
set "new_topic="
set /p "new_topic=Topic key, or AUTO or BACK: "
if /i "%new_topic%"=="BACK" goto menu
if /i "%new_topic%"=="AUTO" set "new_topic=auto"
if defined new_topic set "RANKING_TOPIC=%new_topic%"
goto menu
:toggle_fast
if /i "%RANKING_FAST_MODE%"=="true" (set "RANKING_FAST_MODE=false") else (set "RANKING_FAST_MODE=true")
echo Fast vetting is now %RANKING_FAST_MODE% for this session.
goto menu
:folders
if not exist "%RANKING_RUNTIME%\data" mkdir "%RANKING_RUNTIME%\data"
if not exist "%RANKING_RUNTIME%\temp" mkdir "%RANKING_RUNTIME%\temp"
if not exist "%RANKING_RUNTIME%\output" mkdir "%RANKING_RUNTIME%\output"
start "Ranking data" "%RANKING_RUNTIME%\data"
start "Ranking temp" "%RANKING_RUNTIME%\temp"
start "Ranking output" "%RANKING_RUNTIME%\output"
goto menu
:cleanup_uploaded
call :start_timer "delete uploaded local videos"
call :ensure_python
if not errorlevel 1 "%PY%" cleanup_uploaded.py
call :stop_timer "delete uploaded local videos"
pause
goto menu
:stop_daemon
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root=[IO.Path]::GetFullPath('%PIPE_DIR%');$ps=Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ? { $_.CommandLine -and $_.CommandLine -like ('*'+$root+'*src.main*') -and $_.CommandLine -like '*--mode schedule*' };if(-not $ps){'No ranking scheduler found.'}else{$ps|%%{ 'Stopping PID '+$_.ProcessId;Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
pause
goto menu
:done
endlocal
exit /b 0
