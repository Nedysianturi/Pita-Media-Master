@echo off
setlocal enabledelayedexpansion

:: Check for Administrator privileges
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

echo ======================================================================
echo    PITA MEDIA — UNINSTALLER SERVICE PERMANEN WINDOWS
echo ======================================================================
echo.

echo [1/4] Menghentikan seluruh proses berjalan...
call "%~dp0scripts\stop_all.bat" >nul 2>&1

echo [2/4] Menghapus Scheduled Tasks...
schtasks /Delete /TN "PitaMediaWorker" /F >nul 2>&1
schtasks /Delete /TN "PitaMediaDashboard" /F >nul 2>&1
schtasks /Delete /TN "PitaMediaAutonomousDaemon" /F >nul 2>&1

echo [3/4] Menghapus file Startup User...
del /f /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PitaMedia*.vbs" >nul 2>&1

echo [4/4] Menghapus Shortcut Desktop...
del /f /q "%USERPROFILE%\Desktop\Pita Media.lnk" >nul 2>&1

echo.
echo ======================================================================
echo [SUCCESS] Pita Media services & shortcut telah bersih di-uninstall.
echo ======================================================================
echo.
pause
