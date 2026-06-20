"""RED tests: tile_scheduler error handling.

tile_mmul currently has no input validation. These tests document
the expected behavior by asserting ValueError on invalid input,
which will FAIL until validation is added.
"""

import pytest
from tile_scheduler import tile_mmul


def _noop(*args, **kwargs):
    """Minimal mock that accepts any arguments and does nothing."""
    pass


class _MockRegMap:
    """Minimal register map mock providing attributes tile_mmul accesses."""
    pass


def _make_mocks():
    """Create mock register maps for DMA and MXU.

    Returns (mmio_write, mmio_read, wait_done, dma, mxu).
    """
    dma = _MockRegMap()
    dma.CH0_SRC = 0
    dma.CH0_DST = 4
    dma.CH0_SIZE = 8
    dma.CMD = 12
    dma.STATUS = 16
    dma.CH1_SRC = 20
    dma.CH1_DST = 24
    dma.CH1_SIZE = 28

    mxu = _MockRegMap()
    mxu.I_ADDR = 0
    mxu.W_ADDR = 4
    mxu.SCALE_ADDR = 8
    mxu.O_ADDR = 12
    mxu.CTRL = 16
    mxu.DIM0 = 20
    mxu.DIM1 = 24
    mxu.CMD = 28
    mxu.STATUS = 32

    return _noop, _noop, _noop, dma, mxu


def test_invalid_descriptor_shape():
    """Passing a non-dict descriptor should raise ValueError.

    Currently tile_mmul accesses desc['M'] directly without type
    checking, so this raises TypeError instead — test FAILs (RED).
    """
    mmio_write, mmio_read, wait_done, dma, mxu = _make_mocks()

    with pytest.raises(ValueError):
        tile_mmul("not a dict", mmio_write, mmio_read, wait_done,
                  0x1000, 0x2000, dma, mxu)
