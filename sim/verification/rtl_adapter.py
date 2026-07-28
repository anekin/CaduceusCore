"""RTL DUT adapter — implements DUTAdapter contract for CocotbBridge + VPI.

Todo 10 (FEASIBILITY-ONLY): Wraps cocotb/VPI/APB/TLP/backdoor operations
behind the shared DUT adapter contract from Todo 4.

Every Action is classified (frontdoor, allowed_init_backdoor, allowed_obs_backdoor,
diagnostic) and routed to the correct CocotbBridge method. The adapter preserves
existing FM-SOC testcase loading and mixed-mode controls while providing a
transport-independent interface for the verification layer.

Workarounds and backdoors are tagged in-code with their classification:
    [INIT]   — Initialization backdoor (SRAM/DRAM preload, address translation)
    [OBS]    — Observation backdoor (readback for verification)
    [DIAG]   — Diagnostic-only (signal probes, timing monitors)
    [WAR]    — RTL wrapper workaround (CMD deferral, register reordering)
"""

import logging
from typing import Optional, Dict, Any, List

from sim.verification.dut_adapter import (
    DUTAdapter,
    DUTError,
    DUTTimeoutError,
    DUTConnectionError,
)
from sim.verification.scenario import Action
from sim.verification.observation import Observation, ObservationType
from sim.verification.operation_classifier import OperationClass

logger = logging.getLogger("rtl_adapter")

# ── Address constants (mirror regmap.py for adapter use) ──────────────

MXU_BASE      = 0x4000_0000
SFU_BASE      = 0x4000_1000
VECTOR_BASE   = 0x4000_2000
DMA_BASE      = 0x4000_3000
DOORBELL_BASE = 0x4000_5000
INTC_BASE     = 0x4000_6000
SRAM_BASE     = 0x2000_0000
DRAM_BASE     = 0x8000_0000

# Engine STATUS register offset (DONE=bit1)
STATUS_OFFSET  = 0x08

# ── RTL wrapper module keys ──────────────────────────────────────────

RTL_MODULES = frozenset({"pcie", "dma", "mxu", "sfu", "vector"})


