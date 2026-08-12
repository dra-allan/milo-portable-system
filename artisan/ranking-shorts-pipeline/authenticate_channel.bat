@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0..\..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "CHANNEL="
set /p "CHANNEL=Ranking upload channel key: "
if not defined CHANNEL exit /b 1
call :run_auth
pause
exit /b
:run_auth
call :ensure_python
if errorlevel 1 exit /b 1
"%PY%" -c "from src.publisher import auth; print('Authenticated channel ID:', auth(r'%CHANNEL%') or 'not returned')"
exit /b
:ensure_python
"%PY%" -c "import googleapiclient, google_auth_oauthlib" >nul 2>&1
if errorlevel 1 "%PY%" -m pip install -r requirements.txt
exit /b 0
endlocal
