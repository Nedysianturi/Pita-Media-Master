@echo off
setlocal enabledelayedexpansion

:: ========================================================
:: Pita Media — Permanent Windows Background Service Installer
:: ========================================================

:: 1. Check for Administrator privileges
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Memerlukan hak Administrator untuk memasang service Windows.
    echo [INFO] Membuka jendela konfirmasi Administrator (UAC)...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

echo ======================================================================
echo    PITA MEDIA — INSTALLER SERVICE PERMANEN WINDOWS (AUTO-START 24/7)
echo ======================================================================
echo.

set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"

set "WORKER_VBS=%PROJECT_DIR%\scripts\pita_daemon.vbs"
set "DASHBOARD_VBS=%PROJECT_DIR%\scripts\pita_dashboard.vbs"
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "HOSTS_FILE=%WINDIR%\System32\drivers\etc\hosts"

:: 2. Configure Windows Hosts for http://pitamedia.localhost
echo [1/4] Mengonfigurasi domain lokal 'pitamedia.localhost'...
findstr /i "pitamedia.localhost" "%HOSTS_FILE%" >nul 2>&1
if %errorlevel% neq 0 (
    echo 127.0.0.1 pitamedia.localhost >> "%HOSTS_FILE%"
    echo   [OK] Domain pitamedia.localhost berhasil didaftarkan di hosts.
) else (
    echo   [OK] Domain pitamedia.localhost sudah terdaftar sebelumnya.
)

:: 3. Register Scheduled Tasks with Auto-Start on Boot/Logon
echo [2/4] Mendaftarkan Windows Scheduled Tasks (Permanent Background Services)...

schtasks /Create /TN "PitaMediaWorker" /TR "wscript.exe \"%WORKER_VBS%\"" /SC ONLOGON /RL HIGHEST /F >nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] Task 'PitaMediaWorker' berhasil didaftarkan (Auto-Start saat Boot/Logon).
) else (
    echo   [FALLBACK] Menyalin ke folder Startup User...
    copy /Y "%WORKER_VBS%" "%STARTUP_DIR%\PitaMediaWorker.vbs" >nul
)

schtasks /Create /TN "PitaMediaDashboard" /TR "wscript.exe \"%DASHBOARD_VBS%\"" /SC ONLOGON /RL HIGHEST /F >nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] Task 'PitaMediaDashboard' berhasil didaftarkan (Port 80).
) else (
    echo   [FALLBACK] Menyalin ke folder Startup User...
    copy /Y "%DASHBOARD_VBS%" "%STARTUP_DIR%\PitaMediaDashboard.vbs" >nul
)

:: 4. Create Desktop Shortcut with Pita Media 3D Logo
echo [3/4] Membuat Shortcut Desktop 'Pita Media' berlogo resmi...
cscript //nologo "%PROJECT_DIR%\scripts\create_desktop_shortcut.vbs" >nul 2>&1
if exist "%USERPROFILE%\Desktop\Pita Media.lnk" (
    echo   [OK] Shortcut Desktop 'Pita Media' siap digunakan.
)

:: 5. Start Background Services Immediately
echo [4/4] Menjalankan service Pita Media sekarang di latar belakang...
call "%PROJECT_DIR%\scripts\start_all.bat" >nul 2>&1

echo.
echo ======================================================================
echo 🎉 INSTALASI PERMANEN PITA MEDIA BERHASIL!
echo ======================================================================
echo.
echo 📌 Status Sistem:
echo   • Service Auto-Start : AKTIF (Otomatis berjalan setiap kali Windows nyala)
echo   • Background Mode    : Hening / Tanpa jendela PowerShell / Tanpa IDE
echo   • Web Dashboard URL  : http://pitamedia.localhost
echo   • Desktop Shortcut   : Ada di Desktop Anda ("Pita Media")
echo   • Remote Control     : Bot Telegram @pitamediabot
echo.
echo 💡 Mulai sekarang, Anda CUKUP nyalakan komputer. Pita Media akan bekerja
echo    sendiri 24/7. Untuk memantau, klik ganda shortcut 'Pita Media' di Desktop.
echo ======================================================================
echo.
pause
