"""DRAM address-space contract for the Func Model firmware/host path.

Python source of truth for the DRAM region layout shared by the control
plane (command/completion rings, descriptor pool) and the data plane
(activation/weight arenas). Values are pinned to external truth sources and
must not be redefined independently:

- ``sim/spike_host.py``:44,66,67,347-352
- ``spec/npu_abi.json``:1435,1579-1582 (rings.configuration)
- ``firmware/npu_firmware.c``:15-16,28-31

All regions are half-open intervals ``[base, base + size)``. The 8 MB window
``[DRAM_BASE, DRAM_END)`` matches the RTL ``dram_model`` regression window
(BUG-RTL-SOC-002).

Layout overview::

    command ring    [0x80000000, 0x80008000)   1024 entries x 32 B
    completion ring [0x80008000, 0x80010000)   1024 entries x 32 B
    descriptor pool [0x80010000, 0x80020000)   stride 64 B (1024 slots)
    activation      [0x80020000, 0x801E0000)
    weight          [0x801E0000, 0x80800000)

:func:`contract_check` is parameterized (``ring_entries``, ``desc_base``,
``desc_count``, ``act_base``) so per-runner layouts such as the FM-SOC P0/P4
configurations (fm-hardening-phase10 todo 3) can pass explicit
configurations instead of the defaults.
"""


class OverlapError(ValueError):
    """Two DRAM regions overlap, violating the address-space contract."""


class WindowError(ValueError):
    """An address falls outside the 8 MB DRAM window [DRAM_BASE, DRAM_END)."""


DRAM_BASE = 0x80000000
DRAM_SIZE = 0x00800000
DRAM_END = DRAM_BASE + DRAM_SIZE

RING_ENTRIES = 1024
CMD_ENTRY_SIZE = 32
COMPLETION_ENTRY_SIZE = 32
COMPLETION_RING_ADDR = 0x80008000

DESC_BASE = 0x80010000
DESC_STRIDE = 64

FP_DRAM_BASE = 0x80020000
FP_DRAM_SIZE = 0x007E0000
P10_ACT_BASE = 0x80020000
P10_ACT_END = 0x801E0000
P10_WGT_BASE = 0x801E0000
P10_WGT_END = 0x80800000

REGIONS = {
    "command_ring": (DRAM_BASE, RING_ENTRIES * CMD_ENTRY_SIZE),
    "completion_ring": (
        COMPLETION_RING_ADDR,
        RING_ENTRIES * COMPLETION_ENTRY_SIZE,
    ),
    "descriptor_pool": (DESC_BASE, P10_ACT_BASE - DESC_BASE),
    "activation": (P10_ACT_BASE, P10_ACT_END - P10_ACT_BASE),
    "weight": (P10_WGT_BASE, P10_WGT_END - P10_WGT_BASE),
}


def _as_region(region):
    if isinstance(region, str):
        return REGIONS[region]
    base, size = region
    return int(base), int(size)


def regions_overlap(a, b):
    """Return True if the half-open intervals of ``a`` and ``b`` share an address.

    Each argument is either a key into :data:`REGIONS` or a ``(base, size)``
    tuple. Touching boundaries (``a_end == b_base``) are not an overlap.
    """
    a_base, a_size = _as_region(a)
    b_base, b_size = _as_region(b)
    return a_base < b_base + b_size and b_base < a_base + a_size


def addr_in_window(addr, size=0):
    """Return True if ``[addr, addr + size)`` lies inside [DRAM_BASE, DRAM_END)."""
    return DRAM_BASE <= addr < DRAM_END and addr + size <= DRAM_END


def contract_check(
    ring_entries=RING_ENTRIES, desc_base=None, desc_count=0, act_base=None
):
    """Assert the descriptor region respects the ring and activation layout.

    ``desc_base=None`` resolves to :data:`DESC_BASE`. Assertions:

    (a) ``desc_base >=`` completion-ring end, where the completion ring ends
        at ``DRAM_BASE + ring_entries * (CMD_ENTRY_SIZE + COMPLETION_ENTRY_SIZE)``
        (command and completion rings each hold ``ring_entries`` entries);
        violation raises :class:`OverlapError`.

    (b) ``desc_base + desc_count * DESC_STRIDE <= act_base``, only when
        ``act_base`` is not None. Pass :data:`P10_ACT_BASE` for the default
        spike_host bound; pass another bound for per-runner layouts; pass
        None to skip this assertion. Violation raises :class:`OverlapError`.

    Every span involved (command ring, completion ring, descriptor region,
    and ``act_base`` when given) must lie inside the 8 MB window
    [DRAM_BASE, DRAM_END); violation raises :class:`WindowError`.
    """
    if desc_base is None:
        desc_base = DESC_BASE
    ring_end = DRAM_BASE + ring_entries * CMD_ENTRY_SIZE
    completion_end = ring_end + ring_entries * COMPLETION_ENTRY_SIZE
    desc_end = desc_base + desc_count * DESC_STRIDE

    spans = {
        "command ring": (DRAM_BASE, ring_end),
        "completion ring": (ring_end, completion_end),
        "descriptor region": (desc_base, desc_end),
    }
    for name, (lo, hi) in spans.items():
        if lo < DRAM_BASE or hi > DRAM_END:
            raise WindowError(
                f"{name} [{lo:#x}, {hi:#x}) falls outside the 8 MB DRAM "
                f"window [{DRAM_BASE:#x}, {DRAM_END:#x})"
            )
    if act_base is not None and not addr_in_window(act_base):
        raise WindowError(
            f"act_base {act_base:#x} falls outside the 8 MB DRAM window"
        )
    if desc_base < completion_end:
        raise OverlapError(
            f"desc_base {desc_base:#x} falls below completion-ring end "
            f"{completion_end:#x} (ring_entries={ring_entries})"
        )
    if act_base is not None and desc_end > act_base:
        raise OverlapError(
            f"descriptor region [{desc_base:#x}, {desc_end:#x}) exceeds "
            f"act_base {act_base:#x}"
        )
