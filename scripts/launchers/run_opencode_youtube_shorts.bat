@echo off
TITLE [Milo] YouTube Shorts Pipeline - OpenCode Agent Session
COLOR 0A
echo ============================================================================
echo   [MILO] OPENCODE AGENT — YOUTUBE SHORTS PIPELINE RUNNER
echo   Time: %DATE% %TIME%
echo ============================================================================
echo.
echo [1/2] Spawning OpenCode agent session to run and supervise YouTube Shorts pipeline...
echo.

cd /d "C:\milo-portable-system\artisan\youtube-shorts-pipeline"

opencode run --agent milo --dir "C:\milo-portable-system\artisan\youtube-shorts-pipeline" "You are Milo. Run the YouTube Shorts pipeline in C:\milo-portable-system\artisan\youtube-shorts-pipeline by running C:\milo-portable-system\artisan\youtube-shorts-pipeline\run_pipeline.bat (or python -m src.main --mode once). Supervise the entire run, check all logs, solve and fix any errors if they occur, ensure the video is vetted, rendered, and uploaded, and when complete, send a clear summary report to Allan on Telegram using python C:\milo-portable-system\scripts\telegram_send.py."

echo.
echo ============================================================================
echo   [2/2] OpenCode Agent session completed.
echo   Window will remain open on desktop for inspection.
echo ============================================================================
pause
