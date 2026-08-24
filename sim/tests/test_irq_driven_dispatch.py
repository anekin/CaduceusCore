"""IRQ-driven firmware dispatch FM guard (FW-10).

Todo 6 of fm-soc-datapath-hardening: verify that in a multi-command
stream, the completion of op N is driven by the INTC IRQ signal — not by
STATUS polling — before the firmware dispatches op N+1, and that
suppressing the IRQ stalls the firmware instead of falling back to
polling.

Mechanism under test (``sim/miniv.py``, unchanged):
  - ``NPUFirmware._wait_done`` has two branches.  When a ``RISCVMini``
    is bound, completion is IRQ-driven: it spins on ``_irq_serviced``,
    which is only set by ``dispatch_interrupt`` (the RISC-V trap
    handler).  The STATUS-polling branch is reachable only when no
    RISC-V is bound.
  - Engine CMD writes raise INTC IRQs through ``MMIOBridge._set_irq``,
    whose ENABLE/THRESHOLD gating comes from Todo 2.  Tests program
    ENABLE/THRESHOLD explicitly so gating semantics are deterministic.

Guards:
  1. Happy: 3-command stream (MMUL -> SOFTMAX -> VADD) all complete;
     every engine completion went through the IRQ trap handler and zero
     engine-STATUS reads occurred (no polling fallback while bound).
  2. Contrast: bound firmware never reads STATUS; after unbinding, the
     same command exercises the polling fallback (anti-vacuous spy).
  3. Failure injection (monkeypatch): suppressing the cpu_irq notify
     stalls the firmware after the first command even though the engine
     is DONE; restoring IRQ delivery drains the stream.
  4. Failure injection (Todo 2 gating): explicit INTC ENABLE=0 produces
     the same stall through the real gate, proving the firmware
     dispatch really depends on the INTC ENABLE/THRESHOLD path.
"""

import threading
import time

import numpy as np

from cocotb_bridge import pack_int8_activation_tile_major
from engine.isa import OpCode
from func_model import FuncModel
from golden_executor import GoldenMXU, GoldenSFU, GoldenVector
from regmap import Addr, DOORBELL, DMA, INTC, MXU, SFU, VECTOR

_RNG = np.random.RandomState(20260824)

_STATUS_ADDRS = {
    MXU.BASE + MXU.STATUS,
    SFU.BASE + SFU.STATUS,
    VECTOR.BASE + VECTOR.STATUS,
    DMA.BASE + DMA.STATUS,
}


# ══════════════════════════════════════════════════════════════════════
# Instrumentation helpers
# ══════════════════════════════════════════════════════════════════════


def _program_intc(bridge, enable: int = 0x1FF, threshold: int = 1):
    """Explicitly program INTC ENABLE/THRESHOLD (deterministic gating)."""
    bridge.handle("write", INTC.BASE + INTC.ENABLE, enable)
    bridge.handle("write", INTC.BASE + INTC.THRESHOLD, threshold)


def _status_read_spy(model: FuncModel) -> list:
    """Record every bridge read of an engine STATUS register.

    Wraps both ``MMIOBridge.handle`` and ``RISCVMini.mmio_callback`` so
    STATUS reads made by either the firmware or the CPU are detected.
    """
    polled = []
    orig_handle = model.bridge.handle

    def spy_handle(rw, addr, value=0):
        if rw == "read" and addr in _STATUS_ADDRS:
            polled.append(addr)
        return orig_handle(rw, addr, value)

    model.bridge.handle = spy_handle

    orig_cb = model.riscv.mmio_callback

    def spy_cb(rw, addr, value=0):
        if rw == "read" and addr in _STATUS_ADDRS:
            polled.append(addr)
        return orig_cb(rw, addr, value)

    model.riscv.mmio_callback = spy_cb
    return polled


