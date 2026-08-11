"""PerformanceSession: opt-in event emission for FuncModel/MMIOBridge.

Emits typed PerfEvent instances at command acceptance/completion seams.
Supports profile-only mode (no numerical execution) and batch-mode (profile
but no RX/TX data) for performance characterization without functional evidence.

Usage:
    from timing.perf_session import PerformanceSession
    session = PerformanceSession(workload_id="wl-qwen-blk0")
    bridge.perf_session = session  # opt-in wiring
"""

from __future__ import annotations

from typing import Dict, List, Optional

from timing.perf_contract import (
    EngineType,
    EventKind,
    EventPairValidator,
    OpType,
    PerfEvent,
)

# Engine-to-Op mapping from MMIO register values to contract op enums
_SFU_OP_MAP: Dict[int, OpType] = {
    0: OpType.SOFTMAX,
    1: OpType.LAYERNORM,
    2: OpType.GELU,
    3: OpType.SILU,
    4: OpType.SILU,
    5: OpType.ROPE,
    6: OpType.RMSNORM,
}

_VECTOR_OP_MAP: Dict[int, OpType] = {
    0: OpType.ADD,
    1: OpType.MUL,
    2: OpType.MAX,
    3: OpType.SUM,
    4: OpType.CONV,
    5: OpType.RESID,
}


class PerformanceSession:
    """Opt-in performance event emission session for MMIO command tracking.

    Generates semantic PerfEvent instances (T2 contract) at MMIO command
    acceptance (CMD START) and completion (STATUS=2) seams.  Uses a monotonic
    seq_id counter; EventPairValidator checks for duplicates and missing pairs.

    profile_only mode: when True, the attached bridge skips numerical kernel
    execution but still emits both accepted and completed events.  Evidence
    produced under profile_only mode carries numerical_execution=false and
    must never satisfy a functional gate.

    batch_profile mode: when True, the bridge emits acceptance events but
    skips both numerical execution AND RX/TX data transfer (DMA, PCIe).
    """

    def __init__(
        self,
        workload_id: str = "default",
        profile_only: bool = False,
        batch_profile: bool = False,
    ) -> None:
        self.workload_id = workload_id
        self.profile_only = profile_only
        self.batch_profile = batch_profile
        self._seq_counter: int = 0
        self._events: List[PerfEvent] = []
        self._validator = EventPairValidator()
        self._violations: List[str] = []

    @property
    def numerical_execution(self) -> bool:
        """True when numerical kernels are executed (not profile/batch-only)."""
        return not (self.profile_only or self.batch_profile)

    def next_seq(self) -> int:
        """Return next monotonic seq_id."""
        self._seq_counter += 1
        return self._seq_counter

    def emit_accepted(
        self,
        engine: EngineType,
        op: OpType,
        shape: Dict[str, int],
    ) -> PerfEvent:
        """Emit a command_accepted event and return it.

        Raises ValueError if the event fails validation (Pydantic will
        raise ValidationError for malformed input).
        """
        seq = self.next_seq()
        event = PerfEvent(
            event_id=f"{self.workload_id}-{seq}-a",
            kind=EventKind.COMMAND_ACCEPTED,
            seq_id=seq,
            parent_workload_id=self.workload_id,
            engine=engine,
            op=op,
            programmed_shape=shape,
        )
        self._events.append(event)
        self._violations.extend(self._validator.add(event))
        return event

    def emit_completed(
        self,
        seq_id: int,
        engine: EngineType,
        op: OpType,
        shape: Dict[str, int],
    ) -> PerfEvent:
        """Emit a command_completed event matching the given seq_id."""
        event = PerfEvent(
            event_id=f"{self.workload_id}-{seq_id}-c",
            kind=EventKind.COMMAND_COMPLETED,
            seq_id=seq_id,
            parent_workload_id=self.workload_id,
            engine=engine,
            op=op,
            programmed_shape=shape,
        )
        self._events.append(event)
        self._violations.extend(self._validator.add(event))
        return event

    def emit_ordered(
        self,
        engine: EngineType,
        op: OpType,
        shape: Dict[str, int],
    ) -> PerfEvent:
        """Emit an informational command_ordered event (not pair-tracked)."""
        seq = self.next_seq()
        event = PerfEvent(
            event_id=f"{self.workload_id}-{seq}-o",
            kind=EventKind.COMMAND_ORDERED,
            seq_id=seq,
            parent_workload_id=self.workload_id,
            engine=engine,
            op=op,
            programmed_shape=shape,
        )
        self._events.append(event)
        self._violations.extend(self._validator.add(event))
        return event

    @property
    def events(self) -> List[PerfEvent]:
        """Return a copy of all emitted events."""
        return list(self._events)

    @property
    def violations(self) -> List[str]:
        """All violations including missing-pair checks."""
        v = list(self._violations)
        v.extend(self._validator.check_pairs())
        return v

    @property
    def is_clean(self) -> bool:
        """True when no violations have been detected."""
        return len(self.violations) == 0

    @property
    def accepted_count(self) -> int:
        """Number of command_accepted events emitted."""
        return sum(1 for e in self._events if e.kind == EventKind.COMMAND_ACCEPTED)

    @property
    def completed_count(self) -> int:
        """Number of command_completed events emitted."""
        return sum(1 for e in self._events if e.kind == EventKind.COMMAND_COMPLETED)

    @staticmethod
    def sfu_op(op_code: int) -> Optional[OpType]:
        """Map SFU register op code to contract OpType."""
        return _SFU_OP_MAP.get(op_code)

    @staticmethod
    def vector_op(op_code: int) -> Optional[OpType]:
        """Map Vector register op code to contract OpType."""
        return _VECTOR_OP_MAP.get(op_code)

    def replay_accepted(self, event: PerfEvent) -> PerfEvent:
        """Replay an accepted event (for duplicate-injection testing).

        The event is re-validated and added to the stream.  Its event_id
        is preserved as-is, so a duplicate will be caught by the validator.
        """
        self._events.append(event)
        self._violations.extend(self._validator.add(event))
        return event
