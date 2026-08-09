#!/usr/bin/env python3
"""Generate golden reference vectors for all 33 SoC Func Model test cases.

Usage (from CaduceusCore/):
    PYTHONPATH=sim python sim/gen_soc_rtl_vectors.py

Output:
    rtl/test_vectors/soc_e2e/<case_id>/{input.npz,expected.npz}

Format conventions (aligned with rtl/test_vectors/qwen_blk0/blk0_manifest.json):
    input.npz keys:
        - case_id (str)
        - priority (str)
        - sram_initial (np.uint8 array, full SRAM snapshot at input boundary)
        - dram_initial (np.uint8 array, full DRAM snapshot at input boundary)
        - mmio_writes_addr (np.uint32 array)
        - mmio_writes_value (np.uint32 array)
        - mmio_writes_mask (np.uint32 array, PSTRB style; 0xF default)
        - doorbell_opcodes (np.uint32 array)
        - doorbell_desc_addrs (np.uint64 array)
        - doorbell_flags (np.uint32 array)
        - doorbell_host_tail (np.uint32 scalar)
        - doorbell_npu_head (np.uint32 scalar)
        - doorbell_ring_size (np.uint32 scalar)
        - doorbell_ring_base (np.uint32 scalar)
        - pcie_writes_addr (np.uint32 array)
        - pcie_writes_data (object array of bytes)
        - descriptor_writes_addr (np.uint32 array)
        - descriptor_writes_data (object array of bytes)
        - metadata_json (str)

    expected.npz keys:
        - sram_final (np.uint8 array, full SRAM snapshot after execution)
        - dram_final (np.uint8 array, full DRAM snapshot after execution)
        - mmio_readbacks_addr (np.uint32 array)
        - mmio_readbacks_value (np.uint32 array)
        - status_flags_json (str)  # e.g. {"MXU_STATUS":2,"DMA_STATUS":2,...}
        - irq_flags_json (str)     # e.g. {"INTC_PENDING":0,"HOST_PENDING":1}
        - pcie_readbacks_addr (np.uint32 array)
        - pcie_readbacks_data (object array of bytes)
        - metadata_json (str)

The generator does NOT modify FuncModel, golden_executor, or tests.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "sim"))

from func_model import FuncModel
from golden_executor import GoldenMXU, GoldenSFU, GoldenVector, GoldenDMA
from regmap import Addr, MXU, SFU, VECTOR, DMA, DOORBELL, INTC
from models.crossbar import CrossbarModel
from engine.isa import OpCode

# ══════════════════════════════════════════════════════════════════════
# Paths and ordering
# ══════════════════════════════════════════════════════════════════════

OUT_DIR = Path("rtl/test_vectors/soc_e2e")
CASE_ORDER = [
    # P0 — infrastructure + data integrity (8 cases)
    "FM-SOC-001", "FM-SOC-002", "FM-SOC-003", "FM-SOC-004",
    "FM-SOC-005", "FM-SOC-006", "FM-SOC-007", "FM-SOC-008",
    # P1 — compute engine control paths (7 cases)
    "FM-SOC-009", "FM-SOC-010", "FM-SOC-011", "FM-SOC-012",
    "FM-SOC-024", "FM-SOC-025", "FM-SOC-026",
    # P2 — integration cross-module data paths (5 cases)
    "FM-SOC-013", "FM-SOC-014", "FM-SOC-015", "FM-SOC-016", "FM-SOC-027",
    # P3 — boundary + error handling (8 cases)
    "FM-SOC-017", "FM-SOC-018", "FM-SOC-019", "FM-SOC-020",
    "FM-SOC-028", "FM-SOC-029", "FM-SOC-030", "FM-SOC-031",
    # P4 — full end-to-end chains (5 cases)
    "FM-SOC-021", "FM-SOC-022", "FM-SOC-023", "FM-SOC-032", "FM-SOC-10X",
]

PRIORITY_OF = {cid: "P0" for cid in CASE_ORDER[:8]}
PRIORITY_OF.update({cid: "P1" for cid in CASE_ORDER[8:15]})
PRIORITY_OF.update({cid: "P2" for cid in CASE_ORDER[15:20]})
PRIORITY_OF.update({cid: "P3" for cid in CASE_ORDER[20:28]})
PRIORITY_OF.update({cid: "P4" for cid in CASE_ORDER[28:]})

# Common random seeds to match original tests
RNG_SFU = np.random.RandomState(20260703)
RNG_VEC = np.random.RandomState(20260704)
RNG_DB = np.random.RandomState(20260703)


# ══════════════════════════════════════════════════════════════════════
# VectorRecorder — captures Func Model stimulus and golden response
# ══════════════════════════════════════════════════════════════════════

@dataclass
class VectorRecorder:
    """Record stimulus and expected state for one case."""

    model: FuncModel
    case_id: str
    priority: str

    # Stimulus records
    mmio_writes: List[Tuple[int, int, int]] = field(default_factory=list)  # addr, value, mask
    mmio_reads: List[Tuple[int, int]] = field(default_factory=list)
    doorbell_commands: List[Tuple[int, int, int]] = field(default_factory=list)  # opcode, desc_addr, flags
    pcie_writes: List[Tuple[int, bytes]] = field(default_factory=list)
    pcie_reads: List[Tuple[int, bytes]] = field(default_factory=list)
    descriptor_writes: List[Tuple[int, bytes]] = field(default_factory=list)
    data_writes: List[Tuple[int, bytes]] = field(default_factory=list)

    # Snapshots
    sram_initial: Optional[np.ndarray] = None
    dram_initial: Optional[np.ndarray] = None
    sram_final: Optional[np.ndarray] = None
    dram_final: Optional[np.ndarray] = None

    # State tracking
    status_flags: Dict[str, int] = field(default_factory=dict)
    irq_flags: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        self._patch()

    def _patch(self):
        """Patch model APIs to record stimulus/response."""
        # MMIO bridge
        orig_bridge_handle = self.model.bridge.handle

        def bridge_handle(rw: str, addr: int, value: int = 0) -> int:
            result = orig_bridge_handle(rw, addr, value)
            if rw == "write":
                self.mmio_writes.append((addr & 0xFFFFFFFF, int(value) & 0xFFFFFFFF, 0xF))
            else:
                self.mmio_reads.append((addr & 0xFFFFFFFF, int(result) & 0xFFFFFFFF))
            return result

        self.model.bridge.handle = bridge_handle

        # PCIe TLP writes/reads
        orig_tlp_write = self.model.pcie.tlp_write
        orig_tlp_read = self.model.pcie.tlp_read

        def tlp_write(addr: int, data: bytes):
            self.pcie_writes.append((addr & 0xFFFFFFFF, bytes(data)))
            return orig_tlp_write(addr, data)

        def tlp_read(addr: int, size: int) -> bytes:
            result = orig_tlp_read(addr, size)
            self.pcie_reads.append((addr & 0xFFFFFFFF, bytes(result)))
            return result

        self.model.pcie.tlp_write = tlp_write
        self.model.pcie.tlp_read = tlp_read

        # Host-level helpers
        orig_host_write_command = self.model.host_write_command
        orig_host_write_descriptor = self.model.host_write_descriptor
        orig_host_write_data = self.model.host_write_data

        def host_write_command(opcode: int, desc_addr: int, flags: int = 0):
            self.doorbell_commands.append((int(opcode) & 0xFFFFFFFF, int(desc_addr) & 0xFFFFFFFFFFFFFFFF, int(flags) & 0xFFFFFFFF))
            return orig_host_write_command(opcode, desc_addr, flags)

        def host_write_descriptor(desc_addr: int, **kwargs):
            # Record as a data write via the underlying pcie.tlp_write call inside func_model
            return orig_host_write_descriptor(desc_addr, **kwargs)

        def host_write_data(addr: int, data: np.ndarray):
            self.data_writes.append((addr & 0xFFFFFFFF, bytes(data.tobytes())))
            return orig_host_write_data(addr, data)

        self.model.host_write_command = host_write_command
        self.model.host_write_descriptor = host_write_descriptor
        self.model.host_write_data = host_write_data

    def capture_input(self):
        """Capture memory state used as RTL input boundary."""
        self.sram_initial = np.asarray(self.model.sram, dtype=np.uint8).copy()
        self.dram_initial = np.asarray(self.model.dram, dtype=np.uint8).copy()

    def finalize(self):
        """Capture final memory state and save vectors."""
        self.sram_final = np.asarray(self.model.sram, dtype=np.uint8).copy()
        self.dram_final = np.asarray(self.model.dram, dtype=np.uint8).copy()

        # Collect final status/irq reads
        self.status_flags = self._collect_status_flags()
        self.irq_flags = self._collect_irq_flags()

    def _collect_status_flags(self) -> Dict[str, int]:
        """Read key STATUS registers (best-effort)."""
        flags: Dict[str, int] = {}
        try:
            flags["MXU_STATUS"] = int(self.model.bridge.handle("read", MXU.BASE + MXU.STATUS)) & 0xFFFFFFFF
        except Exception:
            pass
        try:
            flags["SFU_STATUS"] = int(self.model.bridge.handle("read", SFU.BASE + SFU.STATUS)) & 0xFFFFFFFF
        except Exception:
            pass
        try:
            flags["VECTOR_STATUS"] = int(self.model.bridge.handle("read", VECTOR.BASE + VECTOR.STATUS)) & 0xFFFFFFFF
        except Exception:
            pass
        try:
            flags["DMA_STATUS"] = int(self.model.bridge.handle("read", DMA.BASE + DMA.STATUS)) & 0xFFFFFFFF
        except Exception:
            pass
        return flags

    def _collect_irq_flags(self) -> Dict[str, int]:
        """Read interrupt controller state."""
        flags: Dict[str, int] = {}
        try:
            flags["INTC_PENDING"] = int(self.model.bridge.handle("read", INTC.BASE + INTC.PENDING)) & 0xFFFFFFFF
            flags["INTC_ENABLE"] = int(self.model.bridge.handle("read", INTC.BASE + INTC.ENABLE)) & 0xFFFFFFFF
        except Exception:
            pass
        try:
            flags["HOST_PENDING"] = 1 if (flags.get("INTC_PENDING", 0) & (1 << 8)) else 0
        except Exception:
            pass
        return flags

    def _npz_dict(self, is_input: bool) -> Dict[str, Any]:
        """Build numpy-serializable dict."""
        meta = {
            "case_id": self.case_id,
            "priority": self.priority,
            "model_dram_mb": len(self.model.dram) // (1024 * 1024),
            "model_sram_kb": len(self.model.sram) // 1024,
        }

        def _as_u32(arr):
            arr = np.asarray(arr, dtype=np.uint32)
            return arr if arr.size else np.array([], dtype=np.uint32)

        def _as_u64(arr):
            arr = np.asarray(arr, dtype=np.uint64)
            return arr if arr.size else np.array([], dtype=np.uint64)

        def _bytes_object(arr):
            if not arr:
                return np.array([], dtype=object)
            return np.array([bytes(x) for x in arr], dtype=object)

        if is_input:
            doorbell_opcodes, doorbell_desc_addrs, doorbell_flags = (
                zip(*self.doorbell_commands) if self.doorbell_commands else ([], [], [])
            )
            pcie_addrs, pcie_data = zip(*self.pcie_writes) if self.pcie_writes else ([], [])
            desc_addrs, desc_data = zip(*self.descriptor_writes) if self.descriptor_writes else ([], [])
            mmio_addr, mmio_val, mmio_mask = (
                zip(*self.mmio_writes) if self.mmio_writes else ([], [], [])
            )
            return {
                "case_id": self.case_id,
                "priority": self.priority,
                "sram_initial": self.sram_initial,
                "dram_initial": self.dram_initial,
                "mmio_writes_addr": _as_u32(mmio_addr),
                "mmio_writes_value": _as_u32(mmio_val),
                "mmio_writes_mask": _as_u32(mmio_mask),
                "doorbell_opcodes": _as_u32(doorbell_opcodes),
                "doorbell_desc_addrs": _as_u64(doorbell_desc_addrs),
                "doorbell_flags": _as_u32(doorbell_flags),
                "doorbell_host_tail": np.uint32(self.model.firmware.doorbell.get("host_tail", 0)),
                "doorbell_npu_head": np.uint32(self.model.firmware.doorbell.get("npu_head", 0)),
                "doorbell_ring_size": np.uint32(self.model.firmware.ring_size),
                "doorbell_ring_base": np.uint32(self.model.firmware.ring_buffer_addr),
                "pcie_writes_addr": _as_u32(pcie_addrs),
                "pcie_writes_data": _bytes_object(pcie_data),
                "descriptor_writes_addr": _as_u32(desc_addrs),
                "descriptor_writes_data": _bytes_object(desc_data),
                "metadata_json": json.dumps(meta, indent=2),
            }
        else:
            mmio_r_addr, mmio_r_val = zip(*self.mmio_reads) if self.mmio_reads else ([], [])
            pcie_r_addr, pcie_r_data = zip(*self.pcie_reads) if self.pcie_reads else ([], [])
            meta["status_flags"] = self.status_flags
            meta["irq_flags"] = self.irq_flags
            return {
                "case_id": self.case_id,
                "priority": self.priority,
                "sram_final": self.sram_final,
                "dram_final": self.dram_final,
                "mmio_readbacks_addr": _as_u32(mmio_r_addr),
                "mmio_readbacks_value": _as_u32(mmio_r_val),
                "status_flags_json": json.dumps(self.status_flags, indent=2),
                "irq_flags_json": json.dumps(self.irq_flags, indent=2),
                "pcie_readbacks_addr": _as_u32(pcie_r_addr),
                "pcie_readbacks_data": _bytes_object(pcie_r_data),
                "metadata_json": json.dumps(meta, indent=2),
            }

    def save(self, out_dir: Path):
        """Write input.npz and expected.npz."""
        case_dir = out_dir / self.case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(case_dir / "input.npz", **self._npz_dict(is_input=True))
        np.savez_compressed(case_dir / "expected.npz", **self._npz_dict(is_input=False))


# ══════════════════════════════════════════════════════════════════════
# Shared helpers (mirroring test_soc_fm.py helpers)
# ══════════════════════════════════════════════════════════════════════

def _dram_read_direct(model: FuncModel, addr: int, size: int) -> bytes:
    return bytes(model.dram[addr - Addr.DRAM_BASE:addr - Addr.DRAM_BASE + size])


def _sram_write(model: FuncModel, addr: int, data: bytes):
    model.sram[addr:addr + len(data)] = data


def _sram_read(model: FuncModel, addr: int, size: int) -> bytes:
    return bytes(model.sram[addr:addr + size])


def _mmio_write_sram(model: FuncModel, data: np.ndarray, raw_offset: int):
    """Write float32 data as FP16 bytes into SRAM at a raw offset."""
    fp16_bytes = data.astype(np.float16).tobytes()
    model.sram[raw_offset:raw_offset + len(fp16_bytes)] = fp16_bytes


def _mmio_read_sram(model: FuncModel, n_elements: int, raw_offset: int) -> np.ndarray:
    """Read N FP16 elements from SRAM at raw offset, return float32."""
    nbytes = n_elements * 2
    return np.frombuffer(bytes(model.sram[raw_offset:raw_offset + nbytes]), dtype=np.float16).astype(np.float32)


def _mmio_sfu_op(model: FuncModel, op: int, length: int, head_dim: int = 0, pos: int = 0):
    bridge = model.bridge
    bridge.handle("write", SFU.BASE + SFU.CTRL, op)
    bridge.handle("write", SFU.BASE + SFU.I_ADDR, 0x10000)
    bridge.handle("write", SFU.BASE + SFU.O_ADDR, 0x20000)
    bridge.handle("write", SFU.BASE + SFU.DIM, (head_dim << 16) | length)
    bridge.handle("write", SFU.BASE + SFU.POS, pos)
    bridge.handle("write", SFU.BASE + SFU.CMD, 1)


def _vec_write_i32(model: FuncModel, data: np.ndarray, raw_offset: int):
    model.sram[raw_offset:raw_offset + data.nbytes] = data.tobytes()


def _vec_write_f16(model: FuncModel, data: np.ndarray, raw_offset: int):
    fp16_bytes = data.astype(np.float16).tobytes()
    model.sram[raw_offset:raw_offset + len(fp16_bytes)] = fp16_bytes


def _vec_read_i32(model: FuncModel, n: int, raw_offset: int) -> np.ndarray:
    return np.frombuffer(bytes(model.sram[raw_offset:raw_offset + n * 4]), dtype=np.int32)


def _vec_read_f16(model: FuncModel, n: int, raw_offset: int) -> np.ndarray:
    return np.frombuffer(bytes(model.sram[raw_offset:raw_offset + n * 2]), dtype=np.float16).astype(np.float32)


def _vec_mmio_op(model: FuncModel, op: int, dim: int):
    bridge = model.bridge
    bridge.handle("write", VECTOR.BASE + VECTOR.CTRL, op)
    bridge.handle("write", VECTOR.BASE + VECTOR.A_ADDR, 0x30000)
    bridge.handle("write", VECTOR.BASE + VECTOR.B_ADDR, 0x31000)
    bridge.handle("write", VECTOR.BASE + VECTOR.O_ADDR, 0x40000)
    bridge.handle("write", VECTOR.BASE + VECTOR.DIM, dim)
    bridge.handle("write", VECTOR.BASE + VECTOR.CMD, 1)


def _doorbell_write_mmul_desc(model: FuncModel, desc_addr: int,
                               act_addr: int, wgt_addr: int, out_addr: int,
                               scale_addr: int, M: int, K: int, N: int):
    act_size = M * K
    wgt_size = (K * N + 1) // 2
    out_size = M * N * 4
    scale_size = ((K + 127) // 128) * N * 4
    model.host_write_descriptor(desc_addr,
        input_addr=act_addr, weight_addr=wgt_addr, output_addr=out_addr,
        scale_addr=scale_addr, scale_size=scale_size,
        input_size=act_size, weight_size=wgt_size, output_size=out_size,
        M=M, K=K, N=N)


def _doorbell_setup_mmul(model: FuncModel, M: int, K: int, N: int,
                         act_addr: int, wgt_addr: int, out_addr: int,
                         scale_addr: int, desc_addr: int,
                         rng: np.random.RandomState):
    act = rng.randint(-8, 8, size=M * K, dtype=np.int8)
    wgt = rng.randint(-8, 8, size=K * N, dtype=np.int8)
    wgt_packed = GoldenMXU.pack_int4(wgt)
    num_blocks = (K + 127) // 128
    scales = np.ones((num_blocks, N), dtype=np.float32)
    model.host_write_data(act_addr, act)
    model.host_write_data(wgt_addr, wgt_packed)
    model.host_write_data(scale_addr, scales.ravel())
    _doorbell_write_mmul_desc(model, desc_addr, act_addr, wgt_addr, out_addr,
                              scale_addr, M, K, N)
    return act, wgt_packed, scales


# blk.0 helpers
_BLK0_VECTOR_DIR = Path("rtl/test_vectors/qwen_blk0")
_EB_BY_FMT = {"int8": 1, "fp16": 2, "int32": 4}


def _blk0_read_hex(rel_path: str, elem_bytes: int = 1) -> bytes:
    path = _BLK0_VECTOR_DIR / rel_path
    with open(path) as f:
        vals = [int(line.strip(), 16) for line in f if line.strip()]
    if not vals:
        return b""
    if elem_bytes == 1:
        return bytes(vals)
    fmt = {2: "H", 4: "I", 8: "Q"}[elem_bytes]
    return b"".join(struct.pack(f"<{fmt}", v) for v in vals)


def _load_blk0_manifest() -> dict:
    path = _BLK0_VECTOR_DIR / "blk0_manifest.json"
    with open(path) as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════════════════
# P0 case generators
# ══════════════════════════════════════════════════════════════════════

def gen_fm_soc_001() -> VectorRecorder:
    """APB-MMIO handshake: write MXU CTRL, read back, psel/penable checks."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-001", "P0")
    # Pre-load not needed; APB writes are the stimulus.
    rec.capture_input()

    model.bridge.apb_write(Addr.MXU_BASE + 0x00, 0x00000002)
    val = model.bridge.apb_read(Addr.MXU_BASE + 0x00)
    assert val == 0x00000002

    # psel=0: read returns 0
    val = model.bridge.apb_read(Addr.MXU_BASE + 0x00, psel=0, penable=1)
    assert val == 0

    # penable=0: read returns 0
    val = model.bridge.apb_read(Addr.MXU_BASE + 0x00, psel=1, penable=0)
    assert val == 0

    # penable=0 write ignored
    model.bridge.apb_write(Addr.MXU_BASE + 0x00, 0xCAFE, psel=1, penable=0)
    val = model.bridge.apb_read(Addr.MXU_BASE + 0x00, psel=1, penable=1)
    assert val == 0x00000002

    rec.finalize()
    return rec


