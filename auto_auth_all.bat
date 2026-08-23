@echo off
REM Alias for reauth_all_channels.bat, which is the real script. Both names got
REM used in conversation, so both work rather than one of them being a dead end
REM you double-click at 2am.
setlocal EnableExtensions
call "%~dp0reauth_all_channels.bat" %*
endlocal & exit /b %ERRORLEVEL%
