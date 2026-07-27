"""Versioned Scenario — the transport-independent verification scenario.

A Scenario is a self-contained description of a verification test: what
actions to perform on the DUT, what to observe, what tolerance to apply,
where the data came from, and what evidence was collected.

Design principles:
    - Transport-independent: no cocotb signal names, Func Model objects,
      BAR addresses, or FPGA driver details.
    - Deterministic serialization: to_dict() → from_dict() round-trip
      preserves all data.
    - Versioned: the scenario schema version is explicit so readers can
      detect and reject incompatible formats.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sim.verification.operation_classifier import (
    OperationClass,
    classify_action,
    validate_scenario_operations,
)
from sim.verification.observation import Observation, ObservationType
from sim.verification.tolerance import ToleranceConfig, Provenance


# ── Action ──────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Action:
    """A single operation to perform on the DUT.

    Each action has a type (mmio_write, sram_preload, etc.), a classification
    (frontdoor, backdoor, diagnostic), parameters for that operation, and
    optional metadata.

    Attributes:
        action_type: The kind of operation (mmio_write, sram_preload, etc.)
        classification: How this action accesses the DUT.
        parameters: Action-specific parameters (address, value, data, etc.)
        metadata: Arbitrary key-value metadata.
        action_id: Optional unique identifier for cross-referencing.
    """

    action_type: str
    classification: Optional[OperationClass] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    action_id: Optional[str] = None

    def __post_init__(self):
        """Auto-classify if no explicit classification given."""
        if not self.action_type:
            raise ValueError("action_type must not be empty")
        if self.classification is None:
            self.classification = classify_action(self.action_type)

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict with deterministic key order."""
        result: dict[str, object] = {
            "action_type": self.action_type,
            "classification": self.classification.value,
        }
        if self.action_id is not None:
            result["action_id"] = self.action_id
        if self.parameters:
            result["parameters"] = dict(sorted(self.parameters.items()))
        if self.metadata:
            result["metadata"] = dict(sorted(self.metadata.items()))
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "Action":
        """Deserialize from a dict."""
        classification = OperationClass(data.get("classification", "frontdoor"))
        return cls(
            action_type=data["action_type"],
            classification=classification,
            parameters=data.get("parameters", {}),
            metadata=data.get("metadata", {}),
            action_id=data.get("action_id"),
        )

    # ── Factory methods for common actions ───────────────────────────────

    @classmethod
    def mmio_write(cls, address: int, value: int,
                   action_id: Optional[str] = None) -> "Action":
        """Create a frontdoor MMIO write action."""
        return cls(
            action_type="mmio_write",
            classification=OperationClass.frontdoor,
            parameters={"address": address, "value": value},
            action_id=action_id,
        )

    @classmethod
    def mmio_read(cls, address: int,
                  action_id: Optional[str] = None) -> "Action":
        """Create a frontdoor MMIO read action."""
        return cls(
            action_type="mmio_read",
            classification=OperationClass.frontdoor,
            parameters={"address": address},
            action_id=action_id,
        )

    @classmethod
    def sram_preload(cls, offset: int, data_bytes: bytes,
                     action_id: Optional[str] = None) -> "Action":
        """Create an initialization backdoor SRAM preload."""
        return cls(
            action_type="sram_preload",
            classification=OperationClass.allowed_init_backdoor,
            parameters={"offset": offset, "data_hex": data_bytes.hex()},
            action_id=action_id,
        )

    @classmethod
    def dram_preload(cls, offset: int, data_bytes: bytes,
                     action_id: Optional[str] = None) -> "Action":
        """Create an initialization backdoor DRAM preload."""
        return cls(
            action_type="dram_preload",
            classification=OperationClass.allowed_init_backdoor,
            parameters={"offset": offset, "data_hex": data_bytes.hex()},
            action_id=action_id,
        )

    @classmethod
    def doorbell(cls, host_tail: int, desc_addr: Optional[int] = None,
                 action_id: Optional[str] = None) -> "Action":
        """Create a frontdoor doorbell action."""
        params: dict[str, int] = {"host_tail": host_tail}
        if desc_addr is not None:
            params["desc_addr"] = desc_addr
        return cls(
            action_type="doorbell",
            classification=OperationClass.frontdoor,
            parameters=params,
            action_id=action_id,
        )

    @classmethod
    def wait_irq(cls, source: int,
                 action_id: Optional[str] = None) -> "Action":
        """Create a frontdoor wait-for-interrupt action."""
        return cls(
            action_type="wait_irq",
            classification=OperationClass.frontdoor,
            parameters={"source": source},
            action_id=action_id,
        )

    @classmethod
    def poll_status(cls, address: int, mask: int = 0x2,
                    timeout_cycles: int = 100_000,
                    action_id: Optional[str] = None) -> "Action":
        """Create a frontdoor poll-status action."""
        return cls(
            action_type="poll_status",
            classification=OperationClass.frontdoor,
            parameters={
                "address": address,
                "mask": mask,
                "timeout_cycles": timeout_cycles,
            },
            action_id=action_id,
        )

    @classmethod
    def sram_readback(cls, offset: int, size: int,
                      action_id: Optional[str] = None) -> "Action":
        """Create an observation backdoor SRAM readback."""
        return cls(
            action_type="sram_readback",
            classification=OperationClass.allowed_obs_backdoor,
            parameters={"offset": offset, "size": size},
            action_id=action_id,
        )

    @classmethod
    def pcie_write(cls, address: int, data_bytes: bytes,
                   action_id: Optional[str] = None) -> "Action":
        """Create a frontdoor PCIe TLP write."""
        return cls(
            action_type="pcie_write",
            classification=OperationClass.frontdoor,
            parameters={"address": address, "data_hex": data_bytes.hex()},
            action_id=action_id,
        )


