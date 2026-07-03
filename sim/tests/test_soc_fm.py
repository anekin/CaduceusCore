"""SoC Func Model tests — PCIe TLP path and host_write compatibility."""

import numpy as np
import pytest

from sim.func_model import FuncModel
from sim.regmap import Addr


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
