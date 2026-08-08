@echo off
title YouTube Shorts Pipeline - Easy Runner

:: Load persistent settings from .env file
set "BACKGROUND_MODE=smart"
set "CAPTION_STYLE=hormozi"
if exist .env (
    for /f "tokens=1,* delims==" %%a in ('findstr /b /i "BACKGROUND_MODE=" .env') do set "BACKGROUND_MODE=%%b"
    for /f "tokens=1,* delims==" %%a in ('findstr /b /i "CAPTION_STYLE=" .env') do set "CAPTION_STYLE=%%b"
)

:: If arguments are provided, skip the menu and go directly to the selected option
if not "%~1"=="" (
    if "%~1"=="1" goto full_sweep
    if "%~1"=="2" goto url
    if "%~1"=="3" goto schedule
    if "%~1"=="4" goto upload_existing
    if "%~1"=="5" goto library
    if "%~1"=="6" goto test
    if "%~1"=="7" (
        call :set_background %~2
        shift
        shift
        goto main
    )
    if "%~1"=="9" (
        call :set_caption %~2
        shift
        shift
        goto main
    )
    if "%~1"=="10" goto exit
    echo Invalid option: %~1
    pause
    goto main
)

:main
cls
echo.
echo ================================================================
echo           YouTube Shorts Pipeline - Easy Runner
echo ================================================================
echo.
echo Current BackgroundMode: %BACKGROUND_MODE%
echo Current CaptionStyle:   %CAPTION_STYLE%
echo.
echo  1. Run Full Sweep Now (auto-discover ^& process all niches)
echo  2. Process a YouTube URL/Video ID
echo  3. Run in Scheduled Mode (9AM, 2PM, 7PM daily)
echo  4. Upload Existing Local Shorts
echo  5. Process from Library (downloaded videos)
echo  6. Run in Test Mode (check components)
echo  7. Set BackgroundMode
echo  8. (reserved)
echo  9. Set CaptionStyle
echo 10. Exit
echo.
set /p choice="Select an option (1-10): "
if "%choice%"=="1" goto full_sweep
if "%choice%"=="2" goto url
if "%choice%"=="3" goto schedule
if "%choice%"=="4" goto upload_existing
if "%choice%"=="5" goto library
if "%choice%"=="6" goto test
if "%choice%"=="7" goto set_background
if "%choice%"=="8" goto main
if "%choice%"=="9" goto set_caption
if "%choice%"=="10" goto exit
echo Invalid choice! Please try again.
timeout /t 2 > nul
goto main

:full_sweep
cls
echo.
echo ================================================================
echo           Run Full Sweep Now
echo ================================================================
echo.
echo This runs one complete automated sweep across all niches:
echo  - Discovers new videos from each niche's source channels
echo  - Downloads, transcribes, finds highlights, renders Shorts
echo  - Uploads to YouTube (if enabled^)
echo  - Respects per-niche caps and backlog-first logic
echo.
echo Press Ctrl+C at any time to cancel.
echo.
call venv\Scripts\activate
python -m src.main --mode once
echo.
echo Pipeline execution completed.
pause
goto main

:test
cls
echo.
echo Running in Test Mode...
echo.
call venv\Scripts\activate
python -m src.main --mode test
echo.
echo Test mode completed.
pause
goto main

:url
cls
echo.
echo Process YouTube URL/Video ID
echo.
echo Optional flags to append: --force --no-upload --niche ^<name^>
echo.
set "url="
set /p url="Enter YouTube URL or Video ID: "
if "%url%"=="" goto url
set "FORCE_FLAG="
echo %url% | findstr /c:"--force" >nul && set "FORCE_FLAG=--force"
set "url=%url:--force=%"
set "url=%url: =%"
echo.
set /p niche="Enter niche (optional, leave blank for auto): "
if "%niche%"=="" (
    call venv\Scripts\activate
    python -m src.main --mode once "%url%" %FORCE_FLAG%
) else (
    call venv\Scripts\activate
    python -m src.main --mode once "%url%" --niche "%niche%" %FORCE_FLAG%
)
echo.
echo URL processing completed.
pause
goto main

