@echo off
TITLE [Milo] Morning Briefing - OpenCode Agent Session
COLOR 0B
echo ============================================================================
echo   [MILO] OPENCODE AGENT — MORNING BRIEFING GENERATOR
echo   Time: %DATE% %TIME%
echo ============================================================================
echo.
echo [1/2] Spawning OpenCode agent session to generate Allan's Morning Briefing...
echo.

cd /d "C:\Users\Administrator"

opencode run --agent milo --dir "C:\Users\Administrator" "You are Milo. Generate Allan's morning briefing for today. Review yesterday's memory and vault daily notes in C:\Users\Administrator\Desktop\dra-brains\01 - Daily Notes. Structure the briefing: 1. Yesterday's decisions & open items. 2. Today's priorities & focus. 3. System & pipeline status. 4. One high-value recommendation. Append this briefing to today's daily note in C:\Users\Administrator\Desktop\dra-brains\01 - Daily Notes and send the complete briefing directly to Allan on Telegram using python C:\milo-portable-system\scripts\telegram_send.py."

echo.
echo ============================================================================
echo   [2/2] OpenCode Agent session completed.
echo   Window will remain open on desktop for inspection.
echo ============================================================================
pause
