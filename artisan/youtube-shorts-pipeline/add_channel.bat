@echo off
title YouTube Shorts Pipeline - Add / Authenticate Channel
cd /d "%~dp0"

echo.
echo ================================================================
echo           Add / Authenticate a New Upload Channel
echo ================================================================
echo.
echo This signs into a NEW YouTube channel so the pipeline can post to it.
echo A browser window will open -- log in and grant access there.
echo.
echo Type 'exit' to cancel.
echo.

:getname
set "name="
set /p name="Channel name (e.g. wealth_mindset, no spaces): "
set "name=%name: =%"
if /i "%name%"=="exit" exit
if "%name%"=="" goto getname

:getniche
set "niche="
set /p niche="Bind to niche? (Enter to skip, e.g. capital_mindset): "
set "niche=%niche: =%"
if /i "%niche%"=="exit" exit

echo.
echo Authenticating channel '%name%'...
echo.

if "%niche%"=="" (
    call venv\Scripts\activate
    python -m src.add_channel "%name%"
) else (
    call venv\Scripts\activate
    python -m src.add_channel "%name%" --niche "%niche%"
)

echo.
echo Done. Check the messages above for the channel ID.
echo.
pause
exit
