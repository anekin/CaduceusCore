"""RED TDD tests for TimingEngine (Task 4 — intentionally failing until Task 11)."""

from model_specs import get_spec
from timing.types import TokenTiming, RequestMetrics
from timing.timing_engine import TimingEngine


class TestTimingEngineConstruction:
    """TimingEngine should load NPU config without error."""

    def test_create_engine_loads_config(self):
        """TimingEngine('sim/config/npu_config.yaml') instantiates without error."""
        engine = TimingEngine("sim/config/npu_config.yaml")
        assert engine is not None, "TimingEngine should return a non-None instance"


class TestTimingEnginePrefill:
    """simulate_prefill should produce a valid TokenTiming with prefill phase."""

    def test_simulate_prefill_returns_token_timing(self):
        """simulate_prefill returns TokenTiming with phase='prefill' and total_cycles > 0."""
        engine = TimingEngine("sim/config/npu_config.yaml")
        spec = get_spec("qwen2.5-1.5b")
        timing = engine.simulate_prefill(spec, prompt_len=128)
        assert isinstance(timing, TokenTiming), (
            f"Expected TokenTiming, got {type(timing).__name__}"
        )
        assert timing.phase == "prefill", (
            f"Expected phase='prefill', got '{timing.phase}'"
        )
        assert timing.total_cycles > 0, (
            f"Expected total_cycles > 0, got {timing.total_cycles}"
        )


class TestTimingEngineDecode:
    """simulate_decode should produce a valid TokenTiming with decode phase."""

    def test_simulate_decode_returns_token_timing(self):
        """simulate_decode returns TokenTiming with phase='decode' and total_cycles > 0."""
        engine = TimingEngine("sim/config/npu_config.yaml")
        spec = get_spec("qwen2.5-1.5b")
        timing = engine.simulate_decode(spec)
        assert isinstance(timing, TokenTiming), (
            f"Expected TokenTiming, got {type(timing).__name__}"
        )
        assert timing.phase == "decode", (
            f"Expected phase='decode', got '{timing.phase}'"
        )
        assert timing.total_cycles > 0, (
            f"Expected total_cycles > 0, got {timing.total_cycles}"
        )


class TestModuleBreakdown:
    """Module breakdown should contain all 6 expected keys."""

    def test_module_breakdown_keys_present(self):
        """All 8 module keys present in TokenTiming.module_breakdown.cycles."""
        engine = TimingEngine("sim/config/npu_config.yaml")
        spec = get_spec("qwen2.5-1.5b")
        timing = engine.simulate_decode(spec)
        keys = set(timing.module_breakdown.cycles.keys())
        expected = {"mxu", "sfu", "vector", "dma_weight", "dma_effective", "kv_cache",
                     "noc_latency", "noc_contention",
                     "crossbar_wait", "sram_stall", "vcov_bubble"}
        missing = expected - keys
        assert keys == expected, (
            f"Module breakdown keys mismatch. Missing: {missing}. "
            f"Got: {sorted(keys)}"
        )

    def test_total_equals_wall_clock_sum(self):
        """total_cycles equals the wall-clock-advancing module sum.

        Only modules that actually advance the timeline are counted in total_cycles:
        mxu + sfu + vector + crossbar_wait + sram_stall + vcov_bubble
        + all kv events (including DRAM refresh).  DMA and NoC
        cycle counts overlap with compute and are excluded from the wall clock.
        """
        engine = TimingEngine("sim/config/npu_config.yaml")
        spec = get_spec("qwen2.5-1.5b")
        timing = engine.simulate_decode(spec)
        wall_modules = ("mxu", "sfu", "vector", "kv_cache",
                        "crossbar_wait", "sram_stall", "vcov_bubble")
        wall_sum = sum(timing.module_breakdown.cycles.get(k, 0) for k in wall_modules)
        total = timing.total_cycles
        abs_diff = abs(total - wall_sum)
        max_cycles = max(total, wall_sum)
        rel_error = abs_diff / max_cycles if max_cycles > 0 else 0.0
        # DRAM refresh (global kv event at layer=-1) is not present in the
        # per-layer kv_cache aggregate.  Allow its proportional contribution.
        assert rel_error <= 0.10, (
            f"total_cycles ({total}) vs wall-clock sum ({wall_sum}) "
            f"differs by {rel_error*100:.1f}% (> 10%). "
            f"Expected difference ≤ DRAM refresh fraction (~5%)."
        )


class TestRequestMetrics:
    """simulate_request should produce valid RequestMetrics."""

    def test_simulate_request_returns_request_metrics(self):
        """simulate_request returns RequestMetrics with decode_cycles_per_token length 32."""
        engine = TimingEngine("sim/config/npu_config.yaml")
        spec = get_spec("qwen2.5-1.5b")
        metrics = engine.simulate_request(spec, prompt_len=128, gen_len=32)
        assert isinstance(metrics, RequestMetrics), (
            f"Expected RequestMetrics, got {type(metrics).__name__}"
        )
        assert len(metrics.decode_cycles_per_token) == 32, (
            f"Expected 32 decode cycles, got {len(metrics.decode_cycles_per_token)}"
        )
        assert all(c > 0 for c in metrics.decode_cycles_per_token), (
            "All decode cycle entries should be > 0"
        )
