@echo off
REM === OpenCode Server Runner (Supervisor Loop) ===
REM Runs under SYSTEM via Task Scheduler
REM Auto-restarts OpenCode server if it crashes or exits.

set USERPROFILE=C:\Users\Administrator
set HOME=C:\Users\Administrator
set APPDATA=C:\Users\Administrator\AppData\Roaming
set LOCALAPPDATA=C:\Users\Administrator\AppData\Local
set PATH=C:\Users\Administrator\AppData\Roaming\npm;C:\Program Files\nodejs;C:\Program Files\Git\cmd;C:\Windows\System32;C:\Windows;%PATH%

cd /d C:\Users\Administrator

:loop
echo [%date% %time%] Starting OpenCode Server on port 4096... >> C:\milo-portable-system\opencode_server.log
cmd.exe /c "C:\Users\Administrator\AppData\Roaming\npm\opencode.cmd" serve --port 4096 --print-logs >> C:\milo-portable-system\opencode_server.log 2>&1
echo [%date% %time%] OpenCode exited with code %ERRORLEVEL%. Restarting in 3s... >> C:\milo-portable-system\opencode_server.log
ping 127.0.0.1 -n 4 >nul
goto loop
