"""RED negative tests for the C firmware address allowlist / size validation /
completion-bounds defects (todo 5, soc-rtl-review-remediation).

These tests drive the REAL compiled firmware (``firmware/build/npu_firmware.hex``)
inside ``RISCVMini`` + ``MMIOBridge`` — the same harness as
``test_firmware_boot_sequence.py`` — so the semantics under test are those of
``firmware/npu_firmware.c``, not the deprecated Python ``NPUFirmware``
dispatch model:

(a) **Address allowlist** — ``dram_range_ok()`` (npu_firmware.c:458) returns
    1 for ANY address below DRAM_BASE (it only checks an upper bound), so
    boot-ROM (0x0000_0000), address-hole (0x1000_0000) and MMIO
    (0x4000_xxxx) descriptors are all ACCEPTED and executed.  They must be
    rejected with status=1.  → RED today (observed status=0 / execution
    attempted).

(b) **Actual-size validation** — ``dispatch_cmd()`` (npu_firmware.c:466)
    validates only the DECLARED input_size/weight_size/output_size against
    the 8 MB DRAM window; it never compares the bytes implied by M/K/N and
    the tile math (npu_firmware.c:492-494, 513-514, 533-534) with those
    declared sizes.  An MMUL whose declared sizes are 4 bytes while the tile
    loop reads ceil(K/64)*4096 activation bytes, 2048 weight bytes/tile and
    writes M*N*4 output bytes executes and completes with status=0.  It must
    be rejected.  → RED today.

(c) **Completion-status bounds** — ``write_completion()``
    (npu_firmware.c:440-446) indexes ``NPU_DB->COMPLETION_STATUS[cmd_id]``
    (declared ``[16]`` in npu-regmap.h:186) with the raw ring head, which
    ranges over 0..RING_ENTRIES-1 (0..1023).  At cmd_id=1019 the write lands
    at 0x4000_5014 + 4*1019 = 0x4000_6000 — the INTC APB window (INTC.PENDING),
    i.e. out of the declared 16-entry array.  The mirror index must stay
    within [16].  → RED today (INTC.PENDING clobbered, mirror writes up to
    index 1018).

All three are expected to FAIL (RED) against current firmware semantics.
A fourth CONTROL test pins the near-end of the 8 MB DRAM window at the
SOURCE level: the upper-bound rejection that already works
(npu_firmware.c:461-463) must not regress when the allowlist is fixed
(todo 8).  A runtime near-end test is NOT exercisable in this harness —
see the RISCVMini note below.

Harness notes (see .omo/notepads/fm-soc-datapath-hardening/learnings.md):
  - ``FuncModel(sram_kb=4096)`` is required: the firmware's SFU scratch
    buffers live at SRAM+0x80000 (0x20080000), beyond the default 512 KB
    SRAM model.
  - ``RISCVMini`` decodes the RV32M ``mul`` instruction as ``sub``, which
    corrupts runtime-multiplied copy-back addresses; numeric golden output
    is therefore never asserted here — only dispatch/completion status.
  - ``RISCVMini`` also lacks ``sltu``/``sltiu`` decode (its supported
    instruction list, miniv.py:4, omits them): the firmware's unsigned
    DRAM-window comparisons compile to ``sltu`` and therefore do NOT execute
    faithfully in the FM.  This does not affect the three RED tests — (a)
    rests on the source-level unconditional ``addr < DRAM_BASE → return 1``
    acceptance (npu_firmware.c:459-460), (b) on the ABSENCE of any
    derived-size check, (c) on raw-head mirror indexing — but it makes the
    near-end upper-bound path untestable at runtime here (todo 8 should
    re-verify near-end on the Spike path).
  - In the FM bridge, ``_to_crossbar_addr`` aliases 0x0000_0000 → SRAM, so
    the boot-ROM subcase (a) executes in the FM (status=0); the hole/MMIO
    subcases raise the crossbar's DECERR ValueError mid-execution, which the
    drain helper captures as ``EXECUTION_ATTEMPTED``.
"""

import os
import struct

import pytest

from func_model import FuncModel
from regmap import Addr, DOORBELL, INTC

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_HEX_PATH = os.path.join(_REPO_ROOT, "firmware", "build", "npu_firmware.hex")

# COMPLETION_RING_ADDR (0x80008000, gen/npu_abi_firmware.h) as a raw DRAM
# bytearray offset (DRAM_BASE = 0x80000000).
_COMP_RING_OFF = 0x8000

# Firmware tile constants (npu_firmware.c:490-493).
_TILE_H = 64
_TILE_W = 64
_TILE_WEIGHT_BYTES = _TILE_H * _TILE_W // 2  # 2048

