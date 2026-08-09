"""RED TDD tests for MetricsCollector (Task 5).

These tests assert the API and formulas defined in the plan.
They will fail (RED) until MetricsCollector is implemented in Task 11.
"""

import pytest

from timing.metrics import MetricsCollector
from timing.types import RequestMetrics


class TestComputeTTFT:
    """compute_ttft: TTFT (ms) = (prefill_cycles + first_decode_cycles) / (freq_mhz * 1e3)."""

    def test_ttft_basic(self):
        collector = MetricsCollector()
        result = collector.compute_ttft(100_000_000, 10_000_000, 800)
        assert result == 137.5

    def test_ttft_prefill_only(self):
        """When first_decode_cycles is 0, TTFT = prefill time only."""
        collector = MetricsCollector()
        result = collector.compute_ttft(80_000_000, 0, 800)
        assert result == 100.0  # 80e6 / (800 * 1e3)


class TestComputeTPS:
    """compute_tps: TPS = output_tokens * freq_mhz * 1e6 / total_decode_cycles."""

    def test_tps_basic(self):
        collector = MetricsCollector()
        result = collector.compute_tps(32, 32_000_000, 800)
        assert result == 800.0

    def test_tps_larger_batch(self):
        collector = MetricsCollector()
        result = collector.compute_tps(128, 64_000_000, 800)
        assert result == 1600.0  # 128 * 800e6 / 64e6

    def test_zero_decode_no_divide_by_zero(self):
        """Zero output tokens and zero cycles must return 0.0, not crash."""
        collector = MetricsCollector()
        result = collector.compute_tps(0, 0, 800)
        assert result == 0.0

    def test_zero_cycles_nonzero_tokens(self):
        """Nonzero tokens but zero cycles should return 0.0."""
        collector = MetricsCollector()
        result = collector.compute_tps(32, 0, 800)
        assert result == 0.0


class TestComputeTPOT:
    """compute_tpot: TPOT (us) = mean(decode_cycles[1:]) / freq_mhz."""

    def test_tpot_single_token(self):
        """Single token → no inter-token pairs → return None."""
        collector = MetricsCollector()
        result = collector.compute_tpot([1_000_000], 800)
        assert result is None

    def test_tpot_multiple_tokens(self):
        collector = MetricsCollector()
        result = collector.compute_tpot([1_000_000, 2_000_000], 800)
        assert result == 2500.0  # 2_000_000 / 800

    def test_tpot_three_tokens(self):
        collector = MetricsCollector()
        # mean of [2_000_000, 4_000_000] = 3_000_000 / 800 = 3750
        result = collector.compute_tpot([1_000_000, 2_000_000, 4_000_000], 800)
        assert result == 3750.0

    def test_tpot_empty_list(self):
        """Empty decode list → return None."""
        collector = MetricsCollector()
        result = collector.compute_tpot([], 800)
        assert result is None


class TestComputeITL:
    """compute_itl: ITL (us) = [dec_cycles[i] / freq_mhz for i in 1..N-1]."""

    def test_itl_length(self):
        collector = MetricsCollector()
        result = collector.compute_itl([1_000_000, 2_000_000, 3_000_000], 800)
        assert len(result) == 2

    def test_itl_values(self):
        collector = MetricsCollector()
        result = collector.compute_itl([1_000_000, 2_000_000, 3_000_000], 800)
        assert result == [2500.0, 3750.0]  # 2e6/800, 3e6/800

    def test_itl_single_token(self):
        """Single token → empty list (no inter-token pairs)."""
        collector = MetricsCollector()
        result = collector.compute_itl([1_000_000], 800)
        assert result == []

    def test_itl_empty(self):
        collector = MetricsCollector()
        result = collector.compute_itl([], 800)
        assert result == []


class TestComputeModuleUtilization:
    """compute_module_utilization: per-module cycle fraction as percentage."""

    def test_module_utilization(self):
        collector = MetricsCollector()
        result = collector.compute_module_utilization(
            {"mxu": 600, "sfu": 200, "vector": 200}, 1000
        )
        assert sum(result.values()) == 100.0
        assert result["mxu"] == 60.0
        assert result["sfu"] == 20.0
        assert result["vector"] == 20.0

    def test_module_utilization_all_zeros(self):
        """All zero cycles → all utilizations are 0%."""
        collector = MetricsCollector()
        result = collector.compute_module_utilization(
            {"mxu": 0, "sfu": 0}, 1000
        )
        assert sum(result.values()) == 0.0

    def test_module_utilization_zero_total(self):
        """Zero total cycles → avoid divide-by-zero, return 0% for all."""
        collector = MetricsCollector()
        result = collector.compute_module_utilization(
            {"mxu": 100, "sfu": 50}, 0
        )
        assert all(v == 0.0 for v in result.values())


class TestCollect:
    """collect: produces a complete metrics dict from RequestMetrics."""

    def test_collect_populates_request_metrics(self):
        collector = MetricsCollector()
        rm = RequestMetrics(
            prompt_len=128,
            output_tokens=4,
            prefill_cycles=100_000_000,
            decode_cycles_per_token=[10_000_000, 12_000_000, 14_000_000, 16_000_000],
        )
        result = collector.collect(rm, 800)

        # TTFT: (100e6 + 10e6) / (800 * 1e3) = 137.5 ms → 137500.0 us
        assert "ttft_us" in result
        assert result["ttft_us"] == 137500.0

        # TPS: 4 * 800 * 1e6 / (10+12+14+16)e6 = 3200e6 / 52e6 ≈ 61.54
        assert "tps" in result
        assert result["tps"] == pytest.approx(61.53846153846154)

        # TPOT: mean([12e6, 14e6, 16e6]) / 800 = 14e6 / 800 = 17500.0 us
        assert "tpot_us" in result
        assert result["tpot_us"] == 17500.0

        # ITL: [12e6/800, 14e6/800, 16e6/800] = [15000.0, 17500.0, 20000.0]
        assert "itl_us_list" in result
        assert result["itl_us_list"] == [15000.0, 17500.0, 20000.0]

    def test_collect_single_output_token(self):
        """Single output token → TPOT is None, ITL is empty."""
        collector = MetricsCollector()
        rm = RequestMetrics(
            prompt_len=128,
            output_tokens=1,
            prefill_cycles=100_000_000,
            decode_cycles_per_token=[10_000_000],
        )
        result = collector.collect(rm, 800)
        assert result["tpot_us"] is None
        assert result["itl_us_list"] == []

    def test_collect_zero_decode_cycles(self):
        """Zero decode cycles → TPS is 0.0."""
        collector = MetricsCollector()
        rm = RequestMetrics(
            prompt_len=128,
            output_tokens=0,
            prefill_cycles=100_000_000,
            decode_cycles_per_token=[],
        )
        result = collector.collect(rm, 800)
        assert result["tps"] == 0.0
        assert result["tpot_us"] is None
        assert result["itl_us_list"] == []
