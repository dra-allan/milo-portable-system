@echo off
TITLE [Milo] Morning Briefing Session
COLOR 0B
cd /d "C:\milo-portable-system\scripts"

echo ============================================================================
echo   [MILO] MORNING BRIEFING — VISIBLE PHYSICAL TERMINAL
echo   Time: %DATE% %TIME%
echo ============================================================================
echo.

C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe C:\milo-portable-system\scripts\send_morning_brief.py

echo.
echo ============================================================================
echo   [DONE] Morning Briefing delivered to Telegram.
echo   Window will remain open on desktop for inspection.
echo ============================================================================
pause
