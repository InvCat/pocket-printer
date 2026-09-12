# Raspberry Pi network gateway (CUPS / IPP)

Turn a Raspberry Pi into a **network printer** for the Tronic Mini Pocket Printer
so Windows (and other LAN clients) can print without a CLI gateway on the PC.

```text
Windows / macOS / Linux app
        │
        │  1) Windows “driver” draws the page (48×80 mm, greyscale…)
        │  2) Job travels over the LAN (IPP :631 or raw TCP :9100)
        ▼
Raspberry Pi  — CUPS queue TronicPocket + this backend
        │  rasterize @ 203 dpi → 384 px wide
        │  Bluetooth SPP (or USB-C serial)
        ▼
Tronic Mini Pocket Printer (A2Y)  — mono thermal, 48 mm roll
```

The **real printer driver lives on the Pi** (`tronic_printer.py` + CUPS backend).
Windows only needs a page composer + a network port.

## What you get

| Endpoint | Use |
|---|---|
| `http://<pi-ip>:631/printers/TronicPocket` | Windows / macOS / Linux IPP shared printer |
| `<pi-ip>:9100` | JetDirect-style raw TCP (PDF / PNG / JPEG / PS) |
| CUPS queue `TronicPocket` | Local `lp` / `lpr` on the Pi |

Jobs are rendered to **384 px @ 203 dpi**, streamed with pacing tuned to avoid
Bluetooth underrun banding, then finished with a **~10 mm tear-off feed**.

## Requirements

- Raspberry Pi OS with Bluetooth (or USB-C cable to the printer)
- Printer paired once via `bluetoothctl` (classic name: `Mini Pocket Printer`)
- **Only the Pi may hold the classic Bluetooth SPP link** while printing  
  (Windows must not stay connected to the same printer over BT — see below)

## Install

On the Pi, from this repo checkout:

```bash
cd "/path/to/Pocket printer/rpi-gateway"
sudo ./install.sh --address 55:55:XX:XX:XX:XX
```

Or edit the MAC later:

```bash
sudo nano /etc/tronic-pocket-printer.conf
sudo systemctl restart cups tronic-gateway
```

### Pair the printer (once)

```text
bluetoothctl
power on
scan on
pair 55:55:XX:XX:XX:XX
trust 55:55:XX:XX:XX:XX
connect 55:55:XX:XX:XX:XX
```

USB-C instead of Bluetooth:

```bash
DEVICE_URI=tronic:/dev/ttyACM0
```

then recreate the queue (or re-run `install.sh`).

---

## Architecture: what is the “driver”?

| Layer | What it is | Role |
|---|---|---|
| **Port** | IPP URL or TCP `:9100` | Transport Windows → Pi |
| **Windows driver** | **MS Publisher Imagesetter** (recommended) | Composes the page (paper size, fonts, images) into PostScript/PDF |
| **Real device driver** | Pi CUPS backend `tronic` + [`tronic_printer.py`](../tronic_printer.py) | Page → 1-bit 384 px raster → A2Y Bluetooth/USB commands |

### IPP Class Driver vs MS Publisher Imagesetter

| | Microsoft IPP Class Driver | MS Publisher Imagesetter |
|---|---|---|
| Role | Generic IPP client | Inbox page composer (PostScript) |
| Custom **48 mm** paper | **No** — only A4 / Letter / … | **Yes** — honors Windows forms |
| Typical symptom if wrong | Tiny text, huge empty roll, A4 default | Correct 48 mm layout |
| Use with this gateway? | Avoid | **Yes — this is the intended Windows driver** |

So: **IPP is the pipe**, **Imagesetter is the Windows-side formatter**, **the Pi is the Tronic driver**.

---

## Windows setup

### One-click (Administrator PowerShell)

From this folder on the Windows PC (adjust IP):

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\add-printer-windows.ps1 -PrinterHost 192.168.145.211
```

That script:

1. Adds `http://192.168.145.211:631/printers/TronicPocket`
2. Sets the Windows driver to **MS Publisher Imagesetter**
3. Registers form **Tronic 48x80 mm** and aims to set it as default

