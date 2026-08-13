@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Campaign clipper control panel.
REM Same menu shape as the Shorts and ranking lanes so there is one operating
REM pattern across all three.

if not defined PYTHON set PYTHON=python

:menu
cls
echo ============================================================
echo   CAMPAIGN CLIPPER
echo ============================================================
echo.
echo   SETUP
echo     1. Environment check
echo     2. Install dependencies
echo     3. Sign in to the campaign board (once per machine)
echo     4. Authenticate the upload channel
echo.
echo   CAMPAIGNS
echo     5. List YouTube campaigns on the board
echo     6. Pull a campaign by URL and compile its spec
echo     7. Add a campaign from a pasted requirements file
echo     8. List local campaign specs
echo.
echo   BUILD  (publishes nothing)
echo     9. Download a campaign's content folder
echo    10. Build clips
echo    11. Build without the copy model
echo    12. Run end to end
echo.
echo   PUBLISH
echo    13. Upload a validated clip
echo    14. Submit an uploaded clip's link
echo    15. Fill the submission form but do not click
echo.
echo   MAINTENANCE
echo    16. Status
echo    17. Purge temp
echo    18. Purge a campaign's sources
echo    19. Delete already-submitted local files
echo    20. Run tests
echo.
echo     0. Exit
echo.
set /p choice=Choose: 

if "%choice%"=="1" (%PYTHON% -m src.main --mode test & pause & goto menu)
if "%choice%"=="2" (%PYTHON% -m pip install -r requirements.txt & %PYTHON% -m playwright install chromium & pause & goto menu)
if "%choice%"=="3" (%PYTHON% -m src.main --mode login & pause & goto menu)
if "%choice%"=="4" goto authchannel
if "%choice%"=="5" (%PYTHON% -m src.main --mode campaigns --platform youtube & pause & goto menu)
if "%choice%"=="6" goto pull
if "%choice%"=="7" goto addspec
if "%choice%"=="8" (%PYTHON% -m src.main --mode specs & pause & goto menu)
if "%choice%"=="9" goto getsources
if "%choice%"=="10" goto build
if "%choice%"=="11" goto buildnomodel
if "%choice%"=="12" goto runall
if "%choice%"=="13" goto upload
if "%choice%"=="14" goto submit
if "%choice%"=="15" goto fillonly
if "%choice%"=="16" (%PYTHON% -m src.main --mode status & pause & goto menu)
if "%choice%"=="17" (%PYTHON% -m src.main --mode cleanup & pause & goto menu)
if "%choice%"=="18" goto purgesources
if "%choice%"=="19" (%PYTHON% -m src.main --mode cleanup --drop-submitted & pause & goto menu)
if "%choice%"=="20" (%PYTHON% -m unittest discover -s tests -v & pause & goto menu)
if "%choice%"=="0" exit /b 0
goto menu

:authchannel
set /p ch=Channel key: 
%PYTHON% -c "from src.publisher import auth; print(auth('%ch%'))"
pause
goto menu

:pull
set /p url=Campaign URL: 
set /p cid=Campaign id to save as: 
%PYTHON% -m src.main --mode pull --url "%url%" --id "%cid%"
pause
goto menu

:addspec
set /p cid=Campaign id: 
set /p req=Path to requirements text file: 
%PYTHON% -m src.main --mode add --id "%cid%" --file "%req%"
pause
goto menu

:getsources
set /p cid=Campaign id: 
%PYTHON% -m src.main --mode sources --id "%cid%" --refresh
pause
goto menu

:build
set /p cid=Campaign id: 
set /p n=How many clips: 
%PYTHON% -m src.main --mode build --id "%cid%" --count %n%
pause
goto menu

:buildnomodel
set /p cid=Campaign id: 
set /p n=How many clips: 
%PYTHON% -m src.main --mode build --id "%cid%" --count %n% --no-model
pause
goto menu

:runall
set /p cid=Campaign id: 
set /p n=How many clips: 
%PYTHON% -m src.main --mode run --id "%cid%" --count %n%
pause
goto menu

:upload
set /p cid=Campaign id: 
set /p clip=Clip id: 
%PYTHON% -m src.main --mode upload --id "%cid%" --clip %clip%
pause
goto menu

:submit
set /p cid=Campaign id: 
set /p clip=Clip id: 
%PYTHON% -m src.main --mode submit --id "%cid%" --clip %clip%
pause
goto menu

:fillonly
set /p cid=Campaign id: 
set /p clip=Clip id: 
%PYTHON% -m src.main --mode submit --id "%cid%" --clip %clip% --fill-only
pause
goto menu

:purgesources
set /p cid=Campaign id: 
%PYTHON% -m src.main --mode cleanup --id "%cid%" --drop-sources
pause
goto menu
