@echo off
REM Double-click this to start the KiloVault HLX+ Monitor (source install).
REM If you downloaded the .exe instead, just double-click that — you don't need this.
title KiloVault HLX+ Monitor
cd /d "%~dp0\.."

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found.
  echo Please install Python 3.11+ from https://www.python.org/downloads/
  echo and tick "Add Python to PATH" during setup, then run this again.
  echo.
  pause
  exit /b 1
)

echo Starting the KiloVault HLX+ Monitor...
echo A web page will open in your browser. Keep this window open while monitoring.
echo Close this window (or press Ctrl+C) to stop.
echo.
python -m kilovault.cli serve --open
echo.
pause
