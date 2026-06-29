"""GoldenDMA tests: DM-01 through DM-04 from sim/testplan.md.

DM-01: encode/decode — random 100 legal descriptors, encode→decode roundtrip bit-exact
DM-02: decode — invalid src/dst/len combinations must raise exception
DM-03: actual_size — size=0→0, size=4096→4096, size>4096→exception
DM-04: GoldenDMA end-to-end — known pattern → execute_load → readback bit-exact
"""

import numpy as np
import pytest

from golden_executor import DMADescriptor, GoldenDMA, SRAM

SEED = 12345

# ══════════════════════════════════════════════════════════════════════
# DM-01: encode/decode roundtrip — 100 random descriptors
# ══════════════════════════════════════════════════════════════════════


def _random_desc(rng: np.random.RandomState) -> DMADescriptor:
    """Build a random legal descriptor."""
    return DMADescriptor(
        dram_addr=rng.randint(0, 0x100000000),
        sram_addr=rng.randint(0, 0x10000),
        size=rng.randint(0, 4096),          # 0..4095 (0 encodes as 4096 bytes)
        direction=rng.randint(0, 2),
        last=bool(rng.randint(0, 2)),
        channel=rng.randint(0, 4),
    )


def test_dm01_100_roundtrip():
    """Random 100 legal descriptors: encode → decode → all fields match.

    Anti-vacuous gate: asserts actual_size and every field individually.
    A vacuous pass (encode→decode always returns same hard-coded value)
    would be caught because random inputs produce different fields.
    """
    rng = np.random.RandomState(SEED)
    for i in range(100):
        desc = _random_desc(rng)
        word = desc.encode()
        decoded = DMADescriptor.decode(word)

        # Size encoding: 4096 in DMADescriptor → 0 in field → 0 in decoded.size
        expected_size = 0 if desc.size == 4096 else desc.size

        assert decoded.dram_addr == desc.dram_addr, (
            f"[{i}] dram_addr: {decoded.dram_addr:#x} != {desc.dram_addr:#x}"
        )
        assert decoded.sram_addr == desc.sram_addr, (
            f"[{i}] sram_addr: {decoded.sram_addr:#x} != {desc.sram_addr:#x}"
        )
        assert decoded.size == expected_size, (
            f"[{i}] size: {decoded.size} != expected {expected_size} (original {desc.size})"
        )
        assert decoded.direction == desc.direction, (
            f"[{i}] direction: {decoded.direction} != {desc.direction}"
        )
        assert decoded.last == desc.last, (
            f"[{i}] last: {decoded.last} != {desc.last}"
        )
        assert decoded.channel == desc.channel, (
            f"[{i}] channel: {decoded.channel} != {desc.channel}"
        )
        # actual_size must roundtrip too
        assert decoded.actual_size == desc.actual_size, (
            f"[{i}] actual_size: {decoded.actual_size} != {desc.actual_size}"
        )


def test_dm01_anti_vacuous():
    """Anti-vacuous: different descriptors produce different encoded words."""
    d1 = DMADescriptor(dram_addr=0x1000, sram_addr=0x200, size=256,
                        direction=0, last=False, channel=0)
    d2 = DMADescriptor(dram_addr=0x1001, sram_addr=0x200, size=256,
                        direction=0, last=False, channel=0)
    assert d1.encode() != d2.encode(), (
        "Two descriptors differing only in dram_addr must produce different encoded words"
    )


# ══════════════════════════════════════════════════════════════════════
# DM-02: decode — invalid src/dst/len combinations must raise exception
# ══════════════════════════════════════════════════════════════════════


class TestDM02InvalidCombinations:
    """Invalid field values (direction, channel, size) must be rejected."""

    @pytest.mark.parametrize("direction,channel", [
        (2, 0),   # direction out of range
        (-1, 0),  # negative direction
        (0, 4),   # channel out of range
        (0, -1),  # negative channel
        (3, 0),   # direction out of range (multi-bit)
        (0, 7),   # channel out of range (multi-bit)
    ])
    def test_invalid_direction_or_channel(self, direction: int, channel: int):
        """direction must be 0/1, channel must be 0..3."""
        with pytest.raises((ValueError, AssertionError, TypeError)):
            DMADescriptor(dram_addr=0, sram_addr=0, size=64,
                          direction=direction, channel=channel)

    @pytest.mark.parametrize("size", [-1, 4097, -4096, 9999])
    def test_invalid_size(self, size: int):
        """Size must be in range [0, 4095]."""
        with pytest.raises((ValueError, AssertionError)):
            DMADescriptor(dram_addr=0, sram_addr=0, size=size)

    def test_invalid_sram_addr(self):
        """sram_addr must fit in 16 bits (0..0xFFFF)."""
        with pytest.raises((ValueError, AssertionError)):
            DMADescriptor(dram_addr=0, sram_addr=0x10000, size=64)

    def test_invalid_dram_addr(self):
        """dram_addr must fit in 32 bits (0..0xFFFFFFFF)."""
        with pytest.raises((ValueError, AssertionError)):
            DMADescriptor(dram_addr=0x100000000, sram_addr=0, size=64)

    # --- anti-vacuous: valid descriptor does NOT raise ---

    def test_anti_vacuous_valid(self):
        """A fully legal descriptor raises nothing."""
        desc = DMADescriptor(dram_addr=0x1000, sram_addr=0x200, size=256,
                             direction=0, last=False, channel=0)
        assert desc.dram_addr == 0x1000
        assert desc.sram_addr == 0x200


