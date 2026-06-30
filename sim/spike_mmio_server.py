#!/usr/bin/env python3
"""
Spike MMIO Server — Unix domain socket bridge between the Spike RISC-V simulator
and the Python FuncModel MMIOBridge.

The Spike plugin sends simple text commands:

    R 0xADDR\n       -> MMIOBridge.handle('read',  addr, 0)
    W 0xADDR 0xVAL\n -> MMIOBridge.handle('write', addr, value)

Addresses in the firmware SRAM range (0x20000000-0x203FFFFF) are normalized to
Python SRAM offsets by subtracting 0x20000000.  NPU register addresses
(0x40000000+) are passed through unchanged because sim/regmap.py already uses
those absolute bases.
"""

import os
import re
import signal
import socket
import socketserver
import sys
import threading
from typing import Optional

# Allow the script to be run directly as `python3 sim/spike_mmio_server.py`
# while still importing sibling modules under the `sim` package.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PACKAGE_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

from func_model import FuncModel
from mmio_bridge import MMIOBridge


DEFAULT_SOCK_PATH = "/tmp/npu_mmio.sock"

# Firmware SRAM is 4 MB starting at 0x20000000.
SRAM_FIRMWARE_BASE = 0x20000000
SRAM_FIRMWARE_SIZE = 4 * 1024 * 1024
SRAM_FIRMWARE_END = SRAM_FIRMWARE_BASE + SRAM_FIRMWARE_SIZE - 1

# Simple protocol tokenizer: R/W followed by one or two hex integers.
_REQUEST_RE = re.compile(r"^(R|W)\s+(0x[0-9A-Fa-f]+)(?:\s+(0x[0-9A-Fa-f]+))?\s*$")


def _normalize_addr(addr: int) -> int:
    """Convert firmware absolute SRAM addresses to bridge-local offsets."""
    if SRAM_FIRMWARE_BASE <= addr <= SRAM_FIRMWARE_END:
        return addr - SRAM_FIRMWARE_BASE
    return addr


def _handle_request(bridge: MMIOBridge, line: str) -> str:
    """Parse one request line and dispatch to the MMIOBridge."""
    match = _REQUEST_RE.match(line)
    if not match:
        return "ERR invalid request\n"

    op, addr_str, value_str = match.groups()
    addr = int(addr_str, 16)
    addr = _normalize_addr(addr)

    if op == "R":
        value = bridge.handle("read", addr, 0)
        return f"0x{value:08X}\n"

    # op == "W"
    if value_str is None:
        return "ERR write missing value\n"
    value = int(value_str, 16)
    bridge.handle("write", addr, value)
    return "OK\n"


class _MMIORequestHandler(socketserver.BaseRequestHandler):
    """One instance per client connection; handles the text MMIO protocol."""

    def handle(self) -> None:
        bridge: MMIOBridge = self.server.bridge
        file = self.request.makefile("rwb", buffering=0)
        try:
            while True:
                line_bytes = file.readline()
                if not line_bytes:
                    break
                try:
                    line = line_bytes.decode("ascii")
                except UnicodeDecodeError:
                    file.write(b"ERR bad encoding\n")
                    continue

                response = _handle_request(bridge, line)
                file.write(response.encode("ascii"))
        finally:
            file.close()


class ThreadedUnixMMIOServer(socketserver.ThreadingUnixStreamServer):
    """Thread-per-connection Unix socket server carrying a reference bridge."""

    allow_reuse_address = True

    def __init__(
        self,
        sock_path: str,
        bridge: MMIOBridge,
        ready_event: Optional[threading.Event] = None,
    ):
        self.sock_path = sock_path
        self.bridge = bridge
        self.ready_event = ready_event
        # Remove stale socket file before binding.
        try:
            os.unlink(sock_path)
        except FileNotFoundError:
            pass
        super().__init__(sock_path, _MMIORequestHandler)

    def service_actions(self) -> None:
        """Signal listeners that the server is accepting connections."""
        if self.ready_event is not None:
            self.ready_event.set()
        super().service_actions()


def serve(
    bridge: MMIOBridge,
    sock_path: str = DEFAULT_SOCK_PATH,
    ready_event: Optional[threading.Event] = None,
) -> ThreadedUnixMMIOServer:
    """Start a threaded Unix socket MMIO server around *bridge*.

    Returns the server instance so callers can call ``shutdown()`` later.
    If *ready_event* is provided, it is set once the server begins its accept
    loop, which is useful when launching the server from a host adapter thread.
    """
    server = ThreadedUnixMMIOServer(sock_path, bridge, ready_event=ready_event)

    def _shutdown_handler(signum, frame):
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main(sock_path: str = DEFAULT_SOCK_PATH) -> int:
    """Create a FuncModel and run the MMIO server on a Unix socket."""
    model = FuncModel()
    print(f"Spike MMIO server: FuncModel ready, listening on {sock_path}", flush=True)

    server = serve(model.bridge, sock_path)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        try:
            os.unlink(sock_path)
        except FileNotFoundError:
            pass
        print("Spike MMIO server: shut down", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
