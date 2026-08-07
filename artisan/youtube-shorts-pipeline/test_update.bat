@echo off
echo Starting test_update.bat
echo Key: %1
echo Value: %2
rem %1 = key, %2 = value
if exist .env (
    echo .env exists
    findstr /v /i "%1=" .env > .env.tmp
) else (
    echo .env does not exist
    > .env.tmp
)
echo Writing %1=%2 to .env.tmp
echo %1=%2>> .env.tmp
echo Moving .env.tmp to .env
move /y .env.tmp .env > nul
echo Done
goto :eof