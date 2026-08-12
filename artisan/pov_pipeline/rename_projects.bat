@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0..\..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" rename_projects_by_title.py %*
pause
endlocal
