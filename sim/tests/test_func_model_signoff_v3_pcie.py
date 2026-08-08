"""Func Model signoff v3 — PCIe DMA pathway functional verification.

Re-exports the 8 existing DmaEngine unit tests from ``test_pcie_dma_fm.py``
and adds 4 integration-level signoff tests:

- Host→NPU MWr (256B via crossbar → DRAM)
- NPU→Host MRd+CplD (512B reassembly)
- Descriptor IRQ chain (3 descriptors, 3/3 IRQ fire)
- Tag pool no-leak (256→0→256 cycle)

All tests are golden-reference Func Model assertions — no RTL/Cocotb.
"""

import struct

import pytest

from models.crossbar import CrossbarModel
from models.pcie import DmaEngine

# ── Re-export existing 8 DmaEngine unit tests ──────────────────────────
from tests.test_pcie_dma_fm import (  # noqa: F401, E402
    test_tc1_single_mwr_256,
    test_tc2_mrd_split_completion,
    test_tc3_unaligned_transfer,
    test_tc4_max_length_4096,
    test_tc5_concurrent_read_write,
    test_tc6_completion_error_ur,
    test_tc7_axi_dec_error,
    test_tag_enums_distinct,
)


# ═══════════════════════════════════════════════════════════════════════════
# Signoff IT-1: Host→NPU MWr via crossbar → DRAM (256B)
# ═══════════════════════════════════════════════════════════════════════════


def test_pcie_dma_host_to_npu_mwr():
    """Host writes 256B via PCIe TLP → crossbar → DRAM; verify data integrity.

    Simulates the full host→NPU data path:
    1. Host deposits 256B in host_mem at pcie_addr=0x0000 (simulated MWr).
    2. NPU firmware submits a read descriptor to pull that data into DRAM
       via the crossbar (submit_read_desc).
    3. Verify the data landed byte-exact in DRAM and no IRQ error.

    This exercises DmaEngine + CrossbarModel integration: TLP read →
    completion reassembly → AXI write through the M=6/S=2 crossbar.
    """
    # Create crossbar with 16KB DRAM window (enough for our test)
    sram = bytearray(4096)
    dram = bytearray(16384)
    xbar = CrossbarModel(sram=sram, dram=dram)
    dma = DmaEngine(crossbar=xbar)

    # Seed host memory with a known pattern
    host_pattern = bytes(
        (i * 7 + 13) & 0xFF for i in range(256)
    )
    pcie_src = 0x0000
    dma.host_mem[pcie_src : pcie_src + 256] = host_pattern

    # NPU target in DRAM: DRAM_BASE + 0x1000
    axi_dst = 0x8000_1000
    tag = 10

    dma.submit_read_desc(
        pcie_addr=pcie_src,
        axi_addr=axi_dst,
        length=256,
        tag=tag,
    )

    # Descriptor must complete without error
    statuses = dma.desc_status
    assert len(statuses) == 1, f"Expected 1 completion, got {len(statuses)}"
    completed_tag, err = statuses[0]
    assert completed_tag == tag
    assert err == DmaEngine.DESC_ERR_NONE, f"Descriptor error: {err}"

    # IRQ must fire
    assert dma.irq, "IRQ must fire on descriptor completion"

    # Verify data landed in DRAM at the correct offset
    dram_offset = axi_dst - 0x8000_0000
    dram_slice = bytes(dram[dram_offset : dram_offset + 256])
    assert dram_slice == host_pattern, (
        f"DRAM data mismatch: first 16 bytes expected "
        f"{host_pattern[:16].hex()}, got {dram_slice[:16].hex()}"
    )

    # Anti-vacuous: a flipped byte must be detected
    corrupted = bytearray(dram_slice)
    corrupted[128] ^= 0xFF
    assert bytes(corrupted) != host_pattern, "Vacuous — corrupted data matched"


# ═══════════════════════════════════════════════════════════════════════════
# Signoff IT-2: NPU→Host MRd+CplD (512B reassembly)
# ═══════════════════════════════════════════════════════════════════════════


