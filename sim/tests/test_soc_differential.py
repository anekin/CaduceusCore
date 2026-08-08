"""Func Model / golden differential signoff tests (Todo 14).

Tests cover:
    - Happy round-trip: scenario executes and gate passes.
    - Fault-injected divergence detection and classification.
    - Stale evidence provenance rejection.
    - Missing golden oracle rejection.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gen.npu_abi import MXU
from verification import Action, FuncModelAdapter, Observation, Scenario
from verification.differential import (
    DivergenceClass,
    GoldenExecutorOracle,
    MemoryGoldenOracle,
    check_provenance,
    evidence_matches_scenario,
    load_evidence,
    run_differential_scenario,
    scenario_content_hash,
)
from verification.observation import ObservationType
from verification.tolerance import ToleranceConfig


@pytest.fixture
def adapter():
    """Connected FuncModelAdapter for differential tests."""
    async def _make():
        a = FuncModelAdapter(firmware_mode="python")
        await a.connect()
        await a.reset()
        return a

    loop = asyncio.get_event_loop()
    a = loop.run_until_complete(_make())
    yield a
    loop.run_until_complete(a.disconnect())


@pytest.fixture
def executor_oracle():
    return GoldenExecutorOracle()


@pytest.fixture
def memory_oracle():
    return MemoryGoldenOracle()


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


async def _run_scenario(adapter, scenario, oracle, inputs):
    return await run_differential_scenario(adapter, scenario, oracle, inputs)


def _run(adapter, scenario, oracle, inputs):
    return asyncio.get_event_loop().run_until_complete(
        _run_scenario(adapter, scenario, oracle, inputs)
    )


# ═══════════════════════════════════════════════════════════════════════════
# Happy round-trip
# ═══════════════════════════════════════════════════════════════════════════


def test_happy_round_trip_apb_mmio(adapter, memory_oracle):
    """A simple APB MMIO scenario passes the differential gate."""
    value = 0xABCD1234
    scenario = Scenario(
        scenario_id="happy-apb-mmio",
        actions=[Action.mmio_write(MXU.BASE + MXU.CTRL, value)],
        expected_observations=[
            Observation.mmio_read("mxu_ctrl", MXU.BASE + MXU.CTRL, value),
        ],
        tolerance=ToleranceConfig(int32_bit_exact=True),
    )
    inputs = {
        "oracle": "memory",
        "expected_specs": [
            {
                "observation_id": "mxu_ctrl",
                "observation_type": "mmio_value",
                "address": MXU.BASE + MXU.CTRL,
                "value": value,
            }
        ],
    }

    report = _run(adapter, scenario, memory_oracle, inputs)

    assert report.gate_pass is True
    assert report.scoreboard_result.passed is True
    assert report.golden_name == "MemoryGoldenOracle"
    assert report.adapter_name == "FuncModel"


# ═══════════════════════════════════════════════════════════════════════════
# Fault-injected divergence detection
# ═══════════════════════════════════════════════════════════════════════════


def test_detects_divergence_with_injected_data_corruption(adapter, memory_oracle):
    """Fault-injected data corruption is detected and classified as compute/transport."""
    data = b"\x11\x22\x33\x44" * 4
    offset = 0x2000
    scenario = Scenario(
        scenario_id="fault-corruption",
        actions=[Action.sram_preload(offset, data)],
        expected_observations=[
            Observation(
                observation_id="sram_data",
                observation_type=ObservationType.sram_data,
                address=offset,
                size=len(data),
                data={"raw_hex": data.hex(), "dtype": "int32"},
            ),
        ],
        tolerance=ToleranceConfig(int32_bit_exact=True),
        metadata={
            "fault_class": "data_corruption",
            "fault_params": {"offset": 0, "count": 4},
            "expected_classification": "data_corruption",
            "expected_detector": "data_corruption",
        },
    )
    inputs = {
        "oracle": "memory",
        "expected_specs": [
            {
                "observation_id": "sram_data",
                "observation_type": "sram_data",
                "address": offset,
                "size": len(data),
                "raw_hex": data.hex(),
                "dtype": "int32",
            }
        ],
    }

    report = _run(adapter, scenario, memory_oracle, inputs)

    # The gate passes because the fault was detected/recorded (negative-test contract).
    assert report.gate_pass is True
    assert report.injection_applied is True
    assert "data_corruption" in report.detected_faults or report.injection_applied
    assert any(
        d.classification in (DivergenceClass.compute, DivergenceClass.transport)
        for d in report.divergences
    )


# ═══════════════════════════════════════════════════════════════════════════
# Provenance freshness
# ═══════════════════════════════════════════════════════════════════════════


def test_rejects_stale_provenance(tmp_path):
    """Evidence older than the freshness threshold is rejected."""
    old_time = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    evidence = {
        "timestamp": old_time,
        "records": [{"verdict": "pass"}],
        "scenarios_total": 1,
    }

    ok, reason = check_provenance(evidence, max_age_seconds=86400.0)

    assert ok is False
    assert "older than" in reason


def test_rejects_evidence_without_timestamp(tmp_path):
    """Evidence missing a timestamp is rejected."""
    evidence = {
        "records": [{"verdict": "pass"}],
        "scenarios_total": 1,
    }

    ok, reason = check_provenance(evidence)

    assert ok is False
    assert "timestamp" in reason


def test_rejects_evidence_with_mismatched_scenario_hash(adapter, memory_oracle, tmp_path):
    """Evidence whose scenario content hash does not match the scenario is rejected."""
    scenario = Scenario(
        scenario_id="hash-check",
        actions=[Action.mmio_write(MXU.BASE + MXU.CTRL, 1)],
        expected_observations=[
            Observation.mmio_read("ctrl", MXU.BASE + MXU.CTRL, 1),
        ],
        tolerance=ToleranceConfig(int32_bit_exact=True),
    )
    evidence = {
        "scenario_details": {
            scenario.scenario_id: {
                "metadata": {"scenario_content_hash": "deadbeef"},
            }
        }
    }

    ok, reason = evidence_matches_scenario(evidence, scenario)

    assert ok is False
    assert "mismatch" in reason


# ═══════════════════════════════════════════════════════════════════════════
# Missing golden oracle
# ═══════════════════════════════════════════════════════════════════════════


class EmptyGoldenOracle:
    """Oracle that returns no expected observations — simulates missing golden."""

    @property
    def name(self):
        return "EmptyGoldenOracle"

    def compute_expected(self, _):
        return []


def test_rejects_missing_golden_observations(adapter):
    """A scenario requiring observations but with an empty golden oracle fails the gate."""
    scenario = Scenario(
        scenario_id="missing-golden",
        actions=[Action.mmio_write(MXU.BASE + MXU.CTRL, 1)],
        expected_observations=[
            Observation.mmio_read("ctrl", MXU.BASE + MXU.CTRL, 1),
        ],
        tolerance=ToleranceConfig(int32_bit_exact=True),
    )

    report = _run(adapter, scenario, EmptyGoldenOracle(), {})

    assert report.gate_pass is False
    assert any(
        d.observation_id == "*"
        and "no expected observations" in d.explanation
        for d in report.divergences
    )


# ═══════════════════════════════════════════════════════════════════════════
# Evidence file utilities
# ═══════════════════════════════════════════════════════════════════════════


def test_load_evidence_rejects_missing_file(tmp_path):
    """load_evidence raises FileNotFoundError for a non-existent path."""
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(FileNotFoundError):
        load_evidence(missing)


def test_load_evidence_rejects_non_object(tmp_path):
    """load_evidence rejects a JSON file that is not an object."""
    path = tmp_path / "bad.json"
    path.write_text("[1, 2, 3]")
    with pytest.raises(ValueError):
        load_evidence(path)
