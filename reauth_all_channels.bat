@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Milo - re-authenticate YouTube channels

REM =========================================================================
REM  reauth_all_channels.bat
REM
REM  One-click re-authentication for every YouTube channel attached to every
REM  pipeline, one channel at a time. Double-click it, do the browser consents.
REM
REM  What it does:
REM    1. runs from artisan\ so the yt_secrets module imports
REM    2. audits channels.yaml first and warns before touching anything
REM    3. loops every channel in channels.yaml, sequentially, in file order.
REM       No channel list is hardcoded here, so adding a channel to any
REM       pipeline is all it takes to be included next run.
REM    4. per channel: names the Google account, opens the consent page in
REM       that account's Chrome profile, verifies the resolved channel against
REM       the key's binding, writes the token, then writes channel_id straight
REM       into channels.yaml. No copy-paste, no guessing the account.
REM    5. a consent approved on the wrong account writes NOTHING and is
REM       reported as REFUSED
REM    6. one failure does not kill the run: you get an OK/FAIL summary and a
REM       final refresh check of every token
REM
REM  Options: run  reauth_all_channels.bat --help
REM =========================================================================

set "REPO_DIR=%~dp0"
if "!REPO_DIR:~-1!"=="\" set "REPO_DIR=!REPO_DIR:~0,-1!"
set "ARTISAN_DIR=!REPO_DIR!\artisan"
set "REGISTRY=!ARTISAN_DIR!\yt-secrets\channels.yaml"

if not exist "!ARTISAN_DIR!\yt_secrets\auth.py" goto :no_module
if not exist "!REGISTRY!" goto :no_registry

REM yt_secrets finds the registry and every token_dir relative to itself, but
REM `python -m yt_secrets` needs artisan on sys.path, so artisan must be cwd.
cd /d "!ARTISAN_DIR!"
if errorlevel 1 goto :no_module

set "MODE=auth"
set "ONLY="
set "SCOPE=--all"
set "FILTERS="
set "PASSTHRU="
set "TIMEOUT=15"
set "PAUSE_EACH=1"

:parse
if "%~1"=="" goto :parsed
set "ARG=%~1"
if /i "!ARG!"=="--help" goto :usage
if /i "!ARG!"=="-h" goto :usage
if /i "!ARG!"=="/?" goto :usage
if /i "!ARG!"=="--status" ( set "MODE=status" & shift & goto :parse )
if /i "!ARG!"=="--doctor" ( set "MODE=doctor" & shift & goto :parse )
if /i "!ARG!"=="--sync" ( set "MODE=sync" & shift & goto :parse )
if /i "!ARG!"=="--add" ( set "MODE=add" & shift & goto :parse )
if /i "!ARG!"=="--active" ( set "SCOPE=" & shift & goto :parse )
if /i "!ARG!"=="--unbound" ( set "FILTERS=!FILTERS! --unbound" & shift & goto :parse )
if /i "!ARG!"=="--rebind" ( set "PASSTHRU=!PASSTHRU! --rebind" & shift & goto :parse )
if /i "!ARG!"=="--force" ( set "PASSTHRU=!PASSTHRU! --force-registry" & shift & goto :parse )
if /i "!ARG!"=="--no-pause" ( set "PAUSE_EACH=0" & shift & goto :parse )
if /i "!ARG!"=="--yes" ( set "PAUSE_EACH=0" & shift & goto :parse )
if /i "!ARG!"=="--channel" ( set "ONLY=!ONLY! %~2" & shift & shift & goto :parse )
if /i "!ARG!"=="--pipeline" ( set "FILTERS=!FILTERS! --pipeline %~2" & shift & shift & goto :parse )
if /i "!ARG!"=="--timeout" ( set "TIMEOUT=%~2" & shift & shift & goto :parse )
echo [FATAL] unknown option: !ARG!
goto :usage
:parsed

