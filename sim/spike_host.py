#!/usr/bin/env python3
"""
Spike NPU Host Adapter

Prepares DRAM data using FuncModel.host_write_*(), drives the real RISC-V
firmware inside Spike through the MMIO bridge server, and verifies the output
against the GoldenMXU reference.
"""

import argparse
import os
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "ggml-npu"))

from q4_dequant import load_weights_from_gguf
from sim.func_model import FuncModel
from sim.golden_executor import GoldenMXU
from sim.quantize import quantize_int4_per_block
from sim.regmap import Addr, DOORBELL, MXU
from sim.spike_mmio_server import DEFAULT_SOCK_PATH, serve


# ── Paths ──────────────────────────────────────────────────────────

PROJECT = _HERE.parent
SPIKE_BIN = Path("/home/prj/zhengs/caduceuscore/spike_src/build/spike")
FIRMWARE_ELF = PROJECT / "firmware" / "build" / "npu_firmware.elf"
PLUGIN_SO = PROJECT / "spike_src" / "plugins" / "npu_mmio_plugin.so"

FIRMWARE_RING_BASE = 0x80100000  # hard-coded in C firmware; shadows part of low DRAM

# C firmware mmul_desc_t uses a 12-field packed layout.
MMUL_DESC_FMT = "<12I"
MMUL_DESC_SIZE = struct.calcsize(MMUL_DESC_FMT)


# ── Helpers ────────────────────────────────────────────────────────

def write_mmul_descriptor(model: FuncModel, desc_addr: int,
                          input_addr: int, weight_addr: int, output_addr: int,
                          input_sram: int, weight_sram: int, output_sram: int,
                          input_size: int, weight_size: int, output_size: int,
                          M: int, K: int, N: int):
    """Write a descriptor in the format expected by firmware npu_firmware.c."""
    buf = struct.pack(MMUL_DESC_FMT,
                      input_addr, weight_addr, output_addr,
                      input_sram, weight_sram, output_sram,
                      input_size, weight_size, output_size,
                      M, K, N)
    model.host_write_data(desc_addr, np.frombuffer(buf, dtype=np.uint8))


