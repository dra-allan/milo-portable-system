@echo off
REM === Milo Telegram Bot Launcher ===
REM Runs under SYSTEM via Task Scheduler — all paths must be absolute.

set TELEGRAM_BOT_TOKEN=8844481759:AAExAkAIOl_m_JBQ3_RxTf9tM7Afn32Y3nM
set TELEGRAM_CHAT_ID=8101147332
set ALLOWED_USER_IDS=8101147332
set OPENCODE_BIN=C:\Users\Administrator\AppData\Roaming\npm\opencode.cmd
set MILO_AGENT=milo
set OPENCODE_WORKDIR=C:\milo-portable-system
set OPENCODE_TIMEOUT_SEC=600
set LOG_LEVEL=INFO

REM Ensure SYSTEM can find node, npm, opencode, and git
set PATH=C:\Users\Administrator\AppData\Roaming\npm;C:\Program Files\nodejs;C:\Program Files\Git\cmd;%PATH%

C:\milo-portable-system\milo-bot\venv\Scripts\python.exe C:\milo-portable-system\milo-bot\src\bot.py
exit /b
