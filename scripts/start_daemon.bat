@echo off
setlocal
cd /d "%~dp0\.."

echo ========================================================
echo   Pita Media - Starting Autonomous Background Daemon
echo ========================================================

REM Check if Python virtual environment exists
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found at .venv\Scripts\python.exe!
    echo Please run setup first.
    pause
    exit /b 1
)

REM Launch hidden daemon via VBScript
wscript.exe "%~dp0pita_daemon.vbs"

timeout /t 2 /nobreak >nul

REM Check status
if exist "storage\pita_media.lock" (
    echo [SUCCESS] Pita Media Background Daemon successfully launched!
    echo Logs: storage\logs\pita_media.log
    echo Control: Telegram Bot @pitamediabot
) else (
    echo [INFO] Daemon is starting up... Please check logs with scripts\status_daemon.bat
)

echo ========================================================
