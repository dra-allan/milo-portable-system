@echo off
REM DEPRECATED SHIM -- the real script is reauth_all_channels.bat in the REPO ROOT.
REM
REM This file used to be a hardcoded 7-channel menu that:
REM   * ran `%PY% -m yt_secrets` from THIS directory, where the yt_secrets
REM     package is not importable (it lives in artisan/), so every option failed
REM     with "No module named yt_secrets"
REM   * listed channels by hand, so a channel added to a pipeline was invisible
REM     to it until someone remembered to edit the menu
REM   * called `goto menu` from inside `call :label` blocks, so "authenticate
REM     ALL 7" did not reliably continue past the first channel
REM   * ended every channel by telling you to COPY the channel id and PASTE it
REM     into channels.yaml, which is why all twelve channel_id values were still
REM     empty
REM   * named `RankDrop` and `the_other_guys` as ranking channels while
REM     channels.yaml had `the_other_guys` registered on the shorts lane
REM
REM The root script drives the whole registry from channels.yaml, opens each
REM consent page in the right Chrome profile, and writes the ids itself.
setlocal EnableExtensions
call "%~dp0..\..\reauth_all_channels.bat" %*
endlocal & exit /b %ERRORLEVEL%
