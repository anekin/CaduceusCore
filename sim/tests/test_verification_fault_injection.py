"""Fault injection verification tests (Todo 13).

Tests deterministic fault injection through FuncModelAdapter hooks,
scoreboard fault classification, and negative-path verification.

Coverage:
    - FaultInjector unit tests (data corruption, descriptor modification)
    - Scoreboard.classify_faults() detection tests
    - FuncModelAdapter integration (11 fault classes, injection_applied=True)
    - Negative: injection_not_applied_is_failure (detector dependency)
    - Negative: normal scenarios unaffected by fault hooks being disabled
    - All 11 fault classes: data_corruption, wrong_descriptor,
      unsupported_opcode, ring_overflow, stalled_head, wrong_completion,
      dropped_interrupt, duplicated_interrupt, timeout, engine_error,
      reset_during_command
"""

import asyncio

import pytest

from sim.verification import (
    Action,
    EvidenceRecord,
    Observation,
    Scenario,
    Scoreboard,
    ToleranceConfig,
    FaultClass,
    FaultInjector,
    FaultInjectionRecord,
)
from sim.verification.differential import (
    MemoryGoldenOracle,
    run_differential_scenario,
)
from sim.verification.dut_adapter import (
    DUTConnectionError,
    DUTTimeoutError,
)
from sim.verification.fm_adapter import FuncModelAdapter
from sim.verification.observation import ObservationType
from sim.verification.operation_classifier import OperationClass


def async_test(coro):
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════
# FaultInjector unit tests
# ═══════════════════════════════════════════════════════════════════════════


class TestFaultInjectorUnit:
    """Unit tests for FaultInjector data manipulation methods."""

    def test_inject_data_corruption_modifies_bytes(self):
        injector = FaultInjector()
        injector.enable_fault(FaultClass.data_corruption, offset=0, count=4)
        original = b"\x01\x02\x03\x04\x05\x06\x07\x08"
        corrupted = injector.inject_data_corruption(original)
        assert corrupted != original
        assert len(corrupted) == len(original)
        assert corrupted[4:] == original[4:]  # Only first 4 bytes changed

    def test_inject_data_corruption_disabled_returns_original(self):
        injector = FaultInjector()
        original = b"\x01\x02\x03\x04"
        result = injector.inject_data_corruption(original)
        assert result == original

    def test_inject_wrong_descriptor_modifies_opcode_and_addr(self):
        injector = FaultInjector()
        injector.enable_fault(
            FaultClass.wrong_descriptor, wrong_opcode=0x99, wrong_addr=0xDEAD_BEEF
        )
        opcode, addr = injector.inject_wrong_descriptor(0x01, 0x8000_0000)
        assert opcode == 0x99
        assert addr == 0xDEAD_BEEF

    def test_inject_unsupported_opcode_returns_ff(self):
        injector = FaultInjector()
        injector.enable_fault(FaultClass.unsupported_opcode)
        opcode = injector.inject_unsupported_opcode(0x05)
        assert opcode == 0xFF

    def test_inject_ring_overflow_exceeds_capacity(self):
        injector = FaultInjector()
        injector.enable_fault(FaultClass.ring_overflow, overflow_by=5)
        tail = injector.inject_ring_overflow(0, ring_size=16)
        assert tail == 16 + 5  # ring_size + overflow_by = 21

    def test_inject_wrong_completion_returns_dead(self):
        injector = FaultInjector()
        injector.enable_fault(FaultClass.wrong_completion)
        status = injector.inject_wrong_completion(0x02)
        assert status == 0xDEAD

    def test_inject_dropped_interrupt_returns_true(self):
        injector = FaultInjector()
        injector.enable_fault(FaultClass.dropped_interrupt)
        assert injector.inject_dropped_interrupt() is True

    def test_inject_duplicated_interrupt_returns_true(self):
        injector = FaultInjector()
        injector.enable_fault(FaultClass.duplicated_interrupt)
        assert injector.inject_duplicated_interrupt() is True

    def test_fault_disabled_by_default(self):
        injector = FaultInjector()
        assert injector.is_active(FaultClass.data_corruption) is False
        assert injector.is_active(FaultClass.timeout) is False
        assert injector.any_injection_applied is False

    def test_enable_disable_fault_cycle(self):
        injector = FaultInjector()
        injector.enable_fault(FaultClass.timeout)
        assert injector.is_active(FaultClass.timeout) is True
        injector.disable_fault(FaultClass.timeout)
        assert injector.is_active(FaultClass.timeout) is False

    def test_disable_all_clears_all_faults(self):
        injector = FaultInjector()
        injector.enable_fault(FaultClass.data_corruption)
        injector.enable_fault(FaultClass.timeout)
        injector.disable_all()
        assert injector.is_active(FaultClass.data_corruption) is False
        assert injector.is_active(FaultClass.timeout) is False

    def test_record_injection_sets_applied_true(self):
        injector = FaultInjector()
        injector.enable_fault(FaultClass.timeout)
        injector.record_injection(FaultClass.timeout)
        assert injector.any_injection_applied is True
        records = injector.flush_records()
        assert len(records) == 1
        assert records[0].injection_applied is True
        assert records[0].fault_class == "timeout"

    def test_one_shot_injection_disables_after_record(self):
        injector = FaultInjector()
        injector.enable_fault(FaultClass.timeout)
        injector.record_injection(FaultClass.timeout)
        assert injector.is_active(FaultClass.timeout) is False


