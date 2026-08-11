"""T15: Timeline critical-path, overlap, contention, and dma_effective/dma_weight semantics.

Covers:
  - compute_critical_path_from_dag: serialized, overlap, contention DAGs
  - dma_effective / dma_weight semantics (inversion fix verification)
  - Dashboard wall_clock_critical_path normalization
  - Sum-vs-critical-path RED case
  - Zero-event / empty-DAG rejection
"""

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from engine.timeline import CoreTimeline, SimulationReport, TimelineEvent  # noqa: E402
from timing.dashboard import Dashboard  # noqa: E402
from timing.timing_engine import (  # noqa: E402
    _aggregate_events,
    compute_critical_path_from_dag,
)
from timing.types import RequestMetrics  # noqa: E402


# ── Helper: build a simple TimelineEvent ─────────────────────────────────

def _make_event(module, op, start, end, layer=0, overlapped=False):
    return TimelineEvent(
        module=module, op=op, start_cycle=start, end_cycle=end,
        layer=layer, overlapped=overlapped,
    )


# ── compute_critical_path_from_dag tests ─────────────────────────────────

class TestCriticalPathDag:
    """Topological critical-path computation from DAG nodes + edges."""

    def test_empty_dag_returns_zero(self):
        assert compute_critical_path_from_dag([], []) == 0

    def test_single_node_returns_its_cycles(self):
        nodes = [{"cycles": 42}]
        assert compute_critical_path_from_dag(nodes, []) == 42

    def test_serialized_chain(self):
        nodes = [{"cycles": 10}, {"cycles": 20}, {"cycles": 30}]
        edges = [(0, 1), (1, 2)]
        assert compute_critical_path_from_dag(nodes, edges) == 60

    def test_parallel_branches_pick_longest(self):
        nodes = [
            {"cycles": 10},  # 0: start
            {"cycles": 20},  # 1: branch A step 1
            {"cycles": 30},  # 2: branch B step 1 (longer)
            {"cycles": 5},   # 3: branch A step 2
            {"cycles": 10},  # 4: end (merge)
        ]
        edges = [(0, 1), (0, 2), (1, 3), (2, 4), (3, 4)]
        expected = max(10 + 20 + 5 + 10, 10 + 30 + 10)
        assert compute_critical_path_from_dag(nodes, edges) == expected

    def test_overlap_dag_dma_parallel_sfu(self):
        nodes = [
            {"cycles": 30},  # 0: MXU matmul (advances wall clock)
            {"cycles": 25},  # 1: DMA (independent parallel, shorter than compute)
            {"cycles": 10},  # 2: SFU (depends on MXU)
            {"cycles": 0},   # 3: end marker
        ]
        edges = [(0, 2), (2, 3), (1, 3)]
        cpath = compute_critical_path_from_dag(nodes, edges)
        sum_breakdown = sum(n["cycles"] for n in nodes)
        assert cpath == 40, f"Critical path should be MXU(30)+SFU(10)=40, got {cpath}"
        assert cpath < sum_breakdown, f"Overlap means cpath={cpath} < sum={sum_breakdown}"

    def test_contention_dag(self):
        nodes = [
            {"cycles": 20},  # 0: MXU0
            {"cycles": 20},  # 1: MXU1 (contends for crossbar)
            {"cycles": 10},  # 2: SFU (depends on both MXU ops)
        ]
        edges = [(0, 2), (1, 2)]
        cpath = compute_critical_path_from_dag(nodes, edges)
        assert cpath == 30, f"Contention: longest path MXU(20)+SFU(10)=30, got {cpath}"

    def test_sum_vs_critical_path_red_case(self):
        nodes = [
            {"cycles": 30},  # 0: MXU matmul
            {"cycles": 20},  # 1: SFU (depends on MXU)
            {"cycles": 20},  # 2: Vector (depends on SFU)
            {"cycles": 30},  # 3: DMA (independent parallel, shorter than compute)
            {"cycles": 0},   # 4: end marker
        ]
        edges = [(0, 1), (1, 2), (2, 4), (3, 4)]
        cpath = compute_critical_path_from_dag(nodes, edges)
        sum_breakdown = sum(n["cycles"] for n in nodes)
        assert sum_breakdown == 100
        assert cpath == 70, (
            f"RED case: sum-of-breakdowns=100, critical-path=70. "
            f"Got cpath={cpath}. Old sum logic would say 100, new logic says 70."
        )

    def test_disconnected_components_pick_longest(self):
        nodes = [{"cycles": 15}, {"cycles": 25}]
        cpath = compute_critical_path_from_dag(nodes, [])
        assert cpath == 25

    def test_cycle_detection_raises(self):
        nodes = [{"cycles": 10}, {"cycles": 20}]
        edges = [(0, 1), (1, 0)]
        with pytest.raises(ValueError, match="Cycle detected"):
            compute_critical_path_from_dag(nodes, edges)

    def test_out_of_range_edge_raises(self):
        nodes = [{"cycles": 10}]
        with pytest.raises(ValueError, match="out of range"):
            compute_critical_path_from_dag(nodes, [(0, 99)])

    def test_zero_cycle_nodes(self):
        nodes = [{"cycles": 0}, {"cycles": 40}, {"cycles": 0}]
        edges = [(0, 1), (1, 2)]
        assert compute_critical_path_from_dag(nodes, edges) == 40


