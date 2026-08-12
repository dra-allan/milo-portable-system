@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "PY=%~dp0..\..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "RANKING_DIR=%~dp0"
set "REPO_DIR=%~dp0..\.."
call :load_env
set "CHANNEL="
set /p "CHANNEL=Channel key (same name used by Shorts/POV, e.g. ranking_main): "
if not defined CHANNEL exit /b 1
call :ensure_python
if errorlevel 1 exit /b 1
echo.
echo A browser will open. Sign into the YouTube channel for "%CHANNEL%".
echo The token will be saved using the shared youtube_token_%CHANNEL%.json convention.
echo.
"%PY%" -c "from src.publisher import auth; cid=auth(r'%CHANNEL%'); print('Authenticated channel ID:', cid or 'not returned')"
pause
endlocal
exit /b 0
:load_env
if exist "%REPO_DIR%\.env" for /f "usebackq tokens=1,* delims== eol=#" %%A in ("%REPO_DIR%\.env") do if not "%%A"=="" set "%%A=%%B"
if exist "config\.env" for /f "usebackq tokens=1,* delims== eol=#" %%A in ("config\.env") do if not "%%A"=="" set "%%A=%%B"
exit /b 0
:ensure_python
"%PY%" -c "import googleapiclient, google_auth_oauthlib" >nul 2>&1
if errorlevel 1 "%PY%" -m pip install -r requirements.txt
exit /b 0
