"""FM-1 cross-engine pipeline timing model tests."""

from engine.timeline import CoreTimeline, LayerBreakdown
from models.noc import NoCModel


class TestCoreTimelineOverheads:
    """CoreTimeline should track crossbar_wait, sram_stall, vcov_bubble."""

    def test_sfu_call_injects_engine_overhead(self):
        """add_sfu advances timeline by (op_cycles + engine_overhead)."""
        tl = CoreTimeline(core_id=0)
        before = tl.total_cycles
        tl.add_sfu("softmax", 100, layer=0)
        after = tl.total_cycles
        assert after >= before + 100, f"Expected >= {before+100}, got {after}"
        assert tl.total_crossbar_wait > 0
        assert tl.total_sram_stall > 0
        assert tl.total_vcov_bubble > 0

    def test_vector_call_injects_engine_overhead(self):
        """add_vector advances timeline by (op_cycles + engine_overhead)."""
        tl = CoreTimeline(core_id=0)
        before = tl.total_cycles
        tl.add_vector("add", 50, layer=0)
        after = tl.total_cycles
        assert after >= before + 50, f"Expected >= {before+50}, got {after}"
        assert tl.total_crossbar_wait > 0
        assert tl.total_sram_stall > 0
        assert tl.total_vcov_bubble > 0

    def test_snapshot_overheads_returns_tuple(self):
        """snapshot_overheads returns a 3-tuple of ints."""
        tl = CoreTimeline(core_id=0)
        snap = tl.snapshot_overheads()
        assert snap == (0, 0, 0), f"Expected (0,0,0), got {snap}"
        tl.add_sfu("layernorm", 50, layer=0)
        snap2 = tl.snapshot_overheads()
        assert snap2[0] > 0
        assert snap2[1] > 0
        assert snap2[2] > 0

    def test_mxu_call_has_no_overhead(self):
        """add_mxu does NOT inject engine overhead."""
        tl = CoreTimeline(core_id=0)
        snap_before = tl.snapshot_overheads()
        tl.add_mxu("Q_proj", 1000, layer=0)
        snap_after = tl.snapshot_overheads()
        assert snap_after == snap_before, (
            f"MXU should not inject engine overhead: {snap_before} -> {snap_after}"
        )

    def test_crossbar_wait_matches_constant(self):
        """Each SFU/Vector call adds exactly SAME_ENGINE_GAP_CROSSBAR."""
        tl = CoreTimeline(core_id=0)
        before = tl.total_crossbar_wait
        tl.add_sfu("op", 10, layer=0)
        assert tl.total_crossbar_wait == before + CoreTimeline.SAME_ENGINE_GAP_CROSSBAR

    def test_sram_stall_matches_constant(self):
        """Each SFU/Vector call adds exactly SAME_ENGINE_GAP_SRAM."""
        tl = CoreTimeline(core_id=0)
        before = tl.total_sram_stall
        tl.add_vector("add", 10, layer=0)
        assert tl.total_sram_stall == before + CoreTimeline.SAME_ENGINE_GAP_SRAM

    def test_vcov_bubble_matches_constant(self):
        """Each SFU/Vector call adds exactly SAME_ENGINE_GAP_VCOV."""
        tl = CoreTimeline(core_id=0)
        before = tl.total_vcov_bubble
        tl.add_sfu("gelu", 10, layer=0)
        assert tl.total_vcov_bubble == before + CoreTimeline.SAME_ENGINE_GAP_VCOV

    def test_same_engine_gap_total_is_4(self):
        """Same-engine gap matches Phase 5 P2 calibration (4 cycles)."""
        assert CoreTimeline.SAME_ENGINE_GAP_TOTAL == 4, (
            f"Expected 4, got {CoreTimeline.SAME_ENGINE_GAP_TOTAL}"
        )


class TestLayerBreakdownOverheads:
    """LayerBreakdown dataclass includes the three new overhead fields."""

    def test_layer_breakdown_defaults_are_zero(self):
        """New fields default to 0."""
        lb = LayerBreakdown(layer=0)
        assert lb.crossbar_wait == 0
        assert lb.sram_stall == 0
        assert lb.vcov_bubble == 0

    def test_layer_breakdown_overheads_settable(self):
        """Overheads can be set and read back."""
        lb = LayerBreakdown(layer=1, crossbar_wait=10, sram_stall=5, vcov_bubble=3)
        assert lb.crossbar_wait == 10
        assert lb.sram_stall == 5
        assert lb.vcov_bubble == 3


class TestNoCModelOverheads:
    """NoCModel analytical methods for cross-engine overhead estimation."""

    def test_crossbar_wait_cycles_returns_positive(self):
        """crossbar_wait_cycles returns positive value for M=6, S=2."""
        noc = NoCModel({"interconnect": {}})
        wait = noc.crossbar_wait_cycles(num_masters=6, num_slaves=2)
        assert wait > 0, f"Expected positive wait, got {wait}"

    def test_sram_stall_cycles_is_one(self):
        """sram_stall_cycles returns 1 (single cycle turnaround)."""
        noc = NoCModel({"interconnect": {}})
        stall = noc.sram_stall_cycles(num_banks=16)
        assert stall == 1, f"Expected 1, got {stall}"

    def test_vcov_bubble_cycles_returns_one(self):
        """vcov_bubble_cycles returns 1 (base bubble)."""
        noc = NoCModel({"interconnect": {}})
        bubble = noc.vcov_bubble_cycles(data_elements=128)
        assert bubble == 1, f"Expected 1, got {bubble}"

    def test_vcov_bubble_zero_elements_is_zero(self):
        """vcov_bubble_cycles with zero elements returns 0."""
        noc = NoCModel({"interconnect": {}})
        bubble = noc.vcov_bubble_cycles(data_elements=0)
        assert bubble == 0, f"Expected 0, got {bubble}"


class TestTimelineOverheadAccumulation:
    """Multiple engine calls accumulate overhead correctly."""

    def test_two_sfu_calls_accumulate_correctly(self):
        """Two SFU calls produce 2x the per-call overhead."""
        tl = CoreTimeline(core_id=0)
        tl.add_sfu("op1", 10, layer=0)
        tl.add_mxu("gemm", 100, layer=0)
        tl.add_sfu("op2", 10, layer=0)
        expected_cw = 2 * CoreTimeline.SAME_ENGINE_GAP_CROSSBAR
        expected_sr = 2 * CoreTimeline.SAME_ENGINE_GAP_SRAM
        expected_vb = 2 * CoreTimeline.SAME_ENGINE_GAP_VCOV
        assert tl.total_crossbar_wait == expected_cw
        assert tl.total_sram_stall == expected_sr
        assert tl.total_vcov_bubble == expected_vb

    def test_sfu_and_vector_mixed_accumulate(self):
        """One SFU + one Vector call accumulate correctly."""
        tl = CoreTimeline(core_id=0)
        tl.add_sfu("softmax", 10, layer=0)
        tl.add_vector("add", 10, layer=0)
        expected = CoreTimeline.SAME_ENGINE_GAP_CROSSBAR
        assert tl.total_crossbar_wait == 2 * expected