def test_pcie_dma_npu_to_host_mrd():
    """NPU reads 512B from host via MRd+CplD; verify reassembled data.

    Simulates the full NPU→host read pathway:
    1. Seed host_mem with 512B at pcie_addr=0x4000.
    2. NPU submits a read descriptor to pull 512B from host to DRAM.
    3. Use tlp_read_with_reassembly to inspect the CPLD headers directly.
    4. Verify CPLD byte counts follow RCB=128 split rules.
    5. Verify reassembled data matches the source byte-for-byte.

    This covers MRd TLP generation, split completion reassembly
    (2 MRds × 2 CPLDs each = 4 CPLDs), and crossbar write.
    """
    sram = bytearray(4096)
    dram = bytearray(16384)
    xbar = CrossbarModel(sram=sram, dram=dram)
    dma = DmaEngine(crossbar=xbar)

    # Seed 512B of host memory
    host_pattern = bytes((i * 3) & 0xFF for i in range(512))
    pcie_src = 0x4000
    dma.host_mem[pcie_src : pcie_src + 512] = host_pattern

    # ── Path A: read via submit_read_desc (crossbar→DRAM) ────────────
    axi_dst = 0x8000_2000
    tag = 20

    dma.submit_read_desc(
        pcie_addr=pcie_src,
        axi_addr=axi_dst,
        length=512,
        tag=tag,
    )

    statuses = dma.desc_status
    assert len(statuses) == 1
    completed_tag, err = statuses[0]
    assert completed_tag == tag
    assert err == DmaEngine.DESC_ERR_NONE
    assert dma.irq

    dram_offset = axi_dst - 0x8000_0000
    dram_data = bytes(dram[dram_offset : dram_offset + 512])
    assert dram_data == host_pattern, (
        f"MRd+CplD reassembly mismatch: first 16 bytes "
        f"{dram_data[:16].hex()} != {host_pattern[:16].hex()}"
    )

    # ── Path B: verify CPLD header structure via tlp_read_with_reassembly ─
    host_pattern2 = bytes((i * 5 + 7) & 0xFF for i in range(512))
    pcie_src2 = 0x5000
    dma.host_mem[pcie_src2 : pcie_src2 + 512] = host_pattern2

    data, rd_headers, cpld_headers = dma.tlp_read_with_reassembly(
        pcie_addr=pcie_src2, length=512
    )

    # MPS=256 → 2 MRd TLPs
    assert len(rd_headers) == 2, f"Expected 2 MRd headers, got {len(rd_headers)}"
    # RCB=128 → 2 CPLDs per MRd = 4 CPLDs total
    assert len(cpld_headers) == 4, f"Expected 4 CPLD headers, got {len(cpld_headers)}"

    # Data integrity
    assert data == host_pattern2, "CPLD reassembly data mismatch"

    # Verify CPLD header byte count progression
    _, _, bc0, st0 = DmaEngine._parse_cpld_header(cpld_headers[0])
    _, _, bc1, st1 = DmaEngine._parse_cpld_header(cpld_headers[1])
    _, _, bc2, st2 = DmaEngine._parse_cpld_header(cpld_headers[2])
    _, _, bc3, st3 = DmaEngine._parse_cpld_header(cpld_headers[3])

    # All statuses must be SC(0)
    assert st0 == st1 == st2 == st3 == DmaEngine.CPL_STATUS_SC

    # First CPLD of each MRd shows total byte count remaining = 256
    assert bc0 == 256, f"CPLD 0 byte_count expected 256, got {bc0}"
    assert bc2 == 256, f"CPLD 2 byte_count expected 256, got {bc2}"
    # Second CPLD of each MRd shows remaining = 128
    assert bc1 == 128, f"CPLD 1 byte_count expected 128, got {bc1}"
    assert bc3 == 128, f"CPLD 3 byte_count expected 128, got {bc3}"


# ═══════════════════════════════════════════════════════════════════════════
# Signoff IT-3: Descriptor IRQ chain (3 descriptors → 3/3 IRQ fire)
# ═══════════════════════════════════════════════════════════════════════════


