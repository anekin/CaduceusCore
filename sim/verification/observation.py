"""Observation records for verification scenarios.

Observations are typed records of what is expected (or was observed) from
the DUT. They are transport-independent — no cocotb signal names, no Func Model
objects, no raw bytes without type annotation.
"""

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from sim.verification.tolerance import ToleranceConfig


class ObservationType(str, Enum):
    """Known observation types."""

    mmio_value = "mmio_value"
    sram_data = "sram_data"
    dram_data = "dram_data"
    pcie_readback = "pcie_readback"
    completion_status = "completion_status"
    interrupt_status = "interrupt_status"
    timing_measurement = "timing_measurement"
    generic = "generic"


@dataclass(frozen=True, slots=True)
class Observation:
    """A single typed observation — expected or actual.

    Observations are named, typed records of DUT state at a point in time.
    The data dict contains typed values (never raw bytes without a dtype).

    Attributes:
        observation_id: Unique identifier within a scenario.
        observation_type: What kind of data this observation represents.
        address: Optional MMIO or memory address associated with this observation.
        size: Optional size in bytes of the observed data.
        data: Typed observation data (arrays stored as lists-of-lists for JSON compat).
        tolerance: Per-observation tolerance override (if None, use scenario default).
        metadata: Arbitrary key-value metadata.

    Serialization: to_dict() produces deterministic key-ordered output so two
    equal Observations produce identical JSON.
    """

    observation_id: str
    observation_type: ObservationType = ObservationType.mmio_value
    address: Optional[int] = None
    size: Optional[int] = None
    data: Dict[str, Any] = field(default_factory=dict)
    tolerance: Optional[ToleranceConfig] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict with deterministic key order."""
        result: dict[str, object] = {
            "observation_id": self.observation_id,
            "observation_type": self.observation_type.value,
        }
        if self.address is not None:
            result["address"] = self.address
        if self.size is not None:
            result["size"] = self.size
        if self.data:
            result["data"] = dict(sorted(self.data.items()))
        if self.tolerance is not None:
            result["tolerance"] = self.tolerance.to_dict()
        if self.metadata:
            result["metadata"] = dict(sorted(self.metadata.items()))
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "Observation":
        """Deserialize from a dict."""
        return cls(
            observation_id=data["observation_id"],
            observation_type=ObservationType(data.get("observation_type", "generic")),
            address=data.get("address"),
            size=data.get("size"),
            data=data.get("data", {}),
            tolerance=ToleranceConfig.from_dict(data["tolerance"]) if "tolerance" in data else None,
            metadata=data.get("metadata", {}),
        )

    def content_hash(self) -> str:
        """Compute a deterministic content hash for this observation.

        Useful for comparing observation content without comparing identity.
        Two Observations with the same data will produce the same hash.
        """
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    # ── Factory methods for common observation patterns ──────────────────

    @classmethod
    def mmio_read(cls, obs_id: str, address: int, expected_value: int,
                  tolerance: Optional[ToleranceConfig] = None) -> "Observation":
        """Create an MMIO read observation."""
        return cls(
            observation_id=obs_id,
            observation_type=ObservationType.mmio_value,
            address=address,
            data={"value": expected_value},
            tolerance=tolerance,
        )

    @classmethod
    def sram_readback(cls, obs_id: str, offset: int, size: int,
                      dtype: str = "int32") -> "Observation":
        """Create an SRAM readback observation spec."""
        return cls(
            observation_id=obs_id,
            observation_type=ObservationType.sram_data,
            address=offset,
            size=size,
            data={"dtype": dtype},
        )

    @classmethod
    def dram_readback(cls, obs_id: str, offset: int, size: int,
                      dtype: str = "int32") -> "Observation":
        """Create a DRAM readback observation spec."""
        return cls(
            observation_id=obs_id,
            observation_type=ObservationType.dram_data,
            address=offset,
            size=size,
            data={"dtype": dtype},
        )

    @classmethod
    def completion(cls, obs_id: str, expected_status: int = 0) -> "Observation":
        """Create a completion status observation."""
        return cls(
            observation_id=obs_id,
            observation_type=ObservationType.completion_status,
            data={"status": expected_status},
        )
