# SoC Func Model Gap Specification

**Date:** 2026-07-03  
**Status:** Pre-implementation spec (blocks Todos 3–8 of `soc-func-model-verify`)  
**Audit basis:** `.omo/evidence/task-1-ip-audit.md`

---

## Overview

The current Python Func Model at `CaduceusCore/sim/` provides bit-exact golden reference for the four compute engines (MXU, SFU, Vector, DMA) and performance/timing models for DMA, NoC, and DRAM. However, 6 SoC-level data paths have **no functional/behavioral Python model**. This document enumerates each gap — current state, target state, API design, and testability plan — and defines the 4 new cross-module Python interfaces that must be created to close the gaps.

### Gap numbering

Gap numbers match the original audit and plan:
| # | Path ID | Name | Current FM Status |
|---|---------|------|:---:|
| 7 | `PCIE-TLP` | PCIe TLP → AXI → SRAM/DRAM | ❌ Missing |
| 8 | `XBAR-ARB` | AXI4 Crossbar M=6/S=2 + APB Decoder | ❌ Missing |
| 1 | `APB-MMIO` | Ibex → APB Decoder → MMIO Engine Register R/W | ⚠️ Partial |
| 2 | `IBEX-AXI` | Ibex → AXI Crossbar → SRAM/DRAM Data Access | ⚠️ Partial |
| 9 | `IRQ-CHAIN` | Engine IRQ → INTC → CPU WFI Wakeup | ⚠️ Partial |
| 11 | `IBEX-FIRMWARE` | RISC-V Firmware: Boot → DMEM → MMIO → Poll IRQ | ⚠️ Partial |

---

## Gap #7 — PCIe TLP Functional Model (`PCIE-TLP`)

### Current State (`func_model.py:46–93`)

`FuncModel` has three `host_write_*` methods that write **directly** to DRAM via `_dram_write()`, bypassing any PCIe model:

```python
def host_write_command(self, opcode, desc_addr, flags=0):
    # Direct struct.pack + _dram_write to ring buffer

def host_write_descriptor(self, desc_addr, **kwargs):
    # Direct struct.pack('<15I', ...) + _dram_write

def host_write_data(self, addr, data: np.ndarray):
    # Direct data.tobytes() + _dram_write
```

No TLP parser, no BAR address translation, no PCIe EP register state.

### Target State

A `PCIeModel` class (`sim/models/pcie.py`) that:
1. Exposes `tlp_write(addr, data)` and `tlp_read(addr, size) -> bytes` as the canonical host-to-NPU API
2. Internally builds PCIe Memory Write/Read TLP headers (3-DW format, 32-bit addressing)
3. Routes TLPs through BAR mapping: BAR0 → SRAM (`0x2000_0000`), BAR1 → DRAM (`0x8000_0000`)
4. Models MSI-X interrupt generation for host notification
5. Replaces all three `host_write_*` methods in FuncModel

### API Design

```python
# sim/models/pcie.py

class PCIeModel:
    """PCIe EP functional model: TLP parser/builder + BAR routing.

    References:
        rtl/ip/pcie_ep_wrapper.v — TLP port mapping, BAR layout, APB registers
        rtl/ip/pcie_ep_tb.sv    — TLP header format (Fmt+Type, 3-DW for MemWr/MemRd)
        rtl/ip/verilog-pcie/pcie_tlp_demux_wrap.py — BAR matching logic
    """

    # ── Constructor ─────────────────────────────────────────────────
    def __init__(
        self,
        sram: bytearray,
        dram: bytearray,
        bar0_base: int = 0x2000_0000,   # BAR0 → SRAM
        bar1_base: int = 0x8000_0000,   # BAR1 → DRAM
    ):
        """Initialize PCIe model with shared memory references.

        Args:
            sram:  FuncModel.sram bytearray (4 MB)
            dram:  FuncModel.dram bytearray (64 MB default)
            bar0_base: Physical base address of BAR0 SRAM window
            bar1_base: Physical base address of BAR1 DRAM window
        """

    # ── Host-facing API (replaces host_write_*) ──────────────────────
    def tlp_write(self, addr: int, data: bytes) -> None:
        """Host issues PCIe Memory Write TLP to NPU address space.

        Constructs a 3-DW TLP header (Fmt=0x40, Type=0x00 for 32-bit MemWr),
        resolves BAR (addr < bar1_base → BAR0/SRAM else BAR1/DRAM),
        translates address to bytearray offset, writes data.

        Args:
            addr: SoC physical address (0x2000_XXXX for SRAM, 0x8000_XXXX for DRAM)
            data: Raw bytes payload (max 1024 bytes per TLP per PCIe spec;
                  larger payloads are split into multiple TLPs)
        """

    def tlp_read(self, addr: int, size: int) -> bytes:
        """Host issues PCIe Memory Read TLP to NPU address space.

        Constructs 3-DW TLP header (Fmt=0x00, Type=0x00 for 32-bit MemRd),
        resolves BAR, reads from bytearray, returns data.

        Args:
            addr: SoC physical address
            size: Number of bytes to read (max 1024 per TLP)

        Returns:
            Raw bytes read from the target memory
        """

    # ── TLP Header Builder (internal) ────────────────────────────────
    def _build_memwr_header(self, addr: int, length: int) -> bytes:
        """Build 3-DW Memory Write TLP header (12 bytes).

        DW0: [5:0]=Fmt(0x02 for 3-DW,no-data), [12:6]=Type(0x00=MemWr),
             [15:13]=TC(0), [28:24]=Length(32-bit words)
        DW1: [15:0]=Requester ID, [23:16]=Tag
        DW2: [31:2]=Address[31:2]
        Returns: 12-byte header bytes (network byte order)
        """

    def _build_memrd_header(self, addr: int, length: int) -> bytes:
        """Build 3-DW Memory Read TLP header (12 bytes).

        DW0: Fmt=0x00, Type=0x00 for 3-DW MemRd without data
        DW1: Requester ID + Tag
        DW2: Address[31:2]
        """

    # ── MSI-X Interrupt Generator ────────────────────────────────────
    def send_msi(self, vector: int = 0) -> None:
        """Send MSI-X interrupt message to host.

        In Func Model, this sets a flag that host test harness polls.
        In hardware, generates a PCIe Message TLP to the Root Complex.

        Args:
            vector: MSI-X vector number (0–7, maps to INTC source bit 5)
        """

    # ── BAR Resolution ───────────────────────────────────────────────
    def _resolve_bar(self, addr: int) -> tuple[bytearray, int]:
        """Map SoC physical address to (memory, offset) via BAR.

        addr < bar1_base → (sram, addr - bar0_base)
        addr >= bar1_base → (dram, addr - bar1_base)

        Returns:
            (bytearray target, int offset within target)
        """

# ── Module state ─────────────────────────────────────────────────────
class PCIeState:
    """PCIe EP register state (mirrors pcie_ep_wrapper APB registers)."""
    completer_id: int = 0x0001           # Bus:Dev.Func = 00:00.1
    max_payload_size: int = 3            # 3 = 512 bytes
    msix_enable: bool = False
    msix_vector: int = 0
    irq_enable: bool = False
    irq_pending: bool = False
    bar0_base: int = 0x2000_0000
    bar0_mask: int = 0x003F_FFFF        # 4 MB
    bar1_base: int = 0x8000_0000
    bar1_mask: int = 0x7FFF_FFFF        # 2 GB
```