def test_pcie_dma_descriptor_irq_chain():
    """Submit 3 descriptors (write, read, write); verify each completes and
    fires IRQ, and the IRQ read-clear edge-triggered semantics hold.

    The IRQ property of DmaEngine is edge-triggered — it returns True once
    and then self-clears.  This test submits three descriptors sequentially,
    draining desc_status and checking IRQ after each, confirming all three
    fire independently.
    """
    sram = bytearray(4096)
    dram = bytearray(16384)
    xbar = CrossbarModel(sram=sram, dram=dram)
    dma = DmaEngine(crossbar=xbar)

    # Seed host memory for reads
    host_data = b"DESCRIPTOR_CHAIN_TEST_DATA" * 2  # 50 bytes
    dma.host_mem[0x1000 : 0x1000 + len(host_data)] = host_data

    # Seed NPU source data for write (in DRAM for crossbar routing)
    npu_write_src = b"NPU_TO_HOST_CHAIN" * 3  # 51 bytes
    npu_src_dram_addr = 0x8000_0100
    dram_off_src = npu_src_dram_addr - 0x8000_0000
    dram[dram_off_src : dram_off_src + len(npu_write_src)] = npu_write_src

    irq_fire_count = 0

    # ── Descriptor 1: Write NPU→Host ──────────────────────────────────
    dma.submit_write_desc(
        pcie_addr=0x9000,
        axi_addr=npu_src_dram_addr,
        length=len(npu_write_src),
        tag=1,
    )
    statuses = dma.desc_status
    assert len(statuses) == 1, f"D1: expected 1 status, got {len(statuses)}"
    assert statuses[0] == (1, DmaEngine.DESC_ERR_NONE), f"D1: bad status {statuses[0]}"
    if dma.irq:
        irq_fire_count += 1
    # IRQ cleared after read
    assert not dma.irq, "D1: IRQ should clear after read"

    # Verify write landed
    written = bytes(dma.host_mem[0x9000 : 0x9000 + len(npu_write_src)])
    assert written == npu_write_src, "D1: write data mismatch"

    # ── Descriptor 2: Read Host→NPU (via crossbar→DRAM) ───────────────
    dma.submit_read_desc(
        pcie_addr=0x1000,
        axi_addr=0x8000_3000,
        length=len(host_data),
        tag=2,
    )
    statuses = dma.desc_status
    assert len(statuses) == 1, f"D2: expected 1 status, got {len(statuses)}"
    assert statuses[0] == (2, DmaEngine.DESC_ERR_NONE), f"D2: bad status {statuses[0]}"
    if dma.irq:
        irq_fire_count += 1
    assert not dma.irq, "D2: IRQ should clear after read"

    # Verify read landed in DRAM
    dram_off = 0x8000_3000 - 0x8000_0000
    read_result = bytes(dram[dram_off : dram_off + len(host_data)])
    assert read_result == host_data, "D2: read data mismatch"

    # ── Descriptor 3: Write NPU→Host (different target) ───────────────
    npu_src2 = b"THIRD_DESCRIPTOR_TEST" * 2  # 42 bytes
    npu_src3_dram_addr = 0x8000_0200
    dram_off_src3 = npu_src3_dram_addr - 0x8000_0000
    dram[dram_off_src3 : dram_off_src3 + len(npu_src2)] = npu_src2
    dma.submit_write_desc(
        pcie_addr=0xA000,
        axi_addr=npu_src3_dram_addr,
        length=len(npu_src2),
        tag=3,
    )
    statuses = dma.desc_status
    assert len(statuses) == 1, f"D3: expected 1 status, got {len(statuses)}"
    assert statuses[0] == (3, DmaEngine.DESC_ERR_NONE), f"D3: bad status {statuses[0]}"
    if dma.irq:
        irq_fire_count += 1
    assert not dma.irq, "D3: IRQ should clear after read"

    written3 = bytes(dma.host_mem[0xA000 : 0xA000 + len(npu_src2)])
    assert written3 == npu_src2, "D3: write data mismatch"

    # ── Assertions ────────────────────────────────────────────────────
    assert irq_fire_count == 3, (
        f"Expected IRQ to fire 3 times (once per descriptor), "
        f"fired {irq_fire_count}"
    )
    assert not dma.irq, "No pending IRQ after all descriptors consumed"
    assert dma.desc_status == [], "desc_status queue must be empty"


# ═══════════════════════════════════════════════════════════════════════════
# Signoff IT-4: Tag pool lifecycle (256→0→256 cycle, no leak)
# ═══════════════════════════════════════════════════════════════════════════


