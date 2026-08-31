param(
    [string]$PrinterName = "Tronic Mini Pocket Printer",
    [string]$DriverName = "",
    [string]$ComPort = "",
    [string]$Address = "",
    [switch]$NoAutoStart,
    [string]$LogPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-IsAdmin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Restart-Elevated {
    function Quote-Arg([string]$value) {
        return '"' + ($value -replace '"', '\"') + '"'
    }

    $argTokens = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", (Quote-Arg $PSCommandPath)
    )

    foreach ($kv in $script:PSBoundParameters.GetEnumerator()) {
        if ($kv.Value -is [System.Management.Automation.SwitchParameter]) {
            if ($kv.Value.IsPresent) {
                $argTokens += "-$($kv.Key)"
            }
            continue
        }
        if ($null -ne $kv.Value -and "$($kv.Value)".Length -gt 0) {
            $argTokens += "-$($kv.Key)"
            $argTokens += (Quote-Arg "$($kv.Value)")
        }
    }

    $argLine = ($argTokens -join " ")
    try {
        $proc = Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $argLine -PassThru -Wait
        return $proc.ExitCode
    } catch {
        Write-Error "Az UAC emeles megszakadt vagy sikertelen volt: $($_.Exception.Message)"
        return 1
    }
}

function Get-BluetoothComCandidates {
    $ports = Get-CimInstance Win32_SerialPort | ForEach-Object {
        if ($_.Name -match "(COM\d+)") {
            [PSCustomObject]@{
                Port        = $Matches[1]
                Name        = $_.Name
                IsBluetooth = ($_.Name -match "Bluetooth") -or ($_.Name -match "Standard Serial over Bluetooth")
            }
        }
    }
    $ports | Sort-Object -Property @{Expression = "IsBluetooth"; Descending = $true }, Port -Unique
}

function Wait-ForBluetoothPairingAndComPort {
    Write-Host "Nem talaltam Bluetooth COM portot." -ForegroundColor Yellow
    Write-Host "Megnyitom a Bluetooth beallitasokat, parositsd a 'Mini Pocket Printer'-t." -ForegroundColor Yellow
    Start-Process "ms-settings:bluetooth"

    for ($i = 0; $i -lt 36; $i++) {
        Start-Sleep -Seconds 5
        $candidates = Get-BluetoothComCandidates | Where-Object { $_.IsBluetooth }
        if ($candidates) {
            return $candidates
        }
    }
    return @()
}

function Resolve-DriverName {
    param([string]$RequestedName)
    $installed = @(Get-PrinterDriver | Select-Object -ExpandProperty Name)

    function Try-InstallDriver([string]$name) {
        try {
            Add-PrinterDriver -Name $name -ErrorAction Stop
            return $true
        } catch {
            return $false
        }
    }

    if ($RequestedName -and ($installed -contains $RequestedName)) {
        return $RequestedName
    }
    if ($RequestedName -and -not ($installed -contains $RequestedName)) {
        Write-Host "A kert driver nincs telepitve: $RequestedName" -ForegroundColor Yellow
        if (Try-InstallDriver $RequestedName) {
            return $RequestedName
        }
    }

    $preferred = @(
        "EPSON TM-T20 Receipt",
        "EPSON TM-T88V Receipt",
        "EPSON TM-T82 Receipt",
        "Generic / Text Only"
    )

    foreach ($candidate in $preferred) {
        if ($installed -contains $candidate) {
            return $candidate
        }
    }

    # Probalkozzunk a beepitett Generic driverrel.
    if (Try-InstallDriver "Generic / Text Only") {
        $installed = @(Get-PrinterDriver | Select-Object -ExpandProperty Name)
        if ($installed -contains "Generic / Text Only") {
            return "Generic / Text Only"
        }
    }

    # Utolso mentes: valasszunk egy installalt ESC/POS/POS/thermal-szeru drivert.
    $fallback = $installed | Where-Object {
        $_ -match "EPSON|ESC/?POS|Receipt|POS|Thermal|TM-T"
    } | Select-Object -First 1
    if ($fallback) {
        Write-Host "Figyelem: fallback driver hasznalata: $fallback" -ForegroundColor Yellow
        return $fallback
    }

    throw "Nincs talalhato kompatibilis driver. Telepits legalabb a 'Generic / Text Only' vagy egy Epson TM receipt drivert."
}

if (-not (Test-IsAdmin)) {
    Write-Host "UAC emeles szukseges a nyomtato queue es task letrehozashoz..." -ForegroundColor Cyan
    $exitCode = Restart-Elevated
    exit $exitCode
}

