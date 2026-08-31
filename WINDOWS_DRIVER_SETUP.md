# Tronic Mini Pocket Printer - Windows 10/11 "normal printer" setup

This project now includes a practical Windows bridge so the mini printer appears as a standard Windows printer queue.

## Fastest install (one click)

Double-click:

```bat
install_one_click_driver.bat
```

What it automates:
- admin elevation
- Python venv + dependencies
- bridge autostart task at Windows logon
- local TCP print port (`127.0.0.1:9100`)
- Windows printer queue (`Tronic Mini Pocket Printer`)
- best available installed driver selection (`EPSON...` or fallback `Generic / Text Only`)
- custom paper form attempt (`Tronic_48x200mm`)

Optional command line examples:

```bat
install_one_click_driver.bat -ComPort COM5
install_one_click_driver.bat -Address 55:55:09:10:98:B6
```

`-Address` mode may work without pre-pairing on some adapters, but on Windows it is not guaranteed.

## What this solution does

- Runs a local raw TCP bridge (`windows_print_bridge.py`) on `127.0.0.1:9100`.
- Windows prints to that local TCP port as if it were a network printer.
- The bridge forwards the job to the real printer via Bluetooth COM port (recommended) or RFCOMM MAC.
- A2Y-specific start/stop commands are injected automatically.

This gives "normal printer" behavior in Windows apps without writing a kernel-mode driver.

## 1) Pair the printer in Windows

1. Turn on the printer.
2. Open Windows Bluetooth settings.
3. Pair with `Mini Pocket Printer`.
4. In Device Manager / Bluetooth settings, note the outgoing COM port (example: `COM5`).

If you use the one-click installer, it tries to detect COM automatically and opens Bluetooth settings if no COM is found yet.

## 2) Start the bridge

In this folder, run:

```bat
start_windows_printer_bridge.bat --port COM5 --verbose
```

Keep this terminal open while printing.

## 3) Install a Windows printer queue

You need an ESC/POS-compatible printer driver for graphics output.
Example driver name used by script: `EPSON TM-T20 Receipt`.

Run PowerShell as Administrator:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_windows_printer.ps1 -DriverName "EPSON TM-T20 Receipt"
```

Script behavior:
- creates/reuses TCP/IP printer port `IP_127.0.0.1_9100`
- creates/updates printer queue `Tronic Mini Pocket Printer`

## 4) Print from any app

- Select `Tronic Mini Pocket Printer` in Word, Browser, PDF reader, etc.
- Paper width should be configured near 48 mm in the driver preferences.

## Notes and limitations

- The bridge expects ESC/POS-like spool data.
- `GS v 0` raster blocks are auto-rescaled to 384 px width if needed.
- Cutter commands are ignored (device has no cutter).
- The bridge must stay running for the queue to work.
- Fully automatic first-time Bluetooth pairing cannot be guaranteed on Windows due OS security/pairing consent flow.
- Some Windows drivers ignore custom forms; in that case choose a 58 mm (or nearest narrow receipt) size manually in printer preferences.

## Troubleshooting

- **No output:** verify the bridge terminal shows incoming bytes and no errors.
- **Cannot connect to printer:** confirm the COM port and that no other app is using it.
- **Driver not found in script:** install an ESC/POS driver, then rerun.
- **Only text prints:** the selected driver is likely text-only; use an ESC/POS graphics-capable driver.
