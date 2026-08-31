@echo off
REM Builds the Android APK without Android Studio.
REM It downloads portable JDK, Android SDK cmdline tools and Gradle.

cd /d "%~dp0"

where powershell >nul 2>&1
if errorlevel 1 (
    echo PowerShell not found.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File ".\build_android_apk_no_studio.ps1"
if errorlevel 1 (
    echo.
    echo APK build failed.
    pause
    exit /b 1
)

echo.
echo APK build finished.
pause
exit /b 0
