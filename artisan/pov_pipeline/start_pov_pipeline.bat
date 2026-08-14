@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem POV Pipeline Control Panel. Run from any directory.
cd /d "%~dp0"
title POV Pipeline Control Panel
set "POV_DIR=%~dp0"
set "REPO_DIR=%~dp0..\.."
set "FACTORY_DIR=%USERPROFILE%\Desktop\Milo Video Factory\pov"
if not exist "%FACTORY_DIR%" set "FACTORY_DIR=%REPO_DIR%\artisan\pov_pipeline"
set "PY=%REPO_DIR%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "PIPELINE=run_pov_pipeline.py"
set "TASK_NAME=POV Pipeline Daemon"
set "FLOW_PROFILES=flow-1,flow-2,flow-3,flow-4,flow-5,flow-6"
call :load_env

:menu
cls
echo ============================================================================
echo                    POV PIPELINE CONTROL PANEL
echo ============================================================================
echo Repo: %REPO_DIR%
echo Python: %PY%
echo Flow profiles: %FLOW_PROFILES%
echo Scheduled task: %TASK_NAME%
echo.
echo  1. Discover sources and fill queue
 echo 2. Show queue
 echo 3. Process one queued video now
 echo 4. Process one queued video, skip upload
 echo 5. Start daemon in this window
 echo 6. Install or update Windows scheduled daemon
 echo 7. Remove Windows scheduled daemon
 echo 8. Stop running daemon processes
 echo 9. Upload existing project, dry run
 echo 10. Upload existing project, unlisted
 echo 11. Check Chrome Flow profiles
 echo 12. Edit channels YAML or .env
 echo 13. Run Python compile check
 echo 14. Open pipeline log
 echo 15. One-time YouTube auth
 echo 16. Resume incomplete project
 echo 17. Exit
 echo.
set "choice="
set /p "choice=Select: "
if "%choice%"=="1" goto discover
if "%choice%"=="2" goto queue
if "%choice%"=="3" goto once
if "%choice%"=="4" goto once_skip_upload
if "%choice%"=="5" goto daemon_foreground
if "%choice%"=="6" goto install_task
if "%choice%"=="7" goto remove_task
if "%choice%"=="8" goto stop_daemon
if "%choice%"=="9" goto upload_dry
if "%choice%"=="10" goto upload_real
if "%choice%"=="11" goto check_profiles
if "%choice%"=="12" goto edit_config
if "%choice%"=="13" goto compile_check
if "%choice%"=="14" goto open_log
if "%choice%"=="15" goto auth
if "%choice%"=="16" goto resume
if "%choice%"=="17" goto done
goto menu

:load_env
if not exist "%REPO_DIR%\.env" exit /b 0
for /f "usebackq tokens=1,* delims==" %%A in ("%REPO_DIR%\.env") do if not "%%A"=="" if not "%%A:~0,1%%"=="#" set "%%A=%%B"
if defined POV_FLOW_PROFILES set "FLOW_PROFILES=%POV_FLOW_PROFILES%"
exit /b 0

:check_python
"%PY%" --version >nul 2>&1
if errorlevel 1 echo [ERROR] Python not found: %PY% & pause & exit /b 1
exit /b 0

:run_pipeline
call :check_python
if errorlevel 1 exit /b 1
"%PY%" "%PIPELINE%" %*
echo.
echo [EXIT CODE] %ERRORLEVEL%
exit /b %ERRORLEVEL%

:discover
call :run_pipeline --discover --max-channels 5
pause
goto menu
:queue
call :run_pipeline --queue
pause
goto menu
:once
call :run_pipeline --once --ignore-window --flow-profiles "%FLOW_PROFILES%"
pause
goto menu
:once_skip_upload
call :run_pipeline --once --ignore-window --skip-upload --flow-profiles "%FLOW_PROFILES%"
pause
goto menu
:daemon_foreground
call :run_pipeline --daemon --flow-profiles "%FLOW_PROFILES%"
pause
goto menu

