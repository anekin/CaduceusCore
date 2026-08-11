"""Performance contract schemas — typed event, provider, report, and calibration-ready artifact.

Strict validation via Pydantic v2: unknown versions/ops/shapes/units, nonpositive
values, NaN/Inf, duplicates and missing pairs all fail closed.

Usage:
    python3 -m sim.timing.perf_contract --self-check
    python3 -m sim.timing.perf_contract --negative-fixtures <path>,<path>...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from enum import Enum
from typing import Any, ClassVar, Dict, FrozenSet, List, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


# ── Enums ────────────────────────────────────────────────────────────────────


class EngineType(str, Enum):
    MXU = "mxu"
    SFU = "sfu"
    VECTOR = "vector"
    DMA = "dma"
    NOC = "noc"
    DRAM = "dram"
    KV_CACHE = "kv_cache"
    RISC_V = "riscv"
    SW_OVERHEAD = "sw_overhead"


class EventKind(str, Enum):
    COMMAND_ACCEPTED = "command_accepted"
    COMMAND_COMPLETED = "command_completed"
    COMMAND_ORDERED = "command_ordered"


class OpType(str, Enum):
    # MXU
    MMUL = "mmul"
    # SFU
    SOFTMAX = "softmax"
    LAYERNORM = "layernorm"
    RMSNORM = "rmsnorm"
    GELU = "gelu"
    SILU = "silu"
    ROPE = "rope"
    # Vector
    ADD = "add"
    MUL = "mul"
    MAX = "max"
    SUM = "sum"
    CONV = "conv"
    RESID = "resid"
    # DMA
    DMA_COPY = "dma_copy"
    # DRAM
    DRAM_READ = "dram_read"
    DRAM_WRITE = "dram_write"
    # KV
    KV_ACCESS = "kv_access"
    KV_LAYER_SWITCH = "kv_layer_switch"
    # NoC
    NOC_ROUTE = "noc_route"
    # RISC-V
    RISC_V_INSTR = "riscv_instr"


class UnitType(str, Enum):
    CYCLES = "cycles"
    US = "us"
    MS = "ms"
    GB_PER_S = "GB/s"
    TOPS = "TOPS"


class BasisType(str, Enum):
    ARCHITECTURAL_FORMULA = "architectural_formula"
    RTL_MEASUREMENT = "rtl_measurement"


class CalibrationState(str, Enum):
    UNCALIBRATED = "uncalibrated"
    RTL_CALIBRATED = "rtl_calibrated"


class DomainType(str, Enum):
    MXU = "mxu"
    SFU = "sfu"
    VECTOR = "vector"
    DMA = "dma"
    DRAM = "dram"
    NOC = "noc"
    KV_CACHE = "kv_cache"
    SW_OVERHEAD = "sw_overhead"


KNOWN_UNITS: FrozenSet[str] = frozenset(e.value for e in UnitType)
KNOWN_ENGINES: FrozenSet[str] = frozenset(e.value for e in EngineType)
KNOWN_OPS: FrozenSet[str] = frozenset(e.value for e in OpType)

# Shape dimension keys we expect for each engine
_ENGINE_SHAPE_KEYS: Dict[str, FrozenSet[str]] = {
    "mxu": frozenset({"M", "K", "N"}),
    "sfu": frozenset({"elements"}),
    "vector": frozenset({"dim"}),
    "dma": frozenset({"bytes"}),
    "dram": frozenset({"bytes", "rw"}),
    "kv_cache": frozenset({"token_pos", "sram_kb"}),
    "noc": frozenset({"bytes", "topology", "route"}),
    "riscv": frozenset({"instructions"}),
    "sw_overhead": frozenset({"num_layers"}),
}


# ── Validate helpers ─────────────────────────────────────────────────────────


def _check_finite_non_nan_inf(value: float, field_name: str) -> None:
    """Raise ValueError if value is NaN or Inf."""
    if math.isnan(value):
        raise ValueError(f"{field_name} must not be NaN")
    if math.isinf(value):
        raise ValueError(f"{field_name} must not be Inf")


def _check_positive(value: int, field_name: str) -> None:
    """Raise ValueError if value is not positive."""
    if value <= 0:
        raise ValueError(f"{field_name} must be positive, got {value}")


def _check_nonnegative(value: int, field_name: str) -> None:
    """Raise ValueError if value is negative."""
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative, got {value}")


def _check_not_empty(value: str, field_name: str) -> None:
    """Raise ValueError if string is empty or whitespace-only."""
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _check_shape_keys(engine: str, shape: Dict[str, int]) -> None:
    """Validate that shape keys match engine expectations."""
    expected = _ENGINE_SHAPE_KEYS.get(engine)
    if expected is None:
        return  # Unknown engine checked elsewhere
    actual_keys = frozenset(shape.keys())
    if actual_keys != expected:
        raise ValueError(
            f"Engine '{engine}' shape keys {sorted(actual_keys)} "
            f"do not match expected {sorted(expected)}"
        )


# ── Contracts ────────────────────────────────────────────────────────────────


class PerfEvent(BaseModel):
    """Semantic performance event emitted at MMIO command acceptance/completion seams.

    Carries typed engine, operation, programmed shape and parent workload IDs.
    Provider timeline is separate; functional mode executes, profile-only skips
    numerical kernels and is never functional evidence.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(..., description="Unique event identifier")
    kind: EventKind = Field(..., description="command_accepted | command_completed | command_ordered")
    seq_id: int = Field(..., gt=0, description="Positive monotonic sequence identifier")
    parent_workload_id: str = Field(..., min_length=1, description="Owning workload identifier")
    engine: EngineType = Field(..., description="Target engine")
    op: OpType = Field(..., description="Semantic operation type")
    programmed_shape: Dict[str, int] = Field(
        ..., description="Operation shape (e.g. {M:64, K:64, N:64} for MXU mmul)"
    )

    @field_validator("event_id", "parent_workload_id")
    @classmethod
    def not_empty(cls, v: str) -> str:
        _check_not_empty(v, "event_id/parent_workload_id")
        return v

    @field_validator("programmed_shape")
    @classmethod
    def shape_values_nonnegative(cls, v: Dict[str, int]) -> Dict[str, int]:
        if not v:
            raise ValueError("programmed_shape must not be empty")
        for key, val in v.items():
            if not isinstance(val, int):
                raise ValueError(f"Shape dim '{key}' value must be int, got {type(val).__name__}")
            if val < 0:
                raise ValueError(f"programmed_shape['{key}'] must be non-negative, got {val}")
        return v

    @model_validator(mode="after")
    def shape_keys_match_engine(self) -> "PerfEvent":
        _check_shape_keys(self.engine.value, self.programmed_shape)
        return self


