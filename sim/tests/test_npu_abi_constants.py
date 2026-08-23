"""Cross-language ABI constant contract (fm-hardening-phase10, todo 9).

Reads the authoritative ABI schema (spec/npu_abi.json) and asserts that the
Python single-source modules (sim/address_space.py, sim/command_ring.py) agree
with the schema values. The test also documents the intentional divergence
between the firmware tile-scale grouping (per 64 columns, 256B) and the Python
tile_scheduler grouping (per 128 columns, 512B).

This test does NOT parse C constants with regex; the C source of truth is the
JSON schema, and the generated header (gen/npu_abi_firmware.h) is verified by
scripts/gen_npu_abi.py --check and by the firmware build.
"""

import json
from pathlib import Path

from sim import address_space
from sim import command_ring
from sim import tile_scheduler

_ABI_PATH = Path(__file__).resolve().parents[2] / "spec" / "npu_abi.json"


def _schema():
    with open(_ABI_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def test_abi_dram_base_matches_address_space():
    """DRAM base is the same in the schema, address_space, and command_ring."""
    schema = _schema()
    abi_dram_base = int(schema["address_regions"]["DRAM"]["base"], 16)
    assert abi_dram_base == address_space.DRAM_BASE
    assert abi_dram_base == command_ring.RING_BASE


def test_abi_ring_config_matches_command_ring():
    """Ring constants in the schema equal sim/command_ring.py values."""
    schema = _schema()
    cfg = schema["rings"]["configuration"]
    assert command_ring.RING_BASE == int(cfg["ring_buffer_addr"], 16)
    assert command_ring.RING_ENTRIES == cfg["ring_entries"]
    assert command_ring.CMD_ENTRY_SIZE == cfg["cmd_entry_size"]
    assert command_ring.COMPLETION_RING_ADDR == int(
        cfg["completion_ring_addr"], 16
    )
    assert address_space.COMPLETION_ENTRY_SIZE == cfg["completion_entry_size"]


def test_abi_completion_ring_addr_matches_address_space():
    """Completion ring address is consistent across schema and address_space."""
    schema = _schema()
    cfg = schema["rings"]["configuration"]
    assert address_space.COMPLETION_RING_ADDR == int(
        cfg["completion_ring_addr"], 16
    )


def test_abi_tile_scale_matches_firmware_grouping():
    """Schema tile_scale_bytes is the firmware per-64 grouping (256B)."""
    schema = _schema()
    cfg = schema["rings"]["configuration"]
    assert cfg["tile_scale_bytes"] == 256
    # The Python tile_scheduler intentionally doubles the grouping width.
    assert tile_scheduler.TILE_W == 128
    assert tile_scheduler.TILE_SCALE_BYTES == 512
    assert tile_scheduler.TILE_SCALE_BYTES == tile_scheduler.TILE_W * 4


def test_abi_dram_size_regression_window_is_distinct():
    """The 8 MB regression window is distinct from the 2 GB ABI DRAM size."""
    schema = _schema()
    abi_dram_size = int(schema["address_regions"]["DRAM"]["size"], 16)
    assert abi_dram_size == 0x80000000  # 2 GB chip-level window
    assert address_space.DRAM_SIZE == 0x00800000  # 8 MB RTL regression window
    assert abi_dram_size != address_space.DRAM_SIZE