def test_dma_tag_pool_no_leak():
    """Allocate→use→complete→reuse cycle; assert available tags return to 256.

    The DmaEngine manages a pool of PCIE_TAG_COUNT=256 tags.  Each TLP
    operation (tlp_write, tlp_read, descriptor submit) should allocate a
    tag for the transaction, use it, and free it on completion — even
    on error paths.

    This test exercises the tag lifecycle across multiple operations:
    - Initial state: 256 free, 0 in use
    - After single tlp_write: back to 256 free (tag freed on MWr)
    - After single tlp_read: back to 256 free (tag freed on CplD)
    - After large transfer (16 TLPs): back to 256 free
    - After error injection (UR): tag freed, back to 256
    - After mixed descriptor dispatch: back to 256 free

    No tag must leak regardless of normal or error paths.
    """
    # ── Initial state ──────────────────────────────────────────────────
    dma = DmaEngine()
    assert dma.tags_free == 256, f"Initial: {dma.tags_free} free, expected 256"
    assert dma.tags_in_use == 0, f"Initial: {dma.tags_in_use} in use, expected 0"

    # ── Single MWr ─────────────────────────────────────────────────────
    dma.tlp_write(pcie_addr=0x1000, data=b"MWR_TEST" * 32)  # 256 bytes
    assert dma.tags_free == 256, f"After MWr: {dma.tags_free} free"
    assert dma.tags_in_use == 0

    # ── Single MRd ─────────────────────────────────────────────────────
    dma.host_mem[0x2000:0x2100] = b"\xAB" * 256
    dma.tlp_read(pcie_addr=0x2000, length=256)
    assert dma.tags_free == 256, f"After MRd: {dma.tags_free} free"
    assert dma.tags_in_use == 0

    # ── Large transfer (16 TLPs) ───────────────────────────────────────
    payload = bytes(i & 0xFF for i in range(4096))
    dma.tlp_write(pcie_addr=0x0000, data=payload)
    assert dma.tags_free == 256, f"After 16x MWr: {dma.tags_free} free"
    assert dma.tags_in_use == 0

    dma.host_mem[0x0000:0x1000] = payload
    dma.tlp_read(pcie_addr=0x0000, length=4096)
    assert dma.tags_free == 256, f"After 16x MRd: {dma.tags_free} free"

    # ── Error path: UR completion ──────────────────────────────────────
    dma.inject_completion_error(tag=7, status=DmaEngine.CPL_STATUS_UR)
    dma.submit_read_desc(pcie_addr=0x5000, axi_addr=0x8000_4000, length=64, tag=7)
    statuses = dma.desc_status
    assert len(statuses) == 1
    assert statuses[0] == (7, DmaEngine.DESC_ERR_UR)
    assert dma.tags_free == 256, f"After UR error: {dma.tags_free} free"

    # ── Stress: rapid allocate/free cycles ─────────────────────────────
    for cycle in range(10):
        tag_start = dma.tags_free
        # Write + read = 2 tags each (freed inline)
        dma.tlp_write(pcie_addr=cycle * 256, data=b"\x00" * 256)
        dma.tlp_read(pcie_addr=cycle * 256, length=256)
        assert dma.tags_free == 256, (
            f"Stress cycle {cycle}: {dma.tags_free} free (started at {tag_start})"
        )

    # ── Descriptor path mix ────────────────────────────────────────────
    sram = bytearray(4096)
    dram = bytearray(16384)
    xbar = CrossbarModel(sram=sram, dram=dram)
    dma2 = DmaEngine(crossbar=xbar)
    assert dma2.tags_free == 256

    dma2.host_mem[0x0000:0x0100] = b"\xCC" * 256
    dma2.submit_read_desc(pcie_addr=0x0000, axi_addr=0x8000_0000, length=256, tag=1)
    _ = dma2.desc_status
    _ = dma2.irq
    assert dma2.tags_free == 256, f"After read desc: {dma2.tags_free} free"

    dma2.submit_write_desc(pcie_addr=0x1000, axi_addr=0x0000, length=128, tag=2)
    _ = dma2.desc_status
    _ = dma2.irq
    assert dma2.tags_free == 256, f"After write desc: {dma2.tags_free} free"

    # ── Final assertion: pool must be full ─────────────────────────────
    assert dma2.tags_free == 256, f"Final: {dma2.tags_free} free, expected 256"
    assert dma2.tags_in_use == 0, f"Final: {dma2.tags_in_use} in use, expected 0"


# ═══════════════════════════════════════════════════════════════════════════
# SIGNOFF_METRIC: tests.collected=12  tests.passed  (set by pytest junit)
# ═══════════════════════════════════════════════════════════════════════════
