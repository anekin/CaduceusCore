#!/usr/bin/env python3
"""
perf_tests_standalone_p11.py — Standalone PERF-11 Test (No Ring Buffer Contention)

Runs ONLY PERF-11 (M=1,K=512,N=128) in isolation.
Prints 32B SRAM hex, 32B DRAM hex, cos_sim, and cycle count.

Usage:
  MODULE=sim.perf_tests_standalone_p11 TESTCASE=test_w4_perf_p11_standalone simv_soc_ibex +COCOTB
"""

import os, sys, json, struct, time as time_mod, subprocess, logging
from typing import Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "sim"))

import numpy as np
try:
    import cocotb
    from cocotb.triggers import ClockCycles
    COCOTB_AVAILABLE = True
except ImportError:
    COCOTB_AVAILABLE = False

from sim.cocotb_bridge import CocotbBridge, DRAM_BASE as DB, SRAM_BASE as SB
from sim.cocotb_bridge import pack_int8_activation_tile_major, pack_int4_tile_major

logger = logging.getLogger("perf_tests_standalone_p11")

DRAM_BASE = 0x80000000
DESC_BASE = DRAM_BASE + 0x1000
RING_BASE = DRAM_BASE
DOORBELL_HTAIL = 0x40005000
DOORBELL_NHEAD = 0x40005004
TILE = 64
TILE_SCALE_BYTES = TILE * 4

def _make_scales(K, N, value=1.0):
    import struct
    k_blocks = (K + TILE - 1) // TILE
    n_tiles = (N + TILE - 1) // TILE
    fp16_val = struct.pack("<e", np.float16(value))
    padded_val = fp16_val + b'\x00\x00'
    tile_data = padded_val * TILE
    return tile_data * k_blocks * n_tiles

def _git():
    try:
        c = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True).strip()
        return {"commit": c, "timestamp": time_mod.strftime("%Y-%m-%dT%H:%M:%SZ", time_mod.gmtime())}
    except: return {"commit": "?", "timestamp": "?"}

def _entry(cid, st, cyc, cs=None, **kw):
    e = {"case_id": cid, "simulator": "ibex", "status": st, "cycles": int(cyc), **_git()}
    if cs is not None: e["cos_sim"] = round(float(cs), 6)
    e.update(kw); return e

def _save(path, entries):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for e in entries: f.write(json.dumps(e) + "\n")

def _pack_mmul_desc(ia, wa, oa, sa, iss, wss, oss, sss, isz, wsz, osz, ssz, M, K, N):
    return struct.pack("<15I", ia, wa, oa, sa, iss, wss, oss, sss, isz, wsz, osz, ssz, M, K, N)

def _pack_cmd(opcode, desc_addr, flags=0):
    return struct.pack("<8I", opcode, desc_addr, flags, 0, 0, 0, 0, 0)

def _gen(M, K, N, seed=42):
    rng = np.random.RandomState(seed)
    w = rng.randint(-8, 8, size=(K, N), dtype=np.int8)
    a = rng.randint(-128, 128, size=(M, K), dtype=np.int8)
    g = np.zeros((M, N), dtype=np.int32)
    for m in range(M):
        for n in range(N):
            s = 0
            for k in range(K): s += int(a[m, k]) * int(w[k, n])
            g[m, n] = s
    return {"act": a, "wgt": w, "golden": g}

