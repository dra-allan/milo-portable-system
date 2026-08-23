@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
title YouTube Shorts Pipeline - Channel Re-Authentication
set "PIPE_DIR=%~dp0"
set "PY=%PIPE_DIR%venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [ERROR] Python not found at %PY%
    pause
    exit /b 1
)

cls
echo ============================================================================
echo  YOUTUBE SHORTS PIPELINE - CHANNEL RE-AUTHENTICATION
echo ============================================================================
echo.
echo This script re-authenticates expired OAuth tokens for Shorts channels.
echo Each channel requires signing in with a SPECIFIC Google account.
echo.
echo EXPIRED CHANNELS (tokens expired 2026-08-18):
echo.
echo  1. capital_mindset   -> sign in as: draallan0@gmail.com
echo  2. wealth_mindset    -> sign in as: adrasaltsxxx@gmail.com
echo  3. flick_shorts      -> sign in as: draallan0@gmail.com (borrows wealth_mindset client)
echo  4. chop_ug           -> sign in as: daadaallan0@gmail.com
echo  5. NXS               -> sign in as: draallan12@gmail.com  [MUST CONSENT AS draallan12]
echo.
echo  6. Authenticate ALL expired channels (sequential)
echo  7. Check token status for all channels
echo  8. Exit
echo.
echo ============================================================================

:menu
set "choice="
set /p "choice=Select (1-8): "

if "%choice%"=="1" goto auth_capital
if "%choice%"=="2" goto auth_wealth
if "%choice%"=="3" goto auth_flick
if "%choice%"=="4" goto auth_chop
if "%choice%"=="5" goto auth_nxs
if "%choice%"=="6" goto auth_all
if "%choice%"=="7" goto status_all
if "%choice%"=="8" goto done
echo Invalid choice.
goto menu

:auth_capital
cls
echo ============================================================================
echo  AUTHENTICATE: capital_mindset
echo ============================================================================
echo.
echo SIGN IN AS: draallan0@gmail.com
echo.
echo Opening browser for consent... approve access for "capital_mindset".
echo.
"%PY%" -m yt_secrets auth --channel capital_mindset
echo.
echo If successful, COPY the channel_id printed above and paste it into:
echo   artisan/yt-secrets/channels.yaml  (under capital_mindset: channel_id: 'PASTE_HERE')
echo.
pause
goto menu

:auth_wealth
cls
echo ============================================================================
echo  AUTHENTICATE: wealth_mindset
echo ============================================================================
echo.
echo SIGN IN AS: adrasaltsxxx@gmail.com
echo.
echo Opening browser for consent... approve access for "wealth_mindset".
echo.
"%PY%" -m yt_secrets auth --channel wealth_mindset
echo.
echo If successful, COPY the channel_id printed above and paste it into:
echo   artisan/yt-secrets/channels.yaml  (under wealth_mindset: channel_id: 'PASTE_HERE')
echo.
pause
goto menu

:auth_flick
cls
echo ============================================================================
echo  AUTHENTICATE: flick_shorts
echo ============================================================================
echo.
echo SIGN IN AS: draallan0@gmail.com
echo.
echo NOTE: This channel's own OAuth client was DELETED in Google Cloud.
echo It BORROWS wealth_mindset's client (612279340654-oo4sgk4imvrf...).
echo Opening browser for consent... approve access for "flick_shorts".
echo.
"%PY%" -m yt_secrets auth --channel flick_shorts
echo.
echo If successful, COPY the channel_id printed above and paste it into:
echo   artisan/yt-secrets/channels.yaml  (under flick_shorts: channel_id: 'PASTE_HERE')
echo.
pause
goto menu

:auth_chop
cls
echo ============================================================================
echo  AUTHENTICATE: chop_ug
echo ============================================================================
echo.
echo SIGN IN AS: daadaallan0@gmail.com
echo.
echo Opening browser for consent... approve access for "chop_ug".
echo.
"%PY%" -m yt_secrets auth --channel chop_ug
echo.
echo If successful, COPY the channel_id printed above and paste it into:
echo   artisan/yt-secrets/channels.yaml  (under chop_ug: channel_id: 'PASTE_HERE')
echo.
pause
goto menu

:auth_nxs
cls
echo ============================================================================
echo  AUTHENTICATE: NXS  (CRITICAL: MUST CONSENT AS draallan12@gmail.com)
echo ============================================================================
echo.
echo SIGN IN AS: draallan12@gmail.com
echo.
echo WARNING: This channel is owned by draallan12 but uses the adrasaltsxxx project.
echo The consent screen MUST be approved while signed in as draallan12.
echo If you approve as adrasaltsxxx, the token will bind to the WRONG CHANNEL.
echo.
echo Opening browser for consent... approve access for "NXS".
echo.
"%PY%" -m yt_secrets auth --channel NXS
echo.
echo If successful, COPY the channel_id printed above and paste it into:
echo   artisan/yt-secrets/channels.yaml  (under NXS: channel_id: 'PASTE_HERE')
echo.
pause
goto menu

:auth_all
cls
echo ============================================================================
echo  AUTHENTICATE ALL EXPIRED CHANNELS (SEQUENTIAL)
echo ============================================================================
echo.
echo This will run all 5 authentications one after another.
echo You must complete each browser consent before the next starts.
echo.
choice /c YN /m "Continue? [Y/N] "
if errorlevel 2 goto menu

call :auth_capital
call :auth_wealth
call :auth_flick
call :auth_chop
call :auth_nxs

echo.
echo ============================================================================
echo  ALL AUTHENTICATIONS COMPLETE
echo ============================================================================
echo Remember to paste each channel_id into artisan/yt-secrets/channels.yaml
echo.
pause
goto menu

:status_all
cls
echo ============================================================================
echo  TOKEN STATUS CHECK
echo ============================================================================
echo.
"%PY%" -m yt_secrets status
echo.
pause
goto menu

:done
endlocal
exit /b 0