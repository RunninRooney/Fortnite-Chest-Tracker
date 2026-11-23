@echo off
rem Chest Tracker. Only installs Python the first time; everything else (updates, checks,
rem starting the app, error reports) is done by launcher.py, which can update itself.
setlocal
cd /d "%~dp0"
title Chest Tracker
if not exist "%~dp0launcher.py" goto notunzipped
if not exist "%~dp0chest_tracker.py" goto notunzipped
set "VENV=%LOCALAPPDATA%\ChestTracker\venv"

rem Files from a downloaded zip are marked "from the internet", so Windows asks "are you sure?"
rem every time. This folder was run on purpose, so take that mark off its files.
powershell -NoProfile -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse | Unblock-File" >nul 2>&1

if exist "%VENV%\Scripts\python.exe" goto launch

echo.
echo  First-time setup. This downloads what Chest Tracker needs (a few hundred MB).
echo  It only happens once and takes 5-10 minutes. Keep this window open.
echo.
set "PY="
py -3.12 -c "import tkinter" >nul 2>&1 && set "PY=py -3.12"
if not defined PY (py -3.13 -c "import tkinter" >nul 2>&1 && set "PY=py -3.13")
if not defined PY (py -3.11 -c "import tkinter" >nul 2>&1 && set "PY=py -3.11")
if not defined PY call :findpython
if not defined PY (
  echo  Python is not installed yet. Installing Python 3.12 from Microsoft's winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  call :findpython
)
if not defined PY goto nopython
echo  Using Python: %PY%
%PY% -m venv "%VENV%" || goto fail
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip --quiet
"%VENV%\Scripts\python.exe" -m pip install --ignore-requires-python -r requirements.txt || goto fail

:launch
"%VENV%\Scripts\python.exe" "%~dp0launcher.py"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (echo. & echo  Something went wrong ^(see above^). & pause)
if "%RC%"=="0" timeout /t 8 >nul 2>&1
rem An update can replace this file; it's swapped in here, on the last line, after it's done running.
if exist "%~dp0Start Chest Tracker.bat.new" (move /y "%~dp0Start Chest Tracker.bat.new" "%~dp0Start Chest Tracker.bat" >nul & exit /b %RC%)
exit /b %RC%

:findpython
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"" & exit /b 0
if exist "%ProgramFiles%\Python312\python.exe" set "PY="%ProgramFiles%\Python312\python.exe"" & exit /b 0
exit /b 0

:notunzipped
echo.
echo  Chest Tracker needs to be unzipped first.
echo  Close this window, right-click ChestTracker.zip, choose "Extract All...",
echo  then open the extracted ChestTracker folder and run "Start Chest Tracker.bat" there.
echo.
pause
exit /b 1

:nopython
echo.
echo  Could not install Python automatically.
echo  Install Python 3.12 from the Microsoft Store (search "Python 3.12"), then run this file again.
pause
exit /b 1

:fail
echo.
echo  Setup failed (see the messages above). Check your internet connection and run this file again.
echo  If it keeps failing, delete the folder %LOCALAPPDATA%\ChestTracker\venv and retry.
rmdir /s /q "%VENV%" >nul 2>&1
pause
exit /b 1