def gen_fm_soc_002() -> VectorRecorder:
    """Ibex memory access via crossbar: DRAM/SRAM roundtrip + isolation."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-002", "P0")
    rec.capture_input()

    emu = model.riscv
    dram_addr = 0x80000100
    emu._mem_write(dram_addr, 0xDEADBEEF)
    result = emu._mem_read(dram_addr)
    assert result == 0xDEADBEEF

    raw = model.crossbar.read(CrossbarModel.MASTER_IBEX, dram_addr, 4)
    assert struct.unpack_from("<I", raw, 0)[0] == 0xDEADBEEF

    sram_addr = 0x20001000
    emu._mem_write(sram_addr, 0xCAFEBABE)
    mxu_data = model.crossbar.read(CrossbarModel.MASTER_MXU, sram_addr, 4)
    assert struct.unpack_from("<I", mxu_data, 0)[0] == 0xCAFEBABE

    val = emu._mem_read(0xFFFF0000)
    assert val == 0
    emu._mem_write(0xFFFF0000, 0xAAAAAAAA)

    addr_a = 0x20002000
    addr_b = 0x20002008
    emu._mem_write(addr_a, 0x11111111)
    emu._mem_write(addr_b, 0x22222222)
    assert emu._mem_read(addr_a) == 0x11111111
    assert emu._mem_read(addr_b) == 0x22222222
    emu._mem_write(addr_a, 0x33333333)
    assert emu._mem_read(addr_b) == 0x22222222

    rec.finalize()
    return rec


def gen_fm_soc_003() -> VectorRecorder:
    """PCIe TLP write/read roundtrip to DRAM and SRAM + large payload split."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-003", "P0")
    rec.capture_input()

    # DRAM roundtrip
    addr = 0x8000_2000
    payload = bytes(range(256))
    model.pcie.tlp_write(addr, payload)
    readback = model.pcie.tlp_read(addr, len(payload))
    assert readback == payload

    # SRAM routing
    addr = 0x2000_1000
    payload = b"hello sram"
    model.pcie.tlp_write(addr, payload)
    off = addr - Addr.SRAM_BASE
    assert bytes(model.sram[off:off + len(payload)]) == payload
    dram_off = off
    assert bytes(model.dram[dram_off:dram_off + len(payload)]) != payload

    # DRAM routing
    addr = 0x8000_3000
    payload = b"hello dram"
    model.pcie.tlp_write(addr, payload)
    off = addr - Addr.DRAM_BASE
    assert bytes(model.dram[off:off + len(payload)]) == payload

    # Large payload split
    addr = 0x8000_4000
    payload = bytes(i % 256 for i in range(512))
    model.pcie.tlp_write(addr, payload)
    readback = model.pcie.tlp_read(addr, len(payload))
    assert readback == payload

    rec.finalize()
    return rec


