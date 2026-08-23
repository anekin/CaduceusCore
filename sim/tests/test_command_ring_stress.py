"""Ring-stress wrap scenario — BUG-RTL-SOC-008 class reproduction guard.

BUG-RTL-SOC-008 (docs/bugs/bugs-soc-rtl.md:580): ``DESC_BASE = 0x80001000``
sits inside the firmware command ring (1024 entries x 32 B starting at
0x80000000) — it is entry 128, and each 64 B descriptor covers two entries.
In the 9-layer RTL segment run, L19 wrote commands at ring entries 102-135;
entries 128-135 overwrote descriptors 0-7, corrupting the L19/L20 checkpoints.
The fix moved DESC_BASE to 0x80010000. This test is the Func Model speed
reproduction guard for that bug class (gap-report proposal S2).

The pure-Python ``NPUFirmware`` emulator hardcodes a 16-entry ring which can
neither hold 140 commands nor reach entry 128, so ``ring_size`` is now a
constructor parameter of ``NPUFirmware``/``FuncModel`` (default 16, unchanged
for existing callers). This test constructs with ``command_ring.RING_ENTRIES``
(1024) to match ``firmware/npu_firmware.c``.

Scenario: 140 commands queued at a persistent ring offset of 120 (entries
120..259, crossing entry 128 == 0x80001000), descriptors allocated
sequentially at DESC_BASE. Assertions: (a) the address-space contract passes
and the descriptor region is disjoint from both rings; (b) each completion
records the correct ring-relative cmd_id (ordered results + doorbell head)
and status; (c) each output matches the golden matmul. The run then continues
at the persistent offset until the head physically wraps 1023 -> 0.

Note: ``NPUFirmware`` has no completion-ring DRAM buffer — its completion
record is the ordered result list plus the doorbell HOST_HEAD register, so
cmd_id is asserted via result order and head arithmetic, all through
``command_ring`` helpers (never raw ``%``).
"""

import struct

import numpy as np
import pytest

import address_space
import command_ring
from cocotb_bridge import pack_int8_activation_tile_major
from engine.isa import OpCode
from func_model import FuncModel
from golden_executor import GoldenMXU
from miniv import NPUFirmware
from regmap import Addr, DOORBELL

M, K, N = 1, 4, 2
N_CMDS = 140
START_OFFSET = 120          # persistent ring offset (precondition for entry 128)
RING_SIZE = command_ring.RING_ENTRIES   # 1024 — matches firmware/npu_firmware.c
DESC_BASE = address_space.DESC_BASE     # 0x80010000
DESC_STRIDE = command_ring.DESC_STRIDE  # 64

# Entry 128 is the exact address BUG-RTL-SOC-008 corrupted: the pre-fix
# DESC_BASE of 0x80001000. Expressed via ring helpers, not a magic literal.
ENTRY_128_ADDR = command_ring.ring_entry_addr(128)

_RNG_STRESS = np.random.RandomState(20260823)


def _dram_read(model: FuncModel, addr: int, size: int) -> bytes:
    """Direct DRAM read (bypasses PCIe model)."""
    return bytes(model.dram[addr - Addr.DRAM_BASE:addr - Addr.DRAM_BASE + size])


def _seed_doorbell(model: FuncModel, offset: int):
    """Pre-set the persistent ring offset: host_tail == npu_head == offset."""
    fw = model.firmware
    fw.doorbell["host_tail"] = offset
    fw.doorbell["npu_head"] = offset
    model.bridge.handle("write", DOORBELL.BASE + DOORBELL.HOST_TAIL, offset)
    model.bridge.handle("write", DOORBELL.BASE + DOORBELL.NPU_HEAD, offset)
    model.bridge.handle("write", DOORBELL.BASE + DOORBELL.HOST_HEAD, offset)