### Integration into FuncModel

```python
# func_model.py — modified __init__
def __init__(self, dram_mb: int = 64, sram_kb: int = 512):
    self.dram = bytearray(dram_mb * 1024 * 1024)
    self.sram = bytearray(sram_kb * 1024)

    # NEW: PCIe model wraps host-to-NPU path
    self.pcie = PCIeModel(sram=self.sram, dram=self.dram)

    # Existing host_write_* become convenience wrappers:
    def host_write_data(self, addr, data):
        self.pcie.tlp_write(addr, data.tobytes())

    def host_write_command(self, opcode, desc_addr, flags=0):
        buf = struct.pack('<IQI8x', opcode, desc_addr, flags)
        self.pcie.tlp_write(
            self.firmware.ring_buffer_addr + head * 32, buf)
```

### Testability Plan

| Test | Function | Description |
|------|----------|-------------|
| TLP write roundtrip | `test_pcie_tlp_smoke()` | Write 4KB via `tlp_write`, read back via `tlp_read`, verify bit-exact |
| BAR routing | `test_pcie_bar_routing()` | Write to SRAM addr, verify DRAM untouched; write to DRAM addr, verify SRAM untouched |
| TLP header format | `test_pcie_tlp_header()` | Construct TLP, verify DW0–DW2 fields match PCIe spec |
| Anti-vacuous | `test_pcie_corrupted()` | Corrupt TLP payload → MISMATCH detected |
| Multi-TLP split | `test_pcie_large_payload()` | Write 4KB payload → split into 4 × 1024B TLPs, verify |

### Open-Source Verilog References

- **TLP header format**: `rtl/ip/pcie_ep_tb.sv` (3-DW Fmt+Type, Length, ReqID, Tag, Address)
- **BAR mapping**: `rtl/ip/pcie_ep_wrapper.v:13–15, 143–149`
- **TLP→AXI translation**: `rtl/ip/verilog-pcie/pcie_axi_master.v`
- **BAR demux logic**: `rtl/ip/verilog-pcie/pcie_tlp_demux_wrap.py`

---

## Gap #8 — AXI4 Crossbar + APB Decoder Functional Model (`XBAR-ARB`)

### Current State (`mmio_bridge.py:29–335`)

`MMIOBridge._handle_*` methods access `self.modules['sram']` and `self.modules['dram']` **directly** via bytearray indexing:

```python
# _handle_mxu (line 77–104):
if sram and M > 0 and K > 0 and N > 0:
    act = np.frombuffer(sram[i_addr:i_addr + act_bytes], ...)
    # ... compute ...
    sram[o_addr:o_addr + len(result_bytes)] = result_bytes

# _handle_dma (line 245–288):
def _handle_dma(self, ...):
    src_mem = self._get_mem(ch0_src)  # → self.modules['dram'] or ['sram']
    dst_mem = self._get_mem(ch0_dst)
    dst_mem[dst_off:dst_off + ch0_size] = src_mem[src_off:src_off + ch0_size]
```

No crossbar arbitration, no address routing through shared bus, no APB decoder protocol.

### Target State

A `CrossbarModel` class (`sim/models/crossbar.py`) that:
1. Provides `read(addr, size) -> bytes` and `write(addr, data) -> bytes` as the canonical bus access API
2. Routes addresses: `0x0000_0000–0x0001_FFFF` → boot_rom/dmem (Ibex local), `0x2000_0000–0x203F_FFFF` → SRAM, `0x8000_0000–0xFFFF_FFFF` → DRAM
3. Implements round-robin arbitration across 6 master ports, independent for AW and AR channels
4. Preserves per-master AXI ID in response routing (`rid/bid = {master_sel, axi_id}`)
5. Injects DECERR for unmapped addresses

An `APBDecoder` class that decodes APB addresses to 7 slave selects matching `apb_decoder.v`.

### API Design

