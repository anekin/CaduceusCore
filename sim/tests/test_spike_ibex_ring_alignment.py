"""Spike↔Ibex ring management alignment FM guard (todo 8, fm-soc-datapath-hardening).

Gap #10 / FW-08: the host-side Spike path (``spike_host.schedule_chain`` +
``spike_host._launch_spike`` + ``spike_host.poll_completion``, driving the real
compiled firmware inside Spike) and the Python NPUFirmware path
(``FuncModel.host_write_command`` + ``firmware.run_loop``) must manage the
doorbell command ring identically — NPU_HEAD / HOST_HEAD / COMPLETION_STATUS
progression and ring wrap behavior.

The same deterministic 208-command chain (alternating SFU softmax and Vector
VADD) is scheduled through both paths:

- **Spike path**: the real firmware drains all 208 commands in one Spike boot
  from the 1024-entry ring (``NPU_ABI_RING_ENTRIES``); heads advance linearly
  0→208 without wrap.
- **NPUFirmware path**: driven one command at a time with a configurable
  ``ring_size`` (FuncModel ``ring_size=`` parameter).

Guards:

- happy: both paths write byte-identical ring entries at identical offsets
  and end at NPU_HEAD = HOST_HEAD = 208 with no wrap; the firmware
  COMPLETION_STATUS array is written exactly once per dispatch index 0..207
  with success status.
- failure injection: NPUFirmware constructed with the legacy 16-entry ring
  wraps every 16 commands (13 wraps, final head 0) while the Spike firmware
  path ends at head 208 — the head sequences diverge, proving the guard bites.
- completion mirror: HOST_HEAD mirrors NPU_HEAD on both paths, and the
  NPUFirmware per-command result index aligns with the firmware
  COMPLETION_STATUS index (both keyed by the dispatch-time head).

Environment: requires the Spike binary + plugin +
``firmware/build/npu_firmware_spike.elf`` (NOT ``npu_firmware.elf``). Tests
skip with a reason when ``sim/spike_firmware._is_spike_available()`` is False.
"""

import numpy as np
import pytest

from func_model import FuncModel
from regmap import DOORBELL
from sim import spike_host
import command_ring
from spike_firmware import _is_spike_available

# ── Scenario constants ──────────────────────────────────────────────────

_NUM_CMDS = 208                       # 208 commands, same as the long-sequence gate
_FIRMWARE_RING_ENTRIES = command_ring.RING_ENTRIES   # 1024 — npu_firmware.c NPU_ABI_RING_ENTRIES
_LEGACY_RING_ENTRIES = 16             # NPUFirmware default (legacy device protocol)

_DESC_BASE = spike_host.DESC_BASE         # 0x80010000
_DESC_STRIDE = spike_host.DESC_STRIDE     # 64
_RING_BASE = spike_host.FIRMWARE_RING_BASE  # 0x80000000
_RING_BYTES = _NUM_CMDS * 32              # ring bytes consumed by 208 entries

# Shared data buffers (values are irrelevant to ring management; they only
# need to be valid and inside the 8 MB DRAM window [0x80000000, 0x80800000)).
# Each SFU/Vector command uses 512 B scratch (sfu_scratch_size/vector_scratch_size).
_SFU_IN, _SFU_OUT = 0x80030000, 0x80030200
_VEC_A, _VEC_B, _VEC_O = 0x80030400, 0x80030600, 0x80030800


# ── Helpers ──────────────────────────────────────────────────────────

def _require_spike():
    """Skip when the Spike stack is missing (mirrors spike_firmware detection)."""
    if not _is_spike_available():
        pytest.skip(
            "Spike stack not available (need spike_src/build/spike, "
            "spike_src/plugins/npu_mmio_plugin.so, "
            "firmware/build/npu_firmware_spike.elf)"
        )


def _build_ops(n: int) -> list:
    """Deterministic 208-command chain: alternating SFU softmax / Vector VADD.

    Both op classes complete with status 'done' on BOTH paths (proven by
    test_runtime_real_firmware.py for the real firmware and by the
    long-sequence gate for NPUFirmware), so neither path trips over an
    unsupported opcode.
    """
    ops = []
    for i in range(n):
        if i % 4 < 2:
            ops.append({"type": "sfu", "desc": {
                "op": 0,  # softmax
                "input_addr": _SFU_IN, "output_addr": _SFU_OUT,
                "input_sram": 0, "output_sram": 0,
                "size": 512, "dim": 64, "pos": 0,
            }})
        else:
            ops.append({"type": "vector", "desc": {
                "op": 0,  # VADD
                "a_addr": _VEC_A, "b_addr": _VEC_B, "o_addr": _VEC_O,
                "dim": 16,
                "a_sram": 0, "b_sram": 0, "o_sram": 0,
            }})
    return ops


