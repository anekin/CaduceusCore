"""Unit tests for sim/spike_mmio_server.py _handle_request routing.

Covers: MMIO->bridge, SRAM/DRAM->crossbar, crossbar=None ERR."""

from unittest.mock import MagicMock

from models.crossbar import CrossbarModel
from spike_mmio_server import _handle_request


# ── Helpers ──────────────────────────────────────────────────────────

def _mock_bridge():
    bridge = MagicMock()
    bridge.handle.return_value = 0
    return bridge


def _mock_crossbar():
    xbar = MagicMock()
    xbar.read.return_value = b"\x00\x00\x00\x00"
    return xbar


# ── MMIO routing (0x4000_0000 – 0x7FFF_FFFF) ────────────────────────

def test_mmio_read_routes_to_bridge_handle():
    """Given MMIO address 0x40000010, read should call bridge.handle('read', addr, 0)."""
    bridge = _mock_bridge()
    bridge.handle.return_value = 0xDEAD_BEEF
    crossbar = _mock_crossbar()

    result = _handle_request(bridge, crossbar, "R 0x40000010\n")

    bridge.handle.assert_called_once_with("read", 0x40000010, 0)
    crossbar.read.assert_not_called()
    assert result == "0xDEADBEEF\n"


def test_mmio_write_routes_to_bridge_handle():
    """Given MMIO address 0x40000004, write should call bridge.handle('write', addr, val)."""
    bridge = _mock_bridge()
    crossbar = _mock_crossbar()

    result = _handle_request(bridge, crossbar, "W 0x40000004 0x12345678\n")

    bridge.handle.assert_called_once_with("write", 0x40000004, 0x12345678)
    crossbar.write.assert_not_called()
    assert result == "OK\n"


def test_mmio_read_without_crossbar_still_works():
    """Given crossbar=None, MMIO reads still route through bridge."""
    bridge = _mock_bridge()
    bridge.handle.return_value = 0xCAFE

    result = _handle_request(bridge, None, "R 0x40003000\n")

    bridge.handle.assert_called_once_with("read", 0x40003000, 0)
    assert result == "0x0000CAFE\n"


# ── SRAM routing (0x2000_0000 – 0x203F_FFFF) ────────────────────────

def test_sram_read_routes_to_crossbar():
    """Given SRAM address 0x20000100, read should call crossbar.read(MASTER_IBEX, addr, 4)."""
    bridge = _mock_bridge()
    crossbar = _mock_crossbar()
    # Return 4 bytes representing 0x12345678 in little-endian
    crossbar.read.return_value = b"\x78\x56\x34\x12"

    result = _handle_request(bridge, crossbar, "R 0x20000100\n")

    crossbar.read.assert_called_once_with(CrossbarModel.MASTER_IBEX, 0x20000100, 4)
    bridge.handle.assert_not_called()
    assert result == "0x12345678\n"


def test_sram_write_routes_to_crossbar():
    """Given SRAM address 0x2000FF00, write should call crossbar.write with little-endian bytes."""
    bridge = _mock_bridge()
    crossbar = _mock_crossbar()

    result = _handle_request(bridge, crossbar, "W 0x2000FF00 0xAABBCCDD\n")

    crossbar.write.assert_called_once_with(
        CrossbarModel.MASTER_IBEX,
        0x2000FF00,
        b"\xDD\xCC\xBB\xAA",
    )
    bridge.handle.assert_not_called()
    assert result == "OK\n"


def test_sram_read_pads_short_result():
    """Given crossbar returns only 2 bytes, read should pad with zeros on the right."""
    bridge = _mock_bridge()
    crossbar = _mock_crossbar()
    crossbar.read.return_value = b"\xEF\xBE"  # only 2 bytes

    result = _handle_request(bridge, crossbar, "R 0x20000000\n")

    # Should be interpreted as 0x0000BEEF (padded right with zero bytes)
    assert result == "0x0000BEEF\n"


# ── DRAM routing (0x8000_0000+) ─────────────────────────────────────

def test_dram_read_routes_to_crossbar():
    """Given DRAM address 0x81000000, read should call crossbar.read(MASTER_IBEX, addr, 4)."""
    bridge = _mock_bridge()
    crossbar = _mock_crossbar()
    crossbar.read.return_value = b"\x01\x00\x00\x00"

    result = _handle_request(bridge, crossbar, "R 0x81000000\n")

    crossbar.read.assert_called_once_with(CrossbarModel.MASTER_IBEX, 0x81000000, 4)
    bridge.handle.assert_not_called()
    assert result == "0x00000001\n"