class PerfEstimate(BaseModel):
    """A single architectural estimate from a timing provider.

    Carries estimated cycles, decomposition assumptions, uncertainty band,
    and domain/boundary provenance.  Does NOT carry measured cycles — the
    'measured_cycles' field is forbidden by extra="forbid".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(..., min_length=1)
    provider_version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    basis: BasisType = BasisType.ARCHITECTURAL_FORMULA
    calibration_state: CalibrationState = CalibrationState.UNCALIBRATED
    domain: DomainType = Field(..., description="Covered hardware domain")
    boundary_id: str = Field(..., min_length=1, description="Shape/config boundary identifier")
    engine: EngineType
    op: OpType
    shape: Dict[str, int] = Field(..., min_length=1)
    estimated_cycles: int = Field(..., gt=0, description="Architectural estimated cycles")
    units: UnitType = UnitType.CYCLES
    assumptions: List[str] = Field(default_factory=list)
    uncertainty_pct: float = Field(..., ge=0.0, description="Symmetric +/- bound in percent")
    spec_hash: str = Field(..., min_length=1, description="Normative spec content hash")
    config_hash: str = Field(default="", description="Provider configuration hash")

    # ── Future RTL calibration fields (schema-compatible, NOT verdict-eligible) ──
    rtl_head: Optional[str] = Field(default=None, description="RTL commit hash (future)")
    eda_version: Optional[str] = Field(default=None, description="EDA tool version (future)")
    testbench_hash: Optional[str] = Field(default=None, description="Testbench content hash (future)")
    raw_log_hash: Optional[str] = Field(default=None, description="Raw simulation log hash (future)")
    fit_matrix_hash: Optional[str] = Field(default=None, description="Calibration fit matrix hash (future)")

    @field_validator("uncertainty_pct")
    @classmethod
    def finite_uncertainty(cls, v: float) -> float:
        _check_finite_non_nan_inf(v, "uncertainty_pct")
        return v

    @field_validator("estimated_cycles")
    @classmethod
    def positive_cycles(cls, v: int) -> int:
        _check_positive(v, "estimated_cycles")
        return v

    @field_validator("shape")
    @classmethod
    def shape_values_nonnegative(cls, v: Dict[str, int]) -> Dict[str, int]:
        for key, val in v.items():
            if val < 0:
                raise ValueError(f"shape['{key}'] must be non-negative, got {val}")
        return v

    @model_validator(mode="after")
    def shape_keys_match_engine(self) -> "PerfEstimate":
        _check_shape_keys(self.engine.value, self.shape)
        return self

    def content_hash(self) -> str:
        """Canonical content hash excluding volatile metadata (rtl_head, eda_version, etc.)."""
        data = self.model_dump(mode="json", exclude_none=True)
        for key in ("rtl_head", "eda_version", "testbench_hash", "raw_log_hash", "fit_matrix_hash"):
            data.pop(key, None)
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    def is_verdict_eligible(self) -> bool:
        """Only uncalibrated architectural-formula artifacts can participate in verdict."""
        return self.basis == BasisType.ARCHITECTURAL_FORMULA and self.calibration_state == CalibrationState.UNCALIBRATED

    def round_trip(self) -> "PerfEstimate":
        """Serialize to JSON and parse back; returns the reconstructed instance."""
        return PerfEstimate.model_validate_json(self.model_dump_json())


class PerfBand(BaseModel):
    """Three-point uncertainty band: low ≤ base ≤ high."""

    model_config = ConfigDict(extra="forbid")

    low: float
    base: float
    high: float

    @field_validator("low", "base", "high")
    @classmethod
    def finite_check(cls, v: float) -> float:
        _check_finite_non_nan_inf(v, "band value")
        return v

    @model_validator(mode="after")
    def monotonic(self) -> "PerfBand":
        if not (self.low <= self.base <= self.high):
            raise ValueError(
                f"Band not monotonic: low={self.low} base={self.base} high={self.high}"
            )
        return self


class DiagnosticsEntry(BaseModel):
    """Diagnostic metric with assumption and provenance tracking."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    value: float
    assumption: str = Field(..., min_length=1)
    provenance: str = Field(..., min_length=1)

    @field_validator("value")
    @classmethod
    def finite_value(cls, v: float) -> float:
        _check_finite_non_nan_inf(v, "diagnostic value")
        return v


