@echo off
if exist .env (copy .env .env.backup)
:set_background
rem %1 = key, %2 = value
if exist .env (
    findstr /v /i "%1=" .env > .env.tmp
) else (
    > .env.tmp
)
echo %1=%2>> .env.tmp
move /y .env.tmp .env > nul
goto :eof