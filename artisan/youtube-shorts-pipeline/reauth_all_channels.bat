@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
title YouTube Pipelines - Complete Channel Re-Authentication (Shorts + Ranking)
set "PIPE_DIR=%~dp0"
set "PY=%PIPE_DIR%venv\Scripts\python.exe"
set "RANKING_PIPE_DIR=%PIPE_DIR%..\ranking-shorts-pipeline\"
set "RANKING_PY=%RANKING_PIPE_DIR%venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [ERROR] Shorts pipeline Python not found at %PY%
    pause
    exit /b 1
)
if not exist "%RANKING_PY%" (
    echo [ERROR] Ranking pipeline Python not found at %RANKING_PY%
    pause
    exit /b 1
)

cls
echo ============================================================================
echo  COMPLETE CHANNEL RE-AUTHENTICATION (SHORTS + RANKING PIPELINES)
echo ============================================================================
echo.
echo This script re-authenticates ALL expired OAuth tokens for BOTH pipelines.
echo Each channel requires signing in with a SPECIFIC Google account.
echo.
echo TOKEN STATUS (checked 2026-08-23):
echo.
echo  SHORTS PIPELINE (all EXPIRED 2026-08-18):
echo   1. capital_mindset   -> sign in as: draallan0@gmail.com
echo   2. wealth_mindset    -> sign in as: adrasaltsxxx@gmail.com
echo   3. flick_shorts      -> sign in as: draallan0@gmail.com (borrows wealth_mindset client)
echo   4. chop_ug           -> sign in as: daadaallan0@gmail.com
echo   5. NXS               -> sign in as: draallan12@gmail.com  [MUST CONSENT AS draallan12]
echo.
echo  RANKING PIPELINE (EXPIRED 2026-08-16):
echo   6. RankDrop          -> sign in as: daadaallan0@gmail.com
echo   7. the_other_guys    -> sign in as: allandaada@gmail.com
echo.
echo  OPTIONS:
echo   8. Authenticate ALL 7 channels (sequential)
echo   9. Check token status for ALL channels
echo   0. Exit
echo.
echo ============================================================================

:menu
set "choice="
set /p "choice=Select (0-9): "

if "%choice%"=="1" goto auth_capital
if "%choice%"=="2" goto auth_wealth
if "%choice%"=="3" goto auth_flick
if "%choice%"=="4" goto auth_chop
if "%choice%"=="5" goto auth_nxs
if "%choice%"=="6" goto auth_rankdrop
if "%choice%"=="7" goto auth_otherguys
if "%choice%"=="8" goto auth_all
if "%choice%"=="9" goto status_all
if "%choice%"=="0" goto done
echo Invalid choice.
goto menu

:auth_capital
cls
echo ============================================================================
echo  [1/7] AUTHENTICATE: capital_mindset (SHORTS)
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
echo  [2/7] AUTHENTICATE: wealth_mindset (SHORTS)
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
echo  [3/7] AUTHENTICATE: flick_shorts (SHORTS)
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
echo  [4/7] AUTHENTICATE: chop_ug (SHORTS)
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
echo  [5/7] AUTHENTICATE: NXS (SHORTS)  --  CRITICAL: MUST CONSENT AS draallan12
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

:auth_rankdrop
cls
echo ============================================================================
echo  [6/7] AUTHENTICATE: RankDrop (RANKING)
echo ============================================================================
echo.
echo SIGN IN AS: daadaallan0@gmail.com
echo.
echo This is the main ranking channel (normal variant = TOP-N countdowns).
echo Opening browser for consent... approve access for "RankDrop".
echo.
"%PY%" -m yt_secrets auth --channel rankdrop
echo.
echo If successful, COPY the channel_id printed above and paste it into:
echo   artisan/yt-secrets/channels.yaml  (under rankdrop: channel_id: 'PASTE_HERE')
echo.
pause
goto menu

:auth_otherguys
cls
echo ============================================================================
echo  [7/7] AUTHENTICATE: the_other_guys (RANKING)
echo ============================================================================
echo.
echo SIGN IN AS: allandaada@gmail.com
echo.
echo This is the contrast variant channel (OTHERS VS THIS GUY clips).
echo Opening browser for consent... approve access for "the_other_guys".
echo.
"%PY%" -m yt_secrets auth --channel the_other_guys
echo.
echo If successful, COPY the channel_id printed above and paste it into:
echo   artisan/yt-secrets/channels.yaml  (under the_other_guys: channel_id: 'PASTE_HERE')
echo.
pause
goto menu

:auth_all
cls
echo ============================================================================
echo  AUTHENTICATE ALL 7 CHANNELS (SEQUENTIAL)
echo ============================================================================
echo.
echo This will run all 7 authentications one after another.
echo You must complete each browser consent before the next starts.
echo.
choice /c YN /m "Continue? [Y/N] "
if errorlevel 2 goto menu

call :auth_capital
call :auth_wealth
call :auth_flick
call :auth_chop
call :auth_nxs
call :auth_rankdrop
call :auth_otherguys

echo.
echo ============================================================================
echo  ALL 7 AUTHENTICATIONS COMPLETE
echo ============================================================================
echo Remember to paste each channel_id into artisan/yt-secrets/channels.yaml
echo.
pause
goto menu

:status_all
cls
echo ============================================================================
echo  TOKEN STATUS CHECK - ALL CHANNELS
echo ============================================================================
echo.
echo --- SHORTS PIPELINE ---
"%PY%" -m yt_secrets status
echo.
echo --- RANKING PIPELINE (same identity module) ---
"%PY%" -m yt_secrets status --channel rankdrop
"%PY%" -m yt_secrets status --channel the_other_guys
echo.
pause
goto menu

:done
endlocal
exit /b 0