:schedule
cls
echo.
echo Starting Scheduled Pipeline (runs at 9AM, 2PM, 7PM daily)
echo.
echo Press Ctrl+C to stop the scheduler
echo.
call venv\Scripts\activate
python -m src.main --mode schedule
echo.
echo Scheduler stopped.
pause
goto main

:library
cls
echo.
echo Select a downloaded video from the library:
echo.
rem Find all .info.json files in data\temp
if not exist "%~dp0data\temp\*" (
    echo No downloaded videos found in data\temp.
    echo Please download a video first using option 2.
    echo.
    pause
    goto main
)
rem Use PowerShell to list and select
for /f "delims=" %%v in ('powershell -NoProfile -Command "& {
    $files = Get-ChildItem -Path '%~dp0data\temp' -Filter '*.info.json' -File |
             Sort-Object Name
    if ($files.Count -eq 0) { exit 1 }
    $selection = 0
    while ($selection -lt 1 -or $selection -gt $files.Count) {
        Write-Host 'Available videos:' -ForegroundColor Cyan
        for ($i = 0; $i -lt $files.Count; $i++) {
            try {
                $json = Get-Content -Raw -Path $($files[$i].FullName) | ConvertFrom-Json
                $title = $json.title
                if (-not $title) { $title = $json.id }
            } catch {
                $title = $($files[$i].BaseName)
            }
            Write-Host  ($i+1). ') ' $title
        }
        Write-Host ''
        $choice = Read-Host 'Enter number to select (or 0 to cancel)'
        if ($choice -eq 0) { exit 0 }
        $selection = [int]$choice
    }
    $selectedFile = $files[$selection-1].FullName
    $json = Get-Content -Raw -Path $selectedFile | ConvertFrom-Json
    $videoId = $json.id
    $title = $json.title
    if (-not $videoId) { $videoId = $json.id } # ensure
    # Output selected ID for batch to capture
    Write-Host 'SELECTED_ID:' $videoId
    Write-Host 'SELECTED_TITLE:' $title
}" 2^>nul') do (
    if "%%v"=="SELECTED_ID:" set "VIDEOFIXED=%%w"
    if "%%v"=="SELECTED_TITLE:" set "VIDEOTITLE=%%w"
)
rem Check if we got a selection
if not defined VIDEOFIXED (
    echo.
    echo Selection cancelled or error.
    echo.
    pause
    goto main
)
echo.
echo Selected video ID: %VIDEOFIXED%
echo.
set /p niche="Enter niche (optional, leave blank for auto): "
if "%niche%"=="" (
    call venv\Scripts\activate
    python -m src.main --mode once "%VIDEOFIXED%"
) else (
    call venv\Scripts\activate
    python -m src.main --mode once "%VIDEOFIXED%" --niche "%niche%"
)
echo.
echo Library processing completed.
pause
goto main

:upload_existing
cls
echo.
echo Upload Existing Local Shorts
echo.
echo This will upload rendered-but-unpublished shorts to YouTube.
echo.
echo 1. Upload by Niche (upload all pending clips for a specific niche)
echo 2. Upload by Channel Override (force upload to specific channel)
echo 3. Upload All Pending (upload up to limit across all niches)
echo 4. Review and Pick Clips (interactive - choose exactly which clips)
echo 5. Back to Main Menu
echo.
set /p ue_choice="Select an option (1-5): "
if "%ue_choice%"=="1" goto upload_by_niche
if "%ue_choice%"=="2" goto upload_by_channel
if "%ue_choice%"=="3" goto upload_all
if "%ue_choice%"=="4" goto upload_interactive
if "%ue_choice%"=="5" goto main
echo Invalid choice!
timeout /t 2 > nul
goto upload_existing

:upload_interactive
cls
echo.
echo Review and Pick Clips
echo.
echo This lists every rendered-but-unpublished clip grouped by niche and
echo source video, with the optimized title each would get. You pick exactly
echo which clips to post (e.g. "1,2,4-6" or "all").
echo.
call venv\Scripts\activate
python -m src.main --mode upload-existing --interactive
echo.
echo Interactive upload completed.
pause
goto upload_existing

