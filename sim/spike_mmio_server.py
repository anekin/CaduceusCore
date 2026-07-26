#!/usr/bin/env python3
"""
Spike MMIO Server — Unix domain socket bridge between the Spike RISC-V simulator
and the Python FuncModel MMIOBridge / CrossbarModel.

The Spike plugin sends simple text commands:

    R 0xADDR\n       -> MMIOBridge.handle('read',  addr, 0)     (MMIO addresses)
    W 0xADDR 0xVAL\n -> MMIOBridge.handle('write', addr, value)  (MMIO addresses)

MMIO addresses (0x40000000+) are passed through the MMIOBridge unchanged.
Non-MMIO addresses (SRAM at 0x20000000–0x203FFFFF, DRAM at 0x80000000+) are
routed through CrossbarModel on behalf of MASTER_IBEX (the RISC-V core).
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

from sim.func_model import FuncModel
from sim.mmio_bridge import MMIOBridge
from sim.models.crossbar import CrossbarModel


DEFAULT_SOCK_PATH = "/tmp/npu_mmio.sock"

# Firmware SRAM is 4 MB starting at 0x20000000.
SRAM_FIRMWARE_BASE = 0x20000000
SRAM_FIRMWARE_SIZE = 4 * 1024 * 1024
SRAM_FIRMWARE_END = SRAM_FIRMWARE_BASE + SRAM_FIRMWARE_SIZE - 1

# Simple protocol tokenizer: R/W followed by one or two hex integers.
_REQUEST_RE = re.compile(r"^(R|W)\s+(0x[0-9A-Fa-f]+)(?:\s+(0x[0-9A-Fa-f]+))?\s*$")


def _handle_request(bridge: MMIOBridge, crossbar, line: str) -> str:
    """Parse one request line and dispatch to MMIOBridge or CrossbarModel.

    MMIO addresses (0x4000_0000–0x7FFF_FFFF) are routed through the MMIOBridge.
    Non-MMIO addresses (SRAM at 0x2000_0000–0x203F_FFFF, DRAM at 0x8000_0000+)
    are routed through the CrossbarModel on behalf of the Ibex RISC-V core
    (MASTER_IBEX).  When *crossbar* is None, non-MMIO requests return an
    explicit error because the old SRAM-offset fallback path has been removed.
    """
    match = _REQUEST_RE.match(line)
    if not match:
        return "ERR invalid request\n"

    op, addr_str, value_str = match.groups()
    addr = int(addr_str, 16)

    # MMIO window: keep the existing bridge.handle path.
    if 0x4000_0000 <= addr < 0x8000_0000:
        if op == "R":
            value = bridge.handle("read", addr, 0)
            return f"0x{value:08X}\n"
        # op == "W"
        if value_str is None:
            return "ERR write missing value\n"
        value = int(value_str, 16)
        bridge.handle("write", addr, value)
        return "OK\n"

    # Non-MMIO: SRAM, DRAM — route through the CrossbarModel.
    if crossbar is None:
        return "ERR crossbar required for SRAM/DRAM access\n"

    if op == "R":
        data = crossbar.read(CrossbarModel.MASTER_IBEX, addr, 4)
        # Pad with zero bytes on the right if the target returns fewer than
        # 4 bytes (e.g. a partial read at the end of a memory region).
        if len(data) < 4:
            data = data + b"\x00" * (4 - len(data))
        value = int.from_bytes(data, "little")
        return f"0x{value:08X}\n"

    # op == "W"
    if value_str is None:
        return "ERR write missing value\n"
    value = int(value_str, 16)
    crossbar.write(CrossbarModel.MASTER_IBEX, addr, value.to_bytes(4, "little"))
    return "OK\n"


class _MMIORequestHandler(socketserver.BaseRequestHandler):
    """One instance per client connection; handles the text MMIO protocol."""

    def handle(self) -> None:
        bridge: MMIOBridge = self.server.bridge
        crossbar = self.server.crossbar
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

                response = _handle_request(bridge, crossbar, line)
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
        crossbar=None,
        ready_event: Optional[threading.Event] = None,
    ):
        self.sock_path = sock_path
        self.bridge = bridge
        self.crossbar = crossbar
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
    crossbar=None,
    ready_event: Optional[threading.Event] = None,
    register_signals: bool = True,
) -> ThreadedUnixMMIOServer:
    """Start a threaded Unix socket MMIO server around *bridge*.

    Returns the server instance so callers can call ``shutdown()`` later.
    If *ready_event* is provided, it is set once the server begins its accept
    loop, which is useful when launching the server from a host adapter thread.
    """
    server = ThreadedUnixMMIOServer(sock_path, bridge, crossbar=crossbar, ready_event=ready_event)

    if register_signals:
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

    server = serve(model.bridge, sock_path, crossbar=model.crossbar)
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