def _irq_serviced_spy(model: FuncModel) -> list:
    """Record every source bit dispatched by the RISC-V trap handler."""
    serviced = []
    orig_handler = model.riscv.irq_handler

    def spy(source_bit):
        serviced.append(source_bit)
        orig_handler(source_bit)

    model.riscv.irq_handler = spy
    return serviced


def _dispatch_spy(model: FuncModel) -> list:
    """Record every opcode entering ``NPUFirmware._dispatch``."""
    calls = []
    orig_dispatch = model.firmware._dispatch

    def spy(cmd):
        calls.append(cmd["opcode"])
        return orig_dispatch(cmd)

    model.firmware._dispatch = spy
    return calls


def _run_loop_async(model: FuncModel, ncmds: int):
    """Run firmware.run_loop in a daemon thread; return (thread, result box)."""
    box = {}

    def worker():
        box["results"] = model.firmware.run_loop(max_commands=ncmds)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t, box


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ══════════════════════════════════════════════════════════════════════
# Command setup / verification (patterns from test_soc_fm.py doorbell tests)
# ══════════════════════════════════════════════════════════════════════


def _setup_mmul(model, M, K, N, act_addr, wgt_addr, out_addr, scale_addr, desc_addr):
    act = _RNG.randint(-8, 8, size=M * K, dtype=np.int8)
    wgt = _RNG.randint(-8, 8, size=K * N, dtype=np.int8)
    wgt_packed = GoldenMXU.pack_int4(wgt)
    num_blocks = (K + 127) // 128
    scales = np.ones((num_blocks, N), dtype=np.float32)
    model.host_write_data(act_addr, np.frombuffer(
        pack_int8_activation_tile_major(act.tobytes(), M, K), dtype=np.uint8))
    model.host_write_data(wgt_addr, wgt_packed)
    model.host_write_data(scale_addr, scales.ravel())
    model.host_write_descriptor(desc_addr,
        input_addr=act_addr, weight_addr=wgt_addr, output_addr=out_addr,
        scale_addr=scale_addr, scale_size=num_blocks * N * 4,
        input_size=((K + 63) // 64) * 4096,
        weight_size=(K * N + 1) // 2,
        output_size=M * N * 4,
        M=M, K=K, N=N)
    return act, wgt_packed, scales


def _setup_softmax(model, length, in_addr, out_addr, desc_addr):
    data = _RNG.randn(length).astype(np.float32).clip(-5, 5)
    model.host_write_data(in_addr, data.astype(np.float16))
    model.host_write_descriptor(desc_addr,
        input_addr=in_addr, output_addr=out_addr,
        input_size=length, output_size=length,
        M=1, K=length, N=1)
    return data


def _setup_vadd(model, length, a_addr, b_addr, out_addr, desc_addr):
    a = _RNG.randint(-100, 100, size=length).astype(np.int32)
    b = _RNG.randint(-100, 100, size=length).astype(np.int32)
    model.host_write_data(a_addr, a)
    model.host_write_data(b_addr, b)
    model.host_write_descriptor(desc_addr,
        input_addr=a_addr, weight_addr=b_addr, output_addr=out_addr,
        input_size=length, weight_size=length, output_size=length,
        M=1, K=length, N=1)
    return a, b


def _assert_mmul_result(model, act, wgt_packed, scales, out_addr, M, K, N):
    out_off = out_addr - Addr.DRAM_BASE
    out = np.frombuffer(
        model.dram[out_off:out_off + M * N * 4], dtype=np.float32).reshape(M, N)
    golden = GoldenMXU().matmul_int4_per_block(
        act.reshape(M, K), wgt_packed, scales, M, K, N, group_size=128)
    assert np.allclose(out, golden, rtol=1e-5), (
        f"MMUL output mismatch: got {out.tolist()}, expected {golden.tolist()}"
    )


def _assert_softmax_result(model, data, out_addr, length):
    out_off = out_addr - Addr.DRAM_BASE
    out = np.frombuffer(
        model.dram[out_off:out_off + length * 2],
        dtype=np.float16).astype(np.float32)
    ref = GoldenSFU().softmax_hw(data)
    cmp = GoldenSFU.compare_hw_vs_ref(out, ref, tol_abs=2e-3, tol_rel=1e-2)
    assert cmp["within_tolerance"], (
        f"Softmax mismatch: max_abs={cmp['max_abs_err']:.2e}"
    )


def _assert_vadd_result(model, a, b, out_addr, length):
    out_off = out_addr - Addr.DRAM_BASE
    out = np.frombuffer(
        model.dram[out_off:out_off + length * 4], dtype=np.int32)
    ref = GoldenVector().add(a, b)
    assert np.array_equal(out, ref), "Vector ADD mismatch"


def _queue_softmax_vadd_softmax(model):
    """Queue a 3-command SOFTMAX -> VADD -> SOFTMAX stream."""
    _setup_softmax(model, 16, 0x8200_0000, 0x8200_1000, 0x8000_0080)
    model.host_write_command(OpCode.SOFTMAX, 0x8000_0080)
    _setup_vadd(model, 8, 0x8200_2000, 0x8200_3000, 0x8200_4000, 0x8000_00C0)
    model.host_write_command(OpCode.VADD, 0x8000_00C0)
    _setup_softmax(model, 16, 0x8200_5000, 0x8200_6000, 0x8000_0100)
    model.host_write_command(OpCode.SOFTMAX, 0x8000_0100)
    assert model.firmware.doorbell["host_tail"] == 3


def _assert_stream_stalled_after_first_command(model, dispatch_calls, serviced):
    """Firmware must be stuck after command 1, engine done but no poll."""
    assert dispatch_calls == [OpCode.SOFTMAX], (
        f"firmware advanced past command 1: {dispatch_calls}"
    )
    assert model.firmware.doorbell["npu_head"] == 1, (
        "firmware must consume exactly one command before stalling"
    )
    assert model.bridge.handle(
        "read", DOORBELL.BASE + DOORBELL.HOST_HEAD, 0) == 0, (
        "HOST_HEAD advanced: command 1 completion was signaled while stalled"
    )
    # Engine finished (STATUS.BUSY=0) yet firmware waits: if _wait_done
    # polled STATUS while riscv is bound, the command would have
    # completed and this assertion would fail.
    assert model.bridge.handle("read", SFU.BASE + SFU.STATUS, 0) & 1 == 0, (
        "SFU engine not done; stall assertion is vacuous"
    )
    pending = model.bridge.handle("read", INTC.BASE + INTC.PENDING, 0)
    assert pending & (1 << 1), (
        f"SFU IRQ must be pending at INTC, got 0x{pending:08X}"
    )
    assert not serviced, (
        f"trap handler ran while IRQ was suppressed: {serviced}"
    )


def _assert_stream_drained(model, box, serviced):
    """After IRQ restore the whole stream completes via IRQ traps."""
    results = box["results"]
    assert len(results) == 3, f"expected 3 results, got {len(results)}"
    assert all(r["status"] == "done" for r in results), results
    assert {1, 2} <= set(serviced), (
        f"SFU/VECTOR completions must be IRQ-dispatched, got {serviced}"
    )
    assert model.firmware.doorbell["npu_head"] == 3
    assert model.bridge.handle("read", DOORBELL.BASE + DOORBELL.HOST_HEAD, 0) == 3
    assert model.bridge.handle("read", INTC.BASE + INTC.PENDING, 0) == 0, (
        "INTC.PENDING must be 0 after IRQ-driven drain"
    )


# ══════════════════════════════════════════════════════════════════════
# Happy path — 3-command stream driven to completion by IRQ
# ══════════════════════════════════════════════════════════════════════


def test_three_command_stream_all_irq_driven():
    """Happy: MMUL -> SOFTMAX -> VADD all complete via IRQ, zero polling.

    Each engine completion must go through the RISC-V trap handler
    (dispatch_interrupt with the engine's INTC source bit), and no
    engine STATUS register may be read by bridge or CPU while the
    firmware is RISC-V-bound — completion is IRQ-driven, not polled.
    """
    model = FuncModel()
    _program_intc(model.bridge, enable=0x1FF, threshold=1)
    polled = _status_read_spy(model)
    serviced = _irq_serviced_spy(model)

    M, K, N = 1, 4, 2
    act, wgt_packed, scales = _setup_mmul(
        model, M, K, N,
        0x8001_0000, 0x8002_0000, 0x8100_0000, 0x8011_0000, 0x8000_0080)
    model.host_write_command(OpCode.MMUL, 0x8000_0080)

    sfu_len = 16
    sfu_data = _setup_softmax(model, sfu_len, 0x8200_0000, 0x8200_1000, 0x8000_0100)
    model.host_write_command(OpCode.SOFTMAX, 0x8000_0100)

    vec_len = 8
    vec_a, vec_b = _setup_vadd(
        model, vec_len, 0x8200_2000, 0x8200_3000, 0x8200_4000, 0x8000_0200)
    model.host_write_command(OpCode.VADD, 0x8000_0200)

    assert model.firmware.doorbell["host_tail"] == 3

    results = model.firmware.run_loop(max_commands=3)

    assert len(results) == 3, f"expected 3 results, got {len(results)}"
    assert all(r["status"] == "done" for r in results), results

    # MMUL completes via MXU (bit 0) + DMA (bit 3) IRQs, SOFTMAX via
    # SFU (bit 1), VADD via VECTOR (bit 2), doorbell via bit 8.
    assert {0, 1, 2, 3, 8} <= set(serviced), (
        f"engine completions not all IRQ-dispatched: {serviced}"
    )
    assert polled == [], (
        f"STATUS polling detected while riscv is bound: {polled}"
    )

    _assert_mmul_result(model, act, wgt_packed, scales, 0x8100_0000, M, K, N)
    _assert_softmax_result(model, sfu_data, 0x8200_1000, sfu_len)
    _assert_vadd_result(model, vec_a, vec_b, 0x8200_4000, vec_len)

    assert model.firmware.doorbell["npu_head"] == 3
    assert model.bridge.handle("read", DOORBELL.BASE + DOORBELL.HOST_HEAD, 0) == 3
    assert model.bridge.handle("read", INTC.BASE + INTC.PENDING, 0) == 0


# ══════════════════════════════════════════════════════════════════════
# No polling fallback while RISC-V is bound
# ══════════════════════════════════════════════════════════════════════


def test_completion_via_irq_not_status_polling():
    """Bound: completion via IRQ trap, zero STATUS reads.

    Unbinding the RISC-V makes the same command exercise the polling
    fallback, so the STATUS-read spy is proven non-vacuous: the polling
    path exists but is unreachable while riscv is bound.
    """
    model = FuncModel()
    _program_intc(model.bridge, enable=0x1FF, threshold=1)
    polled = _status_read_spy(model)
    serviced = _irq_serviced_spy(model)

    data = _setup_softmax(model, 16, 0x8200_0000, 0x8200_1000, 0x8000_0080)
    model.host_write_command(OpCode.SOFTMAX, 0x8000_0080)

    results = model.firmware.run_loop(max_commands=1)
    assert len(results) == 1 and results[0]["status"] == "done"
    assert polled == [], (
        f"riscv-bound dispatch must not read engine STATUS: {polled}"
    )
    assert 1 in serviced, (
        f"SFU completion must be dispatched via IRQ trap, got {serviced}"
    )
    _assert_softmax_result(model, data, 0x8200_1000, 16)

    # Unbind → the polling fallback becomes reachable and observable.
    model.firmware.riscv = None
    data2 = _setup_softmax(model, 16, 0x8200_2000, 0x8200_3000, 0x8000_00C0)
    model.host_write_command(OpCode.SOFTMAX, 0x8000_00C0)

    results2 = model.firmware.run_loop(max_commands=1)
    assert len(results2) == 1 and results2[0]["status"] == "done"
    assert polled, "unbound dispatch must poll engine STATUS (spy sanity)"
    assert all(addr == SFU.BASE + SFU.STATUS for addr in polled), (
        f"expected only SFU STATUS reads, got {polled}"
    )
    _assert_softmax_result(model, data2, 0x8200_3000, 16)


# ══════════════════════════════════════════════════════════════════════
# Failure injection — suppressed IRQ stalls the firmware
# ══════════════════════════════════════════════════════════════════════


def test_failure_suppressed_irq_stalls_after_first_command():
    """Failure injection: suppress cpu_irq notify → stall after command 1.

    Monkeypatches ``MMIOBridge.irq_notify_callback`` so engine IRQs
    accumulate in INTC.PENDING but never reach the CPU.  The firmware
    consumes command 1 and spins in ``_wait_done``: the engine is DONE
    yet no command completes — proving there is no STATUS-polling
    fallback while riscv is bound.  Restoring IRQ delivery drains the
    remaining commands.
    """
    model = FuncModel()
    _program_intc(model.bridge, enable=0x1FF, threshold=1)

    gate = {"suppress": True, "notified": 0}
    orig_notify = model.bridge.irq_notify_callback

    def gated_notify():
        if gate["suppress"]:
            gate["notified"] += 1
            return
        orig_notify()

    model.bridge.irq_notify_callback = gated_notify

    serviced = _irq_serviced_spy(model)
    dispatch_calls = _dispatch_spy(model)
    _queue_softmax_vadd_softmax(model)

    t, box = _run_loop_async(model, 3)
    assert _wait_until(lambda: len(dispatch_calls) == 1), (
        "firmware never dispatched command 1"
    )
    time.sleep(0.2)  # grace: firmware must stay stalled
    _assert_stream_stalled_after_first_command(model, dispatch_calls, serviced)
    assert gate["notified"] >= 4, (
        f"expected 3 host + 1 engine suppressed notifies, got {gate['notified']}"
    )

    # Restore IRQ delivery and wake the CPU: the stream must drain.
    gate["suppress"] = False
    model.riscv.interrupt_pending = True
    t.join(timeout=15)
    assert not t.is_alive(), "firmware did not resume after IRQ restore"
    _assert_stream_drained(model, box, serviced)


def test_failure_intc_enable_zero_blocks_engine_irq_stalls_firmware():
    """Failure injection via Todo 2 gating: INTC ENABLE=0 → same stall.

    With ENABLE explicitly programmed to 0, ``_set_irq`` accumulates
    PENDING but the cpu_irq notify never fires — the real
    ENABLE/THRESHOLD gate, not a monkeypatch, suppresses the IRQ.  The
    firmware stalls after the first command with the engine DONE;
    reprogramming ENABLE and waking the CPU drains the stream.
    """
    model = FuncModel()
    _program_intc(model.bridge, enable=0, threshold=1)

    notified = []
    orig_notify = model.bridge.irq_notify_callback

    def counting_notify():
        notified.append(1)
        orig_notify()

    model.bridge.irq_notify_callback = counting_notify

    serviced = _irq_serviced_spy(model)
    dispatch_calls = _dispatch_spy(model)
    _queue_softmax_vadd_softmax(model)

    t, box = _run_loop_async(model, 3)
    assert _wait_until(lambda: len(dispatch_calls) == 1), (
        "firmware never dispatched command 1"
    )
    time.sleep(0.2)
    _assert_stream_stalled_after_first_command(model, dispatch_calls, serviced)
    assert notified == [], (
        f"ENABLE=0 must block every cpu_irq notify, got {len(notified)}"
    )

    # Restore the gate and wake the CPU: the stream must drain.
    _program_intc(model.bridge, enable=0x1FF, threshold=1)
    model.riscv.interrupt_pending = True
    t.join(timeout=15)
    assert not t.is_alive(), "firmware did not resume after ENABLE restore"
    _assert_stream_drained(model, box, serviced)