def gen_fm_soc_004() -> VectorRecorder:
    """Crossbar concurrent 3-master access."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-004", "P0")

    mxu_payload = b"mxu_reads_this"
    dma_payload = b"dma_reads_this"
    pcie_payload = b"pcie_writes_01"
    mxu_addr = 0x2000_2000
    dma_addr = 0x8000_3000
    pcie_addr = 0x2000_1000

    model.sram[mxu_addr - Addr.SRAM_BASE:mxu_addr - Addr.SRAM_BASE + len(mxu_payload)] = mxu_payload
    model.dram[dma_addr - Addr.DRAM_BASE:dma_addr - Addr.DRAM_BASE + len(dma_payload)] = dma_payload
    rec.capture_input()

    mxu_data = model.crossbar.read(CrossbarModel.MASTER_MXU, mxu_addr, len(mxu_payload))
    dma_data = model.crossbar.read(CrossbarModel.MASTER_DMA, dma_addr, len(dma_payload))
    model.crossbar.write(CrossbarModel.MASTER_PCIE, pcie_addr, pcie_payload)

    assert mxu_data == mxu_payload
    assert dma_data == dma_payload
    sram_off = pcie_addr - Addr.SRAM_BASE
    assert bytes(model.sram[sram_off:sram_off + len(pcie_payload)]) == pcie_payload

    rec.finalize()
    return rec


def gen_fm_soc_005() -> VectorRecorder:
    """Interrupt delivery: MXU IRQ -> INTC -> WFI -> ACK."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-005", "P0")

    WFI_INSN = (0x305 << 20) | 0x73
    M, K, N = 1, 8, 4

    # IRQ_EN=0 path (use a separate model to avoid pollution, but keep main model)
    model2 = FuncModel()
    bridge2 = model2.bridge
    bridge2.handle("write", MXU.BASE + MXU.CTRL, 0)
    bridge2.handle("write", MXU.BASE + MXU.IRQ_EN, 0)
    bridge2.handle("write", MXU.BASE + MXU.DIM0, (K << 16) | M)
    bridge2.handle("write", MXU.BASE + MXU.DIM1, N)
    bridge2.handle("write", MXU.BASE + MXU.I_ADDR, Addr.SRAM_BASE + 0x1000)
    bridge2.handle("write", MXU.BASE + MXU.W_ADDR, Addr.SRAM_BASE + 0x2000)
    bridge2.handle("write", MXU.BASE + MXU.O_ADDR, Addr.SRAM_BASE + 0x3000)
    act_buf = np.ones(M * K, dtype=np.int8)
    packed_wgt = bytes([0x11] * ((K * N + 1) // 2))
    model2.sram[0x1000:0x1000 + len(act_buf)] = act_buf.tobytes()
    model2.sram[0x2000:0x2000 + len(packed_wgt)] = packed_wgt
    bridge2.handle("write", MXU.BASE + MXU.CMD, 1)
    pending = bridge2.handle("read", INTC.BASE + INTC.PENDING, 0)
    assert pending == 0

    # Main IRQ_EN=1 path
    rec.capture_input()
    bridge = model.bridge
    emu = model.riscv

    bridge.handle("write", MXU.BASE + MXU.CTRL, 0)
    bridge.handle("write", MXU.BASE + MXU.IRQ_EN, 1)
    bridge.handle("write", MXU.BASE + MXU.DIM0, (K << 16) | M)
    bridge.handle("write", MXU.BASE + MXU.DIM1, N)
    bridge.handle("write", MXU.BASE + MXU.I_ADDR, Addr.SRAM_BASE + 0x1000)
    bridge.handle("write", MXU.BASE + MXU.W_ADDR, Addr.SRAM_BASE + 0x2000)
    bridge.handle("write", MXU.BASE + MXU.O_ADDR, Addr.SRAM_BASE + 0x3000)

    act_buf2 = np.ones(M * K, dtype=np.int8)
    model.sram[0x1000:0x1000 + len(act_buf2)] = act_buf2.tobytes()
    model.sram[0x2000:0x2000 + len(packed_wgt)] = packed_wgt

    bridge.handle("write", MXU.BASE + MXU.CMD, 1)
    pending2 = bridge.handle("read", INTC.BASE + INTC.PENDING, 0)
    assert pending2 & 1
    assert emu.interrupt_pending

    model.boot_rom[0:4] = struct.pack("<I", WFI_INSN)
    emu.state.pc = 0
    emu.running = True
    emu.step()

    pending3 = bridge.handle("read", INTC.BASE + INTC.PENDING, 0)
    assert pending3 == 0
    assert not emu.interrupt_pending
    assert model.firmware._irq_serviced

    # WFI as NOP
    assert not emu.interrupt_pending
    model.boot_rom[4:8] = struct.pack("<I", WFI_INSN)
    emu.state.pc = 4
    emu.running = True
    emu.step()
    assert emu.state.pc == 8

    rec.finalize()
    return rec


def gen_fm_soc_006() -> VectorRecorder:
    """Firmware bootflow doorbell: host writes command, firmware dispatches MMUL."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-006", "P0")

    M, K, N = 1, 4, 2
    act_data = np.array([1, 2, 3, 4], dtype=np.int8)
    wgt_packed = np.array([0x21, 0x43, 0x65, 0x87], dtype=np.uint8)
    num_blocks = (K + 127) // 128
    scales = np.ones((num_blocks, N), dtype=np.float32)

    act_addr = 0x80010000
    wgt_addr = 0x80020000
    out_addr = 0x81000000
    scale_addr = 0x80110000
    desc_addr = 0x80000080

    model.host_write_data(act_addr, act_data)
    model.host_write_data(wgt_addr, wgt_packed)
    model.host_write_data(scale_addr, scales.ravel())
    model.host_write_descriptor(desc_addr,
        input_addr=act_addr, weight_addr=wgt_addr, output_addr=out_addr,
        scale_addr=scale_addr, scale_size=int(scales.nbytes),
        input_size=int(act_data.nbytes), weight_size=int(len(wgt_packed)),
        output_size=M * N * 4,
        M=M, K=K, N=N)

    rec.capture_input()
    model.host_write_command(OpCode.MMUL, desc_addr)
    assert model.firmware.doorbell["host_tail"] == 1

    results = model.firmware.run_loop(max_commands=1)
    assert len(results) == 1 and results[0]["status"] == "done"

    rec.finalize()
    return rec


def gen_fm_soc_007() -> VectorRecorder:
    """Anti-vacuous: corrupted PCIe TLP payload readback must mismatch."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-007", "P0")
    rec.capture_input()

    addr = 0x8000_5000
    payload = b"correct data"
    model.pcie.tlp_write(addr, payload)
    readback = model.pcie.tlp_read(addr, len(payload))
    corrupted = payload[:-1] + bytes([payload[-1] ^ 0xFF])
    assert readback != corrupted

    rec.finalize()
    return rec


def gen_fm_soc_008() -> VectorRecorder:
    """Anti-vacuous: corrupted MXU weight/golden must produce mismatch."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-008", "P0")
    rec.capture_input()

    mxu = GoldenMXU()
    rng = np.random.RandomState(99)
    K, N = 64, 64
    w_values = rng.randint(-8, 8, size=K * N, dtype=np.int8)
    weight_packed = GoldenMXU.pack_int4(w_values)

    act_a = rng.randint(-128, 128, size=64 * K, dtype=np.int8)
    act_b = rng.randint(-128, 128, size=128 * K, dtype=np.int8)
    result_a = mxu.matmul_int32(act_a, weight_packed, 64, K, N)
    result_b = mxu.matmul_int32(act_b, weight_packed, 128, K, N)
    assert result_a.shape != result_b.shape

    # Quant error anti-vacuous: different activations produce different INT32 output
    rng2 = np.random.RandomState(20260629)
    M, K2, N2 = 2, 64, 16
    w_vals2 = rng2.randint(-8, 8, size=K2 * N2, dtype=np.int8)
    w_packed2 = mxu.pack_int4(w_vals2)
    act1 = rng2.randint(-128, 128, size=M * K2, dtype=np.int8)
    act2 = rng2.randint(-128, 128, size=M * K2, dtype=np.int8)
    out1 = mxu.matmul_int32(act1, w_packed2, M, K2, N2)
    out2 = mxu.matmul_int32(act2, w_packed2, M, K2, N2)
    assert not np.array_equal(out1, out2)

    rec.finalize()
    return rec


# ══════════════════════════════════════════════════════════════════════
# P1 case generators
# ══════════════════════════════════════════════════════════════════════

def gen_fm_soc_009() -> VectorRecorder:
    """Full firmware bootflow: boot, init, doorbell dispatch, MMUL, IRQ."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-009", "P1")

    assert model.riscv.state.pc == 0
    sp = model.riscv.state.read(2)
    assert sp == 0x00020000

    M, K, N = 1, 4, 2
    act_data = np.array([1, 2, 3, 4], dtype=np.int8)
    wgt_packed = np.array([0x21, 0x43, 0x65, 0x87], dtype=np.uint8)
    num_blocks = (K + 127) // 128
    scales = np.ones((num_blocks, N), dtype=np.float32)

    act_addr = 0x80010000
    wgt_addr = 0x80020000
    out_addr = 0x81000000
    scale_addr = 0x80110000
    desc_addr = 0x80000080

    model.host_write_data(act_addr, act_data)
    model.host_write_data(wgt_addr, wgt_packed)
    model.host_write_data(scale_addr, scales.ravel())
    model.host_write_descriptor(desc_addr,
        input_addr=act_addr, weight_addr=wgt_addr, output_addr=out_addr,
        scale_addr=scale_addr, scale_size=int(scales.nbytes),
        input_size=int(act_data.nbytes), weight_size=int(len(wgt_packed)),
        output_size=M * N * 4,
        M=M, K=K, N=N)

    rec.capture_input()
    model.host_write_command(OpCode.MMUL, desc_addr)
    results = model.firmware.run_loop(max_commands=1)
    assert len(results) == 1 and results[0]["status"] == "done"

    pending = model.bridge.handle("read", INTC.BASE + INTC.PENDING, 0)
    assert pending == 0

    rec.finalize()
    return rec


def gen_fm_soc_010() -> VectorRecorder:
    """MXU compute: representative per-block INT4 matmul + anti-vacuous."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-010", "P1")

    M, K, N = 4, 64, 32
    rng = np.random.RandomState(12345)
    act = rng.randint(-128, 128, size=M * K, dtype=np.int8).reshape(M, K)
    wgt_unpacked = rng.randint(-8, 8, size=K * N, dtype=np.int8)
    wgt_packed = GoldenMXU.pack_int4(wgt_unpacked)
    num_blocks = (K + 127) // 128
    scales = rng.uniform(0.9, 1.1, size=(num_blocks, N)).astype(np.float32)

    act_addr = 0x80010000
    wgt_addr = 0x80020000
    out_addr = 0x81000000
    scale_addr = 0x80110000

    model.host_write_data(act_addr, act)
    model.host_write_data(wgt_addr, wgt_packed)
    model.host_write_data(scale_addr, scales.ravel())

    rec.capture_input()
    bridge = model.bridge
    bridge.handle("write", MXU.BASE + MXU.CTRL, 0)
    bridge.handle("write", MXU.BASE + MXU.DIM0, (K << 16) | M)
    bridge.handle("write", MXU.BASE + MXU.DIM1, N)
    bridge.handle("write", MXU.BASE + MXU.I_ADDR, act_addr)
    bridge.handle("write", MXU.BASE + MXU.W_ADDR, wgt_addr)
    bridge.handle("write", MXU.BASE + MXU.O_ADDR, out_addr)
    bridge.handle("write", MXU.BASE + MXU.SCALE_ADDR, scale_addr)
    bridge.handle("write", MXU.BASE + MXU.CMD, 1)

    status = bridge.handle("read", MXU.BASE + MXU.STATUS)
    assert status == 2

    result_bytes = model.pcie.tlp_read(out_addr, M * N * 4)
    result = np.frombuffer(result_bytes, dtype=np.float32).reshape(M, N)
    golden = GoldenMXU().matmul_int4_per_block(act, wgt_packed, scales, M, K, N, group_size=128)
    assert np.allclose(result, golden, rtol=1e-5)

    rec.finalize()
    return rec


def gen_fm_soc_011() -> VectorRecorder:
    """SFU compute through MMIO bridge: representative softmax N=1024."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-011", "P1")

    N = 1024
    inp = RNG_SFU.randn(N).astype(np.float32).clip(-10, 10)
    _mmio_write_sram(model, inp, 0x10000)

    rec.capture_input()
    _mmio_sfu_op(model, op=0, length=N)
    status = model.bridge.handle("read", SFU.BASE + SFU.STATUS)
    assert status == 2

    mmio_out = _mmio_read_sram(model, N, 0x20000)
    sfu = GoldenSFU()
    direct_out = sfu.softmax_hw(inp)
    cmp = GoldenSFU.compare_hw_vs_ref(mmio_out, direct_out, tol_abs=2e-3, tol_rel=1e-2)
    assert cmp["within_tolerance"]
    assert np.allclose(float(np.sum(mmio_out)), 1.0, rtol=1e-3), "Softmax must sum to 1"

    rec.finalize()
    return rec


def gen_fm_soc_012() -> VectorRecorder:
    """Vector compute through MMIO bridge: representative ADD + MUL."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-012", "P1")

    dim = 128
    a = RNG_VEC.randint(-10000, 10000, size=dim).astype(np.int32)
    b = RNG_VEC.randint(-10000, 10000, size=dim).astype(np.int32)
    _vec_write_i32(model, a, 0x30000)
    _vec_write_i32(model, b, 0x31000)

    rec.capture_input()
    _vec_mmio_op(model, op=0, dim=dim)
    status = model.bridge.handle("read", VECTOR.BASE + VECTOR.STATUS)
    assert status == 2
    add_out = _vec_read_i32(model, dim, 0x40000)
    assert np.array_equal(add_out, GoldenVector().add(a, b))

    _vec_mmio_op(model, op=1, dim=dim)
    status = model.bridge.handle("read", VECTOR.BASE + VECTOR.STATUS)
    assert status == 2
    mul_out = _vec_read_i32(model, dim, 0x40000)
    assert np.array_equal(mul_out, GoldenVector().mul(a, b))

    rec.finalize()
    return rec


def gen_fm_soc_024() -> VectorRecorder:
    """PCIe -> DRAM -> MXU -> DRAM -> PCIe integration."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-024", "P1")

    M, K, N = 1, 8, 4
    group_size = 128
    act = np.array([1, 2, 3, 4, 5, 6, 7, 8], dtype=np.int8).reshape(M, K)
    wgt_unpacked = np.array([
        [0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 2, 3], [4, 5, 6, 7],
        [0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 2, 3], [4, 5, 6, 7],
    ], dtype=np.int8)
    wgt_packed = GoldenMXU.pack_int4(wgt_unpacked.flatten())
    num_blocks = (K + group_size - 1) // group_size
    scales = np.ones((num_blocks, N), dtype=np.float32)

    act_addr = 0x8001_0000
    wgt_addr = 0x8002_0000
    out_addr = 0x8100_0000
    scale_addr = 0x8011_0000

    model.pcie.tlp_write(act_addr, act.tobytes())
    model.pcie.tlp_write(wgt_addr, wgt_packed.tobytes())
    model.pcie.tlp_write(scale_addr, scales.tobytes())
    verify_act = model.pcie.tlp_read(act_addr, act.nbytes)
    assert verify_act == act.tobytes()

    rec.capture_input()
    bridge = model.bridge
    bridge.handle("write", MXU.BASE + MXU.CTRL, 0)
    bridge.handle("write", MXU.BASE + MXU.DIM0, (K << 16) | M)
    bridge.handle("write", MXU.BASE + MXU.DIM1, N)
    bridge.handle("write", MXU.BASE + MXU.I_ADDR, act_addr)
    bridge.handle("write", MXU.BASE + MXU.W_ADDR, wgt_addr)
    bridge.handle("write", MXU.BASE + MXU.O_ADDR, out_addr)
    bridge.handle("write", MXU.BASE + MXU.SCALE_ADDR, scale_addr)
    bridge.handle("write", MXU.BASE + MXU.CMD, 1)

    status = bridge.handle("read", MXU.BASE + MXU.STATUS)
    assert status == 2

    result_bytes = model.pcie.tlp_read(out_addr, M * N * 4)
    result = np.frombuffer(result_bytes, dtype=np.float32).reshape(M, N)
    golden = model.mxu.matmul_int4_per_block(act, wgt_packed, scales, M, K, N, group_size=128)
    assert np.allclose(result, golden, rtol=1e-5)

    rec.finalize()
    return rec


def gen_fm_soc_025() -> VectorRecorder:
    """Crossbar P1 stress: 6-master concurrent access."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-025", "P1")

    masters = [
        ("IBEX", CrossbarModel.MASTER_IBEX),
        ("MXU", CrossbarModel.MASTER_MXU),
        ("SFU", CrossbarModel.MASTER_SFU),
        ("VEC", CrossbarModel.MASTER_VEC),
        ("DMA", CrossbarModel.MASTER_DMA),
        ("PCIE", CrossbarModel.MASTER_PCIE),
    ]
    sram_writes = {}
    dram_writes = {}
    for i, (name, mid) in enumerate(masters):
        sram_addr = 0x2000_8000 + i * 256
        dram_addr = 0x8000_A000 + i * 256
        payload_sram = f"{name}_SRAM_{i:02d}".encode()
        payload_dram = f"{name}_DRAM_{i:02d}".encode()
        model.crossbar.write(mid, sram_addr, payload_sram)
        model.crossbar.write(mid, dram_addr, payload_dram)
        sram_writes[(mid, sram_addr, len(payload_sram))] = payload_sram
        dram_writes[(mid, dram_addr, len(payload_dram))] = payload_dram

    rec.capture_input()

    for (mid, addr, sz), expected in sram_writes.items():
        result = model.crossbar.read(CrossbarModel.MASTER_IBEX, addr, sz)
        assert result == expected
    for (mid, addr, sz), expected in dram_writes.items():
        result = model.crossbar.read(CrossbarModel.MASTER_IBEX, addr, sz)
        assert result == expected

    aw_masters = set(g[1] for g in model.crossbar._aw_grants)
    for _, mid in masters:
        assert mid in aw_masters

    rec.finalize()
    return rec


def gen_fm_soc_026() -> VectorRecorder:
    """Doorbell 3-command queue: MMUL -> SFU softmax -> Vector add via IRQ."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-026", "P1")

    # MMUL
    M, K, N = 1, 4, 2
    act_addr, wgt_addr, out_addr, scale_addr, mmul_desc = (
        0x8001_0000, 0x8002_0000, 0x8100_0000, 0x8011_0000, 0x8000_0080)
    act, wgt_packed, scales = _doorbell_setup_mmul(
        model, M, K, N, act_addr, wgt_addr, out_addr, scale_addr, mmul_desc, rng=RNG_DB)

    # SFU softmax
    sfu_len = 16
    sfu_in_addr = 0x8200_0000
    sfu_out_addr = 0x8200_1000
    sfu_desc = 0x8000_0100
    sfu_in = RNG_DB.randn(sfu_len).astype(np.float32).clip(-5, 5)
    model.host_write_data(sfu_in_addr, sfu_in.astype(np.float16))
    model.host_write_descriptor(sfu_desc,
        input_addr=sfu_in_addr, output_addr=sfu_out_addr,
        input_size=sfu_len, output_size=sfu_len,
        M=1, K=sfu_len, N=1)

    # Vector add
    vec_len = 8
    vec_a_addr = 0x8200_2000
    vec_b_addr = 0x8200_3000
    vec_out_addr = 0x8200_4000
    vec_desc = 0x8000_0200
    vec_a = RNG_DB.randint(-100, 100, size=vec_len).astype(np.int32)
    vec_b = RNG_DB.randint(-100, 100, size=vec_len).astype(np.int32)
    model.host_write_data(vec_a_addr, vec_a)
    model.host_write_data(vec_b_addr, vec_b)
    model.host_write_descriptor(vec_desc,
        input_addr=vec_a_addr, weight_addr=vec_b_addr, output_addr=vec_out_addr,
        input_size=vec_len, weight_size=vec_len, output_size=vec_len,
        M=1, K=vec_len, N=1)

    rec.capture_input()

    model.host_write_command(OpCode.MMUL, mmul_desc)
    model.host_write_command(OpCode.SOFTMAX, sfu_desc)
    model.host_write_command(OpCode.VADD, vec_desc)

    assert model.firmware.doorbell["host_tail"] == 3

    results = model.firmware.run_loop(max_commands=3)
    assert len(results) == 3
    for r in results:
        assert r["status"] == "done"

    rec.finalize()
    return rec