```python
# sim/models/crossbar.py

class CrossbarModel:
    """AXI4 Crossbar functional model — M=6 masters, S=2 slaves.

    Models address decode and round-robin arbitration. Does NOT model
    channel-level timing (awvalid/awready handshake cycles).

    Address routing (matches axi_crossbar.v:117–129):
        SRAM: addr[31:22] == 0b0010000000 → slave 0
        DRAM: addr[31] == 1                → slave 1
        Other                               → DECERR

    References:
        rtl/soc/axi_crossbar.v:1–587        — RTL crossbar with round-robin
        rtl/soc/README.md:29–61             — crossbar topology and arbitration
        sim/config/interconnect.yaml        — crossbar configuration
    """

    MASTER_IBEX  = 0
    MASTER_MXU   = 1
    MASTER_SFU   = 2
    MASTER_VEC   = 3
    MASTER_DMA   = 4
    MASTER_PCIE  = 5

    # ── Constructor ─────────────────────────────────────────────────
    def __init__(
        self,
        sram: bytearray,
        dram: bytearray,
        boot_rom: Optional[bytearray] = None,
    ):
        """Initialize crossbar with shared memory references.

        Args:
            sram:       FuncModel.sram bytearray (4 MB at 0x2000_0000)
            dram:       FuncModel.dram bytearray (at 0x8000_0000)
            boot_rom:   Optional ROM bytearray (64 KB at 0x0000_0000,
                        routed to master MASTER_IBEX only)
        """

    # ── Master-facing API ───────────────────────────────────────────
    def read(self, master_id: int, addr: int, size: int) -> bytes:
        """Issue an AXI4 read from a specific master.

        Decodes address to slave (SRAM/DRAM), reads from underlying
        bytearray, returns data. Master ID is used for arbitration
        tracking and potential future contention modeling.

        Args:
            master_id: Master port index (0=Ibex, 1=MXU, ..., 5=PCIe)
            addr:      SoC physical byte address (32-bit)
            size:      Number of bytes to read (1–4096)

        Returns:
            Bytes read from decoded target. Returns zero-filled bytes
            for DECERR addresses.

        Raises:
            ValueError: If master_id ∉ [0, 5]
        """

    def write(self, master_id: int, addr: int, data: bytes) -> None:
        """Issue an AXI4 write from a specific master.

        Decodes address, resolves to SRAM or DRAM, writes data.

        Args:
            master_id: Master port index (0–5)
            addr:      SoC physical byte address
            data:      Raw bytes to write

        Raises:
            ValueError: If master_id ∉ [0, 5] or address is DECERR
                        (with silent drop option for non-critical paths)
        """

    # ── Address decode (internal) ────────────────────────────────────
    def _decode(self, addr: int) -> tuple[int, bytearray]:
        """Decode physical address to (slave_idx, memory).

        Returns:
            (0, sram)  for 0x2000_0000–0x203F_FFFF
            (1, dram)  for 0x8000_0000–0xFFFF_FFFF

        Raises:
            ValueError:  Address unmapped (DECERR)
        """

    # ── Arbitration tracking ─────────────────────────────────────────
    def _grant(self, slave_idx: int, master_id: int) -> bool:
        """Round-robin arbitration grant for a given slave.

        Tracks per-slave last granted master; next master in round-robin
        order gets grant. For the functional model, this is a check that
        all masters eventually get service (fairness verification).

        Returns:
            True if this master has the grant (always true in current
            functional model — cycle-level contention deferred to later)
        """

# ── APB Decoder ──────────────────────────────────────────────────────

class APBDecoder:
    """APB address decoder — 1 master → 7 slaves, 4 KB windows.

    Matches apb_decoder.v:23–60 psel/paddr decode logic.

    Slave mapping:
        slave0 = MXU       0x4000_0000–0x4000_0FFF
        slave1 = SFU       0x4000_1000–0x4000_1FFF
        slave2 = VECTOR    0x4000_2000–0x4000_2FFF
        slave3 = DMA       0x4000_3000–0x4000_3FFF
        slave4 = PCIe      0x4000_4000–0x4000_4FFF
        slave5 = DOORBELL  0x4000_5000–0x4000_5FFF
        slave6 = INTC      0x4000_6000–0x4000_6FFF

    References:
        rtl/soc/apb_decoder.v:1–133  — RTL APB decoder
        sim/regmap.py:16–28          — Addr base addresses
    """

    def __init__(self):
        """Initialize APB decoder with slave address table."""

    def decode(self, paddr: int) -> int:
        """Decode APB address to slave index (0–6).

        Args:
            paddr: 32-bit APB address (paddr[15:12] selects slave)

        Returns:
            Slave index 0–6

        Raises:
            ValueError:  If paddr is out of MMIO range (0x4000_0000–0x4000_6FFF)
        """

    def get_slave_name(self, slave_idx: int) -> str:
        """Return human-readable slave name for debug."""

    @property
    def slave_map(self) -> dict[int, tuple[int, int]]:
        """Return {idx: (base, size)} for all 7 slaves.
        Useful for MMIOBridge to validate register access ranges.
        """
```

### Integration into FuncModel and MMIOBridge

```python
# func_model.py — modified __init__
def __init__(self, dram_mb=64, sram_kb=512):
    self.dram = bytearray(dram_mb * 1024 * 1024)
    self.sram = bytearray(sram_kb * 1024)

    # NEW: Crossbar wraps shared memory access
    self.crossbar = CrossbarModel(sram=self.sram, dram=self.dram)

    # Pass crossbar to bridge so _handle_* uses crossbar.read/write
    self.bridge = MMIOBridge(modules={
        'mxu': self.mxu, 'sfu': self.sfu,
        'vector': self.vector, 'dma': self.dma_engine,
        'crossbar': self.crossbar,       # ← replaces 'dram'/'sram' refs
    })

# mmio_bridge.py — modified _handle_* methods
# Replace direct sram[off:off+len] with:
#   crossbar.read(master_id, addr, size) / crossbar.write(master_id, addr, data)
# Master IDs: MXU→1, SFU→2, Vector→3, DMA→4, PCIe→5
```

### Testability Plan

| Test | Function | Description |
|------|----------|-------------|
| Address decode | `test_crossbar_decode()` | Verify SRAM (0x2000_0000 → S0) and DRAM (0x8000_0000 → S1) routing |
| DECERR | `test_crossbar_decerr()` | Access 0x50000000 → raises ValueError |
| Concurrent access | `test_crossbar_concurrent()` | 3 masters (MXU read + DMA read + PCIe write) to different addresses → all data correct |
| APB decode | `test_apb_decoder_select()` | Verify paddr[15:12] selects correct slave 0–6 |
| Anti-vacuous | `test_crossbar_wrong_slave()` | Write to SRAM, read from wrong slave → MISMATCH |

### Open-Source Verilog References

- **AXI4 crossbar**: `rtl/soc/axi_crossbar.v:1–587` (full RTL with round-robin)
- **Crossbar topology**: `rtl/soc/README.md:29–61`
- **APB decoder**: `rtl/soc/apb_decoder.v:1–133`
- **APB protocol**: `rtl/soc/apb_decoder_tb.sv`
- **Crossbar config**: `sim/config/interconnect.yaml`

---

## Gap #1 — APB-MMIO Register Model (`APB-MMIO`)

### Current State (`mmio_bridge.py:29–49`)

`MMIOBridge.handle()` routes MMIO reads/writes by matching address base (`addr & 0xFFFFF000`) against module base addresses. Each `_handle_*` method processes registers individually. No unified APB peripheral register model exists — each handler re-implements the same `self._status[addr] = value` / `self._status.get(addr, 0)` pattern.

### Target State

A generic `APBPeripheral` base class that:
1. Defines register fields with read/write access, default values, and side-effect callbacks
2. Auto-generates `read(offset)` and `write(offset, value)` methods
3. Provides `read_register(name)` / `write_register(name, value)` named access for testability
4. Implementations: `MXURegisters`, `SFURegisters`, `VectorRegisters`, `DMARegisters`, `PCIeRegisters`, `DoorbellRegisters`, `INTCRegisters`

### API Design

