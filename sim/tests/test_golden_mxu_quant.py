"""GoldenMXU quantized matmul tests: MX-01 through MX-05 from sim/testplan.md.

MX-04: pack_int4 ↔ unpack_int4 roundtrip for all 16 values [-8,7], bit-exact.
MX-05: unpack_int4 sign extension — 0x08→-8, 0x0F→-1, 0x07→7.
MX-01: matmul_from_sram output == matmul_int32 reference.
MX-02: matmul_int4_per_channel scale=1 → matches matmul_int32; scale≠1 → manual verify.
MX-03: matmul_int4_per_block block_size=K → matches per_channel; block_size=32 → boundary correct.
"""

import numpy as np
import pytest

from golden_executor import GoldenMXU, SRAM

SEED = 12345

# ══════════════════════════════════════════════════════════════════════
# MX-04: pack_int4 ↔ unpack_int4 roundtrip — all 16 values [-8, 7]
# ══════════════════════════════════════════════════════════════════════


class TestMX04PackRoundtrip:
    """pack_int4 ↔ unpack_int4 roundtrip, bit-exact for all 16 int4 values."""

    def test_all_sixteen_values_roundtrip(self):
        """Each value in [-8, 7]: pack single → unpack → original."""
        mxu = GoldenMXU()
        for v in range(-8, 8):
            packed = mxu.pack_int4(np.array([v], dtype=np.int8))
            unpacked = mxu.unpack_int4(packed)
            assert unpacked[0] == v, f"Roundtrip failed for value {v}: got {unpacked[0]}"

    def test_full_sequence_roundtrip(self):
        """Full [-8..7] sequence: pack_all → unpack_all, 16 values bit-exact."""
        mxu = GoldenMXU()
        values = np.arange(-8, 8, dtype=np.int8)
        packed = mxu.pack_int4(values)
        assert len(packed) == 8, f"Expected 8 packed bytes, got {len(packed)}"
        unpacked = mxu.unpack_int4(packed)
        assert np.array_equal(unpacked, values), (
            f"Roundtrip mismatch:\n  original: {values}\n  unpacked: {unpacked}"
        )

    def test_known_packed_bytes(self):
        """Known (low,high) pairs produce expected byte values."""
        mxu = GoldenMXU()
        # [0, 1] → low=0, high=1 → byte = (1<<4)|0 = 0x10
        assert mxu.pack_int4(np.array([0, 1], dtype=np.int8))[0] == 0x10
        # [-1, -1] → unsigned=15,15 → byte = (15<<4)|15 = 0xFF
        assert mxu.pack_int4(np.array([-1, -1], dtype=np.int8))[0] == 0xFF
        # [7, -8] → unsigned=7,8 → byte = (8<<4)|7 = 0x87
        assert mxu.pack_int4(np.array([7, -8], dtype=np.int8))[0] == 0x87

    def test_anti_vacuous(self):
        """Different inputs must produce different packed bytes."""
        mxu = GoldenMXU()
        p1 = mxu.pack_int4(np.array([0, 0], dtype=np.int8))
        p2 = mxu.pack_int4(np.array([1, 0], dtype=np.int8))
        assert not np.array_equal(p1, p2), (
            "pack_int4([0,0]) and pack_int4([1,0]) must differ"
        )


# ══════════════════════════════════════════════════════════════════════
# MX-05: unpack_int4 sign extension
# ══════════════════════════════════════════════════════════════════════


