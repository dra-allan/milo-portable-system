@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ============================================================================
rem run_morning_briefing_visible.bat — Morning Briefing in REAL OpenCode session
rem ============================================================================
rem Spawns a visible Windows Terminal tab running OpenCode (Milo agent) to produce
rem a detailed morning briefing and send it to Telegram.
rem ============================================================================

set "REPO_DIR=C:\milo-portable-system"

rem Fix SSL certificate verification for Telegram API calls
set "SSL_CERT_FILE=%REPO_DIR%\artisan\youtube-shorts-pipeline\venv\Lib\site-packages\certifi\cacert.pem"
if not exist "%SSL_CERT_FILE%" set "SSL_CERT_FILE=%REPO_DIR%\.venv\Lib\site-packages\certifi\cacert.pem"
if not exist "%SSL_CERT_FILE%" set "SSL_CERT_FILE=%REPO_DIR%\Lib\site-packages\certifi\cacert.pem"
if not exist "%SSL_CERT_FILE%" set "SSL_CERT_FILE=%REPO_DIR%\artisan\ranking-shorts-pipeline\venv\Lib\site-packages\certifi\cacert.pem"

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

echo [Morning Briefing] Starting REAL OpenCode session with Milo agent...
echo Model: Nemotron 3 Ultra 550B

rem Write prompt to temp file to avoid quote escaping issues
set "PROMPT_FILE=%TEMP%\milo_briefing_prompt.txt"
echo Produce a DETAILED morning briefing for Allan. Check: 1) What was decided or left unfinished yesterday (use milo_vault_search with yesterday's date, milo recall for recent decisions). 2) Pipeline status: run milo recall for 'pipeline' and 'shorts' to get latest run results. 3) Channel health: check which channels are suppressed, caps status, recent uploads. 4) Anything time-sensitive (deadlines, renewals, payments). 5) One strategic thing worth his attention he hasn't asked about. Format as a structured report with sections. Send the completed briefing to Telegram using miloctl.channels.send_telegram. > "%PROMPT_FILE%"

rem Launch REAL OpenCode session in Windows Terminal with --auto for build yolo mode
set "WT="
where wt.exe >nul 2>&1 && set "WT=1"

if defined WT (
    wt.exe -w 0 new-tab --title "Morning Briefing (Milo)" --startingDirectory "%REPO_DIR%" ^
        cmd /k "title Morning Briefing (Milo) && echo [Morning Briefing] Starting OpenCode with Milo agent (Nemotron 3 Ultra)... && opencode run --agent milo --auto -f \"%PROMPT_FILE%\" && echo. && echo [Session complete - press any key to close] && pause >nul"
) else (
    start "Morning Briefing (Milo)" cmd /k "title Morning Briefing (Milo) && cd /d "%REPO_DIR%" && echo [Morning Briefing] Starting OpenCode with Milo agent (Nemotron 3 Ultra)... && opencode run --agent milo --auto -f \"%PROMPT_FILE%\" && echo. && echo [Session complete - press any key to close] && pause >nul"
)

exit /b 0