```python
# sim/models/apb_peripheral.py

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

@dataclass
class RegisterField:
    """Single register definition in an APB peripheral."""
    name: str
    offset: int           # Byte offset within 4 KB window
    default: int = 0
    access: str = 'rw'    # 'r', 'w', 'rw', 'w1c' (write-1-to-clear)
    callback: Optional[Callable[[int], None]] = None
    # callback(value) called on write; for START bits, triggers side-effect

class APBPeripheral:
    """Base class for APB peripheral register models.

    Reference:
        rtl/soc/apb_decoder.v — slave select mapping
        regmap.py — register offset definitions for each module
    """

    def __init__(self, name: str, base_addr: int, fields: list[RegisterField]):
        self.name = name
        self.base_addr = base_addr
        self._fields: dict[str, RegisterField] = {f.name: f for f in fields}
        self._values: dict[str, int] = {f.name: f.default for f in fields}

    def read(self, offset: int) -> int:
        """Read register at byte offset within 4 KB window.

        Args:
            offset: Byte offset (0x00–0xFFC, 4-byte aligned)

        Returns:
            32-bit register value
        """

    def write(self, offset: int, value: int):
        """Write register at byte offset. Triggers callback if defined.

        Args:
            offset: Byte offset
            value:  32-bit value to write
        """

    def read_field(self, name: str) -> int:
        """Read register by field name (for test assertions)."""

    def write_field(self, name: str, value: int):
        """Write register by field name."""

# ── Example: MXU peripheral registers ────────────────────────────────

def make_mxu_peripheral(mxu_instance) -> APBPeripheral:
    """Factory: MXU register bank matching regmap.MXU."""
    return APBPeripheral("MXU", Addr.MXU_BASE, [
        RegisterField("CTRL",    0x00, default=0, access='rw'),
        RegisterField("CMD",     0x04, access='w',
                      callback=lambda v: mxu_instance.start_compute()),
        RegisterField("STATUS",  0x08, default=0, access='r'),
        RegisterField("DIM0",    0x0C, access='rw'),
        RegisterField("DIM1",    0x10, access='rw'),
        RegisterField("I_ADDR",  0x14, access='rw'),
        RegisterField("W_ADDR",  0x18, access='rw'),
        RegisterField("O_ADDR",  0x1C, access='rw'),
        RegisterField("BIAS_ADDR", 0x20, access='rw'),
        RegisterField("SCALE_ADDR", 0x24, access='rw'),
        RegisterField("IRQ_EN",  0x28, access='rw'),
    ])
```

### Integration into MMIOBridge

```python
# mmio_bridge.py — refactored handle()
def handle(self, rw: str, addr: int, value: int = 0) -> int:
    base = addr & 0xFFFF_F000
    if base not in self._peripherals:
        return 0
    periph = self._peripherals[base]
    if rw == 'read':
        return periph.read(addr - base)
    else:
        periph.write(addr - base, value)
        return 0
```

### Testability Plan

| Test | Function | Description |
|------|----------|-------------|
| Register readback | `test_apb_mmio_readback()` | Write → Readback all regs for each peripheral |
| Side effects | `test_apb_mmio_cmd_start()` | Write CMD=1 → STATUS=1, then STATUS=2 after compute |
| Unmapped address | `test_apb_mmio_unmapped()` | Read 0x4000_7FFF → returns 0, no crash |
| Anti-vacuous | `test_apb_mmio_wrong_field()` | Write to DIM0, read from DIM1 → DIM1 unchanged |

### Open-Source Verilog References

- **Register maps**: `regmap.py` (MXU/SFU/VECTOR/DMA/DOORBELL/INTC offsets)
- **APB decoder**: `rtl/soc/apb_decoder.v`
- **Peripheral APB interfaces**: `rtl/ip/dma_wrapper.v`, `rtl/ip/pcie_ep_wrapper.v`, `rtl/soc/doorbell.v`, `rtl/intc/intc_top.v`
- **Wrapper APB integration**: `rtl/wrapper/mxu_soc_wrapper.v`, `rtl/wrapper/sfu_soc_wrapper.v`, `rtl/wrapper/vector_soc_wrapper.v`

---

## Gap #2 — IBEX-AXI Bridge (`IBEX-AXI`)

### Current State (`miniv.py:42–77`)

`RISCVMini` has its own local `self.mem` bytearray (256 KB):

```python
def __init__(self, memory_size: int = 256 * 1024):
    self.mem = bytearray(memory_size)
    # ...
def _mem_read(self, addr):
    if self._is_mmio(addr):  # addr >= 0x40000000
        return self.mmio_callback('read', addr, 4)
    if addr + 4 <= len(self.mem):
        return struct.unpack_from('<I', self.mem, addr)[0]
    return 0
```

This `self.mem` is **completely independent** of `FuncModel.sram`/`FuncModel.dram`. There is no address decode between boot ROM, DMEM, SRAM, and DRAM — just a flat `self.mem` array.

### Target State

`RISCVMini` shares `FuncModel`'s memory via references. Address routing matches `ibex_wrapper.v`:

| Region | Address Range | Backing |
|--------|---------------|---------|
| Boot ROM | `0x0000_0000–0x0000_FFFF` | Separate ROM `bytearray` (loaded from firmware hex) |
| DMEM | `0x0001_0000–0x0001_FFFF` | Dedicated 64 KB data RAM (within RISCVMini) |
| SRAM | `0x2000_0000–0x203F_FFFF` | `FuncModel.sram` (via `CrossbarModel`) |
| DRAM | `0x8000_0000–0xFFFF_FFFF` | `FuncModel.dram` (via `CrossbarModel`) |
| MMIO | `0x4000_0000–0x4000_6FFF` | `MMIOBridge` (unchanged) |

### API Design — Interface (ii): `RISCVMini` sharing `FuncModel.sram`/`dram`

This is the second new cross-module interface from the plan.

