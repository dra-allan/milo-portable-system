@echo off
TITLE [Milo] OpenCode Server (Port 4096)
COLOR 0D
cd /d "C:\Users\Administrator"

:loop
echo ============================================================================
echo   [MILO] OPENCODE SERVER — SUPERVISOR LOOP (PORT 4096)
echo   Started: %DATE% %TIME%
echo ============================================================================
echo.
opencode serve --port 4096
echo.
echo [!] OpenCode server exited with code %ERRORLEVEL%. Auto-restarting in 5 seconds...
ping 127.0.0.1 -n 6 >nul
goto loop
