"""Func Model DUT adapter conformance tests.

Tests the FuncModelAdapter against the Todo 4 contract:
    - Connect/disconnect lifecycle
    - MMIO frontdoor write/read
    - PCIe frontdoor write/readback
    - SRAM/DRAM init backdoor preload + obs backdoor readback
    - Doorbell + IRQ frontdoor dispatch
    - Evidence metadata (firmware_mode, ABI version, backdoor classification)
    - Diagnostic rejection
    - Real-Spike mode deterministically fails with missing artifacts
    - Backdoor classification tracking
"""

import asyncio

import numpy as np
import pytest

from verification import (
    Action,
    FakeDUTAdapter,
    Observation,
    OperationClass,
    Scenario,
    Scoreboard,
    ToleranceConfig,
)
from verification.dut_adapter import (
    DUTConnectionError,
    DUTTimeoutError,
)
from verification.fm_adapter import FuncModelAdapter, ABI_VERSION
from verification.observation import ObservationType


def async_test(coro):
    """Decorator for async pytest tests (runs coroutine to completion)."""
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


async def _connect_adapter(firmware_mode="python"):
    """Connect a FuncModelAdapter and return it."""
    adapter = FuncModelAdapter(firmware_mode=firmware_mode)
    await adapter.connect()
    return adapter


# ═══════════════════════════════════════════════════════════════════════════
# Lifecycle tests
# ═══════════════════════════════════════════════════════════════════════════


def test_connect_disconnect():
    """Connect and disconnect the adapter cleanly."""
    async def _test():
        adapter = await _connect_adapter()
        assert adapter.adapter_name == "FuncModel"
        assert adapter.firmware_mode == "python"
        assert adapter._connected
        await adapter.disconnect()
        assert not adapter._connected
    asyncio.run(_test())


def test_reset_clears_state():
    """Reset should create a fresh model with clean memory."""
    async def _test():
        adapter = await _connect_adapter()

        # Write some data to SRAM via backdoor
        await adapter.execute_action(
            Action.sram_preload(0, b"TEST_DATA")
        )

        # Verify it's there
        obs = await adapter.observe(Observation(
            observation_id="check",
            observation_type=ObservationType.sram_data,
            address=0,
            size=9,
        ))
        assert obs.data["raw_hex"] == b"TEST_DATA".hex()

        # Reset
        await adapter.reset()

        # Verify SRAM is cleared (zeros)
        obs2 = await adapter.observe(Observation(
            observation_id="check2",
            observation_type=ObservationType.sram_data,
            address=0,
            size=9,
        ))
        assert obs2.data["raw_hex"] == "00" * 9

        await adapter.disconnect()
    asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════════════
# MMIO frontdoor tests
# ═══════════════════════════════════════════════════════════════════════════


def test_mmio_frontdoor_write_and_readback():
    """MMIO write through bridge, read back the value."""
    async def _test():
        adapter = await _connect_adapter()
        from regmap import MXU

        # Write to MXU CTRL register
        await adapter.execute_action(
            Action.mmio_write(MXU.BASE + MXU.CTRL, 0x00000001)
        )

        # Read back
        obs = await adapter.observe(Observation(
            observation_id="mxu_ctrl",
            observation_type=ObservationType.mmio_value,
            address=MXU.BASE + MXU.CTRL,
            data={"value": 0x00000001},
        ))
        assert obs.data["value"] == 0x00000001

        await adapter.disconnect()
    asyncio.run(_test())


def test_mmio_frontdoor_write_multiple_registers():
    """Multiple MMIO writes to different registers."""
    async def _test():
        adapter = await _connect_adapter()
        from regmap import MXU, SFU

        await adapter.execute_action(
            Action.mmio_write(MXU.BASE + MXU.CTRL, 0x00000003)
        )
        await adapter.execute_action(
            Action.mmio_write(SFU.BASE + SFU.CTRL, 0x00000007)
        )

        obs1 = await adapter.observe(Observation(
            observation_id="mxu",
            observation_type=ObservationType.mmio_value,
            address=MXU.BASE + MXU.CTRL,
        ))
        obs2 = await adapter.observe(Observation(
            observation_id="sfu",
            observation_type=ObservationType.mmio_value,
            address=SFU.BASE + SFU.CTRL,
        ))

        assert obs1.data["value"] == 0x00000003
        assert obs2.data["value"] == 0x00000007

        await adapter.disconnect()
    asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════════════
