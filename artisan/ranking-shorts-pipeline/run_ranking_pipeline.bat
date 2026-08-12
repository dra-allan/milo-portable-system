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
set "RANKING_UPLOAD_CHANNEL=rankdrop"
set "UPLOAD_PRIVACY=public"
set "RANKING_VIDEOS_PER_RUN=6"
set "RANKING_UPLOAD_DELAY_MIN=45"
set "RANKING_UPLOAD_DELAY_MAX=180"
call :load_env
:menu
cls
echo ============================================================================
echo                 RANKING SHORTS PIPELINE CONTROL PANEL
echo ============================================================================
echo Channel: %RANKING_UPLOAD_CHANNEL%   Privacy: %UPLOAD_PRIVACY%
echo Batch: 6 videos, 3 normal + 3 Others-vs-This-Guy
 echo 1. Run mixed sweep, build and upload
 echo 2. Build normal ranking, no upload
 echo 3. Build contrast ranking, no upload
 echo 4. Upload pending builds
 echo 5. Source and vet clips
 echo 6. Assemble saved plan
 echo 7. Start scheduler daemon
 echo 8. Test environment
 echo 9. Authenticate YouTube channel
 echo 10. Set upload channel
 echo 11. Set topic
 echo 12. Exit
 echo.
set "choice="
set /p "choice=Select: "
if "%choice%"=="1" goto mixed
if "%choice%"=="2" goto normal
if "%choice%"=="3" goto contrast
if "%choice%"=="4" goto upload
if "%choice%"=="5" goto source
if "%choice%"=="6" goto assemble
if "%choice%"=="7" goto schedule
if "%choice%"=="8" goto test
if "%choice%"=="9" goto auth
if "%choice%"=="10" goto set_channel
if "%choice%"=="11" goto set_topic
if "%choice%"=="12" goto done
goto menu
:load_env
if exist "%REPO_DIR%\.env" for /f "usebackq tokens=1,* delims== eol=#" %%A in ("%REPO_DIR%\.env") do if not "%%A"=="" set "%%A=%%B"
if exist "config\.env" for /f "usebackq tokens=1,* delims== eol=#" %%A in ("config\.env") do if not "%%A"=="" set "%%A=%%B"
if defined RANKING_CHANNEL set "RANKING_UPLOAD_CHANNEL=%RANKING_CHANNEL%"
if defined UPLOAD_PRIVACY set "UPLOAD_PRIVACY=%UPLOAD_PRIVACY%"
exit /b 0
:ensure
"%PY%" --version >nul 2>&1
if errorlevel 1 (echo Python not found.&pause&exit /b 1)
exit /b 0
:mixed
call :ensure
if errorlevel 1 goto menu
"%PY%" mixed_sweep.py
pause
goto menu
:normal
call :ensure
if errorlevel 1 goto menu
"%PY%" -m src.main --mode auto --videos 1 --no-upload
pause
goto menu
:contrast
call :ensure
if errorlevel 1 goto menu
set "CONTRAST_SUBJECT=GUY"
set /p "CONTRAST_SUBJECT=Final subject [GUY]: "
if not defined CONTRAST_SUBJECT set "CONTRAST_SUBJECT=GUY"
"%PY%" run_contrast_pipeline.py
pause
goto menu
:upload
call :ensure
if errorlevel 1 goto menu
"%PY%" -m src.main --mode upload
pause
goto menu
:source
call :ensure
if errorlevel 1 goto menu
"%PY%" -m src.main --mode source --topic "%RANKING_TOPIC%"
pause
goto menu
:assemble
set "PLAN="
set /p "PLAN=Plan JSON path, or BACK: "
if /i "%PLAN%"=="BACK" goto menu
if not defined PLAN goto menu
call :ensure
"%PY%" -m src.main --mode assemble --plan "%PLAN%"
pause
goto menu
:schedule
call :ensure
if errorlevel 1 goto menu
"%PY%" -m src.main --mode schedule
pause
goto menu
:test
call :ensure
if errorlevel 1 goto menu
"%PY%" -m src.main --mode test
pause
goto menu
:auth
call :ensure
if errorlevel 1 goto menu
call :set_channel_value
if not defined CHANNEL goto menu
"%PY%" -c "from src.publisher import auth; cid=auth(r'%CHANNEL%'); print('Authenticated channel ID:', cid or 'not returned')"
pause
goto menu
:set_channel
call :set_channel_value
if defined CHANNEL set "RANKING_UPLOAD_CHANNEL=%CHANNEL%"
goto menu
:set_channel_value
set "CHANNEL="
set /p "CHANNEL=Shared Shorts/POV channel key [%RANKING_UPLOAD_CHANNEL%], or BACK: "
if /i "%CHANNEL%"=="BACK" set "CHANNEL="
if not defined CHANNEL set "CHANNEL=%RANKING_UPLOAD_CHANNEL%"
exit /b 0
:set_topic
set "NEW_TOPIC="
set /p "NEW_TOPIC=Topic key or AUTO: "
if /i "%NEW_TOPIC%"=="AUTO" set "NEW_TOPIC=auto"
if defined NEW_TOPIC set "RANKING_TOPIC=%NEW_TOPIC%"
goto menu
:done
endlocal
exit /b 0
