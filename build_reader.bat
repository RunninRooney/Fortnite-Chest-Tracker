@echo off
rem Builds reader\ChestReplayReader.exe (needs the .NET 10 SDK: winget install Microsoft.DotNet.SDK.10).
rem Self-contained, so the siblings' PCs don't need .NET installed.
cd /d "%~dp0"
dotnet publish reader_src -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -p:DebugType=none -o reader || (pause & exit /b 1)
echo Built reader\ChestReplayReader.exe