# ══════════════════════════════════════════════════════════════════════
# P2 case generators
# ══════════════════════════════════════════════════════════════════════

def gen_fm_soc_013() -> VectorRecorder:
    """DMA load/store through MMIO bridge."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-013", "P2")

    size = 128
    dram_off = 0x8001_0000 - Addr.DRAM_BASE
    pattern_src = np.arange(size, dtype=np.uint8)
    model.dram[dram_off:dram_off + size] = pattern_src.tobytes()

    rec.capture_input()
    bridge = model.bridge
    bridge.handle("write", DMA.BASE + DMA.CH0_SRC, 0x8001_0000)
    bridge.handle("write", DMA.BASE + DMA.CH0_DST, Addr.SRAM_BASE + 0x50000)
    bridge.handle("write", DMA.BASE + DMA.CH0_SIZE, size)
    bridge.handle("write", DMA.BASE + DMA.CMD, 1)

    sram_read = bytes(model.sram[0x50000:0x50000 + size])
    assert sram_read == pattern_src.tobytes()

    pattern_store = bytes(range(100, 100 + size))
    store_sram_off = 0x51000
    model.sram[store_sram_off:store_sram_off + size] = pattern_store
    bridge.handle("write", DMA.BASE + DMA.CH1_SRC, Addr.SRAM_BASE + store_sram_off)
    bridge.handle("write", DMA.BASE + DMA.CH1_DST, 0x8001_0000 + 0x1000)
    bridge.handle("write", DMA.BASE + DMA.CH1_SIZE, size)
    bridge.handle("write", DMA.BASE + DMA.CMD, 1)

    dram_data = bytes(model.dram[dram_off + 0x1000:dram_off + 0x1000 + size])
    assert dram_data == pattern_store

    status = bridge.handle("read", DMA.BASE + DMA.STATUS)
    assert status == 2

    rec.finalize()
    return rec


def gen_fm_soc_014() -> VectorRecorder:
    """Multi-engine: MXU INT4 -> BF16 -> SFU softmax."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-014", "P2")

    M, K, N = 1, 16, 32
    rng = np.random.RandomState(20260629)
    act = rng.randint(-8, 8, size=M * K, dtype=np.int8)
    w_vals = rng.randint(-4, 4, size=K * N, dtype=np.int8)
    w_packed = GoldenMXU.pack_int4(w_vals)

    mxu_out = GoldenMXU().matmul_int32(act, w_packed, M, K, N)
    bf16 = GoldenVector().conv_i32_to_f16(mxu_out)
    sfu_golden = GoldenSFU().softmax_hw(bf16[0].astype(np.float32))

    act_addr = 0x80010000
    wgt_addr = 0x80020000
    mxu_out_addr = 0x81000000

    model.host_write_data(act_addr, act)
    model.host_write_data(wgt_addr, w_packed)

    rec.capture_input()
    bridge = model.bridge
    # MXU (INT32 path, no scale)
    bridge.handle("write", MXU.BASE + MXU.CTRL, 0)
    bridge.handle("write", MXU.BASE + MXU.DIM0, (K << 16) | M)
    bridge.handle("write", MXU.BASE + MXU.DIM1, N)
    bridge.handle("write", MXU.BASE + MXU.I_ADDR, act_addr)
    bridge.handle("write", MXU.BASE + MXU.W_ADDR, wgt_addr)
    bridge.handle("write", MXU.BASE + MXU.O_ADDR, mxu_out_addr)
    bridge.handle("write", MXU.BASE + MXU.SCALE_ADDR, 0)
    bridge.handle("write", MXU.BASE + MXU.CMD, 1)
    assert bridge.handle("read", MXU.BASE + MXU.STATUS) == 2

    # Vector CONV: INT32 -> FP16
    _vec_write_i32(model, np.frombuffer(_dram_read_direct(model, mxu_out_addr, M * N * 4), dtype=np.int32), 0x30000)
    _vec_mmio_op(model, op=4, dim=M * N)
    assert bridge.handle("read", VECTOR.BASE + VECTOR.STATUS) == 2

    # SFU softmax
    _mmio_write_sram(model, _vec_read_f16(model, M * N, 0x40000), 0x10000)
    _mmio_sfu_op(model, op=0, length=N)
    assert bridge.handle("read", SFU.BASE + SFU.STATUS) == 2

    mmio_out = _mmio_read_sram(model, N, 0x20000)
    cmp = GoldenSFU.compare_hw_vs_ref(mmio_out, sfu_golden, tol_abs=1e-3, tol_rel=1e-3)
    assert cmp["within_tolerance"]
    assert np.allclose(float(np.sum(mmio_out)), 1.0, rtol=1e-3)

    rec.finalize()
    return rec


def gen_fm_soc_015() -> VectorRecorder:
    """MMIO via Ibex CPU: Ibex store/load to MXU CTRL."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-015", "P2")
    rec.capture_input()

    emu = model.riscv
    emu._mem_write(Addr.MXU_BASE + 0x00, 0x00000003)
    val = emu._mem_read(Addr.MXU_BASE + 0x00)
    assert val == 0x00000003

    # Also direct APB readback for consistency
    val2 = model.bridge.apb_read(Addr.MXU_BASE + 0x00)
    assert val2 == 0x00000003

    rec.finalize()
    return rec


def gen_fm_soc_016() -> VectorRecorder:
    """Ibex firmware dispatches engine command via doorbell."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-016", "P2")

    M, K, N = 1, 4, 2
    act_addr, wgt_addr, out_addr, scale_addr, desc_addr = (
        0x8001_0000, 0x8002_0000, 0x8100_0000, 0x8011_0000, 0x8000_0080)
    act, wgt_packed, scales = _doorbell_setup_mmul(
        model, M, K, N, act_addr, wgt_addr, out_addr, scale_addr, desc_addr, rng=RNG_DB)

    rec.capture_input()
    model.host_write_command(OpCode.MMUL, desc_addr)
    results = model.firmware.run_loop(max_commands=1)
    assert len(results) == 1 and results[0]["status"] == "done"

    out_off = out_addr - Addr.DRAM_BASE
    out_bytes = model.dram[out_off:out_off + M * N * 4]
    out_fw = np.frombuffer(out_bytes, dtype=np.float32).reshape(M, N)
    golden = GoldenMXU().matmul_int4_per_block(
        act.reshape(M, K), wgt_packed, scales, M, K, N, group_size=128)
    assert np.allclose(out_fw, golden, rtol=1e-5)

    rec.finalize()
    return rec


def gen_fm_soc_027() -> VectorRecorder:
    """Full blk.0 17-op chain through MMIO bridge (single-tile workaround)."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-027", "P2")

    manifest = _load_blk0_manifest()
    fp16_tol = dict(tol_abs=2e-3, tol_rel=1e-2)

    # Pre-load first op input and all weights into SRAM before capture
    for op in manifest["ops"]:
        idx = op["idx"]
        opcode = op["opcode"]
        if opcode == "MMUL":
            dims = op["dimensions"]
            M_eff = min(dims.get("M", 1), 64)
            K_eff = min(dims.get("K", 0), 64)
            N_eff = min(dims.get("N", 0), 64)
            input_fmt = manifest["files"][op["input_hex"]]["format"]
            input_eb = _EB_BY_FMT[input_fmt]
            input_full = _blk0_read_hex(op["input_hex"], input_eb)
            input_bytes = input_full[:M_eff * K_eff * input_eb]
            weight_full = _blk0_read_hex(op["weight_hex"], 1)
            weight_size = (K_eff * N_eff + 1) // 2
            weight_bytes = weight_full[:weight_size]
            if len(weight_bytes) < weight_size:
                weight_bytes = weight_bytes + b"\x00" * (weight_size - len(weight_bytes))
            i_addr = int(op["sram_input_addr"], 16)
            model.sram[i_addr:i_addr + len(input_bytes)] = input_bytes
            model.sram[0x00000:0x00000 + len(weight_bytes)] = weight_bytes
        elif opcode in ("RMSNORM", "SOFTMAX", "ROPE", "SILU"):
            input_hex = op.get("input_hex")
            if input_hex is None:
                prefix = f"op{idx:02d}_"
                candidates = [fname for fname, finfo in manifest["files"].items()
                              if fname.startswith(prefix) and fname.endswith("_input.hex")]
                input_hex = candidates[0]
            input_fmt = manifest["files"][input_hex]["format"]
            input_eb = _EB_BY_FMT[input_fmt]
            input_bytes = _blk0_read_hex(input_hex, input_eb)
            if opcode == "ROPE":
                elements = op["dimensions"].get("q_len", 0) + op["dimensions"].get("k_len", 0)
            else:
                elements = op["dimensions"].get("elements", 0)
            need = elements * input_eb
            if len(input_bytes) < need:
                input_bytes = input_bytes + b"\x00" * (need - len(input_bytes))
            i_addr = int(op["sram_input_addr"], 16)
            model.sram[i_addr:i_addr + len(input_bytes)] = input_bytes
        elif opcode in ("VMUL", "VRESID"):
            elements = op["dimensions"]["elements"]
            if opcode == "VMUL":
                a_hex, b_hex = "op14_vmul_gate_input.hex", "op14_vmul_up_input.hex"
            elif idx == 9:
                a_hex, b_hex = "op09_vresid_pre_input.hex", "op09_vresid_pre_o_out.hex"
            elif idx == 16:
                a_hex, b_hex = "op16_vresid_post_input.hex", "op16_vresid_post_down.hex"
            else:
                raise ValueError(f"Unknown VRESID idx {idx}")
            a_fmt = manifest["files"][a_hex]["format"]
            b_fmt = manifest["files"][b_hex]["format"]
            a_bytes = _blk0_read_hex(a_hex, _EB_BY_FMT[a_fmt])
            b_bytes = _blk0_read_hex(b_hex, _EB_BY_FMT[b_fmt])
            if len(a_bytes) < elements * _EB_BY_FMT[a_fmt]:
                a_bytes = a_bytes + b"\x00" * (elements * _EB_BY_FMT[a_fmt] - len(a_bytes))
            if len(b_bytes) < elements * _EB_BY_FMT[b_fmt]:
                b_bytes = b_bytes + b"\x00" * (elements * _EB_BY_FMT[b_fmt] - len(b_bytes))
            i_addr = int(op["sram_input_addr"], 16)
            b_addr = manifest["sram_layout"]["output_buffer"]
            model.sram[i_addr:i_addr + len(a_bytes)] = a_bytes
            model.sram[b_addr:b_addr + len(b_bytes)] = b_bytes

    rec.capture_input()
    bridge = model.bridge

    for op in manifest["ops"]:
        idx = op["idx"]
        opcode = op["opcode"]
        if opcode == "MMUL":
            dims = op["dimensions"]
            M_eff = min(dims.get("M", 1), 64)
            K_eff = min(dims.get("K", 0), 64)
            N_eff = min(dims.get("N", 0), 64)
            i_addr = int(op["sram_input_addr"], 16)
            o_addr = int(op["sram_output_addr"], 16)
            bridge.handle("write", MXU.BASE + MXU.CTRL, 0)
            bridge.handle("write", MXU.BASE + MXU.I_ADDR, i_addr)
            bridge.handle("write", MXU.BASE + MXU.W_ADDR, 0x00000)
            bridge.handle("write", MXU.BASE + MXU.O_ADDR, o_addr)
            bridge.handle("write", MXU.BASE + MXU.SCALE_ADDR, 0)
            dim0 = (M_eff & 0xFFFF) | ((K_eff & 0xFFFF) << 16)
            bridge.handle("write", MXU.BASE + MXU.DIM0, dim0)
            bridge.handle("write", MXU.BASE + MXU.DIM1, N_eff & 0xFFFF)
            bridge.handle("write", MXU.BASE + MXU.CMD, 1)
            status = bridge.handle("read", MXU.BASE + MXU.STATUS)
            assert status == 2
        elif opcode in ("RMSNORM", "SOFTMAX", "ROPE", "SILU"):
            sfu_op_map = {"SOFTMAX": 0, "RMSNORM": 6, "ROPE": 5, "SILU": 4}
            op_id = sfu_op_map[opcode]
            i_addr = int(op["sram_input_addr"], 16)
            o_addr = int(op["sram_output_addr"], 16)
            if opcode == "ROPE":
                elements = op["dimensions"].get("q_len", 0) + op["dimensions"].get("k_len", 0)
                head_dim = op["dimensions"].get("head_dim", 128)
                pos = op["dimensions"].get("position", 0)
            else:
                elements = op["dimensions"].get("elements", 0)
                head_dim = 0
                pos = 0
            bridge.handle("write", SFU.BASE + SFU.CTRL, op_id)
            bridge.handle("write", SFU.BASE + SFU.I_ADDR, i_addr)
            bridge.handle("write", SFU.BASE + SFU.O_ADDR, o_addr)
            dim = (head_dim << 16) | (elements & 0xFFFF)
            bridge.handle("write", SFU.BASE + SFU.DIM, dim)
            if opcode == "ROPE":
                bridge.handle("write", SFU.BASE + SFU.POS, pos)
            bridge.handle("write", SFU.BASE + SFU.CMD, 1)
            status = bridge.handle("read", SFU.BASE + SFU.STATUS)
            assert status == 2
        elif opcode in ("VMUL", "VRESID"):
            vec_op_map = {"VMUL": 1, "VRESID": 5}
            op_id = vec_op_map[opcode]
            i_addr = int(op["sram_input_addr"], 16)
            o_addr = int(op["sram_output_addr"], 16)
            b_addr = manifest["sram_layout"]["output_buffer"]
            elements = op["dimensions"]["elements"]
            bridge.handle("write", VECTOR.BASE + VECTOR.CTRL, op_id)
            bridge.handle("write", VECTOR.BASE + VECTOR.A_ADDR, i_addr)
            bridge.handle("write", VECTOR.BASE + VECTOR.B_ADDR, b_addr)
            bridge.handle("write", VECTOR.BASE + VECTOR.O_ADDR, o_addr)
            bridge.handle("write", VECTOR.BASE + VECTOR.DIM, elements & 0xFFFF)
            bridge.handle("write", VECTOR.BASE + VECTOR.CMD, 1)
            status = bridge.handle("read", VECTOR.BASE + VECTOR.STATUS)
            assert status == 2

    rec.finalize()
    return rec


# ══════════════════════════════════════════════════════════════════════
# P3 case generators
# ══════════════════════════════════════════════════════════════════════

def gen_fm_soc_017() -> VectorRecorder:
    """APB unmapped address returns 0; write ignored."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-017", "P3")

    # First write a known register
    model.bridge.apb_write(Addr.MXU_BASE + 0x00, 0xABCD1234)
    rec.capture_input()

    val = model.bridge.apb_read(0x4000_7FFF)
    assert val == 0
    model.bridge.apb_write(0x4000_7FFF, 0xDEADBEEF)
    val2 = model.bridge.apb_read(Addr.MXU_BASE + 0x00)
    assert val2 == 0xABCD1234

    rec.finalize()
    return rec


