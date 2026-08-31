#!/usr/bin/env python3
"""
Create/apply a custom Windows printer form for the Tronic mini printer.

Requires: pywin32
"""

from __future__ import annotations

import argparse
import sys

try:
    import win32print
except ImportError as exc:
    print("pywin32 is required: pip install pywin32", file=sys.stderr)
    raise SystemExit(2) from exc


DM_PAPERSIZE = 0x00000002
DM_PAPERLENGTH = 0x00000004
DM_PAPERWIDTH = 0x00000008
DM_FORMNAME = 0x00010000


def mm_to_um(mm: float) -> int:
    """Millimeters to micrometers (FORM_INFO uses 0.001 mm)."""
    return int(round(mm * 1000.0))


def mm_to_dm(mm: float) -> int:
    """Millimeters to DEVMODE paper units (0.1 mm)."""
    return int(round(mm * 10.0))


def ensure_form(hprinter, form_name: str, width_mm: float, length_mm: float) -> None:
    cx = mm_to_um(width_mm)
    cy = mm_to_um(length_mm)
    wanted = {
        "Flags": 0,
        "Name": form_name,
        "Size": {"cx": cx, "cy": cy},
        "ImageableArea": {"left": 0, "top": 0, "right": cx, "bottom": cy},
    }

    recreate = True
    try:
        current = win32print.GetForm(hprinter, form_name)
        cur_size = current.get("Size", {})
        if abs(cur_size.get("cx", 0) - cx) <= 10 and abs(cur_size.get("cy", 0) - cy) <= 10:
            recreate = False
        else:
            try:
                win32print.DeleteForm(hprinter, form_name)
            except Exception:
                pass
    except Exception:
        pass

    if recreate:
        win32print.AddForm(hprinter, wanted)


def apply_form_to_printer(printer_name: str, form_name: str, width_mm: float, length_mm: float) -> None:
    defaults = {"DesiredAccess": win32print.PRINTER_ALL_ACCESS}
    hprinter = win32print.OpenPrinter(printer_name, defaults)
    try:
        ensure_form(hprinter, form_name, width_mm, length_mm)
        info2 = win32print.GetPrinter(hprinter, 2)
        devmode = info2.get("pDevMode")
        if devmode is None:
            raise RuntimeError("Printer driver did not expose DEVMODE.")

        # On NT-based Windows, dmFormName is the authoritative custom size selector.
        devmode.Fields |= DM_FORMNAME | DM_PAPERSIZE | DM_PAPERWIDTH | DM_PAPERLENGTH
        devmode.FormName = form_name
        devmode.PaperSize = 0  # DMPAPER_USER/custom
        devmode.PaperWidth = mm_to_dm(width_mm)
        devmode.PaperLength = mm_to_dm(length_mm)
        info2["pDevMode"] = devmode
        info2["pSecurityDescriptor"] = None

        win32print.SetPrinter(hprinter, 2, info2, 0)
    finally:
        win32print.ClosePrinter(hprinter)


def main() -> int:
    ap = argparse.ArgumentParser(description="Set custom Windows paper form for Tronic printer queue.")
    ap.add_argument("--printer-name", required=True)
    ap.add_argument("--form-name", default="Tronic_48x200mm")
    ap.add_argument("--width-mm", type=float, default=48.0)
    ap.add_argument("--length-mm", type=float, default=200.0)
    args = ap.parse_args()

    try:
        apply_form_to_printer(args.printer_name, args.form_name, args.width_mm, args.length_mm)
    except Exception as exc:
        print(f"FAILED to set paper form: {exc}", file=sys.stderr)
        return 1

    print(
        f"Custom form applied: {args.form_name} "
        f"({args.width_mm:.1f}mm x {args.length_mm:.1f}mm) -> {args.printer_name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
