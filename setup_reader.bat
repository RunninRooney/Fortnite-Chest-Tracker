@echo off
rem Builds the replay reader (reader\ChestReplayReader.exe) on this PC from the source in reader_src.
rem It is built here instead of being included in the zip, because browsers block programs
rem that few people have downloaded. Needs Microsoft's .NET 10 SDK. If it's missing, it's installed
rem into this Windows user's own folder (no admin prompt) with Microsoft's dotnet-install script,
rem and winget is tried if that fails.
setlocal
cd /d "%~dp0"
if exist "reader\ChestReplayReader.exe" exit /b 0
set DOTNET_CLI_TELEMETRY_OPTOUT=1
set DOTNET_NOLOGO=1
if not defined CT_DOTNET_HOME set "CT_DOTNET_HOME=%LOCALAPPDATA%\Microsoft\dotnet"
set "CT_INSTALLER=%TEMP%\ct-dotnet-install.ps1"

echo.
echo  Setting up the replay reader (it counts the chests you BREAK). This happens once.
call :find
if defined DOTNET goto build

echo  Downloading Microsoft .NET 10 SDK from dot.net (about 300 MB, a few minutes)...
if exist "%CT_INSTALLER%" del /q "%CT_INSTALLER%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing 'https://dot.net/v1/dotnet-install.ps1' -OutFile $env:CT_INSTALLER"
if exist "%CT_INSTALLER%" powershell -NoProfile -ExecutionPolicy Bypass -File "%CT_INSTALLER%" -Channel 10.0 -InstallDir "%CT_DOTNET_HOME%" -NoPath
call :find
if defined DOTNET goto build

where winget >nul 2>nul || goto nosdk
echo  Trying winget instead. Windows will ask for permission: click Yes.
winget install -e --id Microsoft.DotNet.SDK.10 --accept-package-agreements --accept-source-agreements
call :find
if not defined DOTNET goto nosdk

:build
echo  Building the replay reader (about a minute)...
rem A .NET in this user's folder isn't found by programs on their own, so the reader carries its own copy then.
set "SELF=false"
if /i not "%DOTNET%"=="%ProgramFiles%\dotnet\dotnet.exe" set "SELF=true"
"%DOTNET%" publish reader_src -c Release -r win-x64 --self-contained %SELF% -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -p:DebugType=none -o reader --nologo -v q
if not exist "reader\ChestReplayReader.exe" goto failed
echo  Replay reader ready.
exit /b 0

:find
rem Sets DOTNET to a dotnet.exe that has a .NET 10 SDK, or leaves it empty. Only runs one that exists:
rem in a batch file, a missing program on the left of a pipe ends the whole script.
set "DOTNET="
for %%D in ("%ProgramFiles%\dotnet\dotnet.exe" "%CT_DOTNET_HOME%\dotnet.exe") do (
    if not defined DOTNET if exist "%%~D" (
        "%%~D" --list-sdks 2>nul | findstr /b "10." >nul && set "DOTNET=%%~D"
    )
)
exit /b 0

:nosdk
echo.
echo  Could not install .NET. Chest Tracker still counts opened chests live, but it
echo  can't count broken chests or past matches until this works.
echo  To try again later, double-click setup_reader.bat.
pause
exit /b 1

:failed
echo.
echo  Building the replay reader failed. Check your internet connection, then
echo  double-click setup_reader.bat to try again. Live counting still works.
pause
exit /b 1