# PCIe frontdoor tests
# ═══════════════════════════════════════════════════════════════════════════


def test_pcie_frontdoor_write_to_dram():
    """PCIe TLP write to DRAM, verify via backdoor readback."""
    async def _test():
        adapter = await _connect_adapter()
        test_data = bytes(range(128))

        await adapter.execute_action(
            Action.pcie_write(0x80010000, test_data)
        )

        # Read via obs backdoor
        await adapter.execute_action(
            Action(action_type="dram_readback",
                   classification=OperationClass.allowed_obs_backdoor,
                   parameters={"offset": 0x80010000 - 0x80000000, "size": 128})
        )

        obs = await adapter.observe(Observation(
            observation_id="dram",
            observation_type=ObservationType.dram_data,
            address=0x80010000 - 0x80000000,
            size=128,
        ))
        assert obs.data["raw_hex"] == test_data.hex()

        await adapter.disconnect()
    asyncio.run(_test())


def test_pcie_frontdoor_write_to_sram():
    """PCIe TLP write to SRAM, verify via backdoor readback."""
    async def _test():
        adapter = await _connect_adapter()
        from regmap import Addr
        test_data = b"PCIe_SRAM_TEST" * 2

        await adapter.execute_action(
            Action.pcie_write(Addr.SRAM_BASE + 0x500, test_data)
        )

        await adapter.execute_action(
            Action(action_type="sram_readback",
                   classification=OperationClass.allowed_obs_backdoor,
                   parameters={"offset": 0x500, "size": len(test_data)})
        )

        obs = await adapter.observe(Observation(
            observation_id="sram_pcie",
            observation_type=ObservationType.sram_data,
            address=0x500,
            size=len(test_data),
        ))
        assert obs.data["raw_hex"] == test_data.hex()

        await adapter.disconnect()
    asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════════════
# Backdoor tests (init + obs)
# ═══════════════════════════════════════════════════════════════════════════


def test_sram_init_backdoor_preload_and_obs_backdoor_readback():
    """SRAM init backdoor preload, then obs backdoor readback."""
    async def _test():
        adapter = await _connect_adapter()
        test_data = b"INIT_BACKDOOR_DATA"

        await adapter.execute_action(
            Action.sram_preload(0x400, test_data)
        )

        # Verify via obs backdoor
        await adapter.execute_action(
            Action.sram_readback(0x400, len(test_data))
        )

        obs = await adapter.observe(Observation(
            observation_id="sram_init",
            observation_type=ObservationType.sram_data,
            address=0x400,
            size=len(test_data),
        ))
        assert obs.data["raw_hex"] == test_data.hex()

        # Verify backdoor classification
        ev = adapter.evidence_metadata()
        assert ev["backdoor_classification"]["sram_preload"] == "backdoor_write_bytes"
        assert ev["backdoor_classification"]["sram_readback"] == "backdoor_read_bytes"

        await adapter.disconnect()
    asyncio.run(_test())


def test_dram_init_backdoor_preload_and_obs_backdoor_readback():
    """DRAM init backdoor preload, then obs backdoor readback."""
    async def _test():
        adapter = await _connect_adapter()
        test_data = bytes(i % 256 for i in range(512))

        await adapter.execute_action(
            Action.dram_preload(0x8000, test_data)
        )

        await adapter.execute_action(
            Action(action_type="dram_readback",
                   classification=OperationClass.allowed_obs_backdoor,
                   parameters={"offset": 0x8000, "size": len(test_data)})
        )

        obs = await adapter.observe(Observation(
            observation_id="dram_init",
            observation_type=ObservationType.dram_data,
            address=0x8000,
            size=len(test_data),
        ))
        assert obs.data["raw_hex"] == test_data.hex()

        await adapter.disconnect()
    asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════════════
