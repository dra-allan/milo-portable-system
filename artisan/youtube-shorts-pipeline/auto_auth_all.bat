@echo off
REM DEPRECATED SHIM -- forwards to reauth_all_channels.bat in the REPO ROOT,
REM which is the single authentication path for every pipeline and channel.
REM Kept so muscle memory and older docs still work.
setlocal EnableExtensions
call "%~dp0..\..\reauth_all_channels.bat" %*
endlocal & exit /b %ERRORLEVEL%