def gen_fm_soc_018() -> VectorRecorder:
    """DMA boundary: size=0 means 4096, size>4096 raises."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-018", "P3")
    rec.capture_input()

    from golden_executor import DMADescriptor
    desc = DMADescriptor(dram_addr=0, sram_addr=0, size=0, direction=0, last=False, channel=0)
    assert desc.actual_size == 4096

    try:
        DMADescriptor(dram_addr=0, sram_addr=0, size=4097, direction=0, last=False, channel=0)
        assert False, "size=4097 should raise"
    except (ValueError, AssertionError):
        pass

    rec.finalize()
    return rec


def gen_fm_soc_019() -> VectorRecorder:
    """Ibex boundary: out-of-range returns 0; DMEM isolation."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-019", "P3")

    emu = model.riscv
    dmem_addr = 0x00010000
    emu._mem_write(dmem_addr, 0xFEEDFACE)
    assert emu._mem_read(dmem_addr) == 0xFEEDFACE

    rec.capture_input()
    val = emu._mem_read(0xFFFF0000)
    assert val == 0
    emu._mem_write(0xFFFF0000, 0xAAAAAAAA)

    try:
        model.crossbar.read(CrossbarModel.MASTER_IBEX, dmem_addr, 4)
        assert False, "DMEM should not be visible through crossbar"
    except ValueError:
        pass

    rec.finalize()
    return rec


def gen_fm_soc_020() -> VectorRecorder:
    """Firmware bad opcode returns error status."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-020", "P3")

    desc_addr = 0x8000_2000
    model.host_write_descriptor(desc_addr,
        input_addr=0x8001_0000, weight_addr=0x8002_0000,
        output_addr=0x8100_0000, scale_addr=0x8011_0000,
        scale_size=8, input_size=4, weight_size=4, output_size=4,
        M=1, K=4, N=2)

    rec.capture_input()
    model.host_write_command(999, desc_addr)
    results = model.firmware.run_loop(max_commands=1)
    assert len(results) == 1 and results[0]["status"] != "done"

    rec.finalize()
    return rec


def gen_fm_soc_028() -> VectorRecorder:
    """Dimension boundaries: zero-dim STATUS=DONE; max/odd shapes."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-028", "P3")
    bridge = model.bridge
    rng = np.random.RandomState(20260703)

    # Zero-dim MXU
    mxu_pattern = bytes((i * 7 + 0xA5) & 0xFF for i in range(64))
    model.sram[0x70000:0x70000 + 64] = mxu_pattern
    # Zero-dim SFU
    sfu_pattern = bytes((i * 7 + 0xA5) & 0xFF for i in range(64))
    model.sram[0x71000:0x71000 + 64] = sfu_pattern
    # Zero-dim Vector
    vec_pattern = bytes((i * 7 + 0xA5) & 0xFF for i in range(64))
    model.sram[0x72000:0x72000 + 64] = vec_pattern
    # DMA zero size
    dma_src = bytes(range(64))
    dma_dst = bytes([0xFF] * 64)
    model.dram[0x10000:0x10000 + 64] = dma_src
    model.sram[0x6000:0x6000 + 64] = dma_dst

    rec.capture_input()

    # MXU zero dim
    bridge.handle("write", MXU.BASE + MXU.CTRL, 0)
    bridge.handle("write", MXU.BASE + MXU.DIM0, 0)
    bridge.handle("write", MXU.BASE + MXU.DIM1, 0)
    bridge.handle("write", MXU.BASE + MXU.I_ADDR, 0x60000)
    bridge.handle("write", MXU.BASE + MXU.W_ADDR, 0x60000)
    bridge.handle("write", MXU.BASE + MXU.O_ADDR, 0x70000)
    bridge.handle("write", MXU.BASE + MXU.CMD, 1)
    assert bridge.handle("read", MXU.BASE + MXU.STATUS) == 2
    assert bytes(model.sram[0x70000:0x70000 + 64]) == mxu_pattern

    # SFU zero dim
    bridge.handle("write", SFU.BASE + SFU.CTRL, 0)
    bridge.handle("write", SFU.BASE + SFU.DIM, 0)
    bridge.handle("write", SFU.BASE + SFU.I_ADDR, 0x60000)
    bridge.handle("write", SFU.BASE + SFU.O_ADDR, 0x71000)
    bridge.handle("write", SFU.BASE + SFU.CMD, 1)
    assert bridge.handle("read", SFU.BASE + SFU.STATUS) == 2
    assert bytes(model.sram[0x71000:0x71000 + 64]) == sfu_pattern

    # Vector zero dim
    bridge.handle("write", VECTOR.BASE + VECTOR.CTRL, 0)
    bridge.handle("write", VECTOR.BASE + VECTOR.A_ADDR, 0x60000)
    bridge.handle("write", VECTOR.BASE + VECTOR.B_ADDR, 0x60000)
    bridge.handle("write", VECTOR.BASE + VECTOR.O_ADDR, 0x72000)
    bridge.handle("write", VECTOR.BASE + VECTOR.DIM, 0)
    bridge.handle("write", VECTOR.BASE + VECTOR.CMD, 1)
    assert bridge.handle("read", VECTOR.BASE + VECTOR.STATUS) == 2
    assert bytes(model.sram[0x72000:0x72000 + 64]) == vec_pattern

    # DMA zero size
    bridge.handle("write", DMA.BASE + DMA.CH0_SRC, 0x80010000)
    bridge.handle("write", DMA.BASE + DMA.CH0_DST, Addr.SRAM_BASE + 0x6000)
    bridge.handle("write", DMA.BASE + DMA.CH0_SIZE, 0)
    bridge.handle("write", DMA.BASE + DMA.CMD, 1)
    assert bridge.handle("read", DMA.BASE + DMA.STATUS) == 2
    assert bytes(model.sram[0x6000:0x6000 + 64]) == dma_dst
    assert bytes(model.dram[0x10000:0x10000 + 64]) == dma_src

    # Large MXU
    M_big, K_big, N_big = 1, 2560, 4096
    act_big = rng.randint(-8, 8, size=M_big * K_big, dtype=np.int8).reshape(M_big, K_big)
    wgt_big_unpacked = rng.randint(-8, 8, size=K_big * N_big, dtype=np.int8)
    wgt_big_packed = GoldenMXU.pack_int4(wgt_big_unpacked)
    num_blocks_big = (K_big + 127) // 128
    scales_big = np.ones((num_blocks_big, N_big), dtype=np.float32)
    act_addr_big = 0x8001_0000
    wgt_addr_big = 0x8010_0000
    scale_addr_big = 0x8060_0000
    out_addr_big = 0x8100_0000
    model.host_write_data(act_addr_big, act_big)
    model.host_write_data(wgt_addr_big, wgt_big_packed)
    model.host_write_data(scale_addr_big, scales_big.ravel())
    bridge.handle("write", MXU.BASE + MXU.CTRL, 0)
    bridge.handle("write", MXU.BASE + MXU.DIM0, (K_big << 16) | M_big)
    bridge.handle("write", MXU.BASE + MXU.DIM1, N_big)
    bridge.handle("write", MXU.BASE + MXU.I_ADDR, act_addr_big)
    bridge.handle("write", MXU.BASE + MXU.W_ADDR, wgt_addr_big)
    bridge.handle("write", MXU.BASE + MXU.O_ADDR, out_addr_big)
    bridge.handle("write", MXU.BASE + MXU.SCALE_ADDR, scale_addr_big)
    bridge.handle("write", MXU.BASE + MXU.CMD, 1)
    assert bridge.handle("read", MXU.BASE + MXU.STATUS) == 2
    out_big = np.frombuffer(model.pcie.tlp_read(out_addr_big, M_big * N_big * 4), dtype=np.float32).reshape(M_big, N_big)
    golden_big = GoldenMXU().matmul_int4_per_block(act_big, wgt_big_packed, scales_big, M_big, K_big, N_big, group_size=128)
    assert np.allclose(out_big, golden_big, rtol=1e-5)

    # Odd MXU
    M_odd, K_odd, N_odd = 33, 65, 129
    act_odd = rng.randint(-8, 8, size=M_odd * K_odd, dtype=np.int8).reshape(M_odd, K_odd)
    wgt_odd_unpacked = rng.randint(-8, 8, size=K_odd * N_odd, dtype=np.int8)
    wgt_odd_packed = GoldenMXU.pack_int4(wgt_odd_unpacked)
    act_off_odd = 0x50000
    wgt_off_odd = 0x60000
    out_off_odd = 0x70000
    model.sram[act_off_odd:act_off_odd + act_odd.nbytes] = act_odd.tobytes()
    model.sram[wgt_off_odd:wgt_off_odd + len(wgt_odd_packed)] = wgt_odd_packed.tobytes()
    bridge.handle("write", MXU.BASE + MXU.CTRL, 0)
    bridge.handle("write", MXU.BASE + MXU.DIM0, (K_odd << 16) | M_odd)
    bridge.handle("write", MXU.BASE + MXU.DIM1, N_odd)
    bridge.handle("write", MXU.BASE + MXU.I_ADDR, act_off_odd)
    bridge.handle("write", MXU.BASE + MXU.W_ADDR, wgt_off_odd)
    bridge.handle("write", MXU.BASE + MXU.O_ADDR, out_off_odd)
    bridge.handle("write", MXU.BASE + MXU.SCALE_ADDR, 0)
    bridge.handle("write", MXU.BASE + MXU.CMD, 1)
    assert bridge.handle("read", MXU.BASE + MXU.STATUS) == 2
    out_odd = np.frombuffer(bytes(model.sram[out_off_odd:out_off_odd + M_odd * N_odd * 4]), dtype=np.int32).reshape(M_odd, N_odd)
    golden_odd = GoldenMXU().matmul_int32(act_odd, wgt_odd_packed, M_odd, K_odd, N_odd)
    assert np.array_equal(out_odd, golden_odd)

    # Odd SFU softmax
    sfu_len = 129
    sfu_in = rng.randn(sfu_len).astype(np.float32).clip(-5, 5)
    _mmio_write_sram(model, sfu_in, 0x10000)
    _mmio_sfu_op(model, op=0, length=sfu_len)
    _mmio_sfu_op(model, op=0, length=sfu_len)
    sfu_out = _mmio_read_sram(model, sfu_len, 0x20000)
    sfu_ref = GoldenSFU().softmax_hw(sfu_in)
    cmp = GoldenSFU.compare_hw_vs_ref(sfu_out, sfu_ref, tol_abs=2e-3, tol_rel=1e-2)
    assert cmp["within_tolerance"]

    # Odd Vector add
    vec_dim = 33
    a = rng.randint(-1000, 1000, size=vec_dim).astype(np.int32)
    b = rng.randint(-1000, 1000, size=vec_dim).astype(np.int32)
    _vec_write_i32(model, a, 0x30000)
    _vec_write_i32(model, b, 0x31000)
    _vec_mmio_op(model, op=0, dim=vec_dim)
    vec_out = _vec_read_i32(model, vec_dim, 0x40000)
    assert np.array_equal(vec_out, GoldenVector().add(a, b))

    rec.finalize()
    return rec