def run_one_op(gguf_path: str, layer: int, op: str, M: int = 1) -> bool:
    """Run a single op through Spike and verify against golden."""
    weights = load_weights_from_gguf(gguf_path)
    target = f"blk.{layer}.attn_{op.lower().replace('_proj', '')}.weight"
    if target not in weights:
        print(f"  [SKIP] L{layer} {op:12s} — weight not found")
        return False

    W_f32 = weights[target]
    K, N = W_f32.shape

    # Quantize to row-major INT4 + per-block scales (C firmware reads row-major)
    wgt_packed, wgt_scales, _ = quantize_int4_per_block(W_f32, 128)
    wgt_bytes = wgt_packed.tobytes()
    scale_bytes = wgt_scales.tobytes()

    # The C firmware copies one contiguous weight blob via DMA.  Pack weights
    # and scales back-to-back so the bridge can find scales at SCALE_ADDR.
    combined_weight_blob = wgt_bytes + scale_bytes

    # Activation
    rng = np.random.RandomState(42)
    act = rng.randint(-128, 128, size=M * K, dtype=np.int8).reshape(M, K)

    # Golden reference (row-major weights + scales)
    mxu = GoldenMXU()
    golden = mxu.matmul_int4_per_block(act, wgt_packed, wgt_scales, M, K, N, group_size=128)

    SRAM_KB = 4096  # match firmware NPU_SRAM_SIZE
    model = FuncModel(sram_kb=SRAM_KB)
    model.firmware.ring_buffer_addr = FIRMWARE_RING_BASE

    wgt_addr = 0x80200000
    act_addr = 0x80010000
    out_addr = 0x81000000
    desc_addr = 0x80001000

    # SRAM layout (must fit in 4 MB and avoid overlap)
    input_sram = 0x00000000
    weight_sram = 0x00100000
    output_sram = 0x00300000
    scale_sram = weight_sram + len(wgt_bytes)

    model.host_write_data(wgt_addr, np.frombuffer(combined_weight_blob, dtype=np.uint8))
    model.host_write_data(act_addr, act)

    write_mmul_descriptor(model, desc_addr,
                          input_addr=act_addr, weight_addr=wgt_addr, output_addr=out_addr,
                          input_sram=input_sram, weight_sram=weight_sram, output_sram=output_sram,
                          input_size=act.nbytes, weight_size=len(combined_weight_blob),
                          output_size=M * N * 4, M=M, K=K, N=N)
    model.host_write_command(0, desc_addr)

    # Pre-set MXU SCALE_ADDR so the bridge uses per-block dequantization.
    # The C firmware does not write SCALE_ADDR, so this persists through CMD.
    model.bridge.handle('write', MXU.BASE + MXU.SCALE_ADDR, scale_sram)

    # Set doorbell HOST_TAIL = 1 via the bridge (MMIO, not DRAM)
    model.bridge.handle('write', DOORBELL.BASE + DOORBELL.HOST_TAIL, 1)

    # Start the MMIO bridge server in a background thread
    ready_event = threading.Event()
    server = serve(model.bridge, sock_path=DEFAULT_SOCK_PATH, ready_event=ready_event)
    ready_event.wait(timeout=5.0)

    # Serialize DRAM image
    ddr_path = PROJECT / "ddr.bin"
    ddr_path.write_bytes(model.dram)

    # Launch Spike
    env = os.environ.copy()
    env["PATH"] = "/home/prj/zhengs/caduceuscore/dtc_src:" + env.get("PATH", "")

    cmd = [
        str(SPIKE_BIN),
        "--isa=RV32IM",
        "-m0x80000000:0x10000000,0x20000000:0x400000",
        f"--kernel={ddr_path}",
        f"--extlib={PLUGIN_SO}",
        "--device=npu,0x20000000",
        str(FIRMWARE_ELF),
    ]

    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Poll completion: NPU_HEAD == command count (mod 64)
    expected_head = 1 % 64
    done = False
    deadline = time.time() + 60.0
    try:
        while time.time() < deadline:
            head = model.bridge._status.get(DOORBELL.BASE + DOORBELL.NPU_HEAD, 0)
            if head == expected_head:
                done = True
                break
            time.sleep(0.05)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        server.shutdown()
        try:
            os.unlink(DEFAULT_SOCK_PATH)
        except FileNotFoundError:
            pass

    if not done:
        print(f"  [FAIL] L{layer} {op:12s} — timeout waiting for NPU_HEAD={expected_head}")
        return False

    # Read output tensor from model.dram
    out_off = out_addr - Addr.DRAM_BASE
    out_fw = np.frombuffer(model.dram[out_off:out_off + M * N * 4],
                           dtype=np.float32).reshape(M, N)

    ok = np.allclose(out_fw, golden, rtol=1e-5)
    print(f"  [{'PASS' if ok else 'FAIL'}] L{layer} {op:12s} ({K}x{N})")
    if not ok:
        print(f"    max_diff={np.max(np.abs(out_fw - golden)):.2e}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Spike NPU host adapter")
    parser.add_argument("--model", default=str(Path.home() / "models" /
                        "qwen2.5-1.5b-instruct-q4_k_m.gguf"),
                        help="Path to GGUF model")
    parser.add_argument("--layers", type=int, default=2,
                        help="Number of layers to test")
    parser.add_argument("--ops", default="Q_proj,K_proj,V_proj",
                        help="Comma-separated list of ops")
    args = parser.parse_args()

    ops = [o.strip() for o in args.ops.split(",")]
    passed = 0
    failed = 0

    print(f"{'='*70}")
    print(f"Spike Host: {Path(args.model).name}  layers={args.layers}  ops={ops}")
    print(f"{'='*70}")

    for layer in range(args.layers):
        for op in ops:
            ok = run_one_op(args.model, layer, op, M=1)
            if ok:
                passed += 1
            else:
                failed += 1

    print(f"\n{'='*70}")
    print(f"Spike Host Summary: {passed} PASS, {failed} FAIL")
    print(f"{'='*70}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
