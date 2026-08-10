@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Ranking Shorts Pipeline - Easy Runner
if not defined RANKING_TOPIC set "RANKING_TOPIC=auto"
if not defined UPLOAD_PRIVACY set "UPLOAD_PRIVACY=private"
rem Load config\.env so the menu path matches python's runtime root.
rem Values are KEY=VALUE with no quoting; lines with an empty value are skipped.
if exist "config\.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("config\.env") do if not "%%b"=="" set "%%a=%%b"
if defined VIDEO_FACTORY_ROOT set "RANKING_RUNTIME=%VIDEO_FACTORY_ROOT%\ranking-shorts-pipeline"
if not defined RANKING_RUNTIME set "RANKING_RUNTIME=%LOCALAPPDATA%\DRA\VideoFactory\ranking-shorts-pipeline"
:main
cls
echo ================================================================
echo             Ranking Shorts Pipeline - Easy Runner
echo ================================================================
echo Topic: %RANKING_TOPIC%   Privacy: %UPLOAD_PRIVACY%
echo Runtime: %RANKING_RUNTIME%
echo.
echo 1. Build, no upload
echo 2. Build and upload
echo 3. Source and vet YouTube clips
echo 4. Assemble saved plan
echo 5. Upload pending builds
echo 6. Test environment
echo 7. Set topic
echo 8. Open runtime folders
echo 9. Delete already-uploaded local videos
echo 10. Exit
echo.
set /p choice="Select: "
if "%choice%"=="1" goto build_private
if "%choice%"=="2" goto build_upload
if "%choice%"=="3" goto source
if "%choice%"=="4" goto assemble
if "%choice%"=="5" goto upload
if "%choice%"=="6" goto test
if "%choice%"=="7" goto set_topic
if "%choice%"=="8" goto folders
if "%choice%"=="9" goto cleanup_uploaded
if "%choice%"=="10" goto done
goto main
:ensure_env
if not exist venv\Scripts\python.exe (
 echo No virtual environment found. Creating it now...
 python -m venv venv
 if errorlevel 1 (echo Could not create venv.&pause&exit /b 1)
)
venv\Scripts\python.exe -c "import yaml, yt_dlp" >nul 2>&1
if errorlevel 1 (
 echo Ranking dependencies are incomplete. Installing requirements now...
 venv\Scripts\python.exe -m pip install --upgrade pip
 venv\Scripts\python.exe -m pip install -r requirements.txt
 if errorlevel 1 (echo Dependency install failed. Fix the error above and retry.&pause&exit /b 1)
 venv\Scripts\python.exe -c "import yaml, yt_dlp" >nul 2>&1
 if errorlevel 1 (echo Dependency verification failed: PyYAML or yt-dlp is still missing.&pause&exit /b 1)
)
exit /b 0
:start_timer
set "RUN_START=%TIME%"
echo.
echo [START] %~1 at %RUN_START%
exit /b 0
:stop_timer
set "RUN_END=%TIME%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[datetime]::ParseExact('%RUN_START%','HH:mm:ss.ff',$null);$e=[datetime]::ParseExact('%RUN_END%','HH:mm:ss.ff',$null);if($e -lt $s){$e=$e.AddDays(1)};$d=$e-$s;Write-Host ('[DONE] '+('%~1')+' | elapsed '+('{0:00}:{1:00}:{2:00}' -f [int]$d.TotalHours,$d.Minutes,$d.Seconds)) -ForegroundColor Green"
echo.
exit /b 0
:python
venv\Scripts\python.exe %*
exit /b %errorlevel%
:build_private
call :start_timer "build no-upload"
call :ensure_env
if errorlevel 1 goto main
if /i "%RANKING_TOPIC%"=="auto" (call :python -m src.main --mode auto --no-upload) else (call :python -m src.main --mode once --topic "%RANKING_TOPIC%" --no-upload)
call :python cleanup_runtime.py
call :stop_timer "build no-upload"
pause
goto main
:build_upload
call :start_timer "build and upload"
call :ensure_env
if errorlevel 1 goto main
if /i "%RANKING_TOPIC%"=="auto" (call :python -m src.main --mode auto) else (call :python -m src.main --mode once --topic "%RANKING_TOPIC%")
call :python cleanup_runtime.py
call :stop_timer "build and upload"
pause
goto main
:source
call :start_timer "source and vet"
call :ensure_env
if errorlevel 1 goto main
if /i "%RANKING_TOPIC%"=="auto" (call :python -m src.main --mode source --topic animal_moments) else (call :python -m src.main --mode source --topic "%RANKING_TOPIC%")
call :stop_timer "source and vet"
pause
goto main
:assemble
set /p plan="Plan JSON path, or BACK: "
if /i "%plan%"=="BACK" goto main
call :start_timer "assemble plan"
call :ensure_env
if errorlevel 1 goto main
call :python -m src.main --mode assemble --plan "%plan%"
call :python cleanup_runtime.py
call :stop_timer "assemble plan"
pause
goto main
:upload
call :start_timer "upload pending"
call :ensure_env
if errorlevel 1 goto main
call :python -m src.main --mode upload
call :stop_timer "upload pending"
pause
goto main
:test
call :start_timer "environment test"
call :ensure_env
if errorlevel 1 goto main
call :python -m src.main --mode test
call :stop_timer "environment test"
pause
goto main
:set_topic
set /p new_topic="Topic key, or AUTO or BACK: "
if /i "%new_topic%"=="BACK" goto main
if /i "%new_topic%"=="AUTO" set "new_topic=auto"
if not "%new_topic%"=="" set "RANKING_TOPIC=%new_topic%"
goto main
:folders
start "Ranking data" "%RANKING_RUNTIME%\data"
start "Ranking temp" "%RANKING_RUNTIME%\temp"
start "Ranking output" "%RANKING_RUNTIME%\output"
goto main
:cleanup_uploaded
call :start_timer "delete uploaded local videos"
call :ensure_env
if errorlevel 1 goto main
call :python cleanup_uploaded.py
call :stop_timer "delete uploaded local videos"
pause
goto main
:done
endlocal
exit /b 0
