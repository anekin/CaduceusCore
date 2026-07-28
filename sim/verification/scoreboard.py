"""Scoreboard — compares expected observations to actual observations.

The scoreboard takes expected observations (from the scenario) and actual
observations (from the DUT adapter) and produces a comparison result.

Critical design rule: the scoreboard MUST NOT read expected output from
the DUT under test. Expected values come only from the scenario's
expected_observations field, never from the DUT adapter.

Todo 13: scoreboard can classify fault symptoms from observations.
This classification operates only on Observation objects and has zero
DUT-specific knowledge.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

import numpy as np

from sim.verification.observation import Observation, ObservationType
from sim.verification.tolerance import ToleranceConfig


@dataclass
class ScoreboardResult:
    """Result of comparing expected vs actual observations.

    Attributes:
        passed: Whether all comparisons passed.
        total_checks: Number of individual comparison checks performed.
        passed_checks: Number of checks that passed.
        failed_checks: Number of checks that failed.
        failures: List of failure messages with details.
        metadata: Arbitrary key-value metadata about the comparison.
    """

    passed: bool = True
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    failures: List[dict] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_failure(self, observation_id: str, message: str, **details) -> None:
        """Record a single comparison failure."""
        self.passed = False
        self.failed_checks += 1
        failure: dict[str, Any] = {
            "observation_id": observation_id,
            "message": message,
        }
        failure.update(details)
        self.failures.append(failure)

    def add_pass(self) -> None:
        """Record a single passing check."""
        self.total_checks += 1
        self.passed_checks += 1


class Scoreboard:
    """Compare expected observations to actual observations.

    Usage:
        scoreboard = Scoreboard(tolerance=ToleranceConfig())
        result = scoreboard.compare(expected_observations, actual_observations)
        assert result.passed

    The scoreboard never reads from the DUT. All data it compares comes
    from the scenario's expected_observations and the actual_observations
    return from the adapter.
    """

    def __init__(self, tolerance: Optional[ToleranceConfig] = None):
        """Initialize the scoreboard with default tolerance."""
        self._tolerance = tolerance or ToleranceConfig()

    def compare(
        self,
        expected: List[Observation],
        actual: List[Observation],
    ) -> ScoreboardResult:
        """Compare expected and actual observations.

        Observations are matched by observation_id. Each expected
        observation is compared to the corresponding actual observation
        by ID. Expected observations without a matching actual are
        reported as failures.

        Args:
            expected: Expected observations from the scenario.
            actual: Actual observations from the DUT adapter.

        Returns:
            A ScoreboardResult with the comparison outcome.
        """
        result = ScoreboardResult()

        # Index actual observations by ID
        actual_by_id: Dict[str, Observation] = {
            o.observation_id: o for o in actual
        }

        for exp in expected:
            obs_id = exp.observation_id

            if obs_id not in actual_by_id:
                result.add_failure(
                    obs_id,
                    f"Expected observation '{obs_id}' not found in actual results",
                )
                continue

            act = actual_by_id[obs_id]
            tol = exp.tolerance or self._tolerance

            self._compare_single(exp, act, tol, result)

        result.total_checks = result.passed_checks + result.failed_checks
        result.metadata["comparison_tolerance"] = self._tolerance.to_dict()
        return result

    def _compare_single(
        self,
        expected: Observation,
        actual: Observation,
        tolerance: ToleranceConfig,
        result: ScoreboardResult,
    ) -> None:
        """Compare a single expected vs actual observation pair.

        Dispatches to type-specific comparison methods based on
        observation_type.
        """
        obs_type = expected.observation_type
        obs_id = expected.observation_id

        if obs_type == ObservationType.mmio_value:
            self._compare_mmio_value(expected, actual, tolerance, obs_id, result)
        elif obs_type == ObservationType.completion_status:
            self._compare_completion(expected, actual, tolerance, obs_id, result)
        elif obs_type in (ObservationType.sram_data, ObservationType.dram_data, ObservationType.pcie_readback):
            self._compare_memory_data(expected, actual, tolerance, obs_id, result)
        else:
            # Generic comparison: compare data dicts
            self._compare_data_dicts(expected, actual, obs_id, result)

    # ── Type-specific comparison helpers ────────────────────────────────

    @staticmethod
    def _compare_mmio_value(
        expected: Observation,
        actual: Observation,
        tolerance: ToleranceConfig,
        obs_id: str,
        result: ScoreboardResult,
    ) -> None:
        """Compare MMIO register values."""
        exp_val = expected.data.get("value")
        act_val = actual.data.get("value")

        if exp_val is None:
            result.add_failure(obs_id, "Expected value not specified")
            return

        if act_val is None:
            result.add_failure(obs_id, "Actual value not observed")
            return

        if exp_val == act_val:
            result.add_pass()
        else:
            result.add_failure(
                obs_id,
                f"MMIO value mismatch",
                expected=exp_val,
                actual=act_val,
            )

    @staticmethod
    def _compare_completion(
        expected: Observation,
        actual: Observation,
        tolerance: ToleranceConfig,
        obs_id: str,
        result: ScoreboardResult,
    ) -> None:
        """Compare completion status values."""
        exp_status = expected.data.get("status")
        act_status = actual.data.get("status")

        if exp_status is None:
            result.add_failure(obs_id, "Expected status not specified")
            return

        if act_status is None:
            result.add_failure(obs_id, "Actual status not observed")
            return

        if exp_status == act_status:
            result.add_pass()
        else:
            result.add_failure(
                obs_id,
                f"Completion status mismatch",
                expected=exp_status,
                actual=act_status,
            )

    @staticmethod
    def _compare_memory_data(
        expected: Observation,
        actual: Observation,
        tolerance: ToleranceConfig,
        obs_id: str,
        result: ScoreboardResult,
    ) -> None:
        """Compare memory data observations.

        Handles INT32 bit-exact and FP16 tolerance-based comparisons.
        """
        dtype = actual.data.get("dtype", "int32")
        exp_hex = expected.data.get("raw_hex")
        act_hex = actual.data.get("raw_hex")

        if not exp_hex or not act_hex:
            result.add_failure(obs_id, "Missing raw_hex data for comparison")
            return

        if exp_hex == act_hex:
            result.add_pass()
            return

        # If not identical hex, try numerical comparison
        try:
            exp_bytes = bytes.fromhex(exp_hex)
            act_bytes = bytes.fromhex(act_hex)

            if dtype == "int32" and tolerance.int32_bit_exact:
                # INT32 bit-exact: even one byte difference is a failure
                mismatch_count = sum(
                    1 for a, b in zip(exp_bytes, act_bytes) if a != b
                )
                # Account for length differences
                len_diff = abs(len(exp_bytes) - len(act_bytes))
                result.add_failure(
                    obs_id,
                    f"INT32 bit-exact mismatch ({mismatch_count + len_diff} bytes differ)",
                    expected_hex=exp_hex[:64],
                    actual_hex=act_hex[:64],
                )
                return

            if dtype in ("fp16", "float16"):
                exp_arr = np.frombuffer(exp_bytes, dtype=np.float16)
                act_arr = np.frombuffer(act_bytes, dtype=np.float16)
                min_len = min(len(exp_arr), len(act_arr))
                if min_len == 0:
                    result.add_failure(obs_id, "Empty FP16 arrays")
                    return

                diff = np.abs(exp_arr[:min_len].astype(np.float64)
                              - act_arr[:min_len].astype(np.float64))
                rel_diff = diff / (np.abs(exp_arr[:min_len].astype(np.float64)) + 1e-10)

                abs_fail = diff > tolerance.fp16_abs_tol
                rel_fail = rel_diff > tolerance.fp16_rel_tol
                fail_mask = abs_fail & rel_fail

                if np.any(fail_mask):
                    fail_count = int(np.sum(fail_mask))
                    max_diff = float(np.max(diff))
                    result.add_failure(
                        obs_id,
                        f"FP16 mismatch: {fail_count}/{min_len} elements fail",
                        max_abs_diff=max_diff,
                        tolerance_abs=tolerance.fp16_abs_tol,
                        tolerance_rel=tolerance.fp16_rel_tol,
                    )
                else:
                    result.add_pass()
                return

            if dtype in ("fp32", "float32"):
                exp_arr = np.frombuffer(exp_bytes, dtype=np.float32)
                act_arr = np.frombuffer(act_bytes, dtype=np.float32)
                min_len = min(len(exp_arr), len(act_arr))
                if min_len == 0:
                    result.add_failure(obs_id, "Empty FP32 arrays")
                    return

                diff = np.abs(exp_arr[:min_len] - act_arr[:min_len])
                rel_diff = diff / (np.abs(exp_arr[:min_len]) + 1e-10)

                abs_fail = diff > tolerance.fp32_abs_tol
                rel_fail = rel_diff > tolerance.fp32_rel_tol
                fail_mask = abs_fail & rel_fail

                if np.any(fail_mask):
                    fail_count = int(np.sum(fail_mask))
                    result.add_failure(
                        obs_id,
                        f"FP32 mismatch: {fail_count}/{min_len} elements fail",
                        max_abs_diff=float(np.max(diff)),
                    )
                else:
                    result.add_pass()
                return

            # Unknown dtype — just compare hex
            result.add_failure(
                obs_id,
                f"Data mismatch (dtype={dtype})",
                expected_hex=exp_hex[:64],
                actual_hex=act_hex[:64],
            )

        except (ValueError, TypeError) as e:
            result.add_failure(
                obs_id,
                f"Comparison error: {e}",
            )

    @staticmethod
    def _compare_data_dicts(
        expected: Observation,
        actual: Observation,
        obs_id: str,
        result: ScoreboardResult,
    ) -> None:
        """Generic data dict comparison."""
        exp_data = expected.data
        act_data = actual.data

        if exp_data == act_data:
            result.add_pass()
        else:
            # Find keys that differ
            all_keys = set(exp_data.keys()) | set(act_data.keys())
            diffs = []
            for key in sorted(all_keys):
                ev = exp_data.get(key)
                av = act_data.get(key)
                if ev != av:
                    diffs.append(f"  {key}: expected={ev!r}, actual={av!r}")

            result.add_failure(
                obs_id,
                f"Data mismatch: {'; '.join(diffs[:5])}" if diffs else "No data found",
            )

    # ── Fault classification (Todo 13) ──────────────────────────────

    @staticmethod
    def classify_faults(observations: List[Observation]) -> Set[str]:
        """Classify fault symptoms from observation data.

        Examines observations for known fault signatures and returns
        the set of detected fault classes. This method operates ONLY
        on Observation objects — it has zero DUT-specific knowledge.

        Each fault class has one or more signature keys in Observation.data
        that indicate its presence. For example:
            - data_corruption: observation data contains incompatible values
            - wrong_completion: "status" value is unexpected
            - timeout: observation data has a "timeout" marker
            - dropped_interrupt: observation data has an "irq_dropped" marker

        Args:
            observations: List of Observation objects to examine.

        Returns:
            Set of fault class strings detected.
        """
        detected: Set[str] = set()
        for obs in observations:
            obs_data = getattr(obs, "data", {}) or {}
            obs_meta = getattr(obs, "metadata", {}) or {}

            combined = {**obs_data, **obs_meta}

            # Completion status: unexpected status indicates wrong_completion or engine_error
            status = combined.get("status")
            if status is not None and isinstance(status, int) and status not in (0, 0x2):
                if status == 0xDEAD:
                    detected.add("engine_error")
                else:
                    detected.add("wrong_completion")

            # Timeout marker
            if combined.get("timeout") is True:
                detected.add("timeout")

            # Interrupt markers
            if combined.get("irq_dropped") is True or combined.get("interrupt_dropped") is True:
                detected.add("dropped_interrupt")
            if combined.get("irq_duplicate") is True or combined.get("interrupt_duplicate") is True:
                detected.add("duplicated_interrupt")

            # Descriptor/opcode errors
            if combined.get("desc_error") is True:
                detected.add("wrong_descriptor")
            if combined.get("unsupported_opcode") is True or combined.get("opcode_error") is True:
                detected.add("unsupported_opcode")

            # Ring overflow
            if combined.get("ring_overflow") is True:
                detected.add("ring_overflow")

            # Stalled head
            if combined.get("stalled_head") is True or combined.get("head_stalled") is True:
                detected.add("stalled_head")

            # Reset during command
            if combined.get("reset_during_cmd") is True:
                detected.add("reset_during_command")

            # Data corruption: check for corruption markers or mismatched data
            raw_hex = combined.get("raw_hex")
            if raw_hex and isinstance(raw_hex, str) and "__DATA_CORRUPTED__" in raw_hex:
                detected.add("data_corruption")

        return detected
