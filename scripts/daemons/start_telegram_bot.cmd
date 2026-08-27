@echo off
rem Milo Telegram bot launcher - used by the MiloTelegramBot scheduled task.
rem No prompts, no pause: anything interactive here hangs the task forever.
setlocal EnableExtensions
set "HERE=%~dp0"
set "REPO=%HERE%..\.."
for %%I in ("%REPO%") do set "REPO=%%~fI"
set "BOT=%REPO%\milo-bot"
set "PY=%BOT%\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%REPO%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
if not defined MILO_HOME set "MILO_HOME=%LOCALAPPDATA%\milo"
if not defined OPENCODE_WORKDIR set "OPENCODE_WORKDIR=%REPO%"
if not defined OPENCODE_SERVER_URL set "OPENCODE_SERVER_URL=http://127.0.0.1:4096"
cd /d "%REPO%"
echo [%DATE% %TIME%] starting bot with %PY% >> "%BOT%\bot.stdout.log"
"%PY%" "%BOT%\src\bot.py" >> "%BOT%\bot.stdout.log" 2>&1
echo [%DATE% %TIME%] bot exited with %ERRORLEVEL% >> "%BOT%\bot.stdout.log"
exit /b %ERRORLEVEL%
