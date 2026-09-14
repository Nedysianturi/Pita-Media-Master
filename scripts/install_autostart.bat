@echo off
setlocal
cd /d "%~dp0\.."

echo ========================================================
echo   Pita Media - Installing Windows Auto-Start Service
echo ========================================================

set "TASK_NAME=PitaMediaAutonomousDaemon"
set "VBS_PATH=%CD%\scripts\pita_daemon.vbs"
set "STARTUP_SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PitaMediaDaemon.vbs"

echo [1/2] Registering Windows Scheduled Task: %TASK_NAME%...
schtasks /Create /TN "%TASK_NAME%" /TR "wscript.exe \"%VBS_PATH%\"" /SC ONLOGON /RL HIGHEST /F >nul 2>&1

if %errorlevel% equ 0 (
    echo [SUCCESS] Windows Scheduled Task '%TASK_NAME%' created successfully!
    echo Pita Media will now start automatically whenever you log into Windows.
) else (
    echo [WARN] Scheduled Task registration required administrator privilege.
    echo [2/2] Using Windows User Startup Folder fallback...
    copy /Y "%VBS_PATH%" "%STARTUP_SHORTCUT%" >nul
    if exist "%STARTUP_SHORTCUT%" (
        echo [SUCCESS] Auto-start launcher installed in Windows Startup folder:
        echo   %STARTUP_SHORTCUT%
    ) else (
        echo [ERROR] Failed to install auto-start.
    )
)

echo.
echo ========================================================
echo Auto-start configuration complete!
echo To test run now, execute: scripts\start_daemon.bat
echo ========================================================
