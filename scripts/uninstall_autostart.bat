@echo off
setlocal
cd /d "%~dp0\.."

echo ========================================================
echo   Pita Media - Removing Windows Auto-Start Service
echo ========================================================

set "TASK_NAME=PitaMediaAutonomousDaemon"
set "STARTUP_SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PitaMediaDaemon.vbs"

echo [1/2] Removing Scheduled Task '%TASK_NAME%'...
schtasks /Delete /TN "%TASK_NAME%" /F >nul 2>&1
if %errorlevel% equ 0 (
    echo [SUCCESS] Scheduled Task removed.
) else (
    echo [INFO] Scheduled Task was not present.
)

echo [2/2] Removing Startup folder shortcut...
if exist "%STARTUP_SHORTCUT%" (
    del /f /q "%STARTUP_SHORTCUT%" >nul 2>&1
    echo [SUCCESS] Startup folder entry removed.
) else (
    echo [INFO] No Startup folder entry found.
)

echo.
echo ========================================================
echo Auto-start has been cleanly uninstalled.
echo ========================================================