```python
# miniv.py — modified RISCVMini.__init__

class RISCVMini:
    """Minimal RV32I emulator with shared FuncModel memory.

    New memory model: instead of self.mem, uses externally-provided
    bytearrays for SRAM and DRAM, plus a local boot_rom and dmem.

    References:
        rtl/cpu/ibex_wrapper.v:1–667 — Ibex address decode map
        rtl/soc/README.md:64–77      — Unified address space table
    """

    # ── Constructor ─────────────────────────────────────────────────
    def __init__(
        self,
        boot_rom: Optional[bytearray] = None,
        dmem_size: int = 64 * 1024,          # 64 KB local data memory
        sram: Optional[bytearray] = None,     # FuncModel.sram (4 MB)
        dram: Optional[bytearray] = None,     # FuncModel.dram (64 MB+)
        mmio_callback: Optional[Callable] = None,
    ):
        """Initialize RISC-V emulator with shared memory.

        Args:
            boot_rom:      ROM for instruction fetch at 0x0000_0000 (64 KB).
                           If None, RISCVMini allocates empty ROM.
            dmem_size:     Size of local data memory at 0x0001_0000.
            sram:          Shared FuncModel SRAM reference (must be len≥4MB
                           if provided; accessed at 0x2000_0000).
            dram:          Shared FuncModel DRAM reference (accessed at
                           0x8000_0000; max offset ≤ len(dram)).
            mmio_callback: Called as mmio_callback('read'/'write', addr, value)
                           for addresses ≥ 0x4000_0000.
        """
        self.boot_rom = boot_rom or bytearray(64 * 1024)
        self.dmem = bytearray(dmem_size)
        self.sram = sram          # None if FuncModel not yet initialized
        self.dram = dram          # None if FuncModel not yet initialized
        self.mmio_callback = mmio_callback
        self.state = RV32State()

    # ── Memory access (address-decode router) ────────────────────────
    def _mem_read(self, addr: int) -> int:
        """Read 4-byte word, routing through SoC address space.

        Address decode (matches ibex_wrapper.v address map):
            0x0000_0000–0x0000_FFFF  → self.boot_rom
            0x0001_0000–0x0001_FFFF  → self.dmem
            0x2000_0000–0x203F_FFFF  → self.sram
            0x4000_0000–0x4000_6FFF  → mmio_callback (APB MMIO)
            0x8000_0000–0xFFFF_FFFF  → self.dram

        Returns:
            32-bit word (little-endian). Returns 0 for out-of-range.
        """

    def _mem_write(self, addr: int, val: int):
        """Write 4-byte word, routing through SoC address space."""

    # ── Boot ROM Loading ─────────────────────────────────────────────
    def load_bootrom_hex(self, hex_path: str):
        """Load $readmemh-style hex file into boot_rom.

        Matches boot_rom.v $readmemh loading behavior.

        Args:
            hex_path: Path to firmware hex file
                       (e.g., firmware/build/npu_firmware.hex)
        """
```

### Testability Plan

| Test | Function | Description |
|------|----------|-------------|
| Memory routing | `test_ibex_memory_access()` | Write to DRAM via Ibex, read back same value; write to SRAM, read back |
| Boot ROM isolation | `test_ibex_bootrom()` | Ibex reads from 0x0000_0000 returns ROM content, not SRAM |
| Shared memory | `test_ibex_shared_sram()` | Ibex writes to SRAM, MXU reads same SRAM location via crossbar → consistent |
| Anti-vacuous | `test_ibex_isolation()` | Write to 0x80000100, read 0x80000200 → different values |

### Open-Source Verilog References

- **Ibex address map**: `rtl/cpu/ibex_wrapper.v:1–667` (instruction fetch, data access FSM, address decode)
- **Unified address space**: `rtl/soc/README.md:64–77`
- **Ibex opcodes / CSR**: `rtl/cpu/ibex/ibex_pkg.sv`
- **Boot ROM**: `rtl/soc/boot_rom.v`

---

## Gap #9 — INTC/IRQ Functional Model (`IRQ-CHAIN`)

### Current State (`mmio_bridge.py:299–335`)

`MMIOBridge._set_irq()` sets INTC PENDING bits in `_status` dict:

```python
def _set_irq(self, module_bit: int):
    base = INTC.BASE
    self._status[base + INTC.PENDING] = \
        self._status.get(base + INTC.PENDING, 0) | (1 << module_bit)

def _handle_intc(self, rw, addr, value):
    if rw == 'write' and off == INTC.ACK:
        self._status[INTC.BASE + INTC.PENDING] &= ~value
    # ...
    return self._status.get(addr & 0xFFFFFFFC, 0)
```

`RISCVMini` treats WFI as a NOP (`miniv.py:189`):

```python
elif funct3 == 0 and (insn >> 20) == 0x305:  # WFI — NOP for us
    pass
```

There is **no path** from `_set_irq` → `RISCVMini` — the CPU never learns about interrupts.

### Target State

Full interrupt delivery chain:
1. Engine completes → `_set_irq(module_bit)` → INTC.PENDING updated
2. INTC evaluates `(PENDING & ENABLE)` → if popcount ≥ THRESHOLD → asserts `cpu_irq`
3. `cpu_irq` assertion triggers `RISCVMini.interrupt_pending = True`
4. If executing WFI → RISCVMini wakes up, jumps to trap handler at `mtvec`
5. Trap handler reads INTC.PENDING, dispatches next firmware operation, writes ACK
6. ACK clears pending bit → cpu_irq de-asserted

### API Design — Interface (i): `MMIOBridge → RISCVMini` IRQ/Trap Notification

This is the first new cross-module interface from the plan.

```python
# mmio_bridge.py — modified _set_irq with CPU callback

class MMIOBridge:
    def __init__(self, modules, cpu: Optional[object] = None):
        """Initialize bridge with optional CPU notification callback.

        Args:
            modules: Dict of compute modules (mxu/sfu/vector/dma)
            cpu:     RISCVMini instance for interrupt notification.
                     When cpu_irq asserts, calls cpu.signal_irq(irq_vector).
        """
        self._cpu = cpu  # ← NEW: optional RISCVMini reference

    def _set_irq(self, module_bit: int):
        """Set INTC pending bit and potentially notify CPU.

        After setting pending bit, evaluates cpu_irq condition:
            cpu_irq = popcount(PENDING & ENABLE) >= THRESHOLD
        If cpu_irq asserts, notifies CPU via self._cpu.signal_irq().
        """
        base = INTC.BASE
        pending = self._status.get(base + INTC.PENDING, 0) | (1 << module_bit)
        self._status[base + INTC.PENDING] = pending

        # Evaluate cpu_irq
        enable = self._status.get(base + INTC.ENABLE, 0)
        threshold = self._status.get(base + INTC.THRESHOLD, 0)
        enabled_pending = pending & enable
        if bin(enabled_pending).count('1') >= threshold:
            self._status[base + INTC.CPU_IRQ] = 1
            if self._cpu:
                self._cpu.signal_irq(self._status[base + INTC.PENDING])


# miniv.py — modified RISCVMini with interrupt support

class RISCVMini:
    def __init__(self, ...):
        # ... existing init ...
        self.interrupt_pending = False
        self._pending_irq_vector = 0
        self.mtvec = 0x0000_0040  # Default trap handler address

    def signal_irq(self, irq_vector: int):
        """Called by MMIOBridge when cpu_irq asserts.

        Sets interrupt_pending flag. If CPU is in WFI, next step()
        will take the trap.

        Args:
            irq_vector: INTC.PENDING register value (for handler dispatch)
        """
        self.interrupt_pending = True
        self._pending_irq_vector = irq_vector

    def _handle_irq(self):
        """Execute interrupt trap handler sequence.

        Called during step() when interrupt_pending and in WFI/next instruction.
        1. Saves current PC to mepc
        2. Jumps to mtvec
        3. Firmware handler reads INTC.PENDING, dispatches, writes ACK
        4. Returns via mret (simplified: just clear interrupt_pending)
        """
        self.state.write(REG_MEPC, self.state.pc)
        self.state.next_pc = self.mtvec & 0xFFFFFFFC
        self.interrupt_pending = False

    def step(self) -> bool:
        """Modified: check interrupt_pending before each instruction.
        If pending, take trap instead of executing insn.
        """
        if self.interrupt_pending:
            self._handle_irq()
            self.state.pc = self.state.next_pc
            return True
        # ... existing instruction execution ...
        # WFI modification:
        # if opcode == 0x73 and (insn >> 20) == 0x305:  # WFI
        #     if self.interrupt_pending:
        #         self._handle_irq()
        #     else:
        #         self.state.next_pc = self.state.pc + 4  # spin (no advance)
```

