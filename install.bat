@echo off
title OxLog Installer
color 0A

:: ---- Self-elevate to admin ----
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process -Verb RunAs -FilePath '%~f0' -ArgumentList '%~dp0'"
    exit /b
)

:: Change to script directory (important after elevation)
cd /d "%~dp0"

echo.
echo  ============================
echo    OxLog - Oxide Plugin Changelog
echo    Installer
echo  ============================
echo.

:: ---- Python ----
python --version >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Python found:
    python --version
    echo.
    goto :install_deps
)

py --version >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Python found:
    py --version
    echo.
    goto :install_deps
)

echo [!!] Python not found. Downloading Python installer...
echo.

set PYTHON_URL=https://www.python.org/ftp/python/3.13.12/python-3.13.12-amd64.exe
set PYTHON_INSTALLER=%cd%\python_installer.exe

echo Downloading Python 3.13.12...
echo This may take a minute...
echo.

:: Method 1: PowerShell with TLS12 and silent progress
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_INSTALLER%'" 2>nul

if exist "%PYTHON_INSTALLER%" goto :python_downloaded

echo   PowerShell download failed, trying certutil...

:: Method 2: certutil
certutil -urlcache -split -f "%PYTHON_URL%" "%PYTHON_INSTALLER%" >nul 2>&1

if exist "%PYTHON_INSTALLER%" goto :python_downloaded

echo   certutil failed, trying bitsadmin...

:: Method 3: bitsadmin
bitsadmin /transfer "PythonDownload" /download /priority high "%PYTHON_URL%" "%PYTHON_INSTALLER%" >nul 2>&1

if exist "%PYTHON_INSTALLER%" goto :python_downloaded

echo.
echo [ERROR] All download methods failed.
echo.
echo Please download Python manually:
echo   https://www.python.org/downloads/
echo.
echo Make sure to check "Add Python to PATH" during installation.
echo Then re-run this installer.
pause
exit /b 1

:python_downloaded
echo [OK] Download complete.
echo.
echo Installing Python (this may take a minute)...
echo.
"%PYTHON_INSTALLER%" /passive InstallAllUsers=1 PrependPath=1 Include_pip=1

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Python installation failed.
    echo Please install Python manually from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

del "%PYTHON_INSTALLER%" >nul 2>&1

echo.
echo [OK] Python installed.
echo.

:: Refresh PATH for this session
set "PATH=%PATH%;C:\Program Files\Python313;C:\Program Files\Python313\Scripts;%LOCALAPPDATA%\Programs\Python\Python313;%LOCALAPPDATA%\Programs\Python\Python313\Scripts"

:install_deps
echo Installing Python dependencies...
echo.

pip install flask requests waitress --quiet 2>nul
if %errorlevel% neq 0 (
    py -m pip install flask requests waitress --quiet 2>nul
    if %errorlevel% neq 0 (
        python -m pip install flask requests waitress --quiet 2>nul
    )
)

echo [OK] Dependencies installed (Flask, Requests, Waitress)
echo.

:: ---- Directories ----
if not exist "templates" mkdir templates
if not exist "versions" mkdir versions
if not exist "backup" mkdir backup

echo [OK] Directories created
echo.

:: ---- Config ----
if exist "config.json" (
    echo [OK] Existing config.json found - keeping it
) else (
    echo [OK] Fresh install - setup wizard will run on first launch
)
echo.

:: ---- Firewall Rule ----
echo Configuring firewall...
netsh advfirewall firewall show rule name="OxLog" >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Firewall rule already exists
) else (
    netsh advfirewall firewall add rule name="OxLog" dir=in action=allow protocol=TCP localport=5000 profile=any >nul 2>&1
    if %errorlevel% equ 0 (
        echo [OK] Firewall rule created (TCP port 5000 inbound)
    ) else (
        echo [!!] Could not create firewall rule. You may need to allow port 5000 manually.
    )
)
echo.

:: ---- Scheduled Task ----
echo Configuring auto-start...

set PYTHON_EXE=
where pythonw >nul 2>&1
if %errorlevel% equ 0 (
    for /f "delims=" %%i in ('where pythonw') do set "PYTHON_EXE=%%i"
)
if "%PYTHON_EXE%"=="" (
    where python >nul 2>&1
    if %errorlevel% equ 0 (
        for /f "delims=" %%i in ('where python') do set "PYTHON_EXE=%%i"
    )
)

if "%PYTHON_EXE%"=="" (
    echo [!!] Could not find Python executable for scheduled task.
    echo     You can set up auto-start manually via Task Scheduler.
    goto :skip_task
)

set "OXLOG_DIR=%cd%"

schtasks /query /tn "OxLog" >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Scheduled task already exists
    choice /c YN /m "     Overwrite existing task?"
    if errorlevel 2 goto :skip_task
    schtasks /delete /tn "OxLog" /f >nul 2>&1
)

schtasks /create /tn "OxLog" /tr "\"%PYTHON_EXE%\" \"%OXLOG_DIR%\OxLog.py\"" /sc onstart /ru SYSTEM /rl HIGHEST /f >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Scheduled task created (runs on system startup)
) else (
    schtasks /create /tn "OxLog" /tr "\"%PYTHON_EXE%\" \"%OXLOG_DIR%\OxLog.py\"" /sc onlogon /rl HIGHEST /f >nul 2>&1
    if %errorlevel% equ 0 (
        echo [OK] Scheduled task created (runs on user logon)
    ) else (
        echo [!!] Could not create scheduled task.
        echo      Set up manually in Task Scheduler if needed.
    )
)

:skip_task
echo.
echo  ============================
echo    Installation Complete!
echo  ============================
echo.
echo  To start OxLog now:
echo    Double-click  start.bat
echo.
echo  OxLog will be available at:
echo    http://localhost:5000
echo.
echo  Auto-start and firewall are configured.
echo.
pause
