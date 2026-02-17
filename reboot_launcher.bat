@echo off
REM Reboot launcher for SSH Connection Manager
REM This script waits for the old instance to close, then launches the new one

echo SSH Connection Manager - Reboot Launcher
echo Waiting for application to close...

REM Wait 3 seconds to ensure the old instance has fully closed
timeout /t 3 /nobreak >nul

REM Get the directory where this batch file is located
set "APP_DIR=%~dp0"

REM Launch the application from the dist folder
echo Starting SSH Connection Manager...
start "" "%APP_DIR%dist\SSH-Connection-Manager.exe"

echo Done!
exit
