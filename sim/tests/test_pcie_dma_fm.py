"""Func Model golden-reference tests for DmaEngine (PCIe DMA).

These tests validate the DmaEngine Func Model against its documented behavior —
MWr/MRd TLP generation, split-completion reassembly, error injection, and
concurrent descriptor dispatch.  They serve as the golden reference for RTL
verification of the NPU→host DMA engine (dma_if_pcie).

Every assertion verifies bit-level data integrity so that a mismatch is
immediately obvious.
"""

import struct

import pytest

from models.crossbar import CrossbarModel
from models.pcie import DmaEngine


# ═══════════════════════════════════════════════════════════════════════════
# TC1: Single MWr NPU→host, 256 bytes
# ═══════════════════════════════════════════════════════════════════════════


def test_tc1_single_mwr_256():
    """Single MWr TLP from NPU to host — 256-byte payload, verify host memory.

    Uses an incrementing byte pattern so that a single corrupted byte is
    immediately obvious.
    """
    dma = DmaEngine()
    pattern = bytes(range(256))

    headers = dma.tlp_write(pcie_addr=0x1000, data=pattern)

    # Exactly one TLP header (256 bytes fits within MPS=256)
    assert len(headers) == 1, f"Expected 1 MWr header, got {len(headers)}"
    hdr = headers[0]
    assert len(hdr) == 12, "3-DW MWr header expected (32-bit addr)"
    dw0, dw1, dw2 = struct.unpack(">III", hdr)
    assert (dw0 >> 24) & 0xFF == DmaEngine.TLP_MWR_3DW, \
        f"Fmt+Type mismatch: 0x{(dw0>>24)&0xFF:02X}"
    assert (dw0 & 0x3FF) == 64, f"Length mismatch: {dw0 & 0x3FF} DWs"
    assert dw2 == 0x1000, f"Address mismatch: 0x{dw2:08X}"

    # Data integrity in host memory
    assert dma.host_mem[0x1000:0x1000 + 256] == pattern, \
        "Host memory data mismatch after MWr"

    # Anti-vacuous: a different pattern at same address should fail
    corrupted = bytearray(pattern)
    corrupted[128] ^= 0xFF
    assert dma.host_mem[0x1000:0x1000 + 256] != corrupted, \
        "Vacuous — corrupted data matched expected"


# ═══════════════════════════════════════════════════════════════════════════
# TC2: Single MRd + split completion (512 bytes, RCB=128)
# ═══════════════════════════════════════════════════════════════════════════


def test_tc2_mrd_split_completion():
    """MRd NPU←host, 512 bytes with split CPLD reassembly (RCB=128).

    MPS=256 means 2 MRd TLPs.  Each MRd returns 128-byte split completions
    (2 CPLDs per MRd) for a total of 4 CPLDs.  Verifies that the Func Model
    correctly reassembles all completion fragments.
    """
    dma = DmaEngine()
    # Seed host memory with an incrementing pattern at 0x2000
    pattern = bytes(i & 0xFF for i in range(512))
    dma.host_mem[0x2000:0x2000 + 512] = pattern

    data, rd_headers, cpld_headers = dma.tlp_read_with_reassembly(
        pcie_addr=0x2000, length=512
    )

    # MRd headers: 512 bytes at MPS=256 → 2 MRd TLPs
    assert len(rd_headers) == 2, \
        f"Expected 2 MRd headers (MPS=256), got {len(rd_headers)}"

    # CPLD headers: 2 MRds × (256/128) = 4 CPLDs
    assert len(cpld_headers) == 4, \
        f"Expected 4 CPLD headers (RCB=128 split), got {len(cpld_headers)}"

    # Data reassembly must match original
    assert data == pattern, \
        f"Split CPLD reassembly mismatch: got {len(data)} bytes"

    # Verify CPLD header byte counts: first CPLD of each MRd shows total=256
    # (the byte_count_remaining for that MRd), second shows 128
    for i, hdr in enumerate(cpld_headers):
        _, tag, byte_count, status = DmaEngine._parse_cpld_header(hdr)
        assert status == DmaEngine.CPL_STATUS_SC, \
            f"CPLD {i}: expected SC(0), got {status}"
        assert len(hdr) == 12, f"CPLD {i}: header should be 12 bytes"

    # First CPLD of each MRd: byte_count_remaining = 256
    _, _, bc0, _ = DmaEngine._parse_cpld_header(cpld_headers[0])
    _, _, bc1, _ = DmaEngine._parse_cpld_header(cpld_headers[2])
    assert bc0 == 256, f"CPLD 0 byte_count should be 256, got {bc0}"
    assert bc1 == 256, f"CPLD 2 byte_count should be 256, got {bc1}"

    # Second CPLD of each MRd: byte_count_remaining = 128
    _, _, bc0b, _ = DmaEngine._parse_cpld_header(cpld_headers[1])
    _, _, bc1b, _ = DmaEngine._parse_cpld_header(cpld_headers[3])
    assert bc0b == 128, f"CPLD 1 byte_count should be 128, got {bc0b}"
    assert bc1b == 128, f"CPLD 3 byte_count should be 128, got {bc1b}"

    # Anti-vacuous: MRd header is not a CPLD header
    mrd_dw0 = struct.unpack(">I", rd_headers[0][:4])[0]
    assert (mrd_dw0 >> 24) & 0xFF == DmaEngine.TLP_MRD_3DW, \
        "MRd header must use MRd Fmt+Type, not CPLD"


