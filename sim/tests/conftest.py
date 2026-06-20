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


@pytest.fixture
def golden_mxu():
    """Default GoldenMXU instance (128x128 systolic array)."""
    return GoldenMXU()


@pytest.fixture
def golden_executor():
    """Default GoldenExecutor instance with 128x128 MXU."""
    return GoldenExecutor()


@pytest.fixture
def sram():
    """Fresh SRAM instance."""
    return SRAM()
