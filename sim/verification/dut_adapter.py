"""DUT adapter contract — async interface for driving a Device Under Test.

The DUTAdapter abstract class defines the contract that Func Model, RTL
(cocotb), and FPGA adapters must implement. It is transport-independent:
no cocotb signal names, Func Model objects, BAR addresses, or FPGA driver
details appear in the contract.

Adapters are async because RTL simulation is inherently async (cocotb
triggers), and the contract should work uniformly across all transports.

A FakeDUTAdapter is provided for testing the contract without any real
hardware or simulation.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from verification.scenario import Action
from verification.observation import Observation
from verification.operation_classifier import OperationClass


class DUTError(Exception):
    """Base error raised by DUT adapters."""
    pass


class DUTTimeoutError(DUTError):
    """Raised when a DUT operation times out."""
    pass


class DUTConnectionError(DUTError):
    """Raised when a DUT connection fails."""
    pass


class DUTAdapter(ABC):
    """Abstract async DUT adapter contract.

    Every DUT adapter (Func Model, RTL, FPGA) implements this interface.
    The adapter is responsible for translating transport-independent
    Actions and Observations into transport-specific operations.

    Lifecycle:
        adapter = ConcreteAdapter(...)
        await adapter.connect()
        await adapter.reset()
        for action in scenario.actions:
            await adapter.execute_action(action)
        obs = await adapter.observe(observation_spec)
        await adapter.disconnect()

    Implementations:
        - FuncModelAdapter: Wraps FuncModel Python API
        - RTLAdapter: Wraps CocotbBridge + RTLSoCRunner
        - FPGADapter: Wraps UIO/VFIO userspace transport (future)
    """

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the DUT.

        Raises:
            DUTConnectionError: If connection fails.
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down connection to the DUT."""

    @abstractmethod
    async def reset(self) -> None:
        """Reset the DUT to a known initial state.

        After reset, the DUT should be in the same state as after
        power-on. All pending operations are cancelled, all buffers
        are cleared, and all registers return to reset values.
        """

    @abstractmethod
    async def execute_action(self, action: Action) -> None:
        """Execute a single action on the DUT.

        The adapter translates the transport-independent Action into
        transport-specific operations. For example, a frontdoor MMIO
        write becomes an APB write on RTL, a FuncModel register write
        on Func Model, or a BAR write on FPGA.

        Args:
            action: The action to execute. The adapter checks
                    action.classification and may reject actions
                    classified as diagnostic in production builds.

        Raises:
            DUTTimeoutError: If the action times out.
            DUTError: If the action fails for any other reason.
            ValueError: If the action type is not supported.
        """

    @abstractmethod
    async def observe(self, observation_spec: Observation) -> Observation:
        """Observe the DUT state according to an observation specification.

        Returns an Observation with actual data populated. The returned
        Observation has the same observation_id and type as the spec,
        but the data dict contains actual observed values.

        Args:
            observation_spec: What to observe (address, size, dtype, etc.)

        Returns:
            An Observation with actual data.

        Raises:
            DUTError: If observation fails.
        """

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Human-readable adapter name for evidence records."""

    @property
    @abstractmethod
    def firmware_mode(self) -> str:
        """Current firmware mode ('python', 'spike', 'compiled', etc.)."""


# ── Fake DUT Adapter ──────────────────────────────────────────────────


@dataclass
class FakeDUTAdapter(DUTAdapter):
    """In-memory fake DUT adapter for testing the contract.

    Maintains a simple memory map and MMIO register space. Actions
    write to/read from this internal state. Useful for:
        - Testing the adapter contract without real hardware
        - Testing the scoreboard's comparison logic
        - Testing scenario validation

    The fake DUT rejects diagnostic-classified actions and undeclared
    backdoors by default (configurable via reject_undeclared_backdoors).
    """

    accept_diagnostics: bool = False

    # Internal state
    _mmio: Dict[int, int] = field(default_factory=dict)
    _sram: Dict[int, bytes] = field(default_factory=dict)
    _dram: Dict[int, bytes] = field(default_factory=dict)
    _pcie_space: Dict[int, bytes] = field(default_factory=dict)
    _doorbell_tail: int = 0
    _doorbell_head: int = 0
    _irq_pending: Dict[int, bool] = field(default_factory=dict)
    _status_register: int = 0
    _connected: bool = False
    _observation_store: Dict[str, bytes] = field(default_factory=dict)

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def reset(self) -> None:
        """Reset fake DUT to power-on state."""
        self._mmio.clear()
        self._sram.clear()
        self._dram.clear()
        self._pcie_space.clear()
        self._doorbell_tail = 0
        self._doorbell_head = 0
        self._irq_pending.clear()
        self._status_register = 0
        self._observation_store.clear()

    async def execute_action(self, action: Action) -> None:
        """Execute an action on the fake DUT.

        Raises:
            ValueError: If the action is diagnostic-classified and
                        accept_diagnostics is False, or if the action
                        type is unrecognized.
        """
        self._check_connected()

        # Reject diagnostic actions (unless explicitly allowed)
        if (action.classification == OperationClass.diagnostic
                and not self.accept_diagnostics):
            raise ValueError(
                f"Action '{action.action_id or action.action_type}' "
                f"is classified as diagnostic — rejected by FakeDUTAdapter"
            )

        params = action.parameters

        if action.action_type == "mmio_write":
            addr = params["address"]
            value = params["value"]
            self._mmio[addr] = value
            # Simulate completion: set status bit after MMIO write
            self._status_register |= 0x2

        elif action.action_type == "mmio_read":
            addr = params["address"]
            # Return stored value or 0
            self._mmio.setdefault(addr, 0)

        elif action.action_type == "sram_preload":
            offset = params["offset"]
            data = bytes.fromhex(params["data_hex"])
            self._sram[offset] = data

        elif action.action_type == "dram_preload":
            offset = params["offset"]
            data = bytes.fromhex(params["data_hex"])
            self._dram[offset] = data

        elif action.action_type == "pcie_write":
            addr = params["address"]
            data = bytes.fromhex(params["data_hex"])
            self._pcie_space[addr] = data

        elif action.action_type == "pcie_read":
            addr = params["address"]
            # Record as observation store
            data = self._pcie_space.get(addr, b"")
            self._observation_store[f"pcie_{addr:08X}"] = data

        elif action.action_type == "doorbell":
            self._doorbell_tail = params["host_tail"]
            # Simulate IRQ after doorbell
            self._irq_pending[0] = True

        elif action.action_type == "wait_irq":
            source = params["source"]
            if not self._irq_pending.get(source, False):
                raise DUTTimeoutError(
                    f"IRQ source {source} not pending"
                )
            self._irq_pending[source] = False

        elif action.action_type == "poll_status":
            # Simulate: status is always ready in fake DUT
            pass

        elif action.action_type == "sram_readback":
            offset = params["offset"]
            size = params["size"]
            data = self._sram.get(offset, b"\x00" * size)
            self._observation_store[f"sram_{offset:08X}"] = data[:size]

        elif action.action_type == "dram_readback":
            offset = params["offset"]
            size = params["size"]
            data = self._dram.get(offset, b"\x00" * size)
            self._observation_store[f"dram_{offset:08X}"] = data[:size]

        elif action.action_type == "mmio_readback":
            addr = params["address"]
            expected = params.get("expected")
            actual = self._mmio.get(addr, 0)
            self._observation_store[f"mmio_readback_{addr:08X}"] = str(actual).encode()

        elif action.action_type == "reset":
            await self.reset()

        else:
            # Diagnostic actions with unrecognized types are no-ops
            # when diagnostics are accepted. Otherwise, raise.
            if (action.classification == OperationClass.diagnostic
                    and self.accept_diagnostics):
                return
            raise ValueError(
                f"Unsupported action_type: {action.action_type}"
            )

    async def observe(self, observation_spec: Observation) -> Observation:
        """Observe fake DUT state according to the specification.

        Reads from the fake DUT's internal state (memory, registers, etc.)
        and returns an Observation with actual data.
        """
        self._check_connected()

        spec_type = observation_spec.observation_type
        obs_id = observation_spec.observation_id

        if spec_type.value == "mmio_value":
            addr = observation_spec.address or 0
            value = self._mmio.get(addr, 0)
            return Observation(
                observation_id=obs_id,
                observation_type=spec_type,
                address=addr,
                data={"value": value},
                tolerance=observation_spec.tolerance,
            )

        elif spec_type.value == "sram_data":
            offset = observation_spec.address or 0
            size = observation_spec.size or 0
            raw = self._sram.get(offset, b"\x00" * size)
            return Observation(
                observation_id=obs_id,
                observation_type=spec_type,
                address=offset,
                size=size,
                data={"raw_hex": raw[:size].hex(), "dtype": observation_spec.data.get("dtype", "int32")},
                tolerance=observation_spec.tolerance,
            )

        elif spec_type.value == "dram_data":
            offset = observation_spec.address or 0
            size = observation_spec.size or 0
            raw = self._dram.get(offset, b"\x00" * size)
            return Observation(
                observation_id=obs_id,
                observation_type=spec_type,
                address=offset,
                size=size,
                data={"raw_hex": raw[:size].hex(), "dtype": observation_spec.data.get("dtype", "int32")},
                tolerance=observation_spec.tolerance,
            )

        elif spec_type.value == "completion_status":
            return Observation(
                observation_id=obs_id,
                observation_type=spec_type,
                data={"status": self._status_register & 0x2},
                tolerance=observation_spec.tolerance,
            )

        else:
            # Generic observation — return spec without data
            return Observation(
                observation_id=obs_id,
                observation_type=spec_type,
                metadata={"note": "fake DUT: generic observation not implemented"},
            )

    @property
    def adapter_name(self) -> str:
        return "FakeDUT"

    @property
    def firmware_mode(self) -> str:
        return "fake"

    def _check_connected(self) -> None:
        if not self._connected:
            raise DUTConnectionError("FakeDUTAdapter is not connected")
