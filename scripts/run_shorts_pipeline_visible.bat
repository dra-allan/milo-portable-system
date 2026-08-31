@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ============================================================================
rem run_shorts_pipeline_visible.bat — Run Shorts pipeline in REAL OpenCode session
rem ============================================================================
rem Spawns a visible Windows Terminal tab running OpenCode (Milo agent) to execute
rem the YouTube Shorts pipeline with full monitoring and detailed Telegram reporting.
rem ============================================================================

set "REPO_DIR=C:\milo-portable-system"
set "PIPE_DIR=%REPO_DIR%\artisan\youtube-shorts-pipeline"

rem Fix SSL certificate verification for Telegram API calls
set "SSL_CERT_FILE=%PIPE_DIR%\venv\Lib\site-packages\certifi\cacert.pem"
if not exist "%SSL_CERT_FILE%" set "SSL_CERT_FILE=%REPO_DIR%\.venv\Lib\site-packages\certifi\cacert.pem"
if not exist "%SSL_CERT_FILE%" set "SSL_CERT_FILE=%REPO_DIR%\Lib\site-packages\certifi\cacert.pem"

rem Load .env from repo root and milo home
if exist "%REPO_DIR%\.env" (
    for /f "usebackq tokens=1,* delims== eol=#" %%A in ("%REPO_DIR%\.env") do (
        if not "%%A"=="" set "%%A=%%B"
    )
)
set "MILO_HOME=C:\Users\Administrator\AppData\Local\milo"
if exist "%MILO_HOME%\.env" (
    for /f "usebackq tokens=1,* delims== eol=#" %%A in ("%MILO_HOME%\.env") do (
        if not "%%A"=="" set "%%A=%%B"
    )
)

echo [Shorts Pipeline] Starting REAL OpenCode session with Milo agent...
echo Model: Nemotron 3 Ultra 550B
echo Pipeline: YouTube Shorts (one-shot sweep, all channels)

rem Write prompt to temp file to avoid quote escaping issues
set "PROMPT_FILE=%TEMP%\milo_shorts_prompt.txt"
echo Run the YouTube Shorts pipeline in one-shot mode across ALL channels. Execute: cd /d "%PIPE_DIR%" ^&^& python -m src.main --mode once --videos 1. After completion, send a DETAILED Telegram report with: 1) Start time, end time, total duration. 2) Per-channel breakdown: videos discovered, processed, uploaded, failed, current cap status. 3) List of video IDs uploaded with titles and URLs. 4) Any errors with full context. 5) Summary stats: total processed, total uploaded, success rate. Use miloctl.channels.send_telegram for the report. > "%PROMPT_FILE%"

rem Launch REAL OpenCode session in Windows Terminal with --auto for build yolo mode
set "WT="
where wt.exe >nul 2>&1 && set "WT=1"

if defined WT (
    wt.exe -w 0 new-tab --title "Shorts Pipeline (Milo)" --startingDirectory "%PIPE_DIR%" ^
        cmd /k "title Shorts Pipeline (Milo) && echo [Shorts Pipeline] Starting OpenCode with Milo agent (Nemotron 3 Ultra)... && opencode run --agent milo --auto -f \"%PROMPT_FILE%\" && echo. && echo [Session complete - press any key to close] && pause >nul"
) else (
    start "Shorts Pipeline (Milo)" cmd /k "title Shorts Pipeline (Milo) && cd /d "%PIPE_DIR%" && echo [Shorts Pipeline] Starting OpenCode with Milo agent (Nemotron 3 Ultra)... && opencode run --agent milo --auto -f \"%PROMPT_FILE%\" && echo. && echo [Session complete - press any key to close] && pause >nul"
)

exit /b 0