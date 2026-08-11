"""Tests for sim/timing/perf_contract.py — typed contract schemas and validation."""

import json
import math
import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from timing.perf_contract import (  # noqa: E402
    BasisType,
    CalibrationState,
    DiagnosticsEntry,
    DomainType,
    EngineType,
    EventKind,
    EventPairValidator,
    OpType,
    PerfArtifact,
    PerfBand,
    PerfEstimate,
    PerfEvent,
    PerfReport,
    UnitType,
    negotiate_version,
    validate_fixture,
)

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "config" / "tests"


# ── PerfEvent tests ──────────────────────────────────────────────────────────


class TestPerfEvent:
    """Valid construction and rejection of semantic performance events."""

    def test_valid_event(self) -> None:
        ev = PerfEvent(
            event_id="ev-001",
            kind=EventKind.COMMAND_ACCEPTED,
            seq_id=1,
            parent_workload_id="wl-qwen-blk0",
            engine=EngineType.MXU,
            op=OpType.MMUL,
            programmed_shape={"M": 64, "K": 64, "N": 64},
        )
        assert ev.engine == EngineType.MXU
        assert ev.op == OpType.MMUL
        assert ev.seq_id == 1

    def test_event_round_trip_json(self) -> None:
        ev = PerfEvent(
            event_id="ev-r",
            kind=EventKind.COMMAND_COMPLETED,
            seq_id=42,
            parent_workload_id="wl-test",
            engine=EngineType.SFU,
            op=OpType.SOFTMAX,
            programmed_shape={"elements": 128},
        )
        data = ev.model_dump_json()
        ev2 = PerfEvent.model_validate_json(data)
        assert ev2.event_id == ev.event_id
        assert ev2.kind == ev.kind
        assert ev2.programmed_shape == ev.programmed_shape

    @pytest.mark.parametrize(
        "engine,op,shape",
        [
            (EngineType.MXU, OpType.MMUL, {"M": 64, "K": 64, "N": 64}),
            (EngineType.SFU, OpType.SOFTMAX, {"elements": 128}),
            (EngineType.VECTOR, OpType.ADD, {"dim": 256}),
            (EngineType.DMA, OpType.DMA_COPY, {"bytes": 4096}),
            (EngineType.DRAM, OpType.DRAM_READ, {"bytes": 65536, "rw": 0}),
            (EngineType.KV_CACHE, OpType.KV_ACCESS, {"token_pos": 1, "sram_kb": 256}),
            (EngineType.NOC, OpType.NOC_ROUTE, {"bytes": 64, "topology": 0, "route": 0}),
            (EngineType.RISC_V, OpType.RISC_V_INSTR, {"instructions": 100}),
        ],
    )
    def test_all_engine_shape_combos(self, engine, op, shape) -> None:
        ev = PerfEvent(
            event_id=f"ev-{engine.value}",
            kind=EventKind.COMMAND_ACCEPTED,
            seq_id=1,
            parent_workload_id="wl",
            engine=engine,
            op=op,
            programmed_shape=shape,
        )
        assert ev.engine == engine

    def test_unknown_engine_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerfEvent(
                event_id="ev-bad",
                kind=EventKind.COMMAND_ACCEPTED,
                seq_id=1,
                parent_workload_id="wl",
                engine="gpu",  # type: ignore[arg-type]
                op=OpType.MMUL,
                programmed_shape={"M": 64, "K": 64, "N": 64},
            )

    def test_unknown_op_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerfEvent(
                event_id="ev-bad",
                kind=EventKind.COMMAND_ACCEPTED,
                seq_id=1,
                parent_workload_id="wl",
                engine=EngineType.MXU,
                op="conv2d",  # type: ignore[arg-type]
                programmed_shape={"M": 64, "K": 64, "N": 64},
            )

    def test_zero_shape_value_allowed(self) -> None:
        """Zero shape values are valid at the schema level (e.g. KV token_pos=0, DRAM rw=0)."""
        ev = PerfEvent(
            event_id="ev-zero-ok",
            kind=EventKind.COMMAND_ACCEPTED,
            seq_id=1,
            parent_workload_id="wl",
            engine=EngineType.KV_CACHE,
            op=OpType.KV_ACCESS,
            programmed_shape={"token_pos": 0, "sram_kb": 64},
        )
        assert ev.programmed_shape["token_pos"] == 0

    def test_negative_shape_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerfEvent(
                event_id="ev-neg",
                kind=EventKind.COMMAND_ACCEPTED,
                seq_id=1,
                parent_workload_id="wl",
                engine=EngineType.MXU,
                op=OpType.MMUL,
                programmed_shape={"M": -1, "K": 64, "N": 64},
            )

    def test_seq_id_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerfEvent(
                event_id="ev-zero",
                kind=EventKind.COMMAND_ACCEPTED,
                seq_id=0,
                parent_workload_id="wl",
                engine=EngineType.MXU,
                op=OpType.MMUL,
                programmed_shape={"M": 64, "K": 64, "N": 64},
            )

    def test_seq_id_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerfEvent(
                event_id="ev-neg",
                kind=EventKind.COMMAND_ACCEPTED,
                seq_id=-5,
                parent_workload_id="wl",
                engine=EngineType.MXU,
                op=OpType.MMUL,
                programmed_shape={"M": 64, "K": 64, "N": 64},
            )

    def test_empty_workload_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerfEvent(
                event_id="ev",
                kind=EventKind.COMMAND_ACCEPTED,
                seq_id=1,
                parent_workload_id="",
                engine=EngineType.MXU,
                op=OpType.MMUL,
                programmed_shape={"M": 64, "K": 64, "N": 64},
            )

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerfEvent.model_validate({
                "event_id": "ev",
                "kind": "command_accepted",
                "seq_id": 1,
                "parent_workload_id": "wl",
                "engine": "mxu",
                "op": "mmul",
                "programmed_shape": {"M": 64, "K": 64, "N": 64},
                "extra_junk": True,
            })

    def test_wrong_shape_keys_for_engine(self) -> None:
        with pytest.raises(ValidationError):
            PerfEvent(
                event_id="ev-wrong",
                kind=EventKind.COMMAND_ACCEPTED,
                seq_id=1,
                parent_workload_id="wl",
                engine=EngineType.MXU,
                op=OpType.MMUL,
                programmed_shape={"X": 1, "Y": 2},
            )


