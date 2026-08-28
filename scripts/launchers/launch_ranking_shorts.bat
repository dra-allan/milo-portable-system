@echo off
TITLE [Milo] Ranking Shorts Pipeline Session
COLOR 0E
echo ======================================================================
echo    MILO - PHYSICAL RANKING SHORTS PIPELINE SESSION
echo ======================================================================
echo.

set PYTHON_EXE=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PYTHON_EXE%" (
    set PYTHON_EXE=python
)

"%PYTHON_EXE%" "C:\milo-portable-system\scripts\launchers\run_ranking_shorts_opencode.py"

echo.
echo ======================================================================
echo  Session completed. Window will remain open for inspection.
echo ======================================================================
pause
