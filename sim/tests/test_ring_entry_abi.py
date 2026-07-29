"""Golden-vector test for ring entry ABI: Python <III> packing must match C cmd_entry_t.

The firmware defines cmd_entry_t as {uint32_t opcode, uint32_t desc_addr,
uint32_t flags, uint32_t _pad[5]} — all fields are uint32_t, packed little-endian.
The Python device server's 24B flat ring format captures the first three uint32_t
fields (12 bytes) plus 12 bytes of padding.

This test verifies that Python struct.pack("<III", ...) produces the same first
12 bytes as the C compiler's cmd_entry_t layout, and that the roundtrip
pack → unpack is identity.
"""

import struct

# Golden bytes from C golden_ring_entry.c (gcc -O0 on x86-64 Linux).
# Each entry is the first 12 bytes of cmd_entry_t: opcode + desc_addr + flags
# as packed by the C compiler (little-endian, uint32_t fields).
C_GOLDEN: list[tuple[int, int, int, bytes]] = [
    # (opcode, desc_addr, flags, golden_12b)
    (0x00000001, 0x80F00000, 0x00000003,
     b"\x01\x00\x00\x00\x00\x00\xf0\x80\x03\x00\x00\x00"),
    (0xFFFFFFFF, 0xDEADBEEF, 0xCAFEBABE,
     b"\xff\xff\xff\xff\xef\xbe\xad\xde\xbe\xba\xfe\xca"),
    (0x00000000, 0x00000000, 0x00000000,
     b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"),
    (0x00000042, 0x01234567, 0x89ABCDEF,
     b"\x42\x00\x00\x00\x67\x45\x23\x01\xef\xcd\xab\x89"),
    (0x7FFFFFFF, 0x80000000, 0x00000001,
     b"\xff\xff\xff\x7f\x00\x00\x00\x80\x01\x00\x00\x00"),
]

# The old <IQI format used before the ABI fix.
C_NON_GOLDEN_IQI: list[tuple[int, int, int, bytes]] = [
    # (opcode, desc_addr, flags, iqi_16b)
    (0x00000001, 0x80F00000, 0x00000003,
     b"\x01\x00\x00\x00\x00\x00\xf0\x80\x00\x00\x00\x00\x03\x00\x00\x00"),
    (0xFFFFFFFF, 0xDEADBEEF, 0xCAFEBABE,
     b"\xff\xff\xff\xff\xef\xbe\xad\xde\x00\x00\x00\x00\xbe\xba\xfe\xca"),
]


def _pack_iii(opcode: int, desc_addr: int, flags: int) -> bytes:
    """Pack the three uint32 fields using the corrected <III format."""
    return struct.pack("<III", opcode, desc_addr, flags)


def _pack_iqi(opcode: int, desc_addr: int, flags: int) -> bytes:
    """Pack using the old (incorrect) <IQI format."""
    return struct.pack("<IQI", opcode, desc_addr, flags)


def _make_24b_flat_entry(opcode: int, desc_addr: int, flags: int) -> bytes:
    """Produce a 24-byte flat ring entry as used by device_server.py."""
    return _pack_iii(opcode, desc_addr, flags) + b"\x00" * 12


# ── C golden match tests ────────────────────────────────────────────────────


def test_py_iii_matches_c_12b():
    """Python <III> packing produces the same first 12B as C cmd_entry_t."""
    for opcode, desc_addr, flags, golden in C_GOLDEN:
        py_bytes = _pack_iii(opcode, desc_addr, flags)
        assert py_bytes == golden, (
            f"opcode=0x{opcode:08x} desc_addr=0x{desc_addr:08x} "
            f"flags=0x{flags:08x}\n"
            f"  Python: {py_bytes.hex()}\n"
            f"  C gold: {golden.hex()}"
        )


def test_flat_24b_starts_with_iii_12b():
    """The 24B flat entry's first 12B match the C golden."""
    for opcode, desc_addr, flags, golden in C_GOLDEN:
        flat = _make_24b_flat_entry(opcode, desc_addr, flags)
        assert flat[:12] == golden, (
            f"24B flat entry first 12B mismatch for "
            f"opcode=0x{opcode:08x}"
        )
        assert len(flat) == 24
        # Tail 12 bytes must be zero padding.
        assert flat[12:] == b"\x00" * 12


# ── Roundtrip tests ─────────────────────────────────────────────────────────


def test_pack_unpack_roundtrip():
    """Pack with <III> then unpack with <III> returns the same values."""
    for opcode, desc_addr, flags, _golden in C_GOLDEN:
        packed = _pack_iii(opcode, desc_addr, flags)
        o2, da2, f2 = struct.unpack_from("<III", packed, 0)
        assert o2 == opcode
        assert da2 == desc_addr
        assert f2 == flags


def test_flat_24b_roundtrip():
    """24B flat ring entry roundtrip: pack → unpack yields same values."""
    for opcode, desc_addr, flags, _golden in C_GOLDEN:
        entry = _make_24b_flat_entry(opcode, desc_addr, flags)
        o2, da2, f2 = struct.unpack_from("<III", entry, 0)
        assert o2 == opcode
        assert da2 == desc_addr
        assert f2 == flags
        assert entry[12:] == b"\x00" * 12


# ── Proof that <IQI> is wrong ───────────────────────────────────────────────


def test_iqi_produces_different_bytes():
    """The old <IQI> packing produces bytes that do NOT match C cmd_entry_t."""
    for opcode, desc_addr, flags, iqi_golden in C_NON_GOLDEN_IQI:
        iqi_bytes = _pack_iqi(opcode, desc_addr, flags)
        # The <IQI> format itself is self-consistent (pack then unpack works),
        # but it encodes desc_addr as uint64_t with extra padding, so the
        # bytes are different from the C golden (uint32_t desc_addr).
        iii_bytes = _pack_iii(opcode, desc_addr, flags)
        assert iqi_bytes == iqi_golden, "pre-computed <IQI> golden mismatch"
        assert iqi_bytes != iii_bytes, (
            f"<IQI> and <III> should differ for "
            f"desc_addr=0x{desc_addr:08x}: "
            f"iqi={iqi_bytes.hex()} iii={iii_bytes.hex()}"
        )


def test_legacy_iqi_unpack_wrong_for_large_desc_addr():
    """Prove that <IQI> shifts the flags field because desc_addr is uint64."""
    # When packing with <IQI>: 4B opcode + 8B desc_addr + 4B flags = 16 bytes.
    # When packing with <III>: 4B opcode + 4B desc_addr + 4B flags = 12 bytes.
    # The <IQI> layout consumes 8 bytes for desc_addr, pushing flags to offset 12
    # instead of offset 8.  When the firmware cmd_entry_t has desc_addr as
    # uint32_t at offset 4-7 and flags at offset 8-11, unpacking the same bytes
    # with <IQI> gives: desc_addr=bytes[4:12] (including flags data) and
    # flags=bytes[12:16] (data from offset 12 onwards, which in the real 24B flat
    # format is padding zeros).
    iii_bytes = _pack_iii(0x42, 0xDEADBEEF, 0xCAFE)
    assert len(iii_bytes) == 12
    # Pad to 24 bytes like the old format did.
    padded = iii_bytes + b"\x00" * 12

    # Unpack with <IQI>: desc_addr reads 8 bytes (4-11), consuming the flags
    # bytes at offset 8-11 as upper 32 bits of desc_addr instead of as flags.
    o_iqi, da_iqi, f_iqi = struct.unpack_from("<IQI", padded, 0)
    # da_iqi = 0x00000000DEADBEEF if padding at 8-11 is zero, but in the
    # <III>-packed data, bytes 8-11 are 0xCAFE (the real flags). So desc_addr
    # via <IQI> now includes 0xCAFE in its upper 32 bits.
    # Expected: da_iqi = 0xDEADBEEF | (flags=0xCAFE << 32) = 0x0000CAFEDEADBEEF
    assert da_iqi == 0x0000CAFEDEADBEEF, (
        f"<IQI> desc_addr=0x{da_iqi:016x}, expected 0x0000CAFEDEADBEEF"
    )
    # The <IQI> flags field reads bytes 12-15, which are padding zeros.
    assert f_iqi == 0, (
        f"<IQI> flags=0x{f_iqi:08x}, expected 0 (shifted to padding zone)"
    )

    # With <III>, the correct unpacking returns the right values.
    o_iii, da_iii, f_iii = struct.unpack_from("<III", padded, 0)
    assert o_iii == 0x42
    assert da_iii == 0xDEADBEEF
    assert f_iii == 0xCAFE


# ── Edge cases ──────────────────────────────────────────────────────────────


def test_max_uint32_values():
    """All uint32 max values roundtrip correctly."""
    opcode = 0xFFFFFFFF
    desc_addr = 0xFFFFFFFF
    flags = 0xFFFFFFFF
    py_bytes = _pack_iii(opcode, desc_addr, flags)
    o2, da2, f2 = struct.unpack_from("<III", py_bytes, 0)
    assert o2 == opcode
    assert da2 == desc_addr
    assert f2 == flags


def test_non_zero_padding_is_preserved_in_flat():
    """In the 32B ring buffer on DRAM, after the 24B flat entry there may
    be non-zero data (the original 32B entry's _pad[5]).  The 24B flat format
    must only use the first 12 bytes of meaningful data and 12B of explicit
    zero padding, regardless of what's in the ring buffer beyond 24B."""
    opcode = 0x42
    desc_addr = 0x80F00000
    flags = 0x03
    entry_24 = _make_24b_flat_entry(opcode, desc_addr, flags)
    # Append garbage after the 24B entry to simulate DRAM ring buffer noise.
    ring_buffer_slot = entry_24 + b"\xDE\xAD\xBE\xEF" * 2  # 8 bytes of garbage
    o2, da2, f2 = struct.unpack_from("<III", ring_buffer_slot, 0)
    assert o2 == opcode
    assert da2 == desc_addr
    assert f2 == flags
    # The garbage is beyond 24B and should not affect the unpack.
    assert ring_buffer_slot[24:] == b"\xDE\xAD\xBE\xEF" * 2
