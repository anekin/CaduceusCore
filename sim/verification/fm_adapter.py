"""Func Model DUT adapter — implements DUTAdapter contract for FuncModel.

Todo 9: Wraps the FuncModel (Python firmware or real-Spike) behind the
shared DUT adapter contract from Todo 4. Supports both Python firmware and
explicit real-Spike modes, with deterministic failure when Spike artifacts
are missing.

Every Action is classified (frontdoor, allowed_init_backdoor,
allowed_obs_backdoor, diagnostic) and routed to the appropriate FuncModel
API. Frontdoor actions go through PCIe TLP / host_write_command / MMIO bridge.
Backdoor actions directly access model.sram / model.dram.

Todo 13: Adds optional fault-injection hooks (disabled by default) for
deterministic testbench fault testing. Fault hooks are NOT reachable via
the public C Host Runtime API — they are Python-side, adapter-level only.

Key design rules:
    - For software-E2E scenarios, NO operation-performing backdoor actions.
    - Init backdoors (sram_preload, dram_preload) are explicitly tagged.
    - Observation backdoors (sram_readback, dram_readback, mmio_readback)
      are also tagged.
    - Evidence records distinguish Python-firmware from real-Spike evidence
      via firmware_mode, ABI version, and actual artifact path.
    - Fault hooks are disabled by default; enable via enable_fault().
    - Fault injections record `injection_applied=True` in evidence metadata.
"""

import logging
from typing import Any, Dict, List, Optional

import numpy as np

from verification.dut_adapter import (
    DUTAdapter,
    DUTError,
    DUTTimeoutError,
    DUTConnectionError,
)
from verification.scenario import Action
from verification.observation import Observation, ObservationType
from verification.operation_classifier import OperationClass
from verification.fault_injector import (
    FaultClass,
    FaultInjector,
    FaultInjectionRecord,
)

logger = logging.getLogger("fm_adapter")

# ── ABI version from spec/npu_abi.json (major.minor as single int) ────
ABI_VERSION = 1