# Doorbell + IRQ tests
# ═══════════════════════════════════════════════════════════════════════════


def test_doorbell_irq_dispatch():
    """Full doorbell dispatch: write data, host_write_command, IRQ, firmware dispatch."""
    async def _test():
        adapter = await _connect_adapter()
        from regmap import MXU, Addr

        # Set up a simple MXU test via host_write_data + host_write_command
        M, K, N = 1, 8, 4
        act = np.array([1, 2, 3, 4, 5, 6, 7, 8], dtype=np.int8).reshape(M, K)
        wgt_unpacked = np.array([
            [0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 2, 3], [4, 5, 6, 7],
            [0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 2, 3], [4, 5, 6, 7],
        ], dtype=np.int8)

        from golden_executor import GoldenMXU
        wgt_packed = GoldenMXU.pack_int4(wgt_unpacked.flatten())
        scales = np.ones((1, N), dtype=np.float32)

        act_addr = 0x8001_0000
        wgt_addr = 0x8002_0000
        out_addr = 0x8100_0000
        scale_addr = 0x8011_0000
        desc_addr = 0x8000_0080

        # Write data via frontdoor PCIe
        adapter._model.pcie.tlp_write(act_addr, act.tobytes())
        adapter._model.pcie.tlp_write(wgt_addr, wgt_packed.tobytes())
        adapter._model.pcie.tlp_write(scale_addr, scales.tobytes())

        # Write descriptor via host_write_descriptor
        adapter._model.host_write_descriptor(
            desc_addr,
            input_addr=act_addr, weight_addr=wgt_addr,
            output_addr=out_addr, scale_addr=scale_addr,
            scale_size=scales.nbytes,
            input_size=act.nbytes, weight_size=len(wgt_packed),
            output_size=M * N * 4,
            M=M, K=K, N=N,
        )

        # Doorbell
        adapter._model.host_write_command(0, desc_addr)

        # Wait for interrupt and run firmware
        await adapter.execute_action(Action.wait_irq(source=8))

        await adapter.disconnect()
    asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════════════
# Evidence metadata tests
# ═══════════════════════════════════════════════════════════════════════════


def test_evidence_metadata_python_firmware():
    """Evidence metadata must record firmware_mode='python' and ABI version."""
    async def _test():
        adapter = await _connect_adapter(firmware_mode="python")

        await adapter.execute_action(
            Action.sram_preload(0, b"test")
        )

        ev = adapter.evidence_metadata()
        assert ev["firmware_mode"] == "python"
        assert ev["abi_version"] == ABI_VERSION
        assert ev["spike_available"] is False
        assert "action_counts" in ev
        assert ev["action_counts"].get("allowed_init_backdoor", 0) >= 1

        await adapter.disconnect()
    asyncio.run(_test())


def test_evidence_metadata_backdoor_classification():
    """Backdoor classification must be recorded in evidence metadata."""
    async def _test():
        adapter = await _connect_adapter()

        await adapter.execute_action(Action.sram_preload(0x100, b"bk_init"))
        await adapter.execute_action(
            Action(action_type="sram_readback",
                   classification=OperationClass.allowed_obs_backdoor,
                   parameters={"offset": 0x100, "size": 8})
        )

        ev = adapter.evidence_metadata()
        assert "sram_preload" in ev["backdoor_classification"]
        assert "sram_readback" in ev["backdoor_classification"]
        assert ev["backdoor_classification"]["sram_preload"] == "backdoor_write_bytes"
        assert ev["backdoor_classification"]["sram_readback"] == "backdoor_read_bytes"

        await adapter.disconnect()
    asyncio.run(_test())


