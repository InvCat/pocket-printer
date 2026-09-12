# Pocket Printer

**An open toolkit for the Tronic Mini Pocket Printer — the little thermal printer from Lidl.**

---

I bought this printer at Lidl. Compact, cheap, Bluetooth, thermal paper — the kind of gadget that looks perfect for notes, labels, receipts, and quick sketches on the go.

Then I opened the official Android app.

It worked… inside its own little world. Closed, limited, and tightly tied to one vendor UI. No proper system print support. No clean way to send a page from another app. No path to use the same printer from a desktop without jumping through hoops. The hardware was capable. The software around it was not.

So I reverse-engineered the factory APK (`com.printer.lidloffice`), verified every command against a real device, and started building what I actually wanted: **a universal driver for a locked-down pocket printer.**

This repository is that work — protocol notes, a Python client, a desktop GUI, an Android Print Service, and a Raspberry Pi CUPS/IPP gateway so the printer can show up as a normal network printer on Windows.

---

## The device

| | |
|---|---|
| Brand / model | Tronic Mini Pocket Printer (Model 2890) |
| Lidl article | IAN `508705_2507` |
| Bluetooth name | `Mini Pocket Printer` |
| Internal model | **A2Y** |
| Print width | **384 px** (48 mm @ 203 dpi) |
| Link | Classic Bluetooth SPP (also USB-C serial) |
| OEM / SDK | Xiamen Print Future / LuckPrinter |

The stock app talks a proprietary wrapper around ESC/POS-style raster printing. Once that sequence is known, the printer is just another thermal engine — and it can belong to *you*, not only to one closed APK.

Full command reference, status bits, and the verified print sequence live in [`PROTOCOL.md`](PROTOCOL.md).

---

## What’s in this repo

### 1. Protocol documentation
[`PROTOCOL.md`](PROTOCOL.md) — reverse-engineered from the factory APK and **checked on real hardware** (firmware `V1.06LY`, bootloader `V3.02`). Model queries, battery, paper/cover status, density, and the working print job order.

### 2. Python library + CLI
[`tronic_printer.py`](tronic_printer.py) — scan, query info, print text or images over Bluetooth RFCOMM or a serial/USB port.

```bash
pip install pillow          # plus pyserial for COM / USB-C
python tronic_printer.py scan
python tronic_printer.py info  --address 55:55:xx:xx:xx:xx
python tronic_printer.py text  "Hello, pocket world!" --address 55:55:xx:xx:xx:xx
python tronic_printer.py image note.png --address 55:55:xx:xx:xx:xx
```

### 3. Desktop GUI
[`tronic_gui.py`](tronic_gui.py) / [`start_gui.bat`](start_gui.bat) — a simple Tk interface for connection, preview, and printing without living in the terminal.

### 4. Android Print Service + Share target
[`android-driver/`](android-driver/) — **Tronic Pocket Print Service** for Android:

- System **Print** dialog target (`Tronic Mini Pocket Printer`)
- System **Share / Send** target with **print preview** (images, PDF, text)

Build locally (Android Studio or the no-Studio scripts), or grab the APK from GitHub Actions / Releases. From **0.1.6** the APK uses a stable project signing key (one-time uninstall of older builds if Android reports a signature conflict). Setup notes: [`android-driver/README.md`](android-driver/README.md).

### 5. Raspberry Pi network gateway (CUPS / IPP)
[`rpi-gateway/`](rpi-gateway/) — CUPS/IPP (+ optional TCP `:9100`) so the Tronic shows up as a normal LAN printer. The Pi holds Bluetooth (or USB-C) to the device; clients only talk to the Pi. Full reference: [`rpi-gateway/README.md`](rpi-gateway/README.md).

---

## Print from Windows (Raspberry Pi gateway)

No custom Windows `.inf` / WDK driver. The **real A2Y driver runs on the Pi**; Windows only composes the page and sends it over the network.

```text
Windows app (Notepad, Word, Photos…)
        │  MS Publisher Imagesetter  →  48×80 mm page
        │  IPP  http://<pi-ip>:631/printers/TronicPocket
        ▼
Raspberry Pi  (CUPS queue TronicPocket + rpi-gateway backend)
        │  384 px @ 203 dpi, dither, tear-off feed, SPP pacing
        │  Classic Bluetooth (or USB-C)
        ▼
Tronic Mini Pocket Printer (A2Y)
```

### 1) Install on the Pi

```bash
cd rpi-gateway
sudo ./install.sh --address 55:55:xx:xx:xx:xx
```

Pair the printer once with `bluetoothctl` (`pair` / `trust`). Config: `/etc/tronic-pocket-printer.conf`.

### 2) Add the printer on Windows

Admin PowerShell, from a checkout of `rpi-gateway/` (replace with your Pi’s LAN IP):

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\add-printer-windows.ps1 -PrinterHost 192.168.xxx.xxx
```

Or already added with the wrong driver:

```powershell
.\fix-windows-paper.ps1
```

### 3) Windows settings that matter

| Setting | Use this | Avoid |
|---|---|---|
| Driver | **MS Publisher Imagesetter** | Microsoft IPP Class Driver (no custom 48 mm) |
| Paper | **Tronic 48×80 mm** | A4 / Letter |
| Color | **Off** (greyscale / B&W) | Colour (hardware is mono) |
| Bluetooth to the Tronic | **Disconnected / disabled** on Windows | Keeping Windows paired on SPP — blocks the Pi |

Close Notepad/Word after changing paper defaults, then reopen.

### Why Imagesetter (not IPP Class Driver)?

- **IPP** = the network pipe to the Pi.
- **IPP Class Driver** = Microsoft’s generic IPP client; paper list is basically A4/Letter only → tiny text / long blank rolls.
- **MS Publisher Imagesetter** = inbox composer that honors a Windows **48 mm** form; the Pi then prints WYSIWYG at true size.

More detail (tear-off margin, photo banding / raster buffer, troubleshooting): [`rpi-gateway/README.md`](rpi-gateway/README.md).

---

## Why this exists

Closed companion apps turn useful hardware into disposable toys.  
This project is the opposite bet:

- **Own the wire protocol** — document it so it can’t disappear with the next store app update.
- **Print from anywhere** — system print on Android, CLI/GUI on the desktop, IPP network printer via a Raspberry Pi gateway.
- **Stay honest to the hardware** — 384 px, 203 dpi, SPP-first, measured and re-checked on a real unit.

If you also grabbed one of these from Lidl (or a rebranded twin with the same LuckPrinter guts), you’re welcome here.

---

## Status

Working MVP. Image printing uses Floyd–Steinberg dithering on both the Python / Pi gateway path and the Android app (share preview is 1-bit WYSIWYG). Android supports both the system Print dialog and a Share-sheet target with preview. Pairing-free Bluetooth is experimental and depends on the phone’s stack. BLE GATT is advertised by the device but Classic SPP is the reliable path.

---

## License / disclaimer

This is an unofficial, independent project. Tronic, Lidl, and related names belong to their owners. Reverse engineering was done for interoperability with hardware you already own. Use at your own risk; thermal printers and paper have limits — don’t expect laser-printer miracles from a pocket brick bought next to the seasonal aisle.

---

*Bought at Lidl. Freed with open tools.*