class TestMX05SignExtension:
    """unpack_int4 sign extension: 0x08→-8, 0x0F→-1, 0x07→7."""

    def test_0x08_to_minus8(self):
        """0x08 in low nibble → -8 (two's complement sign extension)."""
        mxu = GoldenMXU()
        assert mxu.unpack_int4(np.array([0x08], dtype=np.uint8))[0] == -8

    def test_0x0F_to_minus1(self):
        """0x0F in low nibble → -1 (two's complement sign extension)."""
        mxu = GoldenMXU()
        assert mxu.unpack_int4(np.array([0x0F], dtype=np.uint8))[0] == -1

    def test_0x07_to_7(self):
        """0x07 in low nibble → 7 (positive, no sign extension)."""
        mxu = GoldenMXU()
        assert mxu.unpack_int4(np.array([0x07], dtype=np.uint8))[0] == 7

    def test_high_nibble_sign_extension(self):
        """High nibble also sign-extends correctly for both nibbles."""
        mxu = GoldenMXU()
        # 0x80 = low=0, high=8 → [0, -8]
        u = mxu.unpack_int4(np.array([0x80], dtype=np.uint8))
        assert u[0] == 0 and u[1] == -8, f"0x80 → {u}, expected [0, -8]"
        # 0xF7 = low=7, high=15 → [7, -1]
        u2 = mxu.unpack_int4(np.array([0xF7], dtype=np.uint8))
        assert u2[0] == 7 and u2[1] == -1, f"0xF7 → {u2}, expected [7, -1]"

    def test_all_256_bytes(self):
        """All 256 byte values: verify both nibbles sign-extend correctly."""
        mxu = GoldenMXU()
        packed = np.arange(256, dtype=np.uint8)
        unpacked = mxu.unpack_int4(packed)
        for i in range(256):
            low = i & 0x0F
            high = (i >> 4) & 0x0F
            exp_low = low if low < 8 else low - 16
            exp_high = high if high < 8 else high - 16
            assert unpacked[2 * i] == exp_low, (
                f"byte={i:#04x}: low → {unpacked[2*i]}, expected {exp_low}"
            )
            assert unpacked[2 * i + 1] == exp_high, (
                f"byte={i:#04x}: high → {unpacked[2*i+1]}, expected {exp_high}"
            )

    def test_anti_vacuous(self):
        """Different packed bytes produce different unpacked results."""
        mxu = GoldenMXU()
        assert not np.array_equal(
            mxu.unpack_int4(np.array([0x08], dtype=np.uint8)),
            mxu.unpack_int4(np.array([0x00], dtype=np.uint8)),
        ), "unpack_int4(0x08) and unpack_int4(0x00) must differ"


# ══════════════════════════════════════════════════════════════════════
# Helpers for MX-01, MX-02, MX-03
# ══════════════════════════════════════════════════════════════════════


def _setup_matmul_data(M, K, N, rng):
    """Fill SRAM with activation + packed weights for (M,K,N) matmul.

    Returns: (activation_int8_1d, weight_packed_1d, weight_unpacked_2d,
              sram, act_addr, wgt_addr)
    """
    act_addr = 0x200000
    wgt_addr = 0x000000

    activation = rng.randint(-128, 128, size=M * K, dtype=np.int8)
    w_values = rng.randint(-8, 8, size=K * N, dtype=np.int8)

    mxu = GoldenMXU()
    weight_packed = mxu.pack_int4(w_values)
    # Original values before packing — used as reference for manual compute
    weight_unpacked = w_values.reshape(K, N)

    sram = SRAM()
    sram.write_bytes(act_addr, activation.view(np.uint8))
    sram.write_bytes(wgt_addr, weight_packed)

    return activation, weight_packed, weight_unpacked, sram, act_addr, wgt_addr


# ══════════════════════════════════════════════════════════════════════
# MX-01: matmul_from_sram == matmul_int32 reference
# ══════════════════════════════════════════════════════════════════════


class TestMX01MatmulFromSram:
    """matmul_from_sram output must match matmul_int32 with same data."""

    @pytest.mark.parametrize("M,K,N", [
        pytest.param(1, 64, 64, id="M1_K64_N64"),
        pytest.param(2, 128, 64, id="M2_K128_N64"),
        pytest.param(4, 64, 128, id="M4_K64_N128"),
        pytest.param(8, 256, 32, id="M8_K256_N32"),
        pytest.param(16, 32, 16, id="M16_K32_N16"),
    ])
    def test_matches_matmul_int32(self, M, K, N):
        """matmul_from_sram must equal matmul_int32 for same (M,K,N)."""
        rng = np.random.RandomState(SEED + M * 100 + K * 10 + N)
        act, w_packed, _, sram, act_addr, wgt_addr = _setup_matmul_data(M, K, N, rng)
        mxu = GoldenMXU()

        ref = mxu.matmul_int32(act, w_packed, M, K, N)
        result = mxu.matmul_from_sram(M, K, N, act_addr, wgt_addr, sram.data)

        assert np.array_equal(result, ref), (
            f"M={M},K={K},N={N}: matmul_from_sram differs from matmul_int32\n"
            f"  max_diff={np.max(np.abs(result.astype(np.int64) - ref.astype(np.int64)))}"
        )

    def test_anti_vacuous(self):
        """Different SRAM data produces different matmul_from_sram results."""
        rng1 = np.random.RandomState(SEED)
        rng2 = np.random.RandomState(999)
        mxu = GoldenMXU()
        M, K, N = 2, 8, 4

        _, _, _, sram1, act_addr, wgt_addr = _setup_matmul_data(M, K, N, rng1)
        _, _, _, sram2, _, _ = _setup_matmul_data(M, K, N, rng2)

        res1 = mxu.matmul_from_sram(M, K, N, act_addr, wgt_addr, sram1.data)
        res2 = mxu.matmul_from_sram(M, K, N, act_addr, wgt_addr, sram2.data)

        assert not np.array_equal(res1, res2), (
            "Different SRAM data must produce different matmul_from_sram results"
        )


