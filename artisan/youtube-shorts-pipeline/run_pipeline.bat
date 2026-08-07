@echo off
title YouTube Shorts Pipeline - Easy Runner

:: Load persistent settings from .env file
set "BACKGROUND_MODE=smart"
set "CAPTION_STYLE=hormozi"
if exist .env (
    for /f "tokens=1,* delims==" %%a in ('findstr /b /i "BACKGROUND_MODE=" .env') do set "BACKGROUND_MODE=%%b"
    for /f "tokens=1,* delims==" %%a in ('findstr /b /i "CAPTION_STYLE=" .env') do set "CAPTION_STYLE=%%b"
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
echo 1. Run in Test Mode (check components)
echo 2. Process a YouTube URL/Video ID
echo 3. Run in Scheduled Mode (9AM, 2PM, 7PM daily)
echo 4. Process from Library (downloaded videos)
echo 5. Set BackgroundMode
echo 6. Set CaptionStyle
echo 7. Exit
echo.
set /p choice="Select an option (1-7): "

if "%choice%"=="1" goto test
if "%choice%"=="2" goto url
if "%choice%"=="3" goto schedule
if "%choice%"=="4" goto library
if "%choice%"=="5" goto set_background
if "%choice%"=="6" goto set_caption
if "%choice%"=="7" goto exit

echo Invalid choice! Please try again.
timeout /t 2 > nul
goto main

:test
cls
echo.
echo Running in Test Mode...
echo.
call venv\Scripts\activate
python -m src.main --mode test
pause
goto main

:url
cls
echo.
echo Process YouTube URL/Video ID
echo.
set /p url="Enter YouTube URL or Video ID: "
if "%url%"=="" goto url
echo.
set /p niche="Enter niche (optional, leave blank for auto): "
if "%niche%"=="" (
    call venv\Scripts\activate
    python -m src.main --mode once "%url%"
) else (
    call venv\Scripts\activate
    python -m src.main --mode once "%url%" --niche "%niche%"
)
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
pause
goto main

:library
cls
echo.
echo Select a downloaded video from the library:
echo.
rem Find all .info.json files in data\temp
if not exist "%~dp0data\temp\*.info.json" (
    echo No downloaded videos found in data\temp.
    echo Please download a video first using option 2.
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
if not defined VIDEOFIXED (
    echo.
    echo Selection cancelled or error.
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
pause
goto main

:set_background
echo Entering set_background
cls
echo.
echo Set Background Mode
.
echo Current BackgroundMode: %BACKGROUND_MODE%
.
echo 1. crop      - Fill frame by cropping sides (default)
echo 2. blur      - Blurred background bars
echo 3. cheap     - Low-res blurred background (faster)
echo 4. black     - Solid black bars
echo 5. smart     - Person-aware cropping (face detection)
.
set /p bg_choice="Select background mode (1-5): "
echo Debug: bg_choice=[%bg_choice%]
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
    pause
    goto set_background
)
:: Update .env file
call :update_env BACKGROUND_MODE %new_mode%
set "BACKGROUND_MODE=%new_mode%"
echo Background mode set to %BACKGROUND_MODE%
pause
goto main

:set_caption
cls
echo.
echo Set Caption Style
.
echo Current CaptionStyle: %CAPTION_STYLE%
.
echo 1. default   - Original Arial style (default)
echo 2. hormozi   - Alex Hormozi style (bold, dynamic colors)
echo 3. minimalist - Clean minimalist (sans-serif, white with shadow)
echo 4. pop       - Pop & bounce (neon highlights, black outline)
echo 5. kinetic   - Kinetic karaoke (word-by-word highlight)
.
set /p cap_choice="Select caption style (1-5): "
echo Debug: cap_choice=[%cap_choice%]
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
    pause
    goto set_caption
)
:: Update .env file
call :update_env CAPTION_STYLE %new_style%
set "CAPTION_STYLE=%new_style%"
echo Caption style set to %CAPTION_STYLE%
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
echo.
timeout /t 2 > nul
exit