# ═══════════════════════════════════════════════════════════════════════════
# Scoreboard fault classification tests
# ═══════════════════════════════════════════════════════════════════════════


class TestScoreboardFaultClassification:
    """Scoreboard.classify_faults() detection tests."""

    def test_classify_wrong_completion_from_status(self):
        obs = [
            Observation(
                observation_id="comp",
                observation_type=ObservationType.completion_status,
                data={"status": 0xDEAD},  # Not 0 or 0x2
            )
        ]
        faults = Scoreboard.classify_faults(obs)
        assert "engine_error" in faults

    def test_classify_wrong_completion_unexpected_status(self):
        obs = [
            Observation(
                observation_id="comp",
                observation_type=ObservationType.completion_status,
                data={"status": 0x0F},  # Unexpected value, not 0xDEAD
            )
        ]
        faults = Scoreboard.classify_faults(obs)
        assert "wrong_completion" in faults

    def test_classify_data_corruption_from_marker(self):
        obs = [
            Observation(
                observation_id="sram",
                observation_type=ObservationType.sram_data,
                data={"raw_hex": "__DATA_CORRUPTED__"},
            )
        ]
        faults = Scoreboard.classify_faults(obs)
        assert "data_corruption" in faults

    def test_classify_timeout_from_marker(self):
        obs = [
            Observation(
                observation_id="t1",
                observation_type=ObservationType.timing_measurement,
                data={"timeout": True},
            )
        ]
        faults = Scoreboard.classify_faults(obs)
        assert "timeout" in faults

    def test_classify_dropped_interrupt_from_marker(self):
        obs = [
            Observation(
                observation_id="irq",
                observation_type=ObservationType.interrupt_status,
                data={"irq_dropped": True},
            )
        ]
        faults = Scoreboard.classify_faults(obs)
        assert "dropped_interrupt" in faults

    def test_classify_duplicated_interrupt_from_marker(self):
        obs = [
            Observation(
                observation_id="irq",
                observation_type=ObservationType.interrupt_status,
                data={"interrupt_duplicate": True},
            )
        ]
        faults = Scoreboard.classify_faults(obs)
        assert "duplicated_interrupt" in faults

    def test_classify_wrong_descriptor_from_marker(self):
        obs = [
            Observation(
                observation_id="desc",
                observation_type=ObservationType.generic,
                data={"desc_error": True},
            )
        ]
        faults = Scoreboard.classify_faults(obs)
        assert "wrong_descriptor" in faults

    def test_classify_unsupported_opcode_from_marker(self):
        obs = [
            Observation(
                observation_id="op",
                observation_type=ObservationType.generic,
                data={"opcode_error": True},
            )
        ]
        faults = Scoreboard.classify_faults(obs)
        assert "unsupported_opcode" in faults

    def test_classify_ring_overflow_from_marker(self):
        obs = [
            Observation(
                observation_id="ring",
                observation_type=ObservationType.generic,
                data={"ring_overflow": True},
            )
        ]
        faults = Scoreboard.classify_faults(obs)
        assert "ring_overflow" in faults

    def test_classify_stalled_head_from_marker(self):
        obs = [
            Observation(
                observation_id="head",
                observation_type=ObservationType.generic,
                data={"head_stalled": True},
            )
        ]
        faults = Scoreboard.classify_faults(obs)
        assert "stalled_head" in faults

    def test_classify_reset_during_command_from_marker(self):
        obs = [
            Observation(
                observation_id="reset",
                observation_type=ObservationType.generic,
                data={"reset_during_cmd": True},
            )
        ]
        faults = Scoreboard.classify_faults(obs)
        assert "reset_during_command" in faults

    def test_clean_observations_produce_no_faults(self):
        obs = [
            Observation.mmio_read("r1", 0x4000_0000, 42),
            Observation.completion("c1", 0x2),
        ]
        faults = Scoreboard.classify_faults(obs)
        assert len(faults) == 0

    def test_metadata_faults_also_detected(self):
        obs = [
            Observation(
                observation_id="meta",
                observation_type=ObservationType.generic,
                metadata={"timeout": True},
            )
        ]
        faults = Scoreboard.classify_faults(obs)
        assert "timeout" in faults


