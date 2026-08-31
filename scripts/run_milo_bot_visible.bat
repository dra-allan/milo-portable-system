@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ============================================================================
rem run_milo_bot_visible.bat — Run Milo Telegram bot in REAL OpenCode session
rem ============================================================================
rem Spawns a visible Windows Terminal tab running OpenCode (Milo agent) that acts
rem as the Telegram bot - handling messages in a persistent OpenCode session.
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

echo [Milo Bot] Starting REAL OpenCode session with Milo agent...
echo Model: Nemotron 3 Ultra 550B
echo Bot mode: Persistent OpenCode session for Telegram

rem Write prompt to temp file to avoid quote escaping issues
set "PROMPT_FILE=%TEMP%\milo_bot_prompt.txt"
echo You are Milo, Allan's assistant. Run the Telegram bot using miloctl.bot.TelegramBot() - it will long-poll for messages. Handle each message in this persistent OpenCode session. Use the milo agent tools (memory, vault, channels, computer, etc.) to respond. Stay running and handle messages as they arrive. Send a startup message to Telegram: 'Milo bot (OpenCode session) is live - Nemotron 3 Ultra 550B model.' > "%PROMPT_FILE%"

rem Launch REAL OpenCode session in Windows Terminal with --auto for build yolo mode
set "WT="
where wt.exe >nul 2>&1 && set "WT=1"

if defined WT (
    wt.exe -w 0 new-tab --title "Milo Bot (OpenCode)" --startingDirectory "%REPO_DIR%" ^
        cmd /k "title Milo Bot (OpenCode) && echo [Milo Bot] Starting OpenCode with Milo agent (Nemotron 3 Ultra)... && opencode run --agent milo --auto -f \"%PROMPT_FILE%\" && echo. && echo [Session complete - press any key to close] && pause >nul"
) else (
    start "Milo Bot (OpenCode)" cmd /k "title Milo Bot (OpenCode) && cd /d "%REPO_DIR%" && echo [Milo Bot] Starting OpenCode with Milo agent (Nemotron 3 Ultra)... && opencode run --agent milo --auto -f \"%PROMPT_FILE%\" && echo. && echo [Session complete - press any key to close] && pause >nul"
)

exit /b 0