# ── EventPairValidator tests ─────────────────────────────────────────────────


class TestEventPairValidator:
    def test_clean_pair(self) -> None:
        v = EventPairValidator()
        violations = v.validate_all([
            PerfEvent(
                event_id="e1", kind=EventKind.COMMAND_ACCEPTED, seq_id=1,
                parent_workload_id="wl", engine=EngineType.MXU, op=OpType.MMUL,
                programmed_shape={"M": 64, "K": 64, "N": 64},
            ),
            PerfEvent(
                event_id="e2", kind=EventKind.COMMAND_COMPLETED, seq_id=1,
                parent_workload_id="wl", engine=EngineType.MXU, op=OpType.MMUL,
                programmed_shape={"M": 64, "K": 64, "N": 64},
            ),
        ])
        assert len(violations) == 0

    def test_duplicate_event_id(self) -> None:
        v = EventPairValidator()
        base = PerfEvent(
            event_id="dup", kind=EventKind.COMMAND_ACCEPTED, seq_id=1,
            parent_workload_id="wl", engine=EngineType.MXU, op=OpType.MMUL,
            programmed_shape={"M": 64, "K": 64, "N": 64},
        )
        v.add(base)
        violations = v.add(PerfEvent(
            event_id="dup", kind=EventKind.COMMAND_COMPLETED, seq_id=1,
            parent_workload_id="wl", engine=EngineType.MXU, op=OpType.MMUL,
            programmed_shape={"M": 64, "K": 64, "N": 64},
        ))
        assert len(violations) == 1
        assert "Duplicate" in violations[0]

    def test_missing_completion(self) -> None:
        v = EventPairValidator()
        v.add(PerfEvent(
            event_id="e1", kind=EventKind.COMMAND_ACCEPTED, seq_id=10,
            parent_workload_id="wl", engine=EngineType.MXU, op=OpType.MMUL,
            programmed_shape={"M": 64, "K": 64, "N": 64},
        ))
        violations = v.check_pairs()
        assert len(violations) == 1
        assert "Missing completion" in violations[0]

    def test_completion_without_acceptance(self) -> None:
        v = EventPairValidator()
        v.add(PerfEvent(
            event_id="e1", kind=EventKind.COMMAND_COMPLETED, seq_id=20,
            parent_workload_id="wl", engine=EngineType.MXU, op=OpType.MMUL,
            programmed_shape={"M": 64, "K": 64, "N": 64},
        ))
        violations = v.check_pairs()
        assert len(violations) == 1
        assert "Completion without acceptance" in violations[0]


