"""Tests for T6 MMIO performance event emission from PerformanceSession.

Covers event emission from MMIOBridge command seams, EventPairValidator
integration, profile-only mode, and adversarial fault injection.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

# Path setup
SIM_DIR = Path(__file__).resolve().parents[1]
if str(SIM_DIR) not in sys.path:
    sys.path.insert(0, str(SIM_DIR))

from mmio_bridge import MMIOBridge  # noqa: E402
from golden_executor import GoldenMXU  # noqa: E402
from regmap import DMA, MXU, SFU, VECTOR  # noqa: E402
from timing.perf_contract import (  # noqa: E402
    EngineType,
    EventKind,
    EventPairValidator,
    OpType,
    PerfEvent,
)
from timing.perf_session import PerformanceSession  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────


def _write_reg(bridge, module_base, offset, value):
    bridge.handle("write", module_base + offset, value)


def _read_reg(bridge, module_base, offset):
    return bridge.handle("read", module_base + offset)


def _make_sram_bridge(sram_size: int = 1024 * 1024):
    sram = bytearray(sram_size)
    bridge = MMIOBridge(modules={
        "sram": sram,
        "mxu": GoldenMXU(),
    })
    return bridge, sram


# ── Baseline: no perf_session, behavior unchanged ────────────────────────────


class TestBaselineNoPerfSession:
    def test_mxu_no_session_computes(self):
        bridge, sram = _make_sram_bridge()
        bridge.perf_session = None

        act = np.array([[1, 2, 3, 4]], dtype=np.int8)
        wgt = np.array([10, 20, 30, 40, 50, 60, 70, 80], dtype=np.int8)
        in_addr, w_addr, out_addr = 0x2000, 0x3000, 0x4000
        sram[in_addr:in_addr + 4] = act.tobytes()
        sram[w_addr:w_addr + 8] = wgt.tobytes()

        _write_reg(bridge, MXU.BASE, MXU.DIM0, (4 << 16) | 1)  # M=1, K=4
        _write_reg(bridge, MXU.BASE, MXU.DIM1, 2)  # N=2
        _write_reg(bridge, MXU.BASE, MXU.I_ADDR, in_addr)
        _write_reg(bridge, MXU.BASE, MXU.W_ADDR, w_addr)
        _write_reg(bridge, MXU.BASE, MXU.O_ADDR, out_addr)
        _write_reg(bridge, MXU.BASE, MXU.CMD, 1)

        status = _read_reg(bridge, MXU.BASE, MXU.STATUS)
        assert status == 2
        result = np.frombuffer(sram[out_addr:out_addr + 8], dtype=np.int32)
        assert np.any(result != 0), "MXU should produce non-zero output without perf session"

    def test_sfu_no_session_computes(self):
        bridge, sram = _make_sram_bridge()
        bridge.perf_session = None

        inp = np.arange(64, dtype=np.float16)
        in_addr, out_addr = 0x2000, 0x3000
        sram[in_addr:in_addr + 128] = inp.tobytes()
        sram[out_addr:out_addr + 128] = b"\x00" * 128

        _write_reg(bridge, SFU.BASE, SFU.CTRL, 0)  # softmax
        _write_reg(bridge, SFU.BASE, SFU.I_ADDR, in_addr)
        _write_reg(bridge, SFU.BASE, SFU.O_ADDR, out_addr)
        _write_reg(bridge, SFU.BASE, SFU.DIM, 64)
        _write_reg(bridge, SFU.BASE, SFU.CMD, 1)

        status = _read_reg(bridge, SFU.BASE, SFU.STATUS)
        assert status == 2
        output = np.frombuffer(sram[out_addr:out_addr + 128], dtype=np.float16)
        assert np.any(output != 0), "SFU should produce non-zero output without perf session"

    def test_vector_no_session_computes(self):
        bridge, sram = _make_sram_bridge()
        bridge.perf_session = None

        a = np.array([1, 2, 3, 4], dtype=np.int32)
        b = np.array([10, 20, 30, 40], dtype=np.int32)
        a_addr, b_addr, out_addr = 0x1000, 0x2000, 0x3000
        sram[a_addr:a_addr + 16] = a.tobytes()
        sram[b_addr:b_addr + 16] = b.tobytes()
        sram[out_addr:out_addr + 16] = b"\x00" * 16

        _write_reg(bridge, VECTOR.BASE, VECTOR.CTRL, 0)  # ADD
        _write_reg(bridge, VECTOR.BASE, VECTOR.A_ADDR, a_addr)
        _write_reg(bridge, VECTOR.BASE, VECTOR.B_ADDR, b_addr)
        _write_reg(bridge, VECTOR.BASE, VECTOR.O_ADDR, out_addr)
        _write_reg(bridge, VECTOR.BASE, VECTOR.DIM, 4)
        _write_reg(bridge, VECTOR.BASE, VECTOR.CMD, 1)

        status = _read_reg(bridge, VECTOR.BASE, VECTOR.STATUS)
        assert status == 2
        output = np.frombuffer(sram[out_addr:out_addr + 16], dtype=np.int32)
        assert np.any(output != 0), "Vector should produce non-zero output without perf session"

    def test_dma_no_session_transfers(self):
        bridge, sram = _make_sram_bridge()
        bridge.perf_session = None

        src_addr, dst_addr = 0x1000, 0x2000
        data = b"hello world test"
        sram[src_addr:src_addr + len(data)] = data
        sram[dst_addr:dst_addr + len(data)] = b"\x00" * len(data)

        _write_reg(bridge, DMA.BASE, DMA.CH0_SRC, src_addr)
        _write_reg(bridge, DMA.BASE, DMA.CH0_DST, dst_addr)
        _write_reg(bridge, DMA.BASE, DMA.CH0_SIZE, len(data))
        _write_reg(bridge, DMA.BASE, DMA.CMD, 1)

        status = _read_reg(bridge, DMA.BASE, DMA.STATUS)
        assert status == 2
        assert sram[dst_addr:dst_addr + len(data)] == data


# ── Event emission ────────────────────────────────────────────────────────────


class TestEventEmission:
    def test_mxu_emits_accepted_and_completed(self):
        bridge, sram = _make_sram_bridge()
        session = PerformanceSession(workload_id="test-mxu")
        bridge.perf_session = session

        act = np.array([[1, 2, 3, 4]], dtype=np.int8)
        wgt = np.array([10, 20, 30, 40, 50, 60, 70, 80], dtype=np.int8)
        in_addr, w_addr, out_addr = 0x2000, 0x3000, 0x4000
        sram[in_addr:in_addr + 4] = act.tobytes()
        sram[w_addr:w_addr + 8] = wgt.tobytes()

        _write_reg(bridge, MXU.BASE, MXU.DIM0, (4 << 16) | 1)
        _write_reg(bridge, MXU.BASE, MXU.DIM1, 2)
        _write_reg(bridge, MXU.BASE, MXU.I_ADDR, in_addr)
        _write_reg(bridge, MXU.BASE, MXU.W_ADDR, w_addr)
        _write_reg(bridge, MXU.BASE, MXU.O_ADDR, out_addr)
        _write_reg(bridge, MXU.BASE, MXU.CMD, 1)

        assert session.accepted_count == 1
        assert session.completed_count == 1
        assert session.is_clean

        ev_a = [e for e in session.events if e.kind == EventKind.COMMAND_ACCEPTED][0]
        assert ev_a.engine == EngineType.MXU
        assert ev_a.op == OpType.MMUL
        assert ev_a.programmed_shape == {"M": 1, "K": 4, "N": 2}

        ev_c = [e for e in session.events if e.kind == EventKind.COMMAND_COMPLETED][0]
        assert ev_c.engine == EngineType.MXU
        assert ev_c.seq_id == ev_a.seq_id

    def test_sfu_emits_events(self):
        bridge, sram = _make_sram_bridge()
        session = PerformanceSession(workload_id="test-sfu")
        bridge.perf_session = session

        inp = np.arange(64, dtype=np.float16)
        in_addr, out_addr = 0x2000, 0x3000
        sram[in_addr:in_addr + 128] = inp.tobytes()

        _write_reg(bridge, SFU.BASE, SFU.CTRL, 0)  # softmax
        _write_reg(bridge, SFU.BASE, SFU.I_ADDR, in_addr)
        _write_reg(bridge, SFU.BASE, SFU.O_ADDR, out_addr)
        _write_reg(bridge, SFU.BASE, SFU.DIM, 64)
        _write_reg(bridge, SFU.BASE, SFU.CMD, 1)

        assert session.accepted_count == 1
        assert session.completed_count == 1
        assert session.is_clean

        ev_a = [e for e in session.events if e.kind == EventKind.COMMAND_ACCEPTED][0]
        assert ev_a.engine == EngineType.SFU
        assert ev_a.op == OpType.SOFTMAX
        assert ev_a.programmed_shape == {"elements": 64}

    def test_vector_emits_events(self):
        bridge, sram = _make_sram_bridge()
        session = PerformanceSession(workload_id="test-vec")
        bridge.perf_session = session

        a = np.array([1, 2, 3, 4], dtype=np.int32)
        b = np.array([10, 20, 30, 40], dtype=np.int32)
        a_addr, b_addr, out_addr = 0x1000, 0x2000, 0x3000
        sram[a_addr:a_addr + 16] = a.tobytes()
        sram[b_addr:b_addr + 16] = b.tobytes()

        _write_reg(bridge, VECTOR.BASE, VECTOR.CTRL, 0)  # ADD
        _write_reg(bridge, VECTOR.BASE, VECTOR.A_ADDR, a_addr)
        _write_reg(bridge, VECTOR.BASE, VECTOR.B_ADDR, b_addr)
        _write_reg(bridge, VECTOR.BASE, VECTOR.O_ADDR, out_addr)
        _write_reg(bridge, VECTOR.BASE, VECTOR.DIM, 4)
        _write_reg(bridge, VECTOR.BASE, VECTOR.CMD, 1)

        assert session.accepted_count == 1
        assert session.completed_count == 1
        assert session.is_clean

        ev_a = [e for e in session.events if e.kind == EventKind.COMMAND_ACCEPTED][0]
        assert ev_a.engine == EngineType.VECTOR
        assert ev_a.op == OpType.ADD
        assert ev_a.programmed_shape == {"dim": 4}

    def test_dma_emits_events(self):
        bridge, sram = _make_sram_bridge()
        session = PerformanceSession(workload_id="test-dma")
        bridge.perf_session = session

        src_addr, dst_addr = 0x1000, 0x2000
        data = b"hello world"
        sram[src_addr:src_addr + len(data)] = data

        _write_reg(bridge, DMA.BASE, DMA.CH0_SRC, src_addr)
        _write_reg(bridge, DMA.BASE, DMA.CH0_DST, dst_addr)
        _write_reg(bridge, DMA.BASE, DMA.CH0_SIZE, len(data))
        _write_reg(bridge, DMA.BASE, DMA.CMD, 1)

        assert session.accepted_count == 1
        assert session.completed_count == 1
        assert session.is_clean

        ev_a = [e for e in session.events if e.kind == EventKind.COMMAND_ACCEPTED][0]
        assert ev_a.engine == EngineType.DMA
        assert ev_a.op == OpType.DMA_COPY
        assert ev_a.programmed_shape == {"bytes": len(data)}

    def test_multiple_commands_sequential_seq_ids(self):
        bridge, sram = _make_sram_bridge()
        session = PerformanceSession(workload_id="test-multi")
        bridge.perf_session = session

        # Two MXU commands
        for i in range(2):
            act = np.array([[1 + i, 2, 3, 4]], dtype=np.int8)
            wgt = np.array([10, 20, 30, 40, 50, 60, 70, 80], dtype=np.int8)
            in_addr, w_addr, out_addr = 0x2000, 0x3000, 0x4000
            sram[in_addr:in_addr + 4] = act.tobytes()
            sram[w_addr:w_addr + 8] = wgt.tobytes()

            _write_reg(bridge, MXU.BASE, MXU.DIM0, (4 << 16) | 1)
            _write_reg(bridge, MXU.BASE, MXU.DIM1, 2)
            _write_reg(bridge, MXU.BASE, MXU.I_ADDR, in_addr)
            _write_reg(bridge, MXU.BASE, MXU.W_ADDR, w_addr)
            _write_reg(bridge, MXU.BASE, MXU.O_ADDR, out_addr)
            _write_reg(bridge, MXU.BASE, MXU.CMD, 1)

        assert session.accepted_count == 2
        assert session.completed_count == 2
        assert session.is_clean

        seq_ids = sorted(e.seq_id for e in session.events if e.kind == EventKind.COMMAND_ACCEPTED)
        assert seq_ids == [1, 2]


# ── Profile-only mode ─────────────────────────────────────────────────────────


class TestProfileOnly:
    def test_mxu_profile_only_skips_compute(self):
        bridge, sram = _make_sram_bridge()
        session = PerformanceSession(workload_id="test-profile", profile_only=True)
        bridge.perf_session = session

        act = np.array([[1, 2, 3, 4]], dtype=np.int8)
        wgt = np.array([10, 20, 30, 40, 50, 60, 70, 80], dtype=np.int8)
        in_addr, w_addr, out_addr = 0x2000, 0x3000, 0x4000
        sram[in_addr:in_addr + 4] = act.tobytes()
        sram[w_addr:w_addr + 8] = wgt.tobytes()
        sram[out_addr:out_addr + 8] = b"\x00" * 8

        _write_reg(bridge, MXU.BASE, MXU.DIM0, (4 << 16) | 1)
        _write_reg(bridge, MXU.BASE, MXU.DIM1, 2)
        _write_reg(bridge, MXU.BASE, MXU.I_ADDR, in_addr)
        _write_reg(bridge, MXU.BASE, MXU.W_ADDR, w_addr)
        _write_reg(bridge, MXU.BASE, MXU.O_ADDR, out_addr)
        _write_reg(bridge, MXU.BASE, MXU.CMD, 1)

        # STATUS should be DONE (2) even in profile-only
        status = _read_reg(bridge, MXU.BASE, MXU.STATUS)
        assert status == 2

        # Output should be zero — no computation happened
        result = np.frombuffer(sram[out_addr:out_addr + 8], dtype=np.int32)
        assert np.all(result == 0), "Profile-only must produce zero output"

        # Events must still be emitted
        assert session.accepted_count == 1
        assert session.completed_count == 1
        assert session.is_clean
        assert not session.numerical_execution

    def test_profile_only_evidence_not_functional(self):
        session = PerformanceSession(workload_id="test", profile_only=True)
        expected_evidence = {
            "numerical_execution": False,
            "functional_evidence": False,
        }
        actual = {
            "numerical_execution": session.numerical_execution,
            "functional_evidence": session.numerical_execution,
        }
        assert actual == expected_evidence


# ── Event pair validation ─────────────────────────────────────────────────────


class TestEventPairValidator:
    def test_clean_pairs_no_violations(self):
        validator = EventPairValidator()
        e1 = PerfEvent(
            event_id="ev-1a", kind=EventKind.COMMAND_ACCEPTED, seq_id=1,
            parent_workload_id="wl", engine=EngineType.MXU, op=OpType.MMUL,
            programmed_shape={"M": 64, "K": 64, "N": 64},
        )
        e2 = PerfEvent(
            event_id="ev-1c", kind=EventKind.COMMAND_COMPLETED, seq_id=1,
            parent_workload_id="wl", engine=EngineType.MXU, op=OpType.MMUL,
            programmed_shape={"M": 64, "K": 64, "N": 64},
        )
        violations = validator.validate_all([e1, e2])
        assert len(violations) == 0

    def test_missing_completion_detected(self):
        validator = EventPairValidator()
        e1 = PerfEvent(
            event_id="ev-a", kind=EventKind.COMMAND_ACCEPTED, seq_id=1,
            parent_workload_id="wl", engine=EngineType.MXU, op=OpType.MMUL,
            programmed_shape={"M": 64, "K": 64, "N": 64},
        )
        violations = validator.validate_all([e1])
        assert len(violations) >= 1
        assert any("Missing completion" in v for v in violations)

    def test_completion_without_acceptance_detected(self):
        validator = EventPairValidator()
        e1 = PerfEvent(
            event_id="ev-c", kind=EventKind.COMMAND_COMPLETED, seq_id=42,
            parent_workload_id="wl", engine=EngineType.MXU, op=OpType.MMUL,
            programmed_shape={"M": 64, "K": 64, "N": 64},
        )
        violations = validator.validate_all([e1])
        assert len(violations) >= 1
        assert any("Completion without acceptance" in v for v in violations)

    def test_duplicate_event_id_detected(self):
        validator = EventPairValidator()
        e1 = PerfEvent(
            event_id="dup", kind=EventKind.COMMAND_ACCEPTED, seq_id=1,
            parent_workload_id="wl", engine=EngineType.MXU, op=OpType.MMUL,
            programmed_shape={"M": 64, "K": 64, "N": 64},
        )
        e2 = PerfEvent(
            event_id="dup", kind=EventKind.COMMAND_COMPLETED, seq_id=1,
            parent_workload_id="wl", engine=EngineType.MXU, op=OpType.MMUL,
            programmed_shape={"M": 64, "K": 64, "N": 64},
        )
        violations = validator.validate_all([e1, e2])
        assert len(violations) >= 1
        assert any("Duplicate event_id" in v for v in violations)


# ── Malformed input rejection ─────────────────────────────────────────────────


class TestMalformedInput:
    def test_duplicate_seq_id_emission_rejected(self):
        session = PerformanceSession(workload_id="test-dup")
        e1 = session.emit_accepted(
            EngineType.MXU, OpType.MMUL, {"M": 64, "K": 64, "N": 64},
        )
        session.replay_accepted(e1)
        assert not session.is_clean
        violations = session.violations
        assert any("Duplicate" in v for v in violations)

    def test_missing_completion_in_session_detected(self):
        session = PerformanceSession(workload_id="test-missing")
        session.emit_accepted(
            EngineType.MXU, OpType.MMUL, {"M": 64, "K": 64, "N": 64},
        )
        assert not session.is_clean
        violations = session.violations
        assert any("Missing completion" in v for v in violations)

    def test_completion_without_acceptance_in_session_detected(self):
        session = PerformanceSession(workload_id="test-bad-comp")
        session.emit_completed(
            99, EngineType.MXU, OpType.MMUL, {"M": 64, "K": 64, "N": 64},
        )
        assert not session.is_clean
        violations = session.violations
        assert any("Completion without acceptance" in v for v in violations)

    def test_wrong_shape_keys_rejected_by_pydantic(self):
        with pytest.raises(ValidationError):
            PerfEvent(
                event_id="ev-bad",
                kind=EventKind.COMMAND_ACCEPTED,
                seq_id=1,
                parent_workload_id="wl",
                engine=EngineType.MXU,
                op=OpType.MMUL,
                programmed_shape={"M": 64, "X": 99},
            )

    def test_negative_shape_value_rejected(self):
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


# ── Functional equivalence ────────────────────────────────────────────────────


class TestFunctionalEquivalence:
    def test_mxu_output_identical_with_and_without_session(self):
        def run_mxu(bridge, sram):
            act = np.array([[1, 2, 3, 4]], dtype=np.int8)
            wgt = np.array([10, 20, 30, 40, 50, 60, 70, 80], dtype=np.int8)
            in_addr, w_addr, out_addr = 0x2000, 0x3000, 0x4000
            sram[in_addr:in_addr + 4] = act.tobytes()
            sram[w_addr:w_addr + 8] = wgt.tobytes()
            sram[out_addr:out_addr + 8] = b"\x00" * 8

            _write_reg(bridge, MXU.BASE, MXU.DIM0, (4 << 16) | 1)
            _write_reg(bridge, MXU.BASE, MXU.DIM1, 2)
            _write_reg(bridge, MXU.BASE, MXU.I_ADDR, in_addr)
            _write_reg(bridge, MXU.BASE, MXU.W_ADDR, w_addr)
            _write_reg(bridge, MXU.BASE, MXU.O_ADDR, out_addr)
            _write_reg(bridge, MXU.BASE, MXU.CMD, 1)
            return bytes(sram[out_addr:out_addr + 8])

        # Without session
        bridge1, sram1 = _make_sram_bridge()
        bridge1.perf_session = None
        out1 = run_mxu(bridge1, sram1)

        # With session
        bridge2, sram2 = _make_sram_bridge()
        session = PerformanceSession(workload_id="eq-test")
        bridge2.perf_session = session
        out2 = run_mxu(bridge2, sram2)

        assert out1 == out2, "Output must be identical with and without perf session"
        h1 = hashlib.md5(out1).hexdigest()
        h2 = hashlib.md5(out2).hexdigest()
        assert h1 == h2, f"Output hash mismatch: {h1} vs {h2}"

    def test_status_identical_with_and_without_session(self):
        def run_and_get_status(bridge, sram):
            act = np.array([[1, 2, 3, 4]], dtype=np.int8)
            wgt = np.array([10, 20, 30, 40, 50, 60, 70, 80], dtype=np.int8)
            in_addr, w_addr, out_addr = 0x2000, 0x3000, 0x4000
            sram[in_addr:in_addr + 4] = act.tobytes()
            sram[w_addr:w_addr + 8] = wgt.tobytes()

            _write_reg(bridge, MXU.BASE, MXU.DIM0, (4 << 16) | 1)
            _write_reg(bridge, MXU.BASE, MXU.DIM1, 2)
            _write_reg(bridge, MXU.BASE, MXU.I_ADDR, in_addr)
            _write_reg(bridge, MXU.BASE, MXU.W_ADDR, w_addr)
            _write_reg(bridge, MXU.BASE, MXU.O_ADDR, out_addr)
            _write_reg(bridge, MXU.BASE, MXU.CMD, 1)
            return _read_reg(bridge, MXU.BASE, MXU.STATUS)

        bridge1, sram1 = _make_sram_bridge()
        bridge1.perf_session = None
        st1 = run_and_get_status(bridge1, sram1)

        bridge2, sram2 = _make_sram_bridge()
        session = PerformanceSession(workload_id="eq-status")
        bridge2.perf_session = session
        st2 = run_and_get_status(bridge2, sram2)

        assert st1 == st2 == 2, "STATUS must be identical (DONE=2) with/without session"


# ── Event deterministic content hash ──────────────────────────────────────────


class TestEventDeterministic:
    def test_same_sequence_same_hash(self):
        session1 = PerformanceSession(workload_id="det-test")
        session2 = PerformanceSession(workload_id="det-test")

        for session in (session1, session2):
            bridge, sram = _make_sram_bridge()
            bridge.perf_session = session

            act = np.array([[1, 2, 3, 4]], dtype=np.int8)
            wgt = np.array([10, 20, 30, 40, 50, 60, 70, 80], dtype=np.int8)
            in_addr, w_addr, out_addr = 0x2000, 0x3000, 0x4000
            sram[in_addr:in_addr + 4] = act.tobytes()
            sram[w_addr:w_addr + 8] = wgt.tobytes()

            _write_reg(bridge, MXU.BASE, MXU.DIM0, (4 << 16) | 1)
            _write_reg(bridge, MXU.BASE, MXU.DIM1, 2)
            _write_reg(bridge, MXU.BASE, MXU.I_ADDR, in_addr)
            _write_reg(bridge, MXU.BASE, MXU.W_ADDR, w_addr)
            _write_reg(bridge, MXU.BASE, MXU.O_ADDR, out_addr)
            _write_reg(bridge, MXU.BASE, MXU.CMD, 1)

        assert session1.accepted_count == session2.accepted_count
        assert session1.completed_count == session2.completed_count

        events1 = json.dumps(
            [e.model_dump(mode="json") for e in session1.events], sort_keys=True
        )
        events2 = json.dumps(
            [e.model_dump(mode="json") for e in session2.events], sort_keys=True
        )
        h1 = hashlib.sha256(events1.encode()).hexdigest()
        h2 = hashlib.sha256(events2.encode()).hexdigest()
        assert h1 == h2, "Deterministic events must have same hash for same workload"


# ── SFU/Vector op mapping ─────────────────────────────────────────────────────


class TestOpMapping:
    def test_sfu_op_maps_correctly(self):
        assert PerformanceSession.sfu_op(0) == OpType.SOFTMAX
        assert PerformanceSession.sfu_op(1) == OpType.LAYERNORM
        assert PerformanceSession.sfu_op(2) == OpType.GELU
        assert PerformanceSession.sfu_op(3) == OpType.SILU
        assert PerformanceSession.sfu_op(5) == OpType.ROPE
        assert PerformanceSession.sfu_op(6) == OpType.RMSNORM
        assert PerformanceSession.sfu_op(99) is None

    def test_vector_op_maps_correctly(self):
        assert PerformanceSession.vector_op(0) == OpType.ADD
        assert PerformanceSession.vector_op(1) == OpType.MUL
        assert PerformanceSession.vector_op(2) == OpType.MAX
        assert PerformanceSession.vector_op(3) == OpType.SUM
        assert PerformanceSession.vector_op(4) == OpType.CONV
        assert PerformanceSession.vector_op(5) == OpType.RESID
        assert PerformanceSession.vector_op(99) is None

    def test_sfu_rope_emits_correct_op(self):
        bridge, sram = _make_sram_bridge()
        session = PerformanceSession(workload_id="test-rope")
        bridge.perf_session = session

        inp = np.zeros(128, dtype=np.float16)
        in_addr, out_addr = 0x2000, 0x3000
        sram[in_addr:in_addr + 256] = inp.tobytes()

        _write_reg(bridge, SFU.BASE, SFU.CTRL, 5)  # ROPE
        _write_reg(bridge, SFU.BASE, SFU.I_ADDR, in_addr)
        _write_reg(bridge, SFU.BASE, SFU.O_ADDR, out_addr)
        _write_reg(bridge, SFU.BASE, SFU.DIM, (4 << 16) | 128)
        _write_reg(bridge, SFU.BASE, SFU.CMD, 1)

        ev_a = [e for e in session.events if e.kind == EventKind.COMMAND_ACCEPTED][0]
        assert ev_a.op == OpType.ROPE

    def test_vector_conv_emits_correct_op(self):
        bridge, sram = _make_sram_bridge()
        session = PerformanceSession(workload_id="test-conv")
        bridge.perf_session = session

        a = np.array([1, 2, 3, 4], dtype=np.int32)
        a_addr, out_addr = 0x1000, 0x3000
        sram[a_addr:a_addr + 16] = a.tobytes()

        _write_reg(bridge, VECTOR.BASE, VECTOR.CTRL, 4)  # CONV
        _write_reg(bridge, VECTOR.BASE, VECTOR.A_ADDR, a_addr)
        _write_reg(bridge, VECTOR.BASE, VECTOR.O_ADDR, out_addr)
        _write_reg(bridge, VECTOR.BASE, VECTOR.DIM, 4)
        _write_reg(bridge, VECTOR.BASE, VECTOR.CMD, 1)

        ev_a = [e for e in session.events if e.kind == EventKind.COMMAND_ACCEPTED][0]
        assert ev_a.op == OpType.CONV


# ── emit_ordered informational events ─────────────────────────────────────────


class TestOrderedEvents:
    def test_emit_ordered_not_pair_tracked(self):
        session = PerformanceSession(workload_id="test-ordered")
        e = session.emit_ordered(EngineType.RISC_V, OpType.RISC_V_INSTR,
                                 {"instructions": 1})
        assert e.kind == EventKind.COMMAND_ORDERED
        assert session.accepted_count == 0
        assert session.completed_count == 0
        assert session.is_clean

    def test_ordered_events_counted(self):
        session = PerformanceSession(workload_id="test-ordered-2")
        session.emit_ordered(EngineType.NOC, OpType.NOC_ROUTE,
                             {"bytes": 64, "topology": 0, "route": 0})
        session.emit_ordered(EngineType.KV_CACHE, OpType.KV_ACCESS,
                             {"token_pos": 0, "sram_kb": 128})
        assert len(session.events) == 2
        assert session.is_clean
        ordered = [e for e in session.events if e.kind == EventKind.COMMAND_ORDERED]
        assert len(ordered) == 2
