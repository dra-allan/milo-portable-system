@echo off
TITLE [Milo] Ranking Shorts Pipeline - OpenCode Agent Session
COLOR 0E
echo ============================================================================
echo   [MILO] OPENCODE AGENT — RANKING SHORTS PIPELINE RUNNER
echo   Time: %DATE% %TIME%
echo ============================================================================
echo.
echo [1/2] Spawning OpenCode agent session to run and supervise Ranking Shorts pipeline...
echo.

cd /d "C:\milo-portable-system\artisan\ranking-shorts-pipeline"

opencode run --agent milo --dir "C:\milo-portable-system\artisan\ranking-shorts-pipeline" "You are Milo. Run the Ranking Shorts pipeline in C:\milo-portable-system\artisan\ranking-shorts-pipeline by running C:\milo-portable-system\artisan\ranking-shorts-pipeline\run_ranking_pipeline.bat (or python -m src.main --mode auto --videos 3 --variant mixed). Supervise the entire build, monitor vetting and rendering, solve and fix any errors if they occur, and when complete, send a clear summary report to Allan on Telegram using python C:\milo-portable-system\scripts\telegram_send.py."

echo.
echo ============================================================================
echo   [2/2] OpenCode Agent session completed.
echo   Window will remain open on desktop for inspection.
echo ============================================================================
pause