class RTLAdapter(DUTAdapter):
    """RTL DUT adapter wrapping CocotbBridge.

    Implements the transport-independent DUTAdapter contract by routing
    Action execution and Observation retrieval through CocotbBridge VPI
    operations.

    Lifecycle:
        adapter = RTLAdapter(bridge)
        await adapter.connect()
        await adapter.reset()
        for action in scenario.actions:
            await adapter.execute_action(action)
        obs = await adapter.observe(spec)
        await adapter.disconnect()

    DUT mode is tracked internally and reported in evidence metadata.
    """

    def __init__(self, cocotb_bridge):
        """Initialize the RTL adapter.

        Args:
            cocotb_bridge: An initialized CocotbBridge(dut) instance.
                           May be None for off-cocotb testing.
        """
        self._bridge = cocotb_bridge
        self._dut = getattr(cocotb_bridge, "dut", None)
        self._connected = False

        # Mixed-mode: which modules use RTL vs Func Model
        self._rtl_modules: set = set()
        self._defines: List[str] = []

        # DUT mode metadata for evidence records
        self._dut_mode: str = "full_rtl"  # "full_rtl", "mixed", "spike_rtl"
        self._enabled_modules: set = set()

        # Track action classifications for evidence
        self._action_counts: Dict[str, int] = {
            "frontdoor": 0,
            "allowed_init_backdoor": 0,
            "allowed_obs_backdoor": 0,
            "diagnostic": 0,
        }

    # ── Connection lifecycle ──────────────────────────────────────────

    async def connect(self) -> None:
        """Establish connection to the RTL DUT.

        In cocotb mode, the DUT is already connected via VPI. This method
        validates the bridge is available and starts the clock.
        """
        if self._bridge is None:
            raise DUTConnectionError(
                "RTLAdapter requires a CocotbBridge instance"
            )
        self._connected = True
        if hasattr(self._bridge, "start_clock"):
            await self._bridge.start_clock()
        logger.info("RTLAdapter connected (cocotb/VPI)")

    async def disconnect(self) -> None:
        """Tear down connection. No-op for cocotb/VPI."""
        self._connected = False
        logger.info("RTLAdapter disconnected")

    async def reset(self) -> None:
        """Reset the DUT via CocotbBridge.

        Applies N cycles of reset, then releases. After reset all
        engine state is cleared and registers return to power-on values.
        """
        self._check_connected()
        if hasattr(self._bridge, "reset"):
            await self._bridge.reset(5)
        logger.info("RTLAdapter: DUT reset complete")

    # ── Action execution ──────────────────────────────────────────────

    async def execute_action(self, action: Action) -> None:
        """Execute a single Action on the RTL DUT.

        Dispatches to the appropriate CocotbBridge method based on
        action_type. Diagnostic actions are recorded but may be no-ops
        in production builds.

        Args:
            action: The action to execute.

        Raises:
            DUTError: If the action fails.
            DUTTimeoutError: If a poll or wait times out.
            ValueError: If the action type is not supported.
        """
        self._check_connected()

        atype = action.action_type
        aclass = action.classification
        params = action.parameters

        # Track classification for evidence
        if aclass:
            key = aclass.value if hasattr(aclass, "value") else str(aclass)
            self._action_counts[key] = self._action_counts.get(key, 0) + 1

        if atype == "mmio_write":
            # [FRONTDOOR] APB write through ibex_wrapper
            addr = params["address"]
            value = params["value"]
            await self._bridge._apb_write(addr, value)

        elif atype == "mmio_read":
            # [FRONTDOOR] APB read through ibex_wrapper
            addr = params["address"]
            await self._bridge._apb_read(addr)

        elif atype == "sram_preload":
            # [INIT] Backdoor SRAM write for test setup.
            # The real software would DMA data into SRAM via PCIe;
            # we use VPI backdoor for speed and avoid PCIe size limits.
            offset = params["offset"]
            data_hex = params["data_hex"]
            data = bytes.fromhex(data_hex)
            addr = SRAM_BASE + offset
            await self._bridge._sram_backdoor_write(addr, data)

        elif atype == "dram_preload":
            # [INIT] Backdoor DRAM write for test setup.
            # Same rationale as sram_preload: avoids PCIe TLP size limits
            # and is much faster for large golden vectors.
            offset = params["offset"]
            data_hex = params["data_hex"]
            data = bytes.fromhex(data_hex)
            addr = DRAM_BASE + offset
            await self._bridge._dram_backdoor_write(addr, data)

        elif atype == "pcie_write":
            # [FRONTDOOR] PCIe Memory Write TLP via direct signal driving.
            # Drives pcie_rx_req_tlp_* signals on the DUT. This is the same
            # path used by RTLSoCRunner._pcie_tlp_write.
            addr = params["address"]
            data_hex = params["data_hex"]
            data = bytes.fromhex(data_hex)
            if self._dut is not None:
                await self._pcie_tlp_write_dut(addr, data)
            else:
                logger.debug("RTLAdapter: no DUT — PCIe write deferred")

        elif atype == "pcie_read":
            # [FRONTDOOR] PCIe Memory Read TLP with completion.
            addr = params["address"]
            # Read size is recorded as metadata; actual read happens in observe()
            pass

        elif atype == "doorbell":
            # [INIT] Backdoor doorbell register write.
            # In Ibex-RTL mode the APB bus is owned by the CPU firmware,
            # so driving apb_* signals from Python would corrupt firmware
            # transactions. The testbench reaches into the doorbell
            # register file directly via hierarchical VPI access.
            # WORKAROUND: _doorbell_backdoor_write bypasses APB entirely.
            host_tail = params["host_tail"]
            await self._bridge._doorbell_backdoor_write(
                DOORBELL_BASE + 0x0, host_tail
            )

        elif atype == "wait_irq":
            # [FRONTDOOR] Poll INTC PENDING register.
            source = params.get("source", 0)
            mask = 1 << source if isinstance(source, int) else 0
            timeout = params.get("timeout_cycles", 1000)
            await self._bridge.poll_intc_pending(mask, timeout=timeout)

        elif atype == "poll_status":
            # [FRONTDOOR] Poll engine STATUS register.
            addr = params["address"]
            mask = params.get("mask", 0x2)
            timeout = params.get("timeout_cycles", 100000)

            status = await self._bridge._apb_read(addr)
            if status & mask:
                return
            try:
                await self._bridge._poll_done(addr, timeout=timeout)
            except Exception as e:
                raise DUTTimeoutError(
                    f"Poll status timeout: 0x{addr:08X} mask=0x{mask:X} "
                    f"after {timeout} cycles"
                ) from e

        elif atype == "reset":
            await self.reset()

        elif atype in ("sram_readback", "dram_readback", "mmio_readback"):
            # [OBS] These are observation-only actions; the actual read
            # happens in observe().  No-op at execution time.
            pass

        elif aclass == OperationClass.diagnostic:
            # [DIAG] Diagnostic action — logged but no hardware effect.
            logger.debug(
                f"RTLAdapter: diagnostic action '{atype}' — no-op"
            )

        else:
            raise ValueError(
                f"RTLAdapter: unsupported action_type '{atype}'"
            )

    # ── Observation ───────────────────────────────────────────────────

    async def observe(self, observation_spec: Observation) -> Observation:
        """Read actual DUT state and return an Observation with data.

        Maps ObservationType to the appropriate CocotbBridge read method.
        All reads go through VPI backdoor for speed; frontdoor reads
        (via APB or PCIe) are possible but not implemented in this
        feasibility phase.

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
            value = await self._bridge._apb_read(addr)
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
            raw = await self._bridge._sram_backdoor_read(
                SRAM_BASE + offset, size
            )
            return Observation(
                observation_id=obs_id,
                observation_type=spec_type,
                address=offset,
                size=size,
                data={
                    "raw_hex": bytes(raw).hex(),
                    "dtype": observation_spec.data.get("dtype", "int32"),
                },
                tolerance=observation_spec.tolerance,
            )

        elif spec_type == ObservationType.dram_data:
            offset = observation_spec.address or 0
            size = observation_spec.size or 0
            raw = await self._bridge._dram_backdoor_read(
                DRAM_BASE + offset, size
            )
            return Observation(
                observation_id=obs_id,
                observation_type=spec_type,
                address=offset,
                size=size,
                data={
                    "raw_hex": bytes(raw).hex(),
                    "dtype": observation_spec.data.get("dtype", "int32"),
                },
                tolerance=observation_spec.tolerance,
            )

        elif spec_type == ObservationType.pcie_readback:
            addr = observation_spec.address or 0
            size = observation_spec.size or 64
            # [INIT] Use backdoor SRAM read as PCIe readback proxy.
            # In full RTL mode the PCIe TLP completion path is exercised
            # separately; for feasibility we read from SRAM directly.
            raw = await self._bridge._sram_backdoor_read(addr, size)
            return Observation(
                observation_id=obs_id,
                observation_type=spec_type,
                address=addr,
                size=size,
                data={
                    "raw_hex": bytes(raw).hex(),
                    "dtype": observation_spec.data.get("dtype", "int32"),
                },
                tolerance=observation_spec.tolerance,
            )

        elif spec_type == ObservationType.completion_status:
            # Read the last-used engine STATUS via default address
            addr = observation_spec.address or (MXU_BASE + STATUS_OFFSET)
            status = await self._bridge._apb_read(addr)
            return Observation(
                observation_id=obs_id,
                observation_type=spec_type,
                address=addr,
                data={"status": status & 0x2},
                tolerance=observation_spec.tolerance,
            )

        else:
            # Generic observation — return spec with metadata
            return Observation(
                observation_id=obs_id,
                observation_type=spec_type,
                metadata={
                    "note": "RTLAdapter: generic observation not implemented"
                },
            )

    # ── Internal helpers ──────────────────────────────────────────────

    def _check_connected(self) -> None:
        if not self._connected:
            raise DUTConnectionError(
                "RTLAdapter is not connected. Call connect() first."
            )

    # ── Mixed-mode control (preserved from RTLSoCRunner) ──────────────

    def enable_rtl(self, module: str):
        """Enable RTL for a specific module.

        Args:
            module: One of 'pcie', 'dma', 'mxu', 'sfu', 'vector'.
        """
        module = module.lower()
        if module not in RTL_MODULES:
            raise ValueError(
                f"Unknown module '{module}'. Valid: {', '.join(sorted(RTL_MODULES))}"
            )
        self._rtl_modules.add(module)
        self._enabled_modules.add(module)
        define = f"+define+USE_RTL_{module.upper()}"
        if define not in self._defines:
            self._defines.append(define)
        self._dut_mode = "mixed" if self._rtl_modules else "full_rtl"
        logger.info(f"RTLAdapter: enable_rtl({module}) — dut_mode={self._dut_mode}")

    def use_golden(self, module: str):
        """Keep module as Func Model (golden), not RTL."""
        module = module.lower()
        if module not in RTL_MODULES:
            raise ValueError(
                f"Unknown module '{module}'. Valid: {', '.join(sorted(RTL_MODULES))}"
            )
        self._rtl_modules.discard(module)
        self._enabled_modules.discard(module)
        define = f"+define+USE_RTL_{module.upper()}"
        if define in self._defines:
            self._defines.remove(define)
        self._dut_mode = "mixed" if self._rtl_modules else "full_rtl"
        logger.info(f"RTLAdapter: use_golden({module}) — dut_mode={self._dut_mode}")

    def get_defines(self) -> List[str]:
        """Return current +define+ flags for VCS."""
        return list(self._defines)

    # ── Properties for evidence ───────────────────────────────────────

    @property
    def adapter_name(self) -> str:
        return "RTLSoC"

    @property
    def firmware_mode(self) -> str:
        return "cocotb"

    @property
    def dut_mode(self) -> str:
        """Current DUT mode: 'full_rtl', 'mixed', or 'spike_rtl'."""
        return self._dut_mode

    @property
    def enabled_module_set(self) -> list:
        """Sorted list of enabled RTL module names."""
        return sorted(self._enabled_modules)

    def evidence_metadata(self) -> Dict[str, Any]:
        """Return structured metadata for evidence records.

        Includes DUT mode, enabled module set, action counts by
        classification, and ABI version.
        """
        return {
            "dut_adapter": self.adapter_name,
            "firmware_mode": self.firmware_mode,
            "abi_version": 2,  # Todo 2 ABI version
            "dut_mode": self.dut_mode,
            "enabled_modules": self.enabled_module_set,
            "action_counts": dict(self._action_counts),
            "backdoor_classification": {
                "sram_preload": {
                    "classification": "allowed_init_backdoor",
                    "rationale": "Avoids PCIe TLP size limits; faster than DMA setup",
                },
                "dram_preload": {
                    "classification": "allowed_init_backdoor",
                    "rationale": "Same as sram_preload; no functional impact on verification",
                },
                "sram_readback": {
                    "classification": "allowed_obs_backdoor",
                    "rationale": "Verification readback; software would use DMA or PCIe",
                },
                "dram_readback": {
                    "classification": "allowed_obs_backdoor",
                    "rationale": "Verification readback; software would use DMA or PCIe",
                },
                "doorbell_backdoor": {
                    "classification": "allowed_init_backdoor",
                    "rationale": "Ibex owns APB bus; direct VPI avoids firmware corruption",
                },
            },
            "workaround_registry": {
                "fm_soc_004_pcie_write": {
                    "classification": "initialization",
                    "note": "Generator uses model.crossbar.read/write directly; "
                            "PCIe write payload not recorded in input.npz. "
                            "Replay synthesizes the payload from known state.",
                },
                "fm_soc_013_ch1_preload": {
                    "classification": "initialization",
                    "note": "Generator does not record CH1 source payload in "
                            "input.npz. SRAM preload synthesized from expected.npz.",
                },
                "sfu_io_addr_translation": {
                    "classification": "initialization",
                    "note": "SFU wrapper uses absolute addresses; golden vectors "
                            "store SRAM offsets. Adapter adds SRAM_BASE at load time.",
                },
                "dma_cmd_reorder": {
                    "classification": "initialization",
                    "note": "DMA wrapper latches both channels on first START edge. "
                            "CMD.START deferred until after CH1 register programming.",
                },
                "cmd_deferral": {
                    "classification": "initialization",
                    "note": "CMD register write deferred until after SRAM/DRAM "
                            "preloads so engine wrappers read real data.",
                },
                "vector_addr_abs": {
                    "classification": "initialization",
                    "note": "Vector wrapper needs absolute SRAM addresses; "
                            "golden vectors use offsets. Added SRAM_BASE at load.",
                },
                "mxu_preload_sequencer": {
                    "classification": "initialization",
                    "note": "mxu_soc_wrapper preloads weight/activation via AXI4 "
                            "before computation. Not a backdoor — this is the "
                            "RTL's own initialization path.",
                },
            },
        }


    # ── PCIe TLP helpers (direct DUT signal driving) ────────────────

    async def _pcie_tlp_write_dut(self, addr: int, data: bytes,
                                   max_wait: int = 1000):
        import cocotb
        from cocotb.triggers import RisingEdge

        dut = self._dut
        if len(data) == 0:
            return

        length_dw = (len(data) + 3) // 4
        header = _tlp_write_header(
            fmt=0b010, tlp_type=0b00000,
            length_dw=length_dw, addr=addr,
        )

        seg_bytes = 512 // 8
        total_len = len(data)
        num_seg = max(1, (total_len + seg_bytes - 1) // seg_bytes)

        for seg_idx in range(num_seg):
            start = seg_idx * seg_bytes
            end = min(start + seg_bytes, total_len)
            chunk = data[start:end]
            if len(chunk) < seg_bytes:
                chunk = chunk + b"\x00" * (seg_bytes - len(chunk))
            data_int = int.from_bytes(chunk, "little")

            is_first = (seg_idx == 0)
            is_last = (seg_idx == num_seg - 1)

            if is_first:
                dut.pcie_rx_req_tlp_hdr.value = header
            dut.pcie_rx_req_tlp_data.value = data_int
            dut.pcie_rx_req_tlp_sop.value = 1 if is_first else 0
            dut.pcie_rx_req_tlp_eop.value = 1 if is_last else 0
            dut.pcie_rx_req_tlp_valid.value = 1

            ready = 0
            waited = 0
            while not ready and waited < max_wait:
                await RisingEdge(dut.clk)
                try:
                    ready = int(dut.pcie_rx_req_tlp_ready.value)
                except Exception:
                    ready = 0
                waited += 1

            dut.pcie_rx_req_tlp_valid.value = 0
            dut.pcie_rx_req_tlp_sop.value = 0
            dut.pcie_rx_req_tlp_eop.value = 0

            if not ready:
                raise DUTTimeoutError(
                    f"PCIe TLP ready timeout on segment {seg_idx} "
                    f"after {max_wait} cycles"
                )


# ── PCIe TLP header helper ───────────────────────────────────────────

def _tlp_write_header(fmt: int, tlp_type: int, length_dw: int,
                      addr: int, tag: int = 0) -> int:
    dw0 = ((fmt & 0x7) << 29) | ((tlp_type & 0x1F) << 24) | (length_dw & 0x3FF)
    dw1 = ((tag & 0xFF) << 8) | 0xF
    if length_dw > 1:
        dw1 |= (0xF << 4)
    dw2 = (addr & 0xFFFFFFFC)
    return (dw0 << 96) | (dw1 << 64) | (dw2 << 32)