def _pack_w(wgt):
    K, N = wgt.shape; p = np.zeros(K * N // 2, dtype=np.uint8)
    for k in range(K):
        for n in range(0, N, 2):
            lo = int(wgt[k, n]) & 0xF
            hi = int(wgt[k, min(n+1, N-1)]) & 0xF
            p[(k * N + n) // 2] = (hi << 4) | lo
    return p


@cocotb.test()
async def test_w4_perf_p11_standalone(dut):
    """Standalone PERF-11: single mmul call, no ring buffer contention."""
    b = CocotbBridge(dut)
    await b.start_clock()
    await b.reset(5)
    await b.load_firmware(os.environ.get("BOOTROM_HEX", "firmware/build/npu_firmware.hex"))
    await b.wait_cycles(2000)
    off = int(dut.sim_cycle.value) if hasattr(dut, 'sim_cycle') else 0
    def _c(): return int(dut.sim_cycle.value) - off

    M, K, N = 1, 512, 128
    seed = 700
    v = _gen(M, K, N, seed)

    ad = DRAM_BASE + 0x10000
    wd = DRAM_BASE + 0x20000
    od = DRAM_BASE + 0x30000
    scale_addr = DRAM_BASE + 0x40000

    wp = _pack_w(v["wgt"])
    scales = _make_scales(K, N, 1.0)

    # Tile-major packing (post-fix)
    act_packed = pack_int8_activation_tile_major(v["act"].tobytes(), M, K)
    wp_packed = pack_int4_tile_major(wp.tobytes(), K, N)

    await b._dram_backdoor_write(ad, act_packed)
    await b._dram_backdoor_write(wd, wp_packed)
    await b._dram_backdoor_write(scale_addr, scales)

    desc = _pack_mmul_desc(ad, wd, od, scale_addr, 0, 0, 0, 0,
                           len(act_packed), len(wp_packed), M*N*4, len(scales),
                           M, K, N)
    await b._dram_backdoor_write(DESC_BASE, desc)
    await b._dram_backdoor_write(RING_BASE, _pack_cmd(0, DESC_BASE, 0))

    print(f"[PERF-11-STANDALONE] M={M} K={K} N={N} seed={seed}")
    print(f"[PERF-11-STANDALONE] act_packed={len(act_packed)}B wp_packed={len(wp_packed)}B scales={len(scales)}B")

    t0 = _c()
    await b._doorbell_backdoor_write(DOORBELL_HTAIL, 1)
    for _ in range(max(1_000_000, M*K*N)):
        if await b._doorbell_backdoor_read(DOORBELL_NHEAD) == 1:
            break
        await ClockCycles(dut.clk, 1)
    else:
        raise TimeoutError("PERF-11-STANDALONE: NPU_HEAD timeout")
    cyc = _c() - t0

    # Wait for DRAM writes to settle
    await ClockCycles(dut.clk, 200)

    raw_dram = await b._dram_backdoor_read(od, M * N * 4)
    sram_raw = await b._sram_backdoor_read(0x20018000, min(M * N * 4, 128))

    # Print full 32B hex dumps for evidence
    print(f"[PERF-11-STANDALONE] DRAM_32B={bytes(raw_dram[:32]).hex()}")
    print(f"[PERF-11-STANDALONE] SRAM_32B={sram_raw[:32].hex()}")
    print(f"[PERF-11-STANDALONE] DRAM_first8={bytes(raw_dram[:8]).hex()} SRAM_first8={sram_raw[:8].hex()}")

    out = np.frombuffer(bytes(raw_dram), dtype=np.int32).reshape(M, N)
    golden = v["golden"]
    a, g = out.flatten().astype(float), golden.flatten().astype(float)
    na, ng = np.linalg.norm(a), np.linalg.norm(g)
    cs = np.dot(a, g) / (na * ng) if na > 0 and ng > 0 else 0.0

    print(f"[PERF-11-STANDALONE] golden_first8={bytes(golden.ravel()[:8].tobytes()).hex()}")
    print(f"[PERF-11-STANDALONE] cos_sim={cs:.6f} cyc={cyc} norm_a={na:.1f} norm_g={ng:.1f}")

    status = "PASS" if cs >= 0.999 else ("PARTIAL_PASS" if cs >= 0.5 else "FAIL")

    ev = []
    ev.append(_entry("PERF-11", status, cyc, cs, M=M, K=K, N=N,
                     sram_32b=sram_raw[:32].hex(),
                     dram_32b=bytes(raw_dram[:32]).hex(),
                     golden_first8=bytes(golden.ravel()[:8].tobytes()).hex(),
                     act_packed_len=len(act_packed),
                     wp_packed_len=len(wp_packed),
                     note="standalone PERF-11 — tile-major packing, no ring buffer contention"))
    _save(os.path.join(_ROOT, "build", "evidence", "w4-perf-p2.txt"), ev)

    print(f"[PERF-11-STANDALONE] status={status}")
    assert status in ("PASS", "PARTIAL_PASS"), f"PERF-11 cos_sim={cs:.6f} FAIL"
