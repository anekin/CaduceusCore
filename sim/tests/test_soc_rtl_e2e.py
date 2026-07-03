"""
test_soc_rtl_e2e.py — Lightweight RTL SoC E2E Test

SoC Phase 3-4 / Todo 3 (soc-rtl-substitution)

Imports RTLSoCRunner and runs selected FM-SOC-NNN cases against the
RTL SoC via CocotbBridge.

This test file is designed to be run INSIDE a cocotb simulation context
(i.e., via the Makefile's `run_fm_soc_case` target). It reads the
FM_SOC_CASE_ID environment variable to select which case to run.

Usage (via Makefile on EDA server):
    make -C sim/regression run_fm_soc_case CASE_ID=FM-SOC-001

Usage (for direct Python validation outside cocotb):
    PYTHONPATH=sim python sim/tests/test_soc_rtl_e2e.py
"""

import os
import logging
import sys

# When run standalone (not via cocotb), ensure the CaduceusCore/
# package root is on sys.path so relative imports resolve.
# Cocotb sets PYTHONPATH itself, so this no-ops under cocotb.
try:
    _here = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _here = os.getcwd()
_repo = os.path.dirname(os.path.dirname(_here))
if os.path.isdir(os.path.join(_repo, "sim")) and _repo not in sys.path:
    sys.path.insert(0, _repo)

# Conditionally import cocotb (only available during simulation)
try:
    import cocotb
    from cocotb.triggers import Timer
    COCOTB_AVAILABLE = True
except ImportError:
    COCOTB_AVAILABLE = False

from sim.rtl_soc_runner import RTLSoCRunner, TestCaseConfig
from sim.regmap import Addr, MXU, SFU, VECTOR, DMA, DOORBELL, INTC

try:
    from sim.cocotb_bridge import CocotbBridge
except ImportError:
    CocotbBridge = None

logger = logging.getLogger("test_soc_rtl_e2e")


# ═══════════════════════════════════════════════════════════════════════════
# Case registry — maps case_id to test configuration
# ═══════════════════════════════════════════════════════════════════════════

def build_case_001() -> TestCaseConfig:
    """FM-SOC-001: APB-MMIO write/readback on MXU CTRL register."""
    cfg = TestCaseConfig(
        case_id="FM-SOC-001",
        description="APB-MMIO write/readback: MXU CTRL register",
    )
    cfg.mmio_writes = {MXU.BASE + MXU.CTRL: 0x0000_0001}
    cfg.mmio_readbacks = {MXU.BASE + MXU.CTRL: 0x0000_0001}
    return cfg


# ═══════════════════════════════════════════════════════════════════════════
# Cocotb test entry points
# ═══════════════════════════════════════════════════════════════════════════

if COCOTB_AVAILABLE:

    @cocotb.test()
    async def test_soc_rtl_e2e_case(dut):
        """Run a single FM-SOC-NNN case against the RTL SoC.

        Reads FM_SOC_CASE_ID from environment; defaults to FM-SOC-001.
        """
        case_id = os.environ.get("FM_SOC_CASE_ID", "FM-SOC-001")
        logger.info(f"test_soc_rtl_e2e_case: {case_id}")

        bridge = CocotbBridge(dut)
        await bridge.start_clock()
        await bridge.reset(5)

        runner = RTLSoCRunner(bridge)

        # Register known cases
        runner.register_case(build_case_001())

        # Run the case
        passed = await runner.run_single_case(case_id)

        summary = runner.summary()
        logger.info(f"test_soc_rtl_e2e_case: {case_id} — "
                    f"{'PASS' if passed else 'FAIL'}")
        logger.info(f"Summary: {summary}")

        assert passed, f"{case_id} FAILED"


    @cocotb.test()
    async def test_soc_rtl_e2e_smoke(dut):
        """Smoke test: run FM-SOC-001 with explicit configuration.

        This is the entry point used by the Makefile's run_fm_soc_case
        target with default settings.
        """
        case_id = "FM-SOC-001"
        logger.info(f"test_soc_rtl_e2e_smoke: {case_id}")

        bridge = CocotbBridge(dut)
        await bridge.start_clock()
        await bridge.reset(5)

        runner = RTLSoCRunner(bridge)
        runner.register_case(build_case_001())

        passed = await runner.run_single_case(case_id)

        assert passed, f"{case_id} FAILED"


# ═══════════════════════════════════════════════════════════════════════════
# Standalone validation (outside cocotb)
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("test_soc_rtl_e2e.py — Standalone API validation")

    # Validate case configuration
    cfg = build_case_001()
    assert cfg.case_id == "FM-SOC-001"
    assert cfg.mmio_writes == {MXU.BASE + MXU.CTRL: 0x0000_0001}
    assert cfg.mmio_readbacks == {MXU.BASE + MXU.CTRL: 0x0000_0001}
    print(f"  FM-SOC-001 config: OK — {cfg.description}")

    # Validate RTLSoCRunner off-cocotb
    from sim.rtl_soc_runner import RTLSoCRunner
    from sim.cocotb_bridge import CocotbBridge
    bridge = CocotbBridge()  # no DUT
    runner = RTLSoCRunner(bridge)
    runner.register_case(cfg)

    # Verify case retrieval
    retrieved = runner._get_case("FM-SOC-001")
    assert retrieved.case_id == "FM-SOC-001"
    print("  RTLSoCRunner case registry: OK")

    # Verify mixed-mode define API
    runner.enable_rtl("pcie")
    runner.use_golden("pcie")
    assert "pcie" not in runner._rtl_modules
    print("  Mixed-mode toggle API: OK")

    print("test_soc_rtl_e2e.py — All validations PASSED")
