@echo off
:: First-time setup for elscione-dl on Windows
:: Double-click this once to install everything needed.

chcp 65001 >nul
echo === elscione-dl setup ===
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo.
    echo Please install Python 3.11 or later from https://python.org/downloads
    echo Make sure to tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

echo Python found:
python --version
echo.

:: Install the package and dependencies
echo Installing dependencies...
python -m pip install -e "%~dp0." --quiet
if errorlevel 1 (
    echo.
    echo ERROR: Installation failed. See error above.
    pause
    exit /b 1
)

echo.
echo === Setup complete! ===
echo.
echo To use:
echo   Double-click  run.bat       to open the interactive browser
echo   Double-click  download.bat  to download a specific title
echo.
pause