class PerfReport(BaseModel):
    """Aggregated performance report with uncertainty bands and diagnostics.

    canonical_total_cycles is the wall-clock critical path, NOT a sum-of-breakdowns.
    SW overhead is tracked separately and never included in canonical total.
    """

    model_config = ConfigDict(extra="forbid")

    workload_id: str = Field(..., min_length=1)
    provider_id: str
    provider_version: str
    cycles: PerfBand = Field(..., description="Low/base/high cycle estimate band")
    tps: Optional[PerfBand] = Field(default=None, description="Tokens/s band (LLM workloads)")
    ttft_ms: Optional[PerfBand] = Field(default=None, description="Time-to-first-token band")
    tpot_us: Optional[PerfBand] = Field(default=None, description="Time-per-output-token band")
    canonical_total_cycles: int = Field(..., ge=0, description="Critical-path wall-clock cycles")
    sw_overhead_cycles: int = Field(default=0, ge=0, description="SW overhead tracked separately")
    sw_overhead_included: bool = Field(
        default=False, description="Whether SW overhead is in canonical total (must be False)"
    )
    diagnostics: List[DiagnosticsEntry] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def sw_overhead_not_in_canonical(self) -> "PerfReport":
        if self.sw_overhead_included:
            raise ValueError(
                "sw_overhead_included must be False: SW overhead is never in canonical total"
            )
        return self