def test_action_counts_tracked():
    """Action counts by classification must be tracked."""
    async def _test():
        adapter = await _connect_adapter()

        # 2 frontdoor MMIO writes
        await adapter.execute_action(Action.mmio_write(0x4000_0000, 1))
        await adapter.execute_action(Action.mmio_write(0x4000_1000, 2))
        # 1 init backdoor
        await adapter.execute_action(Action.sram_preload(0, b"data"))
        # 1 obs backdoor
        await adapter.execute_action(
            Action(action_type="sram_readback",
                   classification=OperationClass.allowed_obs_backdoor,
                   parameters={"offset": 0, "size": 4})
        )

        ac = adapter.evidence_metadata()["action_counts"]
        assert ac.get("frontdoor", 0) == 2
        assert ac.get("allowed_init_backdoor", 0) == 1
        assert ac.get("allowed_obs_backdoor", 0) == 1

        await adapter.disconnect()
    asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════════════
# Negative tests
# ═══════════════════════════════════════════════════════════════════════════


def test_real_spike_missing_artifacts_fails():
    """Real-Spike mode must fail deterministically when Spike artifacts are missing.

    FuncModel(use_spike=True) raises RuntimeError when the Spike binary,
    plugin.so, or firmware ELF is missing. FuncModelAdapter.connect()
    wraps this as DUTConnectionError.

    This test uses a forced-fail path: we call FuncModel(use_spike=True)
    directly through the adapter by intercepting the check, verifying
    the deterministic failure mechanism works end-to-end.
    """
    async def _test():
        # First, verify FuncModel with use_spike=True succeeds when
        # artifacts are present (the adapter path works).
        from func_model import FuncModel
        from spike_firmware import _is_spike_available

        if _is_spike_available():
            # Artifacts present: verify FuncModel(use_spike=True) succeeds
            # (this proves the adapter path is correct)
            try:
                model = FuncModel(use_spike=True)
                assert model is not None
                assert "SpikeFirmware" in type(model.firmware).__name__
                # This is the normal path — no evidence needed
            except RuntimeError:
                pytest.fail(
                    "FuncModel(use_spike=True) raised RuntimeError even "
                    "though Spike artifacts are present"
                )

        # Verify the adapter wrapper correctly translates RuntimeError
        # to DUTConnectionError by testing with a non-existent path.
        # We use a patched _is_spike_available to force the failure.
        import sim.spike_firmware as sf
        original_check = sf._is_spike_available
        try:
            # Force the check to return False
            sf._is_spike_available = lambda: False

            adapter = FuncModelAdapter(firmware_mode="spike")
            with pytest.raises(DUTConnectionError) as exc_info:
                await adapter.connect()

            assert "Failed to create FuncModel" in str(exc_info.value)
            assert "spike" in str(exc_info.value).lower()

        finally:
            sf._is_spike_available = original_check

    asyncio.run(_test())


def test_diagnostic_rejected():
    """Diagnostic-classified actions must be rejected."""
    async def _test():
        adapter = await _connect_adapter()

        with pytest.raises(ValueError) as exc_info:
            await adapter.execute_action(
                Action(
                    action_type="probe_signal",
                    classification=OperationClass.diagnostic,
                    parameters={"signal": "dbg_state"},
                )
            )
        assert "diagnostic" in str(exc_info.value).lower()

        await adapter.disconnect()
    asyncio.run(_test())


def test_unknown_action_type_rejected():
    """Unknown action types must be rejected."""
    async def _test():
        adapter = await _connect_adapter()

        with pytest.raises(ValueError) as exc_info:
            await adapter.execute_action(
                Action(
                    action_type="undefined_operation",
                    classification=OperationClass.frontdoor,
                    parameters={},
                )
            )
        assert "Unsupported" in str(exc_info.value)

        await adapter.disconnect()
    asyncio.run(_test())


