@echo off
REM One-click setup for Tronic mini printer on Windows 10/11.
REM Double click this file, or run from cmd:
REM   install_one_click_driver.bat
REM Optional:
REM   install_one_click_driver.bat -ComPort COM5
REM   install_one_click_driver.bat -Address 55:55:09:10:98:B6

cd /d "%~dp0"
set "TRONIC_LOG=%TEMP%\tronic_one_click_driver_install.log"

where powershell >nul 2>&1
if errorlevel 1 (
    echo PowerShell was not found.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File ".\one_click_driver_install.ps1" -LogPath "%TRONIC_LOG%" %*
if errorlevel 1 (
    echo.
    echo Installation failed.
    if exist "%TRONIC_LOG%" (
        echo Log file: %TRONIC_LOG%
        echo Opening log...
        start "" notepad "%TRONIC_LOG%"
    )
    pause
    exit /b 1
)

echo.
echo Installation finished.
pause
exit /b 0
