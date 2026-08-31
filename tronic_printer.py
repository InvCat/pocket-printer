#!/usr/bin/env python3
"""
Tronic "Mini Pocket Printer" (Lidl, IAN 508705_2507) vezerlo konyvtar + CLI.

Belso modell: A2Y  -  Xiamen Print Future Technology / LuckPrinter SDK.
A protokoll a gyari APK-bol (com.printer.lidloffice, versionCode 177)
lett visszafejtve, es EGY VALODI KESZULEKEN LE VAN TESZTELVE
(fw V1.06LY, bootloader V3.02).

Ellenorzott hardver-parameterek:
    nyomtatasi szelesseg  384 pixel  (48 mm, 48 bajt/sor)
    felbontas             203 dpi    (vonalzoval hitelesitve)
    kapcsolat             klasszikus Bluetooth SPP (RFCOMM 1. csatorna)

Kapcsolodasi modok:
  * RFCOMM  - Linux es Windows (Python 3.9+), parositas utan kozvetlenul
              a MAC cimre.  Ez a legmegbizhatobb ut.
  * Soros   - Windows-on a parositott SPP eszkozhoz rendelt COM port
              (pyserial).  Akkor hasznald, ha az RFCOMM socket nem megy.
  * USB-C   - a nyomtato USB-rol is fogadja ugyanezeket a parancsokat,
              szinten soros portkent (/dev/ttyACM0, COMx).

A BLE (GATT) utat szandekosan nem hasznaljuk: a keszulek hirdeti ugyan az
e7810a71-73ae-499d-8c15-faa9aef0c3f2 szolgaltatast, de BlueZ 5.55 alatt a
kapcsolodas org.bluez.Error.NotAvailable hibaval elszall, Windows-on pedig
a WinRT reteg gyakran "class not registered" hibat ad.  Az SPP mindket
platformon problemamentes.

Hasznalat:
    pip install pillow            # a soros modhoz meg: pip install pyserial
    python tronic_printer.py scan
    python tronic_printer.py info    --address 55:55:09:10:98:B6
    python tronic_printer.py text    "Hello vilag!" --address 55:55:09:10:98:B6
    python tronic_printer.py image   kep.png --address 55:55:09:10:98:B6
    python tronic_printer.py feed    --address 55:55:09:10:98:B6

A --address helyett a TRONIC_ADDR kornyezeti valtozo is hasznalhato.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time

from PIL import Image, ImageDraw, ImageFont

# --- hardver ---

PRINT_WIDTH = 384                      # BaseDevice.getPrintWidth()
BYTES_PER_ROW = PRINT_WIDTH // 8       # 48
DPI = 203
END_LINE_DOT = 0x50                    # BaseNormalDevice.<init>, 384 px eseten 80

RFCOMM_CHANNEL = 1

# --- parancsok (a visszafejtett SDK metodusnevekkel) ---

CMD_ENABLE = bytes([0x10, 0xFF, 0xF1, 0x03])    # enablePrinterLuck(), mode=3
CMD_WAKEUP = bytes(12)                          # printerWakeupLuck()
CMD_STOP = bytes([0x10, 0xFF, 0xF1, 0x45])      # stopPrintJobLuck() -> 0xAA
CMD_POSITION = bytes([0x1D, 0x0C])              # printerPositionLuck()
CMD_MODEL = bytes([0x10, 0xFF, 0x20, 0xF0])
CMD_FIRMWARE = bytes([0x10, 0xFF, 0x20, 0xF1])
CMD_SERIAL = bytes([0x10, 0xFF, 0x20, 0xF2])
CMD_BOOT = bytes([0x10, 0xFF, 0x20, 0xEF])
CMD_BATTERY = bytes([0x10, 0xFF, 0x50, 0xF1])
CMD_STATUS = bytes([0x10, 0xFF, 0x40])
CMD_SHUTDOWN_GET = bytes([0x10, 0xFF, 0x13])
CMD_ALL_INFO = bytes([0x10, 0xFF, 0x70])


class PrinterError(Exception):
    pass


class Status:
    """A 10 FF 40 valaszaban kapott allapot-bitmaszk."""

    def __init__(self, byte: int):
        self.raw = byte
        self.printing = bool(byte & 0x01)
        self.cover_open = bool(byte & 0x02)
        self.no_paper = bool(byte & 0x04)
        self.low_battery = bool(byte & 0x08)
        self.overheated = bool(byte & 0x10 or byte & 0x40)
        self.charging = bool(byte & 0x20)

    @property
    def ok(self) -> bool:
        return not (self.cover_open or self.no_paper or self.overheated)

    def __str__(self) -> str:
        flags = []
        if self.printing:
            flags.append("nyomtat")
        if self.cover_open:
            flags.append("fedel nyitva")
        if self.no_paper:
            flags.append("NINCS PAPIR")
        if self.low_battery:
            flags.append("gyenge akku")
        if self.overheated:
            flags.append("tulmelegedett")
        if self.charging:
            flags.append("tolt")
        return ", ".join(flags) if flags else "kesz"


# --- atvitel ---


class RFCOMMTransport:
    """Klasszikus Bluetooth SPP socket. Linux + Windows (Python 3.9+)."""

    def __init__(self, address: str, channel: int = RFCOMM_CHANNEL):
        self.address = address
        self.channel = channel
        self.sock: socket.socket | None = None

    def open(self, attempts: int = 6, verbose: bool = True) -> None:
        if not hasattr(socket, "AF_BLUETOOTH"):
            raise PrinterError(
                "Ez a Python nem tamogatja az AF_BLUETOOTH socketet. "
                "Hasznald a soros modot: --port COM5"
            )
        last = None
        for i in range(1, attempts + 1):
            s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                              socket.BTPROTO_RFCOMM)
            s.settimeout(15)
            try:
                s.connect((self.address, self.channel))
                self.sock = s
                return
            except OSError as e:
                # A nyomtato eppen alszik vagy meg tartja az elozo kapcsolatot;
                # par masodperc mulva altalaban sikerul.
                last = e
                s.close()
                if verbose:
                    print(f"  kapcsolodas {i}/{attempts}: {e}", file=sys.stderr)
                time.sleep(3)
        raise PrinterError(f"Nem sikerult csatlakozni ({self.address}): {last}")

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def write(self, data: bytes) -> None:
        self.sock.sendall(data)

    def read(self, timeout: float) -> bytes:
        self.sock.settimeout(timeout)
        try:
            return self.sock.recv(1024)
        except socket.timeout:
            return b""

    def drain(self) -> None:
        """Bentragadt valasz-toredekek eldobasa a kovetkezo kerdes elott."""
        self.sock.setblocking(False)
        try:
            while True:
                if not self.sock.recv(1024):
                    break
        except (BlockingIOError, socket.timeout, OSError):
            pass
        finally:
            self.sock.setblocking(True)


class SerialTransport:
    """Soros port: Windows SPP COM port, vagy USB-C kabel (/dev/ttyACM0, COMx)."""

    def __init__(self, port: str, baud: int = 115200):
        self.port = port
        self.baud = baud
        self.ser = None

    def open(self, attempts: int = 3, verbose: bool = True) -> None:
        try:
            import serial
        except ImportError:
            raise PrinterError("A soros modhoz kell a pyserial: pip install pyserial")
        last = None
        for i in range(1, attempts + 1):
            try:
                self.ser = serial.Serial(self.port, self.baud, timeout=2)
                return
            except Exception as e:
                last = e
                if verbose:
                    print(f"  port megnyitas {i}/{attempts}: {e}", file=sys.stderr)
                time.sleep(2)
        raise PrinterError(f"Nem sikerult megnyitni a portot ({self.port}): {last}")

    def close(self) -> None:
        if self.ser is not None:
            self.ser.close()
            self.ser = None

    def write(self, data: bytes) -> None:
        self.ser.write(data)
        self.ser.flush()

    def read(self, timeout: float) -> bytes:
        self.ser.timeout = timeout
        data = self.ser.read(1)
        if not data:
            return b""
        time.sleep(0.15)
        return data + self.ser.read(self.ser.in_waiting or 0)

    def drain(self) -> None:
        """Bentragadt valasz-toredekek eldobasa a kovetkezo kerdes elott."""
        self.ser.reset_input_buffer()


# --- kliens ---


class Printer:
    def __init__(self, transport):
        self.t = transport

    def __enter__(self) -> "Printer":
        self.t.open()
        return self

    def __exit__(self, *exc) -> None:
        self.t.close()

    # -- alap I/O --

    def send(self, data: bytes) -> None:
        self.t.write(data)

    def ask(self, data: bytes, timeout: float = 2.0) -> bytes:
        self.t.drain()
        self.t.write(data)
        r = self.t.read(timeout)
        # A firmware nehany valaszt tobb csomagban kuld; varjunk a maradekra.
        time.sleep(0.05)
        return r

    # -- lekerdezesek --

    def model(self) -> str:
        return self.ask(CMD_MODEL).decode("ascii", "replace").strip()

    def firmware(self) -> str:
        return self.ask(CMD_FIRMWARE).decode("ascii", "replace").strip()

    def serial_number(self) -> str:
        return self.ask(CMD_SERIAL).decode("ascii", "replace").strip()

    def boot_version(self) -> str:
        return self.ask(CMD_BOOT).decode("ascii", "replace").strip()

    def battery(self) -> int:
        r = self.ask(CMD_BATTERY)
        return r[-1] if r else -1

    def status(self) -> Status:
        r = self.ask(CMD_STATUS)
        if not r:
            raise PrinterError("A nyomtato nem valaszolt az allapot-lekerdezesre")
        return Status(r[-1])

    def shutdown_minutes(self) -> int:
        r = self.ask(CMD_SHUTDOWN_GET)
        if not r:
            return -1
        return (r[0] << 8) | r[1] if len(r) >= 2 else r[0]

    def all_info(self) -> dict:
        """10 FF 70 - csovel elvalasztott osszefoglalo."""
        r = self.ask(CMD_ALL_INFO)
        parts = r.decode("ascii", "replace").split("|")
        if len(parts) >= 6:
            return {
                "nev": parts[0],
                "mac_classic": parts[1],
                "mac_ble": parts[2],
                "firmware": parts[3],
                "sorozatszam": parts[4],
                "akku": parts[5] + "%",
            }
        return {"nyers": r.decode("ascii", "replace")}

    # -- beallitasok --

    def set_density(self, level: int) -> bytes:
        if not 0 <= level <= 2:
            raise ValueError("A surusegnek 0 es 2 kozott kell lennie")
        return self.ask(bytes([0x10, 0xFF, 0x10, 0x00, level]))

    def set_shutdown_minutes(self, minutes: int) -> bytes:
        return self.ask(bytes([0x10, 0xFF, 0x12, (minutes >> 8) & 0xFF, minutes & 0xFF]))

    def feed(self, dots: int = END_LINE_DOT) -> None:
        """printLineDotsLuck() - papir eloretolasa n pont-sorral."""
        self.send(bytes([0x1B, 0x4A, dots & 0xFF]))

    # -- nyomtatas --

    def print_image(self, img: Image.Image, density: int | None = None,
                    dither: bool = True, feed_dots: int = END_LINE_DOT,
                    verbose: bool = False) -> bool:
        """Kep nyomtatasa. A kep automatikusan 384 px szelesre skalazodik.

        A szekvencia pontosan a BaseNormalDevice.printOnce() sorrendjet koveti.
        """
        st = self.status()
        if st.no_paper:
            raise PrinterError("Nincs papir a nyomtatoban")
        if st.cover_open:
            raise PrinterError("A fedel nyitva van")

        data, height = rasterize(img, dither=dither)
        if verbose:
            print(f"  kep: {PRINT_WIDTH}x{height} px, {len(data)} bajt raszter")

        if density is not None:
            self.set_density(density)
            time.sleep(0.1)

        self.send(CMD_ENABLE)
        time.sleep(0.15)
        self.send(CMD_WAKEUP)
        time.sleep(0.15)

        # ESC/POS raszterkep: 1D 76 30 m xL xH yL yH  (a meretek little-endian)
        self.send(bytes([0x1D, 0x76, 0x30, 0x00,
                         BYTES_PER_ROW % 256, BYTES_PER_ROW // 256,
                         height % 256, height // 256]))
        for i in range(0, len(data), 1024):
            self.send(data[i:i + 1024])
            time.sleep(0.02)
        time.sleep(0.5)

        self.feed(feed_dots)
        time.sleep(0.3)

        r = self.ask(CMD_STOP, timeout=30.0)
        return bool(r) and (r[0] == 0xAA or r.startswith(b"OK"))

    def print_text(self, text: str, font_size: int = 24, density: int | None = None,
                   margin: int = 8, verbose: bool = False) -> bool:
        return self.print_image(render_text(text, font_size, margin),
                                density=density, dither=False, verbose=verbose)


# --- kep -> raszter ---


def rasterize(img: Image.Image, dither: bool = True) -> tuple[bytes, int]:
    """Kep -> 1 bites ESC/POS raszter adat (MSB = bal szelso pixel, 1 = fekete)."""
    img = img.convert("L")
    if img.width != PRINT_WIDTH:
        height = max(1, round(img.height * PRINT_WIDTH / img.width))
        img = img.resize((PRINT_WIDTH, height))
    bw = img.convert("1") if dither else img.point(lambda p: 255 if p > 128 else 0).convert("1")

    w, h = bw.size
    px = bw.load()
    out = bytearray()
    for y in range(h):
        for xb in range(BYTES_PER_ROW):
            byte = 0
            for bit in range(8):
                x = xb * 8 + bit
                if x < w and px[x, y] == 0:
                    byte |= 0x80 >> bit
            out.append(byte)
    return bytes(out), h


def _find_font(size: int):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/Library/Fonts/Arial.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\segoeui.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
    # A beepitett bitmap font nagyon apro, termalpapiron alig olvashato,
    # de vegso esetben ez is jobb a semminel.
    return ImageFont.load_default()


def render_text(text: str, font_size: int = 24, margin: int = 8) -> Image.Image:
    """Szoveg kepre rajzolasa, automatikus tordelessel 384 px szelesseghez."""
    font = _find_font(font_size)
    maxw = PRINT_WIDTH - 2 * margin
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))

    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for word in para.split(" "):
            trial = f"{cur} {word}".strip()
            if probe.textlength(trial, font=font) <= maxw or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)

    lh = font_size + 6
    img = Image.new("L", (PRINT_WIDTH, margin * 2 + lh * len(lines)), 255)
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((margin, margin + i * lh), line, font=font, fill=0)
    return img


# --- CLI ---


def cmd_scan() -> None:
    if sys.platform == "win32":
        try:
            from serial.tools import list_ports
        except ImportError:
            print("Telepitsd a pyserial-t: pip install pyserial")
            return
        print("Soros portok (a parositott nyomtato 'Standard Serial over "
              "Bluetooth link' nevvel jelenik meg):")
        for p in list_ports.comports():
            print(f"  {p.device:8s}  {p.description}")
        print("\nHa nem latod: Windows Bluetooth beallitasok -> Eszkoz "
              "hozzaadasa -> 'Mini Pocket Printer' parositasa.")
    else:
        print("Klasszikus Bluetooth keresese (kb. 10 mp)...")
        os.system("hcitool scan --flush")
        print("\nA nyomtato neve 'Mini Pocket Printer', a cime 55:55:...-tel kezdodik.")


def build_transport(args):
    if args.port:
        return SerialTransport(args.port)
    addr = args.address or os.environ.get("TRONIC_ADDR")
    if not addr:
        raise SystemExit("Add meg a --address (MAC) vagy --port (COM) erteket, "
                         "vagy allitsd be a TRONIC_ADDR kornyezeti valtozot.")
    return RFCOMMTransport(addr)


def main() -> None:
    # A kozos kapcsolok kulon szulo-parserben vannak, igy az alparancs elott
    # es utan is megadhatok (pl. "info --address X" es "--address X info").
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--address", help="a nyomtato klasszikus BT MAC cime")
    common.add_argument("--port", help="soros port helyette (pl. COM5 vagy /dev/rfcomm0)")
    common.add_argument("--density", type=int, choices=[0, 1, 2],
                        help="nyomtatasi suruseg: 0=vilagos, 1=kozepes, 2=sotet")

    ap = argparse.ArgumentParser(description="Tronic Mini Pocket Printer (A2Y) vezerlo",
                                 parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", help="nyomtato/port keresese", parents=[common])
    sub.add_parser("info", help="modell, firmware, akku, allapot", parents=[common])
    sub.add_parser("status", help="csak az allapot", parents=[common])

    p_text = sub.add_parser("text", help="szoveg nyomtatasa", parents=[common])
    p_text.add_argument("text")
    p_text.add_argument("--font-size", type=int, default=24)

    p_img = sub.add_parser("image", help="kep nyomtatasa", parents=[common])
    p_img.add_argument("path")
    p_img.add_argument("--no-dither", action="store_true",
                       help="szinarnyalat-szoras helyett egyszeru kuszob")

    p_feed = sub.add_parser("feed", help="papir eloretolasa", parents=[common])
    p_feed.add_argument("--dots", type=int, default=END_LINE_DOT)

    p_set = sub.add_parser("set", help="beallitas modositasa", parents=[common])
    p_set.add_argument("key", choices=["density", "shutdown"])
    p_set.add_argument("value", type=int)

    args = ap.parse_args()

    if args.cmd == "scan":
        cmd_scan()
        return

    with Printer(build_transport(args)) as pr:
        if args.cmd == "info":
            print(f"  modell       : {pr.model()}")
            print(f"  firmware     : {pr.firmware()}")
            print(f"  bootloader   : {pr.boot_version()}")
            print(f"  sorozatszam  : {pr.serial_number()}")
            print(f"  akku         : {pr.battery()}%")
            print(f"  allapot      : {pr.status()}")
            print(f"  auto-kikapcs : {pr.shutdown_minutes()} perc")
            print(f"  szelesseg    : {PRINT_WIDTH} px / {DPI} dpi")

        elif args.cmd == "status":
            print(pr.status())

        elif args.cmd == "text":
            ok = pr.print_text(args.text, font_size=args.font_size,
                               density=args.density, verbose=True)
            print("Kesz." if ok else "A nyomtato nem nyugtazta a feladatot.")

        elif args.cmd == "image":
            ok = pr.print_image(Image.open(args.path), density=args.density,
                                dither=not args.no_dither, verbose=True)
            print("Kesz." if ok else "A nyomtato nem nyugtazta a feladatot.")

        elif args.cmd == "feed":
            pr.feed(args.dots)
            print(f"{args.dots} pontsor eloretolva.")

        elif args.cmd == "set":
            if args.key == "density":
                r = pr.set_density(args.value)
            else:
                r = pr.set_shutdown_minutes(args.value)
            print(f"Valasz: {r!r}")


if __name__ == "__main__":
    main()