# ══════════════════════════════════════════════════════════════════════
# MX-02: matmul_int4_per_channel — scale=1 matches matmul_int32; scale≠1 manual verify
# ══════════════════════════════════════════════════════════════════════


class TestMX02PerChannel:
    """matmul_int4_per_channel: quantized matmul with per-column scaling."""

    @pytest.mark.parametrize("M,K,N", [
        pytest.param(1, 32, 16, id="M1_K32_N16"),
        pytest.param(2, 64, 32, id="M2_K64_N32"),
        pytest.param(4, 16, 64, id="M4_K16_N64"),
        pytest.param(3, 48, 24, id="M3_K48_N24"),
    ])
    def test_scale_one_matches_int32(self, M, K, N):
        """scale=1 → per_channel result == matmul_int32 cast to float32."""
        rng = np.random.RandomState(SEED + 100 + M * 10 + K)
        act, w_packed, _, _, _, _ = _setup_matmul_data(M, K, N, rng)
        mxu = GoldenMXU()

        int32_ref = mxu.matmul_int32(act, w_packed, M, K, N)
        scales = np.ones(N, dtype=np.float32)
        per_ch = mxu.matmul_int4_per_channel(act, w_packed, scales, M, K, N)

        # With scale=1, per_channel = int32_ref.astype(np.float32) * 1.0
        expected = int32_ref.astype(np.float32)
        assert np.allclose(per_ch, expected, atol=1e-6, rtol=0), (
            f"M={M},K={K},N={N}: per_channel(scale=1) != int32 ref\n"
            f"  max_abs_diff={np.max(np.abs(per_ch - expected))}"
        )

    @pytest.mark.parametrize("M,K,N", [
        pytest.param(1, 16, 8, id="M1_K16_N8"),
        pytest.param(2, 32, 16, id="M2_K32_N16"),
    ])
    def test_scale_non_one_manual_verify(self, M, K, N):
        """scale≠1: per_channel result == int32 × scale_elementwise, manually verified."""
        rng = np.random.RandomState(SEED + 200 + M * 10 + K)
        act, w_packed, _, _, _, _ = _setup_matmul_data(M, K, N, rng)
        mxu = GoldenMXU()

        int32_ref = mxu.matmul_int32(act, w_packed, M, K, N)
        scales = rng.uniform(0.5, 2.0, size=N).astype(np.float32)
        per_ch = mxu.matmul_int4_per_channel(act, w_packed, scales, M, K, N)

        # Manual: int32_ref * scales broadcast over columns
        expected = int32_ref.astype(np.float32) * scales[np.newaxis, :]
        assert np.allclose(per_ch, expected, atol=1e-6, rtol=1e-6), (
            f"M={M},K={K},N={N}: per_channel(scale≠1) != manual multiply\n"
            f"  max_abs_diff={np.max(np.abs(per_ch - expected))}"
        )

    def test_anti_vacuous(self):
        """Different scales produce different per_channel results."""
        rng = np.random.RandomState(SEED)
        M, K, N = 2, 8, 4
        act, w_packed, _, _, _, _ = _setup_matmul_data(M, K, N, rng)
        mxu = GoldenMXU()

        s1 = np.ones(N, dtype=np.float32)
        s2 = np.full(N, 2.0, dtype=np.float32)
        r1 = mxu.matmul_int4_per_channel(act, w_packed, s1, M, K, N)
        r2 = mxu.matmul_int4_per_channel(act, w_packed, s2, M, K, N)

        assert not np.allclose(r1, r2, atol=0, rtol=1e-6), (
            "Different scales must produce different per_channel results"
        )