:upload_by_niche
cls
echo.
set /p niche="Enter niche name (e.g., flick_shorts, capital_mindset): "
if "%niche%"=="" (
    echo Niche name is required.
    echo.
    pause
    goto upload_existing
)
call venv\Scripts\activate
python -m src.main --mode upload-existing --niche "%niche%"
echo.
echo Niche upload completed.
pause
goto upload_existing

:upload_by_channel
cls
echo.
set /p channel="Enter channel key (e.g., flick_shorts, capital_mindset): "
if "%channel%"=="" (
    echo Channel key is required.
    echo.
    pause
    goto upload_existing
)
call venv\Scripts\activate
python -m src.main --mode upload-existing --channel "%channel%"
echo.
echo Channel upload completed.
pause
goto upload_existing

:upload_all
cls
echo.
echo Uploading all pending shorts (respects UPLOAD_MAX_PER_RUN limit)...
echo.
call venv\Scripts\activate
python -m src.main --mode upload-existing
echo.
echo All pending uploads completed.
pause
goto upload_existing

:set_background
REM %1 = bg_choice (if passed as argument)
if not "%~1"=="" (
    set "bg_choice=%~1"
) else (
    cls
    echo.
    echo Set Background Mode
    echo.
    echo Current BackgroundMode: %BACKGROUND_MODE%
    echo.
    echo 0. Back to Main Menu
    echo 1. crop      - Fill frame by cropping sides (default)
    echo 2. blur      - Blurred background bars
    echo 3. cheap     - Low-res blurred background (faster)
    echo 4. black     - Solid black bars
    echo 5. smart     - Person-aware cropping (face detection)
    echo.
    set /p bg_choice="Select background mode (0-5): "
)
echo Debug: bg_choice=[%bg_choice%]
if "%bg_choice%"=="0" goto main
if "%bg_choice%"=="1" (
    set "new_mode=crop"
) else if "%bg_choice%"=="2" (
    set "new_mode=blur"
) else if "%bg_choice%"=="3" (
    set "new_mode=cheap"
) else if "%bg_choice%"=="4" (
    set "new_mode=black"
) else if "%bg_choice%"=="5" (
    set "new_mode=smart"
) else (
    echo Invalid choice!
    echo.
    pause
    goto set_background
)
:: Update .env file
call :update_env BACKGROUND_MODE %new_mode%
set "BACKGROUND_MODE=%new_mode%"
echo Background mode set to %BACKGROUND_MODE%
echo.
pause
goto main

:set_caption
REM %1 = cap_choice (if passed as argument)
if not "%~1"=="" (
    set "cap_choice=%~1"
) else (
    cls
    echo.
    echo Set Caption Style
    echo.
    echo Current CaptionStyle: %CAPTION_STYLE%
    echo.
    echo 0. Back to Main Menu
    echo 1. default   - Original Arial style (default)
    echo 2. hormozi   - Alex Hormozi style (bold, dynamic colors)
    echo 3. minimalist - Clean minimalist (sans-serif, white with shadow)
    echo 4. pop       - Pop & bounce (neon highlights, black outline)
    echo 5. kinetic   - Kinetic karaoke (word-by-word highlight)
    echo.
    set /p cap_choice="Select caption style (0-5): "
)
echo Debug: cap_choice=[%cap_choice%]
if "%cap_choice%"=="0" goto main
if "%cap_choice%"=="1" (
    set "new_style=default"
) else if "%cap_choice%"=="2" (
    set "new_style=hormozi"
) else if "%cap_choice%"=="3" (
    set "new_style=minimalist"
) else if "%cap_choice%"=="4" (
    set "new_style=pop"
) else if "%cap_choice%"=="5" (
    set "new_style=kinetic"
) else (
    echo Invalid choice!
    echo.
    pause
    goto set_caption
)
:: Update .env file
call :update_env CAPTION_STYLE %new_style%
set "CAPTION_STYLE=%new_style%"
echo Caption style set to %CAPTION_STYLE%
echo.
pause
goto main

:update_env
rem %1 = key, %2 = value
if exist .env (
    findstr /v /i "%1=" .env > .env.tmp
) else (
    > .env.tmp
)
echo %1=%2>> .env.tmp
move /y .env.tmp .env > nul
goto :eof

:exit
cls
echo.
echo Goodbye!
timeout /t 2 > nul
exit