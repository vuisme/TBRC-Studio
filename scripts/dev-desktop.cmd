@echo off
setlocal

call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat" -arch=x64
if errorlevel 1 exit /b %errorlevel%

set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
set TAURI_SKIP_BACKEND=1

bun run --cwd frontend desktop
