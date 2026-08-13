@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Ranking Shorts Pipeline Control Panel
set "PIPE_DIR=%~dp0"
set "REPO_DIR=%~dp0..\.."
set "PY=%REPO_DIR%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%PIPE_DIR%venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
rem ---- run shape ----
set "RANKING_TOPIC=auto"
set "RANKING_VARIANT=mixed"
set "RANKING_VIDEOS_PER_RUN=3"
set "RANKING_SWEEP_VIDEOS=3"
set "AUTO_UPLOAD=true"
set "UPLOAD_PRIVACY=public"
rem ---- speed ----
set "RANKING_FAST_MODE=true"
set "RANKING_RENDER_WORKERS=2"
set "RANKING_REJECT_BUDGET=20"
rem ---- disk hygiene: purge sources after a build, delete exports once posted
set "RANKING_CLEANUP_AFTER_BUILD=true"
set "RANKING_DELETE_AFTER_UPLOAD=true"
rem ---- channels and caps ----
set "RANKING_CHANNEL_PROFILES=RankDrop:normal,the other guys:contrast"
set "RANKING_UPLOAD_CHANNEL=RankDrop"
set "UPLOAD_MAX_PER_DAY=6"
set "RANKING_UPLOAD_MAX_PER_DAY=6"
set "RANKING_UPLOAD_DELAY_MIN=45"
set "RANKING_UPLOAD_DELAY_MAX=180"
set "CONTRAST_SUBJECT=GUY"
call :load_env
if defined VIDEO_FACTORY_ROOT set "RANKING_RUNTIME=%VIDEO_FACTORY_ROOT%\ranking-shorts-pipeline"
if not defined RANKING_RUNTIME set "RANKING_RUNTIME=%LOCALAPPDATA%\DRA\VideoFactory\ranking-shorts-pipeline"
:menu
cls
echo ============================================================================
echo                 RANKING SHORTS PIPELINE CONTROL PANEL
echo ============================================================================
echo Topic: %RANKING_TOPIC%    Content: %RANKING_VARIANT%    Privacy: %UPLOAD_PRIVACY%
echo Videos per run: %RANKING_VIDEOS_PER_RUN%    Upload after run: %AUTO_UPLOAD%
echo Cap: %UPLOAD_MAX_PER_DAY% uploads / rolling 24h    Profiles: %RANKING_CHANNEL_PROFILES%
echo Contrast subject: %CONTRAST_SUBJECT%    Fast vetting: %RANKING_FAST_MODE%    Workers: %RANKING_RENDER_WORKERS%
echo Purge after build: %RANKING_CLEANUP_AFTER_BUILD%    Delete after upload: %RANKING_DELETE_AFTER_UPLOAD%
echo Runtime: %RANKING_RUNTIME%
echo.
echo  RUN
echo   1. Run now: %RANKING_VIDEOS_PER_RUN% video/s, %RANKING_VARIANT%, upload=%AUTO_UPLOAD%
echo   2. Build normal ranking only, no upload
echo   3. Build contrast ranking only, no upload
echo   4. Configured mixed sweep across channels
echo   5. One queue sweep: backlog, refill, post
echo   6. Start scheduler daemon
echo.
echo  PUBLISH
echo   7. Upload pending builds, capped and %UPLOAD_PRIVACY%
echo   8. Authenticate a channel
echo.
echo  SETTINGS
echo   9. Set topic, key or AUTO
echo  10. Set videos per run
echo  11. Set content type: normal, contrast, mixed
echo  12. Toggle upload after run
echo  13. Toggle privacy public/private
echo  14. Toggle fast vetting
echo  15. Set channel profiles
echo  16. Set contrast subject
echo  17. Toggle purge after build
echo  18. Toggle delete after upload
echo.
echo  MAINTENANCE
echo  19. Purge runtime assets now
echo  20. Delete uploaded local videos now
echo  21. Source and vet only, no render
echo  22. Assemble a saved plan
echo  23. Test environment
echo  24. Compile Python
echo  25. Open runtime folders
echo  26. Stop scheduler
echo  27. Reset upload caps
echo  28. Exit
echo.
set "choice="
set /p "choice=Select: "
if "%choice%"=="1" goto run_now
if "%choice%"=="2" goto build_normal
if "%choice%"=="3" goto build_contrast
if "%choice%"=="4" goto mixed
if "%choice%"=="5" goto sweep
if "%choice%"=="6" goto schedule
if "%choice%"=="7" goto upload
if "%choice%"=="8" goto auth
if "%choice%"=="9" goto set_topic
if "%choice%"=="10" goto set_videos
if "%choice%"=="11" goto set_variant
if "%choice%"=="12" goto toggle_upload
if "%choice%"=="13" goto toggle_privacy
if "%choice%"=="14" goto toggle_fast
if "%choice%"=="15" goto profiles
if "%choice%"=="16" goto subject
if "%choice%"=="17" goto toggle_purge
if "%choice%"=="18" goto toggle_delete
if "%choice%"=="19" goto purge_now
if "%choice%"=="20" goto cleanup_uploaded
if "%choice%"=="21" goto source
if "%choice%"=="22" goto assemble
if "%choice%"=="23" goto test
if "%choice%"=="24" goto compile_check
if "%choice%"=="25" goto folders
if "%choice%"=="26" goto stop_daemon
if "%choice%"=="27" goto reset_caps
if "%choice%"=="28" goto done
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
:run_now
set "UP="
if /i "%AUTO_UPLOAD%"=="false" set "UP=--no-upload"
call :start_timer "run %RANKING_VIDEOS_PER_RUN% video/s"
if /i "%RANKING_TOPIC%"=="auto" (call :run --mode auto --videos %RANKING_VIDEOS_PER_RUN% --variant %RANKING_VARIANT% %UP%) else (call :run --mode once --topic "%RANKING_TOPIC%" --variant %RANKING_VARIANT% %UP%)
call :stop_timer "run %RANKING_VIDEOS_PER_RUN% video/s"
pause
goto menu
:build_normal
call :start_timer "build normal, no upload"
if /i "%RANKING_TOPIC%"=="auto" (call :run --mode auto --videos %RANKING_VIDEOS_PER_RUN% --variant normal --no-upload) else (call :run --mode once --topic "%RANKING_TOPIC%" --variant normal --no-upload)
call :stop_timer "build normal, no upload"
pause
goto menu
:build_contrast
call :start_timer "build contrast, no upload"
if /i "%RANKING_TOPIC%"=="auto" (call :run --mode auto --videos %RANKING_VIDEOS_PER_RUN% --variant contrast --no-upload) else (call :run --mode once --topic "%RANKING_TOPIC%" --variant contrast --no-upload)
call :stop_timer "build contrast, no upload"
pause
goto menu
:mixed
call :ensure_python
if errorlevel 1 goto menu
call :start_timer "mixed sweep"
"%PY%" mixed_sweep.py
call :stop_timer "mixed sweep"
pause
goto menu
:sweep
call :start_timer "queue sweep"
call :run --mode sweep --variant %RANKING_VARIANT% --videos %RANKING_SWEEP_VIDEOS%
call :stop_timer "queue sweep"
pause
goto menu
:schedule
call :start_timer "scheduler daemon"
call :run --mode schedule --variant %RANKING_VARIANT%
call :stop_timer "scheduler daemon"
pause
goto menu
:upload
call :ensure_python
if errorlevel 1 goto menu
call :start_timer "upload pending"
"%PY%" upload_pending_public.py
call :stop_timer "upload pending"
pause
goto menu
:auth
set "CHANNEL="
set /p "CHANNEL=Channel key to authenticate, or BACK: "
if /i "%CHANNEL%"=="BACK" goto menu
if not defined CHANNEL goto menu
call :run --mode auth --channel "%CHANNEL%"
pause
goto menu
:set_topic
set "new_topic="
set /p "new_topic=Topic key, or AUTO or BACK: "
if /i "%new_topic%"=="BACK" goto menu
if /i "%new_topic%"=="AUTO" set "new_topic=auto"
if defined new_topic set "RANKING_TOPIC=%new_topic%"
goto menu
:set_videos
set "new_videos="
set /p "new_videos=Videos per run 1-15, or BACK: "
if /i "%new_videos%"=="BACK" goto menu
if not defined new_videos goto menu
set "new_videos=%new_videos: =%"
set "BAD_V="
for /f "tokens=* delims=0123456789" %%I in ("%new_videos%") do set "BAD_V=%%I"
if defined BAD_V (echo Not a number: %new_videos%&set "BAD_V="&pause&goto menu)
if %new_videos% LSS 1 goto menu
if %new_videos% GTR 15 goto menu
set "RANKING_VIDEOS_PER_RUN=%new_videos%"
set "RANKING_SWEEP_VIDEOS=%new_videos%"
goto menu
:set_variant
set "new_variant="
set /p "new_variant=Content type - normal, contrast, mixed, or BACK: "
if /i "%new_variant%"=="normal" set "RANKING_VARIANT=normal"
if /i "%new_variant%"=="contrast" set "RANKING_VARIANT=contrast"
if /i "%new_variant%"=="mixed" set "RANKING_VARIANT=mixed"
goto menu
:toggle_upload
if /i "%AUTO_UPLOAD%"=="true" (set "AUTO_UPLOAD=false") else (set "AUTO_UPLOAD=true")
goto menu
:toggle_privacy
if /i "%UPLOAD_PRIVACY%"=="public" (set "UPLOAD_PRIVACY=private") else (set "UPLOAD_PRIVACY=public")
goto menu
:toggle_fast
if /i "%RANKING_FAST_MODE%"=="true" (set "RANKING_FAST_MODE=false") else (set "RANKING_FAST_MODE=true")
goto menu
:toggle_purge
if /i "%RANKING_CLEANUP_AFTER_BUILD%"=="true" (set "RANKING_CLEANUP_AFTER_BUILD=false") else (set "RANKING_CLEANUP_AFTER_BUILD=true")
goto menu
:toggle_delete
if /i "%RANKING_DELETE_AFTER_UPLOAD%"=="true" (set "RANKING_DELETE_AFTER_UPLOAD=false") else (set "RANKING_DELETE_AFTER_UPLOAD=true")
goto menu
:profiles
set "NEW_PROFILES="
set /p "NEW_PROFILES=Profiles, e.g. RankDrop:normal,the other guys:contrast: "
if defined NEW_PROFILES set "RANKING_CHANNEL_PROFILES=%NEW_PROFILES%"
goto menu
:subject
set "NEW_SUBJECT="
set /p "NEW_SUBJECT=Contrast subject, e.g. GUY, DOG, PRO: "
if defined NEW_SUBJECT set "CONTRAST_SUBJECT=%NEW_SUBJECT%"
goto menu
:purge_now
call :ensure_python
if errorlevel 1 goto menu
"%PY%" cleanup_runtime.py
pause
goto menu
:cleanup_uploaded
call :ensure_python
if errorlevel 1 goto menu
"%PY%" cleanup_uploaded.py
pause
goto menu
:source
call :start_timer "source and vet"
call :run --mode source --topic "%RANKING_TOPIC%"
call :stop_timer "source and vet"
pause
goto menu
:assemble
echo Note: re-rendering needs the source clips, so keep purge off for that run.
set "plan="
set /p "plan=Plan JSON path, or BACK: "
if /i "%plan%"=="BACK" goto menu
if not defined plan goto menu
call :start_timer "assemble plan"
call :run --mode assemble --plan "%plan%"
call :stop_timer "assemble plan"
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
:folders
if not exist "%RANKING_RUNTIME%\data" mkdir "%RANKING_RUNTIME%\data"
if not exist "%RANKING_RUNTIME%\temp" mkdir "%RANKING_RUNTIME%\temp"
if not exist "%RANKING_RUNTIME%\output" mkdir "%RANKING_RUNTIME%\output"
start "Ranking data" "%RANKING_RUNTIME%\data"
start "Ranking temp" "%RANKING_RUNTIME%\temp"
start "Ranking output" "%RANKING_RUNTIME%\output"
goto menu
:stop_daemon
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root=[IO.Path]::GetFullPath('%PIPE_DIR%');$ps=Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ? { $_.CommandLine -and $_.CommandLine -like ('*'+$root+'*src.main*') -and $_.CommandLine -like '*--mode schedule*' };if(-not $ps){'No ranking scheduler found.'}else{$ps|%%{ 'Stopping PID '+$_.ProcessId;Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
pause
goto menu
:reset_caps
call :ensure_python
if errorlevel 1 goto menu
call :start_timer "reset upload caps"
"%PY%" reset_caps.py
call :stop_timer "reset upload caps"
pause
goto menu
:done
endlocal
exit /b 0