# ═══════════════════════════════════════════════════════════════════════════
# TC3: Unaligned transfer — odd address, odd length
# ═══════════════════════════════════════════════════════════════════════════


def test_tc3_unaligned_transfer():
    """Unaligned transfer: address not 4B-aligned, odd byte count.

    Writes 33 bytes to a non-4B-aligned host address and reads back the
    same range to confirm byte-level data integrity.
    """
    dma = DmaEngine()
    pattern = bytes((i * 3) & 0xFF for i in range(33))

    # Write to odd address 0x1001, odd length 33
    headers = dma.tlp_write(pcie_addr=0x1001, data=pattern)
    assert len(headers) == 1, "33 bytes should fit in one TLP at MPS=256"

    # Read back via tlp_read (MRd + completion)
    readback, rd_headers = dma.tlp_read(pcie_addr=0x1001, length=33)
    assert len(rd_headers) == 1
    assert readback == pattern, \
        f"Unaligned readback mismatch at offset 0x1001"

    # Verify the exact bytes at host_mem boundaries
    assert dma.host_mem[0x1001] == pattern[0], \
        f"First byte mismatch: {dma.host_mem[0x1001]} != {pattern[0]}"
    assert dma.host_mem[0x1021] == pattern[32], \
        f"Last byte mismatch: {dma.host_mem[0x1021]} != {pattern[32]}"

    # Anti-vacuous: byte at 0x1000 (before our write) should be unmodified
    assert dma.host_mem[0x1000] == 0, \
        "Byte at 0x1000 should not have been written (boundary check)"
    # Byte at 0x1022 (after our write) should be unmodified
    assert dma.host_mem[0x1022] == 0, \
        "Byte at 0x1022 should not have been written (boundary check)"


# ═══════════════════════════════════════════════════════════════════════════
# TC4: Max-length transfer — 4096 bytes at MPS=256
# ═══════════════════════════════════════════════════════════════════════════


def test_tc4_max_length_4096():
    """Max-length transfer: 4096 bytes, MPS=256 → 16 MWr TLPs.

    Full data integrity check across all 16 split TLPs.
    """
    dma = DmaEngine()
    payload = bytes(i & 0xFF for i in range(4096))

    headers = dma.tlp_write(pcie_addr=0, data=payload)

    # 4096 bytes / 256 bytes per TLP = 16 TLPs
    assert len(headers) == 16, \
        f"Expected 16 MWr headers, got {len(headers)}"

    for i, hdr in enumerate(headers):
        assert len(hdr) == 12, f"TLP {i}: expected 3-DW header"
        dw0 = struct.unpack(">I", hdr[:4])[0]
        length_dw = dw0 & 0x3FF
        assert length_dw == 64, \
            f"TLP {i}: expected 64 DWs (256 bytes), got {length_dw}"
        # Verify Fmt+Type is MWr 3-DW
        assert (dw0 >> 24) & 0xFF == DmaEngine.TLP_MWR_3DW, \
            f"TLP {i}: Fmt+Type mismatch"

    # Data integrity spanning all chunks
    assert dma.host_mem[0:4096] == payload, \
        "4096-byte payload mismatch in host_mem"

    # Verify individual chunks
    for chunk_idx in range(16):
        offset = chunk_idx * 256
        expected_chunk = payload[offset:offset + 256]
        actual_chunk = bytes(dma.host_mem[offset:offset + 256])
        assert actual_chunk == expected_chunk, \
            f"Chunk {chunk_idx} at offset {offset:#06x} mismatch"

    # Anti-vacuous: readback via MRd path should also match
    readback, rd_headers = dma.tlp_read(pcie_addr=0, length=4096)
    assert readback == payload, "MRd readback mismatch for 4096 bytes"
    assert len(rd_headers) == 16, \
        f"Expected 16 MRd headers for readback, got {len(rd_headers)}"


