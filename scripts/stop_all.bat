@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0\.."

echo ========================================================
echo   Pita Media - Stopping All Background Services
echo ========================================================

echo [1/2] Stopping Worker Daemon...
call "%~dp0stop_daemon.bat" >nul 2>&1

echo [2/2] Stopping Web Dashboard...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":80 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8080 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)

echo.
echo ========================================================
echo [SUCCESS] All Pita Media services stopped.
echo ========================================================
