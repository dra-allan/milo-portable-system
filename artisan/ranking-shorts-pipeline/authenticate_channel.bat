@echo off
REM DEPRECATED SHIM -- kept so old habits and old docs still work.
REM
REM This used to call src.publisher.auth directly, which minted a token for
REM whatever Google account happened to be signed in and never compared the
REM resolved channel against the key. That is the exact path that published four
REM clips to the wrong channel on 2026-08-16.
REM
REM It now forwards to the guarded flow in the repo root, which verifies channel
REM identity BEFORE writing a token and records the channel id in
REM artisan/yt-secrets/channels.yaml.
setlocal EnableExtensions
set "REPO_DIR=%~dp0..\.."
set "CHANNEL=%~1"
if not defined CHANNEL set /p "CHANNEL=Channel key from artisan/yt-secrets/channels.yaml: "
if not defined CHANNEL (
  echo No channel key given.
  endlocal & exit /b 1
)
call "%REPO_DIR%\reauth_all_channels.bat" --channel "%CHANNEL%" %2 %3 %4 %5
endlocal & exit /b %ERRORLEVEL%
