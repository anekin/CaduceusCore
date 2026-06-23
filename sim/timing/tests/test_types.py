"""Tests for sim/timing/types.py dataclasses and SimulationReport extension."""

from sim.engine.timeline import SimulationReport
from sim.timing.types import ModuleBreakdown, TokenTiming, RequestMetrics


class TestSimulationReportExtension:
    """Verify new timing fields have safe defaults on SimulationReport."""

    def test_default_ttft_ms(self):
        r = SimulationReport("test", 1)
        assert r.ttft_ms == 0.0

    def test_default_decode_total_us(self):
        r = SimulationReport("test", 1)
        assert r.decode_total_us == 0.0

    def test_default_module_breakdowns(self):
        r = SimulationReport("test", 1)
        assert r.module_breakdowns == {}

    def test_default_tps(self):
        r = SimulationReport("test", 1)
        assert r.tps == 0.0

    def test_default_tpot_us(self):
        r = SimulationReport("test", 1)
        assert r.tpot_us == 0.0

    def test_default_itl_us_list(self):
        r = SimulationReport("test", 1)
        assert r.itl_us_list == []

    def test_constructor_preserves_existing_fields(self):
        r = SimulationReport("qwen", 28, array_height=128, array_width=128)
        assert r.model_name == "qwen"
        assert r.num_layers == 28
        assert r.array_height == 128
        assert r.array_width == 128

    def test_set_new_fields(self):
        r = SimulationReport("test", 1)
        r.ttft_ms = 42.5
        r.decode_total_us = 100.0
        r.module_breakdowns = {"mxu": 1000}
        r.tps = 25.0
        r.tpot_us = 50.0
        r.itl_us_list = [10.0, 12.0]
        assert r.ttft_ms == 42.5
        assert r.decode_total_us == 100.0
        assert r.module_breakdowns == {"mxu": 1000}
        assert r.tps == 25.0
        assert r.tpot_us == 50.0
        assert r.itl_us_list == [10.0, 12.0]


class TestModuleBreakdown:
    """Verify ModuleBreakdown dataclass."""

    def test_default_cycles(self):
        mb = ModuleBreakdown()
        assert set(mb.cycles.keys()) == {
            "mxu", "sfu", "vector", "dma_weight", "dma_effective", "kv_cache",
            "noc_latency", "noc_contention"
        }
        assert all(v == 0 for v in mb.cycles.values())

    def test_custom_cycles(self):
        mb = ModuleBreakdown(cycles={"mxu": 500, "sfu": 100, "vector": 50,
                                     "dma_weight": 200, "dma_effective": 150,
                                     "kv_cache": 80})
        assert mb.cycles["mxu"] == 500
        assert mb.cycles["kv_cache"] == 80


class TestTokenTiming:
    """Verify TokenTiming dataclass."""

    def test_defaults(self):
        tt = TokenTiming(token_idx=0, phase="prefill")
        assert tt.token_idx == 0
        assert tt.phase == "prefill"
        assert tt.total_cycles == 0
        assert isinstance(tt.module_breakdown, ModuleBreakdown)

    def test_decode_phase(self):
        tt = TokenTiming(token_idx=1, phase="decode", total_cycles=1000)
        assert tt.phase == "decode"
        assert tt.total_cycles == 1000


class TestRequestMetrics:
    """Verify RequestMetrics dataclass."""

    def test_defaults(self):
        rm = RequestMetrics()
        assert rm.prompt_len == 0
        assert rm.output_tokens == 0
        assert rm.prefill_cycles == 0
        assert rm.decode_cycles_per_token == []
        assert rm.ttft_us == 0.0
        assert rm.tps == 0.0
        assert rm.tpot_us == 0.0
        assert rm.itl_us_list == []

    def test_custom_values(self):
        rm = RequestMetrics(
            prompt_len=128,
            output_tokens=32,
            prefill_cycles=100_000_000,
            decode_cycles_per_token=[1_000_000] * 32,
            ttft_us=125_000.0,
            tps=800.0,
            tpot_us=1250.0,
            itl_us_list=[1200.0, 1300.0],
        )
        assert rm.prompt_len == 128
        assert rm.output_tokens == 32
        assert rm.tps == 800.0
        assert len(rm.itl_us_list) == 2