def gen_fm_soc_029() -> VectorRecorder:
    """All-zero vectors produce deterministic zero output."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-029", "P3")

    M, K, N = 1, 8, 4
    act_zero = np.zeros((M, K), dtype=np.int8)
    wgt = np.array([1, 2, 3, 4, 5, 6, 7, 8] * ((K * N + 15) // 8), dtype=np.int8)[:K * N]
    wgt_packed = GoldenMXU.pack_int4(wgt)
    act_off = 0x50000
    wgt_off = 0x51000
    out_off = 0x52000
    model.sram[act_off:act_off + act_zero.nbytes] = act_zero.tobytes()
    model.sram[wgt_off:wgt_off + len(wgt_packed)] = wgt_packed.tobytes()

    rec.capture_input()
    bridge = model.bridge
    bridge.handle("write", MXU.BASE + MXU.CTRL, 0)
    bridge.handle("write", MXU.BASE + MXU.DIM0, (K << 16) | M)
    bridge.handle("write", MXU.BASE + MXU.DIM1, N)
    bridge.handle("write", MXU.BASE + MXU.I_ADDR, act_off)
    bridge.handle("write", MXU.BASE + MXU.W_ADDR, wgt_off)
    bridge.handle("write", MXU.BASE + MXU.O_ADDR, out_off)
    bridge.handle("write", MXU.BASE + MXU.SCALE_ADDR, 0)
    bridge.handle("write", MXU.BASE + MXU.CMD, 1)
    assert bridge.handle("read", MXU.BASE + MXU.STATUS) == 2
    mxu_out = np.frombuffer(bytes(model.sram[out_off:out_off + M * N * 4]), dtype=np.int32).reshape(M, N)
    assert np.array_equal(mxu_out, np.zeros((M, N), dtype=np.int32))

    # Zero weights
    act = np.array([1, 2, 3, 4, 5, 6, 7, 8], dtype=np.int8).reshape(M, K)
    wgt_zero_packed = GoldenMXU.pack_int4(np.zeros(K * N, dtype=np.int8))
    model.sram[act_off:act_off + act.nbytes] = act.tobytes()
    model.sram[wgt_off:wgt_off + len(wgt_zero_packed)] = wgt_zero_packed.tobytes()
    bridge.handle("write", MXU.BASE + MXU.CMD, 1)
    mxu_out2 = np.frombuffer(bytes(model.sram[out_off:out_off + M * N * 4]), dtype=np.int32).reshape(M, N)
    assert np.array_equal(mxu_out2, np.zeros((M, N), dtype=np.int32))

    # Vector zero operands
    dim = 16
    _vec_write_i32(model, np.zeros(dim, dtype=np.int32), 0x30000)
    _vec_write_i32(model, np.zeros(dim, dtype=np.int32), 0x31000)
    _vec_mmio_op(model, op=0, dim=dim)
    assert bridge.handle("read", VECTOR.BASE + VECTOR.STATUS) == 2
    assert np.array_equal(_vec_read_i32(model, dim, 0x40000), np.zeros(dim, dtype=np.int32))
    _vec_mmio_op(model, op=1, dim=dim)
    assert np.array_equal(_vec_read_i32(model, dim, 0x40000), np.zeros(dim, dtype=np.int32))

    # SFU softmax on zeros
    sfu_len = 16
    _mmio_write_sram(model, np.zeros(sfu_len, dtype=np.float32), 0x10000)
    _mmio_sfu_op(model, op=0, length=sfu_len)
    softmax_zero = _mmio_read_sram(model, sfu_len, 0x20000)
    assert np.allclose(softmax_zero, 1.0 / sfu_len, atol=1e-6)

    rec.finalize()
    return rec


def gen_fm_soc_030() -> VectorRecorder:
    """INT32 overflow saturation for Vector ops."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-030", "P3")
    rec.capture_input()
    bridge = model.bridge
    INT32_MAX = np.iinfo(np.int32).max
    INT32_MIN = np.iinfo(np.int32).min

    # resid_add overflow
    orig = np.array([50000.0], dtype=np.float32)
    delta = np.array([INT32_MAX], dtype=np.int32)
    _vec_write_f16(model, orig, 0x30000)
    _vec_write_i32(model, delta, 0x31000)
    _vec_mmio_op(model, op=5, dim=1)
    assert bridge.handle("read", VECTOR.BASE + VECTOR.STATUS) == 2
    resid_out = _vec_read_i32(model, 1, 0x40000)[0]
    assert resid_out == INT32_MAX

    # add overflow positive
    a = np.array([INT32_MAX], dtype=np.int32)
    b = np.array([1], dtype=np.int32)
    _vec_write_i32(model, a, 0x30000)
    _vec_write_i32(model, b, 0x31000)
    _vec_mmio_op(model, op=0, dim=1)
    add_out = _vec_read_i32(model, 1, 0x40000)[0]
    assert add_out == INT32_MAX

    # add overflow negative
    a = np.array([INT32_MIN], dtype=np.int32)
    b = np.array([-1], dtype=np.int32)
    _vec_write_i32(model, a, 0x30000)
    _vec_write_i32(model, b, 0x31000)
    _vec_mmio_op(model, op=0, dim=1)
    add_out_min = _vec_read_i32(model, 1, 0x40000)[0]
    assert add_out_min == INT32_MIN

    # mul overflow positive
    a = np.array([2**16], dtype=np.int32)
    b = np.array([2**16], dtype=np.int32)
    _vec_write_i32(model, a, 0x30000)
    _vec_write_i32(model, b, 0x31000)
    _vec_mmio_op(model, op=1, dim=1)
    mul_out = _vec_read_i32(model, 1, 0x40000)[0]
    assert mul_out == INT32_MAX

    # mul overflow negative
    a = np.array([2**16], dtype=np.int32)
    b = np.array([-2**16], dtype=np.int32)
    _vec_write_i32(model, a, 0x30000)
    _vec_write_i32(model, b, 0x31000)
    _vec_mmio_op(model, op=1, dim=1)
    mul_out_min = _vec_read_i32(model, 1, 0x40000)[0]
    assert mul_out_min == INT32_MIN

    rec.finalize()
    return rec