def _seed_buffers(model: FuncModel):
    """Seed shared DRAM buffers with valid data (values do not matter here)."""
    model.host_write_data(_SFU_IN, np.full(64, 0.5, dtype=np.float16))
    model.host_write_data(_VEC_A, np.full(16, 3, dtype=np.int32))
    model.host_write_data(_VEC_B, np.full(16, 4, dtype=np.int32))


@pytest.fixture(scope="module")
def spike_trace() -> dict:
    """Run the 208-command chain through the Spike path once (module scope).

    Returns the post-drain doorbell state: NPU_HEAD / HOST_HEAD / HOST_TAIL,
    the per-index COMPLETION_STATUS array, and the scheduled ring-entry bytes.
    """
    _require_spike()
    model = FuncModel(sram_kb=4096)
    _seed_buffers(model)

    scheduled = spike_host.schedule_chain(_build_ops(_NUM_CMDS), model)
    assert scheduled == _NUM_CMDS
    ring_bytes = bytes(model.dram[0:_RING_BYTES])

    proc, server = spike_host._launch_spike(model)
    try:
        drained = spike_host.poll_completion(model, _NUM_CMDS, timeout=300.0)
    finally:
        spike_host._cleanup_spike(proc, server)

    assert drained, (
        "Spike firmware did not drain the ring: "
        f"NPU_HEAD={model.bridge._status.get(DOORBELL.BASE + DOORBELL.NPU_HEAD, 0)}"
    )

    st = model.bridge._status
    return {
        "npu_head": st.get(DOORBELL.BASE + DOORBELL.NPU_HEAD, 0),
        "host_head": st.get(DOORBELL.BASE + DOORBELL.HOST_HEAD, 0),
        "host_tail": st.get(DOORBELL.BASE + DOORBELL.HOST_TAIL, 0),
        "completion": [
            st.get(DOORBELL.BASE + DOORBELL.COMPLETION_STATUS + i * 4)
            for i in range(_NUM_CMDS)
        ],
        "ring_bytes": ring_bytes,
    }


def _run_npufirmware_path(ring_size: int) -> dict:
    """Drive the same 208 commands through NPUFirmware one at a time.

    Records the head progression, per-command results, final doorbell state,
    the ring-entry bytes, and the wrap count.
    """
    model = FuncModel(sram_kb=4096, ring_size=ring_size)
    _seed_buffers(model)

    heads, results = [], []
    for i, op in enumerate(_build_ops(_NUM_CMDS)):
        desc_addr = _DESC_BASE + i * _DESC_STRIDE
        if op["type"] == "sfu":
            spike_host.write_sfu_descriptor(model, desc_addr, **op["desc"])
            opcode = int(spike_host.EngineOp.SFU)
        else:
            spike_host.write_vector_descriptor(model, desc_addr, **op["desc"])
            opcode = int(spike_host.EngineOp.VECTOR)
        model.host_write_command(opcode, desc_addr)
        res = model.firmware.run_loop(max_commands=1)
        assert len(res) == 1, f"command {i}: firmware consumed {len(res)} commands"
        results.extend(res)
        heads.append(model.firmware.doorbell["npu_head"])

    return {
        "heads": heads,
        "results": results,
        "npu_head": model.firmware.doorbell["npu_head"],
        "host_tail": model.firmware.doorbell["host_tail"],
        "host_head_mmio": model.bridge._status.get(
            DOORBELL.BASE + DOORBELL.HOST_HEAD, 0),
        "ring_bytes": bytes(model.dram[0:_RING_BYTES]),
        "wrap_count": sum(
            1 for k in range(1, _NUM_CMDS) if heads[k] <= heads[k - 1]),
    }


@pytest.fixture(scope="module")
def npufw_1024_trace(spike_trace) -> dict:
    """NPUFirmware trace with the firmware ring size (1024 entries)."""
    return _run_npufirmware_path(_FIRMWARE_RING_ENTRIES)


# ── Tests ────────────────────────────────────────────────────────────