$scriptDir = Split-Path -Parent $PSCommandPath
$venvPython = Join-Path $scriptDir ".venv\Scripts\python.exe"
$bridgeScript = Join-Path $scriptDir "windows_print_bridge.py"
$installScript = Join-Path $scriptDir "install_windows_printer.ps1"
$paperScript = Join-Path $scriptDir "set_windows_paper_form.py"
$taskName = "TronicMiniPrinterBridge"
if (-not $LogPath) {
    $LogPath = Join-Path $env:TEMP "tronic_one_click_driver_install.log"
}

try { Start-Transcript -Path $LogPath -Force | Out-Null } catch {}

try {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw "Nincs telepitett Python a PATH-ban. Telepitsd a python.org-rol (Add python.exe to PATH)."
    }

    Write-Host "=== 1-kattintasos Tronic driver telepites ===" -ForegroundColor Cyan
    Write-Host "Log file: $LogPath"
    Write-Host ""

    if (-not (Test-Path $venvPython)) {
        Write-Host "Python virtualis kornyezet letrehozasa..." -ForegroundColor Green
        & python -m venv (Join-Path $scriptDir ".venv")
    }

    Write-Host "Fuggosegek telepitese/frissitese..." -ForegroundColor Green
    & $venvPython -m pip install --quiet --upgrade pip
    & $venvPython -m pip install --quiet pillow pyserial pywin32

    $selectedDriver = Resolve-DriverName -RequestedName $DriverName
    Write-Host "Hasznalt Windows driver: $selectedDriver" -ForegroundColor DarkGray

    $transportArgs = @()
    if ($ComPort) {
        $transportArgs = @("--port", $ComPort)
    } elseif ($Address) {
        Write-Host "RFCOMM MAC mod (parositas nelkul esetenkent megy, de nem garantalt)." -ForegroundColor Yellow
        $transportArgs = @("--address", $Address)
    } else {
        $candidates = Get-BluetoothComCandidates | Where-Object { $_.IsBluetooth }
        if (-not $candidates) {
            $candidates = Wait-ForBluetoothPairingAndComPort
        }
        if (-not $candidates) {
            throw "Nem talaltam Bluetooth COM portot. Add meg kezzel: -ComPort COM5 vagy -Address 55:55:..."
        }

    if ($candidates.Count -eq 1) {
            $ComPort = $candidates[0].Port
        } else {
        $ComPort = $candidates[0].Port
        Write-Host "Tobb Bluetooth COM portot talaltam, automatikus valasztas: $ComPort" -ForegroundColor Yellow
        }
        $transportArgs = @("--port", $ComPort)
    }

    Write-Host "Bridge kapcsolat: $($transportArgs -join ' ')" -ForegroundColor DarkGray

    # Scheduled Task letrehozasa/frissitese
    $bridgeArgs = @("`"$bridgeScript`"") + $transportArgs + @(
        "--listen-host", "127.0.0.1",
        "--listen-port", "9100"
    )
    $flatBridgeArgs = ($bridgeArgs -join " ")

    $action = New-ScheduledTaskAction -Execute $venvPython -Argument $flatBridgeArgs
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
        -Description "Bridge from Windows print queue to Tronic mini printer"

    if (-not $NoAutoStart) {
        Start-ScheduledTask -TaskName $taskName
    }

    # Printer queue letrehozasa/frissitese
    & $installScript -PrinterName $PrinterName -DriverName $selectedDriver -PortName "IP_127.0.0.1_9100" `
        -PrinterHost "127.0.0.1" -PrinterPort 9100

    # Probalkozzunk custom 48 mm papir-format beallitasaval.
    if (Test-Path $paperScript) {
        & $venvPython $paperScript --printer-name $PrinterName --form-name "Tronic_48x200mm" --width-mm 48 --length-mm 200
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Figyelem: a custom papirmeret automatikus beallitasa nem sikerult." -ForegroundColor Yellow
            Write-Host "A nyomtatas ettol meg mukodhet, de a driverben kezileg allits 48 mm-es format." -ForegroundColor Yellow
        }
    }

    Write-Host ""
    Write-Host "Kesz." -ForegroundColor Green
    Write-Host "Nyomtato queue: $PrinterName"
    Write-Host "Autostart task : $taskName"
    if ($ComPort) {
        Write-Host "Kapcsolat       : COM ($ComPort)"
    } elseif ($Address) {
        Write-Host "Kapcsolat       : RFCOMM MAC ($Address)"
    }
    Write-Host ""
    Write-Host "Megjegyzes: teljesen automatikus, elso parositas nelkuli COM-uzem nem mindig lehetseges Windows alatt." -ForegroundColor Yellow
    exit 0
} catch {
    Write-Host ""
    Write-Host "HIBA: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Nezd meg a logot: $LogPath" -ForegroundColor Yellow
    exit 1
} finally {
    try { Stop-Transcript | Out-Null } catch {}
}
