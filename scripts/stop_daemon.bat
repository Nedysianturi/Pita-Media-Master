@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0\.."

echo ========================================================
echo   Pita Media - Stopping Background Daemon
echo ========================================================

set "LOCK_FILE=storage\pita_media.lock"

if not exist "%LOCK_FILE%" (
    echo [INFO] No active lock file found. Daemon is not currently running.
    goto cleanup
)

for /f "tokens=1,2 delims=," %%A in (%LOCK_FILE%) do (
    set "PID=%%A"
)

if defined PID (
    echo [INFO] Stopping Pita Media Daemon process (PID: %PID%)...
    taskkill /PID %PID% /T /F >nul 2>&1
    if errorlevel 1 (
        echo [WARN] Process %PID% was not found or already terminated.
    ) else (
        echo [SUCCESS] Daemon process %PID% terminated.
    )
)

:cleanup
if exist "%LOCK_FILE%" del /f /q "%LOCK_FILE%" >nul 2>&1
echo [SUCCESS] Background daemon stopped.
echo ========================================================
