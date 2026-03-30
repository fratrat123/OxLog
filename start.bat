@echo off
title OxLog
color 0A
cd /d "%~dp0"

echo.
echo  OxLog - Oxide Plugin Changelog
echo  ================================
echo.

:start
:: Try python, then py
python --version >nul 2>&1
if %errorlevel% equ 0 (
    echo Starting OxLog...
    echo.
    python OxLog.py
    echo.
    echo  OxLog stopped. Restarting in 3 seconds...
    timeout /t 3 /nobreak >nul
    goto :start
)

py --version >nul 2>&1
if %errorlevel% equ 0 (
    echo Starting OxLog...
    echo.
    py OxLog.py
    echo.
    echo  OxLog stopped. Restarting in 3 seconds...
    timeout /t 3 /nobreak >nul
    goto :start
)

echo [ERROR] Python not found. Run install.bat first.
echo.
pause
