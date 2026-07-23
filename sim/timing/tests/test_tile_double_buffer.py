"""Unit tests for DMAModel tile-level double-buffering overlap ratio."""

import math

import pytest

from sim.models.dma import DMAModel


_BASELINE_CONFIG = {
    "dma": {
        "burst_size_bytes": 256,
        "descriptor_overhead_cycles": 5,
        "max_pending_descriptors": 16,
        "num_channels": 2,
        "per_channel_fifo_depth": 64,
        "max_burst_length": 8,
        "multi_block_mode": "linked_list",
        "ll_prefetch_en": True,
        "arbitration": "round_robin",
    },
    "memory": {
        "bandwidth_bytes_per_cycle": 51.2,
        "dram_efficiency": 0.85,
    },
}


def _make_dma(config_overrides=None):
    cfg = _BASELINE_CONFIG.copy()
    if config_overrides:
        import copy
        cfg = copy.deepcopy(cfg)
        for k, v in config_overrides.items():
            if isinstance(v, dict) and k in cfg:
                cfg[k].update(v)
            else:
                cfg[k] = v
    return DMAModel(cfg)


class TestEstimateTileDoubleBufferOverlap:
    """Test DMAModel.estimate_tile_double_buffer_overlap()."""

    # Default tile parameters (64×64 block engine, INT4×INT8)
    _TILE_H = 64
    _TILE_W = 64
    _W_BITS = 4
    _A_BITS = 8
    _PER_TILE_COMPUTE = 68  # H + broadcast_sync(2) + accumulate(2)

    def test_q_proj_decode_returns_valid_ratio(self):
        """Q_proj K=2560, N=4096 — PERF-09 representative config."""
        dma = _make_dma()
        ratio = dma.estimate_tile_double_buffer_overlap(
            M=1, K=2560, N=4096,
            tile_H=self._TILE_H, tile_W=self._TILE_W,
            weight_bits=self._W_BITS, act_bits=self._A_BITS,
            per_tile_compute_cycles=self._PER_TILE_COMPUTE,
        )
        assert isinstance(ratio, float)
        assert not math.isnan(ratio)
        assert not math.isinf(ratio)
        assert 0.0 <= ratio <= 1.0, f"ratio={ratio} not in [0, 1]"

    def test_decode_small_matmuls_return_valid_ratio(self):
        """All 7 transformer matmuls should return ratios in [0, 1]."""
        dma = _make_dma()
        matmuls = [
            (1, 2048, 2048),   # Q_proj
            (1, 2048, 2048),   # K_proj
            (1, 2048, 2048),   # V_proj
            (1, 2048, 2048),   # O_proj
            (1, 2048, 11008),  # FFN_gate
            (1, 2048, 11008),  # FFN_up
            (1, 11008, 2048),  # FFN_down
        ]
        for M, K, N in matmuls:
            ratio = dma.estimate_tile_double_buffer_overlap(
                M, K, N,
                tile_H=self._TILE_H, tile_W=self._TILE_W,
                weight_bits=self._W_BITS, act_bits=self._A_BITS,
                per_tile_compute_cycles=self._PER_TILE_COMPUTE,
            )
            assert 0.0 <= ratio <= 1.0, (
                f"M={M} K={K} N={N}: ratio={ratio} not in [0, 1]"
            )

    def test_zero_inputs_return_zero(self):
        """Edge case: M=0, K=0, N=0 returns 0.0 (sentinel)."""
        dma = _make_dma()
        ratio = dma.estimate_tile_double_buffer_overlap(
            M=0, K=0, N=0,
            tile_H=self._TILE_H, tile_W=self._TILE_W,
            weight_bits=self._W_BITS, act_bits=self._A_BITS,
            per_tile_compute_cycles=self._PER_TILE_COMPUTE,
        )
        assert ratio == 0.0

    def test_single_tile_returns_valid_ratio(self):
        """Single-tile matmul (K≤64, N≤64) — no overlap, cold start only."""
        dma = _make_dma()
        ratio = dma.estimate_tile_double_buffer_overlap(
            M=1, K=32, N=32,
            tile_H=self._TILE_H, tile_W=self._TILE_W,
            weight_bits=self._W_BITS, act_bits=self._A_BITS,
            per_tile_compute_cycles=self._PER_TILE_COMPUTE,
        )
        assert 0.0 <= ratio <= 1.0
        # Single tile has no double-buffer overlap (one K-tile, one N-tile)
        # DMA + compute are sequential — ratio should be 0.0 (no hiding)
        assert ratio == 0.0, f"Single tile: expected 0.0, got {ratio}"

    def test_multi_n_tile_overlap_positive(self):
        """Multi-N-tile matmul (K=64, N=256) — double-buffering within K-tile."""
        dma = _make_dma()
        # K=64 → K_tiles=1, N=256 → N_tiles=4
        # First N-tile cold, remaining 3 overlap
        ratio = dma.estimate_tile_double_buffer_overlap(
            M=1, K=64, N=256,
            tile_H=self._TILE_H, tile_W=self._TILE_W,
            weight_bits=self._W_BITS, act_bits=self._A_BITS,
            per_tile_compute_cycles=self._PER_TILE_COMPUTE,
        )
        assert 0.0 <= ratio <= 1.0
        # With 1 K-tile and 4 N-tiles: 3/4 tiles have overlap → ratio > 0
        assert ratio > 0.0, f"Multi-N-tile: expected > 0.0, got {ratio}"

    def test_ktile_reload_reduces_overlap(self):
        """K-tile reload stall: multiple K-tiles reduce overlap vs single K-tile."""
        dma = _make_dma()
        # K=256 → K_tiles=4, N=64 → N_tiles=1
        # Each K-tile has 1 N-tile → no within-K-tile overlap
        # K-tile reloads add stalls → ratio should be 0
        ratio = dma.estimate_tile_double_buffer_overlap(
            M=1, K=256, N=64,
            tile_H=self._TILE_H, tile_W=self._TILE_W,
            weight_bits=self._W_BITS, act_bits=self._A_BITS,
            per_tile_compute_cycles=self._PER_TILE_COMPUTE,
        )
        assert 0.0 <= ratio <= 1.0
        # With N_tiles=1 per K-tile, no double-buffering possible within K-tile
        # K-tile reload stalls add extra exposed DMA → ratio should be 0
        assert ratio == 0.0, f"K-tile reload: expected 0.0, got {ratio}"

    def test_no_dma_sentinel(self):
        """When eff_bw is huge, DMA→0, overlap should be close to 1."""
        dma = _make_dma({"memory": {"bandwidth_bytes_per_cycle": 1e12}})
        ratio = dma.estimate_tile_double_buffer_overlap(
            M=1, K=2560, N=4096,
            tile_H=self._TILE_H, tile_W=self._TILE_W,
            weight_bits=self._W_BITS, act_bits=self._A_BITS,
            per_tile_compute_cycles=self._PER_TILE_COMPUTE,
        )
        assert 0.0 <= ratio <= 1.0
        # DMA is negligible → nearly all hidden
        assert ratio >= 0.9, f"Infinite BW: expected ≥ 0.9, got {ratio}"

    def test_overlap_monotonic_with_bw(self):
        """Higher BW → more DMA hiding → higher overlap ratio."""
        dma_low = _make_dma({"memory": {"bandwidth_bytes_per_cycle": 10.0}})
        dma_high = _make_dma({"memory": {"bandwidth_bytes_per_cycle": 100.0}})
        args = dict(
            M=1, K=2560, N=1024,
            tile_H=64, tile_W=64,
            weight_bits=4, act_bits=8,
            per_tile_compute_cycles=68,
        )
        r_low = dma_low.estimate_tile_double_buffer_overlap(**args)
        r_high = dma_high.estimate_tile_double_buffer_overlap(**args)
        assert r_high >= r_low, (
            f"BW 100.0 ratio={r_high} should be ≥ BW 10.0 ratio={r_low}"
        )

    def test_prefill_multi_token_higher_overlap(self):
        """Prefill (M>1) has higher overlap than decode (M=1) because weight
        DMA (dominant) is amortized across more tokens in K-tile streaming,
        while per-token activation DMA is relatively small."""
        dma = _make_dma()
        r_decode = dma.estimate_tile_double_buffer_overlap(
            M=1, K=2560, N=4096,
            tile_H=self._TILE_H, tile_W=self._TILE_W,
            weight_bits=self._W_BITS, act_bits=self._A_BITS,
            per_tile_compute_cycles=self._PER_TILE_COMPUTE,
        )
        r_prefill = dma.estimate_tile_double_buffer_overlap(
            M=128, K=2560, N=4096,
            tile_H=self._TILE_H, tile_W=self._TILE_W,
            weight_bits=self._W_BITS, act_bits=self._A_BITS,
            per_tile_compute_cycles=self._PER_TILE_COMPUTE,
        )
        assert 0.0 <= r_decode <= 1.0
        assert 0.0 <= r_prefill <= 1.0
        assert r_prefill >= r_decode, (
            f"Prefill ratio={r_prefill} should be ≥ decode ratio={r_decode}"
        )
