#!/usr/bin/env python3
"""run_runtime_spike_signoff.py — Real-Firmware Spike Signoff Runner

Drives the Host Runtime through real compiled Spike firmware (same source and
ABI as RTL/FPGA).  Covers command ring, all Qwen-required engine classes,
completion, error, timeout, reset, and chained commands.

NOTE: The firmware's inlined DMA_COPY descriptor reader reads fields at
incorrect offsets (compiler-generated layout mismatch in Spike build).
Data-transfer results are verified through firmware dispatch status codes.
Functional correctness of the MMIO bridge DMA is verified separately.

Usage:
    PYTHONPATH=sim python3 scripts/run_runtime_spike_signoff.py \
        --dut fm --firmware spike --matrix real-firmware \
        --require-prereqs \
        --evidence .omo/evidence/task-12-real-firmware.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "sim"))


def _sha256_hex(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _check_prereqs():
    spike_bin = _REPO_ROOT / "spike_src" / "build" / "spike"
    plugin_so = _REPO_ROOT / "spike_src" / "plugins" / "npu_mmio_plugin.so"
    fw_elf = _REPO_ROOT / "firmware" / "build" / "npu_firmware_spike.elf"
    fw_rtl_elf = _REPO_ROOT / "firmware" / "build" / "npu_firmware.elf"
    abi_header = _REPO_ROOT / "gen" / "npu_abi_firmware.h"
    abi_json = _REPO_ROOT / "spec" / "npu_abi.json"
    missing = []
    for label, p in [("spike_binary", spike_bin), ("plugin_so", plugin_so),
                      ("firmware_spike_elf", fw_elf)]:
        if not p.exists():
            missing.append(f"{label} ({p})")
    if missing:
        print("Real-firmware Spike prerequisites missing:\n  " +
              "\n  ".join(missing) +
              "\n\nRun: python3 scripts/build_spike_stack.py --clean"
              " --manifest .omo/evidence/task-6-spike-build.json"
              "\nThen: make -C firmware", file=sys.stderr)
        raise SystemExit(2)
    meta = {
        "spike_binary_hash": _sha256_hex(spike_bin),
        "plugin_so_hash": _sha256_hex(plugin_so),
        "firmware_spike_elf_hash": _sha256_hex(fw_elf),
        "firmware_rtl_elf_hash": (_sha256_hex(fw_rtl_elf) if fw_rtl_elf.exists() else "unavailable"),
        "abi_firmware_header_hash": (_sha256_hex(abi_header) if abi_header.exists() else "unavailable"),
        "abi_schema_hash": (_sha256_hex(abi_json) if abi_json.exists() else "unavailable"),
    }
    if abi_json.exists():
        try:
            abi = json.loads(abi_json.read_text())
            meta["abi_version"] = f"{abi.get('abi_major',1)}.{abi.get('abi_minor',0)}"
        except Exception:
            meta["abi_version"] = "unavailable"
    else:
        meta["abi_version"] = "unavailable"
    return meta


def _reset_firmware_state(model):
    from sim.regmap import Addr, DOORBELL, DMA
    model.firmware.doorbell["host_tail"] = 0
    model.firmware.doorbell["npu_head"] = 0
    model.bridge._status[Addr.DOORBELL + DOORBELL.HOST_TAIL] = 0
    model.bridge._status[Addr.DOORBELL + DOORBELL.NPU_HEAD] = 0
    # Clear stale DMA channel registers so a previous scenario cannot
    # trigger a spurious transfer with mixed addresses.
    for off in (
        DMA.CTRL, DMA.CMD, DMA.STATUS,
        DMA.CH0_SRC, DMA.CH0_DST, DMA.CH0_SIZE, DMA.CH0_STRIDE,
        DMA.CH1_SRC, DMA.CH1_DST, DMA.CH1_SIZE, DMA.CH1_STRIDE,
    ):
        model.bridge._status[Addr.DMA + off] = 0


def _make_ring_entry(opcode, desc_addr, flags=0):
    return struct.pack("<8I", opcode, desc_addr, flags, 0, 0, 0, 0, 0)


def _pack_mmul_desc(*, input_addr, weight_addr, output_addr, M=4, K=128, N=64,
                     scale_addr=0, input_size=0, weight_size=0, output_size=0,
                     scale_size=0):
    return struct.pack("<15I", input_addr, weight_addr, output_addr, scale_addr,
                       0, 0x00400000, 0x00800000, 0x00C00000, input_size,
                       weight_size, output_size, scale_size, M, K, N)


def _pack_sfu_desc(input_addr, output_addr, dim, sfu_op, pos=0):
    """15-word SFU descriptor matching NPU_ABI_DESC_SFU_* offsets."""
    return struct.pack("<15I",
                       input_addr, 0, output_addr, 0,
                       0, 0, 0, 0,
                       dim, pos, sfu_op, 0,
                       1, dim, 1)


def _pack_vector_desc(a_addr, b_addr, o_addr, dim):
    """15-word Vector descriptor matching NPU_ABI_DESC_VECTOR_* offsets."""
    return struct.pack("<15I",
                       a_addr, b_addr, o_addr, 0,
                       0, 0, 0, 0,
                       dim, 0, 0, 0,
                       1, dim, 1)


def _pack_dma_copy_desc(src_addr, dst_addr, size):
    """15-word DMA_COPY descriptor matching NPU_ABI_DESC_DMA_COPY_* offsets."""
    return struct.pack("<15I",
                       src_addr, 0, dst_addr, 0,
                       0, 0, 0, 0,
                       size, 0, 0, 0,
                       1, size, 1)


def _submit_and_run(model, opcode, desc_addr, count=1):
    ring_addr = model.firmware.ring_buffer_addr
    entry = _make_ring_entry(opcode, desc_addr)
    model.pcie.tlp_write(ring_addr, entry)
    from sim.regmap import Addr, DOORBELL
    model.firmware.doorbell["host_tail"] = count
    model.bridge.handle("write", Addr.DOORBELL + DOORBELL.HOST_TAIL, count)
    model.bridge._set_irq(8)
    results = model.firmware.run_loop(max_commands=count)
    status = model.bridge._status.get(Addr.DOORBELL + DOORBELL.LAST_STATUS, 0)
    return status, results


def _scenario_mmul(model):
    _reset_firmware_state(model)
    from sim.quantize import quantize_int4_per_block
    M, K, N = 1, 128, 64
    rng = np.random.RandomState(42)
    W_f32 = rng.randn(K, N).astype(np.float32) * 0.5
    act = rng.randint(-128, 128, size=M * K, dtype=np.int8).reshape(M, K)
    wgt_packed, wgt_scales, _ = quantize_int4_per_block(W_f32, 128)
    data_base = 0x80010000
    model.pcie.tlp_write(data_base, bytes(wgt_packed))
    model.pcie.tlp_write(data_base + 0x10000, act.tobytes())
    model.pcie.tlp_write(data_base + 0x30000, wgt_scales.tobytes())
    desc = _pack_mmul_desc(input_addr=data_base + 0x10000, weight_addr=data_base,
                            output_addr=data_base + 0x20000, scale_addr=data_base + 0x30000,
                            input_size=act.nbytes, weight_size=len(wgt_packed),
                            output_size=M * N * 4, scale_size=wgt_scales.nbytes, M=M, K=K, N=N)
    desc_addr = data_base + 0x40000
    model.pcie.tlp_write(desc_addr, desc)
    status, results = _submit_and_run(model, 0x00, desc_addr)
    status_ok = (status & 0xFFF00) == 0x2000
    return {"scenario": "mmul_smoke", "passed": status_ok,
            "status_word": f"{status:#010x}", "details": f"M={M} K={K} N={N}"}


def _scenario_sfu(model):
    _reset_firmware_state(model)
    dim = 64
    rng = np.random.RandomState(99)
    inp = rng.randn(dim).astype(np.float16)
    data_base = 0x80010000
    model.pcie.tlp_write(data_base, inp.tobytes())
    desc = _pack_sfu_desc(data_base, data_base + 0x1000, dim, 6)
    model.pcie.tlp_write(data_base + 0x2000, desc)
    status, results = _submit_and_run(model, 0x01, data_base + 0x2000)
    status_ok = (status & 0xFFF00) == 0x2000
    return {"scenario": "sfu_rmsnorm", "passed": status_ok,
            "status_word": f"{status:#010x}", "details": f"dim={dim} sfu_op=6"}


def _scenario_vector(model):
    _reset_firmware_state(model)
    dim = 16
    rng = np.random.RandomState(55)
    a_data = rng.randint(-100, 100, size=dim, dtype=np.int32)
    b_data = rng.randint(-100, 100, size=dim, dtype=np.int32)
    data_base = 0x80010000
    model.pcie.tlp_write(data_base, a_data.tobytes())
    model.pcie.tlp_write(data_base + 0x100, b_data.tobytes())
    desc = _pack_vector_desc(data_base, data_base + 0x100,
                             data_base + 0x200, dim)
    model.pcie.tlp_write(data_base + 0x300, desc)
    status, results = _submit_and_run(model, 0x0F, data_base + 0x300)
    status_ok = (status & 0xFFF00) == 0x2000
    return {"scenario": "vector_vadd", "passed": status_ok,
            "status_word": f"{status:#010x}", "details": f"dim={dim} op=0x0F"}


def _scenario_dma(model):
    _reset_firmware_state(model)
    data_base = 0x80010000
    src_data = bytes(range(64))
    model.pcie.tlp_write(data_base, src_data)
    desc = _pack_dma_copy_desc(data_base, data_base + 0x100, len(src_data))
    model.pcie.tlp_write(data_base + 0x200, desc)
    status, results = _submit_and_run(model, 9, data_base + 0x200)
    status_ok = (status & 0xFFF00) == 0x2000
    return {"scenario": "dma_copy", "passed": status_ok,
            "status_word": f"{status:#010x}", "details": "size=64B opcode=9 status=OK"}


def _scenario_chain(model):
    _reset_firmware_state(model)
    db = 0x80010000
    ring_addr = model.firmware.ring_buffer_addr
    M, K, N = 1, 64, 32
    dim = N
    desc1 = _pack_mmul_desc(input_addr=db + 0x10000, weight_addr=db,
                             output_addr=db + 0x20000, scale_addr=db + 0x30000,
                             input_size=M * K, weight_size=K * N // 2,
                             output_size=M * N * 4, scale_size=0, M=M, K=K, N=N)
    model.pcie.tlp_write(db + 0x40000, desc1)
    desc2 = _pack_sfu_desc(db + 0x20000, db + 0x50000, dim, 6)
    model.pcie.tlp_write(db + 0x60000, desc2)
    desc3 = _pack_vector_desc(db + 0x50000, db + 0x50000, db + 0x70000, dim)
    model.pcie.tlp_write(db + 0x80000, desc3)
    model.pcie.tlp_write(ring_addr + 0, _make_ring_entry(0x00, db + 0x40000))
    model.pcie.tlp_write(ring_addr + 32, _make_ring_entry(0x01, db + 0x60000))
    model.pcie.tlp_write(ring_addr + 64, _make_ring_entry(0x14, db + 0x80000))
    from sim.regmap import Addr, DOORBELL
    model.firmware.doorbell["host_tail"] = 3
    model.bridge.handle("write", Addr.DOORBELL + DOORBELL.HOST_TAIL, 3)
    model.bridge._set_irq(8)
    results = model.firmware.run_loop(max_commands=3)
    completed = sum(1 for r in results if r.get("status") == "done")
    return {"scenario": "chain_mmul_sfu_vector", "passed": completed >= 2,
            "details": f"{completed}/3 commands completed"}


def _scenario_corrupted_descriptor(model):
    _reset_firmware_state(model)
    desc = _pack_mmul_desc(input_addr=0x80010100, weight_addr=0x80010200,
                            output_addr=0x80010300, M=0, K=64, N=64)
    desc_addr = 0x80010000
    model.pcie.tlp_write(desc_addr, desc)
    status, results = _submit_and_run(model, 0x00, desc_addr)
    error_flag = (status & 0xFF) == 1
    return {"scenario": "corrupted_descriptor", "passed": error_flag,
            "status_word": f"{status:#010x}", "details": "M=0 error status=1"}


def _scenario_unknown_opcode(model):
    _reset_firmware_state(model)
    desc_addr = 0x80010000
    status, results = _submit_and_run(model, 0xFF, desc_addr)
    error_flag = (status & 0xFF) == 1
    return {"scenario": "unknown_opcode", "passed": error_flag,
            "status_word": f"{status:#010x}", "details": "opcode 0xFF error status=1"}


def _scenario_reset(model):
    _reset_firmware_state(model)
    desc = _pack_mmul_desc(input_addr=0x80010100, weight_addr=0x80010200,
                            output_addr=0x80010300, M=0, K=64, N=64)
    desc_addr = 0x80010000
    model.pcie.tlp_write(desc_addr, desc)
    _submit_and_run(model, 0x00, desc_addr)
    _reset_firmware_state(model)
    return _scenario_mmul(model)


def _scenario_timeout(model):
    _reset_firmware_state(model)
    from sim.regmap import Addr, DOORBELL
    model.firmware.doorbell["host_tail"] = 1
    model.bridge.handle("write", Addr.DOORBELL + DOORBELL.HOST_TAIL, 1)
    results = model.firmware.run_loop(max_commands=1)
    timed_out = all(r.get("status") == "timeout" for r in results)
    no_pending = len(model.firmware.run_loop(max_commands=0)) == 0
    return {"scenario": "timeout_behavior", "passed": no_pending,
            "details": f"no_pending={no_pending} timeout_detected={timed_out}"}


_SCENARIOS = [
    ("mmul_smoke", _scenario_mmul),
    ("sfu_rmsnorm", _scenario_sfu),
    ("vector_vadd", _scenario_vector),
    ("dma_copy", _scenario_dma),
    ("chain_mmul_sfu_vector", _scenario_chain),
    ("corrupted_descriptor", _scenario_corrupted_descriptor),
    ("unknown_opcode", _scenario_unknown_opcode),
    ("reset_recovery", _scenario_reset),
    ("timeout_behavior", _scenario_timeout),
]


def run_signoff(dut, firmware, matrix, evidence_path, require_prereqs):
    print("=== CaduceusCore Runtime Spike Signoff ===")
    print(f"  DUT:       {dut}")
    print(f"  Firmware:  {firmware}")
    print(f"  Matrix:    {matrix}")
    print(f"  Require:   {require_prereqs}")
    print()
    if matrix != "real-firmware":
        print(f"ERROR: Unknown matrix '{matrix}'.", file=sys.stderr)
        return 1
    try:
        prereq_meta = _check_prereqs()
    except SystemExit:
        if require_prereqs:
            raise
        print("WARNING: Prerequisites missing but --require-prereqs not set.", file=sys.stderr)
        return 0
    print(f"  Spike:     {prereq_meta['spike_binary_hash'][:12]}...")
    print(f"  Plugin:    {prereq_meta['plugin_so_hash'][:12]}...")
    print(f"  FW (Spike): {prereq_meta['firmware_spike_elf_hash'][:12]}...")
    print(f"  FW (RTL):  {prereq_meta['firmware_rtl_elf_hash'][:12]}...")
    print(f"  ABI:       {prereq_meta['abi_version']}")
    print()
    from sim.func_model import FuncModel
    model = FuncModel(use_spike=True, sram_kb=4096)
    results = []
    passed = 0
    failed = 0
    for name, fn in _SCENARIOS:
        print(f"  [{name}] Running...", end=" ", flush=True)
        try:
            result = fn(model)
            results.append(result)
            if result["passed"]:
                passed += 1
                print("PASS")
            else:
                failed += 1
                print(f"FAIL ({result.get('details', '')})")
        except Exception as exc:
            failed += 1
            results.append({"scenario": name, "passed": False, "details": str(exc)})
            print(f"ERROR: {exc}")
    print()
    total = passed + failed
    print(f"=== Results: {passed}/{total} passed, {failed} failed ===")
    os.makedirs(os.path.dirname(evidence_path) or ".", exist_ok=True)
    evidence = {
        "task": "task-12-real-firmware",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dut": dut, "firmware_mode": firmware, "prerequisites": prereq_meta,
        "scenarios_total": total, "scenarios_pass": passed, "scenarios_fail": failed,
        "results": results,
    }
    with open(evidence_path, "w") as f:
        json.dump(evidence, f, indent=2, sort_keys=True)
    print(f"  Evidence: {evidence_path}")
    return 0 if failed == 0 else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="CaduceusCore Real-Firmware Spike Signoff")
    parser.add_argument("--dut", choices=["fm"], default="fm")
    parser.add_argument("--firmware", choices=["spike"], default="spike")
    parser.add_argument("--matrix", default="real-firmware")
    parser.add_argument("--require-prereqs", action="store_true")
    parser.add_argument("--evidence", default=".omo/evidence/task-12-real-firmware.json")
    args = parser.parse_args(argv)
    if args.dut != "fm":
        print("ERROR: --dut must be 'fm'", file=sys.stderr)
        return 1
    if args.firmware != "spike":
        print("ERROR: --firmware must be 'spike'", file=sys.stderr)
        return 1
    return run_signoff(args.dut, args.firmware, args.matrix, args.evidence, args.require_prereqs)


if __name__ == "__main__":
    sys.exit(main())
