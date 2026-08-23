"""Command ring configuration — single source of truth.

All consumers (spike_host, rtl_soc_segment_run, cocotb_bridge, rtl_soc_runner)
must import ring constants and helpers from this module instead of defining
their own copies.

Values are pinned to external truth sources:

- ``spec/npu_abi.json``:1435,1579-1582 (rings.configuration)
- ``firmware/npu_firmware.c``:28-31
"""

RING_BASE = 0x80000000
RING_ENTRIES = 1024
CMD_ENTRY_SIZE = 32
COMPLETION_RING_ADDR = 0x80008000
DESC_STRIDE = 64


class RingOverflowError(ValueError):
    """Command count or descriptor region violates the per-runner ring layout."""


def ring_entry_addr(i: int) -> int:
    """Return the DRAM address of ring entry *i* (modulo RING_ENTRIES)."""
    return RING_BASE + (i % RING_ENTRIES) * CMD_ENTRY_SIZE


def advance_head(cur: int, n: int) -> int:
    """Advance a head pointer by *n* entries (modulo RING_ENTRIES)."""
    return (cur + n) % RING_ENTRIES


def expected_head(total_cmds: int) -> int:
    """Return the expected NPU_HEAD value after *total_cmds* commands."""
    return total_cmds % RING_ENTRIES


def assert_ring_size(total_cmds: int, ring_size: int) -> None:
    """Raise RingOverflowError if total_cmds exceeds the per-runner ring size."""
    if total_cmds > ring_size:
        raise RingOverflowError(
            f"too many commands ({total_cmds}) for per-runner ring size {ring_size}"
        )


def assert_desc_clear_of_used_regions(
    desc_base: int,
    desc_count: int,
    desc_stride: int = DESC_STRIDE,
    ring_usage_end: int = RING_BASE + RING_ENTRIES * CMD_ENTRY_SIZE,
    completion_usage_end: int = COMPLETION_RING_ADDR + RING_ENTRIES * 32,
) -> None:
    """Raise RingOverflowError if descriptor region overlaps used ring areas.

    This is the scoped layout guard for P0/P1/P2P3: only the entries the runner
    actually writes are considered, not the full 1024-entry firmware ring.
    """
    desc_end = desc_base + desc_count * desc_stride
    if desc_base < ring_usage_end and desc_end > RING_BASE:
        raise RingOverflowError(
            f"descriptor region [0x{desc_base:08x}, 0x{desc_end:08x}) overlaps "
            f"command-ring usage [0x{RING_BASE:08x}, 0x{ring_usage_end:08x})"
        )
    if desc_base < completion_usage_end and desc_end > COMPLETION_RING_ADDR:
        raise RingOverflowError(
            f"descriptor region [0x{desc_base:08x}, 0x{desc_end:08x}) overlaps "
            f"completion-ring usage [0x{COMPLETION_RING_ADDR:08x}, "
            f"0x{completion_usage_end:08x})"
        )