# ═══════════════════════════════════════════════════════════════════════════
# TC5: Concurrent read + write descriptors
# ═══════════════════════════════════════════════════════════════════════════


def test_tc5_concurrent_read_write():
    """Concurrent read + write descriptors — both complete without interference.

    Submits one read and one write descriptor with different tags.  Verifies
    that both complete, IRQ fires, and data lands at the correct targets.
    """
    dma = DmaEngine()

    # ── Setup "NPU memory" (simulated in host_mem fallback) ─────────
    npu_data = b"WRITE_FROM_NPU" * 4  # 56 bytes
    dma.host_mem[0x0000:0x0000 + len(npu_data)] = npu_data

    # ── Setup "host memory" for read source ────────────────────────
    host_source = b"HOST_READ_DATA" * 4  # 56 bytes
    dma.host_mem[0x4000:0x4000 + len(host_source)] = host_source

    write_tag = 5
    read_tag = 42

    # Submit write descriptor: NPU→host (axi_addr=0 → reads npu_data from
    # host_mem fallback, writes to pcie_addr=0x8000)
    dma.submit_write_desc(
        pcie_addr=0x8000,
        axi_addr=0x0000,
        length=len(npu_data),
        tag=write_tag,
    )

    # Submit read descriptor: host→NPU (reads host_source from pcie_addr=0x4000,
    # writes to axi_addr=0x1000 via host_mem fallback)
    dma.submit_read_desc(
        pcie_addr=0x4000,
        axi_addr=0x1000,
        length=len(host_source),
        tag=read_tag,
    )

    # Both should complete
    statuses = dma.desc_status
    assert len(statuses) == 2, \
        f"Expected 2 descriptor completions, got {len(statuses)}"

    # Collect by tag
    status_map = {tag: err for tag, err in statuses}
    assert write_tag in status_map, f"Write descriptor tag {write_tag} not completed"
    assert read_tag in status_map, f"Read descriptor tag {read_tag} not completed"
    assert status_map[write_tag] == DmaEngine.DESC_ERR_NONE, \
        f"Write descriptor failed: err={status_map[write_tag]}"
    assert status_map[read_tag] == DmaEngine.DESC_ERR_NONE, \
        f"Read descriptor failed: err={status_map[read_tag]}"

    # IRQ should have fired
    assert dma.irq, "IRQ should be asserted after descriptor completion"
    # IRQ clears on read — second read returns False
    assert not dma.irq, "IRQ should clear after first read"

    # Verify write data landed at pcie_addr
    written = bytes(dma.host_mem[0x8000:0x8000 + len(npu_data)])
    assert written == npu_data, "Write descriptor: host_mem data mismatch"

    # Verify read data landed at axi_addr (fallback offset in host_mem)
    read_result = bytes(dma.host_mem[0x1000:0x1000 + len(host_source)])
    assert read_result == host_source, "Read descriptor: destination data mismatch"

    # Anti-vacuous: write target != read source (no accidental aliasing)
    assert written != host_source, \
        "Vacuous — write and read data should differ"


# ═══════════════════════════════════════════════════════════════════════════
# TC6: PCIe completion error (UR) → DESC_ERR_UR
# ═══════════════════════════════════════════════════════════════════════════