# ── PerfEstimate tests ───────────────────────────────────────────────────────


class TestPerfEstimate:
    def test_valid_estimate(self) -> None:
        pe = PerfEstimate(
            provider_id="spec-block64-v1",
            provider_version="1.0.0",
            domain=DomainType.MXU,
            boundary_id="mxu-64x64x64",
            engine=EngineType.MXU,
            op=OpType.MMUL,
            shape={"M": 64, "K": 64, "N": 64},
            estimated_cycles=32000,
            uncertainty_pct=10.0,
            spec_hash="abc123",
        )
        assert pe.estimated_cycles == 32000
        assert pe.is_verdict_eligible()

    def test_round_trip(self) -> None:
        pe = PerfEstimate(
            provider_id="spec-block64-v1",
            provider_version="1.0.0",
            domain=DomainType.MXU,
            boundary_id="mxu-64x64x64",
            engine=EngineType.MXU,
            op=OpType.MMUL,
            shape={"M": 64, "K": 64, "N": 64},
            estimated_cycles=32000,
            uncertainty_pct=10.0,
            spec_hash="abc123",
        )
        pe2 = pe.round_trip()
        assert pe2.estimated_cycles == pe.estimated_cycles
        assert pe2.domain == pe.domain
        assert pe2.content_hash() == pe.content_hash()

    def test_content_hash_stable(self) -> None:
        pe = PerfEstimate(
            provider_id="spec-block64-v1",
            provider_version="1.0.0",
            domain=DomainType.MXU,
            boundary_id="mxu-64x64x64",
            engine=EngineType.MXU,
            op=OpType.MMUL,
            shape={"M": 64, "K": 64, "N": 64},
            estimated_cycles=32000,
            uncertainty_pct=10.0,
            spec_hash="abc123",
        )
        h1 = pe.content_hash()
        h2 = pe.content_hash()
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex digest

    def test_content_hash_excludes_rtl_fields(self) -> None:
        """RTL metadata fields must not affect the canonical content hash."""
        pe_base = PerfEstimate(
            provider_id="spec-block64-v1",
            provider_version="1.0.0",
            domain=DomainType.MXU,
            boundary_id="mxu-64x64x64",
            engine=EngineType.MXU,
            op=OpType.MMUL,
            shape={"M": 64, "K": 64, "N": 64},
            estimated_cycles=32000,
            uncertainty_pct=10.0,
            spec_hash="abc123",
        )
        pe_rtl = PerfEstimate(
            provider_id="spec-block64-v1",
            provider_version="1.0.0",
            domain=DomainType.MXU,
            boundary_id="mxu-64x64x64",
            engine=EngineType.MXU,
            op=OpType.MMUL,
            shape={"M": 64, "K": 64, "N": 64},
            estimated_cycles=32000,
            uncertainty_pct=10.0,
            spec_hash="abc123",
            rtl_head="deadbeef",
            eda_version="2024.1",
            testbench_hash="cafe",
        )
        assert pe_base.content_hash() == pe_rtl.content_hash()

    def test_measured_cycles_rejected(self) -> None:
        """PerfEstimate with extra="forbid" rejects measured_cycles."""
        with pytest.raises(ValidationError):
            PerfEstimate.model_validate({
                "provider_id": "p",
                "provider_version": "1.0.0",
                "domain": "mxu",
                "boundary_id": "b",
                "engine": "mxu",
                "op": "mmul",
                "shape": {"M": 64, "K": 64, "N": 64},
                "estimated_cycles": 1000,
                "measured_cycles": 999,
                "uncertainty_pct": 10.0,
                "spec_hash": "abc",
            })

    def test_zero_estimated_cycles_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerfEstimate(
                provider_id="p", provider_version="1.0.0",
                domain=DomainType.MXU, boundary_id="b",
                engine=EngineType.MXU, op=OpType.MMUL,
                shape={"M": 64, "K": 64, "N": 64},
                estimated_cycles=0,
                uncertainty_pct=10.0,
                spec_hash="abc",
            )

    def test_nan_uncertainty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerfEstimate(
                provider_id="p", provider_version="1.0.0",
                domain=DomainType.MXU, boundary_id="b",
                engine=EngineType.MXU, op=OpType.MMUL,
                shape={"M": 64, "K": 64, "N": 64},
                estimated_cycles=1000,
                uncertainty_pct=float("nan"),
                spec_hash="abc",
            )

    def test_inf_uncertainty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerfEstimate(
                provider_id="p", provider_version="1.0.0",
                domain=DomainType.MXU, boundary_id="b",
                engine=EngineType.MXU, op=OpType.MMUL,
                shape={"M": 64, "K": 64, "N": 64},
                estimated_cycles=1000,
                uncertainty_pct=float("inf"),
                spec_hash="abc",
            )

    def test_unknown_unit_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerfEstimate(
                provider_id="p", provider_version="1.0.0",
                domain=DomainType.MXU, boundary_id="b",
                engine=EngineType.MXU, op=OpType.MMUL,
                shape={"M": 64, "K": 64, "N": 64},
                estimated_cycles=1000,
                units="bad_unit",  # type: ignore[arg-type]
                uncertainty_pct=10.0,
                spec_hash="abc",
            )

    def test_rtl_calibrated_not_verdict_eligible(self) -> None:
        pe = PerfEstimate(
            provider_id="p", provider_version="1.0.0",
            domain=DomainType.MXU, boundary_id="b",
            engine=EngineType.MXU, op=OpType.MMUL,
            shape={"M": 64, "K": 64, "N": 64},
            estimated_cycles=1000,
            basis=BasisType.RTL_MEASUREMENT,
            calibration_state=CalibrationState.RTL_CALIBRATED,
            uncertainty_pct=5.0,
            spec_hash="abc",
        )
        assert not pe.is_verdict_eligible()

    def test_architectural_uncalibrated_is_verdict_eligible(self) -> None:
        pe = PerfEstimate(
            provider_id="p", provider_version="1.0.0",
            domain=DomainType.MXU, boundary_id="b",
            engine=EngineType.MXU, op=OpType.MMUL,
            shape={"M": 64, "K": 64, "N": 64},
            estimated_cycles=1000,
            basis=BasisType.ARCHITECTURAL_FORMULA,
            calibration_state=CalibrationState.UNCALIBRATED,
            uncertainty_pct=5.0,
            spec_hash="abc",
        )
        assert pe.is_verdict_eligible()


