@echo off
setlocal
cd /d "%~dp0\.."

echo ========================================================
echo   Pita Media - Uninstalling Windows Background Services
echo ========================================================

echo [1/3] Stopping all running processes...
call "%~dp0stop_all.bat" >nul 2>&1

echo [2/3] Removing Scheduled Tasks...
schtasks /Delete /TN "PitaMediaWorker" /F >nul 2>&1
schtasks /Delete /TN "PitaMediaDashboard" /F >nul 2>&1
schtasks /Delete /TN "PitaMediaAutonomousDaemon" /F >nul 2>&1

echo [3/3] Removing Startup folder shortcuts...
del /f /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PitaMedia*.vbs" >nul 2>&1

echo.
echo ========================================================
echo [SUCCESS] Pita Media services cleanly uninstalled.
echo ========================================================
