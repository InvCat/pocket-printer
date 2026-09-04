# Pocket Printer

**An open toolkit for the Tronic Mini Pocket Printer — the little thermal printer from Lidl.**

---

I bought this printer at Lidl. Compact, cheap, Bluetooth, thermal paper — the kind of gadget that looks perfect for notes, labels, receipts, and quick sketches on the go.

Then I opened the official Android app.

It worked… inside its own little world. Closed, limited, and tightly tied to one vendor UI. No proper system print support. No clean way to send a page from another app. No path to use the same printer from a desktop without jumping through hoops. The hardware was capable. The software around it was not.

So I reverse-engineered the factory APK (`com.printer.lidloffice`), verified every command against a real device, and started building what I actually wanted: **a universal driver for a locked-down pocket printer.**

This repository is that work — protocol notes, a Python client, a desktop GUI, and an Android Print Service that plugs into the native print menu.

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

### 4. Android Print Service
[`android-driver/`](android-driver/) — a minimal system print target named **Tronic Mini Pocket Printer**. Print from any Android app that supports the system Print sheet; pages are rendered to 384 px and sent with the verified A2Y sequence.

Build locally (Android Studio or the no-Studio scripts), or grab the APK from GitHub Actions (`Build Android APK` → artifact). Setup notes are in [`android-driver/README.md`](android-driver/README.md).

---

## Why this exists

Closed companion apps turn useful hardware into disposable toys.  
This project is the opposite bet:

- **Own the wire protocol** — document it so it can’t disappear with the next store app update.
- **Print from anywhere** — system print on Android, CLI/GUI on the desktop.
- **Stay honest to the hardware** — 384 px, 203 dpi, SPP-first, measured and re-checked on a real unit.

If you also grabbed one of these from Lidl (or a rebranded twin with the same LuckPrinter guts), you’re welcome here.

---

## Status

Working MVP. Monochrome thresholding for images (dithering still to come). Pairing-free Bluetooth is experimental and depends on the phone’s stack. BLE GATT is advertised by the device but intentionally unused — Classic SPP is the reliable path.

---

## License / disclaimer

This is an unofficial, independent project. Tronic, Lidl, and related names belong to their owners. Reverse engineering was done for interoperability with hardware you already own. Use at your own risk; thermal printers and paper have limits — don’t expect laser-printer miracles from a pocket brick bought next to the seasonal aisle.

---

*Bought at Lidl. Freed with open tools.*