Already installed with IPP Class Driver? Fix in place:

```powershell
.\fix-windows-paper.ps1
# or:
.\fix-windows-paper.ps1 -PrinterName "exact name from Get-Printer"
```

Then **close Notepad/Word completely and reopen**. Print dialog paper size should
be **Tronic 48x80 mm**, not A4.

### Recommended Windows defaults

| Setting | Value | Why |
|---|---|---|
| Driver | MS Publisher Imagesetter | 48 mm form support |
| Paper | **Tronic 48x80 mm** (or other 48×N form) | Native roll width → WYSIWYG size |
| Color | **Black and white / greyscale** (`Color = False`) | Hardware is mono; colour jobs are larger and unused |
| Port | `http://<pi>:631/printers/TronicPocket` | Do not print via Windows↔printer Bluetooth |

Set greyscale (Admin PowerShell):

```powershell
Set-PrintConfiguration -PrinterName '\\http://192.168.145.211:631\TronicPocket' -Color $false
```

### Critical: do not let Windows keep Bluetooth to the printer

Classic SPP is effectively **single-client**. If Windows is paired/connected to
`Mini Pocket Printer`, the Pi often gets `Host is down` / `Device or resource busy`
and CUPS jobs fail with no paper out.

- Prefer: leave the printer’s Windows Bluetooth device **disabled**, and print
  only through the IPP queue.
- Or: disconnect BT on Windows whenever the Pi gateway should print.

### Manual add

1. **Settings → Printers → Add device → printer isn’t listed**
2. Shared printer by name:

```text
http://192.168.145.211:631/printers/TronicPocket
```

3. **Printer properties → Advanced → New Driver → Microsoft → MS Publisher Imagesetter**
4. Run `fix-windows-paper.ps1`, or set Printing Defaults → Paper = **Tronic 48x80 mm**, Color = off

### Alternative: raw TCP :9100

Standard TCP/IP Port → host = Pi IP, Raw, port **9100**, driver **Imagesetter**,
same 48 mm form. Prefer IPP for normal desktop apps.

---

## How the Pi processes jobs (behaviour)

### 48 mm WYSIWYG (default)

When Windows sends a roll-sized page (≲ ~60 mm wide at `TRONIC_DPI`):

- Rasterize **1:1** at 203 dpi (no text reflow)
- Trim **top/bottom only** (never crop-then-upscale — that magnified Notepad text)
- Landscape 48×80 forms (~80×48 mm raster) are **rotated** so 48 mm matches the head
- Pad/scale-down to 384 px without enlarging glyphs

`TRONIC_REFLOW=0` (default). Only turn reflow on if you must support the IPP Class
Driver still composing onto A4:

```bash
# /etc/tronic-pocket-printer.conf
TRONIC_REFLOW=1
```

### Tear-off margin

After the last ink, the backend advances paper by `TRONIC_TEAR_MM` (default **10**)
via ESC/J feed so the strip clears the serrated tear bar.

### Photo banding / stutter fix

Horizontal stripes usually mean the Bluetooth link **underran** (head paused
mid-page). Raster is streamed in **large row-aligned chunks** with a small gap:

| Variable | Default | Meaning |
|---|---|---|
| `TRONIC_RASTER_CHUNK` | `6144` (~128 rows) | Bytes per SPP write |
| `TRONIC_RASTER_GAP_MS` | `2` | Pause between chunks (was 20 ms @ 1 KiB) |

RFCOMM also raises `SO_SNDBUF`. If banding returns on a busy radio environment,
try `TRONIC_RASTER_GAP_MS=0`.

### Config reference (`/etc/tronic-pocket-printer.conf`)

See [`tronic-pocket.conf.example`](tronic-pocket.conf.example). Important keys:

