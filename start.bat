@echo off
title OxLog
color 0A
cd /d "%~dp0"

echo.
echo  OxLog - Oxide Plugin Changelog
echo  ================================
echo.

:: Try python, then py
python --version >nul 2>&1
if %errorlevel% equ 0 (
    echo Starting OxLog...
    echo.
    python OxLog.py
    goto :done
)

py --version >nul 2>&1
if %errorlevel% equ 0 (
    echo Starting OxLog...
    echo.
    py OxLog.py
    goto :done
)

echo [ERROR] Python not found. Run install.bat first.
echo.

:done
pause
