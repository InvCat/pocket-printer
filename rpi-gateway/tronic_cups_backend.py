#!/usr/bin/env python3
"""CUPS backend + optional TCP :9100 gateway for the Tronic Mini Pocket Printer.

Device URIs
-----------
  tronic://AA:BB:CC:DD:EE:FF          Bluetooth Classic SPP (RFCOMM)
  tronic:/dev/ttyACM0                USB-C / serial
  tronic:/dev/rfcomm0                pre-bound RFCOMM TTY

CUPS invokes this script as::

    tronic job-id user title copies options [file]

With no arguments it prints a discovery line for ``lpinfo -v``.

Environment
-----------
  DEVICE_URI     set by CUPS (required when printing)
  TRONIC_ADDR    fallback MAC if URI has no address
  TRONIC_DENSITY 0/1/2 print density (default 1)
  TRONIC_DPI     rasterize DPI hint for PDF (default 203)
  TRONIC_LIB     directory that contains tronic_printer.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from urllib.parse import unquote

# ---------------------------------------------------------------------------
# Locate tronic_printer.py (repo checkout, install prefix, or TRONIC_LIB)
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_CANDIDATES = [
    os.environ.get("TRONIC_LIB", ""),
    str(_HERE.parent),                          # repo: ../tronic_printer.py
    str(_HERE),                                 # same folder
    "/usr/local/lib/tronic-pocket-printer",
]
for _c in _CANDIDATES:
    if _c and (_c not in sys.path):
        sys.path.insert(0, _c)

try:
    from tronic_printer import (  # type: ignore
        DPI,
        PRINT_WIDTH,
        Printer,
        PrinterError,
        RFCOMMTransport,
        SerialTransport,
        render_text,
    )
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(f"ERROR: cannot import tronic_printer: {exc}\n")
    sys.exit(1)

from PIL import Image


LOG_TAG = "tronic-cups"
CONF_PATHS = (
    Path("/etc/tronic-pocket-printer.conf"),
    _HERE / "tronic-pocket.conf",
)


def log(msg: str) -> None:
    sys.stderr.write(f"{LOG_TAG}: {msg}\n")
    sys.stderr.flush()


def load_conf_env() -> None:
    """Load KEY=VALUE pairs from the gateway conf into os.environ (no override)."""
    for path in CONF_PATHS:
        if not path.is_file():
            continue
        try:
            for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'").strip('"')
                if key and key not in os.environ:
                    os.environ[key] = val
        except OSError as exc:
            log(f"could not read {path}: {exc}")
        break


load_conf_env()


# ---------------------------------------------------------------------------
# URI / transport
# ---------------------------------------------------------------------------


def _normalize_mac(value: str) -> str | None:
    """Accept AA:BB:… or AA-BB-… (CUPS rejects multi-colon device URIs)."""
    cleaned = value.strip().replace("-", ":").upper()
    parts = cleaned.split(":")
    if len(parts) != 6:
        return None
    try:
        if not all(len(p) == 2 and int(p, 16) >= 0 for p in parts):
            return None
    except ValueError:
        return None
    return cleaned


def parse_device_uri(uri: str) -> tuple[str, str]:
    """Return (mode, target) where mode is 'rfcomm' or 'serial'.

    Prefer CUPS-safe URIs like ``tronic:55-55-09-10-98-B6`` — lpadmin rejects
    ``tronic://55:55:…`` because of the extra colons.
    """
    if not uri:
        raise PrinterError("DEVICE_URI is empty")

    uri = uri.strip()

    # Bare serial path
    if uri.startswith("/dev/"):
        return "serial", uri

    # Strip scheme variants: tronic://…  tronic:…  socket://…
    rest = uri
    for prefix in ("tronic://", "tronic:", "socket://", "file://"):
        if rest.lower().startswith(prefix):
            rest = rest[len(prefix):]
            break
    rest = unquote(rest).strip()

    # Optional userinfo@
    if "@" in rest and not rest.startswith("/"):
        rest = rest.split("@", 1)[1]

    # Path-only serial: /dev/ttyACM0 or ///dev/ttyACM0
    path = rest.lstrip("/")
    if rest.startswith("/") and (rest.startswith("/dev/") or path.startswith("dev/")):
        serial = rest if rest.startswith("/dev/") else "/" + path
        serial = serial.split("?", 1)[0]
        # Avoid treating /55-55-… as serial
        mac = _normalize_mac(serial.lstrip("/"))
        if mac:
            return "rfcomm", mac
        return "serial", serial

    # MAC may be followed by /path or ?query
    mac_raw = rest.split("/", 1)[0].split("?", 1)[0].strip()
    mac = _normalize_mac(mac_raw)
    if mac:
        return "rfcomm", mac

    if rest.startswith("/dev/") or path.startswith("dev/"):
        serial = rest if rest.startswith("/") else "/" + path
        return "serial", serial.split("?", 1)[0]

    raise PrinterError(f"Cannot parse DEVICE_URI: {uri!r}")


def cups_device_uri(mac: str) -> str:
    """Build a CUPS-safe device URI from a Bluetooth MAC."""
    mac_n = _normalize_mac(mac)
    if not mac_n:
        raise PrinterError(f"Invalid MAC: {mac!r}")
    return "tronic:" + mac_n.replace(":", "-")



def build_transport_from_uri(uri: str):
    mode, target = parse_device_uri(uri)
    if mode == "serial":
        return SerialTransport(target)
    return RFCOMMTransport(target)


# ---------------------------------------------------------------------------
# Job → images
# ---------------------------------------------------------------------------


def _which(name: str) -> str | None:
    return shutil.which(name)


def pdf_page_size_pts(pdf_path: Path) -> tuple[float, float] | None:
    """Return (width_pt, height_pt) of page 1 via pdfinfo, or None."""
    pdfinfo = _which("pdfinfo")
    if not pdfinfo:
        return None
    try:
        out = subprocess.check_output(
            [pdfinfo, str(pdf_path)], text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    for line in out.splitlines():
        if line.lower().startswith("page size:"):
            # Page size:           595 x 842 pts (A4)
            nums = []
            for part in line.replace(",", " ").split():
                try:
                    nums.append(float(part))
                except ValueError:
                    continue
                if len(nums) >= 2:
                    return nums[0], nums[1]
    return None


def pdf_extract_text(pdf_path: Path) -> str:
    """Extract plain text; empty string if none / tool missing."""
    pdftotext = _which("pdftotext")
    if not pdftotext:
        return ""
    try:
        out = subprocess.check_output(
            [pdftotext, "-layout", "-enc", "UTF-8", str(pdf_path), "-"],
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, OSError):
        return ""
    text = out.decode("utf-8", "replace")
    # Normalize Notepad / Windows junk
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    # Drop trailing empty lines
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines).strip()


def pdf_is_office_page(pdf_path: Path) -> bool:
    """True if page is much wider than our 48 mm roll (A4/Letter/etc.)."""
    size = pdf_page_size_pts(pdf_path)
    if not size:
        return False
    width_pt, _height_pt = size
    # 48 mm ≈ 136 pt; anything above ~180 pt is clearly not roll-native
    return width_pt > 180


def pdf_to_roll_images(pdf_path: Path, out_dir: Path, dpi: int) -> list[Path]:
    """Turn a PDF into roll-ready PNG(s).

    Native 48 mm jobs (Imagesetter / correct Windows form) are rasterized 1:1
    at printer DPI — original font size is preserved.

    Optional ``TRONIC_REFLOW=1``: A4/Letter text jobs are re-rendered with
    ``render_text`` (legacy fallback for Microsoft IPP Class Driver).
    """
    reflow = os.environ.get("TRONIC_REFLOW", "0").lower() not in ("0", "false", "no", "")
    if reflow and pdf_is_office_page(pdf_path):
        text = pdf_extract_text(pdf_path)
        if text and any(ch.isalnum() for ch in text):
            font_size = 28 if len(text) < 80 else 24
            log(
                f"A4/Letter PDF — reflowing text to 48 mm "
                f"({len(text)} chars, font={font_size}) [TRONIC_REFLOW=1]"
            )
            img = render_text(text, font_size=font_size, margin=8)
            out = out_dir / "reflow-1.png"
            img.save(out)
            return [out]
        log("wide PDF, reflow on but no text — raster fallback")

    if pdf_is_office_page(pdf_path):
        log(
            f"wide PDF (A4/Letter) — raster 1:1 at {dpi} dpi "
            "(set TRONIC_REFLOW=1 to reflow text instead)"
        )
    else:
        log(f"roll-sized PDF — raster 1:1 at {dpi} dpi (preserve font size)")

    return pdf_to_pngs(pdf_path, out_dir, dpi)


def pdf_to_pngs(pdf_path: Path, out_dir: Path, dpi: int) -> list[Path]:
    """Rasterize PDF pages to PNG. Prefers pdftoppm, falls back to gs."""
    prefix = out_dir / "page"
    pdftoppm = _which("pdftoppm")
    if pdftoppm:
        subprocess.run(
            [pdftoppm, "-png", "-r", str(dpi), str(pdf_path), str(prefix)],
            check=True,
            capture_output=True,
        )
        pages = sorted(out_dir.glob("page*.png"))
        if pages:
            return pages

    gs = _which("gs")
    if not gs:
        raise PrinterError("Need pdftoppm (poppler-utils) or ghostscript (gs)")

    # Ghostscript: one PNG per page
    pattern = str(out_dir / "page-%03d.png")
    subprocess.run(
        [
            gs,
            "-dSAFER",
            "-dBATCH",
            "-dNOPAUSE",
            "-sDEVICE=pnggray",
            f"-r{dpi}",
            f"-sOutputFile={pattern}",
            str(pdf_path),
        ],
        check=True,
        capture_output=True,
    )
    pages = sorted(out_dir.glob("page-*.png"))
    if not pages:
        raise PrinterError("Ghostscript produced no pages")
    return pages


def sniff_and_materialize(data: bytes, work: Path) -> list[Path]:
    """Write stdin/file bytes into page images (PNG list)."""
    if not data:
        raise PrinterError("Empty print job")

    # PNG
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        p = work / "job.png"
        p.write_bytes(data)
        return [p]

    # JPEG
    if data[:3] == b"\xff\xd8\xff":
        p = work / "job.jpg"
        p.write_bytes(data)
        return [p]

    # PDF (also CUPS often sends PDF)
    if data[:4] == b"%PDF" or b"%PDF" in data[:1024]:
        pdf = work / "job.pdf"
        pdf.write_bytes(data)
        dpi = int(os.environ.get("TRONIC_DPI", str(DPI)))
        return pdf_to_roll_images(pdf, work, dpi)

    # PostScript
    head = data[:256].lstrip()
    if head.startswith(b"%!") or head.startswith(b"\x04%!"):
        ps = work / "job.ps"
        ps.write_bytes(data)
        pdf = work / "job.pdf"
        gs = _which("gs")
        if not gs:
            raise PrinterError("PostScript job needs ghostscript (gs)")
        subprocess.run(
            [
                gs,
                "-dSAFER",
                "-dBATCH",
                "-dNOPAUSE",
                "-sDEVICE=pdfwrite",
                f"-sOutputFile={pdf}",
                str(ps),
            ],
            check=True,
            capture_output=True,
        )
        dpi = int(os.environ.get("TRONIC_DPI", str(DPI)))
        return pdf_to_roll_images(pdf, work, dpi)

    # CUPS raster / unknown: try as PDF anyway, then as image
    pdf = work / "job.pdf"
    pdf.write_bytes(data)
    try:
        dpi = int(os.environ.get("TRONIC_DPI", str(DPI)))
        return pdf_to_roll_images(pdf, work, dpi)
    except Exception:
        pass

    try:
        p = work / "job.bin.png"
        Image.open(__import__("io").BytesIO(data)).save(p)
        return [p]
    except Exception as exc:
        raise PrinterError(
            f"Unrecognized job format (first bytes {data[:16]!r}): {exc}"
        ) from exc


def trim_whitespace(
    img: Image.Image,
    *,
    threshold: int = 245,
    margin: int = 8,
    margin_bottom: int = 16,
    vertical_only: bool = False,
) -> Image.Image:
    """Crop near-white margins.

    ``vertical_only`` keeps the full page width (important for native 48 mm
    jobs: cropping to the ink box and then scaling up would magnify text).
    Full crop is still used for oversized A4/Letter pages.
    """
    gray = img.convert("L")
    w, h = gray.size
    if w < 8 or h < 8:
        return img

    px = gray.load()
    min_x, min_y = w, h
    max_x, max_y = -1, -1
    for y in range(h):
        for x in range(w):
            if px[x, y] < threshold:
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y

    if max_x < 0 or max_y < 0:
        stub_h = min(h, max(32, margin + margin_bottom + 8))
        return img.crop((0, 0, w, stub_h))

    top = max(0, min_y - margin)
    bottom = min(h, max_y + 1 + margin_bottom)
    if vertical_only:
        left, right = 0, w
    else:
        left = max(0, min_x - margin)
        right = min(w, max_x + 1 + margin)

    if left == 0 and top == 0 and right == w and bottom == h:
        return img
    return img.crop((left, top, right, bottom))


def fit_to_printer(img: Image.Image, *, trim: bool = True) -> Image.Image:
    """Fit content to 384 px roll width, preserving physical size when possible.

    Roll-native pages (physical width ≲ 60 mm at ``TRONIC_DPI``) are WYSIWYG:
    vertical trim only, scale down to 384 px if the raster DPI was higher than
    the head, never scale up. Wide A4/Letter rasters still crop-to-ink + fit.

    Landscape 48×80 forms from Windows (raster wider than tall, ~80×48 mm)
    are rotated so the 48 mm edge matches the print head.
    """
    img = img.convert("RGB")
    src_w, src_h = img.size
    dpi = int(os.environ.get("TRONIC_DPI", str(DPI)))
    width_mm = src_w / max(dpi, 1) * 25.4
    height_mm = src_h / max(dpi, 1) * 25.4

    # Windows 48x80 form printed in landscape → ~80 mm wide × ~48 mm tall.
    # Rotate so the short edge is across the head.
    if src_w > src_h and 55 <= width_mm <= 95 and 35 <= height_mm <= 60:
        img = img.transpose(Image.ROTATE_90)
        src_w, src_h = img.size
        width_mm = src_w / max(dpi, 1) * 25.4
        height_mm = src_h / max(dpi, 1) * 25.4
        log(f"rotated landscape roll page -> {src_w}x{src_h}px ({width_mm:.0f}x{height_mm:.0f} mm)")

    roll_native = width_mm <= 60.0
    log(
        f"fit: src {src_w}x{src_h}px @ {dpi} dpi ≈ {width_mm:.1f} mm "
        f"({'roll-native WYSIWYG' if roll_native else 'wide page'})"
    )

    if trim and os.environ.get("TRONIC_TRIM", "1") not in ("0", "false", "no"):
        before = img.size
        img = trim_whitespace(img, vertical_only=roll_native)
        if img.size != before:
            log(
                f"trimmed ({'vertical' if roll_native else 'full'}): "
                f"{before[0]}x{before[1]} -> {img.width}x{img.height}"
            )

    if roll_native:
        # Map physical size onto the 384 px head without changing glyph size
        # relative to the 48 mm page (only downsample if raster was denser).
        if img.width > PRINT_WIDTH:
            height = max(1, round(img.height * PRINT_WIDTH / img.width))
            resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)
            img = img.resize((PRINT_WIDTH, height), resample)
            log(f"WYSIWYG downsample to head: {img.width}x{img.height}")
        elif img.width < PRINT_WIDTH:
            canvas = Image.new("RGB", (PRINT_WIDTH, img.height), (255, 255, 255))
            canvas.paste(img, (0, 0))
            img = canvas
            log(f"WYSIWYG pad to head: {img.width}x{img.height}")
        else:
            log(f"WYSIWYG exact: {img.width}x{img.height}")
        return img

    # Wide office page: crop already done; scale to full roll width.
    if abs(img.width - PRINT_WIDTH) <= 2:
        if img.width != PRINT_WIDTH:
            img = img.resize((PRINT_WIDTH, img.height), Image.NEAREST)
        return img

    if img.width != PRINT_WIDTH:
        height = max(1, round(img.height * PRINT_WIDTH / img.width))
        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)
        img = img.resize((PRINT_WIDTH, height), resample)
        log(f"wide-page fit to head: {img.width}x{img.height}")
    return img


def mm_to_dots(mm: float, dpi: int = DPI) -> int:
    """Millimetres → pixel/dot rows at printer DPI."""
    return max(0, int(round(mm / 25.4 * dpi)))


def tearoff_feed_dots(tear_mm: float | None = None) -> int:
    """Dots to advance after the last ink so the strip clears the tear bar.

    White raster padding is unreliable on this head (firmware often skips
    trailing blank rows); ESC/J feed is what actually moves the paper.
    Default ~10 mm is enough to clear the serrated tear edge on the A2Y.
    """
    if tear_mm is None:
        tear_mm = float(os.environ.get("TRONIC_TEAR_MM", "10"))
    return mm_to_dots(tear_mm)


def print_images(images: list[Path], transport, density: int | None) -> None:
    tear_mm = float(os.environ.get("TRONIC_TEAR_MM", "10"))
    feed_dots = tearoff_feed_dots(tear_mm)
    log(f"tear-off feed: {tear_mm:g} mm ({feed_dots} dots)")
    with Printer(transport) as pr:
        for i, path in enumerate(images, 1):
            log(f"printing page {i}/{len(images)}: {path.name}")
            img = fit_to_printer(Image.open(path), trim=True)
            ok = pr.print_image(
                img, density=density, dither=True, feed_dots=feed_dots, verbose=False
            )
            if not ok:
                raise PrinterError(f"Printer did not ACK page {i}")


# ---------------------------------------------------------------------------
# CUPS entry points
# ---------------------------------------------------------------------------


def cups_discover() -> int:
    # device-class device-uri "model" "description"
    sys.stdout.write(
        'direct tronic "Tronic Mini Pocket Printer" '
        '"Tronic Mini Pocket Printer (A2Y / Lidl)"\n'
    )
    return 0


def cups_print(argv: list[str]) -> int:
    # argv: backend job-id user title copies options [file]
    job_id = argv[1] if len(argv) > 1 else "?"
    title = argv[3] if len(argv) > 3 else ""
    options = argv[5] if len(argv) > 5 else ""
    infile = argv[6] if len(argv) > 6 else None

    uri = os.environ.get("DEVICE_URI") or ""
    if not uri or uri == "tronic":
        addr = os.environ.get("TRONIC_ADDR", "")
        if addr:
            uri = cups_device_uri(addr)
    if not uri:
        log("No DEVICE_URI / TRONIC_ADDR")
        return 1

    density_env = os.environ.get("TRONIC_DENSITY")
    density = int(density_env) if density_env not in (None, "") else 1

    # CUPS option: Density=0|1|2
    for opt in options.replace(",", " ").split():
        if opt.lower().startswith("density="):
            try:
                density = int(opt.split("=", 1)[1])
            except ValueError:
                pass

    log(f"job={job_id} title={title!r} uri={uri} density={density}")

    if infile:
        data = Path(infile).read_bytes()
    else:
        data = sys.stdin.buffer.read()

    transport = build_transport_from_uri(uri)

    with tempfile.TemporaryDirectory(prefix="tronic-cups-") as tmp:
        work = Path(tmp)
        try:
            pages = sniff_and_materialize(data, work)
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or b"").decode("utf-8", "replace")[:500]
            log(f"rasterize failed: {err}")
            return 1
        log(f"{len(pages)} page(s), width target {PRINT_WIDTH}px")
        try:
            print_images(pages, transport, density)
        except PrinterError as exc:
            log(str(exc))
            return 1
        except OSError as exc:
            log(f"I/O error: {exc}")
            return 1

    log("job finished OK")
    return 0


# ---------------------------------------------------------------------------
# Optional TCP :9100 JetDirect-style gateway (systemd)
# ---------------------------------------------------------------------------


def run_tcp_gateway(host: str, port: int, uri: str, density: int) -> None:
    import socket
    import threading

    def handle(conn: socket.socket, addr) -> None:
        log(f"TCP client {addr}")
        try:
            chunks: list[bytes] = []
            conn.settimeout(60)
            while True:
                try:
                    buf = conn.recv(65536)
                except socket.timeout:
                    break
                if not buf:
                    break
                chunks.append(buf)
                # Heuristic: if we already have a full PDF and idle, stop early
                if sum(len(c) for c in chunks) > 8 and chunks[0][:4] == b"%PDF":
                    conn.settimeout(1.5)
            data = b"".join(chunks)
            if not data:
                log("empty TCP job")
                return
            transport = build_transport_from_uri(uri)
            with tempfile.TemporaryDirectory(prefix="tronic-tcp-") as tmp:
                pages = sniff_and_materialize(data, Path(tmp))
                print_images(pages, transport, density)
            log(f"TCP job from {addr} OK")
            try:
                conn.sendall(b"OK\n")
            except OSError:
                pass
        except Exception:
            log(traceback.format_exc())
            try:
                conn.sendall(b"ERR\n")
            except OSError:
                pass
        finally:
            conn.close()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(3)
    log(f"TCP gateway listening on {host}:{port} -> {uri}")
    while True:
        conn, addr = sock.accept()
        threading.Thread(target=handle, args=(conn, addr), daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    if len(argv) == 1:
        # CUPS discovery
        return cups_discover()

    if len(argv) >= 2 and argv[1] == "--tcp":
        # systemd helper: --tcp [--host 0.0.0.0] [--port 9100]
        import argparse

        ap = argparse.ArgumentParser(prog="tronic-gateway")
        ap.add_argument("--tcp", action="store_true")
        ap.add_argument("--host", default="0.0.0.0")
        ap.add_argument("--port", type=int, default=9100)
        ap.add_argument("--uri", default=os.environ.get("DEVICE_URI", ""))
        ap.add_argument("--address", default=os.environ.get("TRONIC_ADDR", ""))
        ap.add_argument("--density", type=int, default=int(os.environ.get("TRONIC_DENSITY", "1")))
        args = ap.parse_args(argv[1:])
        uri = args.uri
        if not uri:
            if not args.address:
                log("--tcp needs --uri tronic:AA-BB-… or --address / TRONIC_ADDR")
                return 2
            uri = cups_device_uri(args.address)
        run_tcp_gateway(args.host, args.port, uri, args.density)
        return 0

    # CUPS print invocation
    try:
        return cups_print(argv)
    except Exception:
        log(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
