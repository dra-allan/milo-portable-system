@echo off
rem Long-lived "opencode serve" so every Telegram turn attaches to a warm
rem server instead of cold-booting MCP servers per message.
setlocal EnableExtensions
set "HERE=%~dp0"
set "REPO=%HERE%..\.."
for %%I in ("%REPO%") do set "REPO=%%~fI"
if not defined MILO_HOME set "MILO_HOME=%LOCALAPPDATA%\milo"
if not defined OPENCODE_BIN set "OPENCODE_BIN=opencode"
if not defined OPENCODE_SERVE_PORT set "OPENCODE_SERVE_PORT=4096"
set "LOG=%REPO%\milo-bot\opencode-server.log"
cd /d "%REPO%"
echo [%DATE% %TIME%] starting opencode serve on 127.0.0.1:%OPENCODE_SERVE_PORT% >> "%LOG%"
"%OPENCODE_BIN%" serve --port %OPENCODE_SERVE_PORT% --hostname 127.0.0.1 >> "%LOG%" 2>&1
echo [%DATE% %TIME%] opencode serve exited with %ERRORLEVEL% >> "%LOG%"
exit /b %ERRORLEVEL%
