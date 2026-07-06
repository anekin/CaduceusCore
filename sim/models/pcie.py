"""PCIe Endpoint functional model — TLP builder/parser + BAR routing."""

import struct
from dataclasses import dataclass
from typing import Tuple

from models.crossbar import CrossbarModel


@dataclass
class PCIeState:
    """PCIe EP register state (mirrors pcie_ep_wrapper APB registers)."""

    completer_id: int = 0x0001
    max_payload_size: int = 3  # 3 = 512 bytes per PCIe spec encoding
    msix_enable: bool = False
    msix_vector: int = 0
    irq_enable: bool = False
    irq_pending: bool = False
    bar0_base: int = 0x2000_0000
    bar0_mask: int = 0x003F_FFFF  # 4 MB
    bar1_base: int = 0x8000_0000
    bar1_mask: int = 0x7FFF_FFFF  # 2 GB


class PCIeModel:
    """PCIe EP functional model: TLP parser/builder + crossbar routing.

    Host-facing TLP read/write requests are routed through the shared
    CrossbarModel using master ID MASTER_PCIE (5).

    References:
        rtl/ip/pcie_ep_wrapper.v — TLP port mapping, BAR layout, APB registers
        rtl/ip/pcie_ep_tb.sv    — TLP header format (Fmt+Type, 3-DW)
    """

    def __init__(
        self,
        crossbar: CrossbarModel,
        bar0_base: int = 0x2000_0000,
        bar1_base: int = 0x8000_0000,
    ):
        self.crossbar = crossbar
        self.bar0_base = bar0_base
        self.bar1_base = bar1_base
        self.state = PCIeState(bar0_base=bar0_base, bar1_base=bar1_base)
        self.requester_id = 0x0000
        self.tag = 0
        self.max_payload_bytes = 256
        self.last_tx_headers: list[bytes] = []
        self.last_rx_headers: list[bytes] = []

    def _next_tag(self) -> int:
        tag = self.tag
        self.tag = (self.tag + 1) & 0xFF
        return tag

    def _resolve_bar(self, addr: int) -> Tuple[bytearray, int]:
        """Map SoC physical address to (memory, offset) via BAR.

        Keeps the legacy BAR-base validation; actual access goes through
        the crossbar so decode is centralized.

        addr < bar1_base -> BAR0/SRAM
        addr >= bar1_base -> BAR1/DRAM
        """
        if self.bar0_base <= addr < self.bar0_base + len(self.crossbar.sram):
            return self.crossbar.sram, addr - self.bar0_base
        if self.bar1_base <= addr < self.bar1_base + len(self.crossbar.dram):
            return self.crossbar.dram, addr - self.bar1_base
        raise ValueError(f"Address 0x{addr:08x} out of BAR range")

    def _build_memwr_header(self, addr: int, length: int) -> bytes:
        """Build 3-DW Memory Write TLP header (12 bytes, network byte order).

        DW0: [31:24] = {Fmt=010, Type=00000} = 0x40, [9:0] = length (DWs)
        DW1: [31:16] = requester_id, [15:8] = tag
        DW2: [31:2]  = address[31:2]
        """
        if length <= 0 or length > 1024:
            raise ValueError(f"TLP length {length} out of range")
        dw0 = (0x40 << 24) | (length & 0x3FF)
        dw1 = (self.requester_id << 16) | (self._next_tag() << 8)
        dw2 = addr & 0xFFFFFFFC
        return struct.pack(">III", dw0, dw1, dw2)

    def _build_memrd_header(self, addr: int, length: int) -> bytes:
        """Build 3-DW Memory Read TLP header (12 bytes, network byte order).

        DW0: [31:24] = {Fmt=000, Type=00000} = 0x00, [9:0] = length (DWs)
        DW1: [31:16] = requester_id, [15:8] = tag
        DW2: [31:2]  = address[31:2]
        """
        if length <= 0 or length > 1024:
            raise ValueError(f"TLP length {length} out of range")
        dw0 = (0x00 << 24) | (length & 0x3FF)
        dw1 = (self.requester_id << 16) | (self._next_tag() << 8)
        dw2 = addr & 0xFFFFFFFC
        return struct.pack(">III", dw0, dw1, dw2)

    def _parse_completion_header(self, header: bytes) -> int:
        """Parse 3-DW Completion TLP header and return length in bytes."""
        if len(header) != 12:
            raise ValueError("Completion header must be 12 bytes")
        dw0, _, _ = struct.unpack(">III", header)
        length_dw = dw0 & 0x3FF
        return length_dw * 4

    def _split_payload(self, data: bytes, chunk_size: int) -> list[bytes]:
        """Split payload into chunks that fit into a single TLP."""
        return [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]

    def tlp_write(self, addr: int, data: bytes) -> None:
        """Host issues PCIe Memory Write TLP(s) to NPU address space."""
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes")
        # Validate BAR range; crossbar will re-validate address decode.
        self._resolve_bar(addr)
        chunks = self._split_payload(data, self.max_payload_bytes)
        self.last_tx_headers = []
        cur_addr = addr
        for chunk in chunks:
            length_dw = (len(chunk) + 3) // 4
            header = self._build_memwr_header(cur_addr, length_dw)
            self.last_tx_headers.append(header)
            padded = chunk + b"\x00" * (length_dw * 4 - len(chunk))
            self.crossbar.write(CrossbarModel.MASTER_PCIE, cur_addr, padded)
            cur_addr += len(padded)

    def tlp_read(self, addr: int, size: int) -> bytes:
        """Host issues PCIe Memory Read TLP(s) and returns read data."""
        if size < 0:
            raise ValueError("size must be non-negative")
        if size == 0:
            return b""
        self._resolve_bar(addr)
        result = bytearray()
        self.last_rx_headers = []
        cur_addr = addr
        remaining = size
        while remaining > 0:
            chunk_size = min(remaining, self.max_payload_bytes)
            length_dw = (chunk_size + 3) // 4
            header = self._build_memrd_header(cur_addr, length_dw)
            self.last_rx_headers.append(header)
            cpl_header = self._build_completion_header(length_dw)
            self._parse_completion_header(cpl_header)
            data = self.crossbar.read(
                CrossbarModel.MASTER_PCIE, cur_addr, length_dw * 4
            )
            result.extend(data[:chunk_size])
            cur_addr += length_dw * 4
            remaining -= chunk_size
        return bytes(result)

    def _build_completion_header(self, length_dw: int) -> bytes:
        """Build a 3-DW Completion TLP header (Fmt=010, Type=01010)."""
        dw0 = (0x4A << 24) | (length_dw & 0x3FF)
        dw1 = (self.requester_id << 16) | (self.state.completer_id & 0xFFFF)
        dw2 = 0x0000_0000
        return struct.pack(">III", dw0, dw1, dw2)

    def send_msi(self, vector: int = 0) -> None:
        """Send MSI-X interrupt message to host.

        In Func Model, this sets a flag that host test harness polls.
        """
        if not 0 <= vector <= 7:
            raise ValueError("MSI-X vector must be 0-7")
        self.state.msix_enable = True
        self.state.msix_vector = vector
        self.state.irq_pending = True