def test_tc6_completion_error_ur():
    """PCIe completion error (UR) → descriptor status reports DESC_ERR_UR.

    Injects a UR completion status for a specific tag before submitting a
    read descriptor.  Verifies that the descriptor completes with error,
    IRQ is asserted, and no data is written.
    """
    dma = DmaEngine()
    # Seed host memory so a successful read would have returned data
    dma.host_mem[0x5000:0x5040] = b"\xAA" * 64

    error_tag = 7
    dma.inject_completion_error(error_tag, DmaEngine.CPL_STATUS_UR)

    dma.submit_read_desc(
        pcie_addr=0x5000,
        axi_addr=0x2000,
        length=64,
        tag=error_tag,
    )

    statuses = dma.desc_status
    assert len(statuses) == 1, f"Expected 1 descriptor status, got {len(statuses)}"
    tag, err = statuses[0]
    assert tag == error_tag, f"Expected tag={error_tag}, got {tag}"
    assert err == DmaEngine.DESC_ERR_UR, \
        f"Expected DESC_ERR_UR(1), got {err}"

    # IRQ must fire even on error completion
    assert dma.irq, "IRQ should be asserted on error completion"

    # Anti-vacuous: successful completion would be DESC_ERR_NONE
    assert err != DmaEngine.DESC_ERR_NONE, \
        "Error status should not be DESC_ERR_NONE"

    # Verify the destination was NOT written (no data on UR)
    # The fallback at offset 0x2000 % len(host_mem) = 0x2000 should be 0
    assert dma.host_mem[0x2000:0x2004] == b"\x00\x00\x00\x00", \
        "UR completion should not write data to destination"


# ═══════════════════════════════════════════════════════════════════════════
# TC7: AXI slave error → DESC_ERR_DECERR
# ═══════════════════════════════════════════════════════════════════════════


def test_tc7_axi_dec_error():
    """AXI slave decode error → write descriptor status reports DESC_ERR_DECERR.

    Submits a write descriptor whose axi_addr is unmapped by the crossbar.
    The crossbar read raises ValueError, which the DmaEngine translates to
    DESC_ERR_DECERR.
    """
    # Create a minimal crossbar with tiny memory windows
    sram = bytearray(4096)
    dram = bytearray(4096)
    xbar = CrossbarModel(sram=sram, dram=dram)
    dma = DmaEngine(crossbar=xbar)

    # Seed host memory for the TLP write (which still goes to host_mem)
    dma.host_mem[0x6000:0x6040] = b"\xBB" * 64

    # axi_addr 0x50000000 is between SRAM_END and DRAM_START → DECERR
    decerr_tag = 9
    dma.submit_write_desc(
        pcie_addr=0x6000,
        axi_addr=0x50000000,
        length=64,
        tag=decerr_tag,
    )

    statuses = dma.desc_status
    assert len(statuses) == 1, f"Expected 1 descriptor status, got {len(statuses)}"
    tag, err = statuses[0]
    assert tag == decerr_tag, f"Expected tag={decerr_tag}, got {tag}"
    assert err == DmaEngine.DESC_ERR_DECERR, \
        f"Expected DESC_ERR_DECERR(4), got {err}"

    # IRQ must fire even on error
    assert dma.irq, "IRQ should be asserted on AXI DECERR"

    # Anti-vacuous: verify it's actually the DECERR code, not UR or CA
    assert err == DmaEngine.DESC_ERR_DECERR
    assert err != DmaEngine.DESC_ERR_UR, \
        "Error code should be DECERR(4), not UR(1)"


# ═══════════════════════════════════════════════════════════════════════════
# Cross-cutting: anti-regression — verify all tag enums are distinct
# ═══════════════════════════════════════════════════════════════════════════


def test_tag_enums_distinct():
    """Sanity: all descriptor error codes, CPL statuses, and Fmt+Type are distinct."""
    errors = {
        DmaEngine.DESC_ERR_NONE,
        DmaEngine.DESC_ERR_UR,
        DmaEngine.DESC_ERR_CA,
        DmaEngine.DESC_ERR_DECERR,
        DmaEngine.DESC_ERR_TIMEOUT,
    }
    assert len(errors) == 5, f"Error codes not distinct: {errors}"

    cpl_statuses = {
        DmaEngine.CPL_STATUS_SC,
        DmaEngine.CPL_STATUS_UR,
        DmaEngine.CPL_STATUS_CA,
    }
    assert len(cpl_statuses) == 3

    fmt_types = {
        DmaEngine.TLP_MWR_3DW,
        DmaEngine.TLP_MWR_4DW,
        DmaEngine.TLP_MRD_3DW,
        DmaEngine.TLP_MRD_4DW,
        DmaEngine.TLP_CPLD,
    }
    assert len(fmt_types) == 5
