@echo off
TITLE [Milo] YouTube Shorts Pipeline Session
COLOR 0A
echo ======================================================================
echo    MILO - PHYSICAL YOUTUBE SHORTS PIPELINE SESSION
echo ======================================================================
echo.

set PYTHON_EXE=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PYTHON_EXE%" (
    set PYTHON_EXE=python
)

"%PYTHON_EXE%" "C:\milo-portable-system\scripts\launchers\run_youtube_shorts_opencode.py"

echo.
echo ======================================================================
echo  Session completed. Window will remain open for inspection.
echo ======================================================================
pause