### Testability Plan

| Test | Function | Description |
|------|----------|-------------|
| Single IRQ | `test_irq_single()` | MXU IRQ → INTC.PENDING[0]=1 → cpu_irq asserted → CPU traps |
| IRQ masking | `test_irq_mask()` | MXU IRQ but ENABLE[0]=0 → cpu_irq stays de-asserted |
| ACK clear | `test_irq_ack()` | CPU writes ACK=bit0 → PENDING[0] cleared → cpu_irq de-asserted |
| WFI wakeup | `test_irq_wfi()` | CPU in WFI → IRQ arrives → CPU wakes up, takes trap |
| Multi-source | `test_irq_multi()` | MXU+SFU+DMA all fire → PENDING=0b1011 → all bits visible |
| Anti-vacuous | `test_irq_no_irq()` | ENABLE=0 → no IRQ regardless of PENDING bits |

### Open-Source Verilog References

- **INTC RTL**: `rtl/intc/intc_top.v:1–189` (full register logic, popcount, threshold)
- **IRQ routing**: `rtl/soc/README.md:82–93`
- **Ibex interrupt interface**: `rtl/cpu/ibex_wrapper.v` (cpu_irq_i port)
- **SoC IRQ wiring**: `rtl/soc/caduceus_soc_top.v` (irq signal connects to intc_top instances)

---

## Gap #11 — IBEX-FW (Firmware Emulation Bridge) (`IBEX-FIRMWARE`)

### Current State (`miniv.py:234–415`)

`NPUFirmware` dispatches commands via `self.bridge.handle('write', ...)` — direct MMIO writes:

```python
def _dispatch(self, cmd):
    if op == OpCode.MMUL:
        tile_mmul(desc=desc, mmio_write=mwrite, mmio_read=mread, ...)
    elif op in (OpCode.SOFTMAX, ...):
        self._mmio_write(SFU.BASE + SFU.CTRL, sfu_op)
        # ...
    # ...
    self._wait_done(SFU.BASE + SFU.STATUS)
```

Key issues:
1. Does NOT go through `RISCVMini` load/store instructions for MMIO access
2. `_dram_read()` accesses bridge's dram directly, not through Ibex's memory model
3. `_wait_done()` polls STATUS registers — should be interrupt-driven after Gap #9
4. No boot ROM loading — firmware "just runs" without proper boot sequence

### Target State