REM --- find a python that can import yaml ----------------------------------
set "PY="
for %%C in (
  "!REPO_DIR!\.venv\Scripts\python.exe"
  "!ARTISAN_DIR!\.venv\Scripts\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
) do (
  if not defined PY if exist "%%~fC" set "PY=%%~fC"
)
if not defined PY (
  where python >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY goto :no_python

"!PY!" -c "import yaml" >nul 2>&1
if errorlevel 1 (
  echo Installing PyYAML with !PY! ...
  "!PY!" -m pip install --quiet --disable-pip-version-check PyYAML
  if errorlevel 1 goto :no_pyyaml
)

echo.
echo ==========================================================
echo  Milo channel authentication
echo  repo     : !REPO_DIR!
echo  registry : artisan\yt-secrets\channels.yaml
echo  python   : !PY!
echo ==========================================================

if /i "!MODE!"=="status" goto :run_status
if /i "!MODE!"=="doctor" goto :run_doctor
if /i "!MODE!"=="sync" goto :run_sync
if /i "!MODE!"=="add" goto :add_new
goto :preflight

:run_status
"!PY!" -m yt_secrets status !SCOPE! !FILTERS!
set "RC=!ERRORLEVEL!"
goto :the_end

:run_doctor
"!PY!" -m yt_secrets doctor --verbose
set "RC=!ERRORLEVEL!"
goto :the_end

:run_sync
echo Filling channel_id from tokens already on this machine. No browser needed.
"!PY!" -m yt_secrets sync !SCOPE! !FILTERS!
set "RC=!ERRORLEVEL!"
goto :the_end

REM --- add a new channel to a pipeline, then authenticate it ----------------
:add_new
echo.
echo Adding a NEW channel to a pipeline.
echo The key is the stable name used in youtube_token_KEY.json and by the code.
echo.
set "NEW_KEY="
set "NEW_EMAIL="
set "NEW_SLUG="
set "NEW_LANES="
set "NEW_PROFILE="
set /p "NEW_KEY=Channel key, letters digits underscores: "
if not defined NEW_KEY goto :abort_add
set /p "NEW_EMAIL=Owning Google account email: "
if not defined NEW_EMAIL goto :abort_add
echo The slug is the folder under artisan\yt-secrets\ holding credentials.json
set /p "NEW_SLUG=Project slug: "
if not defined NEW_SLUG goto :abort_add
echo Lanes, space separated. Valid: shorts ranking pov clipper
set /p "NEW_LANES=Pipelines: "
if not defined NEW_LANES goto :abort_add
set /p "NEW_PROFILE=Chrome profile for that account, e.g. Profile 3, blank to skip: "
set "LANE_ARGS="
for %%L in (!NEW_LANES!) do set "LANE_ARGS=!LANE_ARGS! --pipeline %%L"
set PROFILE_ARG=
if defined NEW_PROFILE set PROFILE_ARG=--chrome-profile "!NEW_PROFILE!"
"!PY!" -m yt_secrets add --channel "!NEW_KEY!" --email "!NEW_EMAIL!" --slug "!NEW_SLUG!" !LANE_ARGS! !PROFILE_ARG! --no-auth
if errorlevel 1 (
  set "RC=1"
  goto :the_end
)
set "ONLY= !NEW_KEY!"
goto :preflight

:abort_add
echo Nothing added.
set "RC=1"
goto :the_end

REM --- audit before touching anything --------------------------------------
:preflight
echo.
echo --- checking channels.yaml for mismatches ---
"!PY!" -m yt_secrets doctor
if errorlevel 1 (
  echo.
  echo An ERROR line means a channel would authenticate into the wrong place.
  echo WARN lines are fine: an unbound channel simply gets bound on this run.
  choice /c YN /n /m "Continue anyway? [Y/N] "
  if errorlevel 2 (
    set "RC=1"
    goto :the_end
  )
)

REM --- build the channel list ----------------------------------------------
set "KEYS="
set "FAILED_KEYS="
set /a COUNT=0, INDEX=0, FAILED=0
if defined ONLY goto :keys_from_args
for /f "usebackq delims=" %%K in (`!PY! -m yt_secrets list --keys-only !SCOPE! !FILTERS!`) do set "KEYS=!KEYS! %%K"
goto :keys_ready
:keys_from_args
for %%K in (!ONLY!) do set "KEYS=!KEYS! %%K"
:keys_ready
if not defined KEYS goto :no_keys
for %%K in (!KEYS!) do set /a COUNT+=1

echo.
echo !COUNT! channel/s to re-authenticate, in this order:
echo    !KEYS!
echo.
echo Each one opens a browser. Approve as the account named on screen.
echo A consent approved on the wrong account is REFUSED and writes nothing.
echo.

for %%K in (!KEYS!) do call :do_channel %%K

set /a DONE=!COUNT!-!FAILED!
echo.
echo ==========================================================
echo  !DONE! of !COUNT! channels re-authenticated
if !FAILED! gtr 0 echo  needs attention:!FAILED_KEYS!
echo ==========================================================
echo.
echo --- final refresh check of every token ---
"!PY!" -m yt_secrets status --all
set "RC=!ERRORLEVEL!"
if !FAILED! gtr 0 set "RC=1"
goto :the_end

REM --- one channel ---------------------------------------------------------
:do_channel
set "KEY=%~1"
set /a INDEX+=1
set "C_EMAIL=unknown account"
set "C_STATE=?"
set "C_PROFILE=-"
set "C_BOUND=not bound yet"
set "C_LANES=-"
for /f "usebackq tokens=1-6 delims=|" %%A in (`!PY! -m yt_secrets list --plain --channel "!KEY!"`) do (
  set "C_EMAIL=%%B"
  set "C_STATE=%%C"
  set "C_PROFILE=%%D"
  set "C_BOUND=%%E"
  set "C_LANES=%%F"
)
echo.
echo ----------------------------------------------------------
echo  [!INDEX! of !COUNT!]  !KEY!   !C_STATE!
echo   sign in as     : !C_EMAIL!
echo   chrome profile : !C_PROFILE!
echo   pipelines      : !C_LANES!
echo   bound to       : !C_BOUND!
echo ----------------------------------------------------------
if "!PAUSE_EACH!"=="1" (
  echo Press any key to open the consent page for !KEY! ...
  pause >nul
)
"!PY!" -m yt_secrets auth --channel "!KEY!" --timeout-minutes !TIMEOUT! !PASSTHRU!
if errorlevel 1 (
  set /a FAILED+=1
  set "FAILED_KEYS=!FAILED_KEYS! !KEY!"
  echo [FAIL] !KEY! was not re-authenticated. Read the lines above, then retry
  echo        with:  reauth_all_channels.bat --channel !KEY!
) else (
  echo [DONE] !KEY!
)
exit /b 0

REM --- failure exits -------------------------------------------------------
:no_module
echo [FATAL] artisan\yt_secrets\auth.py is not next to this script.
echo         Keep reauth_all_channels.bat in the repo root.
set "RC=1"
goto :the_end

:no_registry
echo [FATAL] artisan\yt-secrets\channels.yaml is missing. Nothing to authenticate.
set "RC=1"
goto :the_end

:no_python
echo [FATAL] No python found. Install Python 3.11+ or create the repo venv at
echo         !REPO_DIR!\.venv
set "RC=1"
goto :the_end

:no_pyyaml
echo [FATAL] Could not install PyYAML with !PY!
echo         Run:  "!PY!" -m pip install PyYAML google-api-python-client
set "RC=1"
goto :the_end

:no_keys
echo [FATAL] That selection matched no channels in channels.yaml. Known channels:
"!PY!" -m yt_secrets list --all
set "RC=1"
goto :the_end

:usage
echo.
echo Usage: reauth_all_channels.bat [options]
echo.
echo   no options        every channel in channels.yaml, one at a time
echo   --active          only channels with active: true
echo   --channel KEY     only this channel, repeatable
echo   --pipeline NAME   shorts, ranking, pov or clipper
echo   --unbound         only channels with no channel_id yet
echo   --add             register a NEW channel, then authenticate it
echo   --status          refresh-check tokens only, no browser
echo   --doctor          audit channels.yaml only, no browser
echo   --sync            write channel_id from tokens you already have
echo   --rebind          allow a key to move to a different channel
echo   --force           overwrite a conflicting channel_id
echo   --timeout N       minutes per consent, default 15
echo   --no-pause        no keypress between channels
echo.
set "RC=0"
goto :the_end

:the_end
if not defined RC set "RC=0"
echo.
if "!PAUSE_EACH!"=="1" pause
endlocal & exit /b %RC%
