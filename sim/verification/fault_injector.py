"""Fault injection hooks for deterministic testbench fault testing.

FaultInjector provides adapter-level injection hooks that are unavailable
through public production Runtime APIs. Each fault class models a specific
hardware or firmware failure scenario that the verification infrastructure
must detect and classify.

Key design rules:
    - Fault hooks are DISABLED by default. The adapter must opt in via
      enable_fault() / disable_fault().
    - Fault injections record `injection_applied=True` in evidence metadata.
    - Fault hooks must NOT be reachable via the public C Host Runtime API.
      They are Python-side, adapter-level only.
    - The Scoreboard classifies faults independently — it operates only on
      Observation objects, never on adapter internals.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class FaultClass(str, Enum):
    """Deterministic fault classes for testbench injection.

    Each fault class models a specific failure mode that can occur in
    real hardware/firmware. The verification infrastructure must detect
    and correctly classify each one.
    """

    data_corruption = "data_corruption"
    """SRAM/DRAM data corrupted during preload or readback."""

    wrong_descriptor = "wrong_descriptor"
    """Wrong descriptor field (wrong opcode, wrong address)."""

    unsupported_opcode = "unsupported_opcode"
    """Opcode not supported by hardware/firmware."""

    ring_overflow = "ring_overflow"
    """Ring buffer overflow — tail wraps past head."""

    stalled_head = "stalled_head"
    """Head pointer does not advance after completion."""

    wrong_completion = "wrong_completion"
    """Wrong completion status reported."""

    dropped_interrupt = "dropped_interrupt"
    """Interrupt is dropped (never delivered)."""

    duplicated_interrupt = "duplicated_interrupt"
    """Interrupt fires twice for the same event."""

    timeout = "timeout"
    """Operation times out (never completes)."""

    engine_error = "engine_error"
    """Engine error status (hardware fault)."""

    reset_during_command = "reset_during_command"
    """Reset occurs during active command execution."""


# ── Fault classification in observations ──────────────────────────────

# Observation signatures that indicate each fault class.
# The Scoreboard uses these to classify faults from Observation data.
#
# Each entry is (key_path, expected_pattern) where key_path is a dot-separated
# path into Observation.data (e.g., "status" for data["status"]).
_FAULT_SIGNATURES: Dict[FaultClass, List[tuple]] = {
    FaultClass.data_corruption: [
        ("raw_hex", "__DATA_CORRUPTED__"),  # Custom marker injected by fault hook
    ],
    FaultClass.wrong_descriptor: [
        ("desc_error", True),  # Descriptor error flag in observation
    ],
    FaultClass.unsupported_opcode: [
        ("unsupported_opcode", True),
        ("opcode_error", True),
    ],
    FaultClass.ring_overflow: [
        ("ring_overflow", True),
    ],
    FaultClass.stalled_head: [
        ("stalled_head", True),
        ("head_stalled", True),
    ],
    FaultClass.wrong_completion: [
        ("completion_error", True),
        ("wrong_status", True),
    ],
    FaultClass.dropped_interrupt: [
        ("irq_dropped", True),
        ("interrupt_dropped", True),
    ],
    FaultClass.duplicated_interrupt: [
        ("irq_duplicate", True),
        ("interrupt_duplicate", True),
    ],
    FaultClass.timeout: [
        ("timeout", True),
    ],
    FaultClass.engine_error: [
        ("engine_error", True),
        ("hw_error", True),
    ],
    FaultClass.reset_during_command: [
        ("reset_during_cmd", True),
    ],
}


@dataclass
class FaultInjectionRecord:
    """Record of a single fault injection.

    Attached to evidence metadata so downstream consumers can verify
    that a fault was indeed injected and that the scoreboard detected it.
    """

    fault_class: str
    injection_applied: bool = False
    injection_params: Dict[str, Any] = field(default_factory=dict)
    detected_by_scoreboard: bool = False
    detected_classification: Optional[str] = None


@dataclass
class FaultInjector:
    """Adapter-level fault injection hooks.

    The FaultInjector lives on the FuncModelAdapter. It provides hooks
    that the adapter checks before/after each action to inject faults
    into the DUT behavior.

    Faults are DISABLED by default. Call enable_fault(fault_class, **params)
    to activate a specific fault.

    Usage:
        injector = FaultInjector()
        injector.enable_fault(FaultClass.data_corruption, offset=0x100, count=16)
        # ... run scenario ...
        records = injector.flush_records()
        # records includes injection_applied=True entries for activated faults
    """

    # Active fault configuration: fault_class → injection params
    _active_faults: Dict[FaultClass, Dict[str, Any]]

    # Records of applied injections for evidence
    _records: List[FaultInjectionRecord]

    # Whether any fault was injected at all (for negative test: "injection not
    # applied is failure")
    _any_injection_applied: bool

    def __init__(self):
        self._active_faults = {}
        self._records = []
        self._any_injection_applied = False

    # ── Public API ────────────────────────────────────────────────────

    def enable_fault(self, fault_class: FaultClass, **params: Any) -> None:
        """Enable a fault for the next applicable action.

        Args:
            fault_class: The fault class to enable.
            **params: Fault-specific parameters (e.g., offset, count, opcode, etc.)
        """
        self._active_faults[fault_class] = params

    def disable_fault(self, fault_class: FaultClass) -> None:
        """Disable a previously enabled fault."""
        self._active_faults.pop(fault_class, None)

    def disable_all(self) -> None:
        """Disable all active faults."""
        self._active_faults.clear()

    def is_active(self, fault_class: FaultClass) -> bool:
        """Check whether a fault is currently active."""
        return fault_class in self._active_faults

    def get_params(self, fault_class: FaultClass) -> Optional[Dict[str, Any]]:
        """Get the injection params for an active fault, or None."""
        return self._active_faults.get(fault_class)

    def record_injection(
        self,
        fault_class: FaultClass,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record that a fault was injected.

        Called by the adapter after successfully injecting a fault into
        the DUT behavior.

        Args:
            fault_class: The fault class that was injected.
            params: The injection parameters used (defaults to active params).
        """
        record_params = params or self._active_faults.get(fault_class, {})
        record = FaultInjectionRecord(
            fault_class=fault_class.value,
            injection_applied=True,
            injection_params=dict(record_params),
        )
        self._records.append(record)
        self._any_injection_applied = True
        # One-shot: disable after injection
        self._active_faults.pop(fault_class, None)

    def flush_records(self) -> List[FaultInjectionRecord]:
        """Return all injection records and clear the internal list.

        Returns:
            List of FaultInjectionRecord for evidence metadata.
        """
        records = list(self._records)
        self._records.clear()
        return records

    @property
    def any_injection_applied(self) -> bool:
        """Whether any fault has been injected during this adapter session."""
        return self._any_injection_applied

    # ── Fault injection helpers (called by adapter) ───────────────────

    def inject_data_corruption(
        self, data: bytes, params: Optional[Dict[str, Any]] = None
    ) -> bytes:
        """Corrupt a data buffer for SRAM/DRAM preload or readback.

        Inverts bytes at the specified offset range. If no offset specified,
        corrupts all bytes.

        Args:
            data: The original data bytes.
            params: Injection params (offset, count, value).

        Returns:
            Corrupted data bytes.
        """
        p = params or self._active_faults.get(FaultClass.data_corruption, {})
        if not p and not self.is_active(FaultClass.data_corruption):
            return data

        result = bytearray(data)
        offset = p.get("offset", 0)
        count = p.get("count", min(len(data), 16))
        corrupt_value = p.get("value", 0xFF)

        for i in range(offset, min(offset + count, len(result))):
            result[i] = (result[i] ^ corrupt_value) & 0xFF

        return bytes(result)

    def inject_wrong_descriptor(
        self, opcode: int, desc_addr: int, params: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """Inject wrong descriptor fields.

        Args:
            opcode: Original opcode.
            desc_addr: Original descriptor address.
            params: Injection params (wrong_opcode, wrong_addr).

        Returns:
            (modified_opcode, modified_desc_addr)
        """
        p = params or self._active_faults.get(FaultClass.wrong_descriptor, {})
        if not p and not self.is_active(FaultClass.wrong_descriptor):
            return (opcode, desc_addr)

        new_opcode = p.get("wrong_opcode", opcode + 0x100)
        new_addr = p.get("wrong_addr", desc_addr + 0x10000)
        return (new_opcode, new_addr)

    def inject_unsupported_opcode(self, opcode: int) -> int:
        """Replace a valid opcode with an unsupported one.

        Args:
            opcode: Original opcode.

        Returns:
            An unsupported opcode (0xFF).
        """
        params = self._active_faults.get(FaultClass.unsupported_opcode, {})
        return params.get("unsupported_opcode", 0xFF)

    def inject_ring_overflow(self, tail: int, ring_size: int = 16) -> int:
        """Inject a ring overflow by setting tail beyond capacity.

        Args:
            tail: Original tail value.
            ring_size: Ring buffer capacity.

        Returns:
            Overflow tail value (tail + ring_size + 1).
        """
        params = self._active_faults.get(FaultClass.ring_overflow, {})
        overflow = params.get("overflow_by", 10)
        return tail + ring_size + overflow

    def inject_stalled_head(self, head: int) -> int:
        """Inject a stalled head (head does not advance).

        Args:
            head: Original head value.

        Returns:
            Same head value (stalled).
        """
        return head

    def inject_wrong_completion(
        self, status: int, params: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Inject a wrong completion status.

        Args:
            status: Original completion status.
            params: Injection params (wrong_status). If None, uses active fault params.

        Returns:
            Corrupted status (0xDEAD or configured value).
        """
        p = params or self._active_faults.get(FaultClass.wrong_completion, {})
        return p.get("wrong_status", 0xDEAD)

    def inject_dropped_interrupt(self) -> bool:
        """Inject a dropped interrupt by suppressing IRQ delivery.

        Returns:
            True (should be used to suppress IRQ).
        """
        return True

    def inject_duplicated_interrupt(self) -> bool:
        """Inject a duplicated interrupt by triggering IRQ twice.

        Returns:
            True (IRQ should fire).
        """
        return True

    @staticmethod
    def classify_faults_from_observations(
        observations: List["Observation"],  # type: ignore[name-defined]
    ) -> Set[FaultClass]:
        """Classify faults from observation data.

        This is the Scoreboard's perspective: it examines observations
        for fault signatures and returns the set of detected fault classes.

        This method does NOT depend on any DUT internals — it operates
        only on Observation objects.

        Args:
            observations: List of Observation objects to examine.

        Returns:
            Set of FaultClass values detected in the observations.
        """
        detected: Set[FaultClass] = set()

        for obs in observations:
            obs_data = getattr(obs, "data", {})
            if not obs_data:
                continue

            for fault_class, signatures in _FAULT_SIGNATURES.items():
                for key_path, expected_value in signatures:
                    value = _get_nested(obs_data, key_path)
                    if value == expected_value:
                        detected.add(fault_class)
                        break  # One signature match is enough per fault

        return detected


def _get_nested(data: dict, key_path: str) -> Any:
    """Get a nested value from a dict by dot-separated key path.

    Args:
        data: The dict to search.
        key_path: Dot-separated path (e.g., "status" or "desc_error").

    Returns:
        The value at the path, or None if any key is missing.
    """
    keys = key_path.split(".")
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current
