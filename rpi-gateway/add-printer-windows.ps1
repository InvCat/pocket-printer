# Add Tronic Mini Pocket Printer on Windows via the Raspberry Pi IPP gateway.
#
# Microsoft IPP Class Driver CANNOT offer custom 48 mm paper (only A4/Letter…).
# This script adds the IPP printer, switches it to "MS Publisher Imagesetter"
# (inbox driver that honors Windows forms), registers 48×80 mm, and sets default.
#
# Usage (Administrator PowerShell):
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\add-printer-windows.ps1
#   .\add-printer-windows.ps1 -PrinterHost 192.168.xxx.xxx

param(
    [string]$PrinterHost = "192.168.xxx.xxx",
    [string]$QueueName = "TronicPocket",
    [string]$PrinterName = "Tronic Mini Pocket Printer",
    [string]$FormName = "Tronic 48x80 mm",
    [double]$WidthMm = 48.0,
    [double]$HeightMm = 80.0,
    [string]$DriverName = "MS Publisher Imagesetter"
)

$ErrorActionPreference = "Stop"
$ippUrl = "http://${PrinterHost}:631/printers/${QueueName}"

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]$id
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Run as Administrator (right-click PowerShell → Run as administrator)."
}

$fixScript = Join-Path $PSScriptRoot "fix-windows-paper.ps1"
if (-not (Test-Path $fixScript)) {
    Write-Error "Missing $fixScript (needed to register the 48 mm form)."
}

Write-Host "Checking IPP endpoint: $ippUrl"
try {
    $resp = Invoke-WebRequest -Uri $ippUrl -UseBasicParsing -TimeoutSec 8
    if ($resp.StatusCode -ge 400) { throw "HTTP $($resp.StatusCode)" }
    Write-Host "IPP endpoint OK."
} catch {
    Write-Error "Cannot reach $ippUrl`n$_"
}

Write-Host "Ensuring driver '$DriverName' is installed..."
if (-not (Get-PrinterDriver -Name $DriverName -ErrorAction SilentlyContinue)) {
    try {
        Add-PrinterDriver -Name $DriverName
    } catch {
        throw @"
Could not install '$DriverName': $_

Install manually:
  Print Management → Drivers → Add → Manufacturer Microsoft → $DriverName
Then re-run this script.
"@
    }
}

$existing = Get-Printer -Name $PrinterName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing printer '$PrinterName'..."
    Remove-Printer -Name $PrinterName
}

Write-Host "Adding IPP printer '$PrinterName'..."
try {
    Add-Printer -Name $PrinterName -IppUrl $ippUrl
} catch {
    throw @"
Automatic add failed: $_

Manual steps:
1. Settings → Printers → Add device → printer isn't listed
2. Shared printer by name → $ippUrl
3. After it appears: Printer properties → Advanced → New Driver
   → Microsoft → $DriverName  (NOT IPP Class Driver)
4. Run: .\fix-windows-paper.ps1 -PrinterName '$PrinterName'
"@
}

Write-Host "Switching driver to '$DriverName' (required for 48 mm paper UI)..."
Set-Printer -Name $PrinterName -DriverName $DriverName

Write-Host "Registering 48 mm form and setting it as default..."
& $fixScript -PrinterName $PrinterName -FormName $FormName -WidthMm $WidthMm -HeightMm $HeightMm

Write-Host ""
Write-Host "Done. Default paper: '$FormName' — not A4."
Write-Host "Close apps and reopen before printing. IPP: $ippUrl"
