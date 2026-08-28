@echo off
TITLE [Milo] Telegram Bot Bridge
COLOR 09
cd /d "C:\Users\Administrator"

set OPENCODE_WORKDIR=C:\Users\Administrator
set "PYTHONPATH=C:\milo-portable-system"

:loop
echo ============================================================================
echo   [MILO] TELEGRAM BOT — SUPERVISOR LOOP
echo   Started: %DATE% %TIME%
echo ============================================================================
echo.
C:\milo-portable-system\milo-bot\venv\Scripts\python.exe C:\milo-portable-system\milo-bot\src\bot.py
echo.
echo [!] Telegram Bot exited with code %ERRORLEVEL%. Auto-restarting in 5 seconds...
ping 127.0.0.1 -n 6 >nul
goto loop