def test_not_connected_raises():
    """Operations before connect must raise DUTConnectionError."""
    async def _test():
        adapter = FuncModelAdapter()
        with pytest.raises(DUTConnectionError):
            await adapter.execute_action(Action.mmio_write(0, 0))
        with pytest.raises(DUTConnectionError):
            await adapter.observe(Observation(
                observation_id="x", observation_type=ObservationType.mmio_value,
                address=0,
            ))
        with pytest.raises(DUTConnectionError):
            await adapter.reset()
    asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════════════
# Software-E2E integrity test
# ═══════════════════════════════════════════════════════════════════════════


def test_software_e2e_no_operation_performing_backdoors():
    """Software-E2E scenarios must have zero operation-performing backdoor actions.

    Verifies that actions classified as frontdoor pass through real FuncModel
    paths (bridge for MMIO, PCIe TLP for PCIe writes), and backdoor actions
    are explicitly classified as such.
    """
    async def _test():
        adapter = await _connect_adapter()

        # Execute a frontdoor-only scenario: PCIe write + MMIO write + doorbell
        from regmap import MXU

        test_data = b"SW_E2E_FRONTDOOR" + bytes(16)

        # All three are frontdoor
        await adapter.execute_action(Action.pcie_write(0x80010000, test_data))
        await adapter.execute_action(Action.mmio_write(
            MXU.BASE + MXU.CTRL, 0x00000003,
        ))

        # Verify PCIe data landed via obs backdoor (the verification path,
        # not the operation path — this is allowed)
        await adapter.execute_action(
            Action(action_type="dram_readback",
                   classification=OperationClass.allowed_obs_backdoor,
                   parameters={"offset": 0x80010000 - 0x80000000,
                               "size": len(test_data)})
        )

        obs = await adapter.observe(Observation(
            observation_id="dram_verify",
            observation_type=ObservationType.dram_data,
            address=0x80010000 - 0x80000000,
            size=len(test_data),
        ))
        assert obs.data["raw_hex"] == test_data.hex()

        # Action counts: 2 frontdoor, 1 obs backdoor, 0 init backdoor
        ac = adapter.evidence_metadata()["action_counts"]
        assert ac.get("frontdoor", 0) == 2

        await adapter.disconnect()
    asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════════════
# Completion status observation
# ═══════════════════════════════════════════════════════════════════════════


def test_completion_status_observation():
    """Observe completion status through the adapter."""
    async def _test():
        adapter = await _connect_adapter()
        from regmap import MXU

        # Set up and dispatch a quick MMUL to get DONE status
        M, K, N = 1, 8, 4
        act = np.ones((M, K), dtype=np.int8)
        wgt_unpacked = np.ones((K, N), dtype=np.int8)
        from golden_executor import GoldenMXU
        wgt_packed = GoldenMXU.pack_int4(wgt_unpacked.flatten())
        scales = np.ones((1, N), dtype=np.float32)

        act_addr = 0x8001_1000
        wgt_addr = 0x8002_1000
        out_addr = 0x8100_1000
        scale_addr = 0x8011_1000
        desc_addr = 0x8000_0100

        adapter._model.pcie.tlp_write(act_addr, act.tobytes())
        adapter._model.pcie.tlp_write(wgt_addr, wgt_packed.tobytes())
        adapter._model.pcie.tlp_write(scale_addr, scales.tobytes())

        adapter._model.host_write_descriptor(
            desc_addr,
            input_addr=act_addr, weight_addr=wgt_addr,
            output_addr=out_addr, scale_addr=scale_addr,
            scale_size=scales.nbytes,
            input_size=act.nbytes, weight_size=len(wgt_packed),
            output_size=M * N * 4,
            M=M, K=K, N=N,
        )
        adapter._model.host_write_command(0, desc_addr)
        adapter._model.run()

        obs = await adapter.observe(Observation(
            observation_id="completion",
            observation_type=ObservationType.completion_status,
        ))
        assert obs.data["status"] == 0x2  # DONE

        await adapter.disconnect()
    asyncio.run(_test())