# ═══════════════════════════════════════════════════════════════════════════
# FuncModelAdapter fault injection integration tests
# ═══════════════════════════════════════════════════════════════════════════


class TestFuncModelAdapterFaultInjection:
    """Tests that verify fault injection through FuncModelAdapter."""

    @staticmethod
    async def _run_fault_scenario(fault_class, enable_kwargs, actions, observations):
        adapter = FuncModelAdapter(firmware_mode="python")
        await adapter.connect()

        adapter.enable_fault(fault_class, **enable_kwargs)

        for action in actions:
            try:
                await adapter.execute_action(action)
            except (DUTTimeoutError, ValueError):
                pass

        actual_obs = []
        for spec in observations:
            try:
                obs = await adapter.observe(spec)
                actual_obs.append(obs)
            except Exception:
                pass

        evidence = adapter.evidence_metadata()
        await adapter.disconnect()

        return evidence, actual_obs

    # ── Data corruption tests ──────────────────────────────────────────

    @staticmethod
    async def _test_data_corruption_sram_preload_injected():
        data = b"\x01" * 16
        s = Scenario(
            scenario_id="corrupt-sram",
            actions=[Action.sram_preload(0x200, data)],
            expected_observations=[
                Observation.sram_readback("sram", 0x200, 16),
            ],
        )
        evidence, obs = await TestFuncModelAdapterFaultInjection._run_fault_scenario(
            FaultClass.data_corruption,
            {"offset": 0, "count": 4},
            s.actions,
            s.expected_observations,
        )
        assert evidence["injection_applied"] is True
        records = evidence.get("fault_injection_records", [])
        assert any(r["fault_class"] == "data_corruption" for r in records)
        assert any(r["injection_applied"] for r in records)

    def test_data_corruption_sram_preload_injected(self):
        asyncio.run(self._test_data_corruption_sram_preload_injected())

    @staticmethod
    async def _test_data_corruption_sram_readback_injected():
        data = b"\x02" * 32
        actions = [Action.sram_preload(0x300, data), Action.sram_readback(0x300, 32)]
        observations = [Observation.sram_readback("sram", 0x300, 32)]
        evidence, obs = await TestFuncModelAdapterFaultInjection._run_fault_scenario(
            FaultClass.data_corruption,
            {"offset": 0, "count": 8},
            actions,
            observations,
        )
        assert evidence["injection_applied"] is True

    def test_data_corruption_sram_readback_injected(self):
        asyncio.run(self._test_data_corruption_sram_readback_injected())

    # ── Wrong descriptor tests ─────────────────────────────────────────

    @staticmethod
    async def _test_wrong_descriptor_injected():
        actions = [
            Action(
                action_type="doorbell",
                classification=OperationClass.frontdoor,
                parameters={"host_tail": 1, "opcode": 5, "desc_addr": 0x80001000},
            )
        ]
        evidence, obs = await TestFuncModelAdapterFaultInjection._run_fault_scenario(
            FaultClass.wrong_descriptor,
            {"wrong_opcode": 0x88, "wrong_addr": 0x90000000},
            actions,
            [],
        )
        assert evidence["injection_applied"] is True
        records = evidence.get("fault_injection_records", [])
        assert any(r["fault_class"] == "wrong_descriptor" for r in records)

    def test_wrong_descriptor_injected(self):
        asyncio.run(self._test_wrong_descriptor_injected())

    # ── Unsupported opcode test ────────────────────────────────────────

    @staticmethod
    async def _test_unsupported_opcode_injected():
        actions = [
            Action(
                action_type="doorbell",
                classification=OperationClass.frontdoor,
                parameters={"host_tail": 1, "opcode": 5, "desc_addr": 0x80001000},
            )
        ]
        evidence, obs = await TestFuncModelAdapterFaultInjection._run_fault_scenario(
            FaultClass.unsupported_opcode,
            {},
            actions,
            [],
        )
        assert evidence["injection_applied"] is True

    def test_unsupported_opcode_injected(self):
        asyncio.run(self._test_unsupported_opcode_injected())

    # ── Ring overflow test ─────────────────────────────────────────────

    @staticmethod
    async def _test_ring_overflow_injected():
        actions = [Action.doorbell(host_tail=1)]
        evidence, obs = await TestFuncModelAdapterFaultInjection._run_fault_scenario(
            FaultClass.ring_overflow,
            {"overflow_by": 10},
            actions,
            [],
        )
        assert evidence["injection_applied"] is True

    def test_ring_overflow_injected(self):
        asyncio.run(self._test_ring_overflow_injected())

    # ── Wrong completion test ──────────────────────────────────────────

    @staticmethod
    async def _test_wrong_completion_injected():
        actions = [Action.mmio_write(0x4000_0000, 0x01)]
        observations = [Observation.completion("comp", 0x2)]
        evidence, obs = await TestFuncModelAdapterFaultInjection._run_fault_scenario(
            FaultClass.wrong_completion,
            {"wrong_status": 0x0F},
            actions,
            observations,
        )
        assert evidence["injection_applied"] is True
        faults = Scoreboard.classify_faults(obs)
        assert "wrong_completion" in faults

    def test_wrong_completion_injected(self):
        asyncio.run(self._test_wrong_completion_injected())

    # ── Engine error test ──────────────────────────────────────────────

    @staticmethod
    async def _test_engine_error_injected():
        actions = [Action.mmio_write(0x4000_0000, 0x01)]
        observations = [Observation.completion("comp", 0x2)]
        evidence, obs = await TestFuncModelAdapterFaultInjection._run_fault_scenario(
            FaultClass.engine_error,
            {},
            actions,
            observations,
        )
        assert evidence["injection_applied"] is True
        faults = Scoreboard.classify_faults(obs)
        assert "engine_error" in faults

    def test_engine_error_injected(self):
        asyncio.run(self._test_engine_error_injected())

    # ── Timeout test ───────────────────────────────────────────────────

    @staticmethod
    async def _test_timeout_injected():
        actions = [Action.poll_status(0x4000_0008, mask=0x2)]
        evidence, obs = await TestFuncModelAdapterFaultInjection._run_fault_scenario(
            FaultClass.timeout,
            {},
            actions,
            [],
        )
        assert evidence["injection_applied"] is True

    def test_timeout_injected(self):
        asyncio.run(self._test_timeout_injected())

    # ── Reset during command test ──────────────────────────────────────

    @staticmethod
    async def _test_reset_during_command_injected():
        actions = [Action.mmio_write(0x4000_0000, 0x01)]
        evidence, obs = await TestFuncModelAdapterFaultInjection._run_fault_scenario(
            FaultClass.reset_during_command,
            {},
            actions,
            [],
        )
        assert evidence["injection_applied"] is True

    def test_reset_during_command_injected(self):
        asyncio.run(self._test_reset_during_command_injected())

    # ── Dropped interrupt test ─────────────────────────────────────────

    def test_dropped_interrupt_injected(self):
        asyncio.run(self._test_dropped_interrupt_injected())

    @staticmethod
    async def _test_dropped_interrupt_injected():
        adapter = FuncModelAdapter(firmware_mode="python")
        await adapter.connect()
        # Use full doorbell with valid opcode and dummy descriptor address
        await adapter.execute_action(
            Action(
                action_type="doorbell",
                classification=OperationClass.frontdoor,
                parameters={"host_tail": 1, "opcode": 1, "desc_addr": 0x80000000},
            )
        )
        adapter.enable_fault(FaultClass.dropped_interrupt)

        try:
            await adapter.execute_action(Action.wait_irq(source=8))
        except DUTTimeoutError:
            pass

        evidence = adapter.evidence_metadata()
        await adapter.disconnect()
        assert evidence["injection_applied"] is True

    # ── Duplicated interrupt test ──────────────────────────────────────

    def test_duplicated_interrupt_injected(self):
        asyncio.run(self._test_duplicated_interrupt_injected())

    @staticmethod
    async def _test_duplicated_interrupt_injected():
        adapter = FuncModelAdapter(firmware_mode="python")
        await adapter.connect()
        await adapter.execute_action(
            Action(
                action_type="doorbell",
                classification=OperationClass.frontdoor,
                parameters={"host_tail": 1, "opcode": 1, "desc_addr": 0x80000000},
            )
        )
        adapter.enable_fault(FaultClass.duplicated_interrupt)

        await adapter.execute_action(Action.wait_irq(source=8))

        evidence = adapter.evidence_metadata()
        await adapter.disconnect()
        assert evidence["injection_applied"] is True

    # ── Stalled head test ──────────────────────────────────────────────

    def test_stalled_head_injected(self):
        asyncio.run(self._test_stalled_head_injected())

    @staticmethod
    async def _test_stalled_head_injected():
        adapter = FuncModelAdapter(firmware_mode="python")
        await adapter.connect()
        await adapter.execute_action(
            Action(
                action_type="doorbell",
                classification=OperationClass.frontdoor,
                parameters={"host_tail": 1, "opcode": 1, "desc_addr": 0x80000000},
            )
        )
        adapter.enable_fault(FaultClass.stalled_head)

        await adapter.execute_action(Action.wait_irq(source=8))

        evidence = adapter.evidence_metadata()
        await adapter.disconnect()
        assert evidence["injection_applied"] is True


