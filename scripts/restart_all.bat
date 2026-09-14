@echo off
setlocal
cd /d "%~dp0"

echo ========================================================
echo   Pita Media - Restarting All Background Services
echo ========================================================

call stop_all.bat
timeout /t 2 /nobreak >nul
call start_all.bat
