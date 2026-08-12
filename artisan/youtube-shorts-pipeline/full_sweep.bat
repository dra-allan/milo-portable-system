@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0..\..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" full_sweep_all_channels.py
"%PY%" cleanup_stale_files.py
pause
endlocal
