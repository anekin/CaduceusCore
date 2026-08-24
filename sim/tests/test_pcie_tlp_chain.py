"""PCIe TLP complete-chain Func Model verification guard (SOC-13).

Verifies the full host→NPU data path through the PCIe endpoint model:

    PCIeModel.tlp_write → BAR resolution → CrossbarModel routing
    → SRAM/DRAM write → tlp_read readback (bit-exact)

Covers the docs/soc-fm-gap-spec.md Gap #7 testability plan:
  - TLP write roundtrip      → test_tlp_write_read_4kb_roundtrip_bit_exact
  - Multi-TLP split (MPS)    → test_tlp_4kb_split_by_mps_256
  - BAR routing / isolation  → test_bar_routing_* / test_bar_isolation_*
  - Anti-vacuous (corruption)→ test_failure_injection_tampered_payload_detected

Reuses sim/models/pcie.py unchanged (PCIeModel.tlp_write, tlp_read,
_resolve_bar, send_msi). MPS = 256 bytes per PCIeModel.max_payload_bytes.
"""

import struct

import pytest

from func_model import FuncModel
from regmap import Addr
from models.crossbar import CrossbarModel

_MPS = 256  # PCIeModel.max_payload_bytes (bytes per TLP)


def _pattern(n: int, seed: int = 0) -> bytes:
    """Deterministic pseudo-random byte pattern (no numpy dependency)."""
    return bytes(((i * 131) ^ (i >> 3) ^ (seed * 17)) & 0xFF for i in range(n))


def _tx_header_fields(header: bytes):
    """Decode a 3-DW TLP header → (fmt_type, length_dw, address)."""
    dw0, _dw1, dw2 = struct.unpack(">III", header)
    return (dw0 >> 24) & 0xFF, dw0 & 0x3FF, dw2


# ══════════════════════════════════════════════════════════════════
# Happy path — complete chain, bit-exact roundtrip
# ══════════════════════════════════════════════════════════════════


def test_tlp_write_read_4kb_roundtrip_bit_exact():
    """4KB host payload: tlp_write → BAR1 → crossbar → DRAM → tlp_read bit-exact."""
    model = FuncModel()
    addr = 0x8000_4000
    payload = _pattern(4096, seed=1)
    assert len(payload) == 4096

    model.pcie.tlp_write(addr, payload)

    # The payload must have landed in DRAM through the crossbar (MASTER_PCIE).
    off = addr - Addr.DRAM_BASE
    assert bytes(model.dram[off:off + 4096]) == payload

    readback = model.pcie.tlp_read(addr, 4096)
    assert readback == payload, "4KB TLP roundtrip is not bit-exact"


def test_tlp_4kb_split_by_mps_256():
    """4KB payload is split into 16 MWr TLPs at MPS=256B with contiguous addresses."""
    model = FuncModel()
    pcie = model.pcie
    xbar = model.crossbar
    addr = 0x8000_5000
    payload = _pattern(4096, seed=2)

    pcie.tlp_write(addr, payload)

    assert pcie.max_payload_bytes == _MPS
    headers = pcie.last_tx_headers
    assert len(headers) == 4096 // _MPS, (
        f"4096 bytes at MPS=256 → 16 MWr TLPs, got {len(headers)}"
    )
    for i, h in enumerate(headers):
        assert len(h) == 12, f"TLP {i}: expected 3-DW header, got {len(h)} bytes"
        fmt_type, length_dw, hdr_addr = _tx_header_fields(h)
        assert fmt_type == 0x40, f"TLP {i}: MWr Fmt+Type=0x40, got 0x{fmt_type:02X}"
        assert length_dw == _MPS // 4, f"TLP {i}: expected 64 DWs, got {length_dw}"
        assert hdr_addr == addr + i * _MPS, f"TLP {i}: address discontinuity"

    # Crossbar evidence: every chunk went through MASTER_PCIE to slave 1 (DRAM).
    pcie_grants = [g for g in xbar._aw_grants if g[1] == CrossbarModel.MASTER_PCIE]
    assert len(pcie_grants) == 16, f"Expected 16 AW grants for PCIE, got {len(pcie_grants)}"
    assert all(g[0] == 1 for g in pcie_grants), "PCIE writes must target slave 1 (DRAM)"
    assert xbar._txn_ids[CrossbarModel.MASTER_PCIE] == 16

    # Read side is MPS-split too, and reassembly is bit-exact.
    readback = pcie.tlp_read(addr, 4096)
    assert readback == payload
    assert len(pcie.last_rx_headers) == 16, (
        f"4096-byte read → 16 MRd TLPs, got {len(pcie.last_rx_headers)}"
    )