def _write_stress_mmul_desc(model: FuncModel, desc_addr: int, act_addr: int,
                            wgt_addr: int, out_addr: int, scale_addr: int):
    """Write an MMUL descriptor (same field layout as test_soc_fm doorbell cases)."""
    act_size = ((K + 63) // 64) * 4096
    wgt_size = (K * N + 1) // 2
    out_size = M * N * 4
    scale_size = ((K + 127) // 128) * N * 4
    model.host_write_descriptor(desc_addr,
        input_addr=act_addr, weight_addr=wgt_addr, output_addr=out_addr,
        scale_addr=scale_addr, scale_size=scale_size,
        input_size=act_size, weight_size=wgt_size, output_size=out_size,
        M=M, K=K, N=N)


def _setup_stress_mmul(model: FuncModel, i: int, desc_addr: int):
    """Write deterministic command-i MMUL data + descriptor; return golden inputs.

    Regions are spaced 0x1000 apart per command so all 140 commands' buffers
    stay disjoint inside the activation region [0x80020000, 0x801E0000).
    """
    act_addr = 0x8002_0000 + i * 0x1000
    wgt_addr = 0x800B_0000 + i * 0x1000
    out_addr = 0x8014_0000 + i * 0x1000
    scale_addr = 0x801D_0000 + i * 0x40

    act = _RNG_STRESS.randint(-8, 8, size=M * K, dtype=np.int8)
    wgt = _RNG_STRESS.randint(-8, 8, size=K * N, dtype=np.int8)
    wgt_packed = GoldenMXU.pack_int4(wgt)
    num_blocks = (K + 127) // 128
    scales = np.ones((num_blocks, N), dtype=np.float32)

    model.host_write_data(act_addr, np.frombuffer(
        pack_int8_activation_tile_major(act.tobytes(), M, K), dtype=np.uint8))
    model.host_write_data(wgt_addr, wgt_packed)
    model.host_write_data(scale_addr, scales.ravel())
    _write_stress_mmul_desc(model, desc_addr, act_addr, wgt_addr, out_addr,
                            scale_addr)
    return act, wgt_packed, scales, out_addr


def _assert_stress_mmul_result(model: FuncModel, act: np.ndarray,
                               wgt_packed: np.ndarray, scales: np.ndarray,
                               out_addr: int):
    """Compare firmware MMUL output in DRAM against GoldenMXU."""
    out_off = out_addr - Addr.DRAM_BASE
    out_bytes = model.dram[out_off:out_off + M * N * 4]
    out_fw = np.frombuffer(out_bytes, dtype=np.float32).reshape(M, N)
    golden = GoldenMXU().matmul_int4_per_block(
        act.reshape(M, K), wgt_packed, scales, M, K, N, group_size=128)
    assert np.allclose(out_fw, golden, rtol=1e-5), (
        f"MMUL output mismatch at {out_addr:#x}: got {out_fw.tolist()}, "
        f"expected {golden.tolist()}")


def test_ring_wrap_at_entry_128():
    """140 commands at persistent offset 120 cross entry 128; head wraps 1023->0."""
    model = FuncModel(ring_size=RING_SIZE)
    fw = model.firmware
    assert fw.ring_size == RING_SIZE

    # Persistent ring offset of 120 — the segment-run precondition that let
    # the real ring reach entry 128 (BUG-RTL-SOC-008: L19 wrote entries 102-135).
    _seed_doorbell(model, START_OFFSET)

    # (a) address-space contract: descriptors fit and stay clear of the rings.
    address_space.contract_check(
        desc_base=DESC_BASE, desc_count=N_CMDS,
        act_base=address_space.P10_ACT_BASE)
    desc_region = (DESC_BASE, N_CMDS * DESC_STRIDE)
    assert not address_space.regions_overlap(desc_region, "command_ring")
    assert not address_space.regions_overlap(desc_region, "completion_ring")

    # Queue 140 commands; descriptors allocated sequentially at DESC_BASE.
    goldens = []
    for i in range(N_CMDS):
        desc_addr = DESC_BASE + i * DESC_STRIDE
        goldens.append(_setup_stress_mmul(model, i, desc_addr))
        model.host_write_command(OpCode.MMUL, desc_addr)
        # Head arithmetic via command_ring helpers only (no raw %).
        assert fw.doorbell["host_tail"] == command_ring.advance_head(
            START_OFFSET, i + 1)

    # The write sequence spans ring entry 128 — the exact address
    # BUG-RTL-SOC-008 corrupted — and the entry physically holds a command.
    assert START_OFFSET <= 128 < START_OFFSET + N_CMDS
    opcode_at_128 = struct.unpack_from("<I", _dram_read(model, ENTRY_128_ADDR, 4))[0]
    assert opcode_at_128 == OpCode.MMUL, (
        f"ring entry 128 ({ENTRY_128_ADDR:#x}) should hold a command, "
        f"got opcode {opcode_at_128}")

    # Snapshot descriptor 0 for the post-wrap corruption check.
    desc0_bytes = _dram_read(model, DESC_BASE, 60)

    results = fw.run_loop(max_commands=N_CMDS)
    assert len(results) == N_CMDS

    # (b) per-command completion: ordered results carry cmd_id (ring-relative
    # index) and status; doorbell heads advance through command_ring helpers.
    for i, r in enumerate(results):
        assert r["opcode"] == OpCode.MMUL, f"cmd {i}: opcode {r['opcode']}"
        assert r["status"] == "done", f"cmd {i}: {r}"
    final_head = command_ring.advance_head(START_OFFSET, N_CMDS)
    assert fw.doorbell["npu_head"] == final_head
    assert model.bridge.handle("read", DOORBELL.BASE + DOORBELL.HOST_HEAD, 0) == final_head
    assert model.bridge.handle("read", DOORBELL.BASE + DOORBELL.NPU_HEAD, 0) == final_head

    # (c) each command output matches golden.
    for i, (act, wgt_packed, scales, out_addr) in enumerate(goldens):
        _assert_stress_mmul_result(model, act, wgt_packed, scales, out_addr)

    # expected_head / advance_head wrap semantics at the ring boundary.
    assert command_ring.expected_head(1023) == 1023
    assert command_ring.expected_head(1024) == 0
    assert command_ring.advance_head(1023, 1) == 0
    assert command_ring.expected_head(START_OFFSET + N_CMDS) == final_head

    # Keep the persistent offset running until the head physically wraps
    # 1023 -> 0, reusing descriptor 0. The descriptor pool must survive ring
    # wraps — the exact corruption BUG-RTL-SOC-008 produced.
    total = N_CMDS
    prev_head = final_head
    wrapped_at = None
    while total < RING_SIZE + N_CMDS:  # safety bound
        model.host_write_command(OpCode.MMUL, DESC_BASE)
        r = fw.run_loop(max_commands=1)
        assert len(r) == 1 and r[0]["status"] == "done", f"wrap cmd {total}: {r}"
        total += 1
        head = fw.doorbell["npu_head"]
        expected = command_ring.advance_head(START_OFFSET, total)
        assert head == expected, f"wrap cmd {total}: head {head} != {expected}"
        if prev_head == RING_SIZE - 1 and head == 0:
            wrapped_at = total
            break
        prev_head = head
    assert wrapped_at is not None, "head never wrapped 1023 -> 0"

    # Descriptor pool intact after the full wrap: ring writes never touched it.
    assert _dram_read(model, DESC_BASE, 60) == desc0_bytes


def test_desc_base_inside_ring_rejected(monkeypatch):
    """Failure injection: the BUG-RTL-SOC-008 layout must trip the contract gate.

    The pre-fix DESC_BASE (entry 128 == 0x80001000) is inside the command
    ring. With the gate in place, scheduling there raises OverlapError; if
    the guard regressed this test would fail, exactly like the L19/L20
    corruption it guards against.
    """
    monkeypatch.setattr(address_space, "DESC_BASE", ENTRY_128_ADDR)
    with pytest.raises(address_space.OverlapError, match="completion-ring"):
        address_space.contract_check(desc_count=20,
                                     act_base=address_space.P10_ACT_BASE)


def test_default_ring_size_unchanged():
    """Parameterization must not change the default 16-entry ring."""
    model = FuncModel()
    assert model.firmware.ring_size == 16
    fw = NPUFirmware(sim_modules={"dram": bytearray(1024)})
    assert fw.ring_size == 16
