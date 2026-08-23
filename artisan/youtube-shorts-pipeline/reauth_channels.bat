@echo off
REM DEPRECATED SHIM -- forwards to reauth_all_channels.bat in the REPO ROOT.
REM One script, one channel list (artisan/yt-secrets/channels.yaml), one
REM identity gate. Four competing re-auth scripts in two directories is how a
REM channel gets authenticated by the one that skips the checks.
setlocal EnableExtensions
call "%~dp0..\..\reauth_all_channels.bat" %*
endlocal & exit /b %ERRORLEVEL%
