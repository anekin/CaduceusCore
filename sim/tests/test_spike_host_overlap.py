"""Scheduling-time overlap guards in sim/spike_host.py (fm-hardening-phase10, todo 2).

:func:`spike_host.schedule_chain` and :func:`spike_host.write_cmd_entry` must
assert the DRAM address-space contract at dispatch time so that a stale
``DESC_BASE`` (BUG-RTL-SOC-008 class) or an over-long chain fails loudly with
``OverlapError``/``WindowError`` **before** any descriptor or command entry is
written into DRAM.

Happy scenario: a valid chain passes the contract, writes its descriptors and
command entries, and rings the doorbell. Failure scenarios inject a
``DESC_BASE`` below the completion-ring end, a descriptor count that pushes
the pool past ``P10_ACT_BASE``, or a base outside the 8 MB window — each must
raise before any per-op access/write.
"""

import struct

import numpy as np
import pytest

from sim import spike_host

from func_model import FuncModel
from regmap import DOORBELL

# spike_host imports address_space as a top-level module; the exceptions it
# raises come from that module object, which is distinct from sim.address_space.
OverlapError = spike_host.address_space.OverlapError
WindowError = spike_host.address_space.WindowError

_DRAM_BASE = spike_host.FIRMWARE_RING_BASE  # 0x80000000


def _dram_offset(addr: int) -> int:
    return addr - _DRAM_BASE


def test_schedule_chain_happy_path_writes_descriptors_and_doorbell():
    model = FuncModel(sram_kb=4096)
    model.firmware.ring_buffer_addr = spike_host.FIRMWARE_RING_BASE
    rng = np.random.RandomState(7)
    op, _ = spike_host._prepare_mmul_op(model, 0, rng)

    n = spike_host.schedule_chain([op], model)

    assert n == 1
    desc = op["desc"]
    expected_desc = struct.pack(
        spike_host.MMUL_DESC_FMT,
        desc["input_addr"], desc["weight_addr"], desc["output_addr"], 0,
        desc["input_sram"], desc["weight_sram"], desc["output_sram"], 0,
        desc["input_size"], desc["weight_size"], desc["output_size"], 0,
        desc["M"], desc["K"], desc["N"],
    )
    off = _dram_offset(spike_host.DESC_BASE)
    got_desc = bytes(model.dram[off:off + spike_host.MMUL_DESC_SIZE])
    assert got_desc == expected_desc

    entry = struct.unpack(
        spike_host.CMD_ENTRY_FMT,
        bytes(model.dram[0:spike_host.CMD_ENTRY_SIZE]),
    )
    assert entry[0] == int(spike_host.EngineOp.MMUL)
    assert entry[1] == spike_host.DESC_BASE

    assert model.bridge._status[DOORBELL.BASE + DOORBELL.HOST_TAIL] == 1


def test_schedule_chain_empty_qa_command():
    """Happy QA command from the plan: scheduling an empty chain succeeds."""
    assert spike_host.schedule_chain([]) == 0


def test_schedule_chain_stale_desc_base_raises_overlap_before_any_write(monkeypatch):
    """DESC_BASE below completion-ring end raises before any per-op write."""
    monkeypatch.setattr(spike_host, "DESC_BASE", 0x80001000)
    model = FuncModel(sram_kb=4096)
    before = bytes(model.dram)

    with pytest.raises(OverlapError):
        spike_host.schedule_chain([0] * 20, model)

    assert bytes(model.dram) == before, "contract must fail before any DRAM write"


def test_schedule_chain_desc_count_past_act_base_raises():
    """1025 descriptors overflow the pool into the activation arena."""
    with pytest.raises(OverlapError):
        spike_host.schedule_chain([{}] * 1025)


def test_schedule_chain_out_of_window_desc_raises_window_error(monkeypatch):
    """Descriptor region crossing DRAM_END raises WindowError."""
    monkeypatch.setattr(spike_host, "DESC_BASE", 0x807FFF00)
    with pytest.raises(WindowError):
        spike_host.schedule_chain([0] * 16)


def test_write_cmd_entry_rejects_desc_below_completion_end():
    model = FuncModel(sram_kb=4096)
    with pytest.raises(OverlapError):
        spike_host.write_cmd_entry(model, 0, opcode=0, desc_addr=0x80001000)


def test_write_cmd_entry_happy_path_writes_entry():
    model = FuncModel(sram_kb=4096)
    spike_host.write_cmd_entry(model, 0, opcode=7,
                               desc_addr=spike_host.DESC_BASE)
    entry = struct.unpack(
        spike_host.CMD_ENTRY_FMT,
        bytes(model.dram[0:spike_host.CMD_ENTRY_SIZE]),
    )
    assert entry[0] == 7
    assert entry[1] == spike_host.DESC_BASE
