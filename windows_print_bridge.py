#!/usr/bin/env python3
"""
Raw TCP print bridge for Tronic Mini Pocket Printer (A2Y).

This script lets Windows use the mini printer as a normal printer queue:
- Windows sends ESC/POS data to localhost:9100 (Raw TCP port).
- The bridge forwards that data to the printer over RFCOMM or COM port.
- A2Y start/stop wrapper commands are added automatically.

Recommended on Windows:
1) Pair the printer in Bluetooth settings.
2) Use the outgoing COM port (Serial mode) for reliability.
3) Create a TCP/IP printer port pointing to 127.0.0.1:9100.
4) Use an ESC/POS-compatible printer driver (for graphics support).
"""

from __future__ import annotations

import argparse
import logging
import socket
import socketserver
import threading
import time
from dataclasses import dataclass

from tronic_printer import (
    BYTES_PER_ROW,
    CMD_ENABLE,
    CMD_STOP,
    CMD_WAKEUP,
    END_LINE_DOT,
    Printer,
    PrinterError,
    RFCOMMTransport,
    SerialTransport,
)


LOG = logging.getLogger("tronic-bridge")
TARGET_BYTES_PER_ROW = BYTES_PER_ROW  # 48 bytes = 384 px


def _row_to_bits(row: bytes, width_bits: int) -> list[int]:
    bits: list[int] = []
    for x in range(width_bits):
        b = row[x // 8]
        bits.append(1 if (b & (0x80 >> (x % 8))) else 0)
    return bits


def _bits_to_row(bits: list[int], width_bits: int) -> bytes:
    out = bytearray((width_bits + 7) // 8)
    for x, value in enumerate(bits):
        if value:
            out[x // 8] |= 0x80 >> (x % 8)
    return bytes(out)


def _scale_row(row: bytes, src_width_bytes: int, dst_width_bytes: int) -> bytes:
    src_width_bits = src_width_bytes * 8
    dst_width_bits = dst_width_bytes * 8
    src_bits = _row_to_bits(row, src_width_bits)
    dst_bits: list[int] = []
    for x in range(dst_width_bits):
        src_x = int(x * src_width_bits / dst_width_bits)
        dst_bits.append(src_bits[src_x])
    return _bits_to_row(dst_bits, dst_width_bits)


def _rescale_raster(payload: bytes, src_width_bytes: int, rows: int) -> bytes:
    if src_width_bytes == TARGET_BYTES_PER_ROW:
        return payload
    out = bytearray()
    for row_idx in range(rows):
        start = row_idx * src_width_bytes
        row = payload[start:start + src_width_bytes]
        out.extend(_scale_row(row, src_width_bytes, TARGET_BYTES_PER_ROW))
    return bytes(out)


def normalize_escpos_stream(stream: bytes) -> bytes:
    """
    Normalize ESC/POS stream for the A2Y printer.

    - Keeps most commands unchanged.
    - Rewrites GS v 0 raster width to 384 px if needed.
    - Drops GS V cut commands (the device has no cutter).
    """
    out = bytearray()
    i = 0
    n = len(stream)

    while i < n:
        # GS v 0 m xL xH yL yH + data
        if i + 8 <= n and stream[i] == 0x1D and stream[i + 1] == 0x76 and stream[i + 2] == 0x30:
            mode = stream[i + 3]
            src_width_bytes = stream[i + 4] | (stream[i + 5] << 8)
            rows = stream[i + 6] | (stream[i + 7] << 8)
            data_len = src_width_bytes * rows
            payload_start = i + 8
            payload_end = payload_start + data_len
            if src_width_bytes > 0 and rows > 0 and payload_end <= n:
                payload = stream[payload_start:payload_end]
                fixed_payload = _rescale_raster(payload, src_width_bytes, rows)
                out.extend(
                    bytes(
                        [
                            0x1D,
                            0x76,
                            0x30,
                            mode,
                            TARGET_BYTES_PER_ROW & 0xFF,
                            (TARGET_BYTES_PER_ROW >> 8) & 0xFF,
                            rows & 0xFF,
                            (rows >> 8) & 0xFF,
                        ]
                    )
                )
                out.extend(fixed_payload)
                if src_width_bytes != TARGET_BYTES_PER_ROW:
                    LOG.info(
                        "Rescaled raster width from %d to %d bytes",
                        src_width_bytes,
                        TARGET_BYTES_PER_ROW,
                    )
                i = payload_end
                continue

        # GS V ... (cut) - unsupported by this printer
        if i + 3 <= n and stream[i] == 0x1D and stream[i + 1] == 0x56:
            mode = stream[i + 2]
            # GS V m and GS V m n
            if mode in (0x41, 0x42) and i + 4 <= n:
                i += 4
            else:
                i += 3
            continue

        out.append(stream[i])
        i += 1

    return bytes(out)


@dataclass
class BridgeConfig:
    address: str | None
    serial_port: str | None
    listen_host: str
    listen_port: int
    chunk_size: int = 2048


class TronicBridge:
    def __init__(self, cfg: BridgeConfig):
        self.cfg = cfg
        self.lock = threading.Lock()

    def _transport(self):
        if self.cfg.serial_port:
            return SerialTransport(self.cfg.serial_port)
        if self.cfg.address:
            return RFCOMMTransport(self.cfg.address)
        raise PrinterError("No transport selected; use --port or --address.")

    def send_job(self, raw_job: bytes) -> None:
        payload = normalize_escpos_stream(raw_job)
        if not payload:
            raise PrinterError("Empty print payload.")

        with self.lock:
            with Printer(self._transport()) as pr:
                status = pr.status()
                if status.no_paper:
                    raise PrinterError("Printer reports: no paper.")
                if status.cover_open:
                    raise PrinterError("Printer reports: cover open.")

                pr.send(CMD_ENABLE)
                time.sleep(0.15)
                pr.send(CMD_WAKEUP)
                time.sleep(0.15)

                for i in range(0, len(payload), self.cfg.chunk_size):
                    pr.send(payload[i:i + self.cfg.chunk_size])
                    time.sleep(0.01)

                pr.feed(END_LINE_DOT)
                time.sleep(0.25)
                ack = pr.ask(CMD_STOP, timeout=30.0)
                if not ack or (ack[0] != 0xAA and not ack.startswith(b"OK")):
                    raise PrinterError(f"No valid stop ack from printer: {ack!r}")


class _BridgeTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, request_handler_class, bridge: TronicBridge):
        super().__init__(server_address, request_handler_class)
        self.bridge = bridge


class _RawJobHandler(socketserver.BaseRequestHandler):
    def handle(self):
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        LOG.info("Incoming connection from %s", peer)
        self.request.settimeout(2.0)
        chunks = []

        while True:
            try:
                data = self.request.recv(8192)
            except socket.timeout:
                # Raw TCP print jobs usually close connection when complete.
                # A timeout means the stream likely stalled; finish the job.
                break
            if not data:
                break
            chunks.append(data)

        raw_job = b"".join(chunks)
        LOG.info("Received %d bytes from %s", len(raw_job), peer)
        if not raw_job:
            return

        try:
            self.server.bridge.send_job(raw_job)  # type: ignore[attr-defined]
            LOG.info("Print job completed for %s", peer)
        except Exception as exc:
            LOG.error("Print job failed for %s: %s", peer, exc)


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Windows raw print bridge for Tronic Mini Pocket Printer (A2Y)."
    )
    conn = ap.add_mutually_exclusive_group(required=True)
    conn.add_argument("--port", help="Serial/COM port (recommended on Windows), e.g. COM5")
    conn.add_argument("--address", help="Bluetooth MAC address, e.g. 55:55:09:10:98:B6")
    ap.add_argument("--listen-host", default="127.0.0.1", help="Raw TCP listen host")
    ap.add_argument("--listen-port", type=int, default=9100, help="Raw TCP listen port")
    ap.add_argument("--verbose", action="store_true", help="Enable verbose logs")
    return ap


def main() -> None:
    args = build_arg_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="[%(asctime)s] %(levelname)s: %(message)s",
    )

    cfg = BridgeConfig(
        address=args.address,
        serial_port=args.port,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
    )
    bridge = TronicBridge(cfg)

    server = _BridgeTCPServer((cfg.listen_host, cfg.listen_port), _RawJobHandler, bridge)
    LOG.info("Listening on %s:%d", cfg.listen_host, cfg.listen_port)
    print(
        f"Bridge listening on {cfg.listen_host}:{cfg.listen_port} "
        f"-> {'COM ' + cfg.serial_port if cfg.serial_port else cfg.address}"
    )
    print("Keep this terminal open while printing.")
    server.serve_forever()


if __name__ == "__main__":
    main()
