#!/usr/bin/env python3
"""
Tronic Mini Pocket Printer (A2Y) - asztali kezelofelulet.

A protokoll-logikat a tronic_printer.py tartalmazza; ez a fajl csak a
grafikus felulet.  A ket fajlnak egy mappaban kell lennie.

Windows-on a legegyszerubb inditas: dupla katt a start_gui.bat-ra.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from PIL import Image, ImageTk

from tronic_printer import (
    DPI,
    PRINT_WIDTH,
    Printer,
    PrinterError,
    RFCOMMTransport,
    SerialTransport,
    render_text,
)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Tronic Mini Pocket Printer (A2Y)")
        root.geometry("720x640")

        self.msgq: queue.Queue = queue.Queue()
        self.image: Image.Image | None = None
        self.preview_photo = None
        self.busy = False

        self._build_ui()
        self.root.after(100, self._drain_log)

    # ---------- felulet ----------

    def _build_ui(self):
        pad = {"padx": 4, "pady": 3}

        conn = ttk.LabelFrame(self.root, text="Kapcsolat")
        conn.pack(fill="x", **pad)

        self.mode = tk.StringVar(value="rfcomm")
        ttk.Radiobutton(conn, text="Bluetooth MAC (RFCOMM)", variable=self.mode,
                        value="rfcomm").grid(row=0, column=0, sticky="w", **pad)
        ttk.Radiobutton(conn, text="Soros / COM port", variable=self.mode,
                        value="serial").grid(row=1, column=0, sticky="w", **pad)

        self.addr = ttk.Entry(conn, width=28)
        self.addr.insert(0, os.environ.get("TRONIC_ADDR", "55:55:09:10:98:B6"))
        self.addr.grid(row=0, column=1, **pad)

        self.port = ttk.Combobox(conn, width=26)
        self.port.grid(row=1, column=1, **pad)

        ttk.Button(conn, text="Portok frissitese",
                   command=self.refresh_ports).grid(row=1, column=2, **pad)
        ttk.Button(conn, text="Info lekerdezes",
                   command=lambda: self.run(self.do_info)).grid(row=0, column=2, **pad)

        ttk.Label(conn, text=f"Nyomtatasi szelesseg: {PRINT_WIDTH} px / {DPI} dpi "
                             f"({PRINT_WIDTH / DPI * 25.4:.0f} mm)").grid(
            row=2, column=0, columnspan=3, sticky="w", **pad)

        # -- szoveg --
        txt = ttk.LabelFrame(self.root, text="Szoveg nyomtatasa")
        txt.pack(fill="x", **pad)
        self.text_box = tk.Text(txt, height=4, width=54)
        self.text_box.insert("1.0", "Szia!\nEz egy teszt.")
        self.text_box.grid(row=0, column=0, rowspan=2, **pad)
        ttk.Label(txt, text="Betumeret:").grid(row=0, column=1, sticky="e", **pad)
        self.font_size = ttk.Spinbox(txt, from_=10, to=72, width=6)
        self.font_size.set(26)
        self.font_size.grid(row=0, column=2, sticky="w", **pad)
        ttk.Button(txt, text="Szoveg nyomtatasa",
                   command=lambda: self.run(self.do_print_text)).grid(
            row=1, column=1, columnspan=2, sticky="ew", **pad)

        # -- kep --
        img = ttk.LabelFrame(self.root, text="Kep nyomtatasa")
        img.pack(fill="x", **pad)
        ttk.Button(img, text="Kep betoltese...",
                   command=self.load_image).grid(row=0, column=0, **pad)
        self.dither = tk.BooleanVar(value=True)
        ttk.Checkbutton(img, text="Szinarnyalat-szoras (fotokhoz)",
                        variable=self.dither,
                        command=self.refresh_preview).grid(row=0, column=1, **pad)
        ttk.Label(img, text="Suruseg:").grid(row=1, column=0, sticky="e", **pad)
        self.density = ttk.Combobox(img, width=12, state="readonly",
                                    values=["0 - vilagos", "1 - kozepes", "2 - sotet"])
        self.density.current(1)
        self.density.grid(row=1, column=1, sticky="w", **pad)
        ttk.Button(img, text="Kep nyomtatasa",
                   command=lambda: self.run(self.do_print_image)).grid(
            row=2, column=0, columnspan=2, sticky="ew", **pad)
        ttk.Button(img, text="Papir eloretolas",
                   command=lambda: self.run(self.do_feed)).grid(
            row=3, column=0, columnspan=2, sticky="ew", **pad)
        self.preview_lbl = ttk.Label(img, text="(nincs kep)", relief="sunken",
                                     anchor="center", width=28)
        self.preview_lbl.grid(row=0, column=2, rowspan=4, padx=8, pady=4)

        logf = ttk.LabelFrame(self.root, text="Naplo")
        logf.pack(fill="both", expand=True, **pad)
        self.log_txt = scrolledtext.ScrolledText(logf, height=12, font=("Consolas", 9))
        self.log_txt.pack(fill="both", expand=True)

        self.log("Kapcsold be a nyomtatot, majd nyomd meg az 'Info lekerdezes'-t.")
        self.log("Windows-on eloszor parositsd a nyomtatot a Bluetooth "
                 "beallitasokban ('Mini Pocket Printer').")
        self.refresh_ports()

    # ---------- naplo ----------

    def log(self, msg: str):
        self.msgq.put(msg)

    def _drain_log(self):
        while True:
            try:
                msg = self.msgq.get_nowait()
            except queue.Empty:
                break
            self.log_txt.insert("end", msg + "\n")
            self.log_txt.see("end")
        self.root.after(100, self._drain_log)

    # ---------- kapcsolat ----------

    def refresh_ports(self):
        try:
            from serial.tools import list_ports
        except ImportError:
            self.log("(a COM port listahoz telepitsd: pip install pyserial)")
            return
        vals = [f"{p.device} - {p.description}" for p in list_ports.comports()]
        self.port["values"] = vals
        if vals and not self.port.get():
            self.port.current(0)

    def make_transport(self):
        if self.mode.get() == "serial":
            raw = self.port.get().strip()
            if not raw:
                raise PrinterError("Valassz egy COM portot.")
            return SerialTransport(raw.split(" - ")[0])
        addr = self.addr.get().strip()
        if not addr:
            raise PrinterError("Add meg a nyomtato MAC cimet.")
        return RFCOMMTransport(addr)

    def run(self, fn):
        """Hattertszalon futtat, mert a Bluetooth I/O blokkolo."""
        if self.busy:
            self.log("(mar fut egy muvelet, varj)")
            return
        self.busy = True

        def worker():
            try:
                with Printer(self.make_transport()) as pr:
                    fn(pr)
            except PrinterError as e:
                self.log(f"!! {e}")
            except Exception as e:
                self.log(f"!! {type(e).__name__}: {e}")
            finally:
                self.busy = False

        threading.Thread(target=worker, daemon=True).start()

    # ---------- muveletek ----------

    def do_info(self, pr: Printer):
        self.log("\n--- info ---")
        self.log(f"  modell       : {pr.model()}")
        self.log(f"  firmware     : {pr.firmware()}")
        self.log(f"  bootloader   : {pr.boot_version()}")
        self.log(f"  akku         : {pr.battery()}%")
        self.log(f"  allapot      : {pr.status()}")
        self.log(f"  auto-kikapcs : {pr.shutdown_minutes()} perc")

    def do_print_text(self, pr: Printer):
        text = self.text_box.get("1.0", "end").rstrip("\n")
        if not text.strip():
            self.log("Ures szoveg.")
            return
        self.log("\n--- szoveg nyomtatasa ---")
        ok = pr.print_text(text, font_size=int(self.font_size.get()),
                           density=self.density.current())
        self.log("Kesz." if ok else "A nyomtato nem nyugtazta a feladatot.")

    def do_print_image(self, pr: Printer):
        if self.image is None:
            self.log("Nincs betoltve kep.")
            return
        self.log("\n--- kep nyomtatasa ---")
        ok = pr.print_image(self.image, density=self.density.current(),
                            dither=self.dither.get())
        self.log("Kesz." if ok else "A nyomtato nem nyugtazta a feladatot.")

    def do_feed(self, pr: Printer):
        pr.feed()
        self.log("Papir eloretolva.")

    # ---------- kep ----------

    def load_image(self):
        path = filedialog.askopenfilename(
            title="Kep kivalasztasa",
            filetypes=[("Kepek", "*.png *.jpg *.jpeg *.bmp *.gif"),
                       ("Minden fajl", "*.*")])
        if not path:
            return
        self.image = Image.open(path)
        self.log(f"Betoltve: {path} ({self.image.width}x{self.image.height})")
        self.refresh_preview()

    def refresh_preview(self):
        if self.image is None:
            return
        img = self.image.convert("L")
        h = max(1, round(img.height * PRINT_WIDTH / img.width))
        img = img.resize((PRINT_WIDTH, h))
        bw = img.convert("1") if self.dither.get() else \
            img.point(lambda p: 255 if p > 128 else 0).convert("1")
        # elonezet felmeretben, hogy elferjen az ablakban
        shown = bw.resize((PRINT_WIDTH // 2, max(1, min(h // 2, 220))))
        self.preview_photo = ImageTk.PhotoImage(shown)
        self.preview_lbl.config(image=self.preview_photo, text="")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
