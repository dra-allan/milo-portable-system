@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Ranking Shorts Pipeline Control Panel
set "PIPE_DIR=%~dp0"
set "REPO_DIR=%~dp0..\.."
set "PY=%REPO_DIR%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%PIPE_DIR%venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "RANKING_CHANNEL_PROFILES=rankdrop:both"
set "RANKING_UPLOAD_CHANNEL=rankdrop"
set "RANKING_UPLOAD_MAX_PER_DAY=6"
set "RANKING_UPLOAD_DELAY_MIN=45"
set "RANKING_UPLOAD_DELAY_MAX=180"
set "CONTRAST_SUBJECT=GUY"
call :load_env
:menu
cls
echo ============================================================================
echo                 RANKING SHORTS PIPELINE CONTROL PANEL
echo ============================================================================
echo Profiles: %RANKING_CHANNEL_PROFILES%
echo Cap: %RANKING_UPLOAD_MAX_PER_DAY% uploads / rolling 24h, public
 echo 1. Run configured mixed sweep
 echo 2. Build normal ranking, no upload
 echo 3. Build contrast ranking, no upload
 echo 4. Upload pending builds, capped and public
 echo 5. Configure channel profiles
 echo 6. Authenticate a channel
 echo 7. Set contrast subject
 echo 8. Exit
 echo.
set "choice="
set /p "choice=Select: "
if "%choice%"=="1" goto mixed
if "%choice%"=="2" goto normal
if "%choice%"=="3" goto contrast
if "%choice%"=="4" goto upload
if "%choice%"=="5" goto profiles
if "%choice%"=="6" goto auth
if "%choice%"=="7" goto subject
if "%choice%"=="8" goto done
goto menu
:load_env
if exist "%REPO_DIR%\.env" for /f "usebackq tokens=1,* delims== eol=#" %%A in ("%REPO_DIR%\.env") do if not "%%A"=="" set "%%A=%%B"
if exist "config\.env" for /f "usebackq tokens=1,* delims== eol=#" %%A in ("config\.env") do if not "%%A"=="" set "%%A=%%B"
exit /b 0
:ensure
"%PY%" --version >nul 2>&1
if errorlevel 1 echo Python not found.&pause&exit /b 1
exit /b 0
:mixed
call :ensure
"%PY%" mixed_sweep.py
pause
goto menu
:normal
call :ensure
"%PY%" -m src.main --mode auto --videos 1 --no-upload
pause
goto menu
:contrast
call :ensure
"%PY%" run_contrast_pipeline.py
pause
goto menu
:upload
call :ensure
"%PY%" upload_pending_public.py
pause
goto menu
:profiles
set "NEW_PROFILES="
set /p "NEW_PROFILES=Profiles, e.g. rankdrop:contrast,rankings_main:normal,rankmix:both: "
if defined NEW_PROFILES set "RANKING_CHANNEL_PROFILES=%NEW_PROFILES%"
goto menu
:auth
call :ensure
set "CHANNEL="
set /p "CHANNEL=Channel key to authenticate: "
if not defined CHANNEL goto menu
"%PY%" -c "from src.publisher import auth; print('Authenticated channel ID:', auth(r'%CHANNEL%') or 'not returned')"
pause
goto menu
:subject
set "NEW_SUBJECT="
set /p "NEW_SUBJECT=Contrast subject, e.g. GUY, DOG, PRO: "
if defined NEW_SUBJECT set "CONTRAST_SUBJECT=%NEW_SUBJECT%"
goto menu
:done
endlocal
exit /b 0