# ═══════════════════════════════════════════════════════════════════════════
# Negative: injection_not_applied_is_failure
# ═══════════════════════════════════════════════════════════════════════════


class TestInjectionNotAppliedIsFailure:
    """Negative tests: verifying that without injection, scenarios pass
    normally and fault detection produces empty results.

    Removing the Scoreboard.classify_faults detector from the codebase
    MUST make test_injection_not_applied_is_failure fail.
    """

    def test_clean_scenario_no_fault_detected(self):
        obs = [
            Observation.mmio_read("r1", 0x4000_0000, 42),
            Observation.completion("c1", 0x2),
        ]
        faults = Scoreboard.classify_faults(obs)
        assert len(faults) == 0

    def test_classify_faults_method_exists(self):
        assert hasattr(Scoreboard, "classify_faults"), (
            "Detector removed! Scoreboard.classify_faults must exist. "
            "Removing it makes this test fail."
        )
        assert callable(Scoreboard.classify_faults)

    def test_normal_scenario_without_fault_injection_passes(self):
        asyncio.run(self._test_normal_scenario_without_fault_injection_passes())

    @staticmethod
    async def _test_normal_scenario_without_fault_injection_passes():
        adapter = FuncModelAdapter(firmware_mode="python")
        await adapter.connect()

        await adapter.execute_action(Action.mmio_write(0x4000_0000, 0x00000001))
        obs = await adapter.observe(
            Observation.mmio_read("mxu_ctrl", 0x4000_0000, 0x00000001)
        )
        evidence = adapter.evidence_metadata()
        await adapter.disconnect()

        assert evidence["injection_applied"] is False
        assert obs.data["value"] == 0x00000001

    def test_disabled_fault_does_not_inject(self):
        asyncio.run(self._test_disabled_fault_does_not_inject())

    @staticmethod
    async def _test_disabled_fault_does_not_inject():
        adapter = FuncModelAdapter(firmware_mode="python")
        await adapter.connect()

        adapter.enable_fault(FaultClass.data_corruption, offset=0, count=4)
        adapter.disable_fault(FaultClass.data_corruption)

        data = b"\x01" * 16
        await adapter.execute_action(Action.sram_preload(0x100, data))
        obs = await adapter.observe(Observation.sram_readback("sram", 0x100, 16))
        evidence = adapter.evidence_metadata()
        await adapter.disconnect()

        assert evidence["injection_applied"] is False
        assert obs.data["raw_hex"] == data.hex()


