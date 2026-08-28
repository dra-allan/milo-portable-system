@echo off
TITLE [Milo] YouTube Shorts Pipeline Session
COLOR 0A
cd /d "C:\milo-portable-system\artisan\youtube-shorts-pipeline"

echo ============================================================================
echo   [MILO] YOUTUBE SHORTS PIPELINE — VISIBLE PHYSICAL TERMINAL
echo   Time: %DATE% %TIME%
echo ============================================================================
echo.

set "PY=venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python.exe"

C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe C:\milo-portable-system\scripts\telegram_send.py "🎬 Milo: Starting YouTube Shorts pipeline sweep on VPS desktop..."

"%PY%" -m src.main --mode once --videos 1
set "EXIT_CODE=%ERRORLEVEL%"

if exist cleanup_runtime.py "%PY%" cleanup_runtime.py

if %EXIT_CODE% equ 0 (
    C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe C:\milo-portable-system\scripts\telegram_send.py "✅ Milo: YouTube Shorts pipeline sweep completed successfully!"
) else (
    C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe C:\milo-portable-system\scripts\telegram_send.py "⚠️ Milo: YouTube Shorts pipeline exited with code %EXIT_CODE%. Check VPS terminal."
)

echo.
echo ============================================================================
echo   [DONE] Pipeline finished with exit code %EXIT_CODE%.
echo   Window will remain open on desktop for inspection.
echo ============================================================================
pause
