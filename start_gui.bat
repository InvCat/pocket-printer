@echo off
REM Tronic Mini Pocket Printer (A2Y) kezelofelulet inditasa Windows alatt.
REM Elso futtataskor letrehoz egy sajat Python kornyezetet es telepiti a
REM szukseges csomagokat. Utana mar azonnal indul.

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo Nincs telepitve a Python. Toltsd le: https://www.python.org/downloads/
    echo FONTOS: a telepitonel pipald ki az "Add python.exe to PATH" opciot!
    echo.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Python kornyezet letrehozasa...
    python -m venv .venv
    if errorlevel 1 goto fail
    echo Csomagok telepitese ^(pillow, pyserial^)...
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet pillow pyserial
    if errorlevel 1 goto fail
)

".venv\Scripts\python.exe" tronic_gui.py
if errorlevel 1 goto fail
exit /b 0

:fail
echo.
echo Hiba tortent. A fenti uzenet alapjan lehet tovabblepni.
pause
exit /b 1