def test_tlp_unaligned_payload_padding():
    """Non-DW-multiple payload: readback stays bit-exact; tail is zero-padded.

    tlp_write pads the final chunk to a 4-byte (DW) boundary before the
    crossbar write, so the padded tail bytes land in memory as zeros.
    The original payload length must still read back bit-exact.
    """
    model = FuncModel()
    addr = 0x8000_6000
    payload = _pattern(1001, seed=3)  # 1001 % 4 != 0 → final chunk padded
    pad_len = (4 - len(payload) % 4) % 4
    assert pad_len > 0

    model.pcie.tlp_write(addr, payload)

    readback = model.pcie.tlp_read(addr, len(payload))
    assert readback == payload, "Unaligned payload readback is not bit-exact"

    # Lock in the padding behavior: DW-padded zeros are visible in memory
    # and in an extended readback.
    off = addr - Addr.DRAM_BASE
    assert bytes(model.dram[off + len(payload):off + len(payload) + pad_len]) == (
        b"\x00" * pad_len
    )
    padded_readback = model.pcie.tlp_read(addr, len(payload) + pad_len)
    assert padded_readback == payload + b"\x00" * pad_len


# ══════════════════════════════════════════════════════════════════
# BAR routing + isolation
# ══════════════════════════════════════════════════════════════════


def test_bar_routing_sram_lands_in_sram():
    """BAR0: TLP write to a SRAM address lands in SRAM via crossbar slave 0."""
    model = FuncModel()
    xbar = model.crossbar
    addr = 0x2004_0000
    payload = _pattern(512, seed=4)

    model.pcie.tlp_write(addr, payload)

    off = addr - Addr.SRAM_BASE
    assert bytes(model.sram[off:off + len(payload)]) == payload
    assert model.pcie.tlp_read(addr, len(payload)) == payload

    sram_grants = [g for g in xbar._aw_grants if g[0] == 0]
    assert sram_grants, "No AW grant to slave 0 (SRAM)"
    assert all(g[1] == CrossbarModel.MASTER_PCIE for g in sram_grants)


def test_bar_routing_dram_lands_in_dram():
    """BAR1: TLP write to a DRAM address lands in DRAM via crossbar slave 1."""
    model = FuncModel()
    xbar = model.crossbar
    addr = 0x8000_8000
    payload = _pattern(512, seed=5)

    model.pcie.tlp_write(addr, payload)

    off = addr - Addr.DRAM_BASE
    assert bytes(model.dram[off:off + len(payload)]) == payload
    assert model.pcie.tlp_read(addr, len(payload)) == payload

    dram_grants = [g for g in xbar._aw_grants if g[0] == 1]
    assert dram_grants, "No AW grant to slave 1 (DRAM)"
    assert all(g[1] == CrossbarModel.MASTER_PCIE for g in dram_grants)


def test_bar_isolation_sram_write_does_not_affect_dram():
    """BAR isolation: SRAM write must not leak into DRAM at the same offset."""
    model = FuncModel()
    off = 0x41000
    sram_addr = Addr.SRAM_BASE + off
    dram_addr = Addr.DRAM_BASE + off
    sram_payload = _pattern(256, seed=6)
    dram_payload = _pattern(256, seed=7)
    assert sram_payload != dram_payload

    # Seed DRAM with a distinct pattern before the SRAM write.
    model.dram[off:off + 256] = dram_payload
    model.pcie.tlp_write(sram_addr, sram_payload)

    assert bytes(model.sram[off:off + 256]) == sram_payload
    assert bytes(model.dram[off:off + 256]) == dram_payload, (
        "SRAM write leaked into DRAM"
    )
    assert model.pcie.tlp_read(sram_addr, 256) == sram_payload
    assert model.pcie.tlp_read(dram_addr, 256) == dram_payload


