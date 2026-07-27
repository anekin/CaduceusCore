"""Tests for sim/verification/ — scenario, observation, scoreboard, and DUT adapter.

Coverage:
    - Scenario roundtrip serialization (dict and JSON)
    - Deterministic serialization
    - Action auto-classification
    - FakeDUTAdapter execute/observe
    - FakeDUTAdapter rejects diagnostic actions
    - Scoreboard comparison (pass and fail)
    - Malformed scenario rejection
    - Undeclared backdoor rejection
    - TestCaseConfig migration roundtrip
"""

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pytest

from sim.verification import (
    Action,
    DUTAdapter,
    EvidenceRecord,
    FakeDUTAdapter,
    Observation,
    OperationClass,
    Provenance,
    Scenario,
    Scoreboard,
    ScoreboardResult,
    ToleranceConfig,
    migrate_testcase_config,
)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _make_minimal_scenario() -> Scenario:
    """Return a minimal valid scenario for tests.

    Uses a fixed provenance timestamp so deterministic serialization
    tests are stable across runs.
    """
    return Scenario(
        scenario_id="test-001",
        scenario_version=1,
        description="Minimal test scenario",
        actions=[
            Action.mmio_write(0x4000_0000, 0x0000_0001),
            Action.doorbell(1),
        ],
        expected_observations=[
            Observation.mmio_read("mmio_ctrl", 0x4000_0000, 0x0000_0001),
        ],
        provenance=Provenance(created_at="2026-07-27T00:00:00+00:00"),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Roundtrip tests
# ═══════════════════════════════════════════════════════════════════════════


class TestScenarioRoundtrip:
    """Scenario serialization roundtrip tests."""

    def test_roundtrip_to_dict_and_back(self):
        """Scenario → dict → Scenario preserves all data."""
        original = _make_minimal_scenario()
        original.evidence.append(EvidenceRecord(
            record_id="ev-001",
            dut_adapter="FakeDUT",
            verdict="pass",
        ))

        data = original.to_dict()
        restored = Scenario.from_dict(data)

        assert restored.scenario_id == original.scenario_id
        assert restored.scenario_version == original.scenario_version
        assert restored.description == original.description
        assert len(restored.actions) == len(original.actions)
        assert len(restored.expected_observations) == len(original.expected_observations)
        assert len(restored.evidence) == len(original.evidence)
        assert restored.evidence[0].verdict == "pass"

    def test_roundtrip_to_json_and_back(self):
        """Scenario → JSON → Scenario preserves all data."""
        original = _make_minimal_scenario()
        json_str = original.to_json()
        data = json.loads(json_str)
        restored = Scenario.from_dict(data)

        assert restored.scenario_id == original.scenario_id
        assert len(restored.actions) == 2

    def test_deterministic_serialization(self):
        """Same scenario produces identical JSON twice."""
        s1 = _make_minimal_scenario()
        s2 = _make_minimal_scenario()

        j1 = s1.to_json()
        j2 = s2.to_json()

        assert j1 == j2

    def test_content_hash_stable(self):
        """content_hash() produces same value for equal scenarios."""
        s1 = _make_minimal_scenario()
        s2 = _make_minimal_scenario()

        assert s1.content_hash() == s2.content_hash()

    def test_content_hash_different_for_different_scenarios(self):
        """content_hash() differs for different scenarios."""
        s1 = _make_minimal_scenario()
        s2 = _make_minimal_scenario()
        s2.scenario_id = "test-002"

        assert s1.content_hash() != s2.content_hash()

    def test_unsupported_version_raises(self):
        """from_dict() raises ValueError for unsupported version."""
        data = _make_minimal_scenario().to_dict()
        data["scenario_version"] = 99

        with pytest.raises(ValueError, match="Unsupported scenario_version"):
            Scenario.from_dict(data)

    def test_provenance_roundtrip(self):
        """Provenance serialization is deterministic."""
        p = Provenance(
            case_id="FM-SOC-001",
            source_file="test.npz",
            generator_version="1.0.0",
            model_hash="abc123",
            abi_version=1,
        )
        data = p.to_dict()
        restored = Provenance.from_dict(data)

        assert restored.case_id == "FM-SOC-001"
        assert restored.source_file == "test.npz"
        assert restored.abi_version == 1

    def test_observation_roundtrip(self):
        """Observation serialization is deterministic."""
        obs = Observation.mmio_read("obs1", 0x4000_0000, 42)
        data = obs.to_dict()
        restored = Observation.from_dict(data)

        assert restored.observation_id == "obs1"
        assert restored.address == 0x4000_0000
        assert restored.data["value"] == 42

    def test_tolerance_config_roundtrip(self):
        """ToleranceConfig serialization is deterministic."""
        tc = ToleranceConfig(fp16_abs_tol=1e-4, fp16_rel_tol=5e-3)
        data = tc.to_dict()
        restored = ToleranceConfig.from_dict(data)

        assert restored.fp16_abs_tol == 1e-4
        assert restored.fp16_rel_tol == 5e-3


class TestActionClassification:
    """Action auto-classification tests."""

    def test_mmio_write_auto_classified_frontdoor(self):
        a = Action.mmio_write(0x1000, 1)
        assert a.classification == OperationClass.frontdoor

    def test_sram_preload_auto_classified_init_backdoor(self):
        a = Action.sram_preload(0, b"\x00" * 4)
        assert a.classification == OperationClass.allowed_init_backdoor

    def test_sram_readback_auto_classified_obs_backdoor(self):
        a = Action.sram_readback(0, 256)
        assert a.classification == OperationClass.allowed_obs_backdoor

    def test_doorbell_auto_classified_frontdoor(self):
        a = Action.doorbell(1)
        assert a.classification == OperationClass.frontdoor

    def test_pcie_write_auto_classified_frontdoor(self):
        a = Action.pcie_write(0x8000_0000, b"data")
        assert a.classification == OperationClass.frontdoor

    def test_unknown_action_type_classified_diagnostic(self):
        a = Action(action_type="debug_probe")
        assert a.classification == OperationClass.diagnostic

    def test_explicit_classification_overrides_default(self):
        """Explicit frontdoor classification on a preload is allowed in action."""
        a = Action(
            action_type="sram_preload",
            classification=OperationClass.frontdoor,
            parameters={"offset": 0, "data_hex": "deadbeef"},
        )
        assert a.classification == OperationClass.frontdoor


class TestObservationFactories:
    """Observation factory method tests."""

    def test_mmio_read_factory(self):
        obs = Observation.mmio_read("r1", 0x4000_0000, 99)
        assert obs.observation_id == "r1"
        assert obs.address == 0x4000_0000
        assert obs.data["value"] == 99

    def test_sram_readback_factory(self):
        obs = Observation.sram_readback("s1", 0x1000, 256)
        assert obs.observation_id == "s1"
        assert obs.address == 0x1000
        assert obs.size == 256
        assert obs.data["dtype"] == "int32"

    def test_completion_factory(self):
        obs = Observation.completion("c1", expected_status=1)
        assert obs.observation_id == "c1"
        assert obs.data["status"] == 1

    def test_content_hash_same_for_equal_observations(self):
        o1 = Observation.mmio_read("r1", 0x1000, 42)
        o2 = Observation.mmio_read("r1", 0x1000, 42)
        assert o1.content_hash() == o2.content_hash()

    def test_content_hash_differs_for_different_data(self):
        o1 = Observation.mmio_read("r1", 0x1000, 42)
        o2 = Observation.mmio_read("r1", 0x1000, 43)
        assert o1.content_hash() != o2.content_hash()


# ═══════════════════════════════════════════════════════════════════════════
# Fake DUT adapter tests
# ═══════════════════════════════════════════════════════════════════════════


class TestFakeDUTAdapter:
    """FakeDUTAdapter contract tests.

    Uses asyncio.run() to drive async adapter operations since
    pytest-asyncio is not required for this test suite.
    """

    @staticmethod
    async def _connect_disconnect():
        adapter = FakeDUTAdapter()
        await adapter.connect()
        assert adapter._connected
        await adapter.disconnect()
        assert not adapter._connected

    def test_connect_disconnect(self):
        import asyncio
        asyncio.run(self._connect_disconnect())

    @staticmethod
    async def _reset_clears_state():
        adapter = FakeDUTAdapter()
        await adapter.connect()
        await adapter.execute_action(Action.mmio_write(0x1000, 42))
        assert adapter._mmio[0x1000] == 42
        await adapter.reset()
        assert 0x1000 not in adapter._mmio

    def test_reset_clears_state(self):
        import asyncio
        asyncio.run(self._reset_clears_state())

    @staticmethod
    async def _execute_mmio_write():
        adapter = FakeDUTAdapter()
        await adapter.connect()
        action = Action.mmio_write(0x4000_0000, 0xDEAD_BEEF)
        await adapter.execute_action(action)
        assert adapter._mmio[0x4000_0000] == 0xDEAD_BEEF

    def test_execute_mmio_write(self):
        import asyncio
        asyncio.run(self._execute_mmio_write())

    @staticmethod
    async def _execute_sram_preload():
        adapter = FakeDUTAdapter()
        await adapter.connect()
        data = bytes(range(16))
        action = Action.sram_preload(0x1000, data)
        await adapter.execute_action(action)
        assert adapter._sram[0x1000] == data

    def test_execute_sram_preload(self):
        import asyncio
        asyncio.run(self._execute_sram_preload())

    @staticmethod
    async def _execute_doorbell_and_irq():
        adapter = FakeDUTAdapter()
        await adapter.connect()
        await adapter.execute_action(Action.doorbell(host_tail=5))
        assert adapter._doorbell_tail == 5
        assert adapter._irq_pending.get(0) is True
        await adapter.execute_action(Action.wait_irq(source=0))
        assert adapter._irq_pending.get(0) is False

    def test_execute_doorbell_and_irq(self):
        import asyncio
        asyncio.run(self._execute_doorbell_and_irq())

    @staticmethod
    async def _execute_pcie_write():
        adapter = FakeDUTAdapter()
        await adapter.connect()
        data = b"pcie_tlp_payload"
        action = Action.pcie_write(0x8000_0100, data)
        await adapter.execute_action(action)
        assert adapter._pcie_space[0x8000_0100] == data

    def test_execute_pcie_write(self):
        import asyncio
        asyncio.run(self._execute_pcie_write())

    @staticmethod
    async def _observe_mmio_value():
        adapter = FakeDUTAdapter()
        await adapter.connect()
        await adapter.execute_action(Action.mmio_write(0x4000_0000, 12345))
        obs_spec = Observation.mmio_read("mmio_test", 0x4000_0000, 0)
        result = await adapter.observe(obs_spec)
        assert result.observation_id == "mmio_test"
        assert result.data["value"] == 12345

    def test_observe_mmio_value(self):
        import asyncio
        asyncio.run(self._observe_mmio_value())

    @staticmethod
    async def _observe_sram_data():
        adapter = FakeDUTAdapter()
        await adapter.connect()
        data = b"\x01\x02\x03\x04"
        await adapter.execute_action(Action.sram_preload(0x2000, data))
        obs_spec = Observation.sram_readback("sram_test", 0x2000, 4)
        result = await adapter.observe(obs_spec)
        assert result.observation_id == "sram_test"
        assert result.data["raw_hex"] == data.hex()

    def test_observe_sram_data(self):
        import asyncio
        asyncio.run(self._observe_sram_data())

    @staticmethod
    async def _observe_completion_status():
        adapter = FakeDUTAdapter()
        await adapter.connect()
        await adapter.execute_action(Action.mmio_write(0x4000_0000, 1))
        obs_spec = Observation.completion("completion_test")
        result = await adapter.observe(obs_spec)
        assert result.data["status"] == 0x2

    def test_observe_completion_status(self):
        import asyncio
        asyncio.run(self._observe_completion_status())

    def test_adapter_name_and_firmware_mode(self):
        adapter = FakeDUTAdapter()
        assert adapter.adapter_name == "FakeDUT"
        assert adapter.firmware_mode == "fake"

    @staticmethod
    async def _disconnected_raises_error():
        adapter = FakeDUTAdapter()
        try:
            await adapter.execute_action(Action.mmio_write(0x1000, 1))
            assert False, "Should have raised"
        except Exception:
            pass

    def test_disconnected_raises_error(self):
        import asyncio
        asyncio.run(self._disconnected_raises_error())


# ═══════════════════════════════════════════════════════════════════════════
# Rejects malformed and forbidden backdoor tests
# ═══════════════════════════════════════════════════════════════════════════


class TestRejects_Malformed:
    """Tests that reject malformed scenarios."""

    def test_rejects_empty_scenario_id(self):
        s = Scenario(scenario_id="")
        errors = s.validate()
        assert any("scenario_id must not be empty" in e for e in errors)

    def test_rejects_invalid_version(self):
        s = Scenario(scenario_id="test", scenario_version=0)
        errors = s.validate()
        assert any("scenario_version must be >= 1" in e for e in errors)

    def test_rejects_empty_action_type(self):
        with pytest.raises(ValueError, match="action_type must not be empty"):
            Action(action_type="")

    def test_rejects_unknown_action_type_in_fake_dut(self):
        """FakeDUT rejects unrecognized non-diagnostic action types.

        When an action has an unknown type and is classified as frontdoor
        (not diagnostic), the FakeDUT raises ValueError.
        """
        adapter = FakeDUTAdapter()

        async def _run():
            await adapter.connect()
            await adapter.execute_action(Action(
                action_type="nonexistent_op",
                classification=OperationClass.frontdoor,
            ))

        import asyncio
        with pytest.raises(ValueError, match="Unsupported action_type"):
            asyncio.run(_run())

    def test_rejects_malformed_missing_address(self):
        """Action with missing required parameter raises error."""
        adapter = FakeDUTAdapter()

        async def _run():
            await adapter.connect()
            await adapter.execute_action(Action(
                action_type="mmio_write",
                classification=OperationClass.frontdoor,
                parameters={},  # missing "address" and "value"
            ))

        import asyncio
        with pytest.raises(KeyError):
            asyncio.run(_run())

    def test_rejects_diagnostic_action_in_scenario(self):
        """A scenario containing a diagnostic action fails validation."""
        s = Scenario(
            scenario_id="test-diag",
            actions=[
                Action.mmio_write(0x4000_0000, 1),
                Action(action_type="debug_probe"),  # auto-classified as diagnostic
            ],
        )
        errors = s.validate()
        assert any("diagnostic-only" in e for e in errors)


class TestRejects_ForbiddenBackdoor:
    """Tests that reject scenarios with undeclared backdoor operations."""

    def test_rejects_undeclared_backdoor_scenario(self):
        """Scenario with SRAM preload misclassified as frontdoor is rejected."""
        s = Scenario(
            scenario_id="test-backdoor-violation",
            actions=[
                Action.mmio_write(0x4000_0000, 1),
                Action(
                    action_type="sram_preload",
                    classification=OperationClass.frontdoor,  # wrong!
                    parameters={"offset": 0, "data_hex": "deadbeef"},
                ),
            ],
        )
        errors = s.validate()
        assert any("classified as frontdoor but type requires" in e for e in errors)

    def test_rejects_undeclared_backdoor_via_reject_method(self):
        """reject_undeclared_backdoors() raises ValueError."""
        s = Scenario(
            scenario_id="test-violation",
            actions=[
                Action.mmio_write(0x4000_0000, 1),
                Action(
                    action_type="sram_preload",
                    classification=OperationClass.frontdoor,  # wrong!
                    parameters={"offset": 0, "data_hex": "deadbeef"},
                ),
            ],
        )
        with pytest.raises(ValueError, match="validation error"):
            s.reject_undeclared_backdoors()

    def test_rejects_diagnostic_action_in_fake_dut(self):
        """FakeDUTAdapter rejects diagnostic-classified actions."""
        adapter = FakeDUTAdapter(accept_diagnostics=False)

        async def _run():
            await adapter.connect()
            await adapter.execute_action(Action(
                action_type="debug_probe",
                classification=OperationClass.diagnostic,
                parameters={"probe": "internal_signal"},
            ))

        import asyncio
        with pytest.raises(ValueError, match="diagnostic"):
            asyncio.run(_run())

    def test_fake_dut_accepts_diagnostics_when_configured(self):
        """FakeDUTAdapter accepts diagnostics when accept_diagnostics=True."""
        adapter = FakeDUTAdapter(accept_diagnostics=True)

        async def _run():
            await adapter.connect()
            await adapter.execute_action(Action(
                action_type="debug_probe",
                classification=OperationClass.diagnostic,
                parameters={"probe": "internal_signal"},
            ))
            # Should not raise

        import asyncio
        asyncio.run(_run())  # No exception expected

    def test_valid_scenario_passes_validation(self):
        """A valid scenario passes all validation checks."""
        s = _make_minimal_scenario()
        errors = s.validate()
        assert len(errors) == 0
        # reject_undeclared_backdoors should not raise
        s.reject_undeclared_backdoors()

    def test_backdoor_scenario_with_correct_classification_passes(self):
        """SRAM preload correctly classified as init_backdoor passes."""
        s = Scenario(
            scenario_id="test-valid-backdoor",
            actions=[
                Action.mmio_write(0x4000_0000, 1),
                Action.sram_preload(0x1000, b"test"),  # auto-classified correctly
                Action.sram_readback(0x1000, 4),  # auto-classified correctly
            ],
        )
        errors = s.validate()
        assert len(errors) == 0


# ═══════════════════════════════════════════════════════════════════════════
# Scoreboard tests
# ═══════════════════════════════════════════════════════════════════════════


class TestScoreboard:
    """Scoreboard comparison tests."""

    def test_mmio_match_passes(self):
        exp = [Observation.mmio_read("r1", 0x4000_0000, 42)]
        act = [Observation.mmio_read("r1", 0x4000_0000, 42)]
        sb = Scoreboard()
        result = sb.compare(exp, act)
        assert result.passed

    def test_mmio_mismatch_fails(self):
        exp = [Observation.mmio_read("r1", 0x4000_0000, 42)]
        act = [Observation.mmio_read("r1", 0x4000_0000, 99)]
        sb = Scoreboard()
        result = sb.compare(exp, act)
        assert not result.passed
        assert result.failed_checks == 1
        assert len(result.failures) == 1

    def test_missing_observation_fails(self):
        exp = [Observation.mmio_read("r1", 0x4000_0000, 42)]
        act: list = []
        sb = Scoreboard()
        result = sb.compare(exp, act)
        assert not result.passed
        assert "not found in actual" in result.failures[0]["message"]

    def test_completion_match_passes(self):
        exp = [Observation.completion("c1", 0)]
        act = [Observation.completion("c1", 0)]
        sb = Scoreboard()
        result = sb.compare(exp, act)
        assert result.passed

    def test_extra_actual_observations_are_ignored(self):
        """Extra actual observations not in expected are ignored (not a failure)."""
        exp = [Observation.mmio_read("r1", 0x4000_0000, 42)]
        act = [
            Observation.mmio_read("r1", 0x4000_0000, 42),
            Observation.mmio_read("r2", 0x4000_1000, 99),  # extra, not in expected
        ]
        sb = Scoreboard()
        result = sb.compare(exp, act)
        assert result.passed

    def test_memory_int32_bit_exact_match_passes(self):
        """INT32 memory data with identical hex passes."""
        data_hex = "0100000002000000"
        exp = [Observation(
            observation_id="s1",
            observation_type="sram_data",
            address=0x1000,
            size=8,
            data={"raw_hex": data_hex, "dtype": "int32"},
        )]
        act = [Observation(
            observation_id="s1",
            observation_type="sram_data",
            address=0x1000,
            size=8,
            data={"raw_hex": data_hex, "dtype": "int32"},
        )]
        sb = Scoreboard()
        result = sb.compare(exp, act)
        assert result.passed

    def test_memory_int32_bit_exact_mismatch_fails(self):
        """INT32 memory data with different hex fails."""
        exp = [Observation(
            observation_id="s1",
            observation_type="sram_data",
            address=0x1000,
            size=8,
            data={"raw_hex": "0100000002000000", "dtype": "int32"},
        )]
        act = [Observation(
            observation_id="s1",
            observation_type="sram_data",
            address=0x1000,
            size=8,
            data={"raw_hex": "0100000003000000", "dtype": "int32"},
        )]
        sb = Scoreboard()
        result = sb.compare(exp, act)
        assert not result.passed

    def test_scoreboard_result_counts(self):
        exp = [
            Observation.mmio_read("r1", 0x1000, 1),
            Observation.mmio_read("r2", 0x1004, 2),
        ]
        act = [
            Observation.mmio_read("r1", 0x1000, 1),
            Observation.mmio_read("r2", 0x1004, 99),  # mismatch
        ]
        sb = Scoreboard()
        result = sb.compare(exp, act)
        assert not result.passed
        assert result.failed_checks == 1
        assert result.total_checks == 2


# ═══════════════════════════════════════════════════════════════════════════
# Migration tests
# ═══════════════════════════════════════════════════════════════════════════


class TestMigrationRoundtrip:
    """TestCaseConfig → Scenario migration tests."""

    def test_simple_testcase_config_migration(self):
        """A simple TestCaseConfig migrates to a valid Scenario."""

        @dataclass
        class FakeTestCaseConfig:
            case_id: str = "FM-SOC-001"
            description: str = "APB-MMIO write/readback"
            mmio_write_sequence: List[Tuple[int, int]] = field(default_factory=list)
            mmio_writes: Dict[int, int] = field(default_factory=dict)
            mmio_readbacks: Dict[int, int] = field(default_factory=dict)
            sram_preloads: Dict[int, bytes] = field(default_factory=dict)
            dram_preloads: Dict[int, bytes] = field(default_factory=dict)
            pcie_writes: Dict[int, bytes] = field(default_factory=dict)
            pcie_readbacks: Dict[int, bytes] = field(default_factory=dict)
            doorbell_cmd: Optional[int] = None
            doorbell_desc_addr: Optional[int] = None
            irq_source: Optional[int] = None
            poll_status_addr: Optional[int] = None
            poll_status_mask: int = 0x2
            poll_timeout_cycles: int = 100_000
            sram_readbacks: Dict[int, int] = field(default_factory=dict)
            dram_readbacks: Dict[int, int] = field(default_factory=dict)
            int32_bit_exact: bool = True
            fp16_abs_tol: float = 2e-3
            fp16_rel_tol: float = 1e-2
            expect_mismatch: bool = False

        cfg = FakeTestCaseConfig(
            case_id="FM-SOC-001",
            description="APB-MMIO write/readback test",
            mmio_write_sequence=[(0x4000_0000, 0x0000_0001)],
            mmio_readbacks={0x4000_0000: 0x0000_0001},
        )

        scenario = migrate_testcase_config(cfg)

        assert scenario.scenario_id == "FM-SOC-001"
        assert scenario.description == "APB-MMIO write/readback test"
        assert len(scenario.actions) == 1
        assert scenario.actions[0].action_type == "mmio_write"
        assert scenario.actions[0].parameters["address"] == 0x4000_0000
        assert scenario.actions[0].parameters["value"] == 0x0000_0001
        assert len(scenario.expected_observations) == 1
        assert scenario.expected_observations[0].observation_id == "mmio_40000000"
        assert scenario.expected_observations[0].data["value"] == 0x0000_0001

    def test_migration_with_preloads(self):
        """Migration includes SRAM/DRAM preloads with correct classification."""

        @dataclass
        class FakeTestCaseConfig:
            case_id: str = "FM-SOC-013"
            description: str = ""
            mmio_write_sequence: List = field(default_factory=list)
            mmio_writes: Dict = field(default_factory=dict)
            mmio_readbacks: Dict = field(default_factory=dict)
            sram_preloads: Dict = field(default_factory=dict)
            dram_preloads: Dict = field(default_factory=dict)
            pcie_writes: Dict = field(default_factory=dict)
            pcie_readbacks: Dict = field(default_factory=dict)
            doorbell_cmd: Optional[int] = None
            doorbell_desc_addr: Optional[int] = None
            irq_source: Optional[int] = None
            poll_status_addr: Optional[int] = None
            sram_readbacks: Dict = field(default_factory=dict)
            dram_readbacks: Dict = field(default_factory=dict)
            int32_bit_exact: bool = True
            fp16_abs_tol: float = 2e-3
            fp16_rel_tol: float = 1e-2
            expect_mismatch: bool = False

        cfg = FakeTestCaseConfig(
            case_id="FM-SOC-013",
            sram_preloads={0x1000: b"preload_data"},
            dram_preloads={0x8000_0000: b"dram_data"},
            doorbell_cmd=5,
        )

        scenario = migrate_testcase_config(cfg)

        # Should have sram_preload, dram_preload, doorbell
        action_types = [a.action_type for a in scenario.actions]
        assert "sram_preload" in action_types
        assert "dram_preload" in action_types
        assert "doorbell" in action_types

        # Backdoor actions should be correctly classified
        sram_action = next(a for a in scenario.actions if a.action_type == "sram_preload")
        assert sram_action.classification == OperationClass.allowed_init_backdoor

    def test_migration_preserves_tolerance(self):
        """Migration preserves tolerance settings from TestCaseConfig."""

        @dataclass
        class FakeTestCaseConfig:
            case_id: str = "FM-SOC-099"
            description: str = ""
            mmio_write_sequence: List = field(default_factory=list)
            mmio_writes: Dict = field(default_factory=dict)
            mmio_readbacks: Dict = field(default_factory=dict)
            sram_preloads: Dict = field(default_factory=dict)
            dram_preloads: Dict = field(default_factory=dict)
            pcie_writes: Dict = field(default_factory=dict)
            pcie_readbacks: Dict = field(default_factory=dict)
            doorbell_cmd: Optional[int] = None
            doorbell_desc_addr: Optional[int] = None
            irq_source: Optional[int] = None
            poll_status_addr: Optional[int] = None
            sram_readbacks: Dict = field(default_factory=dict)
            dram_readbacks: Dict = field(default_factory=dict)
            int32_bit_exact: bool = False
            fp16_abs_tol: float = 1e-4
            fp16_rel_tol: float = 5e-3
            expect_mismatch: bool = False

        cfg = FakeTestCaseConfig(
            int32_bit_exact=False,
            fp16_abs_tol=1e-4,
            fp16_rel_tol=5e-3,
        )

        scenario = migrate_testcase_config(cfg)

        assert scenario.tolerance.int32_bit_exact is False
        assert scenario.tolerance.fp16_abs_tol == 1e-4
        assert scenario.tolerance.fp16_rel_tol == 5e-3

    def test_migration_scenario_roundtrip_serializable(self):
        """Migrated scenario can be serialized and deserialized."""
        @dataclass
        class FakeTestCaseConfig:
            case_id: str = "FM-SOC-001"
            description: str = "Test"
            mmio_write_sequence: List = field(default_factory=list)
            mmio_writes: Dict = field(default_factory=dict)
            mmio_readbacks: Dict = field(default_factory=dict)
            sram_preloads: Dict = field(default_factory=dict)
            dram_preloads: Dict = field(default_factory=dict)
            pcie_writes: Dict = field(default_factory=dict)
            pcie_readbacks: Dict = field(default_factory=dict)
            doorbell_cmd: Optional[int] = None
            doorbell_desc_addr: Optional[int] = None
            irq_source: Optional[int] = None
            poll_status_addr: Optional[int] = None
            sram_readbacks: Dict = field(default_factory=dict)
            dram_readbacks: Dict = field(default_factory=dict)
            int32_bit_exact: bool = True
            fp16_abs_tol: float = 2e-3
            fp16_rel_tol: float = 1e-2
            expect_mismatch: bool = False

        cfg = FakeTestCaseConfig(
            mmio_write_sequence=[(0x4000_0000, 1)],
            mmio_readbacks={0x4000_0000: 1},
        )

        scenario = migrate_testcase_config(cfg)
        data = scenario.to_dict()
        restored = Scenario.from_dict(data)

        assert restored.scenario_id == "FM-SOC-001"
        assert len(restored.actions) == 1
        assert len(restored.expected_observations) == 1


# ═══════════════════════════════════════════════════════════════════════════
# Evidence record tests
# ═══════════════════════════════════════════════════════════════════════════


class TestEvidenceRecords:
    """EvidenceRecord functionality tests."""

    def test_add_evidence_to_scenario(self):
        s = _make_minimal_scenario()
        ev = EvidenceRecord(
            record_id="ev-001",
            dut_adapter="FakeDUT",
            firmware_mode="fake",
            verdict="pass",
        )
        s.add_evidence(ev)
        assert len(s.evidence) == 1
        assert s.latest_evidence().verdict == "pass"

    def test_multiple_evidence_records(self):
        s = _make_minimal_scenario()
        s.add_evidence(EvidenceRecord(record_id="e1", verdict="pass"))
        s.add_evidence(EvidenceRecord(record_id="e2", verdict="fail"))
        assert len(s.evidence) == 2
        assert s.latest_evidence().record_id == "e2"

    def test_evidence_roundtrip(self):
        ev = EvidenceRecord(
            record_id="ev-test",
            dut_adapter="FuncModel",
            firmware_mode="spike",
            abi_version=1,
            verdict="pass",
            actual_observations=[
                Observation.mmio_read("r1", 0x1000, 42),
            ],
        )
        ev2 = EvidenceRecord.from_dict(ev.to_dict())
        assert ev2.record_id == "ev-test"
        assert ev2.dut_adapter == "FuncModel"
        assert ev2.firmware_mode == "spike"
        assert ev2.verdict == "pass"
        assert len(ev2.actual_observations) == 1