# ── PerfBand tests ───────────────────────────────────────────────────────────


class TestPerfBand:
    def test_valid_band(self) -> None:
        b = PerfBand(low=10.0, base=20.0, high=30.0)
        assert b.low == 10.0
        assert b.base == 20.0

    def test_non_monotonic_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerfBand(low=30.0, base=20.0, high=10.0)

    def test_equal_low_base_ok(self) -> None:
        b = PerfBand(low=10.0, base=10.0, high=20.0)
        assert b.low == b.base

    def test_nan_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerfBand(low=float("nan"), base=20.0, high=30.0)

    def test_inf_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerfBand(low=10.0, base=float("inf"), high=30.0)


# ── DiagnosticsEntry tests ──────────────────────────────────────────────────


class TestDiagnosticsEntry:
    def test_valid(self) -> None:
        d = DiagnosticsEntry(
            name="mxu_util", value=85.0,
            assumption="1GHz clock", provenance="spec v1.0",
        )
        assert d.value == 85.0

    def test_nan_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DiagnosticsEntry(
                name="bad", value=float("nan"),
                assumption="x", provenance="y",
            )


# ── PerfReport tests ─────────────────────────────────────────────────────────


class TestPerfReport:
    def test_valid_report(self) -> None:
        r = PerfReport(
            workload_id="wl-qwen-blk0",
            provider_id="p",
            provider_version="1.0.0",
            cycles=PerfBand(low=30000, base=32000, high=35000),
            canonical_total_cycles=32000,
        )
        assert r.canonical_total_cycles == 32000

    def test_sw_overhead_included_true_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerfReport(
                workload_id="wl",
                provider_id="p",
                provider_version="1.0.0",
                cycles=PerfBand(low=10, base=20, high=30),
                canonical_total_cycles=20,
                sw_overhead_included=True,
            )

    def test_round_trip(self) -> None:
        r = PerfReport(
            workload_id="wl-qwen-blk0",
            provider_id="p",
            provider_version="1.0.0",
            cycles=PerfBand(low=30000, base=32000, high=35000),
            canonical_total_cycles=32000,
            diagnostics=[
                DiagnosticsEntry(
                    name="mxu_util", value=85.0,
                    assumption="1GHz", provenance="spec v1",
                ),
            ],
        )
        data = r.model_dump_json()
        r2 = PerfReport.model_validate_json(data)
        assert r2.canonical_total_cycles == r.canonical_total_cycles


