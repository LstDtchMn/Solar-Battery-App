@echo off
REM Run this once to install what the monitor needs (source install).
REM Not needed if you downloaded the ready-made KiloVaultMonitor.exe.
title KiloVault HLX+ Monitor - Install
cd /d "%~dp0\.."

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found.
  echo 1) Install Python 3.11+ from https://www.python.org/downloads/
  echo 2) IMPORTANT: tick "Add Python to PATH" during setup.
  echo 3) Run this file again.
  echo.
  pause
  exit /b 1
)

echo Installing Bluetooth and serial support (needs internet just this once)...
python -m pip install --upgrade pip
python -m pip install bleak pyserial
echo.
echo Done. Now double-click "Start KiloVault Monitor.bat".
echo.
pause