# ═══════════════════════════════════════════════════════════════════════════
# Fault hooks not reachable from public API
# ═══════════════════════════════════════════════════════════════════════════


class TestFaultHooksNotInPublicAPI:
    """Verify that fault hooks are NOT reachable via public Runtime API."""

    def test_runtime_h_has_no_fault_injection_references(self):
        import re

        runtime_path = "software/include/caduceus/runtime.h"
        with open(runtime_path) as f:
            content = f.read()

        forbidden = [
            "fault",
            "inject",
            "corrupt",
            "FaultClass",
            "FaultInjector",
        ]
        for term in forbidden:
            matches = re.findall(rf"\b{term}\b", content, re.IGNORECASE)
            assert len(matches) == 0, (
                f"Forbidden term '{term}' found in public runtime.h: {matches}"
            )

    def test_runtime_hpp_has_no_fault_injection_references(self):
        import re

        runtime_path = "software/include/caduceus/runtime.hpp"
        with open(runtime_path) as f:
            content = f.read()

        forbidden = [
            "fault",
            "inject",
            "corrupt",
            "FaultClass",
            "FaultInjector",
        ]
        for term in forbidden:
            matches = re.findall(rf"\b{term}\b", content, re.IGNORECASE)
            assert len(matches) == 0, (
                f"Forbidden term '{term}' found in public runtime.hpp: {matches}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# Full fault classification coverage verification
# ═══════════════════════════════════════════════════════════════════════════


class TestFullFaultClassificationCoverage:
    """Verify all 11 fault classes are covered."""

    EXPECTED_FAULTS = {
        "data_corruption",
        "wrong_descriptor",
        "unsupported_opcode",
        "ring_overflow",
        "stalled_head",
        "wrong_completion",
        "dropped_interrupt",
        "duplicated_interrupt",
        "timeout",
        "engine_error",
        "reset_during_command",
    }

    def test_all_fault_classes_defined(self):
        defined = {f.value for f in FaultClass}
        assert defined == self.EXPECTED_FAULTS

    def test_all_fault_classes_in_scoreboard_classifier(self):
        import inspect

        source = inspect.getsource(Scoreboard.classify_faults)
        for fault_name in self.EXPECTED_FAULTS:
            assert fault_name in source, (
                f"Fault class '{fault_name}' not handled in Scoreboard.classify_faults"
            )

    def test_all_fault_classes_envidence_metadata_compatible(self):
        for fault_name in self.EXPECTED_FAULTS:
            record = FaultInjectionRecord(
                fault_class=fault_name,
                injection_applied=True,
            )
            assert record.fault_class == fault_name


# ═══════════════════════════════════════════════════════════════════════════
# Anti-vacuity scenarios (W4-T4 / Todo 19)
# ═══════════════════════════════════════════════════════════════════════════


class TestAntiVacuityGate:
    """Anti-vacuity tests: prove the detector actually fired, not just
    that the mutation was applied.

    Scenario A: no-fault → no false positive.
    Scenario B: corruption injected, wrong detector specified → FAIL.
    Scenario C: corruption injected, correct detector specified → PASS.
    """

    @staticmethod
    async def _run_differential(adapter, scenario, oracle, inputs):
        return await run_differential_scenario(adapter, scenario, oracle, inputs)

    @staticmethod
    def _make_adapter():
        return FuncModelAdapter(firmware_mode="python")

    def test_anti_vacuity_no_fault_no_false_positive(self):
        """Scenario A: no fault injection → scoreboard passes → anti-vacuity gate PASS."""
        data = b"\xAA\xBB\xCC\xDD" * 4
        offset = 0x1000
        scenario = Scenario(
            scenario_id="anti-vacuity-no-fault",
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
            metadata={"expected_detector": "no_fault"},
        )
        inputs = {
            "oracle": "memory",
            "expected_specs": [{
                "observation_id": "sram_data",
                "observation_type": "sram_data",
                "address": offset,
                "size": len(data),
                "raw_hex": data.hex(),
                "dtype": "int32",
            }],
        }

        async def _run():
            adapter = self._make_adapter()
            await adapter.connect()
            try:
                report = await self._run_differential(
                    adapter, scenario, MemoryGoldenOracle(), inputs
                )
                return report
            finally:
                await adapter.disconnect()

        report = asyncio.run(_run())

        assert report.gate_pass is True
        assert report.injection_applied is False
        assert report.expected_detector == "no_fault"
        assert report.detection_hit is True
        assert report.detector_failure_reason == ""
        assert report.scoreboard_result.passed is True

    def test_anti_vacuity_corruption_wrong_detector_fails(self):
        """Scenario B: data corruption injected, wrong detector specified → FAIL."""
        data = b"\x01\x02\x03\x04" * 4
        offset = 0x2000
        scenario = Scenario(
            scenario_id="anti-vacuity-wrong-detector",
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
                "expected_detector": "wrong_completion",
            },
        )
        inputs = {
            "oracle": "memory",
            "expected_specs": [{
                "observation_id": "sram_data",
                "observation_type": "sram_data",
                "address": offset,
                "size": len(data),
                "raw_hex": data.hex(),
                "dtype": "int32",
            }],
        }

        async def _run():
            adapter = self._make_adapter()
            await adapter.connect()
            try:
                import sys
                adapter.enable_fault(FaultClass.data_corruption, offset=0, count=4)
                report = await self._run_differential(
                    adapter, scenario, MemoryGoldenOracle(), inputs
                )
                return report
            finally:
                await adapter.disconnect()

        report = asyncio.run(_run())

        assert report.gate_pass is False, "Wrong detector should cause gate fail"
        assert report.injection_applied is True
        assert report.expected_detector == "wrong_completion"
        assert report.detection_hit is False
        assert report.detector_failure_reason is not None
        assert "wrong detector" in report.detector_failure_reason
        assert len(report.divergences) > 0

    def test_anti_vacuity_corruption_correct_detector_passes(self):
        """Scenario C: data corruption injected, correct detector specified → PASS."""
        data = b"\x11\x22\x33\x44" * 4
        offset = 0x3000
        scenario = Scenario(
            scenario_id="anti-vacuity-correct-detector",
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
                "expected_detector": "scoreboard_mismatch",
            },
        )
        inputs = {
            "oracle": "memory",
            "expected_specs": [{
                "observation_id": "sram_data",
                "observation_type": "sram_data",
                "address": offset,
                "size": len(data),
                "raw_hex": data.hex(),
                "dtype": "int32",
            }],
        }

        async def _run():
            adapter = self._make_adapter()
            await adapter.connect()
            try:
                adapter.enable_fault(FaultClass.data_corruption, offset=0, count=4)
                report = await self._run_differential(
                    adapter, scenario, MemoryGoldenOracle(), inputs
                )
                return report
            finally:
                await adapter.disconnect()

        report = asyncio.run(_run())

        assert report.gate_pass is True, "Correct detector should make gate pass"
        assert report.injection_applied is True
        assert report.expected_detector == "scoreboard_mismatch"
        assert report.detection_hit is True
        assert report.detector_failure_reason == ""