# ══════════════════════════════════════════════════════════════════════
# MX-03: matmul_int4_per_block — block_size=K matches per_channel; block_size=32 boundary correct
# ══════════════════════════════════════════════════════════════════════


class TestMX03PerBlock:
    """matmul_int4_per_block: K-dimension block-split with per-block per-channel scales."""

    @pytest.mark.parametrize("M,K,N", [
        pytest.param(1, 32, 16, id="M1_K32_N16"),
        pytest.param(2, 64, 32, id="M2_K64_N32"),
        pytest.param(4, 48, 24, id="M4_K48_N24"),
    ])
    def test_block_size_K_matches_per_channel(self, M, K, N):
        """block_size=K → 1 block → result matches per_channel."""
        rng = np.random.RandomState(SEED + 300 + M * 10 + K)
        act, w_packed, _, _, _, _ = _setup_matmul_data(M, K, N, rng)
        mxu = GoldenMXU()

        scales = rng.uniform(0.5, 2.0, size=N).astype(np.float32)
        per_ch = mxu.matmul_int4_per_channel(act, w_packed, scales, M, K, N)

        # Per-block with group_size=K: scales shape (1, N)
        block_scales = scales[np.newaxis, :]
        per_blk = mxu.matmul_int4_per_block(
            act, w_packed, block_scales, M, K, N, group_size=K
        )

        assert np.allclose(per_blk, per_ch, atol=1e-6, rtol=1e-6), (
            f"M={M},K={K},N={N}: per_block(block_size=K) != per_channel\n"
            f"  max_abs_diff={np.max(np.abs(per_blk - per_ch))}"
        )

    @pytest.mark.parametrize("M,K,N", [
        pytest.param(1, 64, 16, id="M1_K64_N16"),
        pytest.param(2, 96, 32, id="M2_K96_N32"),
        pytest.param(4, 128, 16, id="M4_K128_N16"),
    ])
    def test_block_size_32_boundary(self, M, K, N):
        """block_size=32: split K into blocks, manual recompute matches."""
        rng = np.random.RandomState(SEED + 400 + M * 10 + K)
        act, w_packed, w_unpacked, _, _, _ = _setup_matmul_data(M, K, N, rng)
        mxu = GoldenMXU()
        group_size = 32
        num_blocks = (K + group_size - 1) // group_size

        block_scales = rng.uniform(0.5, 2.0, size=(num_blocks, N)).astype(np.float32)
        per_blk = mxu.matmul_int4_per_block(
            act, w_packed, block_scales, M, K, N, group_size=group_size
        )

        # Manual recompute: per block, int32 dot → scale → accumulate
        A = act.reshape(M, K).astype(np.int32)
        W = w_unpacked.astype(np.int32)
        expected = np.zeros((M, N), dtype=np.float32)
        for b in range(num_blocks):
            k_start = b * group_size
            k_end = min(k_start + group_size, K)
            a_blk = A[:, k_start:k_end]
            w_blk = W[k_start:k_end, :]
            partial = np.dot(a_blk, w_blk)
            partial = np.clip(partial, -2**31, 2**31 - 1)
            expected += partial.astype(np.float32) * block_scales[b, :][np.newaxis, :]

        assert np.allclose(per_blk, expected, atol=1e-6, rtol=1e-6), (
            f"M={M},K={K},N={N},group_size={group_size}: per_block != manual compute\n"
            f"  max_abs_diff={np.max(np.abs(per_blk - expected))}"
        )

    def test_anti_vacuous(self):
        """Different block scales produce different per_block results."""
        rng = np.random.RandomState(SEED)
        M, K, N = 2, 64, 8
        act, w_packed, _, _, _, _ = _setup_matmul_data(M, K, N, rng)
        mxu = GoldenMXU()
        group_size = 32
        num_blocks = 2

        s1 = np.ones((num_blocks, N), dtype=np.float32)
        s2 = np.full((num_blocks, N), 2.0, dtype=np.float32)
        r1 = mxu.matmul_int4_per_block(
            act, w_packed, s1, M, K, N, group_size=group_size
        )
        r2 = mxu.matmul_int4_per_block(
            act, w_packed, s2, M, K, N, group_size=group_size
        )

        assert not np.allclose(r1, r2, atol=0, rtol=1e-6), (
            "Different block scales must produce different per_block results"
        )