# ── dma_effective / dma_weight semantics ─────────────────────────────────

class TestDmaEffectiveWeightSemantics:
    """Verify dma_weight=hidden/overlapped, dma_effective=exposed/stall."""

    def test_overlapped_dma_goes_to_weight(self):
        events = [
            _make_event("mxu", "mmul", 0, 100, layer=0),
            _make_event("dma", "dma_weights", 0, 80, layer=0, overlapped=True),
        ]
        mb = _aggregate_events(SimulationReport(
            model_name="test", num_layers=1, events=events,
        ))
        assert mb.cycles["dma_weight"] == 80, (
            f"Overlapped DMA should go to dma_weight (hidden), got dma_weight={mb.cycles['dma_weight']}"
        )
        assert mb.cycles["dma_effective"] == 0, (
            f"Overlapped DMA should leave dma_effective=0, got {mb.cycles['dma_effective']}"
        )

    def test_non_overlapped_dma_goes_to_effective(self):
        events = [
            _make_event("mxu", "mmul", 0, 60, layer=0),
            _make_event("dma", "dma_weights", 0, 100, layer=0, overlapped=False),
        ]
        mb = _aggregate_events(SimulationReport(
            model_name="test", num_layers=1, events=events,
        ))
        assert mb.cycles["dma_effective"] == 100, (
            f"Non-overlapped DMA should go to dma_effective (stall), got {mb.cycles['dma_effective']}"
        )
        assert mb.cycles["dma_weight"] == 0, (
            f"Non-overlapped DMA should leave dma_weight=0, got {mb.cycles['dma_weight']}"
        )

    def test_mixed_dma_events(self):
        events = [
            _make_event("mxu", "mmul", 0, 100, layer=0),
            _make_event("dma", "dma_hidden", 0, 80, layer=0, overlapped=True),
            _make_event("dma", "dma_stall", 100, 150, layer=0, overlapped=False),
        ]
        mb = _aggregate_events(SimulationReport(
            model_name="test", num_layers=1, events=events,
        ))
        assert mb.cycles["dma_weight"] == 80
        assert mb.cycles["dma_effective"] == 50

    def test_noc_overlapped_goes_to_latency(self):
        events = [
            _make_event("mxu", "mmul", 0, 200, layer=0),
            _make_event("noc", "route", 0, 30, layer=0, overlapped=True),
        ]
        mb = _aggregate_events(SimulationReport(
            model_name="test", num_layers=1, events=events,
        ))
        assert mb.cycles["noc_latency"] == 30
        assert mb.cycles["noc_contention"] == 0

    def test_noc_non_overlapped_goes_to_contention(self):
        events = [
            _make_event("mxu", "mmul", 0, 30, layer=0),
            _make_event("noc", "route", 0, 50, layer=0, overlapped=False),
        ]
        mb = _aggregate_events(SimulationReport(
            model_name="test", num_layers=1, events=events,
        ))
        assert mb.cycles["noc_contention"] == 50
        assert mb.cycles["noc_latency"] == 0


# ── Dashboard wall-clock normalization ───────────────────────────────────