class PerfArtifact(BaseModel):
    """Calibration-ready provider artifact container.

    Must have explicit schema_version, provider provenance, basis, calibration_state,
    and domain/boundary metadata.  Future RTL fields (rtl_head, eda_version, etc.)
    are schema-compatible but exclude this artifact from verdict eligibility.

    measured_cycles is NOT a valid field — extra="forbid" rejects it.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    provider_id: str = Field(..., min_length=1)
    provider_version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    basis: BasisType = Field(..., description="Only architectural_formula allowed for current phase")
    calibration_state: CalibrationState = Field(
        ..., description="Only uncalibrated allowed for current phase"
    )
    domain: DomainType
    boundary_id: str = Field(..., min_length=1)
    spec_hash: str = Field(..., min_length=1)
    config_hash: str = Field(default="", min_length=0)
    estimated_cycles: int = Field(..., gt=0)
    units: UnitType = UnitType.CYCLES
    uncertainty_pct: float = Field(..., ge=0.0)
    assumptions: List[str] = Field(default_factory=list)

    # ── Future RTL fields (schema-compatible, verdict-ineligible) ──
    rtl_head: Optional[str] = None
    eda_version: Optional[str] = None
    testbench_hash: Optional[str] = None
    raw_log_hash: Optional[str] = None
    fit_matrix_hash: Optional[str] = None

    @field_validator("uncertainty_pct")
    @classmethod
    def finite_uncertainty(cls, v: float) -> float:
        _check_finite_non_nan_inf(v, "uncertainty_pct")
        return v

    @field_validator("estimated_cycles")
    @classmethod
    def positive_cycles(cls, v: int) -> int:
        _check_positive(v, "estimated_cycles")
        return v

    def content_hash(self) -> str:
        """Canonical content hash excluding volatile RTL metadata."""
        data = self.model_dump(mode="json", exclude_none=True)
        for key in ("rtl_head", "eda_version", "testbench_hash", "raw_log_hash", "fit_matrix_hash"):
            data.pop(key, None)
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    def is_verdict_eligible(self) -> bool:
        """Only uncalibrated architectural basis passes verdict gate."""
        return (
            self.basis == BasisType.ARCHITECTURAL_FORMULA
            and self.calibration_state == CalibrationState.UNCALIBRATED
        )

    def round_trip(self) -> "PerfArtifact":
        """Serialize and re-parse; returns the reconstructed instance."""
        return PerfArtifact.model_validate_json(self.model_dump_json())


# ── Event stream validation ──────────────────────────────────────────────────


class EventPairValidator:
    """Validates a stream of PerfEvent objects for duplicates and missing pairs.

    Every command_accepted must have a matching command_completed with the same
    seq_id, and vice versa.  Duplicate event IDs are rejected.
    """

    def __init__(self) -> None:
        self._seen_ids: Set[str] = set()
        self._accepted: Dict[int, PerfEvent] = {}
        self._completed: Dict[int, PerfEvent] = {}

    def add(self, event: PerfEvent) -> List[str]:
        """Register an event.  Returns list of violation messages (empty = clean)."""
        violations: List[str] = []

        if event.event_id in self._seen_ids:
            violations.append(f"Duplicate event_id: {event.event_id}")
        self._seen_ids.add(event.event_id)

        if event.kind == EventKind.COMMAND_ACCEPTED:
            self._accepted[event.seq_id] = event
        elif event.kind == EventKind.COMMAND_COMPLETED:
            self._completed[event.seq_id] = event
        # COMMAND_ORDERED events are informational; not pair-tracked

        return violations

    def check_pairs(self) -> List[str]:
        """Validate that every acceptance has a completion and vice versa."""
        violations: List[str] = []
        for seq_id in self._accepted:
            if seq_id not in self._completed:
                violations.append(
                    f"Missing completion for accepted seq_id={seq_id} "
                    f"(event_id={self._accepted[seq_id].event_id})"
                )
        for seq_id in self._completed:
            if seq_id not in self._accepted:
                violations.append(
                    f"Completion without acceptance: seq_id={seq_id} "
                    f"(event_id={self._completed[seq_id].event_id})"
                )
        return violations

    def validate_all(self, events: List[PerfEvent]) -> List[str]:
        """Add all events and check pairs in one call."""
        violations: List[str] = []
        for e in events:
            violations.extend(self.add(e))
        violations.extend(self.check_pairs())
        return violations


# ── Version negotiation ──────────────────────────────────────────────────────


SUPPORTED_SCHEMA_VERSIONS: Tuple[str, ...] = ("1.0.0",)


def negotiate_version(requested: str) -> str:
    """Negotiate the highest mutually-supported schema version."""
    if requested in SUPPORTED_SCHEMA_VERSIONS:
        return requested
    raise ValueError(
        f"Unsupported schema version '{requested}'; supported: {SUPPORTED_SCHEMA_VERSIONS}"
    )


# ── Fixture validation ───────────────────────────────────────────────────────


def validate_fixture(json_path: str, model_cls: Any) -> Tuple[bool, Optional[str]]:
    """Try to parse a JSON file as the given model.

    Returns (accepted: bool, error_message: Optional[str]).
    accepted=True means validation passed; accepted=False means rejected as expected.
    """
    with open(json_path, "r") as f:
        data = json.load(f)
    try:
        model_cls.model_validate(data)
        return True, None
    except ValidationError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)


# ── Self-check ───────────────────────────────────────────────────────────────


_SELF_CHECK_VERDICT: Dict[str, Any] = {
    "passed": 0,
    "total": 0,
    "details": [],
}


def _self_check_ok(detail: str) -> None:
    _SELF_CHECK_VERDICT["passed"] += 1
    _SELF_CHECK_VERDICT["total"] += 1
    _SELF_CHECK_VERDICT["details"].append(f"PASS: {detail}")


def _self_check_fail(detail: str) -> None:
    _SELF_CHECK_VERDICT["total"] += 1
    _SELF_CHECK_VERDICT["details"].append(f"FAIL: {detail}")


def run_self_check() -> int:
    """Run all contract self-tests. Returns 0 on pass, 1 on failure."""
    errors: List[str] = []

    # 1. PerfEvent — valid construction
    try:
        ev = PerfEvent(
            event_id="ev-001",
            kind=EventKind.COMMAND_ACCEPTED,
            seq_id=1,
            parent_workload_id="wl-qwen-blk0",
            engine=EngineType.MXU,
            op=OpType.MMUL,
            programmed_shape={"M": 64, "K": 64, "N": 64},
        )
        _self_check_ok("PerfEvent valid construction")
    except Exception as e:
        _self_check_fail(f"PerfEvent valid construction: {e}")

    # 2. PerfEvent — unknown engine fails
    try:
        PerfEvent(
            event_id="ev-bad",
            kind=EventKind.COMMAND_ACCEPTED,
            seq_id=1,
            parent_workload_id="wl",
            engine="gpu",  # type: ignore[arg-type]
            op=OpType.MMUL,
            programmed_shape={"M": 64, "K": 64, "N": 64},
        )
        _self_check_fail("PerfEvent: unknown engine should have failed")
    except Exception:
        _self_check_ok("PerfEvent: unknown engine rejected")

    # 3. PerfEvent — unknown op fails
    try:
        PerfEvent(
            event_id="ev-bad",
            kind=EventKind.COMMAND_ACCEPTED,
            seq_id=1,
            parent_workload_id="wl",
            engine=EngineType.MXU,
            op="conv2d",  # type: ignore[arg-type]
            programmed_shape={"M": 64, "K": 64, "N": 64},
        )
        _self_check_fail("PerfEvent: unknown op should have failed")
    except Exception:
        _self_check_ok("PerfEvent: unknown op rejected")

    # 4. PerfEvent — negative shape value fails
    try:
        PerfEvent(
            event_id="ev-neg",
            kind=EventKind.COMMAND_ACCEPTED,
            seq_id=1,
            parent_workload_id="wl",
            engine=EngineType.MXU,
            op=OpType.MMUL,
            programmed_shape={"M": -1, "K": 64, "N": 64},
        )
        _self_check_fail("PerfEvent: negative shape value should have failed")
    except Exception:
        _self_check_ok("PerfEvent: negative shape value rejected")

    # 5. PerfEvent — zero shape value allowed (valid for DRAM rw, NoC index, KV pos=0)
    try:
        ev_zero = PerfEvent(
            event_id="ev-kv-zero",
            kind=EventKind.COMMAND_ACCEPTED,
            seq_id=1,
            parent_workload_id="wl",
            engine=EngineType.KV_CACHE,
            op=OpType.KV_ACCESS,
            programmed_shape={"token_pos": 0, "sram_kb": 64},
        )
        _self_check_ok("PerfEvent: zero shape value accepted (KV token_pos=0)")
    except Exception as e:
        _self_check_fail(f"PerfEvent: zero shape value should be allowed: {e}")

    # 6. PerfEvent — seq_id <= 0 fails
    try:
        PerfEvent(
            event_id="ev-seq-zero",
            kind=EventKind.COMMAND_ACCEPTED,
            seq_id=0,
            parent_workload_id="wl",
            engine=EngineType.MXU,
            op=OpType.MMUL,
            programmed_shape={"M": 64, "K": 64, "N": 64},
        )
        _self_check_fail("PerfEvent: seq_id=0 should have failed")
    except Exception:
        _self_check_ok("PerfEvent: seq_id=0 rejected")

    # 7. EventPairValidator — duplicate event_id
    validator = EventPairValidator()
    e1 = PerfEvent(
        event_id="dup", kind=EventKind.COMMAND_ACCEPTED, seq_id=1,
        parent_workload_id="wl", engine=EngineType.MXU, op=OpType.MMUL,
        programmed_shape={"M": 64, "K": 64, "N": 64},
    )
    v = validator.add(e1)
    if len(v) != 0:
        _self_check_fail(f"EventPairValidator: unexpected violation on first add: {v}")
    else:
        _self_check_ok("EventPairValidator: first add clean")

    e2 = PerfEvent(
        event_id="dup", kind=EventKind.COMMAND_COMPLETED, seq_id=1,
        parent_workload_id="wl", engine=EngineType.MXU, op=OpType.MMUL,
        programmed_shape={"M": 64, "K": 64, "N": 64},
    )
    v = validator.add(e2)
    if len(v) != 1 or "Duplicate" not in v[0]:
        _self_check_fail(f"EventPairValidator: duplicate not caught: {v}")
    else:
        _self_check_ok("EventPairValidator: duplicate event_id caught")

    # 8. EventPairValidator — missing completion
    validator2 = EventPairValidator()
    validator2.add(PerfEvent(
        event_id="ev-a", kind=EventKind.COMMAND_ACCEPTED, seq_id=10,
        parent_workload_id="wl", engine=EngineType.MXU, op=OpType.MMUL,
        programmed_shape={"M": 64, "K": 64, "N": 64},
    ))
    pv = validator2.check_pairs()
    if len(pv) != 1 or "Missing completion" not in pv[0]:
        _self_check_fail(f"EventPairValidator: missing completion not caught: {pv}")
    else:
        _self_check_ok("EventPairValidator: missing completion caught")

    # 9. EventPairValidator — completion without acceptance
    validator3 = EventPairValidator()
    validator3.add(PerfEvent(
        event_id="ev-c", kind=EventKind.COMMAND_COMPLETED, seq_id=20,
        parent_workload_id="wl", engine=EngineType.MXU, op=OpType.MMUL,
        programmed_shape={"M": 64, "K": 64, "N": 64},
    ))
    pv = validator3.check_pairs()
    if len(pv) != 1 or "Completion without acceptance" not in pv[0]:
        _self_check_fail(f"EventPairValidator: completion w/o acceptance not caught: {pv}")
    else:
        _self_check_ok("EventPairValidator: completion w/o acceptance caught")

    # 10. PerfEstimate — valid construction
    try:
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
        _self_check_ok("PerfEstimate valid construction")
    except Exception as e:
        _self_check_fail(f"PerfEstimate valid construction: {e}")

    # 11. PerfEstimate — content hash stability
    try:
        h1 = pe.content_hash()
        h2 = pe.content_hash()
        if h1 == h2:
            _self_check_ok("PerfEstimate: content hash stable")
        else:
            _self_check_fail("PerfEstimate: content hash unstable")
    except Exception as e:
        _self_check_fail(f"PerfEstimate content hash: {e}")

    # 12. PerfEstimate — round-trip
    try:
        pe2 = pe.round_trip()
        if pe2.estimated_cycles == pe.estimated_cycles and pe2.content_hash() == pe.content_hash():
            _self_check_ok("PerfEstimate: round-trip successful")
        else:
            _self_check_fail("PerfEstimate: round-trip mismatch")
    except Exception as e:
        _self_check_fail(f"PerfEstimate round-trip: {e}")

    # 13. PerfEstimate — nonpositive estimated_cycles fails
    try:
        PerfEstimate(
            provider_id="spec-block64-v1",
            provider_version="1.0.0",
            domain=DomainType.MXU,
            boundary_id="mxu-64x64x64",
            engine=EngineType.MXU,
            op=OpType.MMUL,
            shape={"M": 64, "K": 64, "N": 64},
            estimated_cycles=0,
            uncertainty_pct=10.0,
            spec_hash="abc123",
        )
        _self_check_fail("PerfEstimate: zero cycles should have failed")
    except Exception:
        _self_check_ok("PerfEstimate: zero cycles rejected")

    # 14. PerfEstimate — NaN uncertainty fails
    try:
        PerfEstimate(
            provider_id="spec-block64-v1",
            provider_version="1.0.0",
            domain=DomainType.MXU,
            boundary_id="mxu-64x64x64",
            engine=EngineType.MXU,
            op=OpType.MMUL,
            shape={"M": 64, "K": 64, "N": 64},
            estimated_cycles=1000,
            uncertainty_pct=float("nan"),
            spec_hash="abc123",
        )
        _self_check_fail("PerfEstimate: NaN uncertainty should have failed")
    except Exception:
        _self_check_ok("PerfEstimate: NaN uncertainty rejected")

    # 15. PerfEstimate — Inf uncertainty fails
    try:
        PerfEstimate(
            provider_id="spec-block64-v1",
            provider_version="1.0.0",
            domain=DomainType.MXU,
            boundary_id="mxu-64x64x64",
            engine=EngineType.MXU,
            op=OpType.MMUL,
            shape={"M": 64, "K": 64, "N": 64},
            estimated_cycles=1000,
            uncertainty_pct=float("inf"),
            spec_hash="abc123",
        )
        _self_check_fail("PerfEstimate: Inf uncertainty should have failed")
    except Exception:
        _self_check_ok("PerfEstimate: Inf uncertainty rejected")

    # 16. PerfEstimate — unknown unit fails
    try:
        PerfEstimate(
            provider_id="spec-block64-v1",
            provider_version="1.0.0",
            domain=DomainType.MXU,
            boundary_id="mxu-64x64x64",
            engine=EngineType.MXU,
            op=OpType.MMUL,
            shape={"M": 64, "K": 64, "N": 64},
            estimated_cycles=1000,
            units="furlongs_per_fortnight",  # type: ignore[arg-type]
            uncertainty_pct=10.0,
            spec_hash="abc123",
        )
        _self_check_fail("PerfEstimate: unknown unit should have failed")
    except Exception:
        _self_check_ok("PerfEstimate: unknown unit rejected")

    # 17. PerfEstimate — extra="forbid" on measured_cycles
    try:
        PerfEstimate.model_validate({
            "provider_id": "spec-block64-v1",
            "provider_version": "1.0.0",
            "domain": "mxu",
            "boundary_id": "mxu-64x64x64",
            "engine": "mxu",
            "op": "mmul",
            "shape": {"M": 64, "K": 64, "N": 64},
            "estimated_cycles": 1000,
            "uncertainty_pct": 10.0,
            "spec_hash": "abc123",
            "measured_cycles": 999,
        })
        _self_check_fail("PerfEstimate: measured_cycles should have been rejected by extra=forbid")
    except ValidationError:
        _self_check_ok("PerfEstimate: measured_cycles rejected by extra=forbid")

    # 18. PerfEstimate — verdict eligibility: architectural+uncalibrated → eligible
    try:
        pe_ok = PerfEstimate(
            provider_id="spec-block64-v1",
            provider_version="1.0.0",
            domain=DomainType.MXU,
            boundary_id="mxu-64x64x64",
            engine=EngineType.MXU,
            op=OpType.MMUL,
            shape={"M": 64, "K": 64, "N": 64},
            estimated_cycles=1000,
            basis=BasisType.ARCHITECTURAL_FORMULA,
            calibration_state=CalibrationState.UNCALIBRATED,
            uncertainty_pct=10.0,
            spec_hash="abc123",
        )
        if pe_ok.is_verdict_eligible():
            _self_check_ok("PerfEstimate: architectural+uncalibrated IS verdict eligible")
        else:
            _self_check_fail("PerfEstimate: architectural+uncalibrated SHOULD be verdict eligible")
    except Exception as e:
        _self_check_fail(f"PerfEstimate verdict eligible: {e}")

    # 19. PerfEstimate — RTL calibrated NOT verdict eligible
    try:
        pe_rtl = PerfEstimate(
            provider_id="spec-block64-v1",
            provider_version="1.0.0",
            domain=DomainType.MXU,
            boundary_id="mxu-64x64x64",
            engine=EngineType.MXU,
            op=OpType.MMUL,
            shape={"M": 64, "K": 64, "N": 64},
            estimated_cycles=1000,
            basis=BasisType.RTL_MEASUREMENT,
            calibration_state=CalibrationState.RTL_CALIBRATED,
            uncertainty_pct=10.0,
            spec_hash="abc123",
        )
        if pe_rtl.is_verdict_eligible():
            _self_check_fail("PerfEstimate: rtl_calibrated should NOT be verdict eligible")
        else:
            _self_check_ok("PerfEstimate: rtl_calibrated correctly NOT verdict eligible")
    except Exception as e:
        _self_check_fail(f"PerfEstimate rtl-verdict: {e}")

    # 20. PerfBand — monotonicity
    try:
        PerfBand(low=10.0, base=20.0, high=30.0)
        _self_check_ok("PerfBand: monotonic OK")
    except Exception as e:
        _self_check_fail(f"PerfBand monotonic: {e}")

    try:
        PerfBand(low=30.0, base=20.0, high=10.0)
        _self_check_fail("PerfBand: non-monotonic should have failed")
    except Exception:
        _self_check_ok("PerfBand: non-monotonic rejected")

    # 21. PerfBand — NaN in band
    try:
        PerfBand(low=float("nan"), base=20.0, high=30.0)
        _self_check_fail("PerfBand: NaN should have failed")
    except Exception:
        _self_check_ok("PerfBand: NaN rejected")

    # 22. PerfReport — sw_overhead_included must be False
    try:
        PerfReport(
            workload_id="wl",
            provider_id="p",
            provider_version="1.0.0",
            cycles=PerfBand(low=10, base=20, high=30),
            canonical_total_cycles=20,
            sw_overhead_included=True,
        )
        _self_check_fail("PerfReport: sw_overhead_included=True should have failed")
    except Exception:
        _self_check_ok("PerfReport: sw_overhead_included=True rejected")

    # 23. PerfArtifact — valid
    try:
        art = PerfArtifact(
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
        if art.is_verdict_eligible():
            _self_check_ok("PerfArtifact: valid + verdict eligible")
        else:
            _self_check_fail("PerfArtifact: should be verdict eligible")
    except Exception as e:
        _self_check_fail(f"PerfArtifact valid: {e}")

    # 24. PerfArtifact — round-trip
    try:
        art2 = art.round_trip()
        if art2.content_hash() == art.content_hash():
            _self_check_ok("PerfArtifact: round-trip hash stable")
        else:
            _self_check_fail("PerfArtifact: round-trip hash mismatch")
    except Exception as e:
        _self_check_fail(f"PerfArtifact round-trip: {e}")

    # 25. PerfArtifact — content hash excludes RTL fields
    try:
        art_no_rtl = PerfArtifact(
            schema_version="1.0.0",
            provider_id="p1", provider_version="1.0.0",
            basis=BasisType.ARCHITECTURAL_FORMULA,
            calibration_state=CalibrationState.UNCALIBRATED,
            domain=DomainType.MXU, boundary_id="b1", spec_hash="abc",
            estimated_cycles=1000, uncertainty_pct=10.0,
        )
        art_rtl = PerfArtifact(
            schema_version="1.0.0",
            provider_id="p1", provider_version="1.0.0",
            basis=BasisType.ARCHITECTURAL_FORMULA,
            calibration_state=CalibrationState.UNCALIBRATED,
            domain=DomainType.MXU, boundary_id="b1", spec_hash="abc",
            estimated_cycles=1000, uncertainty_pct=10.0,
            rtl_head="deadbeef",
            eda_version="2024.1",
        )
        if art_no_rtl.content_hash() == art_rtl.content_hash():
            _self_check_ok("PerfArtifact: content hash excludes RTL fields")
        else:
            _self_check_fail("PerfArtifact: content hash should exclude RTL fields")
    except Exception as e:
        _self_check_fail(f"PerfArtifact hash stability: {e}")

    # 26. PerfArtifact — rtl_calibrated NOT verdict eligible
    try:
        art_rtl2 = PerfArtifact(
            schema_version="1.0.0",
            provider_id="p2", provider_version="1.0.0",
            basis=BasisType.RTL_MEASUREMENT,
            calibration_state=CalibrationState.RTL_CALIBRATED,
            domain=DomainType.MXU, boundary_id="b2", spec_hash="abc",
            estimated_cycles=1000, uncertainty_pct=10.0,
        )
        if not art_rtl2.is_verdict_eligible():
            _self_check_ok("PerfArtifact: rtl_calibrated NOT verdict eligible")
        else:
            _self_check_fail("PerfArtifact: rtl_calibrated should NOT be verdict eligible")
    except Exception as e:
        _self_check_fail(f"PerfArtifact rtl verdict: {e}")

    # 27. Version negotiation
    try:
        v = negotiate_version("1.0.0")
        if v == "1.0.0":
            _self_check_ok("negotiate_version: 1.0.0 accepted")
        else:
            _self_check_fail(f"negotiate_version: expected 1.0.0 got {v}")
    except Exception as e:
        _self_check_fail(f"negotiate_version: {e}")

    try:
        negotiate_version("9.9.9")
        _self_check_fail("negotiate_version: 9.9.9 should have failed")
    except Exception:
        _self_check_ok("negotiate_version: 9.9.9 rejected")

    # 28. PerfEvent — missing shape keys
    try:
        PerfEvent(
            event_id="ev-bad",
            kind=EventKind.COMMAND_ACCEPTED,
            seq_id=1,
            parent_workload_id="wl",
            engine=EngineType.MXU,
            op=OpType.MMUL,
            programmed_shape={"M": 64, "X": 99},  # wrong keys for MXU
        )
        _self_check_fail("PerfEvent: wrong shape keys should have failed")
    except Exception:
        _self_check_ok("PerfEvent: wrong shape keys rejected")

    # 29. PerfReport — cycles band non-monotonic fails
    try:
        PerfReport(
            workload_id="wl",
            provider_id="p",
            provider_version="1.0.0",
            cycles=PerfBand(low=50, base=40, high=60),  # low > base
            canonical_total_cycles=40,
        )
        _self_check_fail("PerfReport: non-monotonic band should have failed")
    except Exception:
        _self_check_ok("PerfReport: non-monotonic band rejected")

    # 30. PerfEstimate — wrong shape keys for engine
    try:
        PerfEstimate(
            provider_id="spec-sfu-v1",
            provider_version="1.0.0",
            domain=DomainType.SFU,
            boundary_id="sfu-128",
            engine=EngineType.SFU,
            op=OpType.SOFTMAX,
            shape={"M": 64, "K": 64, "N": 64},  # should be {elements: ...}
            estimated_cycles=1000,
            uncertainty_pct=5.0,
            spec_hash="abc",
        )
        _self_check_fail("PerfEstimate: wrong shape keys for SFU should have failed")
    except Exception:
        _self_check_ok("PerfEstimate: wrong shape keys for SFU rejected")

    # Summary
    passed = _SELF_CHECK_VERDICT["passed"]
    total = _SELF_CHECK_VERDICT["total"]
    result = {
        "test": "perf_contract.self_check",
        "passed": passed,
        "total": total,
        "verdict": "pass" if passed == total else "fail",
        "details": _SELF_CHECK_VERDICT["details"],
    }
    print(json.dumps(result, indent=2))
    return 0 if passed == total else 1


def run_negative_fixtures(fixture_paths: List[str]) -> int:
    """Load each negative fixture JSON and verify it is rejected by the contract.

    Returns 0 when all are rejected (rejected=N, accepted=0).
    """
    rejected = 0
    accepted = 0
    details = []

    for path in fixture_paths:
        # Try multiple model classes — the first one that fits determines the test
        for model_cls, label in [
            (PerfArtifact, "PerfArtifact"),
            (PerfEstimate, "PerfEstimate"),
            (PerfReport, "PerfReport"),
            (PerfEvent, "PerfEvent"),
        ]:
            ok, err = validate_fixture(path, model_cls)
            if ok:
                accepted += 1
                details.append({"path": path, "model": label, "accepted": True})
                break
            else:
                # This model class rejected; try next
                continue
        else:
            # No model class accepted — that's a rejection
            rejected += 1
            details.append({"path": path, "accepted": False})

    result = {
        "test": "perf_contract.negative_fixtures",
        "rejected": rejected,
        "accepted": accepted,
        "verdict": "pass" if accepted == 0 and rejected == len(fixture_paths) else "fail",
        "details": details,
    }
    print(json.dumps(result, indent=2))
    return 0 if result["verdict"] == "pass" else 1


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Performance contract validation")
    parser.add_argument("--self-check", action="store_true", help="Run built-in self-tests")
    parser.add_argument(
        "--negative-fixtures",
        type=str,
        default="",
        help="Comma-separated paths to negative fixture JSON files",
    )
    args = parser.parse_args()

    if args.self_check:
        return run_self_check()

    if args.negative_fixtures:
        paths = [p.strip() for p in args.negative_fixtures.split(",") if p.strip()]
        if not paths:
            print("Error: --negative-fixtures requires at least one path", file=sys.stderr)
            return 1
        return run_negative_fixtures(paths)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
