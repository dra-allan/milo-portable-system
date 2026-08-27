@echo off
rem Non-interactive pipeline run for Task Scheduler and /run from Telegram.
rem   run_pipeline.cmd shorts
rem   run_pipeline.cmd ranking 3
setlocal EnableExtensions
set "HERE=%~dp0"
set "REPO=%HERE%..\.."
for %%I in ("%REPO%") do set "REPO=%%~fI"
set "PY=%REPO%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
if not defined MILO_HOME set "MILO_HOME=%LOCALAPPDATA%\milo"
set "WHICH=%~1"
if "%WHICH%"=="" set "WHICH=shorts"
set "EXTRA="
if not "%~2"=="" set "EXTRA=--videos %~2"
cd /d "%REPO%"
"%PY%" "%REPO%\scripts\daemons\pipeline_runner.py" %WHICH% %EXTRA% --notify
exit /b %ERRORLEVEL%