| Key | Default | Purpose |
|---|---|---|
| `TRONIC_ADDR` | (MAC) | Classic Bluetooth address |
| `TRONIC_DENSITY` | `1` | 0 light / 1 medium / 2 dark |
| `TRONIC_DPI` | `203` | PDF/PS rasterize DPI |
| `TRONIC_TRIM` | `1` | Crop blank margins (vertical-only on roll pages) |
| `TRONIC_TEAR_MM` | `10` | Post-job paper advance for tear-off |
| `TRONIC_REFLOW` | `0` | A4 text reflow (legacy); keep off with Imagesetter+48 mm |
| `TRONIC_RASTER_CHUNK` | `6144` | SPP write size |
| `TRONIC_RASTER_GAP_MS` | `2` | SPP pacing |
| `TCP_PORT` / `TCP_HOST` | `9100` / `0.0.0.0` | Optional raw listener |

After edits:

```bash
sudo systemctl restart cups tronic-gateway
```

---

## Print from the Pi itself

```bash
echo "Hello from CUPS" | lp -d TronicPocket
lp -d TronicPocket ~/Documents/note.pdf
lpstat -p TronicPocket -l
```

Smoke-test Bluetooth without CUPS:

```bash
cd /usr/local/lib/tronic-pocket-printer
python3 - <<'PY'
from tronic_printer import Printer, RFCOMMTransport, render_text
with Printer(RFCOMMTransport("55:55:XX:XX:XX:XX")) as pr:
    print(pr.status())
    pr.print_text("BT OK")
PY
```

---

## Files installed

| Path | Role |
|---|---|
| `/usr/lib/cups/backend/tronic` | CUPS backend |
| `/usr/share/cups/model/tronic-pocket.ppd` | 48 mm roll page sizes |
| `/usr/local/lib/tronic-pocket-printer/` | `tronic_printer.py` + backend |
| `/etc/tronic-pocket-printer.conf` | MAC / density / trim / tear / raster pacing |
| `tronic-gateway.service` | optional always-on `:9100` listener |
| `add-printer-windows.ps1` / `fix-windows-paper.ps1` | Windows Imagesetter + 48 mm form |

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Nothing comes out; CUPS `Backend returned status 1` | Pi cannot open SPP — printer asleep, out of range, or **Windows still on Bluetooth**. Wake printer; disable Windows BT device; `python3` status smoke test above |
| Jobs stuck / paused on Windows | Cancel on Pi (`cancel -a TronicPocket`) and in Windows print queue; fix BT first |
| Tiny text / long blank strip | Still on **IPP Class Driver** or **A4** paper — switch to Imagesetter + **Tronic 48x80 mm** |
| Text hugely magnified | Old “trim then upscale” path — update backend; roll pages must stay WYSIWYG |
| Horizontal bands / stutter on photos | Update `tronic_printer.py` raster pacing; lower `TRONIC_RASTER_GAP_MS` |
| Tear cuts through last line | Raise `TRONIC_TEAR_MM` (10 mm is the usual sweet spot) |
| Too much blank after last line | Lower `TRONIC_TEAR_MM` |
| Windows only offers A4 | Imagesetter + `fix-windows-paper.ps1`; never rely on IPP Class Driver for 48 mm |
| Colour default in Windows | Set **Color = False** — mono hardware |
| TCP :9100 refused | `systemctl status tronic-gateway`; valid `TRONIC_ADDR` |
| CUPS missing `pstopdf` | Symlink to `gstopdf`: `ln -sf gstopdf /usr/lib/cups/filter/pstopdf` |

Logs:

```bash
sudo journalctl -u cups -u tronic-gateway -f
sudo tail -f /var/log/cups/error_log
bluetoothctl info 55:55:XX:XX:XX:XX
```

---

## Uninstall (optional)

```bash
sudo lpadmin -x TronicPocket
sudo systemctl disable --now tronic-gateway
sudo rm -f /usr/lib/cups/backend/tronic \
           /usr/share/cups/model/tronic-pocket.ppd \
           /etc/systemd/system/tronic-gateway.service
sudo rm -rf /usr/local/lib/tronic-pocket-printer
sudo systemctl daemon-reload
```
