@echo off
setlocal
cd /d "%~dp0\.."

echo ========================================================
echo   Pita Media - Installing Autonomous Windows Services
echo ========================================================

set "WORKER_TASK=PitaMediaWorker"
set "DASHBOARD_TASK=PitaMediaDashboard"
set "WORKER_VBS=%CD%\scripts\pita_daemon.vbs"
set "DASHBOARD_VBS=%CD%\scripts\pita_dashboard.vbs"
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

echo [1/3] Registering Worker Service (%WORKER_TASK%)...
schtasks /Create /TN "%WORKER_TASK%" /TR "wscript.exe \"%WORKER_VBS%\"" /SC ONLOGON /RL HIGHEST /F >nul 2>&1
if %errorlevel% neq 0 (
    echo [FALLBACK] Adding Worker to User Startup folder...
    copy /Y "%WORKER_VBS%" "%STARTUP_DIR%\PitaMediaWorker.vbs" >nul
)

echo [2/3] Registering Web Dashboard Service (%DASHBOARD_TASK%)...
schtasks /Create /TN "%DASHBOARD_TASK%" /TR "wscript.exe \"%DASHBOARD_VBS%\"" /SC ONLOGON /RL HIGHEST /F >nul 2>&1
if %errorlevel% neq 0 (
    echo [FALLBACK] Adding Dashboard to User Startup folder...
    copy /Y "%DASHBOARD_VBS%" "%STARTUP_DIR%\PitaMediaDashboard.vbs" >nul
)

echo [3/3] Setting up local domain resolution (pitamedia.localhost)...
echo 127.0.0.1 pitamedia.localhost >> "%WINDIR%\System32\drivers\etc\hosts" >nul 2>&1

echo.
echo ========================================================
echo [SUCCESS] Pita Media Background Services Installed!
echo.
echo • Background Worker: Running 24/7 with crash recovery
echo • Web Dashboard URL: http://pitamedia.localhost
echo • Telegram Control:  @pitamediabot
echo.
echo To start now: scripts\start_all.bat
echo ========================================================
