@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ============================================================================
rem POV PIPELINE CONTROL PANEL
rem Advanced Windows launcher for discovery, queue control, daemon scheduling,
rem upload tests, Flow preflight, diagnostics and safe process management.
rem
rem Run this file from any directory. It resolves paths relative to itself.
rem Real secrets stay in .env / config files and are never written here.
rem ============================================================================

cd /d "%~dp0"
title POV Pipeline Control Panel
set "POV_DIR=%~dp0"
set "REPO_DIR=%~dp0..\.."
set "PY=%REPO_DIR%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "PIPELINE=run_pov_pipeline.py"
set "TASK_NAME=POV Pipeline Daemon"
set "DAEMON_ARGS=--daemon"
set "FLOW_PROFILES=flow-1,flow-2,flow-3,flow-4,flow-5,flow-6"
set "LAST_RC=0"

call :load_env

:menu
cls
echo ============================================================================
echo                    POV PIPELINE CONTROL PANEL
echo ============================================================================
echo Repo:      %REPO_DIR%
echo Pipeline:  %POV_DIR%
echo Python:    %PY%
echo Profiles:  %FLOW_PROFILES%
echo Task:      %TASK_NAME%
echo.
echo  1. Discover sources and fill the queue
 echo 2. Show queue
 echo 3. Process one queued video now
 echo 4. Process one queued video, skip upload
 echo 5. Start daemon in this window
 echo 6. Install / update Windows scheduled daemon
 echo 7. Remove Windows scheduled daemon
 echo 8. Stop running daemon processes
 echo 9. Upload an existing project (dry run)
 echo 10. Upload an existing project (real upload)
 echo 11. Check Chrome Flow profiles
 echo 12. Edit pipeline config / .env
 echo 13. Run Python compile check
 echo 14. Open pipeline log
 echo 15. One-time YouTube auth
 echo 16. Exit
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
if "%choice%"=="16" goto done
goto menu

:load_env
rem Load simple KEY=VALUE lines from the repo .env without echoing secrets.
if not exist "%REPO_DIR%\.env" exit /b 0
for /f "usebackq tokens=1,* delims==" %%A in ("%REPO_DIR%\.env") do (
  if not "%%A"=="" if not "%%A:~0,1%%"=="#" set "%%A=%%B"
)
exit /b 0

:check_python
"%PY%" --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found: %PY%
  echo Create .venv or put python on PATH.
  pause
  exit /b 1
)
exit /b 0

:run_pipeline
call :check_python
if errorlevel 1 exit /b 1
"%PY%" "%PIPELINE%" %*
set "LAST_RC=%ERRORLEVEL%"
echo.
echo [EXIT CODE] %LAST_RC%
exit /b %LAST_RC%

:discover
call :run_pipeline --discover --max-channels 5
pause
goto menu

:queue
call :run_pipeline --queue
pause
goto menu

:once
call :run_pipeline --once --flow-profiles "%FLOW_PROFILES%"
pause
goto menu

:once_skip_upload
call :run_pipeline --once --skip-upload --flow-profiles "%FLOW_PROFILES%"
pause
goto menu

:daemon_foreground
call :run_pipeline --daemon --flow-profiles "%FLOW_PROFILES%"
pause
goto menu

:install_task
call :check_python
if errorlevel 1 goto menu
set "TASK_CMD=\"%PY%\" \"%POV_DIR%%PIPELINE%\" --daemon --flow-profiles \"%FLOW_PROFILES%\""
echo.
echo Installing or updating: %TASK_NAME%
echo This starts at logon and restarts every 15 minutes if it exits.
choice /c YN /n /m "Continue? [Y/N] "
if errorlevel 2 goto menu
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
schtasks /create /tn "%TASK_NAME%" /tr "%TASK_CMD%" /sc onlogon /ru "%USERNAME%" /f
if errorlevel 1 (
  echo [ERROR] Could not create the scheduled task.
) else (
  echo [OK] Scheduled daemon installed. Cadence comes from pov_channels.yaml.
  echo [OK] Use menu 7 to remove it, or menu 8 to stop a current process.
)
pause
goto menu

:remove_task
echo Removing scheduled task: %TASK_NAME%
schtasks /delete /tn "%TASK_NAME%" /f
if errorlevel 1 echo [INFO] Task was not present.
pause
goto menu

:stop_daemon
echo Stopping POV daemon processes launched from this repo...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root=[IO.Path]::GetFullPath('%POV_DIR%'); $ps=Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -and $_.CommandLine -like ('*' + $root + '*run_pov_pipeline.py*') -and $_.CommandLine -like '*--daemon*' -and $_.ProcessId -ne $PID }; if(-not $ps){ Write-Host '[INFO] No daemon process found.'; exit 0 }; foreach($p in $ps){ Write-Host ('Stopping PID ' + $p.ProcessId); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }"
pause
goto menu

:upload_dry
set "PROJECT="
set /p "PROJECT=Project folder name: "
if "%PROJECT%"=="" goto menu
call :run_pipeline --project "%PROJECT%" --stage upload --dry-run-upload --privacy unlisted
pause
goto menu

:upload_real
set "PROJECT="
set /p "PROJECT=Project folder name: "
if "%PROJECT%"=="" goto menu
echo.
echo WARNING: this calls YouTube and creates an upload.
choice /c YN /n /m "Upload as unlisted? [Y/N] "
if errorlevel 2 goto menu
call :run_pipeline --project "%PROJECT%" --stage upload --privacy unlisted
pause
goto menu

:check_profiles
call :run_pipeline --check-profiles --flow-profiles "%FLOW_PROFILES%"
pause
goto menu

:edit_config
choice /c CYEN /n /m "Edit [C]hannels YAML, [Y].env, [E]nv vars, [N]othing: "
if errorlevel 4 goto menu
if errorlevel 3 goto edit_env_vars
if errorlevel 2 goto edit_env
if errorlevel 1 goto edit_yaml
:edit_yaml
start "POV channels config" notepad "%POV_DIR%config\pov_channels.yaml"
pause
goto menu
:edit_env
if not exist "%REPO_DIR%\.env" copy /y "%POV_DIR%config\notify.env.template" "%REPO_DIR%\.env" >nul
start "POV environment" notepad "%REPO_DIR%\.env"
pause
goto menu
:edit_env_vars
set /p "FLOW_PROFILES=Flow profiles, comma separated [%FLOW_PROFILES%]: "
if not defined FLOW_PROFILES set "FLOW_PROFILES=flow-1,flow-2,flow-3,flow-4,flow-5,flow-6"
echo Session-only setting updated. Persist it in .env if needed:
echo POV_FLOW_PROFILES=%FLOW_PROFILES%
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
if exist "%POV_DIR%state\pipeline.log" (start "POV pipeline log" notepad "%POV_DIR%state\pipeline.log") else echo No pipeline log yet.
pause
goto menu

auth
:auth
call :check_python
if errorlevel 1 goto menu
call :run_pipeline --help >nul
"%PY%" -m uploader auth --channel explaination
pause
goto menu

:done
endlocal
exit /b 0
