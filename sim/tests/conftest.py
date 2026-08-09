"""Pytest conftest for CaduceusCore golden model tests.

Adds sim/ to sys.path for imports and provides shared fixtures.
"""

import sys
from pathlib import Path

# Add sim directory to path so golden_executor can be imported
_sim_dir = str(Path(__file__).resolve().parents[1])
if _sim_dir not in sys.path:
    sys.path.insert(0, _sim_dir)

from golden_executor import GoldenMXU, GoldenSFU, GoldenVector, GoldenDMA, GoldenExecutor, SRAM
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--require-spike",
        action="store_true",
        default=False,
        help="Fail tests when Spike firmware prerequisites are missing",
    )


@pytest.fixture
def golden_mxu():
    """Default GoldenMXU instance (64x64 broadcast-based block array)."""
    return GoldenMXU()


@pytest.fixture
def golden_executor():
    """Default GoldenExecutor instance with 64x64 broadcast-based block MXU."""
    return GoldenExecutor()


@pytest.fixture
def sram():
    """Fresh SRAM instance."""
    return SRAM()


@pytest.fixture(scope="session")
def require_spike(request):
    return request.config.getoption("--require-spike")


@pytest.fixture(scope="session")
def spike_available():
    """Check whether Spike firmware artifacts exist."""
    repo = Path(__file__).resolve().parents[2]
    spike_bin = repo / "spike_src" / "build" / "spike"
    plugin_so = repo / "spike_src" / "plugins" / "npu_mmio_plugin.so"
    fw_elf = repo / "firmware" / "build" / "npu_firmware_spike.elf"
    return spike_bin.exists() and plugin_so.exists() and fw_elf.exists()


@pytest.fixture(scope="session")
def func_model_spike(spike_available, require_spike):
    """Return a FuncModel(use_spike=True) or fail/skip as appropriate.

    When require_spike is set, a missing prerequisite causes test failure
    (nonzero exit) instead of skipping.
    """
    if not spike_available:
        if require_spike:
            pytest.fail(
                "Spike firmware prerequisites are missing.\n"
                "Run: python3 scripts/build_spike_stack.py --clean"
                " --manifest .omo/evidence/task-6-spike-build.json\n"
                "Then: make -C firmware"
            )
        else:
            pytest.skip("Spike firmware prerequisites not available")

    from func_model import FuncModel

    return FuncModel(use_spike=True, sram_kb=4096)