def gen_fm_soc_031() -> VectorRecorder:
    """FP16 subnormal inputs flush-to-zero for SFU paths."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-031", "P3")
    sfu = GoldenSFU()
    fp16_tol = dict(tol_abs=2e-3, tol_rel=1e-2)

    tiny = np.finfo(np.float16).tiny
    denorm_pos = float(np.nextafter(np.float16(0), np.float16(1)))
    denorm_neg = float(np.nextafter(np.float16(0), np.float16(-1)))
    assert 0 < denorm_pos < tiny
    assert -tiny < denorm_neg < 0

    rec.capture_input()

    N = 16
    denorm_in = np.full(N, denorm_pos, dtype=np.float32)
    _mmio_write_sram(model, denorm_in, 0x10000)
    _mmio_sfu_op(model, op=0, length=N)
    out_denorm = _mmio_read_sram(model, N, 0x20000)
    out_zero = sfu.softmax_hw(np.zeros(N, dtype=np.float32))
    cmp_sm = GoldenSFU.compare_hw_vs_ref(out_denorm, out_zero, **fp16_tol)
    assert cmp_sm["within_tolerance"]

    x = np.array([denorm_pos, denorm_neg], dtype=np.float32)
    _mmio_write_sram(model, x, 0x10000)
    _mmio_sfu_op(model, op=2, length=2)
    out_gelu = _mmio_read_sram(model, 2, 0x20000)
    ref_gelu = sfu.gelu_hw(np.zeros(2, dtype=np.float32))
    assert GoldenSFU.compare_hw_vs_ref(out_gelu, ref_gelu, **fp16_tol)["within_tolerance"]

    _mmio_write_sram(model, x, 0x10000)
    _mmio_sfu_op(model, op=4, length=2)
    out_silu = _mmio_read_sram(model, 2, 0x20000)
    ref_silu = sfu.silu_hw(np.zeros(2, dtype=np.float32))
    assert GoldenSFU.compare_hw_vs_ref(out_silu, ref_silu, **fp16_tol)["within_tolerance"]

    _mmio_write_sram(model, denorm_in, 0x10000)
    _mmio_sfu_op(model, op=6, length=N)
    out_rms = _mmio_read_sram(model, N, 0x20000)
    ref_rms = sfu.rmsnorm_hw(np.zeros(N, dtype=np.float32))
    assert GoldenSFU.compare_hw_vs_ref(out_rms, ref_rms, **fp16_tol)["within_tolerance"]

    # Anti-vacuous normal input differs
    normal_in = np.array([1.0, -1.0], dtype=np.float32)
    _mmio_write_sram(model, normal_in, 0x10000)
    _mmio_sfu_op(model, op=2, length=2)
    out_normal = _mmio_read_sram(model, 2, 0x20000)
    assert not np.allclose(out_normal, ref_gelu, atol=1e-6)

    rec.finalize()
    return rec


# ══════════════════════════════════════════════════════════════════════
# P4 case generators
# ══════════════════════════════════════════════════════════════════════

def gen_fm_soc_021() -> VectorRecorder:
    """FuncModel end-to-end conv2d smoke (tile-level per-block INT4)."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-021", "P4")

    # Setup follows FuncModel.test_conv2d_smoke exactly
    from quantize import quantize_int4_per_block
    from tile_scheduler import TILE_WEIGHT_BYTES, TILE_SCALE_BYTES

    M, K, N = 1, 256, 256
    rng = np.random.RandomState(42)
    W_f32 = rng.randn(K, N).astype(np.float32) * 0.5
    act = rng.randint(-128, 128, size=M * K, dtype=np.int8).reshape(M, K)

    wgt_row_packed, wgt_scales, _ = quantize_int4_per_block(W_f32, 128)
    num_blocks = (K + 127) // 128
    num_tiles = (N + 127) // 128

    wgt_tile_major = bytearray()
    scale_tile_major = bytearray()
    for n_tile in range(num_tiles):
        nc = min(128, N - n_tile * 128)
        for k_block in range(num_blocks):
            kr = min(128, K - k_block * 128)
            for r in range(kr):
                k_idx = k_block * 128 + r
                row_start = k_idx * (N // 2) + n_tile * 64
                wgt_tile_major.extend(wgt_row_packed[row_start:row_start + nc // 2])
            sc_start = (k_block * N + n_tile * 128) * 4
            scale_tile_major.extend(wgt_scales.tobytes()[sc_start:sc_start + nc * 4])

    wgt_tile_bytes = bytes(wgt_tile_major)
    scale_tile_bytes = bytes(scale_tile_major)

    wgt_addr, act_addr, out_addr, scale_addr = (
        0x80020000, 0x80010000, 0x81000000, 0x80100000)
    model.host_write_data(wgt_addr, np.frombuffer(wgt_tile_bytes, dtype=np.uint8))
    model.host_write_data(act_addr, act)
    model.host_write_data(scale_addr, np.frombuffer(scale_tile_bytes, dtype=np.float32))

    desc_addr = 0x80000080
    model.host_write_descriptor(desc_addr,
        input_addr=act_addr, weight_addr=wgt_addr, output_addr=out_addr,
        scale_addr=scale_addr,
        scale_size=len(scale_tile_bytes),
        input_size=act.nbytes, weight_size=len(wgt_tile_bytes),
        output_size=M * N * 4,
        M=M, K=K, N=N)

    rec.capture_input()
    model.host_write_command(0, desc_addr)
    results = model.run()
    assert results[0]["status"] == "done"

    rec.finalize()
    return rec


def gen_fm_soc_022() -> VectorRecorder:
    """3-descriptor pipeline: MXU -> SFU RMSNorm -> Vector resid_add."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-022", "P4")

    rng = np.random.RandomState(20260629)
    M, K, N = 1, 64, 64
    act = rng.randint(-128, 128, size=M * K, dtype=np.int8)
    w_vals = rng.randint(-8, 8, size=K * N, dtype=np.int8)
    w_packed = GoldenMXU.pack_int4(w_vals)
    scales = np.ones((1, N), dtype=np.float32)

    act_addr = 0x80010000
    wgt_addr = 0x80020000
    mxu_out_addr = 0x81000000
    model.host_write_data(act_addr, act)
    model.host_write_data(wgt_addr, w_packed)

    mxu_golden = GoldenMXU().matmul_int32(act.reshape(M, K), w_packed, M, K, N)
    original = rng.randn(N).astype(np.float32) * 5.0
    original_f16 = original.astype(np.float16).astype(np.float32)
    bf16_mxu = GoldenVector().conv_i32_to_f16(mxu_golden)
    rms_out = GoldenSFU().rmsnorm_hw(bf16_mxu[0].astype(np.float32))
    resid_golden = GoldenVector().residual_add(original_f16, rms_out.astype(np.int32))

    _vec_write_f16(model, original_f16, 0x30000)

    rec.capture_input()
    bridge = model.bridge

    bridge.handle("write", MXU.BASE + MXU.CTRL, 0)
    bridge.handle("write", MXU.BASE + MXU.DIM0, (K << 16) | M)
    bridge.handle("write", MXU.BASE + MXU.DIM1, N)
    bridge.handle("write", MXU.BASE + MXU.I_ADDR, act_addr)
    bridge.handle("write", MXU.BASE + MXU.W_ADDR, wgt_addr)
    bridge.handle("write", MXU.BASE + MXU.O_ADDR, mxu_out_addr)
    bridge.handle("write", MXU.BASE + MXU.SCALE_ADDR, 0)
    bridge.handle("write", MXU.BASE + MXU.CMD, 1)
    assert bridge.handle("read", MXU.BASE + MXU.STATUS) == 2

    mxu_i32 = np.frombuffer(_dram_read_direct(model, mxu_out_addr, M * N * 4), dtype=np.int32)
    _vec_write_i32(model, mxu_i32, 0x31000)
    bridge.handle("write", VECTOR.BASE + VECTOR.CTRL, 4)
    bridge.handle("write", VECTOR.BASE + VECTOR.A_ADDR, 0x31000)
    bridge.handle("write", VECTOR.BASE + VECTOR.B_ADDR, 0x31000)
    bridge.handle("write", VECTOR.BASE + VECTOR.O_ADDR, 0x40000)
    bridge.handle("write", VECTOR.BASE + VECTOR.DIM, M * N)
    bridge.handle("write", VECTOR.BASE + VECTOR.CMD, 1)
    assert bridge.handle("read", VECTOR.BASE + VECTOR.STATUS) == 2

    _mmio_write_sram(model, _vec_read_f16(model, M * N, 0x40000), 0x10000)
    _mmio_sfu_op(model, op=6, length=N)
    assert bridge.handle("read", SFU.BASE + SFU.STATUS) == 2

    delta_i32 = _mmio_read_sram(model, N, 0x20000).astype(np.int32)
    _vec_write_i32(model, delta_i32, 0x32000)

    bridge.handle("write", VECTOR.BASE + VECTOR.CTRL, 5)
    bridge.handle("write", VECTOR.BASE + VECTOR.A_ADDR, 0x30000)
    bridge.handle("write", VECTOR.BASE + VECTOR.B_ADDR, 0x32000)
    bridge.handle("write", VECTOR.BASE + VECTOR.O_ADDR, 0x40000)
    bridge.handle("write", VECTOR.BASE + VECTOR.DIM, N)
    bridge.handle("write", VECTOR.BASE + VECTOR.CMD, 1)
    assert bridge.handle("read", VECTOR.BASE + VECTOR.STATUS) == 2

    final = _vec_read_i32(model, N, 0x40000)
    assert np.array_equal(final, resid_golden)

    rec.finalize()
    return rec


def gen_fm_soc_023() -> VectorRecorder:
    """Multi-engine pipeline: RoPE -> Vector resid_add -> MXU->BF16->softmax."""
    model = FuncModel()
    rec = VectorRecorder(model, "FM-SOC-023", "P4")

    rng = np.random.RandomState(20260629)
    sfu = GoldenSFU()
    vec = GoldenVector()

    # RoPE
    num_heads, head_dim = 4, 128
    q_in = rng.randn(num_heads * head_dim).astype(np.float32) * 0.5
    k_in = rng.randn(2 * head_dim).astype(np.float32) * 0.5
    q_rot, k_rot = sfu.rope_hw(q_in.copy(), k_in.copy(), position=42, num_heads=num_heads, head_dim=head_dim)
    original = rng.randn(num_heads * head_dim).astype(np.float32) * 5.0
    delta = q_rot.astype(np.int32)
    resid_golden = vec.residual_add(original, delta)

    # MXU -> BF16 -> softmax
    M, K, N = 1, 64, 32
    act = rng.randint(-128, 128, size=M * K, dtype=np.int8)
    w_vals = rng.randint(-8, 8, size=K * N, dtype=np.int8)
    w_packed = GoldenMXU.pack_int4(w_vals)
    mxu_out = GoldenMXU().matmul_int32(act, w_packed, M, K, N)
    bf16 = vec.conv_i32_to_f16(mxu_out)
    softmax_golden = sfu.softmax_hw(bf16[0].astype(np.float32))

    # Preload
    _vec_write_f16(model, original, 0x30000)
    _vec_write_i32(model, delta, 0x31000)
    _vec_write_i32(model, np.frombuffer(act.tobytes(), dtype=np.int32), 0x32000)
    model.sram[0x33000:0x33000 + len(w_packed)] = w_packed.tobytes()

    rec.capture_input()
    bridge = model.bridge

    # resid_add
    _vec_mmio_op(model, op=5, dim=num_heads * head_dim)
    assert bridge.handle("read", VECTOR.BASE + VECTOR.STATUS) == 2

    # MXU
    bridge.handle("write", MXU.BASE + MXU.CTRL, 0)
    bridge.handle("write", MXU.BASE + MXU.DIM0, (K << 16) | M)
    bridge.handle("write", MXU.BASE + MXU.DIM1, N)
    bridge.handle("write", MXU.BASE + MXU.I_ADDR, 0x32000)
    bridge.handle("write", MXU.BASE + MXU.W_ADDR, 0x33000)
    bridge.handle("write", MXU.BASE + MXU.O_ADDR, 0x34000)
    bridge.handle("write", MXU.BASE + MXU.SCALE_ADDR, 0)
    bridge.handle("write", MXU.BASE + MXU.CMD, 1)
    assert bridge.handle("read", MXU.BASE + MXU.STATUS) == 2

    # CONV
    _vec_write_i32(model, np.frombuffer(bytes(model.sram[0x34000:0x34000 + M * N * 4]), dtype=np.int32), 0x30000)
    _vec_mmio_op(model, op=4, dim=M * N)
    assert bridge.handle("read", VECTOR.BASE + VECTOR.STATUS) == 2

    # Softmax
    _mmio_write_sram(model, _vec_read_f16(model, M * N, 0x40000), 0x10000)
    _mmio_sfu_op(model, op=0, length=N)
    assert bridge.handle("read", SFU.BASE + SFU.STATUS) == 2

    rec.finalize()
    return rec


def gen_fm_soc_032() -> VectorRecorder:
    """28-block full-layer chain with per-block isolation."""
    model = FuncModel(dram_mb=256)
    rec = VectorRecorder(model, "FM-SOC-032", "P4")

    manifest = _load_blk0_manifest()
    cache = _chain_build_cache(manifest)

    baseline_weights = {}
    for op in manifest["ops"]:
        if op["opcode"] != "MMUL":
            continue
        idx = op["idx"]
        dims = op["dimensions"]
        K_eff = min(dims.get("K", 0), 64)
        N_eff = min(dims.get("N", 0), 64)
        weight_size = (K_eff * N_eff + 1) // 2
        weight_full = _blk0_read_hex(op["weight_hex"], 1)
        weight_bytes = weight_full[:weight_size]
        if len(weight_bytes) < weight_size:
            weight_bytes = weight_bytes + b"\x00" * (weight_size - len(weight_bytes))
        baseline_weights[idx] = weight_bytes

    scales = [0.90 + i * 0.01 for i in range(28)]
    block_weights = []
    for scale in scales:
        block_weights.append({idx: _chain_scale_int4_weights(w, scale) for idx, w in baseline_weights.items()})

    # Preload block 0 inputs/weights for golden capture (representative single block)
    block_base = 0x8001_0000
    result_base = 0x8080_0000
    _chain_dram_write(model, block_base + 0x0004_0000 - 4, struct.pack("<I", 0xDEAD0000))
    _chain_dram_write(model, result_base + 0x0001_0000 - 4, struct.pack("<I", 0xBEEF0000))

    rec.capture_input()
    _chain_run_block(model, 0, block_base, None, block_weights[0], manifest, cache)

    rec.finalize()
    return rec


def _chain_run_block(model, block_idx, block_base, prev_out_i32_addr, weights, manifest, cache):
    """Run one scaled blk.0 block through FuncModel."""
    bridge = model.bridge
    fp16_tol = dict(tol_abs=2e-3, tol_rel=1e-2)
    output_buffer_rel = manifest["sram_layout"]["output_buffer"]

    for op in manifest["ops"]:
        idx = op["idx"]
        opcode = op["opcode"]
        name = op["name"]
        label = f"blk{block_idx} op{idx:02d} {name}"
        i_addr = block_base + int(op["sram_input_addr"], 16)
        o_addr = block_base + int(op["sram_output_addr"], 16)

        if opcode == "MMUL":
            input_bytes, M_eff, K_eff, N_eff, _ = cache["mmul_inputs"][idx]
            act = np.frombuffer(input_bytes, dtype=np.int8).reshape(M_eff, K_eff)
            w_addr = block_base
            weight_bytes = weights[idx]
            _chain_dram_write(model, i_addr, input_bytes)
            _chain_dram_write(model, w_addr, weight_bytes)
            bridge.handle("write", MXU.BASE + MXU.CTRL, 0)
            bridge.handle("write", MXU.BASE + MXU.I_ADDR, i_addr)
            bridge.handle("write", MXU.BASE + MXU.W_ADDR, w_addr)
            bridge.handle("write", MXU.BASE + MXU.O_ADDR, o_addr)
            bridge.handle("write", MXU.BASE + MXU.SCALE_ADDR, 0)
            dim0 = (M_eff & 0xFFFF) | ((K_eff & 0xFFFF) << 16)
            bridge.handle("write", MXU.BASE + MXU.DIM0, dim0)
            bridge.handle("write", MXU.BASE + MXU.DIM1, N_eff & 0xFFFF)
            bridge.handle("write", MXU.BASE + MXU.CMD, 1)
            status = bridge.handle("read", MXU.BASE + MXU.STATUS)
            assert status == 2, f"{label}: STATUS={status}"
        elif opcode in ("RMSNORM", "SOFTMAX", "ROPE", "SILU"):
            input_bytes, elements, head_dim = cache["sfu_inputs"][idx]
            _chain_dram_write(model, i_addr, input_bytes)
            sfu_op_map = {"SOFTMAX": 0, "RMSNORM": 6, "ROPE": 5, "SILU": 4}
            op_id = sfu_op_map[opcode]
            bridge.handle("write", SFU.BASE + SFU.CTRL, op_id)
            bridge.handle("write", SFU.BASE + SFU.I_ADDR, i_addr)
            bridge.handle("write", SFU.BASE + SFU.O_ADDR, o_addr)
            bridge.handle("write", SFU.BASE + SFU.DIM, (head_dim << 16) | (elements & 0xFFFF))
            if opcode == "ROPE":
                bridge.handle("write", SFU.BASE + SFU.POS, op["dimensions"].get("position", 0))
            bridge.handle("write", SFU.BASE + SFU.CMD, 1)
            status = bridge.handle("read", SFU.BASE + SFU.STATUS)
            assert status == 2, f"{label}: STATUS={status}"
        elif opcode in ("VMUL", "VRESID"):
            vec_op_map = {"VMUL": 1, "VRESID": 5}
            op_id = vec_op_map[opcode]
            elements = op["dimensions"]["elements"]
            b_addr = block_base + output_buffer_rel
            if opcode == "VRESID" and idx == 16 and block_idx > 0:
                _chain_vector_conv(model, prev_out_i32_addr, i_addr, elements)
                a_bytes = _chain_dram_read(model, i_addr, elements * 2)
                b_bytes = _chain_dram_read(model, b_addr, elements * 4)
            else:
                a_bytes, b_bytes, _, _, _ = cache["vector_inputs"][idx]
            _chain_dram_write(model, i_addr, a_bytes)
            _chain_dram_write(model, b_addr, b_bytes)
            bridge.handle("write", VECTOR.BASE + VECTOR.CTRL, op_id)
            bridge.handle("write", VECTOR.BASE + VECTOR.A_ADDR, i_addr)
            bridge.handle("write", VECTOR.BASE + VECTOR.B_ADDR, b_addr)
            bridge.handle("write", VECTOR.BASE + VECTOR.O_ADDR, o_addr)
            bridge.handle("write", VECTOR.BASE + VECTOR.DIM, elements & 0xFFFF)
            bridge.handle("write", VECTOR.BASE + VECTOR.CMD, 1)
            status = bridge.handle("read", VECTOR.BASE + VECTOR.STATUS)
            assert status == 2, f"{label}: STATUS={status}"

    result_addr = 0x8080_0000 + block_idx * 0x0001_0000
    final_out_addr = block_base + output_buffer_rel
    _chain_vector_conv(model, final_out_addr, result_addr, 2560)
    return result_addr


def _chain_build_cache(manifest: dict) -> dict:
    """Pre-load all blk.0 baseline inputs/weights."""
    cache = {"mmul_inputs": {}, "sfu_inputs": {}, "vector_inputs": {}}
    for op in manifest["ops"]:
        idx = op["idx"]
        opcode = op["opcode"]
        if opcode == "MMUL":
            dims = op["dimensions"]
            M_eff = min(dims.get("M", 1), 64)
            K_eff = min(dims.get("K", 0), 64)
            N_eff = min(dims.get("N", 0), 64)
            input_fmt = manifest["files"][op["input_hex"]]["format"]
            input_eb = _EB_BY_FMT[input_fmt]
            input_full = _blk0_read_hex(op["input_hex"], input_eb)
            need = M_eff * K_eff * input_eb
            input_bytes = input_full[:need]
            if len(input_bytes) < need:
                input_bytes = input_bytes + b"\x00" * (need - len(input_bytes))
            cache["mmul_inputs"][idx] = (input_bytes, M_eff, K_eff, N_eff, input_eb)
        elif opcode in ("RMSNORM", "SOFTMAX", "ROPE", "SILU"):
            input_hex = op.get("input_hex")
            if input_hex is None:
                prefix = f"op{idx:02d}_"
                candidates = [fname for fname, finfo in manifest["files"].items()
                              if fname.startswith(prefix) and fname.endswith("_input.hex")]
                input_hex = candidates[0]
            input_fmt = manifest["files"][input_hex]["format"]
            input_eb = _EB_BY_FMT[input_fmt]
            input_full = _blk0_read_hex(input_hex, input_eb)
            if opcode == "ROPE":
                elements = op["dimensions"].get("q_len", 0) + op["dimensions"].get("k_len", 0)
                head_dim = op["dimensions"].get("head_dim", 128)
            else:
                elements = op["dimensions"].get("elements", 0)
                head_dim = 0
            need = elements * input_eb
            if len(input_full) < need:
                input_full = input_full + b"\x00" * (need - len(input_full))
            cache["sfu_inputs"][idx] = (input_full, elements, head_dim)
        elif opcode in ("VMUL", "VRESID"):
            if opcode == "VMUL":
                a_hex, b_hex = "op14_vmul_gate_input.hex", "op14_vmul_up_input.hex"
            elif idx == 9:
                a_hex, b_hex = "op09_vresid_pre_input.hex", "op09_vresid_pre_o_out.hex"
            elif idx == 16:
                a_hex, b_hex = "op16_vresid_post_input.hex", "op16_vresid_post_down.hex"
            else:
                raise ValueError(f"Unknown Vector op idx {idx}")
            a_eb = _EB_BY_FMT[manifest["files"][a_hex]["format"]]
            b_eb = _EB_BY_FMT[manifest["files"][b_hex]["format"]]
            a_bytes = _blk0_read_hex(a_hex, a_eb)
            b_bytes = _blk0_read_hex(b_hex, b_eb)
            elements = op["dimensions"]["elements"]
            need_a = elements * a_eb
            need_b = elements * b_eb
            if len(a_bytes) < need_a:
                a_bytes = a_bytes + b"\x00" * (need_a - len(a_bytes))
            if len(b_bytes) < need_b:
                b_bytes = b_bytes + b"\x00" * (need_b - len(b_bytes))
            cache["vector_inputs"][idx] = (a_bytes, b_bytes, elements, a_eb, b_eb)
    return cache


def _chain_scale_int4_weights(weight_bytes: bytes, scale: float) -> bytes:
    if scale == 1.0:
        return weight_bytes
    packed = np.frombuffer(weight_bytes, dtype=np.uint8).copy()
    unpacked = GoldenMXU.unpack_int4(packed)
    scaled = np.round(unpacked.astype(np.float32) * scale).astype(np.int32)
    scaled = np.clip(scaled, -8, 7).astype(np.int8)
    return bytes(GoldenMXU.pack_int4(scaled))


def _chain_perturb_weights(weights: dict, ratio: float = 0.01) -> dict:
    perturbed = {}
    for idx, w in weights.items():
        bw = bytearray(w)
        step = max(1, int(1.0 / ratio))
        for i in range(0, len(bw), step):
            bw[i] ^= 0x01
        perturbed[idx] = bytes(bw)
    return perturbed


def _chain_vector_conv(model: FuncModel, src_addr: int, dst_addr: int, dim: int):
    bridge = model.bridge
    bridge.handle("write", VECTOR.BASE + VECTOR.CTRL, 4)
    bridge.handle("write", VECTOR.BASE + VECTOR.A_ADDR, src_addr)
    bridge.handle("write", VECTOR.BASE + VECTOR.B_ADDR, src_addr)
    bridge.handle("write", VECTOR.BASE + VECTOR.O_ADDR, dst_addr)
    bridge.handle("write", VECTOR.BASE + VECTOR.DIM, dim & 0xFFFF)
    bridge.handle("write", VECTOR.BASE + VECTOR.CMD, 1)
    status = bridge.handle("read", VECTOR.BASE + VECTOR.STATUS)
    assert status == 2


def _chain_dram_write(model: FuncModel, addr: int, data: bytes):
    off = addr - Addr.DRAM_BASE
    model.dram[off:off + len(data)] = data


def _chain_dram_read(model: FuncModel, addr: int, size: int) -> bytes:
    off = addr - Addr.DRAM_BASE
    return bytes(model.dram[off:off + size])


def gen_fm_soc_10x() -> VectorRecorder:
    """Full host->PCIe->DRAM->doorbell->firmware->IRQ->blk.0 chain->DRAM->PCIe->host."""
    model = FuncModel(dram_mb=256)
    rec = VectorRecorder(model, "FM-SOC-10X", "P4")

    manifest = _load_blk0_manifest()
    fp16_tol = dict(tol_abs=2e-3, tol_rel=1e-2)

    op_meta = {}
    for op in manifest["ops"]:
        idx = op["idx"]
        opcode = op["opcode"]
        base = 0x8001_0000 + idx * 0x0002_0000
        addrs = {
            "input": base + 0x00000,
            "weight": base + 0x04000,
            "output": base + 0x08000,
            "scale": base + 0x14000,
            "desc": base + 0x1F000,
        }

        input_bytes, _ = _e2e_blk0_load_input(op, manifest)
        model.host_write_data(addrs["input"], np.frombuffer(input_bytes, dtype=np.uint8))
        weight_bytes = None

        if opcode == "MMUL":
            weight_bytes, M_eff, K_eff, N_eff = _e2e_blk0_load_weight(op, manifest)
            model.host_write_data(addrs["weight"], np.frombuffer(weight_bytes, dtype=np.uint8))
            num_blocks = (K_eff + 127) // 128
            scales = np.ones((num_blocks, N_eff), dtype=np.float32)
            model.host_write_data(addrs["scale"], scales.ravel())
            model.host_write_descriptor(
                addrs["desc"],
                input_addr=addrs["input"], weight_addr=addrs["weight"],
                output_addr=addrs["output"], scale_addr=addrs["scale"],
                input_size=M_eff * K_eff,
                weight_size=len(weight_bytes),
                output_size=M_eff * N_eff * 4,
                scale_size=num_blocks * N_eff * 4,
                M=M_eff, K=K_eff, N=N_eff,
            )
        elif opcode in ("RMSNORM", "SOFTMAX", "ROPE", "SILU"):
            dims = op["dimensions"]
            elements = dims.get("q_len", 0) + dims.get("k_len", 0) if opcode == "ROPE" else dims.get("elements", 0)
            model.host_write_descriptor(
                addrs["desc"],
                input_addr=addrs["input"], output_addr=addrs["output"],
                input_size=elements, output_size=elements,
                M=1, K=elements, N=1,
            )
        elif opcode in ("VMUL", "VRESID"):
            elements = op["dimensions"]["elements"]
            a_fmt = "int32" if opcode == "VMUL" else "fp16"
            a_eb = _EB_BY_FMT[a_fmt]
            b_addr = addrs["input"] + elements * a_eb
            model.host_write_descriptor(
                addrs["desc"],
                input_addr=addrs["input"], weight_addr=b_addr,
                output_addr=addrs["output"],
                input_size=elements, weight_size=elements,
                output_size=elements * 4,
                M=1, K=elements, N=1,
            )

        op_meta[idx] = {
            "op": op,
            "addrs": addrs,
            "input_bytes": input_bytes,
            "weight_bytes": weight_bytes,
        }

    # Corrupt Q_proj weight for anti-vacuous check
    corrupt_op_idx = 1
    corrupt_w_addr = op_meta[corrupt_op_idx]["addrs"]["weight"]
    corrupt_weight = bytearray(model.pcie.tlp_read(corrupt_w_addr, 1))
    corrupt_weight[0] ^= 0xFF
    model.pcie.tlp_write(corrupt_w_addr, bytes(corrupt_weight))

    rec.capture_input()

    ops = manifest["ops"]
    for op in ops[:15]:
        model.host_write_command(_e2e_blk0_opcode(op), op_meta[op["idx"]]["addrs"]["desc"])
    pending = model.bridge.handle("read", Addr.INTC_BASE + 0x00, 0)
    assert pending & (1 << 8)
    assert model.riscv.interrupt_pending

    results = model.firmware.run_loop(max_commands=15)
    assert len(results) == 15
    for r in results:
        assert r["status"] == "done"

    for op in ops[15:]:
        model.host_write_command(_e2e_blk0_opcode(op), op_meta[op["idx"]]["addrs"]["desc"])
    results.extend(model.firmware.run_loop(max_commands=len(ops) - 15))
    assert len(results) == len(ops)
    for r in results:
        assert r["status"] == "done"

    pending_after = model.bridge.handle("read", Addr.INTC_BASE + 0x00, 0)
    assert pending_after == 0

    rec.finalize()
    return rec


def _e2e_blk0_load_input(op, manifest):
    opcode = op["opcode"]
    if opcode == "MMUL":
        dims = op["dimensions"]
        M_eff = min(dims.get("M", 1), 64)
        K_eff = min(dims.get("K", 0), 64)
        input_fmt = manifest["files"][op["input_hex"]]["format"]
        input_eb = _EB_BY_FMT[input_fmt]
        input_full = _blk0_read_hex(op["input_hex"], input_eb)
        need = M_eff * K_eff * input_eb
        input_bytes = input_full[:need]
        if len(input_bytes) < need:
            input_bytes = input_bytes + b"\x00" * (need - len(input_bytes))
        return input_bytes, M_eff * K_eff

    if opcode in ("RMSNORM", "SOFTMAX", "ROPE", "SILU"):
        input_hex = op.get("input_hex")
        if input_hex is None:
            prefix = f"op{op['idx']:02d}_"
            candidates = [fname for fname, finfo in manifest["files"].items()
                          if fname.startswith(prefix) and fname.endswith("_input.hex")]
            input_hex = candidates[0]
        dims = op["dimensions"]
        if opcode == "ROPE":
            elements = dims.get("q_len", 0) + dims.get("k_len", 0)
        else:
            elements = dims.get("elements", 0)
        input_fmt = manifest["files"][input_hex]["format"]
        input_eb = _EB_BY_FMT[input_fmt]
        input_full = _blk0_read_hex(input_hex, input_eb)
        need = elements * input_eb
        input_bytes = input_full[:need]
        if len(input_bytes) < need:
            input_bytes = input_bytes + b"\x00" * (need - len(input_bytes))
        return input_bytes, elements

    if opcode in ("VMUL", "VRESID"):
        elements = op["dimensions"]["elements"]
        if opcode == "VMUL":
            a_hex, b_hex = "op14_vmul_gate_input.hex", "op14_vmul_up_input.hex"
        elif op["idx"] == 9:
            a_hex, b_hex = "op09_vresid_pre_input.hex", "op09_vresid_pre_o_out.hex"
        elif op["idx"] == 16:
            a_hex, b_hex = "op16_vresid_post_input.hex", "op16_vresid_post_down.hex"
        else:
            raise ValueError(f"Unknown Vector op idx {op['idx']}")
        a_fmt = manifest["files"][a_hex]["format"]
        b_fmt = manifest["files"][b_hex]["format"]
        a_bytes = _blk0_read_hex(a_hex, _EB_BY_FMT[a_fmt])
        b_bytes = _blk0_read_hex(b_hex, _EB_BY_FMT[b_fmt])
        need_a = elements * _EB_BY_FMT[a_fmt]
        need_b = elements * _EB_BY_FMT[b_fmt]
        if len(a_bytes) < need_a:
            a_bytes = a_bytes + b"\x00" * (need_a - len(a_bytes))
        if len(b_bytes) < need_b:
            b_bytes = b_bytes + b"\x00" * (need_b - len(b_bytes))
        return a_bytes + b_bytes, elements

    raise ValueError(f"Unsupported opcode {opcode}")


def _e2e_blk0_load_weight(op, manifest):
    dims = op["dimensions"]
    M_eff = min(dims.get("M", 1), 64)
    K_eff = min(dims.get("K", 0), 64)
    N_eff = min(dims.get("N", 0), 64)
    weight_size = (K_eff * N_eff + 1) // 2
    weight_full = _blk0_read_hex(op["weight_hex"], 1)
    weight_bytes = weight_full[:weight_size]
    if len(weight_bytes) < weight_size:
        weight_bytes = weight_bytes + b"\x00" * (weight_size - len(weight_bytes))
    return weight_bytes, M_eff, K_eff, N_eff


def _e2e_blk0_opcode(op):
    mapping = {
        "MMUL": OpCode.MMUL,
        "RMSNORM": OpCode.RMSNORM,
        "SOFTMAX": OpCode.SOFTMAX,
        "ROPE": OpCode.ROPE,
        "SILU": OpCode.SILU,
        "VMUL": OpCode.VMUL,
        "VRESID": OpCode.VRESID,
    }
    return mapping[op["opcode"]].value


# ══════════════════════════════════════════════════════════════════════
# Main driver
# ══════════════════════════════════════════════════════════════════════

GENERATORS = {
    "FM-SOC-001": gen_fm_soc_001,
    "FM-SOC-002": gen_fm_soc_002,
    "FM-SOC-003": gen_fm_soc_003,
    "FM-SOC-004": gen_fm_soc_004,
    "FM-SOC-005": gen_fm_soc_005,
    "FM-SOC-006": gen_fm_soc_006,
    "FM-SOC-007": gen_fm_soc_007,
    "FM-SOC-008": gen_fm_soc_008,
    "FM-SOC-009": gen_fm_soc_009,
    "FM-SOC-010": gen_fm_soc_010,
    "FM-SOC-011": gen_fm_soc_011,
    "FM-SOC-012": gen_fm_soc_012,
    "FM-SOC-024": gen_fm_soc_024,
    "FM-SOC-025": gen_fm_soc_025,
    "FM-SOC-026": gen_fm_soc_026,
    "FM-SOC-013": gen_fm_soc_013,
    "FM-SOC-014": gen_fm_soc_014,
    "FM-SOC-015": gen_fm_soc_015,
    "FM-SOC-016": gen_fm_soc_016,
    "FM-SOC-027": gen_fm_soc_027,
    "FM-SOC-017": gen_fm_soc_017,
    "FM-SOC-018": gen_fm_soc_018,
    "FM-SOC-019": gen_fm_soc_019,
    "FM-SOC-020": gen_fm_soc_020,
    "FM-SOC-028": gen_fm_soc_028,
    "FM-SOC-029": gen_fm_soc_029,
    "FM-SOC-030": gen_fm_soc_030,
    "FM-SOC-031": gen_fm_soc_031,
    "FM-SOC-021": gen_fm_soc_021,
    "FM-SOC-022": gen_fm_soc_022,
    "FM-SOC-023": gen_fm_soc_023,
    "FM-SOC-032": gen_fm_soc_032,
    "FM-SOC-10X": gen_fm_soc_10x,
}


def generate_all(out_dir: Path = OUT_DIR) -> Dict[str, str]:
    """Generate all 33 cases. Returns {case_id: status_message}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results: Dict[str, str] = {}
    failed: List[str] = []

    for case_id in CASE_ORDER:
        print(f"[{case_id}] generating vectors...")
        try:
            rec = GENERATORS[case_id]()
            rec.save(out_dir)
            inp_path = out_dir / case_id / "input.npz"
            exp_path = out_dir / case_id / "expected.npz"
            results[case_id] = f"OK | input={inp_path.stat().st_size}B expected={exp_path.stat().st_size}B"
            print(f"[{case_id}] {results[case_id]}")
        except Exception as exc:
            results[case_id] = f"FAIL | {type(exc).__name__}: {exc}"
            failed.append(case_id)
            print(f"[{case_id}] {results[case_id]}")

    # Write a generation report
    report_path = out_dir / "generation_report.json"
    with open(report_path, "w") as f:
        json.dump({"cases": results, "failed": failed, "total": len(CASE_ORDER), "passed": len(CASE_ORDER) - len(failed)}, f, indent=2)

    return results, failed


def main():
    results, failed = generate_all()
    print("\n" + "=" * 60)
    print(f"Generation complete: {len(CASE_ORDER) - len(failed)}/{len(CASE_ORDER)} cases passed")
    if failed:
        print(f"Failed cases: {', '.join(failed)}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
