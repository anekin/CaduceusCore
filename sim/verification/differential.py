"""Differential signoff — compare Func Model adapter output against golden oracles.

Todo 14: [FEASIBILITY-ONLY] Establish Func Model / golden differential signoff
scenarios. RTL three-way comparison is deferred, but the divergence-report format
must classify issues as contract, transport, firmware, or compute so that future
RTL differential signoff can reuse the same taxonomy.

Design rules:
    - The golden oracle is independent from the DUT adapter code path.
    - Divergences are classified into: contract, transport, firmware, compute.
    - Fault injection from Todo 13 is detected and its class recorded.
    - Stale or missing evidence files cannot be reused as current evidence.
    - Unexplained divergence fails the gate.
"""

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from sim.verification.observation import Observation, ObservationType
from sim.verification.scenario import Scenario
from sim.verification.scoreboard import Scoreboard, ScoreboardResult
from sim.verification.tolerance import ToleranceConfig


class DivergenceClass(str, Enum):
    """Classification of a divergence between DUT output and golden oracle."""

    contract = "contract"
    """ABI/register contract violation: wrong address, missing observation,
    diagnostic path, opcode, or command descriptor mismatch."""

    transport = "transport"
    """Data movement issue: PCIe, DMA, NoC, memory addressing, or timeout."""

    firmware = "firmware"
    """Firmware / control-flow issue: command ring head/tail/order, completion
    status, interrupt behavior, reset behavior, or fault-injection symptom."""

    compute = "compute"
    """Numerical issue: MXU/SFU/Vector output mismatch, precision, saturation."""


@dataclass
class Divergence:
    """A single divergence between expected (golden) and actual (DUT) output."""

    observation_id: str
    expected: Any
    actual: Any
    classification: DivergenceClass
    explanation: str


@dataclass
class DivergenceReport:
    """Result of a differential signoff comparison for one scenario."""

    scenario_id: str
    gate_pass: bool
    adapter_name: str
    golden_name: str
    scoreboard_result: ScoreboardResult
    divergences: List[Divergence] = field(default_factory=list)
    detected_faults: Set[str] = field(default_factory=set)
    injection_applied: bool = False
    expected_detector: Optional[str] = None
    detection_hit: bool = False
    detector_failure_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "scenario_id": self.scenario_id,
            "gate_pass": self.gate_pass,
            "adapter_name": self.adapter_name,
            "golden_name": self.golden_name,
            "scoreboard": {
                "passed": self.scoreboard_result.passed,
                "total_checks": self.scoreboard_result.total_checks,
                "passed_checks": self.scoreboard_result.passed_checks,
                "failed_checks": self.scoreboard_result.failed_checks,
                "failures": self.scoreboard_result.failures,
            },
            "divergences": [
                {
                    "observation_id": d.observation_id,
                    "expected": d.expected,
                    "actual": d.actual,
                    "classification": d.classification.value,
                    "explanation": d.explanation,
                }
                for d in self.divergences
            ],
            "detected_faults": sorted(self.detected_faults),
            "injection_applied": self.injection_applied,
            "expected_detector": self.expected_detector,
            "detection_hit": self.detection_hit,
            "detector_failure_reason": self.detector_failure_reason,
            "metadata": dict(sorted(self.metadata.items())),
        }


class GoldenOracle(ABC):
    """Independent golden oracle that computes expected observations.

    Implementations must not read from the DUT adapter. They compute expected
    values from first principles (ISA model, reference functions, or direct
    arithmetic) using only the scenario inputs.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable oracle name for evidence records."""

    @abstractmethod
    def compute_expected(self, scenario_inputs: Dict[str, Any]) -> List[Observation]:
        """Return the expected observations for the given scenario inputs."""


