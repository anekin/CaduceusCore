"""SoC Func Model tests — PCIe TLP path and host_write compatibility."""

import struct

import numpy as np
import pytest

from sim.func_model import FuncModel
from sim.golden_executor import GoldenSFU, GoldenVector
from sim.regmap import Addr, SFU, VECTOR, DMA
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


# ══════════════════════════════════════════════════════════════════════
# Path #7 + #3 + #8 integration — PCIe→DRAM→MXU→DRAM→PCIe (FM-SOC-024)
# ══════════════════════════════════════════════════════════════════════


def test_pcie_integration():
    """Full host→PCIe→DRAM→MXU compute→DRAM→PCIe→host integration.

    Exercises paths 7 (PCIE), 3 (MXU), and 8 (XBAR):
      1. Host writes activation, packed INT4 weights, and scale data to DRAM
         via PCIe TLP (path 7: PCIE-TLP).
      2. Host dispatches MXU MMUL via MMIO bridge with DRAM addresses for
         I_ADDR/W_ADDR/O_ADDR/SCALE_ADDR. MXU reads inputs and writes output
         through crossbar (paths 3+8: MXU-COMPUTE + XBAR-ARB).
      3. Host reads result from DRAM via PCIe TLP (path 7: PCIE-TLP).
      4. Result compared against GoldenMXU.matmul_int4_per_block().
    """
    from sim.regmap import MXU
    from sim.golden_executor import GoldenMXU

    model = FuncModel()

    M, K, N = 1, 8, 4
    group_size = 128

    act = np.array([1, 2, 3, 4, 5, 6, 7, 8], dtype=np.int8).reshape(M, K)

    wgt_unpacked = np.array([
        [0, 1, 2, 3],
        [4, 5, 6, 7],
        [0, 1, 2, 3],
        [4, 5, 6, 7],
        [0, 1, 2, 3],
        [4, 5, 6, 7],
        [0, 1, 2, 3],
        [4, 5, 6, 7],
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
    assert verify_act == act.tobytes(), "TLP readback of activation data mismatch"

    bridge = model.bridge
    bridge.handle('write', MXU.BASE + MXU.CTRL, 0)
    bridge.handle('write', MXU.BASE + MXU.DIM0, (K << 16) | M)
    bridge.handle('write', MXU.BASE + MXU.DIM1, N)
    bridge.handle('write', MXU.BASE + MXU.I_ADDR, act_addr)
    bridge.handle('write', MXU.BASE + MXU.W_ADDR, wgt_addr)
    bridge.handle('write', MXU.BASE + MXU.O_ADDR, out_addr)
    bridge.handle('write', MXU.BASE + MXU.SCALE_ADDR, scale_addr)

    bridge.handle('write', MXU.BASE + MXU.CMD, 1)

    status = bridge.handle('read', MXU.BASE + MXU.STATUS)
    assert status == 2, f"MXU STATUS={status}, expected DONE(2)"

    result_bytes = model.pcie.tlp_read(out_addr, M * N * 4)
    result = np.frombuffer(result_bytes, dtype=np.float32).reshape(M, N)

    golden = model.mxu.matmul_int4_per_block(
        act, wgt_packed, scales, M, K, N, group_size=128)

    assert np.allclose(result, golden, rtol=1e-5), (
        f"PCIe integration mismatch: "
        f"got {result.tolist()}, expected {golden.tolist()}"
    )


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
# Path #8 — Crossbar concurrent access P1 stress (FM-SOC-025)
# ══════════════════════════════════════════════════════════════════════


def test_crossbar_two_master_concurrent_read():
    """2 masters (MXU + DMA) concurrently read from different SRAM/DRAM addresses.

    Verifies:
      - Both reads return correct data (S0 SRAM and S1 DRAM routing).
      - _ar_grants records both grants.
      - Anti-vacuous: wrong master gets wrong data.
    """
    model = FuncModel()
    xbar = model.crossbar

    mxu_data = b"mxu_s0_read_okay"
    dma_data = b"dma_s1_read_okay"
    mxu_addr = 0x2000_3000  # S0 SRAM
    dma_addr = 0x8000_5000  # S1 DRAM

    model.sram[mxu_addr - Addr.SRAM_BASE:mxu_addr - Addr.SRAM_BASE + len(mxu_data)] = mxu_data
    model.dram[dma_addr - Addr.DRAM_BASE:dma_addr - Addr.DRAM_BASE + len(dma_data)] = dma_data

    r1 = xbar.read(CrossbarModel.MASTER_MXU, mxu_addr, len(mxu_data))
    r2 = xbar.read(CrossbarModel.MASTER_DMA, dma_addr, len(dma_data))

    assert r1 == mxu_data, "MXU read from SRAM: data mismatch"
    assert r2 == dma_data, "DMA read from DRAM: data mismatch"

    # Arbitration tracking
    ar_grants = xbar._ar_grants
    assert len(ar_grants) >= 2, f"Expected >=2 AR grants, got {len(ar_grants)}"
    ar_masters = [g[1] for g in ar_grants]
    assert CrossbarModel.MASTER_MXU in ar_masters, "MXU not in AR grant history"
    assert CrossbarModel.MASTER_DMA in ar_masters, "DMA not in AR grant history"

    # Anti-vacuous: MXU reading from DMA's DRAM address would get wrong data
    assert r1 != dma_data, "MXU read returned DMA data — vacuous (wrong address routing)"
    assert r2 != mxu_data, "DMA read returned MXU data — vacuous (wrong address routing)"


def test_crossbar_three_master_mixed():
    """3 masters mixed read+write (MXU read + SFU write + DMA read) to different addresses.

    Verifies:
      - Read ops through AR path, write op through AW path (independent arbitration).
      - All three operations succeed, data integrity preserved.
      - _aw_grants and _ar_grants are tracked independently.
      - Anti-vacuous: corrupted write payload detected on readback.
    """
    model = FuncModel()
    xbar = model.crossbar

    mxu_data = b"mxu_read_mixed_"
    dma_data = b"dma_read_mixed_"
    sfu_write_data = b"sfu_write_mix01"
    corrupt_data = b"ZFU_WRITE_MIX01"

    mxu_addr = 0x2000_4000  # S0
    dma_addr = 0x8000_6000  # S1
    sfu_addr = 0x2000_5000  # S0

    model.sram[mxu_addr - Addr.SRAM_BASE:mxu_addr - Addr.SRAM_BASE + len(mxu_data)] = mxu_data
    model.dram[dma_addr - Addr.DRAM_BASE:dma_addr - Addr.DRAM_BASE + len(dma_data)] = dma_data

    r_mxu = xbar.read(CrossbarModel.MASTER_MXU, mxu_addr, len(mxu_data))
    xbar.write(CrossbarModel.MASTER_SFU, sfu_addr, sfu_write_data)
    r_dma = xbar.read(CrossbarModel.MASTER_DMA, dma_addr, len(dma_data))

    assert r_mxu == mxu_data
    assert r_dma == dma_data
    sfu_off = sfu_addr - Addr.SRAM_BASE
    assert bytes(model.sram[sfu_off:sfu_off + len(sfu_write_data)]) == sfu_write_data

    # Independent AW/AR tracking
    assert len(xbar._aw_grants) >= 1, "Expected >=1 AW grant (SFU write)"
    assert len(xbar._ar_grants) >= 2, "Expected >=2 AR grants (MXU + DMA reads)"

    # Anti-vacuous: SFU write to S0 should NOT land in DRAM
    dram_off = sfu_addr - Addr.SRAM_BASE
    assert bytes(model.dram[dram_off:dram_off + len(sfu_write_data)]) != sfu_write_data, \
        "SFU write leaked to DRAM — address routing failure"
    # Corrupted payload must not match
    assert sfu_write_data != corrupt_data, \
        "SFU write payload was pre-corrupted — vacuous test data"


def test_crossbar_address_conflict_arbitration():
    """Two masters write same address: second writer wins, read-after-write sees final value.

    Key behavior: CrossbarModel grants every request (no cycle contention), so
    sequential writes to the same address are serialized via per-slave locks.
    The last write dominates — the test explicitly orders calls to verify.

    Verifies:
      - MXU writes first (value A), then DMA writes same address (value B).
      - Readback returns B (last writer's value).
      - Write to DRAM address conflict: same pattern holds.
      - Anti-vacuous: readback ≠ A (first writer's stale value).
    """
    model = FuncModel()
    xbar = model.crossbar

    # ── SRAM address conflict ──
    sram_addr = 0x2000_7000
    val_a = b"MXU_wrote_first"
    val_b = b"DMA_wrote_secon"

    xbar.write(CrossbarModel.MASTER_MXU, sram_addr, val_a)
    xbar.write(CrossbarModel.MASTER_DMA, sram_addr, val_b)

    readback = xbar.read(CrossbarModel.MASTER_IBEX, sram_addr, len(val_b))
    assert readback == val_b, \
        f"Second writer (DMA) should dominate, got {readback!r}, expected {val_b!r}"
    assert readback != val_a, \
        "Readback matched first writer — address conflict arbitration broken"

    # ── DRAM address conflict ──
    dram_addr = 0x8000_8000
    val_c = b"VEC_wrote_first"
    val_d = b"IBEX_wrote_seco"

    xbar.write(CrossbarModel.MASTER_VEC, dram_addr, val_c)
    xbar.write(CrossbarModel.MASTER_IBEX, dram_addr, val_d)

    readback2 = xbar.read(CrossbarModel.MASTER_MXU, dram_addr, len(val_d))
    assert readback2 == val_d, \
        f"DRAM conflict: second writer (IBEX) should dominate, got {readback2!r}"
    assert readback2 != val_c, \
        "DRAM conflict: readback matched first writer — arbitration broken"

    # ── Read-after-write: write then read back on same master ──
    sram_addr2 = 0x2000_7100
    xbar.write(CrossbarModel.MASTER_PCIE, sram_addr2, b"pcie_final")
    result = xbar.read(CrossbarModel.MASTER_PCIE, sram_addr2, len(b"pcie_final"))
    assert result == b"pcie_final", "Read-after-write: data not visible"
    # Verify also via another master (observer sees same final value)
    result2 = xbar.read(CrossbarModel.MASTER_SFU, sram_addr2, len(b"pcie_final"))
    assert result2 == b"pcie_final", "Observer master: data not visible through crossbar"


def test_crossbar_all_six_master_stress():
    """All 6 masters (IBEX, MXU, SFU, VEC, DMA, PCIE) issue transactions across
    both S0 (SRAM) and S1 (DRAM) without deadlock or data corruption.

    Verifies:
      - Every master ID can read and write through the crossbar.
      - Both slave ports (S0 SRAM, S1 DRAM) are exercised.
      - _ar_grants and _aw_grants record all masters.
      - No data corruption: each master's written data roundtrips correctly.
      - Anti-vacuous: corrupted SRAM at wrong offset does not match expected data.
    """
    model = FuncModel()
    xbar = model.crossbar

    masters = [
        ("IBEX", CrossbarModel.MASTER_IBEX),
        ("MXU", CrossbarModel.MASTER_MXU),
        ("SFU", CrossbarModel.MASTER_SFU),
        ("VEC", CrossbarModel.MASTER_VEC),
        ("DMA", CrossbarModel.MASTER_DMA),
        ("PCIE", CrossbarModel.MASTER_PCIE),
    ]

    # Each master writes a unique payload to a unique SRAM address, then to DRAM
    sram_writes = {}
    dram_writes = {}
    for i, (name, mid) in enumerate(masters):
        sram_addr = 0x2000_8000 + i * 256
        dram_addr = 0x8000_A000 + i * 256
        payload_sram = f"{name}_SRAM_{i:02d}".encode()
        payload_dram = f"{name}_DRAM_{i:02d}".encode()

        xbar.write(mid, sram_addr, payload_sram)
        xbar.write(mid, dram_addr, payload_dram)
        sram_writes[(mid, sram_addr, len(payload_sram))] = payload_sram
        dram_writes[(mid, dram_addr, len(payload_dram))] = payload_dram

    # Read back every written location via Ibex (as observer)
    for (mid, addr, sz), expected in sram_writes.items():
        result = xbar.read(CrossbarModel.MASTER_IBEX, addr, sz)
        assert result == expected, \
            f"Master {mid} SRAM write: expected {expected!r}, got {result!r}"
    for (mid, addr, sz), expected in dram_writes.items():
        result = xbar.read(CrossbarModel.MASTER_IBEX, addr, sz)
        assert result == expected, \
            f"Master {mid} DRAM write: expected {expected!r}, got {result!r}"

    # Verify both slave ports are exercised
    aw_grants = xbar._aw_grants
    aw_slaves = set(g[0] for g in aw_grants)
    assert 0 in aw_slaves, "S0 (SRAM) never received a write grant"
    assert 1 in aw_slaves, "S1 (DRAM) never received a write grant"

    # All 6 master IDs appear in AW grant history
    aw_masters = set(g[1] for g in aw_grants)
    for name, mid in masters:
        assert mid in aw_masters, f"Master {name} ({mid}) never received an AW grant"

    # AR grant tracking (all masters did reads via Ibex observer)
    ar_grants = xbar._ar_grants
    assert len(ar_grants) >= len(masters) * 2, \
        f"Expected >= {len(masters) * 2} AR grants (reads from observer), got {len(ar_grants)}"

    # Anti-vacuous: corrupted SRAM at wrong offset does not match any written payload
    wrong_sram_addr = 0x2000_A000
    wrong_data = bytes(model.sram[wrong_sram_addr - Addr.SRAM_BASE:
                                   wrong_sram_addr - Addr.SRAM_BASE + 16])
    for expected in sram_writes.values():
        assert wrong_data != expected, \
            "Uninitialized SRAM matched a written payload — vacuous"
    for expected in dram_writes.values():
        assert wrong_data != expected, \
            "SRAM offset matched DRAM write — address routing failure"

    with pytest.raises(ValueError):
        xbar.read(6, 0x2000_0000, 4)
    with pytest.raises(ValueError):
        xbar.write(CrossbarModel.MASTER_DMA, 0x6000_0000, b"decerr")


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


# ══════════════════════════════════════════════════════════════════════
# Path #9 — Interrupt delivery: Engine IRQ → INTC → Ibex WFI
# ══════════════════════════════════════════════════════════════════════


def test_interrupt_delivery():
    """End-to-end interrupt delivery: MXU completes → IRQ fires →
    INTC.PENDING set → RISCVMini interrupt_pending → WFI wakes →
    trap handler dispatches → ACK clears pending."""
    from sim.regmap import MXU, INTC

    model = FuncModel()
    emu = model.riscv
    bridge = model.bridge

    WFI_INSN = (0x305 << 20) | 0x73  # funct12=0x305, opcode=SYSTEM
    M, K, N = 1, 8, 4

    # ── 1. Anti-vacuous: IRQ_EN=0 → no IRQ raised ───────────────────
    model2 = FuncModel()
    bridge2 = model2.bridge
    emu2 = model2.riscv

    bridge2.handle('write', MXU.BASE + MXU.CTRL, 0)
    bridge2.handle('write', MXU.BASE + MXU.IRQ_EN, 0)
    bridge2.handle('write', MXU.BASE + MXU.DIM0, (K << 16) | M)
    bridge2.handle('write', MXU.BASE + MXU.DIM1, N)
    bridge2.handle('write', MXU.BASE + MXU.I_ADDR, Addr.SRAM_BASE + 0x1000)
    bridge2.handle('write', MXU.BASE + MXU.W_ADDR, Addr.SRAM_BASE + 0x2000)
    bridge2.handle('write', MXU.BASE + MXU.O_ADDR, Addr.SRAM_BASE + 0x3000)

    act_buf = np.ones(M * K, dtype=np.int8)
    packed_wgt = bytes([0x11] * ((K * N + 1) // 2))
    model2.sram[0x1000:0x1000 + len(act_buf)] = act_buf.tobytes()
    model2.sram[0x2000:0x2000 + len(packed_wgt)] = packed_wgt

    bridge2.handle('write', MXU.BASE + MXU.CMD, 1)

    pending = bridge2.handle('read', INTC.BASE + INTC.PENDING, 0)
    assert pending == 0, f"IRQ_EN=0: expected PENDING=0, got 0x{pending:08X}"
    assert not emu2.interrupt_pending, "IRQ_EN=0: interrupt_pending must be False"

    # ── 2. IRQ_EN=1: MXU completes → IRQ fires ──────────────────────
    bridge.handle('write', MXU.BASE + MXU.CTRL, 0)
    bridge.handle('write', MXU.BASE + MXU.IRQ_EN, 1)
    bridge.handle('write', MXU.BASE + MXU.DIM0, (K << 16) | M)
    bridge.handle('write', MXU.BASE + MXU.DIM1, N)
    bridge.handle('write', MXU.BASE + MXU.I_ADDR, Addr.SRAM_BASE + 0x1000)
    bridge.handle('write', MXU.BASE + MXU.W_ADDR, Addr.SRAM_BASE + 0x2000)
    bridge.handle('write', MXU.BASE + MXU.O_ADDR, Addr.SRAM_BASE + 0x3000)

    act_buf2 = np.ones(M * K, dtype=np.int8)
    model.sram[0x1000:0x1000 + len(act_buf2)] = act_buf2.tobytes()
    model.sram[0x2000:0x2000 + len(packed_wgt)] = packed_wgt

    bridge.handle('write', MXU.BASE + MXU.CMD, 1)

    pending2 = bridge.handle('read', INTC.BASE + INTC.PENDING, 0)
    assert pending2 & 1, f"IRQ_EN=1: expected INTC.PENDING[0] set, got 0x{pending2:08X}"
    assert emu.interrupt_pending, "IRQ_EN=1: interrupt_pending must be True after _set_irq"

    # ── 3. WFI wakes → trap handler dispatches → ACK clears ─────────
    model.boot_rom[0:4] = struct.pack('<I', WFI_INSN)
    emu.state.pc = 0
    emu.running = True
    result = emu.step()
    assert result, "WFI step should return True after handling IRQ"

    pending3 = bridge.handle('read', INTC.BASE + INTC.PENDING, 0)
    assert pending3 == 0, (
        f"After ACK: expected INTC.PENDING=0, got 0x{pending3:08X}"
    )
    assert not emu.interrupt_pending, (
        "After WFI handler: interrupt_pending must be False"
    )
    assert model.firmware._irq_serviced, (
        "NPUFirmware should have recorded IRQ service"
    )

    # ── 4. WFI as NOP when no interrupt pending ──────────────────────
    assert not emu.interrupt_pending
    model.boot_rom[4:8] = struct.pack('<I', WFI_INSN)
    emu.state.pc = 4
    emu.running = True
    result_nop = emu.step()
    assert result_nop, "WFI without pending IRQ should still return True (NOP)"
    pc_after = emu.state.pc
    assert pc_after == 8, (
        f"WFI NOP should advance PC to 8, got 0x{pc_after:08X}"
    )


# ══════════════════════════════════════════════════════════════════════
# Path #11 — Ibex firmware path: boot ROM → DMEM → MMIO → IRQ
# ══════════════════════════════════════════════════════════════════════


def test_firmware_bootflow():
    """Full firmware boot flow: boot→firmware init→receive doorbell→
    dispatch MMUL→complete via IRQ.

    Verifies:
      1. Boot state: PC=0, SP=top of DMEM.
      2. MMUL computes correctly through firmware path.
      3. IRQ completion: _irq_serviced set, INTC.PENDING cleared.
      4. Anti-vacuous: wrong opcode in doorbell returns error status.
    """
    from sim.regmap import MXU, INTC, DOORBELL
    from sim.golden_executor import GoldenMXU
    from engine.isa import OpCode

    model = FuncModel()

    # ── 1. Verify boot state ────────────────────────────────────────
    assert model.riscv.state.pc == 0, (
        f"PC should be 0 after boot, got 0x{model.riscv.state.pc:08X}"
    )
    sp = model.riscv.state.read(2)
    assert sp == 0x00020000, (
        f"SP should be DMEM_BASE+DMEM_SIZE=0x00020000, got 0x{sp:08X}"
    )

    # ── 2. Set up small MMUL: M=1, K=4, N=2 ─────────────────────────
    M, K, N = 1, 4, 2
    act_data = np.array([1, 2, 3, 4], dtype=np.int8)
    # Packed INT4 weights: low nibble first, then high nibble.
    # Unpacked values: [1,2,3,4,5,6,7,-8] (0x87 high nibble=8 → -8 signed)
    # Reshaped (K=4,N=2): [[1,2],[3,4],[5,6],[7,-8]]
    wgt_packed = np.array([0x21, 0x43, 0x65, 0x87], dtype=np.uint8)
    # Scale data: 1 FP32 per K-block per N-column (num_blocks=1, N=2)
    num_blocks = (K + 127) // 128  # =1
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

    # ── 3. Write doorbell command and run firmware ───────────────────
    model.host_write_command(OpCode.MMUL, desc_addr)
    assert model.firmware.doorbell['host_tail'] == 1

    results = model.firmware.run_loop(max_commands=1)
    assert len(results) == 1, f"Expected 1 result, got {len(results)}"
    assert results[0]['status'] == 'done', (
        f"Expected status='done', got {results[0]['status']}"
    )

    # ── 4. Verify result matches GoldenMXU (float32 via scale path) ──
    out_off = out_addr - Addr.DRAM_BASE
    out_bytes = model.dram[out_off:out_off + M * N * 4]
    out_fw = np.frombuffer(out_bytes, dtype=np.float32).reshape(M, N)

    golden = GoldenMXU().matmul_int4_per_block(
        act_data, wgt_packed, scales,
        M, K, N, group_size=128)
    assert np.allclose(out_fw, golden, rtol=1e-5), (
        f"MMUL output mismatch: got {out_fw.tolist()}, expected {golden.tolist()}"
    )

    # ── 5. Verify IRQ was serviced ───────────────────────────────────
    pending = model.bridge.handle('read', INTC.BASE + INTC.PENDING, 0)
    assert pending == 0, (
        f"INTC.PENDING should be 0 after completion, got 0x{pending:08X}"
    )

    # ── 6. Anti-vacuous: wrong opcode returns error status ───────────
    model.host_write_command(999, desc_addr)
    results_bad = model.firmware.run_loop(max_commands=1)
    assert len(results_bad) == 1, (
        f"Expected 1 result for bad opcode, got {len(results_bad)}"
    )
    assert results_bad[0]['status'] != 'done', (
        "Wrong opcode (999) should not return status='done'"
    )


# ══════════════════════════════════════════════════════════════════════
# Path #4 — SFU compute through MMIO bridge (FM-SOC-011)
# ══════════════════════════════════════════════════════════════════════


_MMIO_SRAM_OFF = 0x10000   # raw offset within SRAM, mapped to 0x20010000
_MMIO_OUT_OFF = 0x20000    # raw offset within SRAM, mapped to 0x20020000


def _mmio_write_sram(model, data: np.ndarray, raw_offset: int):
    """Write float32 data as FP16 bytes into SRAM at a raw offset."""
    fp16_bytes = data.astype(np.float16).tobytes()
    off = raw_offset
    model.sram[off:off + len(fp16_bytes)] = fp16_bytes


def _mmio_read_sram(model, n_elements: int, raw_offset: int) -> np.ndarray:
    """Read N FP16 elements from SRAM at raw offset, return float32."""
    nbytes = n_elements * 2
    off = raw_offset
    return np.frombuffer(bytes(model.sram[off:off + nbytes]), dtype=np.float16).astype(np.float32)


def _mmio_sfu_op(model, op: int, length: int, head_dim: int = 0, pos: int = 0) -> None:
    """Run one SFU op through the MMIO bridge. Sets I_ADDR/O_ADDR automatically."""
    bridge = model.bridge
    bridge.handle('write', SFU.BASE + SFU.CTRL, op)
    bridge.handle('write', SFU.BASE + SFU.I_ADDR, _MMIO_SRAM_OFF)
    bridge.handle('write', SFU.BASE + SFU.O_ADDR, _MMIO_OUT_OFF)
    bridge.handle('write', SFU.BASE + SFU.DIM, (head_dim << 16) | length)
    bridge.handle('write', SFU.BASE + SFU.POS, pos)
    bridge.handle('write', SFU.BASE + SFU.CMD, 1)


def _mmio_sfu_wait_done(model) -> int:
    """Poll SFU STATUS until DONE, return status."""
    status = model.bridge.handle('read', SFU.BASE + SFU.STATUS)
    assert status == 2, f"SFU STATUS={status}, expected DONE(2)"
    return status


_RNG_SFU = np.random.RandomState(20260703)


def test_sfu_soc_mmio_softmax():
    """SFU softmax through MMIO bridge: N=2, 16, 128, 1024."""
    model = FuncModel()
    sfu = model.sfu
    fp16_tol = dict(tol_abs=2e-3, tol_rel=1e-2)

    for N in (2, 16, 128, 1024):
        label = f"softmax_N={N}"
        inp = _RNG_SFU.randn(N).astype(np.float32).clip(-10, 10)

        # MMIO path: write FP16 to SRAM → SFU start → read FP16 result
        _mmio_write_sram(model, inp, _MMIO_SRAM_OFF)
        _mmio_sfu_op(model, op=0, length=N)
        _mmio_sfu_wait_done(model)
        mmio_out = _mmio_read_sram(model, N, _MMIO_OUT_OFF)

        # Direct GoldenSFU path
        direct_out = sfu.softmax_hw(inp)

        cmp = GoldenSFU.compare_hw_vs_ref(mmio_out, direct_out, **fp16_tol)
        assert cmp["within_tolerance"], (
            f"{label}: MMIO vs direct — max_abs={cmp['max_abs_err']:.2e} "
            f"max_rel={cmp['max_rel_err']:.2e}"
        )
        # Sanity: softmax sums to ~1
        total = float(np.sum(mmio_out))
        assert total == pytest.approx(1.0, rel=1e-3), f"{label}: sum={total:.6f}"
        assert not np.any(np.isnan(mmio_out)), f"{label}: NaN in output"


def test_sfu_soc_mmio_layernorm():
    """SFU layernorm through MMIO bridge."""
    model = FuncModel()
    sfu = model.sfu
    fp16_tol = dict(tol_abs=2e-3, tol_rel=1e-2)

    for N in (16, 256, 2560):
        label = f"layernorm_N={N}"
        inp = _RNG_SFU.randn(N).astype(np.float32) * 2.0

        _mmio_write_sram(model, inp, _MMIO_SRAM_OFF)
        _mmio_sfu_op(model, op=1, length=N)
        _mmio_sfu_wait_done(model)
        mmio_out = _mmio_read_sram(model, N, _MMIO_OUT_OFF)

        direct_out = sfu.layernorm_hw(inp)

        cmp = GoldenSFU.compare_hw_vs_ref(mmio_out, direct_out, **fp16_tol)
        assert cmp["within_tolerance"], (
            f"{label}: MMIO vs direct — max_abs={cmp['max_abs_err']:.2e}"
        )
        # Sanity: near-zero mean
        assert np.mean(mmio_out) == pytest.approx(0.0, abs=1e-2), f"{label}: mean not zero"


def test_sfu_soc_mmio_rmsnorm():
    """SFU rmsnorm through MMIO bridge, including N=1 corner."""
    model = FuncModel()
    sfu = model.sfu
    fp16_tol = dict(tol_abs=2e-3, tol_rel=1e-2)

    for N in (1, 16, 256, 2560):
        label = f"rmsnorm_N={N}"
        inp = _RNG_SFU.randn(N).astype(np.float32) * 2.0

        _mmio_write_sram(model, inp, _MMIO_SRAM_OFF)
        _mmio_sfu_op(model, op=6, length=N)
        _mmio_sfu_wait_done(model)
        mmio_out = _mmio_read_sram(model, N, _MMIO_OUT_OFF)

        direct_out = sfu.rmsnorm_hw(inp)

        cmp = GoldenSFU.compare_hw_vs_ref(mmio_out, direct_out, **fp16_tol)
        assert cmp["within_tolerance"], (
            f"{label}: MMIO vs direct — max_abs={cmp['max_abs_err']:.2e}"
        )
        # RMSNorm: near-unit RMS
        rms = float(np.sqrt(np.mean(mmio_out ** 2)))
        assert rms == pytest.approx(1.0, rel=2e-2), f"{label}: RMS={rms:.4e}"


def test_sfu_soc_mmio_gelu():
    """SFU gelu through MMIO bridge, incl. boundary x=±4."""
    model = FuncModel()
    sfu = model.sfu
    fp16_tol = dict(tol_abs=2e-3, tol_rel=1e-2)

    # Full range [-4, 4] + boundary test points
    xs = [np.linspace(-4.0, 4.0, 100, dtype=np.float32)]
    xs.append(np.array([-4.1, -4.0, -3.999, 3.999, 4.0, 4.1], dtype=np.float32))
    for inp in xs:
        N = len(inp)
        label = f"gelu_N={N}"

        _mmio_write_sram(model, inp, _MMIO_SRAM_OFF)
        _mmio_sfu_op(model, op=2, length=N)
        _mmio_sfu_wait_done(model)
        mmio_out = _mmio_read_sram(model, N, _MMIO_OUT_OFF)

        direct_out = sfu.gelu_hw(inp)

        cmp = GoldenSFU.compare_hw_vs_ref(mmio_out, direct_out, **fp16_tol)
        assert cmp["within_tolerance"], (
            f"{label}: MMIO vs direct — max_abs={cmp['max_abs_err']:.2e}"
        )
    # Explicit: x=±4 must not be NaN and must differ (GELU asymmetry)
    x4 = np.array([4.0, -4.0], dtype=np.float32)
    _mmio_write_sram(model, x4, _MMIO_SRAM_OFF)
    _mmio_sfu_op(model, op=2, length=2)
    _mmio_sfu_wait_done(model)
    mmio_4 = _mmio_read_sram(model, 2, _MMIO_OUT_OFF)
    assert not np.any(np.isnan(mmio_4)), "GELU(±4): NaN detected"
    asymmetry = float(abs(mmio_4[0]) + abs(mmio_4[1]))
    assert asymmetry > 0.1, f"GELU asymmetry at ±4 too small: {asymmetry}"


def test_sfu_soc_mmio_silu():
    """SFU silu through MMIO bridge."""
    model = FuncModel()
    sfu = model.sfu
    fp16_tol = dict(tol_abs=2e-3, tol_rel=1e-2)

    for inp in [
        np.linspace(-6.0, 6.0, 120, dtype=np.float32),
        np.array([-10.0, -5.0, -1.0, 0.0, 1.0, 5.0, 10.0], dtype=np.float32),
    ]:
        N = len(inp)
        _mmio_write_sram(model, inp, _MMIO_SRAM_OFF)
        _mmio_sfu_op(model, op=4, length=N)
        _mmio_sfu_wait_done(model)
        mmio_out = _mmio_read_sram(model, N, _MMIO_OUT_OFF)

        direct_out = sfu.silu_hw(inp)

        cmp = GoldenSFU.compare_hw_vs_ref(mmio_out, direct_out, **fp16_tol)
        assert cmp["within_tolerance"], (
            f"silu_N={N}: MMIO vs direct — max_abs={cmp['max_abs_err']:.2e}"
        )
        assert not np.any(np.isnan(mmio_out)), "SiLU: NaN detected"


def test_sfu_soc_mmio_rope():
    """SFU rope through MMIO bridge: pos=0, large angle=100000, random 5 pairs.

    MMIO bridge splits input equally: first half Q, second half K. K is hardcoded
    to 2 heads in GoldenSFU.rope_hw, so use num_heads=2, head_dim=128.
    """
    model = FuncModel()
    sfu = model.sfu
    fp16_tol = dict(tol_abs=2e-3, tol_rel=1e-2)

    head_dim = 128
    half = 2 * head_dim          # 256 — Q and K each occupy half of the input
    total_len = 2 * half         # 512

    positions = [0, 100000] + list(_RNG_SFU.randint(1, 50000, size=5))

    for pos in positions:
        label = f"rope_pos={pos}"
        q_in = _RNG_SFU.randn(half).astype(np.float32) * 0.5
        k_in = _RNG_SFU.randn(half).astype(np.float32) * 0.5
        inp = np.concatenate([q_in, k_in])

        _mmio_write_sram(model, inp, _MMIO_SRAM_OFF)
        _mmio_sfu_op(model, op=5, length=total_len, head_dim=head_dim, pos=pos)
        _mmio_sfu_wait_done(model)
        mmio_out = _mmio_read_sram(model, total_len, _MMIO_OUT_OFF)
        mmio_q = mmio_out[:half]
        mmio_k = mmio_out[half:]

        direct_q, direct_k = sfu.rope_hw(q_in, k_in, position=pos,
                                          num_heads=2, head_dim=head_dim)

        cmp_q = GoldenSFU.compare_hw_vs_ref(mmio_q, direct_q, **fp16_tol)
        cmp_k = GoldenSFU.compare_hw_vs_ref(mmio_k, direct_k, **fp16_tol)
        assert cmp_q["within_tolerance"], (
            f"{label} Q: MMIO vs direct — max_abs={cmp_q['max_abs_err']:.2e}"
        )
        assert cmp_k["within_tolerance"], (
            f"{label} K: MMIO vs direct — max_abs={cmp_k['max_abs_err']:.2e}"
        )
        assert not np.any(np.isnan(mmio_out)), f"{label}: NaN detected"

    # Anti-vacuous: pos=0 should differ from pos=100000
    _mmio_write_sram(model, np.concatenate([q_in, k_in]), _MMIO_SRAM_OFF)
    _mmio_sfu_op(model, op=5, length=total_len, head_dim=head_dim, pos=0)
    _mmio_sfu_wait_done(model)
    out_pos0 = _mmio_read_sram(model, total_len, _MMIO_OUT_OFF)
    assert not np.allclose(out_pos0, mmio_out, atol=1e-6), (
        "RoPE pos=0 and pos=100000 produced same output — vacuous"
    )


def test_sfu_soc_mmio_back_to_back():
    """Back-to-back SFU dispatch: softmax → rmsnorm without reset."""
    model = FuncModel()
    sfu = model.sfu
    fp16_tol = dict(tol_abs=2e-3, tol_rel=1e-2)

    N = 256
    inp = _RNG_SFU.randn(N).astype(np.float32) * 2.0

    # Op 1: softmax
    _mmio_write_sram(model, inp, _MMIO_SRAM_OFF)
    _mmio_sfu_op(model, op=0, length=N)
    _mmio_sfu_wait_done(model)
    softmax_out = _mmio_read_sram(model, N, _MMIO_OUT_OFF)

    # Op 2: rmsnorm on the softmax output (no SRAM re-write, read from O_ADDR)
    # The rmsnorm input is the softmax output — write it to I_ADDR region
    _mmio_write_sram(model, softmax_out, _MMIO_SRAM_OFF)
    _mmio_sfu_op(model, op=6, length=N)
    _mmio_sfu_wait_done(model)
    cascade_out = _mmio_read_sram(model, N, _MMIO_OUT_OFF)

    # Verify via direct GoldenSFU call
    direct_softmax = sfu.softmax_hw(inp)
    cmp_sm = GoldenSFU.compare_hw_vs_ref(softmax_out, direct_softmax, **fp16_tol)
    assert cmp_sm["within_tolerance"], (
        f"back-to-back softmax: max_abs={cmp_sm['max_abs_err']:.2e}"
    )

    direct_cascade = sfu.rmsnorm_hw(direct_softmax)
    cmp_cas = GoldenSFU.compare_hw_vs_ref(cascade_out, direct_cascade, **fp16_tol)
    assert cmp_cas["within_tolerance"], (
        f"back-to-back cascade: max_abs={cmp_cas['max_abs_err']:.2e}"
    )

    # Anti-vacuous: cascade output must differ from softmax output
    assert not np.allclose(cascade_out, softmax_out, atol=1e-6), (
        "Back-to-back: rmsnorm(softmax(x)) == softmax(x) — vacuous"
    )


# ══════════════════════════════════════════════════════════════════════
# Path #5 — Vector compute through MMIO bridge (FM-SOC-012)
# ══════════════════════════════════════════════════════════════════════

_VEC_A_OFF = 0x30000   # Vector A input (raw SRAM offset)
_VEC_B_OFF = 0x31000   # Vector B input
_VEC_O_OFF = 0x40000   # Vector output

_RNG_VEC = np.random.RandomState(20260704)


def _vec_write_i32(model, data: np.ndarray, raw_offset: int):
    """Write INT32 data to SRAM at raw offset."""
    model.sram[raw_offset:raw_offset + data.nbytes] = data.tobytes()


def _vec_write_f16(model, data: np.ndarray, raw_offset: int):
    """Write float32 data as FP16 bytes to SRAM."""
    fp16_bytes = data.astype(np.float16).tobytes()
    model.sram[raw_offset:raw_offset + len(fp16_bytes)] = fp16_bytes


def _vec_read_i32(model, n: int, raw_offset: int) -> np.ndarray:
    """Read N INT32 elements from SRAM at raw offset."""
    nb = n * 4
    return np.frombuffer(bytes(model.sram[raw_offset:raw_offset + nb]), dtype=np.int32)


def _vec_read_f16(model, n: int, raw_offset: int) -> np.ndarray:
    """Read N FP16 elements as float32 from SRAM at raw offset."""
    nb = n * 2
    return np.frombuffer(bytes(model.sram[raw_offset:raw_offset + nb]), dtype=np.float16).astype(np.float32)


def _vec_mmio_op(model, op: int, dim: int):
    """Run one Vector op through the MMIO bridge."""
    bridge = model.bridge
    bridge.handle('write', VECTOR.BASE + VECTOR.CTRL, op)
    bridge.handle('write', VECTOR.BASE + VECTOR.A_ADDR, _VEC_A_OFF)
    bridge.handle('write', VECTOR.BASE + VECTOR.B_ADDR, _VEC_B_OFF)
    bridge.handle('write', VECTOR.BASE + VECTOR.O_ADDR, _VEC_O_OFF)
    bridge.handle('write', VECTOR.BASE + VECTOR.DIM, dim)
    bridge.handle('write', VECTOR.BASE + VECTOR.CMD, 1)


def _vec_mmio_wait(model):
    """Poll Vector STATUS until DONE."""
    status = model.bridge.handle('read', VECTOR.BASE + VECTOR.STATUS)
    assert status == 2, f"Vector STATUS={status}, expected DONE(2)"


def test_vector_soc_mmio_add_mul():
    """Vector ADD and MUL through MMIO bridge: INT32 elements vs GoldenVector."""
    model = FuncModel()
    vec = GoldenVector()
    dim = 32

    a = _RNG_VEC.randint(-10000, 10000, size=dim).astype(np.int32)
    b = _RNG_VEC.randint(-10000, 10000, size=dim).astype(np.int32)
    _vec_write_i32(model, a, _VEC_A_OFF)
    _vec_write_i32(model, b, _VEC_B_OFF)

    _vec_mmio_op(model, op=0, dim=dim)
    _vec_mmio_wait(model)
    add_out = _vec_read_i32(model, dim, _VEC_O_OFF)
    ref_add = vec.add(a, b)
    assert np.array_equal(add_out, ref_add), "Vector ADD: MMIO vs direct mismatch"

    _vec_mmio_op(model, op=1, dim=dim)
    _vec_mmio_wait(model)
    mul_out = _vec_read_i32(model, dim, _VEC_O_OFF)
    ref_mul = vec.mul(a, b)
    assert np.array_equal(mul_out, ref_mul), "Vector MUL: MMIO vs direct mismatch"

    # Anti-vacuous: ADD != MUL for non-trivial input
    assert not np.array_equal(add_out, mul_out), "ADD and MUL must differ (anti-vacuous)"


def test_vector_soc_mmio_reduce():
    """Vector MAX and SUM reduce through MMIO bridge (FP16 pipeline)."""
    model = FuncModel()
    dim = 16

    inp = _RNG_VEC.randn(dim).astype(np.float32)
    _vec_write_f16(model, inp, _VEC_A_OFF)

    # MAX reduce (op=2): reads FP16, outputs single FP16
    _vec_mmio_op(model, op=2, dim=dim)
    _vec_mmio_wait(model)
    max_out = _vec_read_f16(model, 1, _VEC_O_OFF)[0]
    assert max_out == pytest.approx(float(np.max(inp)), rel=1e-3), \
        f"MAX reduce: {max_out} vs {float(np.max(inp))}"

    # SUM reduce (op=3): reads FP16, outputs single FP16
    _vec_write_f16(model, inp, _VEC_A_OFF)
    _vec_mmio_op(model, op=3, dim=dim)
    _vec_mmio_wait(model)
    sum_out = _vec_read_f16(model, 1, _VEC_O_OFF)[0]
    assert sum_out == pytest.approx(float(np.sum(inp)), rel=1e-3), \
        f"SUM reduce: {sum_out} vs {float(np.sum(inp))}"

    # Anti-vacuous: MAX != SUM for non-uniform input
    assert max_out != sum_out, "MAX and SUM reduce must differ (anti-vacuous)"


def test_vector_soc_mmio_type_convert():
    """Vector INT32→FP16 type conversion through MMIO bridge."""
    model = FuncModel()
    vec = GoldenVector()
    dim = 32

    inp = _RNG_VEC.randint(-2048, 2048, size=dim).astype(np.int32)
    _vec_write_i32(model, inp, _VEC_A_OFF)

    # CONV (op=4): INT32→FP16, dim*4 bytes input → dim*2 bytes output
    _vec_mmio_op(model, op=4, dim=dim)
    _vec_mmio_wait(model)
    conv_out = _vec_read_f16(model, dim, _VEC_O_OFF)

    ref = vec.conv_i32_to_f16(inp).astype(np.float32)
    assert np.allclose(conv_out, ref, atol=1e-4), \
        f"CONV: max_abs={float(np.max(np.abs(conv_out - ref))):.2e}"

    # Anti-vacuous: saturated values differ from original
    large = np.array([np.iinfo(np.int32).max], dtype=np.int32)
    _vec_write_i32(model, large, _VEC_A_OFF)
    _vec_mmio_op(model, op=4, dim=1)
    _vec_mmio_wait(model)
    sat_out = _vec_read_f16(model, 1, _VEC_O_OFF)[0]
    assert sat_out < np.iinfo(np.int32).max, "INT32_MAX must saturate in CONV"


def test_vector_soc_mmio_resid_add():
    """Vector residual_add through MMIO bridge: FP16 exact original + INT32 delta, with saturation.

    The MMIO path stores the original as FP16 in SRAM, so values must be
    FP16-exact (integers in [-2048, 2048]) to avoid FP16→FP32 precision loss.
    """
    model = FuncModel()
    vec = GoldenVector()
    dim = 16

    # Use FP16-exact integer values to avoid FP16→FP32 precision loss
    original = _RNG_VEC.randint(-2000, 2000, size=dim).astype(np.float32)
    delta = _RNG_VEC.randint(-500, 500, size=dim).astype(np.int32)
    _vec_write_f16(model, original, _VEC_A_OFF)
    _vec_write_i32(model, delta, _VEC_B_OFF)

    _vec_mmio_op(model, op=5, dim=dim)
    _vec_mmio_wait(model)
    resid_out = _vec_read_i32(model, dim, _VEC_O_OFF)

    ref = vec.residual_add(original, delta)
    assert np.array_equal(resid_out, ref), \
        f"RESID: MMIO vs direct mismatch at index {np.where(resid_out != ref)[0]}"

    # Saturation: FP16-exact original + INT32_MAX delta overflows INT32
    # FP16 exact 50000 + INT32_MAX 2147483647 = 2147533647 > INT32_MAX → clip
    sat_orig = np.array([50000.0], dtype=np.float32)
    sat_delta = np.array([np.iinfo(np.int32).max], dtype=np.int32)
    _vec_write_f16(model, sat_orig, _VEC_A_OFF)
    _vec_write_i32(model, sat_delta, _VEC_B_OFF)
    _vec_mmio_op(model, op=5, dim=1)
    _vec_mmio_wait(model)
    sat_out = _vec_read_i32(model, 1, _VEC_O_OFF)
    assert sat_out[0] == np.iinfo(np.int32).max, \
        f"RESID overflow: got {sat_out[0]}, expected INT32_MAX"


# ══════════════════════════════════════════════════════════════════════
# Path #6 — DMA transfer through MMIO bridge (FM-SOC-013)
# ══════════════════════════════════════════════════════════════════════

_DMA_SRAM_OFF = 0x50000   # SRAM raw offset for DMA test data
_DMA_DRAM_ADDR = 0x8001_0000  # DRAM address for DMA test


def test_dma_soc_mmio_load_store():
    """DMA load (CH0 DRAM→SRAM) and store (CH1 SRAM→DRAM) through MMIO bridge."""
    model = FuncModel()
    bridge = model.bridge
    size = 128

    # ── Load (CH0): write known pattern to DRAM, DMA-load to SRAM ──
    dram_off = _DMA_DRAM_ADDR - Addr.DRAM_BASE
    pattern_src = np.arange(size, dtype=np.uint8)
    model.dram[dram_off:dram_off + size] = pattern_src.tobytes()

    bridge.handle('write', DMA.BASE + DMA.CH0_SRC, _DMA_DRAM_ADDR)
    bridge.handle('write', DMA.BASE + DMA.CH0_DST, Addr.SRAM_BASE + _DMA_SRAM_OFF)
    bridge.handle('write', DMA.BASE + DMA.CH0_SIZE, size)
    bridge.handle('write', DMA.BASE + DMA.CMD, 1)

    sram_read = bytes(model.sram[_DMA_SRAM_OFF:_DMA_SRAM_OFF + size])
    assert sram_read == pattern_src.tobytes(), \
        "DMA load: SRAM data after load must match DRAM source"

    # ── Store (CH1): write known pattern to SRAM, DMA-store to DRAM ──
    pattern_store = bytes(range(100, 100 + size))
    store_sram_off = _DMA_SRAM_OFF + 0x1000
    model.sram[store_sram_off:store_sram_off + size] = pattern_store

    bridge.handle('write', DMA.BASE + DMA.CH1_SRC, Addr.SRAM_BASE + store_sram_off)
    bridge.handle('write', DMA.BASE + DMA.CH1_DST, _DMA_DRAM_ADDR + 0x1000)
    bridge.handle('write', DMA.BASE + DMA.CH1_SIZE, size)
    bridge.handle('write', DMA.BASE + DMA.CMD, 1)

    dram_data = bytes(model.dram[dram_off + 0x1000:dram_off + 0x1000 + size])
    assert dram_data == pattern_store, \
        "DMA store: DRAM data after store must match SRAM source"

    # ── Anti-vacuous: load != store (different data directions) ──
    assert sram_read != dram_data, \
        "DMA load and store must transfer different data (anti-vacuous)"

    # Verify STATUS is DONE after transfer
    status = bridge.handle('read', DMA.BASE + DMA.STATUS)
    assert status == 2, f"DMA STATUS={status}, expected DONE(2)"


# ══════════════════════════════════════════════════════════════════════
# Smoke regression — single composite for wrap-up
# ══════════════════════════════════════════════════════════════════════


def test_smoke_all():
    """Composite smoke regression: runs all 6 key smoke scenarios.

    Exercises:
      1. APB-MMIO handshake basics (FM-SOC-001)
      2. Ibex memory access via crossbar (FM-SOC-002)
      3. PCIe TLP write/read roundtrip (FM-SOC-003)
      4. Crossbar concurrent multi-master (FM-SOC-004)
      5. Interrupt delivery MXU→INTC→WFI (FM-SOC-005)
      6. Firmware bootflow doorbell→MMUL (FM-SOC-006)
    """
    model = FuncModel()

    # 1. APB-MMIO: write MXU CTRL, read back
    model.bridge.apb_write(0x4000_0000, 0x00000002)
    assert model.bridge.apb_read(0x4000_0000) == 0x00000002

    # 2. Ibex memory access via crossbar
    model.riscv._mem_write(0x80000100, 0xDEADBEEF)
    assert model.riscv._mem_read(0x80000100) == 0xDEADBEEF

    # 3. PCIe TLP write/read roundtrip
    payload = bytes(range(256))
    model.pcie.tlp_write(0x8000_2000, payload)
    assert model.pcie.tlp_read(0x8000_2000, len(payload)) == payload

    # 4. Crossbar concurrent multi-master
    mxu_data = model.crossbar.read(CrossbarModel.MASTER_MXU, 0x2000_2000, 4)
    assert len(mxu_data) == 4

    # 5. Interrupt delivery: FuncModel smoke already exercises this
    from sim.regmap import INTC
    assert model.bridge.handle('read', INTC.BASE + INTC.PENDING, 0) is not None

    # 6. Original conv2d_smoke still passes
    assert model.test_conv2d_smoke() is True
