"""spike_rtl_bridge.py — Spike MMIO bridge for RTL SoC verification.

Routes Spike RISC-V firmware loads/stores to real RTL bus transactions:

  - SRAM/DRAM data accesses (0x2000_0000+, 0x8000_0000+) → AXI4 via cocotbext-axi
  - NPU MMIO register accesses (0x4000_0000+)           → APB master

The Unix socket server runs in a cocotb.external thread so that
bridge.handle() (decorated with @cocotb.function) executes in the cocotb
simulation context and the server thread blocks until the RTL transaction
completes.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import socketserver
import sys
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import cocotb
except Exception:
    cocotb = None

_SCRIPT_DIR = Path(__file__).resolve().parent
_PACKAGE_ROOT = _SCRIPT_DIR.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from regmap import Addr


DEFAULT_SOCK_PATH = "/tmp/npu_mmio_rtl.sock"
_REQUEST_RE = re.compile(r"^(R|W)\s+(0x[0-9A-Fa-f]+)(?:\s+(0x[0-9A-Fa-f]+))?\s*$")

SRAM_BASE = Addr.SRAM_BASE
DRAM_BASE = Addr.DRAM_BASE
MMIO_BASE = Addr.MXU_BASE


class SimpleAPBMaster:
    """Bit-banged APB master driven from a cocotb coroutine."""

    def __init__(self, dut, prefix: str = "cpu_apb"):
        self._dut = dut
        self._prefix = prefix

    def _sig(self, name: str):
        return getattr(self._dut, f"{self._prefix}_{name}")

    async def write(self, addr: int, data: int, strobe: int = 0xF) -> None:
        from cocotb.triggers import RisingEdge
        psel = self._sig("psel")
        penable = self._sig("penable")
        pwrite = self._sig("pwrite")
        paddr = self._sig("paddr")
        pwdata = self._sig("pwdata")
        pready = self._sig("pready")

        paddr.value = addr & 0xFFFFFFFF
        pwdata.value = data & 0xFFFFFFFF
        pwrite.value = 1
        psel.value = 1
        penable.value = 0
        await RisingEdge(self._dut.clk)
        penable.value = 1
        while True:
            await RisingEdge(self._dut.clk)
            if int(pready.value):
                break
        psel.value = 0
        penable.value = 0
        pwrite.value = 0

    async def read(self, addr: int) -> int:
        from cocotb.triggers import RisingEdge
        psel = self._sig("psel")
        penable = self._sig("penable")
        pwrite = self._sig("pwrite")
        paddr = self._sig("paddr")
        prdata = self._sig("prdata")
        pready = self._sig("pready")

        paddr.value = addr & 0xFFFFFFFF
        pwrite.value = 0
        psel.value = 1
        penable.value = 0
        await RisingEdge(self._dut.clk)
        penable.value = 1
        while True:
            await RisingEdge(self._dut.clk)
            if int(pready.value):
                break
        data = int(prdata.value)
        psel.value = 0
        penable.value = 0
        return data & 0xFFFFFFFF


class RTLMMIOBridge:
    """Bridge from Spike text MMIO protocol to RTL AXI4/APB transactions.

    Requests from the socket server thread are placed on a queue and drained
    by a background cocotb task so that RTL transactions run in the simulator
    context.
    """

    def __init__(self, axi_master, apb_master, dut):
        self.axi = axi_master
        self.apb = apb_master
        self.dut = dut
        self._status: dict = {}
        self._req_queue: queue.Queue = queue.Queue()

    async def _process_requests(self) -> None:
        """Background cocotb task that drains the MMIO request queue."""
        from cocotb.triggers import ClockCycles
        while True:
            await ClockCycles(self.dut.clk, 1)
            while not self._req_queue.empty():
                op, addr, value, evt, result = self._req_queue.get_nowait()
                try:
                    result[0] = await self._handle_async(op, addr, value)
                except Exception as exc:
                    result[0] = exc
                evt.set()

    @staticmethod
    def _abs_addr(addr: int) -> int:
        if SRAM_BASE <= addr <= SRAM_BASE + Addr.SRAM_SIZE - 1:
            return addr
        if addr < SRAM_BASE:
            return SRAM_BASE + addr
        return addr

    async def _handle_async(self, op: str, addr: int, value: int) -> int:
        addr = self._abs_addr(addr)

        if MMIO_BASE <= addr < 0x5000_0000:
            if op == "read":
                data = await self.apb.read(addr)
                self._status[addr] = data
                return data
            else:
                await self.apb.write(addr, value)
                self._status[addr] = value & 0xFFFFFFFF
                return 0

        if op == "read":
            data = await self.axi.read(addr, 4)
            return int.from_bytes(data, "little") & 0xFFFFFFFF
        else:
            data = value.to_bytes(4, "little")
            await self.axi.write(addr, data)
            self._status[addr] = value & 0xFFFFFFFF
            return 0

    def handle(self, op: str, addr: int, value: int = 0) -> int:
        """Synchronous entry point called from the server thread."""
        result = [None]
        evt = threading.Event()
        self._req_queue.put((op, addr, value, evt, result))
        evt.wait()
        if isinstance(result[0], Exception):
            raise result[0]
        return result[0]


def _handle_request(bridge: RTLMMIOBridge, line: str) -> str:
    match = _REQUEST_RE.match(line)
    if not match:
        return "ERR invalid request\n"
    op, addr_str, value_str = match.groups()
    addr = int(addr_str, 16)
    if op == "R":
        value = bridge.handle("read", addr, 0)
        return f"0x{value:08X}\n"
    if value_str is None:
        return "ERR write missing value\n"
    value = int(value_str, 16)
    bridge.handle("write", addr, value)
    return "OK\n"


class _RTLRequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        bridge: RTLMMIOBridge = self.server.bridge
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


class ThreadedRTLServer(socketserver.ThreadingUnixStreamServer):
    allow_reuse_address = True

    def __init__(self, sock_path: str, bridge: RTLMMIOBridge,
                 ready_event: Optional[threading.Event] = None):
        self.sock_path = sock_path
        self.bridge = bridge
        self.ready_event = ready_event
        try:
            os.unlink(sock_path)
        except FileNotFoundError:
            pass
        super().__init__(sock_path, _RTLRequestHandler)

    def service_actions(self) -> None:
        if self.ready_event is not None:
            self.ready_event.set()
        super().service_actions()


def serve_rtl(
    bridge: RTLMMIOBridge,
    sock_path: str = DEFAULT_SOCK_PATH,
    ready_event: Optional[threading.Event] = None,
):
    """Start a threaded Unix socket server around *bridge* and a request processor."""
    server = ThreadedRTLServer(sock_path, bridge, ready_event=ready_event)
    if cocotb is not None:
        cocotb.start_soon(bridge._process_requests())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