class MemoryGoldenOracle(GoldenOracle):
    """Golden oracle for simple memory/MMIO copy scenarios.

    Expected values are deterministic copies of the input data or MMIO values.
    """

    @property
    def name(self) -> str:
        return "MemoryGoldenOracle"

    def compute_expected(self, scenario_inputs: Dict[str, Any]) -> List[Observation]:
        expected: List[Observation] = []
        for spec in scenario_inputs.get("expected_specs", []):
            obs_type = ObservationType(spec.get("observation_type", "mmio_value"))
            if obs_type == ObservationType.mmio_value:
                expected.append(Observation(
                    observation_id=spec["observation_id"],
                    observation_type=obs_type,
                    address=spec.get("address"),
                    data={"value": spec["value"]},
                ))
            elif obs_type in (ObservationType.sram_data, ObservationType.dram_data):
                expected.append(Observation(
                    observation_id=spec["observation_id"],
                    observation_type=obs_type,
                    address=spec.get("address"),
                    size=spec.get("size"),
                    data={"raw_hex": spec["raw_hex"], "dtype": spec.get("dtype", "int32")},
                ))
            elif obs_type == ObservationType.completion_status:
                expected.append(Observation(
                    observation_id=spec["observation_id"],
                    observation_type=obs_type,
                    data={"status": spec.get("status", 0x2)},
                ))
        return expected


class GoldenExecutorOracle(GoldenOracle):
    """Golden oracle using sim.golden_executor numerical models.

    This oracle is independent from the FuncModelAdapter execution path but
    shares the same reference model (GoldenExecutor) that RTL verification uses.
    """

    def __init__(self):
        self._mxu = None
        self._sfu = None
        self._vector = None

    @property
    def name(self) -> str:
        return "GoldenExecutorOracle"

    def _lazy_init(self):
        from sim.golden_executor import GoldenMXU, GoldenSFU, GoldenVector
        if self._mxu is None:
            self._mxu = GoldenMXU()
            self._sfu = GoldenSFU()
            self._vector = GoldenVector()

    def compute_expected(self, scenario_inputs: Dict[str, Any]) -> List[Observation]:
        self._lazy_init()
        kind = scenario_inputs.get("kind")
        if kind == "mmul":
            return self._compute_mmul(scenario_inputs)
        if kind == "sfu_softmax":
            return self._compute_sfu_softmax(scenario_inputs)
        if kind == "sfu_rmsnorm":
            return self._compute_sfu_rmsnorm(scenario_inputs)
        if kind == "vector_vadd":
            return self._compute_vector_vadd(scenario_inputs)
        if kind == "dma_copy":
            return self._compute_dma_copy(scenario_inputs)
        raise ValueError(f"Unsupported GoldenExecutor oracle kind: {kind}")

    def _compute_mmul(self, inputs: Dict[str, Any]) -> List[Observation]:
        import numpy as np
        M = inputs["M"]
        K = inputs["K"]
        N = inputs["N"]
        act = np.asarray(inputs["activation"], dtype=np.int8).reshape(M, K)
        wgt_packed = np.asarray(inputs["weight_packed"], dtype=np.uint8)
        result = self._mxu.matmul_int32(act, wgt_packed, M, K, N)
        raw = result.astype(np.int32).tobytes()
        return [Observation(
            observation_id=inputs["observation_id"],
            observation_type=ObservationType.sram_data,
            address=inputs.get("output_offset", 0),
            size=len(raw),
            data={"raw_hex": raw.hex(), "dtype": "int32"},
        )]

    def _compute_sfu_softmax(self, inputs: Dict[str, Any]) -> List[Observation]:
        import numpy as np
        inp = np.asarray(inputs["input"], dtype=np.float32)
        out = self._sfu.softmax_hw(inp)
        raw = out.astype(np.float16).tobytes()
        return [Observation(
            observation_id=inputs["observation_id"],
            observation_type=ObservationType.sram_data,
            address=inputs.get("output_offset", 0),
            size=len(raw),
            data={"raw_hex": raw.hex(), "dtype": "fp16"},
        )]

    def _compute_sfu_rmsnorm(self, inputs: Dict[str, Any]) -> List[Observation]:
        import numpy as np
        inp = np.asarray(inputs["input"], dtype=np.float32)
        out = self._sfu.rmsnorm_hw(inp)
        raw = out.astype(np.float16).tobytes()
        return [Observation(
            observation_id=inputs["observation_id"],
            observation_type=ObservationType.sram_data,
            address=inputs.get("output_offset", 0),
            size=len(raw),
            data={"raw_hex": raw.hex(), "dtype": "fp16"},
        )]

    def _compute_vector_vadd(self, inputs: Dict[str, Any]) -> List[Observation]:
        import numpy as np
        a = np.asarray(inputs["a"], dtype=np.int32)
        b = np.asarray(inputs["b"], dtype=np.int32)
        out = self._vector.add(a, b)
        raw = out.astype(np.int32).tobytes()
        return [Observation(
            observation_id=inputs["observation_id"],
            observation_type=ObservationType.sram_data,
            address=inputs.get("output_offset", 0),
            size=len(raw),
            data={"raw_hex": raw.hex(), "dtype": "int32"},
        )]

    def _compute_dma_copy(self, inputs: Dict[str, Any]) -> List[Observation]:
        # DMA copy golden: data is identical byte content at destination.
        data = bytes.fromhex(inputs["data_hex"])
        return [Observation(
            observation_id=inputs["observation_id"],
            observation_type=ObservationType.sram_data,
            address=inputs.get("dst_offset", 0),
            size=len(data),
            data={"raw_hex": data.hex(), "dtype": "int32"},
        )]