# Engine opcodes in the current ring ABI (firmware/npu_firmware.c:475-625).
_OP_MMUL = 0x00
_OP_SFU = 0x01
_OP_UNKNOWN = 0xEE  # not 0 / 0x01 / 0x05 / 0x0F-0x14 / 7 / 9 / 10 / 0x15 / 0x16

_EXECUTION_ATTEMPTED = "EXECUTION_ATTEMPTED"


# ── Helpers ──────────────────────────────────────────────────────────────


def _step_until(emu, cond, cap: int = 200_000):
    """Step the emulator until cond() is true.

    Returns True (condition met), False (cap exhausted) or
    ``EXECUTION_ATTEMPTED`` when a step raises ValueError — the FM bridge's
    crossbar raises DECERR for a transaction to an unmapped address, which
    is the signature of the firmware ACCEPTING a bad address and actually
    issuing the transaction instead of rejecting the command.
    """
    emu.running = True
    for _ in range(cap):
        if cond():
            return True
        try:
            emu.step()
        except ValueError:
            return _EXECUTION_ATTEMPTED
    return False


def _last_status(model: FuncModel) -> int:
    return model.bridge.handle("read", Addr.DOORBELL + DOORBELL.LAST_STATUS, 0)


def _boot_to_main(model: FuncModel) -> None:
    """Step from reset until firmware_main() has run its self-test block.

    firmware_main() writes INTC.ENABLE=0x1FF, probes the completion ring
    with a 0xDEADBEEF write/readback and records 0xAA in LAST_STATUS
    (npu_firmware.c:650-658).  Passing these checks proves the harness is
    executing the real firmware image.
    """
    reached = _step_until(
        model.riscv,
        lambda: (_last_status(model) == 0xAA
                 and model.bridge.handle("read", Addr.INTC + INTC.ENABLE, 0)
                 == 0x1FF),
        cap=50_000,
    )
    assert reached is True, f"step() never reached firmware_main() self-test ({reached})"
    marker = struct.unpack_from("<I", model.dram, _COMP_RING_OFF)[0]
    assert marker == 0xDEADBEEF, (
        f"Expected 0xDEADBEEF completion-ring marker, got 0x{marker:08X}"
    )


def _drain_completion_status(model: FuncModel, expected_head: int,
                             cap: int = 500_000):
    """Drain the ring to expected_head and return the last completion status.

    Returns the firmware completion-ring status word (0=success, 1=rejected),
    or ``EXECUTION_ATTEMPTED`` when the firmware accepted the command and the
    FM bridge crashed (DECERR) while executing it.
    """
    reached = _step_until(
        model.riscv,
        lambda: model.bridge.handle("read", Addr.DOORBELL + DOORBELL.HOST_HEAD, 0)
        == expected_head,
        cap=cap,
    )
    if reached == _EXECUTION_ATTEMPTED:
        return _EXECUTION_ATTEMPTED
    assert reached is True, (
        f"firmware never drained the ring to head {expected_head} ({reached})"
    )
    _, status = struct.unpack_from(
        "<II", model.dram, _COMP_RING_OFF + (expected_head - 1) * 32)
    return status


# ── (a) Address allowlist: addresses below DRAM_BASE must be rejected ──────

_BAD_ADDRS = [
    ("boot ROM", 0x0000_0000),
    ("address hole", 0x1000_0000),
    ("MMIO", 0x4000_0000),
]


@pytest.mark.parametrize("label, bad_addr", _BAD_ADDRS)
def test_dispatch_must_reject_addresses_below_dram_base(label, bad_addr):
    """dram_range_ok() must reject ROM/hole/MMIO addresses → status=1.

    dram_range_ok() (npu_firmware.c:458-464) returns 1 for ANY address below
    DRAM_BASE before consulting the window bounds, so the SFU dispatch
    (npu_firmware.c:558-572) accepts the descriptor and executes it.
    """
    model = FuncModel(sram_kb=4096)
    _boot_to_main(model)

    # SFU (opcode 0x01), dim=64 → io_size = sfu_scratch_size(64) = 512 B > 0
    # (npu_firmware.c:290-293): the address check is exercised with a
    # non-trivial size, so it is not vacuous.
    model.host_write_descriptor(
        0x80000080,
        input_addr=bad_addr, output_addr=0x80030000,
        input_size=64, weight_size=0, output_size=0,
        M=0, K=0, N=0,
    )
    model.host_write_command(_OP_SFU, 0x80000080)

    observed = _drain_completion_status(model, expected_head=1)

    if observed == 0:
        behavior = "accepted the descriptor and executed it (status=0)"
    elif observed == _EXECUTION_ATTEMPTED:
        behavior = ("accepted the descriptor and issued a transaction the FM "
                    "crossbar cannot route (DECERR) instead of rejecting")
    else:
        behavior = f"returned status={observed}"

    assert observed == 1, (
        f"{label} address 0x{bad_addr:08X} is below DRAM_BASE (0x80000000) "
        f"and MUST be rejected with status=1, but the firmware {behavior}"
    )


