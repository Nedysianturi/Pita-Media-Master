@echo off
setlocal
cd /d "%~dp0\.."

echo ========================================================
echo   Pita Media - Starting All Background Services
echo ========================================================

echo [1/2] Starting Web Command Center (http://pitamedia.localhost)...
wscript.exe "%~dp0pita_dashboard.vbs"

echo [2/2] Starting Autonomous Worker Daemon...
wscript.exe "%~dp0pita_daemon.vbs"

timeout /t 2 /nobreak >nul

echo.
echo ========================================================
echo [SUCCESS] Services Started!
echo • Open Dashboard: http://pitamedia.localhost
echo • Status Check:   scripts\status.bat
echo ========================================================
