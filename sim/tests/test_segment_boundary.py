"""Segment-boundary SRAM-clear contract tests (fm-hardening-phase10, todo 10)."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from cocotb_bridge import CocotbBridge, SegmentBoundaryError, SRAM_BASE, SRAM_SIZE


class DummyDut:
    pass


@pytest.fixture
def bridge(monkeypatch):
    b = CocotbBridge(DummyDut())
    monkeypatch.setattr(b, "_dram_bulk_load", AsyncMock())
    monkeypatch.setattr(b, "_dram_word_write", AsyncMock())
    monkeypatch.setattr(b, "_sram_backdoor_write", AsyncMock())
    return b


def _run(coro):
    return asyncio.run(coro)


def test_two_segment_sram_clear(bridge):
    dirty = b"\xab" * SRAM_SIZE

    _run(bridge.segment_preload(dram=bytes(8 * 1024 * 1024), sram=dirty))
    bridge._sram_backdoor_write.assert_awaited_once_with(SRAM_BASE, dirty)

    bridge._sram_backdoor_write.reset_mock()
    _run(bridge.segment_preload(
        dram=bytes(8 * 1024 * 1024),
        sram=b"\x00" * SRAM_SIZE,
        force_full=True,
        clear_sram=True,
    ))
    bridge._sram_backdoor_write.assert_awaited_once_with(SRAM_BASE, b"\x00" * SRAM_SIZE)


def test_segment_boundary_error_injection(bridge):
    with pytest.raises(SegmentBoundaryError):
        _run(bridge.segment_preload(
            dram=bytes(8 * 1024 * 1024),
            sram=b"",
            force_full=True,
            clear_sram=True,
        ))

    with pytest.raises(SegmentBoundaryError):
        _run(bridge.segment_preload(
            dram=bytes(8 * 1024 * 1024),
            sram=b"\xff" * SRAM_SIZE,
            force_full=True,
            clear_sram=True,
        ))

    with pytest.raises(SegmentBoundaryError):
        _run(bridge.segment_preload(
            dram=bytes(8 * 1024 * 1024),
            sram=b"\x00" * (SRAM_SIZE - 1),
            force_full=True,
            clear_sram=True,
        ))


def test_clear_sram_default_allows_empty_sram(bridge):
    _run(bridge.segment_preload(
        dram=bytes(8 * 1024 * 1024),
        sram=b"",
        force_full=True,
        clear_sram=False,
    ))
    bridge._sram_backdoor_write.assert_not_awaited()