# ── (b) Actual-size validation: derived bytes must not exceed declared ─────

_MMUL_SHAPES = [
    ("undersized buffer", 4, 8, 8),
    ("maximum dimensions", 64, 64, 64),
]


@pytest.mark.parametrize("label, M, K, N", _MMUL_SHAPES)
def test_dispatch_must_reject_mmul_when_tile_math_exceeds_declared_sizes(
        label, M, K, N):
    """MMUL dispatch must reject when M/K/N imply more bytes than declared.

    The firmware validates only the DECLARED sizes against the DRAM window
    (npu_firmware.c:481-485) and then unconditionally runs the tile loop,
    which moves ceil(K/64)*4096 activation bytes, TILE_WEIGHT_BYTES (2048)
    per (n_tile, k_block) weight tile and M*N*4 output bytes
    (npu_firmware.c:492-494, 513-514, 533-534, 550-553).
    """
    declared_input = declared_weight = declared_output = 4

    num_blocks = (K + _TILE_H - 1) // _TILE_H
    num_tiles = (N + _TILE_W - 1) // _TILE_W
    derived_input = ((K + 63) // 64) * 4096          # tile-major activation
    derived_weight = num_tiles * num_blocks * _TILE_WEIGHT_BYTES
    derived_output = M * N * 4

    # Anti-vacuous precondition: the derived access really exceeds the
    # declared sizes for this shape.
    assert derived_input > declared_input
    assert derived_weight > declared_weight
    assert derived_output > declared_output

    model = FuncModel(sram_kb=4096)
    _boot_to_main(model)

    model.host_write_descriptor(
        0x80000080,
        input_addr=0x80010000, weight_addr=0x80020000, output_addr=0x81000000,
        scale_addr=0, scale_size=0,
        input_size=declared_input, weight_size=declared_weight,
        output_size=declared_output,
        M=M, K=K, N=N,
    )
    model.host_write_command(_OP_MMUL, 0x80000080)

    observed = _drain_completion_status(model, expected_head=1)

    assert observed == 1, (
        f"MMUL ({label}) M={M} K={K} N={N} derives input={derived_input}B, "
        f"weight={derived_weight}B, output={derived_output}B from the tile "
        f"math, but the descriptor declares "
        f"{declared_input}/{declared_weight}/{declared_output}B. Dispatch "
        f"MUST reject with status=1; observed {observed!r} (accepted and "
        f"executed against current firmware semantics)"
    )


# ── (c) Completion-status bounds: mirror index must stay within [16] ───────

def test_completion_status_mirror_must_stay_within_16_entry_array():
    """COMPLETION_STATUS[cmd_id] must never index past the [16] mirror array.

    write_completion() (npu_firmware.c:440-446) writes
    NPU_DB->COMPLETION_STATUS[cmd_id] with cmd_id = ring head.  The array is
    declared [16] at doorbell offset 0x14 (npu-regmap.h:186), but heads run
    0..RING_ENTRIES-1 (0..1023, NPU_ABI_RING_ENTRIES=1024).  cmd_id=1019 maps
    to 0x4000_5014 + 4*1019 = 0x4000_6000 — the INTC APB window (the 4 KB
    page immediately after DOORBELL at 0x4000_5000), clobbering INTC.PENDING.

    The ring must be constructed with ring_size=1024 (>= 1019): the default
    16-entry NPUFirmware ring wraps heads at 0..15 and can never reach
    cmd_id >= 16, which would make this test vacuous.
    """
    ring_size = 1024
    num_cmds = 1020  # dispatch heads 0..1019 → cmd_id 1019 crosses into INTC
    model = FuncModel(sram_kb=4096, ring_size=ring_size)
    _boot_to_main(model)

    # Unknown opcode: the fastest dispatch path (single branch to status=1,
    # npu_firmware.c:626-627) — still exercises write_completion() in full.
    for _ in range(num_cmds):
        model.host_write_command(_OP_UNKNOWN, 0x80000080)

    # Anti-vacuous: the host really queued num_cmds entries on the 1024-entry
    # ring (host_write_command would raise "ring full" at 16 entries on the
    # default ring_size).
    assert model.firmware.doorbell["host_tail"] == num_cmds

    reached = _step_until(
        model.riscv,
        lambda: model.bridge.handle("read", Addr.DOORBELL + DOORBELL.HOST_HEAD, 0)
        == num_cmds,
        cap=2_000_000,
    )
    assert reached is True, f"firmware never drained the ring ({reached})"

    # Anti-vacuous (stale-state guard): the head really reached >= 1019 with
    # no wrap — the assertions below prove the ring_size=1024 construction
    # exercised cmd_id 1019, not a 16-entry wrap.
    npu_head = model.bridge.handle("read", Addr.DOORBELL + DOORBELL.NPU_HEAD, 0)
    assert npu_head == num_cmds == 1020, (
        f"ring head should be 1020 (no wrap) but is {npu_head}"
    )
    cmd_id_1019, status_1019 = struct.unpack_from(
        "<II", model.dram, _COMP_RING_OFF + 1019 * 32)
    assert (cmd_id_1019, status_1019) == (1019, 1), (
        f"completion ring entry 1019 should be (1019, 1), got "
        f"({cmd_id_1019}, {status_1019}) — the firmware never dispatched "
        f"cmd_id 1019"
    )

    # The guard: the mirror index must stay within the declared [16]-entry
    # array.  (1) No mirror write may land in the INTC APB window
    # (0x4000_6000+): the firmware only ever writes INTC.ENABLE (boot) and
    # INTC.ACK, so INTC.PENDING must remain 0.  (2) No mirror write may
    # exceed the last array slot (0x4000_5014 + 15*4 = 0x4000_5050).
    pending = model.bridge._status.get(INTC.BASE + INTC.PENDING, 0)
    assert pending == 0, (
        f"COMPLETION_STATUS[cmd_id=1019] spilled out of the [16]-entry mirror "
        f"and clobbered INTC.PENDING at 0x{INTC.BASE + INTC.PENDING:08X} "
        f"(=0x{pending:X}) — the completion mirror wrote into the INTC APB "
        f"window at 0x4000_6000"
    )
    mirror_keys = [
        k for k in model.bridge._status
        if (Addr.DOORBELL + DOORBELL.COMPLETION_STATUS
            <= k < Addr.DOORBELL + 0x1000)
    ]
    max_mirror_key = max(mirror_keys, default=0)
    assert max_mirror_key <= Addr.DOORBELL + DOORBELL.COMPLETION_STATUS + 15 * 4, (
        f"completion mirror wrote up to 0x{max_mirror_key:08X} (index "
        f"{(max_mirror_key - Addr.DOORBELL - DOORBELL.COMPLETION_STATUS) // 4}), "
        f"beyond the [16]-entry array ending at 0x"
        f"{Addr.DOORBELL + DOORBELL.COMPLETION_STATUS + 15 * 4:08X}"
    )


# ── CONTROL: the existing upper-bound rejection must not regress ──────────

def test_control_near_end_dram_window_upper_bound_pinned():
    """CONTROL (expected PASS today and after the allowlist fix).

    An address INSIDE the 8 MB DRAM window whose size crosses DRAM_END, an
    address at/above DRAM_END, or a size above DRAM_SIZE must keep being
    rejected (dram_range_ok → 0, npu_firmware.c:461-463).

    This is pinned at the SOURCE level because RISCVMini does not decode
    ``sltu``/``sltiu`` (miniv.py supported-instruction list), so the
    firmware's unsigned window comparisons do not execute faithfully in the
    FM — a runtime near-end probe would report a harness artifact, not
    firmware behaviour.  The pin asserts the boundary expressions and the
    8 MB window constant survive any todo-8 allowlist rewrite; runtime
    near-end re-verification belongs on the Spike path (todo 8).
    """
    src_path = os.path.join(_REPO_ROOT, "firmware", "npu_firmware.c")
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()

    # dram_range_ok() body must contain the DRAM-window boundary constants.
    assert "DRAM_END" in src and "DRAM_SIZE" in src and "DRAM_BASE" in src, (
        "firmware/npu_firmware.c: dram_range_ok() boundary constants missing"
    )
    # The upper-bound rejection must reference the 8 MB window constant
    # (npu_firmware.c:19) — the todo-8 allowlist rewrite must not delete it.
    assert "#define DRAM_SIZE  0x00800000UL" in src, (
        "firmware/npu_firmware.c: 8 MB DRAM window constant removed or changed"
    )
    # The rejection branch: addr >= DRAM_END or size > DRAM_SIZE → 0.
    assert "addr >= DRAM_END" in src and "size > DRAM_SIZE" in src, (
        "firmware/npu_firmware.c: dram_range_ok() upper-bound rejection "
        "expressions missing"
    )
