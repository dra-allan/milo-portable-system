@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Ranking Shorts Pipeline - Easy Runner

if not defined RANKING_TOPIC set "RANKING_TOPIC=fishing_moments"
if not defined UPLOAD_PRIVACY set "UPLOAD_PRIVACY=private"
if not defined DRY_RUN set "DRY_RUN=false"
if defined VIDEO_FACTORY_ROOT set "RANKING_RUNTIME=%VIDEO_FACTORY_ROOT%\ranking-shorts-pipeline"
if not defined RANKING_RUNTIME set "RANKING_RUNTIME=%LOCALAPPDATA%\DRA\VideoFactory\ranking-shorts-pipeline"

if not "%~1"=="" goto arg_%~1

:main
cls
echo.
echo ================================================================
echo             Ranking Shorts Pipeline - Easy Runner
echo ================================================================
echo.
echo  Topic       : %RANKING_TOPIC%
echo  Privacy     : %UPLOAD_PRIVACY%
echo  Dry run     : %DRY_RUN%
echo  Runtime     : %RANKING_RUNTIME%
echo.
echo   1. Build ranking video, no upload
 echo  2. Build ranking video and upload
 echo  3. Source and vet YouTube clips only
 echo  4. Assemble a saved plan
 echo  5. Upload pending builds
 echo  6. Test environment
 echo  7. Set topic
 echo  8. Set upload privacy
 echo  9. Toggle dry-run mode
 echo 10. Open runtime folders
 echo 11. Exit
 echo.
set "choice="
set /p "choice=Select an option: "
set "choice=%choice: =%"
if "%choice%"=="1" goto build_private
if "%choice%"=="2" goto build_upload
if "%choice%"=="3" goto source
if "%choice%"=="4" goto assemble
if "%choice%"=="5" goto upload
if "%choice%"=="6" goto test
if "%choice%"=="7" goto set_topic
if "%choice%"=="8" goto set_privacy
if "%choice%"=="9" goto set_dryrun
if "%choice%"=="10" goto folders
if "%choice%"=="11" goto done
echo Invalid choice.
timeout /t 2 >nul
goto main

:arg_1
goto build_private
:arg_2
goto build_upload
:arg_3
goto source
:arg_4
goto assemble
:arg_5
goto upload
:arg_6
goto test

:activate
if exist "venv\Scripts\activate.bat" (
  call "venv\Scripts\activate.bat"
) else (
  echo Missing venv. Run: python -m venv venv ^&^& pip install -r requirements.txt
  pause
  exit /b 1
)
exit /b 0

:build_private
cls
echo Building %RANKING_TOPIC% with upload disabled. Safe first run.
call :activate
python -m src.main --mode once --topic "%RANKING_TOPIC%" --no-upload
pause
goto main

:build_upload
cls
echo Building %RANKING_TOPIC% and uploading with current privacy settings.
call :activate
python -m src.main --mode once --topic "%RANKING_TOPIC%"
pause
goto main

:source
cls
echo Discovering and vetting YouTube candidates for %RANKING_TOPIC%.
call :activate
python -m src.main --mode source --topic "%RANKING_TOPIC%"
pause
goto main

:assemble
cls
set "plan="
set /p "plan=Plan JSON path, or BACK: "
if /i "%plan%"=="BACK" goto main
if "%plan%"=="" goto assemble
call :activate
python -m src.main --mode assemble --plan "%plan%"
pause
goto main

:upload
cls
echo Uploading rendered builds waiting in the external runtime queue.
call :activate
python -m src.main --mode upload
pause
goto main

:test
cls
echo Running dependency, FFmpeg, font and topic checks.
call :activate
python -m src.main --mode test
pause
goto main

:set_topic
cls
echo Current topic: %RANKING_TOPIC%
echo.
set "new_topic="
set /p "new_topic=Topic key, or BACK: "
if /i "%new_topic%"=="BACK" goto main
if not "%new_topic%"=="" call :update_env RANKING_TOPIC %new_topic%
if not "%new_topic%"=="" set "RANKING_TOPIC=%new_topic%"
goto main

:set_privacy
cls
echo Current upload privacy: %UPLOAD_PRIVACY%
echo 1. private
echo 2. unlisted
echo 3. public
echo 0. back
set "privacy_choice="
set /p "privacy_choice=Select: "
if "%privacy_choice%"=="0" goto main
set "new_privacy="
if "%privacy_choice%"=="1" set "new_privacy=private"
if "%privacy_choice%"=="2" set "new_privacy=unlisted"
if "%privacy_choice%"=="3" set "new_privacy=public"
if defined new_privacy call :update_env UPLOAD_PRIVACY %new_privacy%
if defined new_privacy set "UPLOAD_PRIVACY=%new_privacy%"
goto main

:set_dryrun
cls
echo Current dry-run: %DRY_RUN%
echo 1. Enable
echo 2. Disable
echo 0. Back
set "dry_choice="
set /p "dry_choice=Select: "
if "%dry_choice%"=="0" goto main
if "%dry_choice%"=="1" call :update_env DRY_RUN true
if "%dry_choice%"=="1" set "DRY_RUN=true"
if "%dry_choice%"=="2" call :update_env DRY_RUN false
if "%dry_choice%"=="2" set "DRY_RUN=false"
goto main

:folders
cls
echo Runtime folders are outside the repo:
echo   Data  : %RANKING_RUNTIME%\data
echo   Temp  : %RANKING_RUNTIME%\temp
echo   Output: %RANKING_RUNTIME%\output
if exist "%RANKING_RUNTIME%\data" start "Ranking data" "%RANKING_RUNTIME%\data"
if exist "%RANKING_RUNTIME%\temp" start "Ranking temp" "%RANKING_RUNTIME%\temp"
if exist "%RANKING_RUNTIME%\output" start "Ranking output" "%RANKING_RUNTIME%\output"
pause
goto main

:update_env
if exist config\.env (findstr /v /i "%1=" config\.env > config\.env.tmp) else (type nul > config\.env.tmp)
echo %1=%2>> config\.env.tmp
move /y config\.env.tmp config\.env >nul
exit /b 0

:done
endlocal
exit /b 0
