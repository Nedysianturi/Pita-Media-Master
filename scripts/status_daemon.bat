@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0\.."

echo ========================================================
echo   Pita Media - Daemon Status Monitor
echo ========================================================

set "LOCK_FILE=storage\pita_media.lock"
set "LOG_FILE=storage\logs\pita_media.log"

if not exist "%LOCK_FILE%" (
    echo [STATUS] INACTIVE (No daemon process currently running)
    goto show_logs
)

set "PID="
set "TIMESTAMP="
set "HOSTNAME="

for /f "usebackq tokens=1,2,3 delims=," %%A in ("%LOCK_FILE%") do (
    set "PID=%%A"
    set "TIMESTAMP=%%B"
    set "HOSTNAME=%%C"
)

if not defined PID (
    echo [STATUS] INACTIVE (Lockfile is empty or invalid)
    goto show_logs
)

echo [STATUS] ACTIVE (Lockfile Present)
echo   - PID: !PID!
echo   - Started: !TIMESTAMP!
echo   - Host: !HOSTNAME!
echo.
echo Checking tasklist for PID !PID!...
tasklist /FI "PID eq !PID!" 2>nul | find /i "!PID!" >nul
if errorlevel 1 (
    echo [WARNING] PID !PID! is NOT active in Windows Task Manager (Stale lockfile).
) else (
    echo [OK] Process is actively running in background.
)

:show_logs
echo.
echo ========================================================
echo   Recent Log Snippet (Last 10 lines)
echo ========================================================
if exist "%LOG_FILE%" (
    powershell -NoProfile -Command "if (Test-Path '%LOG_FILE%') { Get-Content '%LOG_FILE%' -Tail 10 }"
) else (
    echo No log file found at %LOG_FILE% yet.
)

echo ========================================================
