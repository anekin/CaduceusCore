"""SoC Func Model tests — PCIe TLP path and host_write compatibility."""

import struct

import numpy as np
import pytest

from sim.func_model import FuncModel
from sim.regmap import Addr
from sim.models.crossbar import CrossbarModel


def _dram_read_direct(model: FuncModel, addr: int, size: int) -> bytes:
    """Direct DRAM read helper (bypasses PCIe model)."""
    return bytes(model.dram[addr - Addr.DRAM_BASE:addr - Addr.DRAM_BASE + size])


def test_host_write_data_baseline():
    """Baseline: host_write_data to DRAM must still land in model.dram."""
    model = FuncModel()
    data = np.arange(16, dtype=np.uint8)
    addr = 0x8000_1000
    model.host_write_data(addr, data)
    readback = _dram_read_direct(model, addr, data.nbytes)
    assert readback == data.tobytes()


def test_pcie_smoke():
    """PCIe TLP write to DRAM and read back."""
    model = FuncModel()
    addr = 0x8000_2000
    payload = bytes(range(256))
    model.pcie.tlp_write(addr, payload)
    readback = model.pcie.tlp_read(addr, len(payload))
    assert readback == payload


def test_pcie_sram_routing():
    """PCIe TLP write to SRAM must land in model.sram, not dram."""
    model = FuncModel()
    addr = 0x2000_1000
    payload = b"hello sram"
    model.pcie.tlp_write(addr, payload)
    off = addr - Addr.SRAM_BASE
    assert bytes(model.sram[off:off + len(payload)]) == payload
    # DRAM at the equivalent offset should be untouched
    dram_off = off
    assert bytes(model.dram[dram_off:dram_off + len(payload)]) != payload


def test_pcie_dram_routing():
    """PCIe TLP write to DRAM must land in model.dram, not sram."""
    model = FuncModel()
    addr = 0x8000_3000
    payload = b"hello dram"
    model.pcie.tlp_write(addr, payload)
    off = addr - Addr.DRAM_BASE
    assert bytes(model.dram[off:off + len(payload)]) == payload


def test_pcie_out_of_range_raises():
    """Out-of-BAR address must raise ValueError."""
    model = FuncModel()
    with pytest.raises(ValueError):
        model.pcie.tlp_write(0x5000_0000, b"fail")
    with pytest.raises(ValueError):
        model.pcie.tlp_read(0x5000_0000, 4)
    # Beyond SRAM size
    with pytest.raises(ValueError):
        model.pcie.tlp_write(Addr.SRAM_BASE + len(model.sram), b"fail")
    # Beyond DRAM size
    with pytest.raises(ValueError):
        model.pcie.tlp_write(Addr.DRAM_BASE + len(model.dram), b"fail")


def test_pcie_large_payload_split():
    """Payload larger than max TLP size is split into multiple TLPs."""
    model = FuncModel()
    addr = 0x8000_4000
    payload = bytes(i % 256 for i in range(512))
    model.pcie.tlp_write(addr, payload)
    readback = model.pcie.tlp_read(addr, len(payload))
    assert readback == payload


def test_pcie_corrupted():
    """Anti-vacuous: corrupting expected readback must produce mismatch."""
    model = FuncModel()
    addr = 0x8000_5000
    payload = b"correct data"
    model.pcie.tlp_write(addr, payload)
    readback = model.pcie.tlp_read(addr, len(payload))
    corrupted = payload[:-1] + bytes([payload[-1] ^ 0xFF])
    assert readback != corrupted


def test_crossbar_concurrent():
    """3 masters (MXU read + DMA read + PCIe write) concurrently, different addresses."""
    from sim.models.crossbar import CrossbarModel

    model = FuncModel()
    xbar = model.crossbar

    mxu_payload = b"mxu_reads_this"
    dma_payload = b"dma_reads_this"
    pcie_payload = b"pcie_writes_01"

    mxu_addr = 0x2000_2000
    dma_addr = 0x8000_3000
    pcie_addr = 0x2000_1000

    model.sram[mxu_addr - Addr.SRAM_BASE:mxu_addr - Addr.SRAM_BASE + len(mxu_payload)] = mxu_payload
    model.dram[dma_addr - Addr.DRAM_BASE:dma_addr - Addr.DRAM_BASE + len(dma_payload)] = dma_payload

    mxu_data = xbar.read(CrossbarModel.MASTER_MXU, mxu_addr, len(mxu_payload))
    dma_data = xbar.read(CrossbarModel.MASTER_DMA, dma_addr, len(dma_payload))
    xbar.write(CrossbarModel.MASTER_PCIE, pcie_addr, pcie_payload)

    assert mxu_data == mxu_payload
    assert dma_data == dma_payload
    sram_off = pcie_addr - Addr.SRAM_BASE
    assert bytes(model.sram[sram_off:sram_off + len(pcie_payload)]) == pcie_payload

    assert xbar._txn_ids[CrossbarModel.MASTER_MXU] == 1
    assert xbar._txn_ids[CrossbarModel.MASTER_DMA] == 1
    assert xbar._txn_ids[CrossbarModel.MASTER_PCIE] == 1

    aw_grants = [g for g in xbar._aw_grants if g[1] == CrossbarModel.MASTER_PCIE]
    ar_grants = [g for g in xbar._ar_grants if g[1] in (
        CrossbarModel.MASTER_MXU, CrossbarModel.MASTER_DMA)]
    assert len(aw_grants) >= 1
    assert len(ar_grants) >= 2

    dram_off = pcie_addr - Addr.SRAM_BASE
    assert bytes(model.dram[dram_off:dram_off + len(pcie_payload)]) != pcie_payload

    with pytest.raises(ValueError):
        xbar.read(7, mxu_addr, 4)
    with pytest.raises(ValueError):
        xbar.write(CrossbarModel.MASTER_PCIE, 0x5000_0000, b"decerr")


