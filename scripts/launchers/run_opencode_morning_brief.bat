@echo off
TITLE [Milo] Morning Briefing — OpenCode Agent Session
COLOR 0B
setlocal EnableExtensions EnableDelayedExpansion

set USERPROFILE=C:\Users\Administrator
set HOME=C:\Users\Administrator
set APPDATA=C:\Users\Administrator\AppData\Roaming
set LOCALAPPDATA=C:\Users\Administrator\AppData\Local
set PATH=C:\Users\Administrator\AppData\Roaming\npm;C:\Program Files\nodejs;C:\Users\Administrator\AppData\Local\Programs\Python\Python312;C:\Program Files\Git\cmd;C:\Windows\System32;C:\Windows;%PATH%

echo ============================================================================
echo   [MILO] OPENCODE AGENT — MORNING BRIEFING GENERATOR
echo   Time: %DATE% %TIME%
echo ============================================================================
echo.
echo [1/2] Notifying Telegram that morning briefing session is starting...
C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe C:\milo-portable-system\scripts\telegram_send.py "🌅 Milo: Spawning visible OpenCode agent session on VPS desktop to generate your Morning Briefing..."

echo.
echo [2/2] Spawning OpenCode agent session (Milo) to generate and deliver briefing...
echo.

cd /d "C:\Users\Administrator"

cmd.exe /c "C:\Users\Administrator\AppData\Roaming\npm\opencode.cmd" run --agent milo --dir "C:\Users\Administrator" "You are Milo. Generate Allan's morning briefing for today. Review yesterday's memory and vault daily notes in C:\Users\Administrator\Desktop\dra-brains\01 - Daily Notes. Structure the briefing: 1. Yesterday's decisions & open items. 2. Today's priorities & focus. 3. System & pipeline status (YouTube Shorts & Ranking Shorts). 4. One high-value recommendation. Format it cleanly with markdown. Append this briefing to today's daily note in C:\Users\Administrator\Desktop\dra-brains\01 - Daily Notes (create file if needed). Then deliver the full briefing directly to Allan on Telegram by running: C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe C:\milo-portable-system\scripts\telegram_send.py \"<full briefing text>\"."

echo.
echo ============================================================================
echo   [DONE] OpenCode Agent session completed.
echo   Window will remain open on desktop for inspection.
echo ============================================================================
pause
