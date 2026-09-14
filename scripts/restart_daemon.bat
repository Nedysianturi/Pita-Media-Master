@echo off
setlocal
cd /d "%~dp0"

echo ========================================================
echo   Pita Media - Restarting Background Daemon
echo ========================================================

call stop_daemon.bat
timeout /t 2 /nobreak >nul
call start_daemon.bat