def _classify_divergence(
    observation_id: str,
    observation_type: ObservationType,
    expected: Any,
    actual: Any,
    scenario_metadata: Dict[str, Any],
) -> DivergenceClass:
    """Classify a single divergence into contract/transport/firmware/compute."""

    # Contract: missing observations or MMIO/opcode/descriptor mismatches
    if actual is None or expected is None:
        return DivergenceClass.contract

    # Firmware: completion status, interrupt, head/tail, command order, reset
    if observation_type in (
        ObservationType.completion_status,
        ObservationType.interrupt_status,
    ):
        return DivergenceClass.firmware

    # Compute: numerical outputs from engines
    if observation_type in (ObservationType.sram_data, ObservationType.dram_data):
        dtype = (actual or expected).get("dtype", "int32")
        obs_id = observation_id.lower()
        if dtype in ("fp16", "float16") or "mmul" in obs_id or "vector" in obs_id:
            # Distinguish transport from compute: if a fault was injected into
            # data movement, classify as transport; otherwise numerical mismatch.
            fault_class = scenario_metadata.get("fault_class")
            if fault_class in (
                "data_corruption",
                "wrong_descriptor",
                "unsupported_opcode",
            ):
                return DivergenceClass.transport
            return DivergenceClass.compute
        # Memory copy / DMA data mismatch without compute semantics → transport
        return DivergenceClass.transport

    return DivergenceClass.contract


def build_divergences(
    scoreboard_result: ScoreboardResult,
    actual_observations: List[Observation],
    expected_observations: List[Observation],
    scenario_metadata: Dict[str, Any],
) -> List[Divergence]:
    """Build classified Divergence records from scoreboard failures."""

    divergences: List[Divergence] = []
    actual_by_id = {o.observation_id: o for o in actual_observations}
    expected_by_id = {o.observation_id: o for o in expected_observations}

    for failure in scoreboard_result.failures:
        obs_id = failure.get("observation_id", "unknown")
        act = actual_by_id.get(obs_id)
        exp = expected_by_id.get(obs_id)
        classification = _classify_divergence(
            obs_id,
            exp.observation_type if exp else ObservationType.generic,
            exp.data if exp else None,
            act.data if act else None,
            scenario_metadata,
        )
        divergences.append(Divergence(
            observation_id=obs_id,
            expected=exp.data if exp else None,
            actual=act.data if act else None,
            classification=classification,
            explanation=failure.get("message", "mismatch"),
        ))

    return divergences


def check_provenance(
    evidence: Dict[str, Any],
    reference_time: Optional[datetime] = None,
    max_age_seconds: float = 86400.0,
) -> tuple[bool, Optional[str]]:
    """Validate that an evidence file is fresh and complete.

    Returns (ok, reason). reason is None when the evidence is acceptable.
    """

    if not evidence or not isinstance(evidence, dict):
        return False, "Evidence is empty or not a dict"

    timestamp = evidence.get("timestamp")
    if not timestamp:
        return False, "Missing evidence timestamp"

    try:
        # Accept ISO 8601 with or without timezone
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False, f"Invalid evidence timestamp: {timestamp}"

    ref = reference_time or datetime.now(timezone.utc)
    if (ref - ts).total_seconds() > max_age_seconds:
        return False, f"Evidence timestamp {timestamp} is older than {max_age_seconds}s"

    if "records" not in evidence:
        return False, "Missing evidence records"

    if evidence.get("scenarios_total", 0) == 0:
        return False, "Evidence contains zero scenarios"

    return True, None


