@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ============================================================================
rem launch_opencode_visible.bat — Spawn a visible OpenCode session with a prompt
rem ============================================================================
rem Usage: launch_opencode_visible.bat "prompt" "window_title"
rem   prompt       - The prompt to send to OpenCode (Milo)
rem   window_title - Title for the terminal window (optional)
rem
rem This opens a visible Windows Terminal (wt.exe) or cmd.exe window running
rem OpenCode with the Milo agent. The session stays open so errors can be
rem monitored and self-corrected.
rem ============================================================================

if "%~1"=="" (
    echo Usage: %~nx0 "prompt" ["window_title"]
    exit /b 1
)

set "PROMPT=%~1"
set "TITLE=%~2"
if "%TITLE%"=="" set "TITLE=Milo OpenCode Session"

rem Resolve repo root and Python
set "REPO_DIR=C:\milo-portable-system"
set "PY=%REPO_DIR%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%REPO_DIR%\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

rem Load .env for Telegram and other credentials
if exist "%REPO_DIR%\.env" (
    for /f "usebackq tokens=1,* delims== eol=#" %%A in ("%REPO_DIR%\.env") do (
        if not "%%A"=="" set "%%A=%%B"
    )
)

rem Prefer Windows Terminal (wt.exe) for visible tabs; fall back to cmd.exe
set "WT="
where wt.exe >nul 2>&1 && set "WT=1"

if defined WT (
    rem Windows Terminal: new tab, keep open on exit
    wt.exe -w 0 new-tab --title "%TITLE%" --startingDirectory "%REPO_DIR%" ^
        cmd /k "title %TITLE% && echo [Milo] %TITLE% && echo [Prompt] %PROMPT% && echo. && opencode run --agent milo \"%PROMPT%\" && echo. && echo [Session ended - press any key to close] && pause >nul"
) else (
    rem Fallback: cmd.exe in new window
    start "Milo: %TITLE%" cmd /k "title %TITLE% && cd /d \"%REPO_DIR%\" && echo [Milo] %TITLE% && echo [Prompt] %PROMPT% && echo. && opencode run --agent milo \"%PROMPT%\" && echo. && echo [Session ended - press any key to close] && pause >nul"
)

exit /b 0