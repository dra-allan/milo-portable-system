@echo off
TITLE [Milo] Morning Briefing Runner
COLOR 0B
echo ======================================================================
echo    MILO - PHYSICAL MORNING BRIEFING SESSION
echo ======================================================================
echo.

set PYTHON_EXE=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PYTHON_EXE%" (
    set PYTHON_EXE=python
)

"%PYTHON_EXE%" "C:\milo-portable-system\scripts\launchers\run_morning_brief_opencode.py"

echo.
echo ======================================================================
echo  Session completed. Window will remain open for inspection.
echo ======================================================================
pause