# ── EvidenceRecord ─────────────────────────────────────────────────────


@dataclass(slots=True)
class EvidenceRecord:
    """Evidence collected from executing a scenario against a DUT adapter.

    Records the DUT mode, firmware mode, ABI version, and observations
    made during execution. Multiple evidence records can exist for the
    same scenario run against different adapters.

    Attributes:
        record_id: Unique identifier for this evidence record.
        timestamp: ISO 8601 timestamp of evidence collection.
        dut_adapter: Name of the DUT adapter used (e.g., 'FuncModel', 'RTLSoC').
        firmware_mode: Firmware mode used (e.g., 'python', 'spike', 'compiled').
        abi_version: ABI schema version used, if known.
        verdict: Outcome of the scenario (pass, fail, error).
        actual_observations: What was actually observed from the DUT.
        metadata: Arbitrary key-value metadata.
    """

    record_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    dut_adapter: str = ""
    firmware_mode: str = "python"
    abi_version: Optional[int] = None
    verdict: str = "pending"
    actual_observations: List[Observation] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict with deterministic key order."""
        return {
            "record_id": self.record_id,
            "timestamp": self.timestamp,
            "dut_adapter": self.dut_adapter,
            "firmware_mode": self.firmware_mode,
            "abi_version": self.abi_version,
            "verdict": self.verdict,
            "actual_observations": [o.to_dict() for o in self.actual_observations],
            "metadata": dict(sorted(self.metadata.items())) if self.metadata else {},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvidenceRecord":
        """Deserialize from a dict."""
        return cls(
            record_id=data["record_id"],
            timestamp=data.get("timestamp",
                               datetime.now(timezone.utc).isoformat()),
            dut_adapter=data.get("dut_adapter", ""),
            firmware_mode=data.get("firmware_mode", "python"),
            abi_version=data.get("abi_version"),
            verdict=data.get("verdict", "pending"),
            actual_observations=[
                Observation.from_dict(o)
                for o in data.get("actual_observations", [])
            ],
            metadata=data.get("metadata", {}),
        )


# ── Scenario ────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Scenario:
    """A transport-independent verification scenario.

    A Scenario bundles everything needed to drive a verification test:
    what actions to perform, what observations to expect, what tolerances
    to apply, where the data came from, and what evidence was collected.

    Scenarios are versioned; readers should check scenario_version before
    interpreting the schema.

    Attributes:
        scenario_id: Unique identifier for this scenario.
        scenario_version: Schema version (increment on incompatible changes).
        description: Human-readable description of what this scenario tests.
        actions: Ordered list of actions to perform on the DUT.
        expected_observations: What to observe and compare after execution.
        tolerance: Default tolerance for numerical comparisons.
        provenance: Source and origin metadata.
        evidence: Evidence records collected during execution.
        metadata: Arbitrary key-value metadata.
    """

    scenario_id: str
    scenario_version: int = 1
    description: str = ""
    actions: List[Action] = field(default_factory=list)
    expected_observations: List[Observation] = field(default_factory=list)
    tolerance: ToleranceConfig = field(default_factory=ToleranceConfig)
    provenance: Provenance = field(default_factory=Provenance)
    evidence: List[EvidenceRecord] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ── Validation ────────────────────────────────────────────────────

    def validate(self) -> list[str]:
        """Validate this scenario and return a list of error messages.

        Checks:
            - scenario_id is non-empty
            - No undeclared backdoor operations
            - No diagnostic-only operations in signoff scenarios
        """
        errors: list[str] = []

        if not self.scenario_id:
            errors.append("scenario_id must not be empty")

        if self.scenario_version < 1:
            errors.append(f"scenario_version must be >= 1, got {self.scenario_version}")

        # Check for undeclared backdoor operations
        errors.extend(validate_scenario_operations(self.actions))

        # Check for diagnostic-only actions (not allowed in normal scenarios)
        for i, action in enumerate(self.actions):
            if action.classification == OperationClass.diagnostic:
                errors.append(
                    f"Action[{i}] ({action.action_type}): diagnostic-only "
                    f"actions are not allowed in verification scenarios"
                )

        return errors

    def reject_undeclared_backdoors(self) -> None:
        """Raise ValueError if any action has an invalid classification.

        This is the enforcement point — scenario runners should call this
        before execution to prevent invalid scenarios from running.
        """
        errors = self.validate()
        if errors:
            raise ValueError(
                f"Scenario {self.scenario_id} has {len(errors)} validation error(s):\n"
                + "\n".join(f"  - {e}" for e in errors)
            )

    # ── Serialization ─────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict with deterministic key order."""
        return {
            "scenario_id": self.scenario_id,
            "scenario_version": self.scenario_version,
            "description": self.description,
            "actions": [a.to_dict() for a in self.actions],
            "expected_observations": [o.to_dict() for o in self.expected_observations],
            "tolerance": self.tolerance.to_dict(),
            "provenance": self.provenance.to_dict(),
            "evidence": [e.to_dict() for e in self.evidence],
            "metadata": dict(sorted(self.metadata.items())) if self.metadata else {},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Scenario":
        """Deserialize from a dict.

        Raises:
            ValueError: If scenario_version is incompatible.
        """
        version = data.get("scenario_version", 1)
        if version != 1:
            raise ValueError(
                f"Unsupported scenario_version {version} "
                f"(this reader supports version 1)"
            )

        return cls(
            scenario_id=data["scenario_id"],
            scenario_version=version,
            description=data.get("description", ""),
            actions=[Action.from_dict(a) for a in data.get("actions", [])],
            expected_observations=[
                Observation.from_dict(o)
                for o in data.get("expected_observations", [])
            ],
            tolerance=ToleranceConfig.from_dict(data.get("tolerance", {})),
            provenance=Provenance.from_dict(data.get("provenance", {})),
            evidence=[EvidenceRecord.from_dict(e) for e in data.get("evidence", [])],
            metadata=data.get("metadata", {}),
        )

    def to_json(self) -> str:
        """Serialize to a deterministic JSON string."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2, separators=(",", ": "))

    def content_hash(self) -> str:
        """Compute a deterministic content hash for this scenario.

        Two equal scenarios produce the same hash. Useful for comparing
        scenario content without comparing identity.
        """
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    # ── Evidence collection ───────────────────────────────────────────

    def add_evidence(self, record: EvidenceRecord) -> None:
        """Append an evidence record."""
        self.evidence.append(record)

    def latest_evidence(self) -> Optional[EvidenceRecord]:
        """Return the most recent evidence record, if any."""
        return self.evidence[-1] if self.evidence else None