# ══════════════════════════════════════════════════════════════════════
# Paths #1 APB-MMIO and #2 IBEX-AXI tests
# ══════════════════════════════════════════════════════════════════════


def test_apb_handshake_basics():
    """APB read/write with psel/penable handshake validation."""
    model = FuncModel()

    # Write via APB: set MXU CTRL register, then read back
    model.bridge.apb_write(Addr.MXU_BASE + 0x00, 0x00000002)
    val = model.bridge.apb_read(Addr.MXU_BASE + 0x00)
    assert val == 0x00000002

    # psel=0: read returns 0 (slave not selected)
    model.bridge.apb_write(Addr.MXU_BASE + 0x00, 0xDEAD, psel=1, penable=1)
    val = model.bridge.apb_read(Addr.MXU_BASE + 0x00, psel=0, penable=1)
    assert val == 0

    # penable=0: read returns 0 (setup phase)
    val = model.bridge.apb_read(Addr.MXU_BASE + 0x00, psel=1, penable=0)
    assert val == 0

    # penable=0: write is silently ignored
    model.bridge.apb_write(Addr.MXU_BASE + 0x00, 0xCAFE, psel=1, penable=0)
    val = model.bridge.apb_read(Addr.MXU_BASE + 0x00, psel=1, penable=1)
    assert val == 0xDEAD  # unchanged


def test_ibex_memory_access():
    """Ibex (RISCVMini) stores/loads through shared crossbar SRAM/DRAM.

    Verifies:
      - Ibex write to DRAM → Ibex reads back same value.
      - Ibex writes SRAM → MXU reads through crossbar, data consistent.
      - Out-of-range address returns 0 without exception.
      - Isolation: writing address A does not corrupt address B.
    """
    model = FuncModel()
    emu = model.riscv

    # 1. Ibex writes 0xDEADBEEF to DRAM at 0x80000100
    dram_addr = 0x80000100
    emu._mem_write(dram_addr, 0xDEADBEEF)
    result = emu._mem_read(dram_addr)
    assert result == 0xDEADBEEF, (
        f"Ibex DRAM readback: expected 0xDEADBEEF, got 0x{result:08X}"
    )

    # Also verify via crossbar directly
    raw = model.crossbar.read(CrossbarModel.MASTER_IBEX, dram_addr, 4)
    assert struct.unpack_from('<I', raw, 0)[0] == 0xDEADBEEF

    # 2. Ibex writes known pattern to SRAM → MXU reads through crossbar
    sram_addr = 0x20001000
    emu._mem_write(sram_addr, 0xCAFEBABE)
    mxu_data = model.crossbar.read(CrossbarModel.MASTER_MXU, sram_addr, 4)
    mxu_val = struct.unpack_from('<I', mxu_data, 0)[0]
    assert mxu_val == 0xCAFEBABE, (
        f"MXU crossbar read: expected 0xCAFEBABE, got 0x{mxu_val:08X}"
    )

    # 3. Out-of-range address 0xFFFF0000 returns 0 without exception
    val = emu._mem_read(0xFFFF0000)
    assert val == 0, f"Out-of-range read: expected 0, got 0x{val:08X}"
    # Write to out-of-range should not raise
    emu._mem_write(0xFFFF0000, 0xAAAAAAAA)

    # 4. Isolation: write to addr A does not corrupt addr B
    addr_a = 0x20002000
    addr_b = 0x20002008
    emu._mem_write(addr_a, 0x11111111)
    emu._mem_write(addr_b, 0x22222222)
    assert emu._mem_read(addr_a) == 0x11111111
    assert emu._mem_read(addr_b) == 0x22222222
    # Verify addr_b still intact after re-writing addr_a
    emu._mem_write(addr_a, 0x33333333)
    assert emu._mem_read(addr_b) == 0x22222222, (
        "Isolation violation: writing addr_a corrupted addr_b"
    )


def test_boot_rom_loading():
    """Boot ROM loader loads npu_firmware.hex; graceful when missing."""
    model = FuncModel()

    # Load from the known firmware build path
    import os
    hex_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "firmware", "build", "npu_firmware.hex",
    )
    loaded = model.load_boot_rom(hex_path)
    assert loaded > 0, f"Expected to load > 0 bytes from {hex_path}"

    # Verify first 4 bytes are non-zero (firmware code loaded)
    first_word = model.riscv._mem_read(0x00000000)
    assert first_word != 0, "Boot ROM first word should be non-zero firmware code"

    # Missing file returns 0 without raising
    assert model.load_boot_rom("/nonexistent/hex/file.hex") == 0


def test_riscv_dmem_isolation():
    """RISCVMini DMEM is local and does not leak to shared SRAM."""
    model = FuncModel()
    emu = model.riscv

    # Write to DMEM (local)
    dmem_addr = 0x00010000
    emu._mem_write(dmem_addr, 0xFEEDFACE)
    assert emu._mem_read(dmem_addr) == 0xFEEDFACE

    # DMEM data should NOT be visible through the crossbar at that address
    # (crossbar only handles SRAM >= 0x2000_0000 and DRAM >= 0x8000_0000)
    with pytest.raises(ValueError):
        model.crossbar.read(CrossbarModel.MASTER_IBEX, dmem_addr, 4)


def test_riscv_mmio_routing():
    """RISCVMini routes MMIO addresses through the bridge callback."""
    model = FuncModel()
    emu = model.riscv

    # Write to MXU CTRL register through Ibex MMIO
    emu._mem_write(Addr.MXU_BASE + 0x00, 0x00000003)
    val = emu._mem_read(Addr.MXU_BASE + 0x00)
    assert val == 0x00000003