def test_bar_isolation_dram_write_does_not_affect_sram():
    """BAR isolation: DRAM write must not leak into SRAM at the same offset."""
    model = FuncModel()
    off = 0x42000
    sram_addr = Addr.SRAM_BASE + off
    dram_addr = Addr.DRAM_BASE + off
    sram_payload = _pattern(256, seed=8)
    dram_payload = _pattern(256, seed=9)
    assert sram_payload != dram_payload

    # Seed SRAM with a distinct pattern before the DRAM write.
    model.sram[off:off + 256] = sram_payload
    model.pcie.tlp_write(dram_addr, dram_payload)

    assert bytes(model.dram[off:off + 256]) == dram_payload
    assert bytes(model.sram[off:off + 256]) == sram_payload, (
        "DRAM write leaked into SRAM"
    )
    assert model.pcie.tlp_read(sram_addr, 256) == sram_payload
    assert model.pcie.tlp_read(dram_addr, 256) == dram_payload


# ══════════════════════════════════════════════════════════════════
# Failure injection + guard rails
# ══════════════════════════════════════════════════════════════════


def test_failure_injection_tampered_payload_detected():
    """Failure injection: tamper 1 byte of the landed payload → readback mismatch.

    Simulates a corrupted TLP payload having landed in memory. The guard
    must surface the mismatch: readback differs from the original payload
    exactly at the tampered byte.
    """
    model = FuncModel()
    addr = 0x8000_7000
    payload = _pattern(256, seed=10)
    model.pcie.tlp_write(addr, payload)

    baseline = model.pcie.tlp_read(addr, len(payload))
    assert baseline == payload, "Baseline readback must be bit-exact"

    # Tamper one byte in DRAM, as if a corrupted TLP payload landed.
    off = addr - Addr.DRAM_BASE
    tamper_idx = 42
    model.dram[off + tamper_idx] ^= 0xFF

    readback = model.pcie.tlp_read(addr, len(payload))
    assert readback != payload, (
        "Tampered payload was not detected — readback still matches original"
    )
    assert readback[tamper_idx] == payload[tamper_idx] ^ 0xFF, (
        "Readback does not reflect the tampered byte"
    )
    assert readback[:tamper_idx] == payload[:tamper_idx]
    assert readback[tamper_idx + 1:] == payload[tamper_idx + 1:]


def test_out_of_bar_address_rejected():
    """Addresses outside both BARs raise ValueError (no silent misroute)."""
    model = FuncModel()
    pcie = model.pcie

    # Address hole between BAR0 (SRAM) and BAR1 (DRAM).
    with pytest.raises(ValueError):
        pcie.tlp_write(0x5000_0000, b"decerr")
    with pytest.raises(ValueError):
        pcie.tlp_read(0x5000_0000, 4)

    # Beyond SRAM end (default FuncModel SRAM = 512 KB).
    with pytest.raises(ValueError):
        pcie.tlp_write(Addr.SRAM_BASE + len(model.sram), b"past sram")

    # Beyond DRAM end (default FuncModel DRAM = 64 MB).
    with pytest.raises(ValueError):
        pcie.tlp_write(Addr.DRAM_BASE + len(model.dram), b"past dram")


def test_msi_send_sets_state():
    """MSI-X send sets host-visible state; out-of-range vector rejected."""
    model = FuncModel()
    pcie = model.pcie

    assert not pcie.state.msix_enable
    pcie.send_msi(3)
    assert pcie.state.msix_enable
    assert pcie.state.msix_vector == 3
    assert pcie.state.irq_pending

    with pytest.raises(ValueError):
        pcie.send_msi(8)
    with pytest.raises(ValueError):
        pcie.send_msi(-1)