class FuncModelAdapter(DUTAdapter):
    """Func Model DUT adapter wrapping FuncModel.

    Supports two firmware modes:
        - "python": Uses the Python firmware emulator (miniv.NPUFirmware).
          All actions execute synchronously in-process.
        - "spike": Uses the real RISC-V firmware compiled for Spike.
          FuncModel(use_spike=True) raises RuntimeError if Spike artifacts
          are missing.

    Lifecycle:
        adapter = FuncModelAdapter(firmware_mode="python")
        await adapter.connect()
        await adapter.reset()
        for action in scenario.actions:
            await adapter.execute_action(action)
        obs = await adapter.observe(observation_spec)
        await adapter.disconnect()

    Evidence metadata records:
        - firmware_mode: "python" or "spike"
        - abi_version: integer ABI major version
        - spike_available: bool (for real-Spike mode tracking)
        - action_counts: Dict[str, int] by classification
        - backdoor_classification: registered backdoor types
    """

    def __init__(
        self,
        firmware_mode: str = "python",
        dram_mb: int = 64,
        sram_kb: int = 512,
    ):
        """Initialize the Func Model adapter.

        Args:
            firmware_mode: "python" for emulated firmware, "spike" for
                           real Spike-based firmware.
            dram_mb: DRAM size in MB (default 64).
            sram_kb: SRAM size in KB (default 512).

        Raises:
            ValueError: If firmware_mode is not "python" or "spike".
        """
        self._firmware_mode = firmware_mode
        self._dram_mb = dram_mb
        self._sram_kb = sram_kb
        self._model: Any = None
        self._connected = False

        # Track action classifications for evidence
        self._action_counts: Dict[str, int] = {
            "frontdoor": 0,
            "allowed_init_backdoor": 0,
            "allowed_obs_backdoor": 0,
            "diagnostic": 0,
        }

        # Registered backdoor types
        self._backdoor_classification: Dict[str, str] = {}

        # Fault injection hooks (Todo 13: disabled by default)
        self.fault_injector = FaultInjector()

    # ── Connection lifecycle ──────────────────────────────────────────

    async def connect(self) -> None:
        """Create the FuncModel instance.

        For 'spike' mode, FuncModel(use_spike=True) raises RuntimeError
        if Spike artifacts (spike binary, plugin.so, firmware ELF) are
        missing — this is the deterministic failure behaviour required
        by the contract.

        Raises:
            DUTConnectionError: If the model cannot be created.
        """
        if self._firmware_mode not in ("python", "spike"):
            raise DUTConnectionError(
                f"Unknown firmware_mode: {self._firmware_mode!r}. "
                f"Must be 'python' or 'spike'."
            )

        try:
            use_spike = (self._firmware_mode == "spike")
            from func_model import FuncModel
            self._model = FuncModel(
                dram_mb=self._dram_mb,
                sram_kb=self._sram_kb,
                use_spike=use_spike,
            )
        except RuntimeError as e:
            # Re-raise as DUTConnectionError for the adapter contract.
            # FuncModel raises RuntimeError when use_spike=True but
            # Spike artifacts are missing.
            raise DUTConnectionError(
                f"Failed to create FuncModel in {self._firmware_mode!r} mode: {e}"
            ) from e
        except Exception as e:
            raise DUTConnectionError(
                f"Failed to create FuncModel: {e}"
            ) from e

        self._connected = True
        firmware_type = type(self._model.firmware).__name__
        logger.info(
            "FuncModelAdapter connected (mode=%s, firmware=%s, dram=%dMB, sram=%dKB)",
            self._firmware_mode, firmware_type,
            self._dram_mb, self._sram_kb,
        )

    async def disconnect(self) -> None:
        """Release the FuncModel instance."""
        self._connected = False
        self._model = None
        logger.info("FuncModelAdapter disconnected")

    async def reset(self) -> None:
        """Reset the FuncModel.

        Creates a fresh FuncModel with clean SRAM/DRAM state.
        This keeps the same firmware_mode and configuration.
        """
        self._check_connected()
        use_spike = (self._firmware_mode == "spike")
        from func_model import FuncModel
        self._model = FuncModel(
            dram_mb=self._dram_mb,
            sram_kb=self._sram_kb,
            use_spike=use_spike,
        )
        self._action_counts = {
            "frontdoor": 0,
            "allowed_init_backdoor": 0,
            "allowed_obs_backdoor": 0,
            "diagnostic": 0,
        }
        self._backdoor_classification.clear()
        logger.info("FuncModelAdapter: DUT reset complete")

    # ── Action execution ──────────────────────────────────────────────

    async def execute_action(self, action: Action) -> None:
        """Execute a single Action on the FuncModel.

        Routes each action_type to the appropriate FuncModel API.
        Frontdoor actions use PCIe/BAR/MMIO/doorbell.
        Backdoor actions directly access model.sram / model.dram.

        Fault injection (Todo 13): reset_during_command triggers reset
        before executing the action.

        Args:
            action: The action to execute.

        Raises:
            DUTError: If the action fails.
            DUTTimeoutError: If a wait times out.
            ValueError: If the action type is not supported.
        """
        self._check_connected()

        atype = action.action_type
        aclass = action.classification
        params = action.parameters

        # Reject diagnostic actions (unless explicitly accepted)
        if aclass == OperationClass.diagnostic:
            raise ValueError(
                f"Action '{action.action_id or atype}' is classified "
                f"as diagnostic — rejected by FuncModelAdapter"
            )

        # Track classification for evidence
        cls_key = aclass.value if hasattr(aclass, "value") else str(aclass)
        self._action_counts[cls_key] = self._action_counts.get(cls_key, 0) + 1

        # Fault injection: reset_during_command
        if self._check_inject_fault(FaultClass.reset_during_command, action.action_id):
            await self.reset()
            return  # Command is aborted by reset

        if atype == "mmio_write":
            await self._handle_mmio_write(params)
        elif atype == "mmio_read":
            await self._handle_mmio_read(params)
        elif atype == "sram_preload":
            await self._handle_sram_preload(params)
        elif atype == "dram_preload":
            await self._handle_dram_preload(params)
        elif atype == "pcie_write":
            await self._handle_pcie_write(params)
        elif atype == "pcie_read":
            await self._handle_pcie_read(params)
        elif atype == "doorbell":
            await self._handle_doorbell(params)
        elif atype == "wait_irq":
            await self._handle_wait_irq(params)
        elif atype == "poll_status":
            await self._handle_poll_status(params)
        elif atype == "sram_readback":
            await self._handle_sram_readback(params)
        elif atype == "dram_readback":
            await self._handle_dram_readback(params)
        elif atype == "mmio_readback":
            await self._handle_mmio_readback(params)
        elif atype == "reset":
            await self.reset()
        else:
            raise ValueError(
                f"Unsupported action_type: {atype!r}"
            )

    # ── Fault injection control (Todo 13) ───────────────────────────

    def enable_fault(self, fault_class: FaultClass, **params: Any) -> None:
        """Enable a fault for the next applicable action.

        These hooks are adapter-level only and are NOT reachable via
        the public C Host Runtime API.

        Args:
            fault_class: The fault class to enable.
            **params: Fault-specific parameters.
        """
        self.fault_injector.enable_fault(fault_class, **params)

    def disable_fault(self, fault_class: FaultClass) -> None:
        """Disable a previously enabled fault."""
        self.fault_injector.disable_fault(fault_class)

    def disable_all_faults(self) -> None:
        """Disable all active faults."""
        self.fault_injector.disable_all()

    def _check_inject_fault(
        self, fault_class: FaultClass, action_id: Optional[str] = None
    ) -> bool:
        """Check whether a fault should be injected for the current action.

        If the fault is active, records the injection and disables the fault
        (one-shot semantics). Returns True if a fault was injected.
        """
        if self.fault_injector.is_active(fault_class):
            self.fault_injector.record_injection(fault_class)
            logger.debug(
                "Fault injected: %s (action=%s)",
                fault_class.value, action_id or "?",
            )
            return True
        return False

    async def _handle_mmio_write(self, params: dict) -> None:
        """[FRONTDOOR] MMIO write through the bridge."""
        addr = params["address"]
        value = params["value"]
        self._model.bridge.handle("write", addr, value)

    async def _handle_mmio_read(self, params: dict) -> None:
        """[FRONTDOOR] MMIO read through the bridge."""
        addr = params["address"]
        self._model.bridge.handle("read", addr, 0)

    async def _handle_pcie_write(self, params: dict) -> None:
        """[FRONTDOOR] PCIe TLP write to DRAM/SRAM."""
        addr = params["address"]
        data_hex = params["data_hex"]
        data = bytes.fromhex(data_hex)
        self._model.pcie.tlp_write(addr, data)

    async def _handle_pcie_read(self, params: dict) -> None:
        """[FRONTDOOR] PCIe TLP read — stores result for observation."""
        addr = params["address"]
        # Use default size if not specified
        size = params.get("size", 4)
        data = self._model.pcie.tlp_read(addr, size)
        self._model._last_pcie_read = (addr, data)

    async def _handle_doorbell(self, params: dict) -> None:
        """[FRONTDOOR] Host doorbell: write command to ring buffer.

        Uses host_write_command to queue a command. If opcode/desc_addr
        are in params, uses those. Otherwise uses the host_tail only
        (backward compat with FakeDUT adapter scenarios).

        Fault injection (Todo 13):
            - wrong_descriptor: corrupts opcode/desc_addr
            - unsupported_opcode: replaces opcode with 0xFF
            - ring_overflow: writes tail beyond ring capacity
        """
        host_tail = params.get("host_tail")
        opcode = params.get("opcode", 0)
        desc_addr = params.get("desc_addr", 0)

        # Fault injection: wrong_descriptor
        wd_params = dict(self.fault_injector.get_params(FaultClass.wrong_descriptor) or {})
        if self._check_inject_fault(FaultClass.wrong_descriptor, "doorbell"):
            opcode, desc_addr = self.fault_injector.inject_wrong_descriptor(
                int(opcode), int(desc_addr), wd_params,
            )

        # Fault injection: unsupported_opcode
        if self._check_inject_fault(FaultClass.unsupported_opcode, "doorbell"):
            opcode = self.fault_injector.inject_unsupported_opcode(int(opcode))

        if opcode or desc_addr:
            # Full doorbell: host_write_command queues command
            self._model.host_write_command(int(opcode), int(desc_addr))
        elif host_tail is not None:
            # Fault injection: ring_overflow
            if self._check_inject_fault(FaultClass.ring_overflow, "doorbell"):
                host_tail = self.fault_injector.inject_ring_overflow(int(host_tail))

            # Set host_tail directly (for scenarios that don't use
            # descriptors, e.g. simple backdoor doorbell)
            self._model.firmware.doorbell["host_tail"] = int(host_tail)
            from regmap import DOORBELL, INTC
            self._model.bridge.handle(
                "write",
                DOORBELL.BASE + DOORBELL.HOST_TAIL,
                int(host_tail),
            )
            self._model.bridge._set_irq(8)  # HOST doorbell interrupt

    async def _handle_wait_irq(self, params: dict) -> None:
        """[FRONTDOOR] Wait for interrupt, then dispatch firmware.

        Checks INTC.PENDING for the specified source, runs the firmware
        dispatch loop, and verifies the interrupt was handled.

        Fault injection (Todo 13):
            - dropped_interrupt: clears IRQ pending before dispatch (IRQ lost)
            - duplicated_interrupt: sets pending bit again after dispatch
            - stalled_head: prevents head from advancing after dispatch
        """
        source = params["source"]
        from regmap import INTC

        # Fault injection: dropped_interrupt
        if self._check_inject_fault(FaultClass.dropped_interrupt, "wait_irq"):
            # Clear the pending bit silently (IRQ lost)
            pending_before = self._model.bridge.handle("read", INTC.BASE + INTC.PENDING, 0)
            source_bit = 1 << int(source)
            self._model.bridge.handle("write", INTC.BASE + INTC.PENDING, pending_before & ~source_bit)
            raise DUTTimeoutError(
                f"IRQ source {source} dropped (fault injection)"
            )

        # Check IRQ is pending
        pending = self._model.bridge.handle("read", INTC.BASE + INTC.PENDING, 0)
        source_bit = 1 << int(source)
        if not (pending & source_bit):
            raise DUTTimeoutError(
                f"IRQ source {source} not pending (PENDING=0x{pending:08X})"
            )

        # Fault injection: stalled_head — prevent head advancement
        stalled = self._check_inject_fault(FaultClass.stalled_head, "wait_irq")

        # Run firmware dispatch loop
        self._model.run()

        # Fault injection: duplicated_interrupt — re-trigger IRQ
        if self._check_inject_fault(FaultClass.duplicated_interrupt, "wait_irq"):
            self._model.bridge._set_irq(8)  # Re-trigger HOST doorbell interrupt
            return  # Don't verify IRQ cleared — we expect it to still be set

        # Verify IRQ was acknowledged (cleared by firmware)
        pending_after = self._model.bridge.handle(
            "read", INTC.BASE + INTC.PENDING, 0
        )
        if pending_after & source_bit and not stalled:
            raise DUTTimeoutError(
                f"IRQ source {source} still pending after dispatch "
                f"(PENDING=0x{pending_after:08X})"
            )

    async def _handle_poll_status(self, params: dict) -> None:
        """[FRONTDOOR] Poll a status register until mask matches.

        Fault injection (Todo 13): timeout — always times out.
        """
        if self._check_inject_fault(FaultClass.timeout, "poll_status"):
            raise DUTTimeoutError(
                f"Poll status timeout (fault injection: timeout)"
            )

        addr = params["address"]
        mask = params.get("mask", 0x2)
        timeout = params.get("timeout_cycles", 100_000)

        for _ in range(timeout):
            status = self._model.bridge.handle("read", addr, 0)
            if status & mask:
                return
        raise DUTTimeoutError(
            f"Poll status timeout: addr=0x{addr:08X} mask=0x{mask:X}"
        )

    # ── Backdoor action handlers ──────────────────────────────────

    async def _handle_sram_preload(self, params: dict) -> None:
        """[INIT BACKDOOR] Direct SRAM write for test setup.

        Registered as: 'sram_preload:backdoor_write_bytes'

        Fault injection (Todo 13): data_corruption corrupts data before write.
        """
        offset = params["offset"]
        data_hex = params["data_hex"]
        data = bytes.fromhex(data_hex)

        # Fault injection: data_corruption
        fault_params = dict(self.fault_injector.get_params(FaultClass.data_corruption) or {})
        if self._check_inject_fault(FaultClass.data_corruption, "sram_preload"):
            data = self.fault_injector.inject_data_corruption(data, fault_params)

        self._model.sram[int(offset):int(offset) + len(data)] = data
        self._backdoor_classification["sram_preload"] = "backdoor_write_bytes"

    async def _handle_dram_preload(self, params: dict) -> None:
        """[INIT BACKDOOR] Direct DRAM write for test setup.

        Registered as: 'dram_preload:backdoor_write_bytes'

        Fault injection (Todo 13): data_corruption corrupts data before write.
        """
        offset = params["offset"]
        data_hex = params["data_hex"]
        data = bytes.fromhex(data_hex)

        fault_params = dict(self.fault_injector.get_params(FaultClass.data_corruption) or {})
        if self._check_inject_fault(FaultClass.data_corruption, "dram_preload"):
            data = self.fault_injector.inject_data_corruption(data, fault_params)

        self._model.dram[int(offset):int(offset) + len(data)] = data
        self._backdoor_classification["dram_preload"] = "backdoor_write_bytes"

    async def _handle_sram_readback(self, params: dict) -> None:
        """[OBS BACKDOOR] Direct SRAM read for verification.

        Stores result in _last_sram_readback for observation.
        Registered as: 'sram_readback:backdoor_read_bytes'

        Fault injection (Todo 13): data_corruption corrupts data during readback.
        """
        offset = params["offset"]
        size = params["size"]
        data = bytes(self._model.sram[int(offset):int(offset) + int(size)])

        fault_params = dict(self.fault_injector.get_params(FaultClass.data_corruption) or {})
        if self._check_inject_fault(FaultClass.data_corruption, "sram_readback"):
            data = self.fault_injector.inject_data_corruption(data, fault_params)

        self._model._last_sram_readback = (int(offset), int(size), data)
        self._backdoor_classification["sram_readback"] = "backdoor_read_bytes"

    async def _handle_dram_readback(self, params: dict) -> None:
        """[OBS BACKDOOR] Direct DRAM read for verification.

        Stores result in _last_dram_readback for observation.
        Registered as: 'dram_readback:backdoor_read_bytes'

        Fault injection (Todo 13): data_corruption corrupts data during readback.
        """
        offset = params["offset"]
        size = params["size"]
        data = bytes(self._model.dram[int(offset):int(offset) + int(size)])

        fault_params = dict(self.fault_injector.get_params(FaultClass.data_corruption) or {})
        if self._check_inject_fault(FaultClass.data_corruption, "dram_readback"):
            data = self.fault_injector.inject_data_corruption(data, fault_params)

        self._model._last_dram_readback = (int(offset), int(size), data)
        self._backdoor_classification["dram_readback"] = "backdoor_read_bytes"

    async def _handle_mmio_readback(self, params: dict) -> None:
        """[OBS BACKDOOR] MMIO register readback for verification.

        Stores result in _last_mmio_readback for observation.
        Registered as: 'mmio_readback:backdoor_read_register'
        """
        addr = params["address"]
        value = self._model.bridge.handle("read", addr, 0)
        self._model._last_mmio_readback = (addr, value)
        self._backdoor_classification["mmio_readback"] = "backdoor_read_register"

    # ── Observation ───────────────────────────────────────────────────

    async def observe(self, observation_spec: Observation) -> Observation:
        """Observe the FuncModel state.

        Reads from model.sram, model.dram, bridge MMIO, or stored
        readback results from the most recent action.

        Args:
            observation_spec: What to observe.

        Returns:
            An Observation with actual data populated.
        """
        self._check_connected()

        spec_type = observation_spec.observation_type
        obs_id = observation_spec.observation_id

        if spec_type == ObservationType.mmio_value:
            addr = observation_spec.address or 0
            value = self._model.bridge.handle("read", addr, 0)
            return Observation(
                observation_id=obs_id,
                observation_type=spec_type,
                address=addr,
                data={"value": value},
                tolerance=observation_spec.tolerance,
            )

        elif spec_type == ObservationType.sram_data:
            offset = observation_spec.address or 0
            size = observation_spec.size or 0
            # Prefer stored readback from most recent action
            last = getattr(self._model, "_last_sram_readback", None)
            if last and last[0] == offset and last[1] == size:
                raw = last[2]
            else:
                raw = bytes(self._model.sram[int(offset):int(offset) + int(size)])
            return Observation(
                observation_id=obs_id,
                observation_type=spec_type,
                address=offset,
                size=size,
                data={
                    "raw_hex": raw.hex(),
                    "dtype": observation_spec.data.get("dtype", "int32"),
                },
                tolerance=observation_spec.tolerance,
            )

        elif spec_type == ObservationType.dram_data:
            offset = observation_spec.address or 0
            size = observation_spec.size or 0
            last = getattr(self._model, "_last_dram_readback", None)
            if last and last[0] == offset and last[1] == size:
                raw = last[2]
            else:
                raw = bytes(self._model.dram[int(offset):int(offset) + int(size)])
            return Observation(
                observation_id=obs_id,
                observation_type=spec_type,
                address=offset,
                size=size,
                data={
                    "raw_hex": raw.hex(),
                    "dtype": observation_spec.data.get("dtype", "int32"),
                },
                tolerance=observation_spec.tolerance,
            )

        elif spec_type == ObservationType.pcie_readback:
            last = getattr(self._model, "_last_pcie_read", None)
            if last:
                addr, data = last
                return Observation(
                    observation_id=obs_id,
                    observation_type=spec_type,
                    address=addr,
                    size=len(data),
                    data={"raw_hex": data.hex()},
                    tolerance=observation_spec.tolerance,
                )
            return Observation(
                observation_id=obs_id,
                observation_type=spec_type,
                data={},
                tolerance=observation_spec.tolerance,
            )

        elif spec_type == ObservationType.completion_status:
            from regmap import MXU
            status = self._model.bridge.handle(
                "read", MXU.BASE + MXU.STATUS, 0
            )
            actual_status = status & 0x2

            # Fault injection: wrong_completion
            wc_params = dict(self.fault_injector.get_params(FaultClass.wrong_completion) or {})
            if self._check_inject_fault(FaultClass.wrong_completion, obs_id):
                actual_status = self.fault_injector.inject_wrong_completion(actual_status, wc_params)

            # Fault injection: engine_error
            if self._check_inject_fault(FaultClass.engine_error, obs_id):
                actual_status = 0xDEAD  # Engine error code

            return Observation(
                observation_id=obs_id,
                observation_type=spec_type,
                data={"status": actual_status},
                tolerance=observation_spec.tolerance,
            )

        elif spec_type == ObservationType.interrupt_status:
            from regmap import INTC
            pending = self._model.bridge.handle(
                "read", INTC.BASE + INTC.PENDING, 0
            )
            return Observation(
                observation_id=obs_id,
                observation_type=spec_type,
                data={"pending": pending},
                tolerance=observation_spec.tolerance,
            )

        else:
            # Generic observation — return spec without data
            return Observation(
                observation_id=obs_id,
                observation_type=spec_type,
                metadata={"note": "FuncModelAdapter: generic observation not implemented"},
            )

    # ── Properties ────────────────────────────────────────────────────

    @property
    def adapter_name(self) -> str:
        """Human-readable adapter name for evidence records."""
        return "FuncModel"

    @property
    def firmware_mode(self) -> str:
        """Current firmware mode ('python' or 'spike')."""
        return self._firmware_mode

    # ── Evidence metadata ─────────────────────────────────────────────

    def evidence_metadata(self) -> dict:
        """Return metadata for evidence records.

        Includes firmware mode, ABI version, path info, backdoor
        classification, action counts by classification, and fault
        injection records (Todo 13).

        Returns:
            dict with: firmware_mode, abi_version, spike_available,
                       backdoor_classification, action_counts,
                       injection_applied, fault_injection_records
        """
        metadata: Dict[str, Any] = {
            "firmware_mode": self._firmware_mode,
            "abi_version": ABI_VERSION,
            "backdoor_classification": dict(self._backdoor_classification),
            "action_counts": dict(self._action_counts),
            # Todo 13: Fault injection evidence
            "injection_applied": self.fault_injector.any_injection_applied,
            "fault_injection_records": [
                {
                    "fault_class": r.fault_class,
                    "injection_applied": r.injection_applied,
                    "injection_params": r.injection_params,
                    "detected_by_scoreboard": r.detected_by_scoreboard,
                    "detected_classification": r.detected_classification,
                }
                for r in self.fault_injector.flush_records()
            ],
        }

        if self._firmware_mode == "spike":
            from spike_firmware import _is_spike_available
            metadata["spike_available"] = _is_spike_available()
            if _is_spike_available():
                import os as _os
                metadata["spike_binary_path"] = str(
                    _os.path.join(
                        _os.path.dirname(__file__), "..",
                        "..", "spike_src", "build", "spike"
                    )
                )
                metadata["firmware_elf_path"] = str(
                    _os.path.join(
                        _os.path.dirname(__file__), "..",
                        "..", "firmware", "build", "npu_firmware_spike.elf"
                    )
                )
        else:
            metadata["spike_available"] = False

        if self._model is not None:
            metadata["firmware_class"] = type(self._model.firmware).__name__
            metadata["dram_mb"] = self._dram_mb
            metadata["sram_kb"] = self._sram_kb

        return metadata

    # ── Internal helpers ──────────────────────────────────────────────

    def _check_connected(self) -> None:
        if not self._connected or self._model is None:
            raise DUTConnectionError("FuncModelAdapter is not connected")

    def _get_sram_offset(self, addr: int) -> int:
        """Translate address to SRAM byte offset."""
        from regmap import Addr
        return int(addr) - Addr.SRAM_BASE

    def _get_dram_offset(self, addr: int) -> int:
        """Translate address to DRAM byte offset."""
        from regmap import Addr
        return int(addr) - Addr.DRAM_BASE