def test_dram_write_routes_to_crossbar():
    """Given DRAM address 0x80001000, write should call crossbar.write with little-endian bytes."""
    bridge = _mock_bridge()
    crossbar = _mock_crossbar()

    result = _handle_request(bridge, crossbar, "W 0x80001000 0xDEADBEEF\n")

    crossbar.write.assert_called_once_with(
        CrossbarModel.MASTER_IBEX,
        0x80001000,
        b"\xEF\xBE\xAD\xDE",
    )
    assert result == "OK\n"


# ── crossbar=None + non-MMIO → ERR ──────────────────────────────────

def test_crossbar_none_sram_read_returns_err():
    """Given crossbar=None and SRAM address, read returns ERR."""
    bridge = _mock_bridge()

    result = _handle_request(bridge, None, "R 0x20000000\n")

    assert result.startswith("ERR crossbar required"), result


def test_crossbar_none_dram_read_returns_err():
    """Given crossbar=None and DRAM address, read returns ERR."""
    bridge = _mock_bridge()

    result = _handle_request(bridge, None, "R 0x80000000\n")

    assert result.startswith("ERR crossbar required"), result


def test_crossbar_none_sram_write_returns_err():
    """Given crossbar=None and SRAM address, write returns ERR."""
    bridge = _mock_bridge()

    result = _handle_request(bridge, None, "W 0x20000100 0x42\n")

    assert result.startswith("ERR crossbar required"), result


def test_crossbar_none_dram_write_returns_err():
    """Given crossbar=None and DRAM address, write returns ERR."""
    bridge = _mock_bridge()

    result = _handle_request(bridge, None, "W 0x80001000 0x99\n")

    assert result.startswith("ERR crossbar required"), result


# ── Edge cases ──────────────────────────────────────────────────────

def test_invalid_request_line():
    """Given an unparseable line, return ERR invalid request."""
    bridge = _mock_bridge()
    crossbar = _mock_crossbar()

    result = _handle_request(bridge, crossbar, "GARBAGE\n")

    assert result == "ERR invalid request\n"


def test_mmio_write_missing_value():
    """Given MMIO write without value, return ERR."""
    bridge = _mock_bridge()
    crossbar = _mock_crossbar()

    result = _handle_request(bridge, crossbar, "W 0x40000000\n")

    assert result == "ERR write missing value\n"


def test_crossbar_write_missing_value():
    """Given non-MMIO write without value, return ERR."""
    bridge = _mock_bridge()
    crossbar = _mock_crossbar()

    result = _handle_request(bridge, crossbar, "W 0x20000000\n")

    assert result == "ERR write missing value\n"


def test_zero_value_read_through_crossbar():
    """Given crossbar returns all-zero bytes, read returns 0x00000000."""
    bridge = _mock_bridge()
    crossbar = _mock_crossbar()
    crossbar.read.return_value = b"\x00\x00\x00\x00"

    result = _handle_request(bridge, crossbar, "R 0x20000000\n")

    assert result == "0x00000000\n"


def test_max_value_read_through_crossbar():
    """Given crossbar returns 0xFFFFFFFF bytes, read returns 0xFFFFFFFF."""
    bridge = _mock_bridge()
    crossbar = _mock_crossbar()
    crossbar.read.return_value = b"\xFF\xFF\xFF\xFF"

    result = _handle_request(bridge, crossbar, "R 0x80000000\n")

    assert result == "0xFFFFFFFF\n"


def test_sram_end_boundary_read_routes_to_crossbar():
    """Given SRAM end address 0x203FFFFF, read routes to crossbar not bridge."""
    bridge = _mock_bridge()
    crossbar = _mock_crossbar()
    crossbar.read.return_value = b"\x00\x00\x00\x00"

    result = _handle_request(bridge, crossbar, "R 0x203FFFFF\n")

    crossbar.read.assert_called_once_with(CrossbarModel.MASTER_IBEX, 0x203FFFFF, 4)
    bridge.handle.assert_not_called()
    assert result == "0x00000000\n"