:install_task
call :check_python
if errorlevel 1 goto menu
echo.
echo Installing or updating %TASK_NAME%.
echo It starts at logon; cadence and daily limits come from pov_channels.yaml.
choice /c YN /n /m "Continue? [Y/N] "
if errorlevel 2 goto menu
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
rem Quote the executable and script correctly for schtasks. No backslash escaping.
schtasks /create /tn "%TASK_NAME%" /tr "\"%PY%\" \"%POV_DIR%%PIPELINE%\" --daemon --flow-profiles \"%FLOW_PROFILES%\"" /sc onlogon /ru "%USERNAME%" /f
if errorlevel 1 echo [ERROR] Could not create the scheduled task.
if not errorlevel 1 echo [OK] Scheduled daemon installed.
pause
goto menu
:remove_task
schtasks /delete /tn "%TASK_NAME%" /f
if errorlevel 1 echo [INFO] Task was not present.
pause
goto menu
:stop_daemon
echo Stopping daemon processes launched from this repo...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root=[IO.Path]::GetFullPath('%POV_DIR%'); $ps=Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ? { $_.CommandLine -and $_.CommandLine -like ('*' + $root + '*run_pov_pipeline.py*') -and $_.CommandLine -like '*--daemon*' }; if(-not $ps){ 'No daemon process found.' } else { $ps | %% { 'Stopping PID ' + $_.ProcessId; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }"
pause
goto menu

:upload_dry
set "PROJECT="
set /p "PROJECT=Project folder name: "
if not defined PROJECT goto menu
call :run_pipeline --project "%PROJECT%" --stage upload --dry-run-upload --privacy unlisted
pause
goto menu
:upload_real
set "PROJECT="
set /p "PROJECT=Project folder name: "
if not defined PROJECT goto menu
choice /c YN /n /m "Create an unlisted YouTube upload? [Y/N] "
if errorlevel 2 goto menu
call :run_pipeline --project "%PROJECT%" --stage upload --privacy unlisted
pause
goto menu

:check_profiles
call :run_pipeline --check-profiles --flow-profiles "%FLOW_PROFILES%"
pause
goto menu
:edit_config
choice /c CYEN /n /m "Edit [C]hannels YAML, [Y].env, [E]nv profiles, [N]othing: "
if errorlevel 4 goto menu
if errorlevel 3 goto edit_env_vars
if errorlevel 2 goto edit_env
start "POV channels" notepad "%POV_DIR%config\pov_channels.yaml"
pause
goto menu
:edit_env
if not exist "%FACTORY_DIR%\config\notify.env" copy /y "%POV_DIR%config\notify.env.template" "%FACTORY_DIR%\config\notify.env" >nul
start "POV environment" notepad "%FACTORY_DIR%\config\notify.env"
pause
goto menu
:edit_env_vars
set /p "FLOW_PROFILES=Flow profiles, comma separated [%FLOW_PROFILES%]: "
if not defined FLOW_PROFILES set "FLOW_PROFILES=flow-1,flow-2,flow-3,flow-4,flow-5,flow-6"
echo Session-only profiles: %FLOW_PROFILES%
echo Persist them as POV_FLOW_PROFILES in .env if desired.
pause
goto menu

:compile_check
call :check_python
if errorlevel 1 goto menu
"%PY%" -m py_compile agent_runner.py povconfig.py discovery.py uploader.py daemon.py notify.py run_pov_pipeline.py
if errorlevel 1 (echo [FAIL] Python compile check failed.) else echo [OK] Python compile check passed.
pause
goto menu
:open_log
if exist "%FACTORY_DIR%\state\pipeline.log" (start "POV log" notepad "%FACTORY_DIR%\state\pipeline.log") else echo No pipeline log yet.
pause
goto menu
:auth
call :check_python
if errorlevel 1 goto menu
"%PY%" -m uploader auth --channel explaination
pause
goto menu
:resume
set "PROJECT="
set /p "PROJECT=Project folder name (blank = newest incomplete): "
call :check_python
if errorlevel 1 goto menu
"%PY%" resume_project.py %PROJECT%
echo.
echo [EXIT CODE] %ERRORLEVEL%
pause
goto menu
:done
endlocal
exit /b 0