# ══════════════════════════════════════════════════════════════════════
# DM-03: actual_size — field overflow semantics
# ══════════════════════════════════════════════════════════════════════


class TestDM03ActualSize:
    """actual_size property: hardware encoding 0→4096, field max 4095."""

    def test_size_zero_means_4096(self):
        """size=0 → actual_size=4096 (hardware encoding: 0 in 12-bit field = 4096 bytes)."""
        desc = DMADescriptor(dram_addr=0, sram_addr=0, size=0,
                             direction=0, last=False, channel=0)
        assert desc.actual_size == 4096, f"expected 4096, got {desc.actual_size}"

    def test_size_4096_roundtrip(self):
        """size=4096 → encode→decode → actual_size=4096 (field 0 means 4096 bytes)."""
        desc = DMADescriptor(dram_addr=0, sram_addr=0, size=4096,
                             direction=0, last=False, channel=0)
        word = desc.encode()
        decoded = DMADescriptor.decode(word)
        # After encode→decode, size field is 0 (since 4096→0 encoding)
        assert decoded.size == 0, f"decoded.size should be 0, got {decoded.size}"
        assert decoded.actual_size == 4096, (
            f"decoded.actual_size should be 4096, got {decoded.actual_size}"
        )

    def test_size_over_4096_raises(self):
        """size > 4095 must raise (12-bit field overflow)."""
        with pytest.raises((ValueError, AssertionError)):
            DMADescriptor(dram_addr=0, sram_addr=0, size=4097)

    def test_regular_sizes(self):
        """size=1..4095 → actual_size == size."""
        for sz in [1, 64, 512, 1024, 2048, 4095]:
            desc = DMADescriptor(dram_addr=0, sram_addr=0, size=sz)
            assert desc.actual_size == sz, f"size={sz}: actual_size={desc.actual_size}"

    def test_anti_vacuous_encode_size_zero(self):
        """DMADescriptor(size=0) encodes with size bits = 0, not 4096."""
        desc = DMADescriptor(dram_addr=0xABCD, sram_addr=0x1234, size=0)
        word = desc.encode()
        # The size field occupies bits [15:4]
        encoded_size = (word >> 4) & 0xFFF
        assert encoded_size == 0, (
            f"size=0 should encode as 0 in field, got {encoded_size}"
        )


# ══════════════════════════════════════════════════════════════════════
# DM-04: GoldenDMA end-to-end — known pattern → execute_load → readback
# ══════════════════════════════════════════════════════════════════════


def test_dm04_execute_load_bit_exact():
    """GoldenDMA.execute_load writes exact byte pattern from DRAM to SRAM.

    Creates a known non-trivial byte sequence, places it in DRAM,
    executes a DMA load descriptor, then reads back from SRAM and
    asserts bit-exact match.
    """
    sram = SRAM()
    dma = GoldenDMA()
    desc = DMADescriptor(
        dram_addr=0x1000,
        sram_addr=0x200,
        size=256,
        direction=0,   # load (DRAM → SRAM)
        last=True,
        channel=0,
    )

    # DRAM data: a known non-trivial byte pattern
    rng = np.random.RandomState(SEED)
    dram_data = np.zeros(0x10000, dtype=np.uint8)
    pattern = rng.randint(0, 256, size=256, dtype=np.uint8)
    dram_data[0x1000:0x1000 + 256] = pattern

    dma.execute_load(sram, desc, dram_data)

    # Read back from SRAM
    actual = sram.read_bytes(0x200, 256)
    assert np.array_equal(actual, pattern), (
        "SRAM data does not match DRAM source after DMA load"
    )


def test_dm04_execute_store_bit_exact():
    """GoldenDMA.execute_store writes exact byte pattern from SRAM to DRAM."""
    sram = SRAM()
    dma = GoldenDMA()
    desc = DMADescriptor(
        dram_addr=0x2000,
        sram_addr=0x400,
        size=128,
        direction=1,   # store (SRAM → DRAM)
        last=True,
        channel=1,
    )

    rng = np.random.RandomState(42)
    pattern = rng.randint(0, 256, size=128, dtype=np.uint8)
    sram.write_bytes(0x400, pattern)

    dram_data = np.zeros(0x10000, dtype=np.uint8)
    dma.execute_store(sram, desc, dram_data)

    actual = dram_data[0x2000:0x2000 + 128]
    assert np.array_equal(actual, pattern), (
        "DRAM data does not match SRAM source after DMA store"
    )


def test_dm04_anti_vacuous():
    """Anti-vacuous: DMA load from different DRAM addresses reads different data."""
    sram_a = SRAM()
    sram_b = SRAM()
    dma = GoldenDMA()

    dram_data = np.zeros(0x10000, dtype=np.uint8)
    dram_data[0x1000:0x1000 + 16] = np.arange(16, dtype=np.uint8)
    dram_data[0x2000:0x2000 + 16] = np.arange(16, 32, dtype=np.uint8)

    desc_a = DMADescriptor(dram_addr=0x1000, sram_addr=0, size=16, direction=0)
    desc_b = DMADescriptor(dram_addr=0x2000, sram_addr=0, size=16, direction=0)

    dma.execute_load(sram_a, desc_a, dram_data)
    dma.execute_load(sram_b, desc_b, dram_data)

    data_a = sram_a.read_bytes(0, 16)
    data_b = sram_b.read_bytes(0, 16)

    # Same SRAM address from different DRAM sources → different data
    assert not np.array_equal(data_a, data_b), (
        "DMA load from different DRAM addresses must produce different SRAM data"
    )
