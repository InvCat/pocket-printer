@echo off
REM Starts the Windows raw print bridge for Tronic Mini Pocket Printer.
REM Usage example:
REM   start_windows_printer_bridge.bat --port COM5

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo Python is not installed.
    echo Download: https://www.python.org/downloads/
    echo During install, enable "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 goto fail
)

echo Installing/updating dependencies...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet pillow pyserial
if errorlevel 1 goto fail

if "%~1"=="" (
    echo.
    echo Missing connection argument.
    echo Example: start_windows_printer_bridge.bat --port COM5
    echo          start_windows_printer_bridge.bat --address 55:55:09:10:98:B6
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" windows_print_bridge.py %*
exit /b %errorlevel%

:fail
echo.
echo Failed to start bridge.
pause
exit /b 1
