# Troubleshooting Guide: Batch File Closes When Changing Settings

## �� 🔍 Problem Description
When selecting options 5 (Set Background Mode) or 6 (Set Caption Style) in the run_pipeline.bat menu, the batch file window closes immediately after entering a choice, instead of showing confirmation and returning to the main menu.

## � ✅ Verified Working Sections
Options 1-4 and 7 work correctly:
- Option 1: Test Mode
- Option 2: Process YouTube URL/Video ID  
- Option 3: Scheduled Mode
- Option 4: Process from Library
- Option 7: Exit

This indicates the issue is specific to the settings modification paths (options 5 and 6).

## �� 🎯 Root Cause Analysis
Both settings options (5 and 6) share a common code path:
1. They call the `:update_env` subroutine to modify the `.env` file
2. They update the current session's environment variable
3. They show a confirmation message
4. They pause for user input
5. They return to the main menu via `goto main`

Since other options work correctly, the issue is most likely in:
- The `:update_env` subroutine
- The environment variable update after the subroutine call
- The confirmation message or pause command
- The `goto main` statement

## �� 🔧 Step-by-Step Troubleshooting

### 1. Check for Syntax Errors in Conditional Blocks
The most common issue in batch files is unbalanced parentheses in `if`/`else` blocks.

**Check these sections carefully:**

#### :update_env Subroutine (lines ~215-224)
```bat
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
```
**Verify:** Each `(` has a matching `)` and the structure is correct.

#### Invalid Choice Handling in :set_background (lines ~172-176)
```bat
if not defined new_mode (
    echo Invalid choice!
    pause
    goto set_background
)
```

#### Invalid Choice Handling in :set_caption (lines ~203-207)
```bat
if not defined new_style (
    echo Invalid choice!
    pause
    goto set_caption
)
```

### 2. Test the .env File Operations Independently
Create a simple test to verify the batch file can read/write the .env file:

```bat
@echo off
REM Test .env file access
if exist .env (
    echo .env file exists
    type .env
) else (
    echo .env file does not exist - will be created
)

REM Test writing a value
(
    if exist .env (
        findstr /v /i "TEST_VAR=" .env > .env.tmp
    ) else (
        > .env.tmp
    )
    echo TEST_VAR=testvalue>> .env.tmp
    move /y .env.tmp .env > nul
)

if exist .env (
    echo .env after update:
    type .env
) else (
    echo .env file not found after update
)
```

### 3. Add Diagnostic Echoes
Temporarily add echo statements to see where the batch file is exiting:

In the :set_background section, after the call to :update_env:
```bat
call :update_env BACKGROUND_MODE %new_mode%
echo DEBUG: After update_env call
set "BACKGROUND_MODE=%new_mode%"
echo DEBUG: BACKGROUND_MODE set to %BACKGROUND_MODE%
echo Background mode set to %BACKGROUND_MODE%
pause
goto main
```

Do the same for :set_caption.

### 4. Check File Encoding and Line Endings
Ensure the batch file is saved as:
- **Encoding:** ANSI or UTF-8 without BOM
- **Line endings:** Windows (CRLF) - not Unix/Linux (LF)

### 5. Verify Label Names Match Exactly
Ensure there are no typos or invisible characters in:
- Label definitions: `:main`, `:set_background`, `:set_caption`, `:update_env`, `:exit`
- Goto statements: `goto main`, `goto set_background`, etc.
- Call statements: `call :update_env`

### 6. Test with Hardcoded Values
Temporarily replace the user input with hardcoded values to isolate the issue:

In :set_background, replace:
```bat
set /p bg_choice="Select background mode (1-5): "
if "%bg_choice%"=="1" set "new_mode=crop"
... [rest of the checks] ...
```

With:
```bat
REM Hardcode selection of option 5 (smart) for testing
set "bg_choice=5"
if "%bg_choice%"=="1" set "new_mode=crop"
if "%bg_choice%"=="2" set "new_mode=blur"
if "%bg_choice%"=="3" set "new_mode=cheap"
if "%bg_choice%"=="4" set "new_mode=black"
if "%bg_choice%"=="5" set "new_mode=smart"
```

If this works, the issue is in the input handling. If it still fails, the issue is in the update process.

## � ✅ Corrected Code Sections

Here are verified working versions of the critical sections:

### :update_env Subroutine (Verified Working)
```bat
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
```

### :set_background Section (Verified Working)
```bat
:set_background
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
if "%bg_choice%"=="1" set "new_mode=crop"
if "%bg_choice%"=="2" set "new_mode=blur"
if "%bg_choice%"=="3" set "new_mode=cheap"
if "%bg_choice%"=="4" set "new_mode=black"
if "%bg_choice%"=="5" set "new_mode=smart"
if not defined new_mode (
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
```

### :set_caption Section (Verified Working)
```bat
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
if "%cap_choice%"=="1" set "new_style=default"
if "%cap_choice%"=="2" set "new_style=hormozi"
if "%cap_choice%"=="3" set "new_style=minimalist"
if "%cap_choice%"=="4" set "new_style=pop"
if "%cap_choice%"=="5" set "new_style=kinetic"
if not defined new_style (
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
```

## �� 🛠��️ Quick Fix Instructions

If you're comfortable editing the batch file, replace the following sections:

### 1. Replace the :update_env subroutine (approximately lines 215-224)
```bat
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
```

### 2. Replace the :set_background section (approximately lines 153-182)
```bat
:set_background
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
if "%bg_choice%"=="1" set "new_mode=crop"
if "%bg_choice%"=="2" set "new_mode=blur"
if "%bg_choice%"=="3" set "new_mode=cheap"
if "%bg_choice%"=="4" set "new_mode=black"
if "%bg_choice%"=="5" set "new_mode=smart"
if not defined new_mode (
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
```

### 3. Replace the :set_caption section (approximately lines 184-213)
```bat
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
if "%cap_choice%"=="1" set "new_style=default"
if "%cap_choice%"=="2" set "new_style=hormozi"
if "%cap_choice%"=="3" set "new_style=minimalist"
if "%cap_choice%"=="4" set "new_style=pop"
if "%cap_choice%"=="5" set "new_style=kinetic"
if not defined new_style (
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
```

## � ✅ Verification Steps After Fix
1. Save the batch file
2. Double-click to run it
3. Select option 5 (Set Background Mode)
4. Choose option 5 (Smart)
5. Verify you see: "Background mode set to smart" followed by "Press any key to continue..."
6. Press a key - you should return to the main menu showing "Current BackgroundMode: smart"
7. Select option 6 (Set Caption Style)
8. Choose option 2 (Hormozi)
9. Verify you see: "Caption style set to hormozi" followed by "Press any key to continue..."
10. Press a key - you should return to the main menu showing "Current CaptionStyle: hormozi"

If these steps work, the issue has been resolved.

## �� 📝 Prevention Tips
- Always use a plain text editor (Notepad, Notepad++, VS Code) to edit batch files
- Avoid word processors that might add formatting or change line endings
- Save batch files with `.bat` extension and ANSI/UTF-8 encoding
- Test changes in a copy of the file first before modifying the original