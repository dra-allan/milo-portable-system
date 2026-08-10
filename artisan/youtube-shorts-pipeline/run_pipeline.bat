@echo off
title YouTube Shorts Pipeline - Easy Runner
set "SCHEDULE_MAX_VIDEOS=1"
set "SCHEDULE_MAX_TOTAL=0"
set "BACKGROUND_MODE=smart"
set "CAPTION_STYLE=hormozi"
if exist .env (
  for /f "tokens=1,* delims==" %%a in ('findstr /b /i "BACKGROUND_MODE=" .env') do set "BACKGROUND_MODE=%%b"
  for /f "tokens=1,* delims==" %%a in ('findstr /b /i "CAPTION_STYLE=" .env') do set "CAPTION_STYLE=%%b"
)
if not "%~1"=="" (
 if "%~1"=="1" goto full_sweep
 if "%~1"=="2" goto url
 if "%~1"=="3" goto schedule
 if "%~1"=="4" goto upload_existing
 if "%~1"=="5" goto library
 if "%~1"=="6" goto test
 if "%~1"=="7" goto set_background
 if "%~1"=="8" goto stats_report
 if "%~1"=="9" goto set_caption
 if "%~1"=="10" goto reset_caps
 if "%~1"=="11" goto exit_script
)
:main
cls
echo ================================================================
echo           YouTube Shorts Pipeline - Easy Runner
echo ================================================================
echo  Background: %BACKGROUND_MODE%   Captions: %CAPTION_STYLE%
echo  Source cap: 1 video per niche per sweep/day
echo.
echo  1. Run Full Sweep
echo  2. Process YouTube URL / ID
echo  3. Scheduled Mode
echo  4. Upload Existing Shorts
echo  5. Process Library
echo  6. Test Mode
echo  7. Set Background Mode
echo  8. Stats Report
echo  9. Set Caption Style
echo 10. Reset Caps / Dead Channels
echo 11. Exit
echo.
set /p choice="Select: "
if "%choice%"=="1" goto full_sweep
if "%choice%"=="2" goto url
if "%choice%"=="3" goto schedule
if "%choice%"=="4" goto upload_existing
if "%choice%"=="5" goto library
if "%choice%"=="6" goto test
if "%choice%"=="7" goto set_background
if "%choice%"=="8" goto stats_report
if "%choice%"=="9" goto set_caption
if "%choice%"=="10" goto reset_caps
if "%choice%"=="11" goto exit_script
goto main
:full_sweep
call venv\Scripts\activate
python -m src.main --mode once --videos 1
pause
goto main
:schedule
call venv\Scripts\activate
python -m src.main --mode schedule
pause
goto main
:test
call venv\Scripts\activate
python -m src.main --mode test
pause
goto main
:upload_existing
cls
echo 1. Upload by Niche
echo 2. Upload by Channel
echo 3. Upload All Pending
echo 0. Back
set /p ue="Select: "
if "%ue%"=="0" goto main
if "%ue%"=="1" goto upload_niche
if "%ue%"=="2" goto upload_channel
if "%ue%"=="3" goto upload_all
goto upload_existing
:upload_niche
set /p niche="Niche: "
if "%niche%"=="" goto upload_existing
call venv\Scripts\activate
python -m src.safe_upload --niche "%niche%"
pause
goto upload_existing
:upload_channel
set /p channel="Channel: "
if "%channel%"=="" goto upload_existing
call venv\Scripts\activate
python -m src.safe_upload --channel "%channel%"
pause
goto upload_existing
:upload_all
call venv\Scripts\activate
python -m src.safe_upload
pause
goto upload_existing
:url
set /p url="YouTube URL or ID: "
if "%url%"=="" goto main
call venv\Scripts\activate
python -m src.main --mode once "%url%"
pause
goto main
:library
call venv\Scripts\activate
python -m src.main --mode library
pause
goto main
:stats_report
call venv\Scripts\activate
python -m src.main --mode stats --stats-age-hours 0
pause
goto main
:set_background
set /p mode="Background (crop/blur/cheap/black/smart): "
if not "%mode%"=="" call :update_env BACKGROUND_MODE %mode%
goto main
:set_caption
set /p style="Caption style: "
if not "%style%"=="" call :update_env CAPTION_STYLE %style%
goto main
:reset_caps
call venv\Scripts\activate
python reset_caps.py
pause
goto main
:update_env
if exist .env (findstr /v /i "%1=" .env > .env.tmp) else (type nul > .env.tmp)
echo %1=%2>> .env.tmp
move /y .env.tmp .env >nul
goto :eof
:exit_script
exit
