param(
    [string]$PrinterName = "Tronic Mini Pocket Printer",
    [string]$DriverName = "EPSON TM-T20 Receipt",
    [string]$PortName = "IP_127.0.0.1_9100",
    [string]$PrinterHost = "127.0.0.1",
    [uint32]$PrinterPort = 9100
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "=== Tronic mini printer queue setup ===" -ForegroundColor Cyan
Write-Host "Printer name : $PrinterName"
Write-Host "Driver name  : $DriverName"
Write-Host ("Port         : {0} ({1}:{2})" -f $PortName, $PrinterHost, $PrinterPort)
Write-Host ""

try {
    $null = Get-Printer -ErrorAction Stop
} catch {
    Write-Error "PrintManagement cmdlets are unavailable. Run this on Windows 10/11 with admin rights."
    exit 1
}

$driver = Get-PrinterDriver -Name $DriverName -ErrorAction SilentlyContinue
if (-not $driver) {
    Write-Host "Installed drivers:" -ForegroundColor Yellow
    Get-PrinterDriver | Select-Object -ExpandProperty Name
    Write-Error "Driver '$DriverName' is not installed. Install an ESC/POS-compatible driver first, then rerun."
    exit 1
}

$port = Get-PrinterPort -Name $PortName -ErrorAction SilentlyContinue
if (-not $port) {
    Write-Host "Creating TCP/IP printer port..." -ForegroundColor Green
    Add-PrinterPort -Name $PortName -PrinterHostAddress $PrinterHost -PortNumber $PrinterPort
} else {
    Write-Host "Port already exists, reusing it." -ForegroundColor DarkGray
}

$printer = Get-Printer -Name $PrinterName -ErrorAction SilentlyContinue
if (-not $printer) {
    Write-Host "Creating printer queue..." -ForegroundColor Green
    Add-Printer -Name $PrinterName -DriverName $DriverName -PortName $PortName
} else {
    Write-Host "Printer already exists, updating settings..." -ForegroundColor DarkGray
    Set-Printer -Name $PrinterName -DriverName $DriverName -PortName $PortName
}

Write-Host ""
Write-Host "Done. Windows printer queue is ready: '$PrinterName'." -ForegroundColor Cyan
Write-Host "Important: keep windows_print_bridge.py running while printing."
