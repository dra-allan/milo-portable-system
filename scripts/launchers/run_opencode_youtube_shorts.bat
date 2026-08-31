@echo off
TITLE [Milo] YouTube Shorts Pipeline — OpenCode Agent Session
COLOR 0A
setlocal EnableExtensions EnableDelayedExpansion

set USERPROFILE=C:\Users\Administrator
set HOME=C:\Users\Administrator
set APPDATA=C:\Users\Administrator\AppData\Roaming
set LOCALAPPDATA=C:\Users\Administrator\AppData\Local
set PATH=C:\Users\Administrator\AppData\Roaming\npm;C:\Program Files\nodejs;C:\Users\Administrator\AppData\Local\Programs\Python\Python312;C:\Program Files\Git\cmd;C:\Windows\System32;C:\Windows;%PATH%

echo ============================================================================
echo   [MILO] OPENCODE AGENT — YOUTUBE SHORTS PIPELINE SUPERVISOR
echo   Time: %DATE% %TIME%
echo ============================================================================
echo.
echo [1/2] Notifying Telegram that supervisor session is starting...
C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe C:\milo-portable-system\scripts\telegram_send.py "🎬 Milo: Spawning visible OpenCode agent session on VPS desktop to run YouTube Shorts pipeline..."

echo.
echo [2/2] Spawning OpenCode agent session (Milo) to execute and supervise pipeline...
echo.

cd /d "C:\milo-portable-system\artisan\youtube-shorts-pipeline"

cmd.exe /c "C:\Users\Administrator\AppData\Roaming\npm\opencode.cmd" run --agent milo --dir "C:\milo-portable-system\artisan\youtube-shorts-pipeline" "You are Milo. Run the YouTube Shorts pipeline in C:\milo-portable-system\artisan\youtube-shorts-pipeline. First execute: venv\Scripts\python.exe -m src.main --mode once --videos 1. Monitor the execution, inspect logs in logs/ or runtime, diagnose and fix any errors (such as yt-dlp download, openai whisper transcription, ffmpeg rendering, or metadata errors). Once video generation and upload complete (or if an error was resolved), deliver a clear and comprehensive summary report to Allan on Telegram by running: C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe C:\milo-portable-system\scripts\telegram_send.py \"<your report>\"."

echo.
echo ============================================================================
echo   [DONE] OpenCode Agent session completed.
echo   Window will remain open on desktop for inspection.
echo ============================================================================
pause