`NPUFirmware` is refactored to go through `RISCVMini` for all memory and MMIO access:
1. Boot ROM loaded at `0x0000_0000` → RISCVMini starts at PC=0
2. Firmware initialization: set stack pointer, initialize doorbell, configure INTC
3. Main loop: poll doorbell (or wait on WFI after Gap #9) → read command descriptor from DRAM via Ibex load → dispatch via MMIO stores → receive IRQ → write completion
4. All address resolution goes through `RISCVMini._mem_read/_mem_write` → proper SoC address decode

### API Design

```python
# miniv.py — refactored NPUFirmware

class NPUFirmware:
    """NPU firmware emulator — routes dispatch through RISCVMini.

    Reference:
        firmware/npu_firmware.c  — C firmware source
        sim/miniv.py:32–207       — RISCVMini (now shares FuncModel memory)
        sim/regmap.py             — MMIO register offsets (Addr/MXU/SFU/...)
    """

    def __init__(
        self,
        cpu: RISCVMini,   # ← NEW: RISC-V emulator with shared memory
        ring_buffer_addr: int = 0x80000000,
        ring_size: int = 64,
    ):
        """Initialize firmware emulator.

        Args:
            cpu:               RISCVMini instance (shared memory + MMIO callback)
            ring_buffer_addr:  DRAM address of ring buffer (default 0x80000000)
            ring_size:         Number of ring buffer entries (default 64)
        """

    def load_firmware(self, hex_path: str):
        """Load firmware hex into CPU boot_rom and set initial state.

        Sets PC=0x0000_0000, initializes stack pointer (sp=x2) from
        boot_rom vector table, sets mtvec.

        Args:
            hex_path: Path to firmware hex file
        """

    def boot(self) -> bool:
        """Execute boot sequence: ROM init → stack setup → main() entry.

        Runs RISCVMini.step() until firmware reaches main() or
        exit(0). Returns True if boot succeeded.
        """

    def run_loop(self, max_commands: int = 10) -> list[dict]:
        """Main firmware loop: poll doorbell → dispatch → complete.

        Modified: uses RISCVMini for MMIO reads/writes instead of
        direct bridge.handle() calls. Uses interrupt-driven completion
        after Gap #9, falling back to STATUS polling for now.

        Args:
            max_commands: Max commands to process (0 = run until idle)

        Returns:
            List of {'opcode': int, 'status': str} per command
        """

    def _dispatch_via_ibex(self, cmd: dict) -> dict:
        """Dispatch command by driving RISCVMini through firmware path.

        The firmware handler code:
        1. Reads descriptor fields from DRAM via Ibex loads
        2. Writes engine MMIO registers via Ibex stores
        3. Writes CMD.START via Ibex store
        4. Waits for IRQ (or polls STATUS) via Ibex load
        5. Returns result

        For speed, can short-circuit compute engines to direct
        GoldenExecutor calls while keeping memory access routed
        through RISCVMini address decode.

        Args:
            cmd: {'opcode': int, 'desc_addr': int, 'flags': int}

        Returns:
            {'opcode': int, 'status': str}
        """

    def _mmio_write_via_cpu(self, addr: int, val: int):
        """Write MMIO register via RISCVMini store instruction.

        Uses self.cpu._mem_write(addr, val) → routed through
        SoC address decode → MMIOBridge.handle('write', ...).
        """

    def _mmio_read_via_cpu(self, addr: int) -> int:
        """Read MMIO register via RISCVMini load instruction.

        Uses self.cpu._mem_read(addr) → MMIOBridge.handle('read', ...).
        """
```

### Integration into FuncModel

```python
# func_model.py — modified __init__
def __init__(self, dram_mb=64, sram_kb=512):
    self.dram = bytearray(dram_mb * 1024 * 1024)
    self.sram = bytearray(sram_kb * 1024)

    # Compute modules (unchanged)
    self.mxu = GoldenMXU()
    self.sfu = GoldenSFU()
    self.vector = GoldenVector()
    self.dma_engine = GoldenDMA()

    # NEW: Crossbar wraps shared memory
    self.crossbar = CrossbarModel(sram=self.sram, dram=self.dram)

    # NEW: RISCVMini with shared memory
    self.cpu = RISCVMini(
        sram=self.sram,
        dram=self.dram,
        mmio_callback=self.bridge.handle,
    )

    # Bridge gets CPU reference for interrupt delivery
    self.bridge = MMIOBridge(
        modules={'mxu': self.mxu, ..., 'crossbar': self.crossbar},
        cpu=self.cpu,  # ← Interface (i): IRQ notification
    )

    # Firmware uses the shared CPU
    self.firmware = NPUFirmware(cpu=self.cpu)
```

### Testability Plan

| Test | Function | Description |
|------|----------|-------------|
| Boot flow | `test_firmware_bootflow()` | Boot from ROM → init → receive doorbell → dispatch MMUL → complete via IRQ → read result from DRAM |
| Ring buffer | `test_firmware_ring_buffer()` | Queue 3 commands → all dispatched → ring buffer wraps at ring_size |
| MMIO through CPU | `test_firmware_mmio_via_cpu()` | Write MMIO register via `_mem_write` → verify bridge sees correct value |
| Interrupt-driven | `test_firmware_interrupt_dispatch()` | Command dispatched, engine completes → IRQ triggers → next command (no polling) |
| Anti-vacuous | `test_firmware_bad_opcode()` | Corrupted doorbell command → firmware rejects, returns error status |

### Open-Source Verilog References

- **C firmware**: `firmware/npu_firmware.c`
- **Firmware build**: `firmware/Makefile`
- **Ibex integration**: `rtl/cpu/ibex_wrapper.v`
- **Doorbell protocol**: `rtl/soc/doorbell.v:1–113`
- **Boot ROM**: `rtl/soc/boot_rom.v`
- **SoC hierarchy**: `rtl/soc/caduceus_soc_top.v`

---

## Four New Cross-Module Interfaces

### Interface (i): `MMIOBridge → RISCVMini` IRQ/Trap Notification

| Aspect | Detail |
|--------|--------|
| **Who calls whom** | `MMIOBridge._set_irq()` calls `RISCVMini.signal_irq(irq_vector)` |
| **When** | After any engine IRQ handler sets INTC PENDING bits AND cpu_irq evaluates to true (popcount ≥ THRESHOLD) |
| **Parameters** | `irq_vector: int` — copy of INTC.PENDING register value |
| **Return** | `None` — sets `RISCVMini.interrupt_pending = True` |
| **Integration point** | `MMIOBridge.__init__(cpu=RISCVMini)` sets `self._cpu`. `_set_irq()` checks `self._cpu is not None` before calling `signal_irq` |
| **Python signature** | `def signal_irq(self, irq_vector: int) -> None:` |

### Interface (ii): `RISCVMini` sharing `FuncModel.sram`/`dram` bytearrays

| Aspect | Detail |
|--------|--------|
| **Who calls whom** | `FuncModel.__init__()` passes `sram=` and `dram=` references to `RISCVMini.__init__()` |
| **When** | Constructor time, before any model execution |
| **Parameters** | `sram: bytearray` — reference to FuncModel.sram (4 MB, shared). `dram: bytearray` — reference to FuncModel.dram (shared) |
| **Return** | `None` — stores references as `self.sram` and `self.dram` |
| **Integration point** | `RISCVMini._mem_read/write` address decoder routes `0x2000_0000+` → `self.sram`, `0x8000_0000+` → `self.dram`. `RISCVMini.mem` (private) is replaced by per-region arrays: `self.boot_rom`, `self.dmem`, plus the shared `self.sram` and `self.dram` |
| **Python signature** | `def __init__(self, ..., sram: Optional[bytearray] = None, dram: Optional[bytearray] = None)` |

### Interface (iii): `CrossbarModel.read/write` API

| Aspect | Detail |
|--------|--------|
| **Who calls whom** | `MMIOBridge._handle_mxu/_handle_sfu/_handle_vector/_handle_dma` call `crossbar.read(master_id, addr, size)` / `crossbar.write(master_id, addr, data)` instead of direct `sram[off:off+len]` |
| **When** | Every MMIO-initiated data movement: engine reads activation/weight from memory, engine writes result to memory, DMA transfers between DRAM and SRAM |
| **Parameters** | `read(master_id: int, addr: int, size: int) -> bytes`. `write(master_id: int, addr: int, data: bytes) -> None` |
| **Return** | `read` returns `bytes` (may be zero-filled for DECERR). `write` returns `None` |
| **Integration point** | `MMIOBridge.modules['crossbar'] = CrossbarModel` instance. `_get_mem()` is removed; `_translate_addr()` becomes `_resolve_addr_to_crossbar()`. Master IDs: MXU→1, SFU→2, Vector→3, DMA→4, PCIe→5 |
| **Python signatures** | `def read(self, master_id: int, addr: int, size: int) -> bytes:`. `def write(self, master_id: int, addr: int, data: bytes) -> None:` |

### Interface (iv): `PCIeModel.tlp_write/tlp_read` API

| Aspect | Detail |
|--------|--------|
| **Who calls whom** | `FuncModel.host_write_*` methods (and test harnesses) call `pcie.tlp_write(addr, data)` / `pcie.tlp_read(addr, size)` |
| **When** | Host sends data to NPU: model weights/activations written to DRAM, commands written to ring buffer, results read back |
| **Parameters** | `tlp_write(addr: int, data: bytes) -> None`. `tlp_read(addr: int, size: int) -> bytes` |
| **Return** | `tlp_write` returns `None`. `tlp_read` returns raw bytes |
| **Integration point** | `FuncModel.__init__()` creates `self.pcie = PCIeModel(sram=self.sram, dram=self.dram)`. `host_write_data/command/descriptor` become convenience wrappers around `self.pcie.tlp_write()`. The PCIe model internally routes through CrossbarModel (master_id=MASTER_PCIE) for address-resolved access |
| **Python signatures** | `def tlp_write(self, addr: int, data: bytes) -> None:`. `def tlp_read(self, addr: int, size: int) -> bytes:` |

---

## Summary Table

| Gap # | Path ID | Current File(s) | New File(s) | API Entry Points | Test Function |
|:-----:|---------|-----------------|-------------|------------------|---------------|
| 7 | `PCIE-TLP` | `func_model.py:46–93` (`host_write_*`) | `sim/models/pcie.py` | `PCIeModel.tlp_write(addr, data)`, `PCIeModel.tlp_read(addr, size)`, `PCIeModel.send_msi(vector)` | `test_pcie_tlp_smoke()` |
| 8 | `XBAR-ARB` | `mmio_bridge.py:309–323` (`_get_mem`, `_translate_addr`) | `sim/models/crossbar.py` | `CrossbarModel.read(master_id, addr, size)`, `CrossbarModel.write(master_id, addr, data)`, `APBDecoder.decode(paddr)` | `test_crossbar_concurrent()` |
| 1 | `APB-MMIO` | `mmio_bridge.py:29–335` (per-engine `_handle_*`) | `sim/models/apb_peripheral.py` | `APBPeripheral.read(offset)`, `APBPeripheral.write(offset, value)` | `test_apb_mmio_readback()` |
| 2 | `IBEX-AXI` | `miniv.py:42–77` (`self.mem`, `_mem_read/write`) | `miniv.py` (modified `_mem_*`) | `RISCVMini(sram=, dram=)` ← Interface (ii); `RISCVMini.load_bootrom_hex(path)` | `test_ibex_memory_access()` |
| 9 | `IRQ-CHAIN` | `mmio_bridge.py:325–328` (`_set_irq`), `miniv.py:189` (WFI NOP) | `mmio_bridge.py` (modified), `miniv.py` (modified) | `RISCVMini.signal_irq(vector)` ← Interface (i); `RISCVMini._handle_irq()` | `test_irq_single()` |
| 11 | `IBEX-FIRMWARE` | `miniv.py:234–415` (`NPUFirmware`) | `miniv.py` (refactored `NPUFirmware`) | `NPUFirmware(cpu=RISCVMini)`, `NPUFirmware.load_firmware(path)`, `NPUFirmware.boot()`, `NPUFirmware._dispatch_via_ibex(cmd)` | `test_firmware_bootflow()` |

### Build Order (Dependency Chain)

```
Wave 1: Foundation (no dependencies)
  ├── CrossbarModel (Gap #8)        ← Interface (iii)
  ├── APB Peripheral base (Gap #1)   ← enables clean MMIO refactoring
  └── PCIe Model (Gap #7)           ← Interface (iv)

Wave 2: Integration (depends on Wave 1)
  ├── RISCVMini shared memory (Gap #2)  ← Interface (ii), depends on CrossbarModel
  └── Interrupt delivery (Gap #9)        ← Interface (i)

Wave 3: Firmware unification (depends on Wave 2)
  └── Ibex firmware path (Gap #11)       ← depends on Gaps #2, #9
```

---

## Open-Source Verilog Specification References

### Primary References (CaduceusCore-authored, used directly)

| File | Content | Used For |
|------|---------|----------|
| `rtl/soc/caduceus_soc_top.v` | Full SoC hierarchy, 12 module instances, interconnect wiring | Overall topology validation |
| `rtl/soc/axi_crossbar.v` | M=6/S=2 round-robin crossbar RTL (587 lines) | CrossbarModel address decode, arbitration, ID routing |
| `rtl/soc/apb_decoder.v` | 1→7 APB decoder with psel/paddr logic (133 lines) | APBDecoder slave select mapping |
| `rtl/soc/doorbell.v` | Ring buffer doorbell APB registers (113 lines) | Doorbell register model, IRQ protocol |
| `rtl/soc/boot_rom.v` | 64KB ROM with $readmemh loading | Boot ROM loading behavior |
| `rtl/soc/README.md` | Address space table, IRQ routing, crossbar topology | Single-source-of-truth for addresses and routing |
| `rtl/intc/intc_top.v` | 7-source INTC: PENDING/ENABLE/THRESHOLD/ACK (189 lines) | INTC register model, popcount threshold logic |
| `rtl/cpu/ibex_wrapper.v` | Ibex address decode, memory map, bus protocols (667 lines) | RISCVMini address routing (boot_rom/DMEM/SRAM/DRAM) |
| `rtl/ip/pcie_ep_wrapper.v` | PCIe EP with TLP ports, AXI4 master, APB slave (400 lines) | PCIeModel TLP port mapping, BAR layout |
| `rtl/ip/pcie_ep_tb.sv` | PCIe testbench with TLP header format (636 lines) | TLP 3-DW header field definitions |
| `rtl/ip/dma_wrapper.v` | DMA wrapper APB registers + descriptor FSM (441 lines) | DMA register model, descriptor translation |
| `rtl/ip/dma_wrapper_tb.sv` | DMA testbench (311 lines) | DMA expected behavior verification vectors |

### Secondary References (Vendored open-source IP, for protocol detail)

| File | License | Content | Used For |
|------|---------|---------|----------|
| `rtl/ip/verilog-pcie/pcie_axi_master.v` | MIT | PCIe→AXI bridge RTL | TLP→AXI translation logic reference |
| `rtl/ip/verilog-pcie/pcie_tlp_demux_wrap.py` | MIT | Parametric TLP demux Jinja2 template | BAR matching logic, TLP header fields |
| `rtl/ip/verilog-axi/axi_crossbar_wrap.py` | MIT | Parametric crossbar Jinja2 template | AXI4 signal names, arbitration parameters |
| `rtl/ip/verilog-axi/arbiter.v` | MIT | Round-robin arbiter RTL | Arbitration algorithm reference |
| `rtl/cpu/ibex/ibex_pkg.sv` | Apache 2.0 | RV32IMC opcodes, CSR addresses | RISC-V instruction decode reference |
| `rtl/cpu/ibex/ibex_tracer.sv` | Apache 2.0 | RVFI instruction tracer | Tracer output format for cross-validation |

### Internal Python References

| File | Content | Used For |
|------|---------|----------|
| `sim/regmap.py` | Full MMIO register map (Addr, MXU, SFU, VECTOR, DMA, DOORBELL, INTC) | All module base addresses and register offsets |
| `sim/func_model.py` | FuncModel.__init__, host_write_*, test_conv2d_smoke | Reference for existing integration points |
| `sim/mmio_bridge.py` | MMIOBridge handle + per-engine _handle_* methods | Reference for current MMIO routing behavior |
| `sim/miniv.py` | RISCVMini, NPUFirmware | Reference for current CPU and firmware models |
| `sim/golden_executor.py` | GoldenMXU, GoldenSFU, GoldenVector, GoldenDMA | BIT-exact compute reference |
| `sim/config/interconnect.yaml` | Crossbar and interconnect configuration | Crossbar topology parameters |