# ── PerfArtifact tests ───────────────────────────────────────────────────────


class TestPerfArtifact:
    def test_valid_artifact(self) -> None:
        a = PerfArtifact(
            schema_version="1.0.0",
            provider_id="spec-block64-v1",
            provider_version="1.0.0",
            basis=BasisType.ARCHITECTURAL_FORMULA,
            calibration_state=CalibrationState.UNCALIBRATED,
            domain=DomainType.MXU,
            boundary_id="mxu-64",
            spec_hash="abc",
            estimated_cycles=1000,
            uncertainty_pct=10.0,
        )
        assert a.is_verdict_eligible()
        assert len(a.content_hash()) == 64

    def test_rtl_calibrated_not_eligible(self) -> None:
        a = PerfArtifact(
            schema_version="1.0.0",
            provider_id="p", provider_version="1.0.0",
            basis=BasisType.RTL_MEASUREMENT,
            calibration_state=CalibrationState.RTL_CALIBRATED,
            domain=DomainType.MXU,
            boundary_id="b", spec_hash="abc",
            estimated_cycles=1000,
            uncertainty_pct=5.0,
        )
        assert not a.is_verdict_eligible()

    def test_round_trip(self) -> None:
        a = PerfArtifact(
            schema_version="1.0.0",
            provider_id="p", provider_version="1.0.0",
            basis=BasisType.ARCHITECTURAL_FORMULA,
            calibration_state=CalibrationState.UNCALIBRATED,
            domain=DomainType.MXU,
            boundary_id="b", spec_hash="abc",
            estimated_cycles=1000,
            uncertainty_pct=10.0,
        )
        a2 = a.round_trip()
        assert a2.content_hash() == a.content_hash()
        assert a2.estimated_cycles == 1000

    def test_content_hash_excludes_rtl(self) -> None:
        a_base = PerfArtifact(
            schema_version="1.0.0",
            provider_id="p", provider_version="1.0.0",
            basis=BasisType.ARCHITECTURAL_FORMULA,
            calibration_state=CalibrationState.UNCALIBRATED,
            domain=DomainType.MXU,
            boundary_id="b", spec_hash="abc",
            estimated_cycles=1000,
            uncertainty_pct=10.0,
        )
        a_rtl = PerfArtifact(
            schema_version="1.0.0",
            provider_id="p", provider_version="1.0.0",
            basis=BasisType.ARCHITECTURAL_FORMULA,
            calibration_state=CalibrationState.UNCALIBRATED,
            domain=DomainType.MXU,
            boundary_id="b", spec_hash="abc",
            estimated_cycles=1000,
            uncertainty_pct=10.0,
            rtl_head="deadbeef",
            eda_version="2024.1",
        )
        assert a_base.content_hash() == a_rtl.content_hash()

    def test_measured_cycles_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerfArtifact.model_validate({
                "schema_version": "1.0.0",
                "provider_id": "p",
                "provider_version": "1.0.0",
                "basis": "architectural_formula",
                "calibration_state": "uncalibrated",
                "domain": "mxu",
                "boundary_id": "b",
                "spec_hash": "abc",
                "estimated_cycles": 1000,
                "measured_cycles": 999,
                "uncertainty_pct": 10.0,
            })


