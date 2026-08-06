@echo off
setlocal enabledelayedexpansion
title Gemini TTS Dashboard
cd /d "%~dp0"

REM ── Sanity check: gemini_tts.py must live next to this .bat ───────────
if not exist "%~dp0gemini_tts.py" (
    echo [error] gemini_tts.py not found next to this batch file.
    echo         Expected at: %~dp0gemini_tts.py
    echo.
    pause
    exit /b 1
)

REM ── Pick a python launcher ────────────────────────────────────────────
set PY=
where py >nul 2>nul && set PY=py -3
if "!PY!"=="" (
    where python >nul 2>nul && set PY=python
)
if "!PY!"=="" (
    echo [error] Neither 'py' nor 'python' is on PATH.
    pause
    exit /b 1
)

set VOICE=Charon
set FORMAT=wav
set WORKERS=3
set PROJECT_DIR=
set SCRIPT=
set AUDIO_OUT=

:askproject
cls
echo ========================================================
echo              GEMINI TTS - PROJECT DASHBOARD
echo ========================================================
echo.
set /p PROJECT_DIR="Project folder path: "
set PROJECT_DIR=!PROJECT_DIR:"=!

if "!PROJECT_DIR!"=="" (
    echo [error] Project folder is required.
    pause
    goto askproject
)
if not exist "!PROJECT_DIR!" (
    echo [error] Folder not found: !PROJECT_DIR!
    pause
    goto askproject
)

set SCRIPT=!PROJECT_DIR!\02_SCRIPT_ELEVENLABS.txt
if not exist "!SCRIPT!" (
    echo [error] 02_SCRIPT_ELEVENLABS.txt not found in:
    echo         !PROJECT_DIR!
    pause
    goto askproject
)

set AUDIO_OUT=!PROJECT_DIR!\04_AUDIO
if not exist "!AUDIO_OUT!" mkdir "!AUDIO_OUT!"

echo  [ok] Script:    !SCRIPT!
echo  [ok] Audio out: !AUDIO_OUT!\^<VIDEO_ID^>\
echo.

:scan
set VID=NONE
set DONE=0
set TOTAL=0
set NEXTID=?
set GAPS=0
set PROBE_ERR=

echo Scanning project...

REM Write probe output to a temp file so we can inspect both stdout and stderr
set PROBE_OUT=%TEMP%\gtts_probe_%RANDOM%.txt
set PROBE_ERRFILE=%TEMP%\gtts_probe_err_%RANDOM%.txt

!PY! "%~dp0gemini_tts.py" --script "!SCRIPT!" --audio-dir "!AUDIO_OUT!" --format "!FORMAT!" --probe 1>"!PROBE_OUT!" 2>"!PROBE_ERRFILE!"

REM Parse the DATA| line
for /f "usebackq tokens=1-6 delims=|" %%a in ("!PROBE_OUT!") do (
    if "%%a"=="DATA" (
        set VID=%%b
        set DONE=%%c
        set TOTAL=%%d
        set NEXTID=%%e
        set GAPS=%%f
    )
)

REM If probe failed, capture stderr for display
if "!VID!"=="NONE" (
    for /f "usebackq delims=" %%L in ("!PROBE_ERRFILE!") do set PROBE_ERR=!PROBE_ERR! %%L
)

del "!PROBE_OUT!" 2>nul
del "!PROBE_ERRFILE!" 2>nul

:menu
cls
echo ========================================================
echo              GEMINI TTS - PROJECT DASHBOARD
echo ========================================================
echo.
echo  [ PROJECT  ]  !PROJECT_DIR!
echo  [ VIDEO_ID ]  !VID!
echo  [ SCRIPT   ]  02_SCRIPT_ELEVENLABS.txt
echo  [ AUDIO    ]  04_AUDIO\!VID!\
echo  [ VOICE    ]  !VOICE!
echo  [ FORMAT   ]  !FORMAT!
echo  [ WORKERS  ]  !WORKERS! concurrent
echo.
echo  [ PROGRESS ]  !DONE! of !TOTAL! segments done
echo  [ NEXT     ]  !NEXTID!
echo  [ GAPS     ]  !GAPS!
if not "!PROBE_ERR!"=="" (
    echo.
    echo  [ PROBE WARN ] !PROBE_ERR!
)
echo.
echo ========================================================
echo  1. Run / Continue generating
echo  2. Change voice
echo  3. Change format  (current: !FORMAT!)
echo  4. Switch project folder
echo  5. Refresh status
echo  6. Quit
echo ========================================================
echo.
set /p C="Choose: "
if "!C!"=="1" goto run
if "!C!"=="2" goto voice
if "!C!"=="3" goto format
if "!C!"=="4" goto askproject
if "!C!"=="5" goto scan
if "!C!"=="6" exit /b
goto menu

:run
if "!VID!"=="NONE" (
    echo.
    echo Probe failed — cannot start generation.
    echo Try option 5 to refresh, or check the script file.
    echo.
    pause
    goto menu
)
echo.
echo  Starting generation for !VID!...
echo.
!PY! "%~dp0gemini_tts.py" --script "!SCRIPT!" --audio-dir "!AUDIO_OUT!" --voice "!VOICE!" --format "!FORMAT!"
echo.
pause
goto scan

:voice
cls
echo  1. Charon
echo  2. Puck
echo  3. Kore
echo  4. Fenrir
echo  5. Aoede
echo.
set /p V="Choose: "
if "!V!"=="1" set VOICE=Charon
if "!V!"=="2" set VOICE=Puck
if "!V!"=="3" set VOICE=Kore
if "!V!"=="4" set VOICE=Fenrir
if "!V!"=="5" set VOICE=Aoede
goto menu

:format
if "!FORMAT!"=="mp3" (
    set FORMAT=wav
) else (
    set FORMAT=mp3
)
goto scan