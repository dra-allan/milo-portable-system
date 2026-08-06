# How to Fix the Batch File Settings Issue

## ���� ������ �� ���� Problem
When selecting options 5 (Set Background Mode) or 6 (Set Caption Style) in run_pipeline.bat, the terminal window closes immediately after entering a number and pressing Enter, instead of showing confirmation and returning to the main menu.

## ��� ������ � ���� Root Cause
This is caused by a syntax error in the batch file's conditional statements. The original format:
```bat
if not defined new_mode (
    echo Invalid choice!
    pause
    goto set_background
)
```
Can sometimes cause issues with batch file parsing, leading to immediate exit when the condition is false (i.e., when a valid choice is entered).

## ��� ������ � ���� Solution
Replace the settings sections in `run_pipeline.bat` with the corrected versions below, which use explicit else-if logic that is more robust and less prone to parsing issues.

## ��� ������ � ���� Steps to Apply the Fix

### 1. Backup Your File (Important!)
First, make a backup of your current batch file:
```
copy run_pipeline.bat run_pipeline.bat.backup
```

### 2. Open the File for Editing
- Right-click on `run_pipeline.bat`
- Select "Edit" (if using Notepad++) or "Open with" → Notepad
- Do NOT use WordPad or Microsoft Word as they can corrupt the file

### 3. Replace the :update_env Subroutine
Find the section starting with `:update_env` (around line 215) and replace it with:
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

### 4. Replace the :set_background Section
Find the section starting with `:set_background` (around line 153) and replace it with:
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
```

### 5. Replace the :set_caption Section
Find the section starting with `:set_caption` (around line 184) and replace it with:
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
```

### 6. Save the File
- Save the changes (Ctrl+S in Notepad++)
- Close the editor

### 7. Test the Fix
Double-click `run_pipeline.bat` to run it:
1. Select option 5 (Set Background Mode)
2. Choose option 5 (Smart) and press Enter
3. You should see: "Background mode set to smart" followed by "Press any key to continue..."
4. Press any key - you should return to the main menu showing "Current BackgroundMode: smart"
5. Select option 6 (Set Caption Style)
6. Choose option 2 (Hormozi) and press Enter
7. You should see: "Caption style set to hormozi" followed by "Press any key to continue..."
8. Press any key - you should return to the main menu showing "Current CaptionStyle: hormozi"

## ���� ������ �� ���� Verification
If the fix works correctly:
- � ✅ Options 5 and 6 will no longer close the terminal immediately
- � ✅ You will see confirmation messages after selecting options
- � ✅ You will return to the main menu after pressing a key
- � ✅ The selected settings will persist in the `.env` file
- � ✅ All other options (1-4, 7) will continue to work as before

## ���� ������ �� ���� Troubleshooting If Issues Persist
If you still experience problems:

1. **Check file encoding**: Ensure the file is saved as ANSI or UTF-8 without BOM
2. **Verify line endings**: Use Windows CRLF (not Unix LF)
3. **Look for hidden characters**: Some editors insert invisible characters that can break batch files
4. **Test with a simple batch file**: Create a minimal test to verify your environment processes batch files correctly
5. **Check antivirus software**: Some security software may interfere with batch file execution

## ���� ������ �� ���� Why This Fix Is More Robust
The revised format uses explicit `else if` clauses which:
- Are less prone to batch file parsing quirks
- Make the logic flow more obvious and easier to debug
- Eliminate any ambiguity about where the IF statement ends
- Follow batch file best practices for complex conditional logic

The core functionality you requested (smart person-aware cropping, enhanced caption styles, unique titles, persistent settings) is all correctly implemented in the Python code - this fix only addresses the batch file user interface for changing those settings.