@echo off
TITLE [Milo] System Launcher
COLOR 0B
echo ============================================================================
echo   STARTING MILO VISIBLE PHYSICAL DESKTOP SESSIONS
echo ============================================================================
echo.
echo [1/2] Launching OpenCode Server (Port 4096)...
start "Milo OpenCode Server" cmd.exe /k "C:\milo-portable-system\scripts\launchers\start_milo_server_visible.bat"

ping 127.0.0.1 -n 3 >nul

echo [2/2] Launching Telegram Bot...
start "Milo Telegram Bot" cmd.exe /k "C:\milo-portable-system\scripts\launchers\start_milo_bot_visible.bat"

echo.
echo [+] Both visible sessions launched on your desktop!
timeout /t 3 >nul