class TestDashboardWallClockNormalization:
    """Dashboard uses wall_clock_critical_path for total_cycles and percentages."""

    def test_wall_clock_critical_path_used_when_provided(self):
        mb = {"mxu": 600, "sfu": 200, "vector": 100, "dma_weight": 80, "dma_effective": 60}
        result = Dashboard.generate_json(
            model_name="test",
            request_metrics=RequestMetrics(),
            module_breakdown=mb,
            freq_mhz=1000,
            is_cv=False,
            wall_clock_critical_path=900,
        )
        assert result["total_cycles"] == 900, (
            f"wall_clock_critical_path=900 should be used, got {result['total_cycles']}"
        )

    def test_fallback_to_sum_when_wall_clock_zero(self):
        mb = {"mxu": 600, "sfu": 200, "vector": 100}
        result = Dashboard.generate_json(
            model_name="test",
            request_metrics=RequestMetrics(),
            module_breakdown=mb,
            freq_mhz=1000,
            is_cv=False,
            wall_clock_critical_path=0,
        )
        assert result["total_cycles"] == 900, (
            f"Should fall back to sum=900, got {result['total_cycles']}"
        )

    def test_utilization_normalized_to_wall_clock(self):
        mb = {"mxu": 600, "sfu": 200, "vector": 100, "dma_weight": 800, "dma_effective": 100}
        result = Dashboard.generate_json(
            model_name="test",
            request_metrics=RequestMetrics(),
            module_breakdown=mb,
            freq_mhz=1000,
            is_cv=False,
            wall_clock_critical_path=900,
        )
        mu = result["module_utilization_pct"]
        assert abs(mu["mxu"] - 600 / 900 * 100) < 0.01
        assert abs(mu["sfu"] - 200 / 900 * 100) < 0.01
        assert abs(mu["vector"] - 100 / 900 * 100) < 0.01

    def test_bandwidth_utilization_normalized_to_wall_clock(self):
        mb = {"mxu": 600, "dma_weight": 800, "dma_effective": 100}
        result = Dashboard.generate_json(
            model_name="test",
            request_metrics=RequestMetrics(),
            module_breakdown=mb,
            freq_mhz=1000,
            is_cv=False,
            wall_clock_critical_path=600,
        )
        bw = result["bandwidth_utilization_pct"]
        assert abs(bw - (900 / 600 * 100)) < 0.01

    def test_noc_contention_normalized_to_wall_clock(self):
        mb = {"mxu": 400, "noc_contention": 200, "noc_latency": 100}
        result = Dashboard.generate_json(
            model_name="test",
            request_metrics=RequestMetrics(),
            module_breakdown=mb,
            freq_mhz=1000,
            is_cv=False,
            wall_clock_critical_path=400,
        )
        assert abs(result["noc_contention_pct"] - (200 / 400 * 100)) < 0.01


# ── CoreTimeline critical-path flow ──────────────────────────────────────

class TestCoreTimelineCriticalPath:
    """CoreTimeline.total_cycles matches wall-clock critical path."""

    def test_simple_serial_events(self):
        tl = CoreTimeline(core_id=0)
        tl.add_mxu("mmul", 100, 0)
        tl.add_sfu("softmax", 50, 0)
        assert tl.total_cycles == 150 + CoreTimeline.SAME_ENGINE_GAP_TOTAL

    def test_parallel_dma_does_not_advance_wall_clock(self):
        tl = CoreTimeline(core_id=0)
        tl.add_mxu("mmul", 100, 0)
        mxu_end = tl._current_cycle
        tl.add_dma_parallel("dma_hidden", 80, 0)
        assert tl._current_cycle == 180, "DMA scheduled after MXU advances clock to MXU_end + DMA"
        tl._current_cycle = mxu_end
        assert tl._current_cycle == 100, "npu_sim manually restores _current_cycle after DMA"

    def test_dma_beyond_mxu_advances_wall_clock(self):
        tl = CoreTimeline(core_id=0)
        tl.add_mxu("mmul", 60, 0)
        tl.add_dma_parallel("dma_stall", 100, 0)
        assert tl._current_cycle == 160, "DMA(100) beyond MXU(60) = 60+100=160"


# ── Wall_clock_critical_path in SimulationReport ─────────────────────────

class TestSimulationReportCriticalPath:
    """SimulationReport carries wall_clock_critical_path."""

    def test_report_has_field(self):
        report = SimulationReport(model_name="test", num_layers=1)
        assert hasattr(report, "wall_clock_critical_path")
        assert report.wall_clock_critical_path == 0

    def test_report_can_set_critical_path(self):
        report = SimulationReport(
            model_name="test", num_layers=1, wall_clock_critical_path=12345,
        )
        assert report.wall_clock_critical_path == 12345


# ── Zero-event / empty rejection ────────────────────────────────────────

class TestZeroEventRejection:
    """Empty event list should produce zero cycles, not crash."""

    def test_empty_events_in_report(self):
        report = SimulationReport(model_name="test", num_layers=1, events=[])
        mb = _aggregate_events(report)
        assert sum(mb.cycles.values()) == 0

    def test_empty_dag_produces_zero_cpath(self):
        assert compute_critical_path_from_dag([], []) == 0

    def test_events_with_zero_cycles(self):
        events = [
            _make_event("mxu", "nop", 0, 0, layer=0),
            _make_event("dma", "nop", 10, 10, layer=0, overlapped=True),
        ]
        mb = _aggregate_events(SimulationReport(
            model_name="test", num_layers=1, events=events,
        ))
        assert sum(abs(v) for v in mb.cycles.values()) == 0