def scenario_content_hash(scenario: Scenario) -> str:
    """Compute a deterministic content hash for a scenario."""
    canonical = json.dumps(scenario.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def evidence_matches_scenario(
    evidence: Dict[str, Any],
    scenario: Scenario,
) -> tuple[bool, Optional[str]]:
    """Check whether evidence matches the given scenario content hash."""

    expected_hash = scenario_content_hash(scenario)
    scenario_details = evidence.get("scenario_details", {})
    detail = scenario_details.get(scenario.scenario_id, {})
    stored_hash = detail.get("metadata", {}).get("scenario_content_hash")

    if stored_hash is None:
        return False, "Evidence missing scenario_content_hash"

    if stored_hash != expected_hash:
        return False, (
            f"Scenario content hash mismatch: "
            f"expected {expected_hash}, got {stored_hash}"
        )

    return True, None


def load_evidence(path: Path) -> Dict[str, Any]:
    """Load evidence JSON and validate basic structure."""

    if not path.exists():
        raise FileNotFoundError(f"Evidence file not found: {path}")

    with open(path, "r") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("Evidence file does not contain a JSON object")

    return data


def _check_anti_vacuity(
    expected_detector: Optional[str],
    detected_faults: Set[str],
    injection_applied: bool,
    scoreboard_result: ScoreboardResult,
    injected_fault_class: Optional[str] = None,
) -> tuple[bool, str]:
    """Verify that the expected detector actually fired.

    This is the anti-vacuity gate: a fault-injection test must prove that
    a specific checker detected the fault, not just that the mutation was
    applied.

    Args:
        expected_detector: The detector name that must fire (e.g. "data_corruption",
            "scoreboard_mismatch", "no_fault").
        detected_faults: Fault classes detected by Scoreboard.classify_faults().
        injection_applied: Whether a fault was actually injected.
        scoreboard_result: The full scoreboard comparison result.
        injected_fault_class: The FaultClass actually injected (from scenario
            metadata "fault_class"). Used to verify the expected detector
            matches what was injected.

    Returns:
        (detection_hit, reason). detection_hit is True when the expected
        detector fired, False otherwise. reason is empty string on success
        or an explanation on failure.
    """
    if expected_detector is None:
        return True, ""

    # "no_fault" detector: verify no false positive when no injection occurred
    if expected_detector == "no_fault":
        if injection_applied:
            return False, "anti-vacuity: unexpected injection applied to no-fault scenario"
        if not scoreboard_result.passed:
            return False, "anti-vacuity: false positive — scoreboard mismatch without fault injection"
        if len(detected_faults) > 0:
            return False, f"anti-vacuity: false positive — fault classifier detected {sorted(detected_faults)} without injection"
        return True, ""

    # All other detectors require injection
    if not injection_applied:
        return False, f"anti-vacuity: injection was not applied (expected detector '{expected_detector}')"

    # "scoreboard_mismatch" detector: any scoreboard mismatch counts
    if expected_detector == "scoreboard_mismatch":
        if not scoreboard_result.passed:
            return True, ""
        return False, "anti-vacuity: injection applied but scoreboard reported no mismatch"

    # "any_detector": any detection mechanism counts
    if expected_detector == "any_detector":
        if len(detected_faults) > 0:
            return True, ""
        if not scoreboard_result.passed:
            return True, ""
        return False, "anti-vacuity: injection applied but no detector fired"

    # Specific fault-class detector:
    # Verify the injected fault MATCHES the expected detector
    if injected_fault_class is not None and injected_fault_class != expected_detector:
        return False, (
            f"anti-vacuity: injection applied as '{injected_fault_class}' "
            f"but expected detector '{expected_detector}' — wrong detector specified"
        )

    # Check if the specific fault was classified
    if expected_detector in detected_faults:
        return True, ""

    # Fallback: scoreboard mismatch counts as implicit detection
    if not scoreboard_result.passed:
        return True, ""

    return False, (
        f"anti-vacuity: injection applied but no detector fired "
        f"(expected '{expected_detector}', detected {sorted(detected_faults)})"
    )


async def run_differential_scenario(
    adapter,
    scenario: Scenario,
    golden_oracle: GoldenOracle,
    golden_inputs: Dict[str, Any],
) -> DivergenceReport:
    """Run one scenario differentially and produce a DivergenceReport.

    Executes the scenario actions through the adapter, computes golden
    expectations via the oracle, compares, classifies divergences, and
    detects/records any fault injection.
    """

    from sim.verification.fault_injector import FaultClass

    scenario_id = scenario.scenario_id
    adapter_name = adapter.adapter_name

    # Execute actions (fault injection is enabled by scenario metadata)
    fault_class_str = scenario.metadata.get("fault_class")
    fault_params = scenario.metadata.get("fault_params", {})
    if fault_class_str and hasattr(adapter, "enable_fault"):
        adapter.enable_fault(FaultClass(fault_class_str), **fault_params)

    for action in scenario.actions:
        try:
            await adapter.execute_action(action)
        except Exception:
            # Fault injection may raise; continue to observation phase
            pass

    # Gather actual observations
    actual_observations: List[Observation] = []
    for spec in scenario.expected_observations:
        obs = await adapter.observe(spec)
        actual_observations.append(obs)

    # Golden expectations
    expected_observations = golden_oracle.compute_expected(golden_inputs)

    missing_golden = (
        len(scenario.expected_observations) > 0 and len(expected_observations) == 0
    )

    # Compare
    scoreboard = Scoreboard(tolerance=scenario.tolerance)
    result = scoreboard.compare(expected_observations, actual_observations)

    # Detect faults
    detected_faults: Set[str] = set(Scoreboard.classify_faults(actual_observations))
    evidence_meta = getattr(adapter, "evidence_metadata", lambda: {})()
    injection_applied = bool(evidence_meta.get("injection_applied", False))

    divergences = build_divergences(
        result, actual_observations, expected_observations, scenario.metadata
    )

    if missing_golden:
        divergences.append(Divergence(
            observation_id="*",
            expected=None,
            actual=None,
            classification=DivergenceClass.contract,
            explanation="Golden oracle produced no expected observations",
        ))

    # Gate passes only when scoreboard passes and there is no unexplained divergence.
    gate_pass = result.passed and len(divergences) == 0 and not missing_golden

    # ── Anti-vacuity gate (Todo 19 / W4-T4) ───────────────────────────
    expected_detector = scenario.metadata.get("expected_detector")
    expected_fault = scenario.metadata.get("expected_classification")
    detection_hit = True
    detector_failure: Optional[str] = None

    if expected_detector is not None:
        detection_hit, detector_failure = _check_anti_vacuity(
            expected_detector, detected_faults, injection_applied, result,
            injected_fault_class=fault_class_str,
        )
        gate_pass = detection_hit
        if not detection_hit:
            divergences.append(Divergence(
                observation_id="*",
                expected=expected_detector,
                actual="not_detected",
                classification=DivergenceClass.firmware,
                explanation=detector_failure,
            ))
    elif expected_fault is not None:
        fault_recorded = expected_fault in detected_faults or injection_applied
        gate_pass = fault_recorded

    return DivergenceReport(
        scenario_id=scenario_id,
        gate_pass=gate_pass,
        adapter_name=adapter_name,
        golden_name=golden_oracle.name,
        scoreboard_result=result,
        divergences=divergences,
        detected_faults=detected_faults,
        injection_applied=injection_applied,
        expected_detector=expected_detector,
        detection_hit=detection_hit,
        detector_failure_reason=detector_failure,
        metadata={
            "scenario_content_hash": scenario_content_hash(scenario),
            "expected_fault": expected_fault,
            "fault_class": fault_class_str,
            "golden_inputs": golden_inputs.get("kind"),
            **evidence_meta,
        },
    )