# ── Fixture validation tests ─────────────────────────────────────────────────


class TestNegativeFixtures:
    """Verify negative fixtures are rejected by the contract."""

    def test_measured_cycles_fixture_rejected(self) -> None:
        ok, err = validate_fixture(
            str(FIXTURE_DIR / "perf_contract_measured_cycles.json"),
            PerfArtifact,
        )
        assert not ok, f"Should be rejected, got: {err}"

    def test_bad_unit_fixture_rejected(self) -> None:
        ok, err = validate_fixture(
            str(FIXTURE_DIR / "perf_contract_bad_unit.json"),
            PerfArtifact,
        )
        assert not ok, f"Should be rejected, got: {err}"

    def test_nan_fixture_rejected(self) -> None:
        ok, err = validate_fixture(
            str(FIXTURE_DIR / "perf_contract_nan.json"),
            PerfArtifact,
        )
        assert not ok, f"Should be rejected, got: {err}"


# ── Version negotiation tests ────────────────────────────────────────────────


class TestVersionNegotiation:
    def test_supported_version_accepted(self) -> None:
        assert negotiate_version("1.0.0") == "1.0.0"

    def test_unsupported_version_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsupported"):
            negotiate_version("9.9.9")


# ── Enumerated constants tests ────────────────────────────────────────────────


class TestEnums:
    def test_all_units_are_known(self) -> None:
        assert UnitType.CYCLES.value == "cycles"
        assert UnitType.US.value == "us"

    def test_all_bases_defined(self) -> None:
        assert BasisType.ARCHITECTURAL_FORMULA.value == "architectural_formula"
        assert BasisType.RTL_MEASUREMENT.value == "rtl_measurement"

    def test_all_calibration_states_defined(self) -> None:
        assert CalibrationState.UNCALIBRATED.value == "uncalibrated"
        assert CalibrationState.RTL_CALIBRATED.value == "rtl_calibrated"

    def test_event_kinds(self) -> None:
        assert len(EventKind) == 3
        assert EventKind.COMMAND_ACCEPTED.value == "command_accepted"
        assert EventKind.COMMAND_COMPLETED.value == "command_completed"
        assert EventKind.COMMAND_ORDERED.value == "command_ordered"
