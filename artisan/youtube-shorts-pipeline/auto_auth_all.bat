@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0.."
title YouTube Pipelines - FULL AUTO AUTH (writes channel_id to channels.yaml automatically)
set "ARTISAN_DIR=%CD%"
set "PY=%ARTISAN_DIR%\youtube-shorts-pipeline\venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [ERROR] Python not found at %PY%
    pause
    exit /b 1
)

cd /d "%ARTISAN_DIR%"

cls
echo ============================================================================
echo  FULL AUTO-AUTHENTICATION (SHORTS + RANKING PIPELINES)
echo ============================================================================
echo.
echo This script authenticates ALL 7 channels and AUTOMATICALLY writes
echo the channel_id to artisan/yt-secrets/channels.yaml
echo NO MANUAL COPY-PASTE NEEDED.
echo.
echo CHANNELS TO AUTHENTICATE:
echo.
echo  SHORTS PIPELINE:
echo   1. capital_mindset   -> draallan0@gmail.com
echo   2. wealth_mindset    -> adrasaltsxxx@gmail.com
echo   3. flick_shorts      -> draallan0@gmail.com (borrows wealth_mindset client)
echo   4. chop_ug           -> daadaallan0@gmail.com
echo   5. NXS               -> draallan12@gmail.com  [MUST CONSENT AS draallan12]
echo.
echo  RANKING PIPELINE:
echo   6. rankdrop          -> daadaallan0@gmail.com
echo   7. the_other_guys    -> allandaada@gmail.com
echo.
echo ============================================================================
echo.
echo Each channel will open a browser window for consent.
echo Complete each one before the next starts.
echo.
choice /c YN /m "Start auto-authentication for all 7 channels? [Y/N] "
if errorlevel 2 (
    echo Cancelled.
    pause
    exit /b 0
)

echo.
echo Starting auto-authentication from %ARTISAN_DIR%...
echo.

"%PY%" youtube-shorts-pipeline\auto_auth_all.py

echo.
echo ============================================================================
echo  DONE
echo ============================================================================
echo channels.yaml has been automatically updated with all channel_ids.
echo.
pause