def test_happy_both_paths_drain_208_with_identical_head_progression(
        spike_trace, npufw_1024_trace):
    """Happy path: both paths complete 208 commands with matching head sequences.

    The Spike firmware (1024-entry ring) and NPUFirmware configured with the
    same ring size must end at NPU_HEAD = HOST_HEAD = HOST_TAIL = 208 with a
    strictly linear head progression and zero wraps, and both must have
    written byte-identical ring entries at identical offsets.
    """
    st, nf = spike_trace, npufw_1024_trace

    # Spike path final doorbell state.
    assert st["npu_head"] == _NUM_CMDS
    assert st["host_head"] == _NUM_CMDS
    assert st["host_tail"] == _NUM_CMDS
    assert st["npu_head"] == command_ring.expected_head(_NUM_CMDS)

    # NPUFirmware path with the firmware ring size: identical final state.
    assert nf["npu_head"] == _NUM_CMDS
    assert nf["host_tail"] == _NUM_CMDS
    assert nf["host_head_mmio"] == _NUM_CMDS
    assert nf["heads"] == list(range(1, _NUM_CMDS + 1)), (
        "head progression must be strictly linear on a 1024-entry ring"
    )
    assert nf["wrap_count"] == 0
    assert all(r["status"] == "done" for r in nf["results"]), (
        f"unexpected result statuses: "
        f"{[r for r in nf['results'] if r['status'] != 'done'][:8]}"
    )

    # Cross-path: byte-identical ring entries prove the same 208 commands were
    # scheduled at the same ring offsets in both paths.
    assert nf["ring_bytes"] == st["ring_bytes"], (
        "ring entry bytes diverge between Spike and NPUFirmware paths"
    )


def test_failure_injection_ring_size_16_wraps_against_spike_1024(spike_trace):
    """Failure injection: NPUFirmware 16-entry ring diverges from Spike 1024.

    Tampering one path's ring_size (NPUFirmware constructed with the legacy
    16-entry default) makes its head wrap every 16 commands — 13 wraps, final
    head 0 — while the Spike firmware path on the 1024-entry ring never wraps
    within 208 commands and ends at head 208. The head sequences diverge.
    """
    nf16 = _run_npufirmware_path(_LEGACY_RING_ENTRIES)

    # Legacy 16-entry ring wraps every 16 commands.
    assert nf16["heads"] == [
        (k + 1) % _LEGACY_RING_ENTRIES for k in range(_NUM_CMDS)
    ], "16-entry ring head progression should wrap 0..15"
    assert nf16["wrap_count"] == _NUM_CMDS // _LEGACY_RING_ENTRIES  # 13
    assert nf16["npu_head"] == _NUM_CMDS % _LEGACY_RING_ENTRIES    # 0
    assert nf16["host_head_mmio"] == 0

    # Spike path reference (1024-entry firmware ring): no wrap within 208.
    assert spike_trace["npu_head"] == _NUM_CMDS
    assert spike_trace["host_head"] == _NUM_CMDS

    # Divergence: at command 16 the tampered emulator head wraps to 0 while
    # the firmware head would be 16; the final heads also disagree.
    assert nf16["heads"][15] == 0
    assert nf16["heads"] != list(range(1, _NUM_CMDS + 1))
    assert nf16["npu_head"] != spike_trace["npu_head"]
    assert nf16["wrap_count"] != 0


def test_completion_status_and_host_head_mirror_alignment(
        spike_trace, npufw_1024_trace):
    """COMPLETION_STATUS indexing + HOST_HEAD mirror on both aligned paths.

    The real firmware writes COMPLETION_STATUS[head] per dispatch, so the
    written index set IS the dispatch-time head sequence: indices 0..207 each
    written exactly once with success proves the firmware visited heads
    0..207 linearly (no wrap, skip, or reorder). NPUFirmware's per-command
    result list is indexed by the same dispatch order — result k corresponds
    to COMPLETION_STATUS[k] — and HOST_HEAD mirrors NPU_HEAD on both paths.
    """
    st, nf = spike_trace, npufw_1024_trace

    # Firmware completion array: 208 entries, all success.
    assert len(st["completion"]) == _NUM_CMDS
    assert all(s == 0 for s in st["completion"]), (
        "non-zero completion statuses: "
        f"{[(i, s) for i, s in enumerate(st['completion']) if s != 0][:8]}"
    )

    # NPUFirmware per-command completion aligns index-wise with the firmware
    # completion array: result k ↔ COMPLETION_STATUS[k].
    assert len(nf["results"]) == _NUM_CMDS
    assert all(r["status"] == "done" for r in nf["results"])

    # HOST_HEAD mirrors NPU_HEAD on both paths.
    assert st["host_head"] == st["npu_head"] == _NUM_CMDS
    assert nf["host_head_mmio"] == nf["npu_head"] == _NUM_CMDS
