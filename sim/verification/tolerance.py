"""Tolerance and Provenance models for verification scenarios.

ToleranceConfig defines the numerical comparison tolerances for expected-vs-actual
observations. Provenance records where a scenario came from so evidence can
be traced back to its source.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True, slots=True)
class ToleranceConfig:
    """Numerical comparison tolerances for expected-vs-actual observations.

    Attributes:
        int32_bit_exact: If True, INT32 values must match bit-for-bit.
        fp16_abs_tol: Absolute tolerance for FP16 comparisons.
        fp16_rel_tol: Relative tolerance for FP16 comparisons.
        fp32_abs_tol: Absolute tolerance for FP32 comparisons.
        fp32_rel_tol: Relative tolerance for FP32 comparisons.
    """

    int32_bit_exact: bool = True
    fp16_abs_tol: float = 2e-3
    fp16_rel_tol: float = 1e-2
    fp32_abs_tol: float = 1e-5
    fp32_rel_tol: float = 1e-4

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict with deterministic key order."""
        return {
            "int32_bit_exact": self.int32_bit_exact,
            "fp16_abs_tol": self.fp16_abs_tol,
            "fp16_rel_tol": self.fp16_rel_tol,
            "fp32_abs_tol": self.fp32_abs_tol,
            "fp32_rel_tol": self.fp32_rel_tol,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ToleranceConfig":
        """Deserialize from a dict."""
        return cls(
            int32_bit_exact=data.get("int32_bit_exact", True),
            fp16_abs_tol=data.get("fp16_abs_tol", 2e-3),
            fp16_rel_tol=data.get("fp16_rel_tol", 1e-2),
            fp32_abs_tol=data.get("fp32_abs_tol", 1e-5),
            fp32_rel_tol=data.get("fp32_rel_tol", 1e-4),
        )

    @classmethod
    def from_testcase_config(cls, cfg) -> "ToleranceConfig":
        """Build ToleranceConfig from a TestCaseConfig (sim.rtl_soc_runner)."""
        return cls(
            int32_bit_exact=getattr(cfg, "int32_bit_exact", True),
            fp16_abs_tol=getattr(cfg, "fp16_abs_tol", 2e-3),
            fp16_rel_tol=getattr(cfg, "fp16_rel_tol", 1e-2),
        )


@dataclass(frozen=True, slots=True)
class Provenance:
    """Source and origin metadata for a verification scenario.

    Records where the test data came from and when it was created so
    evidence can be traced and stale data detected.

    Attributes:
        case_id: Original test case identifier (e.g., 'FM-SOC-001').
        source_file: Path to the originating .npz or config file.
        generator_version: Version of the generator that produced this scenario.
        model_hash: Hash of the model weights if applicable.
        created_at: ISO 8601 timestamp of scenario creation.
        abi_version: ABI schema version used for this scenario.
    """

    case_id: Optional[str] = None
    source_file: Optional[str] = None
    generator_version: Optional[str] = None
    model_hash: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    abi_version: Optional[int] = None

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict with deterministic key order."""
        result: dict[str, object] = {"created_at": self.created_at}
        if self.case_id is not None:
            result["case_id"] = self.case_id
        if self.source_file is not None:
            result["source_file"] = self.source_file
        if self.generator_version is not None:
            result["generator_version"] = self.generator_version
        if self.model_hash is not None:
            result["model_hash"] = self.model_hash
        if self.abi_version is not None:
            result["abi_version"] = self.abi_version
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "Provenance":
        """Deserialize from a dict."""
        return cls(
            case_id=data.get("case_id"),
            source_file=data.get("source_file"),
            generator_version=data.get("generator_version"),
            model_hash=data.get("model_hash"),
            created_at=data.get("created_at",
                               datetime.now(timezone.utc).isoformat()),
            abi_version=data.get("abi_version"),
        )
