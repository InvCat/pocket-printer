param(
    [string]$JdkDir = "",
    [string]$AndroidSdkDir = "",
    [string]$GradleDir = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Ensure-Dir([string]$path) {
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Path $path | Out-Null
    }
}

function Download-File([string]$url, [string]$target) {
    Write-Host "Downloading: $url" -ForegroundColor DarkGray
    Invoke-WebRequest -Uri $url -OutFile $target -UseBasicParsing
}

function Expand-Zip([string]$zipPath, [string]$destDir) {
    Expand-Archive -Path $zipPath -DestinationPath $destDir -Force
}

function Find-Bin([string]$root, [string]$exeName) {
    $found = Get-ChildItem -Path $root -Recurse -Filter $exeName -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $found) {
        throw "Could not find $exeName under $root"
    }
    return $found.FullName
}

function Try-FindBin([string]$root, [string]$exeName) {
    $found = Get-ChildItem -Path $root -Recurse -Filter $exeName -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { return $found.FullName }
    return $null
}

function Normalize-WindowsPathForLocalProperties([string]$path) {
    return ($path -replace "\\", "\\\\")
}

$projectDir = Split-Path -Parent $PSCommandPath
$androidProjectDir = Join-Path $projectDir "android-driver"
if (-not (Test-Path $androidProjectDir)) {
    throw "android-driver folder not found: $androidProjectDir"
}

$toolsRoot = Join-Path $projectDir ".android-build-tools"
Ensure-Dir $toolsRoot

if (-not $JdkDir) {
    $JdkDir = Join-Path $toolsRoot "jdk17"
}
if (-not $AndroidSdkDir) {
    $AndroidSdkDir = Join-Path $toolsRoot "android-sdk"
}
if (-not $GradleDir) {
    $GradleDir = Join-Path $toolsRoot "gradle"
}
Ensure-Dir $JdkDir
Ensure-Dir $AndroidSdkDir
Ensure-Dir $GradleDir

Write-Host "=== Build Android APK without Android Studio ===" -ForegroundColor Cyan
Write-Host "Project : $androidProjectDir"
Write-Host "JDK     : $JdkDir"
Write-Host "SDK     : $AndroidSdkDir"
Write-Host "Gradle  : $GradleDir"
Write-Host ""

# ---- JDK 17 (portable) ----
$javaExe = Join-Path $JdkDir "bin\java.exe"
if (-not (Test-Path $javaExe)) {
    $jdkZip = Join-Path $toolsRoot "jdk17.zip"
    $jdkUrl = "https://github.com/adoptium/temurin17-binaries/releases/latest/download/OpenJDK17U-jdk_x64_windows_hotspot.zip"
    Download-File $jdkUrl $jdkZip
    Expand-Zip $jdkZip $JdkDir
}

$javaExe = Find-Bin $JdkDir "java.exe"
$env:JAVA_HOME = Split-Path -Parent (Split-Path -Parent $javaExe)
$env:Path = "$($env:JAVA_HOME)\bin;$env:Path"
Write-Host "Java ready: $javaExe" -ForegroundColor Green

# ---- Android cmdline-tools ----
$sdkManagerBat = Join-Path $AndroidSdkDir "cmdline-tools\latest\bin\sdkmanager.bat"
if (-not (Test-Path $sdkManagerBat)) {
    $sdkZip = Join-Path $toolsRoot "commandlinetools-win.zip"
    $sdkUrl = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"
    Download-File $sdkUrl $sdkZip

    $tmpSdkExtract = Join-Path $toolsRoot "_sdk_extract"
    if (Test-Path $tmpSdkExtract) { Remove-Item -Recurse -Force $tmpSdkExtract }
    New-Item -ItemType Directory -Path $tmpSdkExtract | Out-Null
    Expand-Zip $sdkZip $tmpSdkExtract

    $latestDir = Join-Path $AndroidSdkDir "cmdline-tools\latest"
    Ensure-Dir (Split-Path -Parent $latestDir)
    if (Test-Path $latestDir) { Remove-Item -Recurse -Force $latestDir }
    Move-Item -Path (Join-Path $tmpSdkExtract "cmdline-tools") -Destination $latestDir
    Remove-Item -Recurse -Force $tmpSdkExtract
}

$sdkManagerBat = Join-Path $AndroidSdkDir "cmdline-tools\latest\bin\sdkmanager.bat"
if (-not (Test-Path $sdkManagerBat)) {
    throw "sdkmanager.bat not found after extraction: $sdkManagerBat"
}

$env:ANDROID_SDK_ROOT = $AndroidSdkDir
$env:ANDROID_HOME = $AndroidSdkDir
$env:Path = "$AndroidSdkDir\platform-tools;$AndroidSdkDir\cmdline-tools\latest\bin;$env:Path"

Write-Host "Installing Android SDK packages..." -ForegroundColor Green
"y`n" * 200 | & $sdkManagerBat --licenses | Out-Null
& $sdkManagerBat "platform-tools" "platforms;android-34" "build-tools;34.0.0"

# ---- Gradle 8.7 ----
$gradleBat = Try-FindBin $GradleDir "gradle.bat"
if (-not $gradleBat) {
    $gradleZip = Join-Path $toolsRoot "gradle-8.7-bin.zip"
    $gradleUrl = "https://services.gradle.org/distributions/gradle-8.7-bin.zip"
    Download-File $gradleUrl $gradleZip
    Expand-Zip $gradleZip $GradleDir
    $gradleBat = Find-Bin $GradleDir "gradle.bat"
}

Write-Host "Gradle ready: $gradleBat" -ForegroundColor Green

# ---- local.properties ----
$localProps = Join-Path $androidProjectDir "local.properties"
$sdkEscaped = Normalize-WindowsPathForLocalProperties $AndroidSdkDir
Set-Content -Path $localProps -Value "sdk.dir=$sdkEscaped" -Encoding ASCII

Write-Host "Building APK..." -ForegroundColor Cyan
Push-Location $androidProjectDir
try {
    & $gradleBat --no-daemon clean assembleDebug
} finally {
    Pop-Location
}

$apk = Join-Path $androidProjectDir "app\build\outputs\apk\debug\app-debug.apk"
if (-not (Test-Path $apk)) {
    throw "APK not found after build: $apk"
}

Write-Host ""
Write-Host "SUCCESS: APK created" -ForegroundColor Green
Write-Host $apk -ForegroundColor Green
Write-Host ""
Write-Host "Install command (USB phone connected, debugging enabled):"
Write-Host "`"$AndroidSdkDir\platform-tools\adb.exe`" install -r `"$apk`""
