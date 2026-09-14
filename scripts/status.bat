@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0\.."

echo ========================================================
echo   Pita Media - Full System Status
echo ========================================================

echo [1] WORKER DAEMON STATUS:
call "%~dp0status_daemon.bat"

echo.
echo [2] WEB DASHBOARD STATUS:
netstat -aon | findstr ":80 " | findstr "LISTENING" >nul
if %errorlevel% equ 0 (
    echo [OK] Web Dashboard is actively listening on Port 80 (http://pitamedia.localhost)
    goto end
)
netstat -aon | findstr ":8080 " | findstr "LISTENING" >nul
if %errorlevel% equ 0 (
    echo [OK] Web Dashboard is listening on Port 8080 (http://localhost:8080)
    goto end
)
echo [INACTIVE] Web Dashboard is not currently running.

:end
echo ========================================================
