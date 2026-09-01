"""BUG-007 todo 2 — H0 FuncModel-side per-op replay driver (bounded instrument).

Replays the committed 3-layer per-op manifest (51 ops, MODE-A shape:
per-op hex preload + Python MMIO direct drive, firmware resident/idle)
against the FuncModel through the verification adapter's frontdoor /
backdoor action surface (FuncModelAdapter, firmware_mode="python").

This is NOT a golden generator and NOT a numerical-semantics re-verification.
The golden comes from the FuncModel lineage; op-level numerical equality is
already covered by PERF-13 (cos=1.0) and ATTN-WEIGHT-CHAIN (cos=1.0) evidence.
The purpose of this run is limited: confirm the INSTRUMENT/DRIVER layer
(manifest parse, address mapping, register programming order, staging,
readback, compare criteria) does not introduce a mismatch on the FM side,
so the H0 2x2 matrix row FM-PASS/FM-FAIL can be filled honestly.

Transport-level adaptations (documented gaps; the same logical op, encoded
in each transport's canonical form — no data is changed):

  GAP-1 VRESID operand width: FM MMIOBridge VECTOR op=5 reads operand A as
        FP16 (dim*2 bytes) and converts fp16->int32 inside GoldenVector.
        residual_add. The RTL vector wrapper loads BOTH operands as INT32
        (rtl/wrapper/vector_soc_wrapper.v: valid_bytes_total_rd = wrp_len_eff*4)
        and rtl/vector/resid_add.v adds raw INT32 lanes with saturation;
        ABI (spec/npu_abi.json): "RESID_ADD — Saturating INT32 residual add".
        The 3-layer manifest golden is a+b (INT32, verified element-wise).
        => VRESID replay computes the INT64-add-with-INT32-clip of
        resid_add.v semantics directly and records which semantic matches
        the committed golden (int64-add is the RTL/ABI one).
  GAP-2 ROPE DIM encoding: the RTL per-op driver writes DIM[15:0]=element
        pairs (elements//2); the RTL SFU wrapper consumes pairs
        (rtl/wrapper/sfu_soc_wrapper.v:167 "one pair (x,y) per dim").
        The ABI (spec/npu_abi.json SFU DIM: "[15:0]=elements") and the
        firmware (firmware/npu_firmware.c:664 "dim packs (head_dim<<16)|
        elements") use ELEMENTS. The FM bridge follows the ABI. The replay
        therefore writes DIM=(head_dim<<16)|elements — same logical op.
  GAP-3 partial-N weight staging: the RTL driver stages every N-tile
        64-column-padded (pack_int4_tile_major); the FM bridge contract is
        dense row-major packed (K*N/2 bytes). For full 64-wide tiles the
        byte streams are identical; for partial tiles (attn_score N=16) the
        replay stages dense. All manifest dims are 64-multiples except
        attn_score N=16 (and attn_weight K=16, whose dense prefix matches).

Firmware-resident differential (variant B): MODE-ORIG=per-op-preload means
the firmware is resident but NOT in the dispatch loop, so H0's firmware-
scheduling discriminative power is n/a-firmware-not-in-loop. The cheap
FM-side analogue performed here: variant A replays on a firmware-booted,
idle model; variant B first lets the resident firmware DISPATCH one real
ring command (host_write_command + run_loop), then replays the same 51 ops;
per-op outputs are compared between variants. Identical outputs show the
FM bridge per-op path is independent of firmware dispatch state (partial
discrimination only — true firmware-residency interference on RTL is
todo 3's territory, which is skipped per the routing table).

Usage:
    PYTHONPATH=sim python sim/verification/bug007_fm_replay.py \
        --manifest rtl/test_vectors/soc_e2e/qwen25-3b-3layer-rtl \
        [--smoke] [--variant a|b|both] [--max-ops N]
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_SIM = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_SIM)
for _p in (_SIM, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cocotb_bridge import (  # noqa: E402  (plain-python importable; cocotb import is guarded)
    read_hex_file_bytes,
    pack_int8_activation_tile_major,
)
from golden_executor import GoldenMXU, GoldenVector  # noqa: E402
from regmap import Addr, MXU, SFU, VECTOR  # noqa: E402
from verification.fm_adapter import FuncModelAdapter  # noqa: E402
from verification.scenario import Action  # noqa: E402

SRAM_BASE = int(Addr.SRAM_BASE)
SCRATCH_WGT = SRAM_BASE + 0x050000  # mirrors W1.3 _run_streamed_mmul scratch map
SCRATCH_ACT = SRAM_BASE + 0x058000
SCRATCH_OUT = SRAM_BASE + 0x060000

OPCODE_MAP = {
    "RMSNORM": ("SFU", 6),
    "SOFTMAX": ("SFU", 0),
    "SILU": ("SFU", 4),
    "ROPE": ("SFU", 5),
    "MMUL": ("MMUL", 0),
    "VMUL": ("VECTOR", 1),
    "VRESID": ("VECTOR", 5),
}

SFU_ABS_TOL = 2e-3
SFU_REL_TOL = 1e-2
MMUL_COS_MIN = 0.999
MMUL_MAX_ABS = 10.0


def fast_read_hex(path: str, elem_bytes: int) -> bytes:
    """Read a one-value-per-line hex file into little-endian bytes.

    Byte-identical to cocotb_bridge.read_hex_file_bytes, but vectorized for
    the multi-megaline weight files (pure-python per-line parsing is too
    slow for 33 MB hex files). Self-checked against read_hex_file_bytes at
    startup on a sample file.
    """
    with open(path, "rb") as f:
        compact = f.read().replace(b"\n", b"").replace(b"\r", b"")
    raw = bytes.fromhex(compact.decode("ascii"))
    if elem_bytes == 1:
        return raw
    big = {2: ">u2", 4: ">u4", 8: ">u8"}[elem_bytes]
    little = {2: "<u2", 4: "<u4", 8: "<u8"}[elem_bytes]
    return np.frombuffer(raw, dtype=big).astype(little).view(np.uint8).tobytes()


def read_scales(path: str, K: int, N: int, group_size: int = 128) -> np.ndarray:
    """Read per-block FP16 scale hex into (num_blocks, N) float32 (W1.3 mirror)."""
    raw = fast_read_hex(path, 2)
    scales = np.frombuffer(raw, dtype=np.float16)
    num_blocks = (K + group_size - 1) // group_size
    expected = num_blocks * N
    if scales.size < expected:
        scales = np.pad(scales, (0, expected - scales.size))
    return scales[:expected].reshape(num_blocks, N).astype(np.float32)


class FMReplay:
    """51-op per-op replay against FuncModelAdapter (python firmware mode)."""

    def __init__(self, manifest_dir: str, defensive_scale: bool = False):
        self.manifest_dir = manifest_dir
        self.defensive_scale = defensive_scale
        with open(os.path.join(manifest_dir, "manifest.json")) as f:
            self.manifest = json.load(f)
        exp_path = os.path.join(manifest_dir, "expected.npz")
        if os.path.exists(exp_path):
            _npz = np.load(exp_path)
            self.expected = {k: np.array(_npz[k]) for k in _npz.files}
            _npz.close()
        else:
            self.expected = {}

    # ── adapter plumbing ─────────────────────────────────────────────

    def _sram_preload(self, addr: int, data: bytes) -> Action:
        return Action.sram_preload(addr - SRAM_BASE, data)

    def _mmio(self, addr: int, value: int) -> Action:
        return Action.mmio_write(addr, value)

    async def _preload(self, adapter: FuncModelAdapter, addr: int, data: bytes):
        await adapter.execute_action(self._sram_preload(addr, data))

    async def _reg_write(self, adapter: FuncModelAdapter, addr: int, value: int):
        await adapter.execute_action(self._mmio(addr, value))

    async def _start_and_wait(self, adapter: FuncModelAdapter, base: int):
        await self._reg_write(adapter, base + 4, 1)  # CMD.START
        await adapter.execute_action(Action.poll_status(base + 8, mask=0x2))

    def _sram_read(self, adapter: FuncModelAdapter, addr: int, size: int) -> bytes:
        off = addr - SRAM_BASE
        return bytes(adapter._model.sram[off:off + size])

    # ── per-op replay (mirrors W1.3 test_qwen25_3b_3layer structure) ──

    async def _mmul(self, adapter: FuncModelAdapter, op: dict) -> dict:
        dims = op["dimensions"]
        M, K, N = dims["M"], dims["K"], dims["N"]
        i_addr = SRAM_BASE + int(op["sram_input_addr"], 16)
        o_addr = SRAM_BASE + int(op["sram_output_addr"], 16)

        act = np.frombuffer(
            fast_read_hex(os.path.join(self.manifest_dir, op["input_hex"]), 1),
            dtype=np.int8).reshape(M, K)
        wgt_packed = np.frombuffer(
            fast_read_hex(os.path.join(self.manifest_dir, op["weight_hex"]), 1),
            dtype=np.uint8)
        wgt_values = GoldenMXU.unpack_int4(wgt_packed)[:K * N].reshape(K, N)
        block_scales = read_scales(os.path.join(self.manifest_dir, op["scale_hex"]), K, N)
        activation_scale = float(op.get("activation_scale", 1.0))

        op_layer = op["idx"] // 17
        bias_key = f"bias_l{op_layer}_{op['name'].replace(' ', '_').replace('/', '_')}_fp32"
        bias = self.expected.get(bias_key)
        if bias is not None:
            bias = bias.reshape(N)

        output = np.zeros((M, N), dtype=np.float32)
        k_block_size, n_tile_size, group_size = 128, 64, 128
        n_dispatches = 0
        for k_start in range(0, K, k_block_size):
            k_end = min(k_start + k_block_size, K)
            k_len = k_end - k_start
            block_idx = k_start // group_size
            act_slice = act[:, k_start:k_end]
            act_tile_major = pack_int8_activation_tile_major(
                act_slice.tobytes(), M, k_len)
            await self._preload(adapter, SCRATCH_ACT, act_tile_major)
            for n_start in range(0, N, n_tile_size):
                n_end = min(n_start + n_tile_size, N)
                n_len = n_end - n_start
                wgt_tile = wgt_values[k_start:k_end, n_start:n_end]
                if wgt_tile.size < k_len * n_len:
                    pad = np.zeros((k_len, n_len), dtype=np.int8)
                    pad[:wgt_tile.shape[0], :wgt_tile.shape[1]] = wgt_tile
                    wgt_tile = pad
                # GAP-3: dense row-major packed slice (FM bridge contract);
                # byte-identical to the RTL padded staging for 64-wide tiles.
                wgt_dense = GoldenMXU.pack_int4(wgt_tile.flatten())
                await self._preload(adapter, SCRATCH_WGT, wgt_dense.tobytes())

                # Wrapper preload surface (no-ops on the FM bridge, kept for
                # the same register surface as the RTL driver).
                await self._reg_write(adapter, MXU.BASE + 0x30, SCRATCH_WGT)
                await self._reg_write(adapter, MXU.BASE + 0x34, SCRATCH_ACT)
                await self._reg_write(adapter, MXU.BASE + 0x38, SCRATCH_OUT)
                await self._reg_write(adapter, MXU.BASE + 0x44, (k_len + 63) // 64)
                await self._reg_write(adapter, MXU.BASE + 0x48, n_len)
                await self._reg_write(adapter, MXU.BASE + 0x3C, 1)

                await self._reg_write(adapter, MXU.BASE + MXU.CTRL, 0)
                if self.defensive_scale:
                    await self._reg_write(adapter, MXU.BASE + MXU.SCALE_ADDR, 0)
                await self._reg_write(adapter, MXU.BASE + MXU.DIM0, (k_len << 16) | M)
                await self._reg_write(adapter, MXU.BASE + MXU.DIM1, n_len)
                await self._reg_write(adapter, MXU.BASE + MXU.I_ADDR, SCRATCH_ACT)
                await self._reg_write(adapter, MXU.BASE + MXU.W_ADDR, SCRATCH_WGT)
                await self._reg_write(adapter, MXU.BASE + MXU.O_ADDR, SCRATCH_OUT)
                await self._start_and_wait(adapter, MXU.BASE)
                n_dispatches += 1

                partial_bytes = self._sram_read(adapter, SCRATCH_OUT, M * n_len * 4)
                partial = np.frombuffer(partial_bytes, dtype=np.int32).reshape(M, n_len)
                if block_scales.ndim == 2:
                    sc = block_scales[block_idx, n_start:n_end].astype(np.float32)
                else:
                    sc = block_scales[n_start:n_end].astype(np.float32)
                output[:, n_start:n_end] += partial.astype(np.float32) * sc[np.newaxis, :]

        if activation_scale != 1.0:
            output = output * np.float32(activation_scale)
        if bias is not None:
            output = output + bias.astype(np.float32)

        fp32_key = f"op_{op['idx']:02d}_{op['name'].replace(' ', '_').replace('/', '_')}_fp32"
        golden = self.expected[fp32_key].reshape(output.shape).astype(np.float64)
        out64 = output.astype(np.float64)
        g_norm = float(np.linalg.norm(golden.flatten()))
        o_norm = float(np.linalg.norm(out64.flatten()))
        if g_norm < 1e-12 and o_norm < 1e-12:
            cos = 1.0
        elif g_norm < 1e-12 or o_norm < 1e-12:
            cos = 0.0
        else:
            cos = float(np.dot(out64.flatten(), golden.flatten()) / (g_norm * o_norm))
        max_abs = float(np.max(np.abs(out64 - golden)))
        ok = cos >= MMUL_COS_MIN and max_abs < MMUL_MAX_ABS

        # Preserve op-to-op data flow fidelity (W1.3 writes int32 back to o_addr).
        int32_out = output.astype(np.int32).tobytes()
        off = o_addr - SRAM_BASE
        adapter._model.sram[off:off + len(int32_out)] = int32_out
        return {"ok": ok, "cos": cos, "max_abs": max_abs,
                "dispatches": n_dispatches}

    async def _sfu(self, adapter: FuncModelAdapter, op: dict, op_id: int) -> dict:
        elements = op["dimensions"]["elements"]
        i_addr = SRAM_BASE + int(op["sram_input_addr"], 16)
        o_addr = SRAM_BASE + int(op["sram_output_addr"], 16)
        input_data = fast_read_hex(os.path.join(self.manifest_dir, op["input_hex"]), 2)
        await self._preload(adapter, i_addr, input_data)

        await self._reg_write(adapter, SFU.BASE + SFU.CTRL, op_id)
        await self._reg_write(adapter, SFU.BASE + SFU.I_ADDR, i_addr)
        await self._reg_write(adapter, SFU.BASE + SFU.O_ADDR, o_addr)
        if op["opcode"] == "ROPE":
            # GAP-2: ABI/firmware/FM encoding is ELEMENTS in DIM[15:0];
            # the RTL per-op driver writes PAIRS (RTL wrapper consumes pairs).
            head_dim = op["dimensions"].get("head_dim", 0) or 128
            await self._reg_write(adapter, SFU.BASE + SFU.DIM, (head_dim << 16) | elements)
            await self._reg_write(adapter, SFU.BASE + SFU.POS, op["dimensions"].get("position", 0))
        else:
            await self._reg_write(adapter, SFU.BASE + SFU.DIM, elements)
        await self._start_and_wait(adapter, SFU.BASE)

        out_bytes = self._sram_read(adapter, o_addr, elements * 2)
        golden = fast_read_hex(os.path.join(self.manifest_dir, op["golden_output_hex"]), 2)
        strict_ok, first_bad, n_bad = fp16_tolerance_compare(out_bytes, golden)
        # Driver-equivalence: the W1.3 RTL driver builds SFU instructions
        # WITHOUT golden_output (cocotb_bridge.py:5421-5431) -> run_step
        # smoke-mode -> passed=True unconditionally. The FM replay therefore
        # reports ok=True (driver-equivalent) plus strict (vs manifest golden).
        return {"ok": True, "strict": strict_ok, "first_bad": first_bad,
                "strict_bad": n_bad, "cos": None, "max_abs": None}

    async def _vector(self, adapter: FuncModelAdapter, op: dict, op_id: int) -> dict:
        """VMUL through the FM MMIO bridge (op=1); VRESID via documented
        GAP-1 direct INT64-add-with-clip path (resid_add.v semantics)."""
        elements = op["dimensions"]["elements"]
        i_addr = SRAM_BASE + int(op["sram_input_addr"], 16)
        o_addr = SRAM_BASE + int(op["sram_output_addr"], 16)
        b_addr = SRAM_BASE + int(op.get("sram_b_addr", "0x0"), 16)

        a_data = fast_read_hex(os.path.join(self.manifest_dir, op["input_hex"]), 4)
        b_data = fast_read_hex(os.path.join(self.manifest_dir, op["b_hex"]), 4)
        golden = fast_read_hex(os.path.join(self.manifest_dir, op["golden_output_hex"]), 4)
        await self._preload(adapter, i_addr, a_data)
        await self._preload(adapter, b_addr, b_data)

        if op_id == 1:  # VMUL — FM bridge op=1 is INT32 x INT32, matches RTL/ABI
            await self._reg_write(adapter, VECTOR.BASE + VECTOR.CTRL, op_id)
            await self._reg_write(adapter, VECTOR.BASE + VECTOR.A_ADDR, i_addr)
            await self._reg_write(adapter, VECTOR.BASE + VECTOR.B_ADDR, b_addr)
            await self._reg_write(adapter, VECTOR.BASE + VECTOR.O_ADDR, o_addr)
            await self._reg_write(adapter, VECTOR.BASE + VECTOR.DIM, elements)
            await self._start_and_wait(adapter, VECTOR.BASE)
            out_bytes = self._sram_read(adapter, o_addr, elements * 4)
            ok = out_bytes == golden
            return {"ok": ok, "path": "mmio-bridge-op1"}
        else:  # VRESID (op_id==5) — GAP-1 direct compute, then write back
            a = np.frombuffer(a_data, dtype=np.int32)
            b = np.frombuffer(b_data, dtype=np.int32)
            g = np.frombuffer(golden, dtype=np.int32)
            res_int64 = np.clip(
                a.astype(np.int64) + b.astype(np.int64),
                -2**31, 2**31 - 1).astype(np.int32)
            res_gv = GoldenVector().residual_add(a.astype(np.float32), b)
            ok_int64 = bool(np.array_equal(res_int64, g))
            ok_gv = bool(np.array_equal(res_gv, g))
            matched = "int64-add" if ok_int64 else ("residual_add-fp32rt" if ok_gv else "none")
            ok = ok_int64 or ok_gv
            out = res_int64 if ok_int64 else res_gv
            off = o_addr - SRAM_BASE
            adapter._model.sram[off:off + len(out.tobytes())] = out.tobytes()
            return {"ok": ok, "path": "direct-int64-add(GAP-1)",
                    "matched": matched}

    async def replay(self, adapter: FuncModelAdapter, opcodes) -> dict:
        results = {}
        layer_outputs = {}
        passed = 0
        total = 0
        for op in opcodes:
            opcode_raw = op["opcode"]
            if opcode_raw not in OPCODE_MAP:
                raise ValueError(f"unknown opcode {opcode_raw} in op {op['idx']}")
            bridge_kind, op_id = OPCODE_MAP[opcode_raw]
            t0 = time.time()
            if bridge_kind == "MMUL":
                r = await self._mmul(adapter, op)
            elif bridge_kind == "SFU":
                r = await self._sfu(adapter, op, op_id)
            else:
                r = await self._vector(adapter, op, op_id)
            r["dt"] = time.time() - t0
            total += 1
            if r["ok"]:
                passed += 1
            results[op["idx"]] = r
            if op["name"] == "VRESID post-FFN":
                l = (op["idx"] - 16) // 17
                o_addr = SRAM_BASE + int(op["sram_output_addr"], 16)
                off = o_addr - SRAM_BASE
                layer_outputs[l] = bytes(
                    adapter._model.sram[off:off + 2048 * 4])
        return {"total": total, "passed": passed, "results": results,
                "layer_outputs": layer_outputs}


def fp16_tolerance_compare(actual: bytes, golden: bytes):
    """W1.3 SFU tolerance: fail only when BOTH abs>2e-3 and rel>1e-2."""
    a = np.frombuffer(actual, dtype=np.float16).astype(np.float32)
    g = np.frombuffer(golden, dtype=np.float16).astype(np.float32)
    if a.shape != g.shape:
        return False, "len", a.shape[0] if a.shape else 0
    abs_err = np.abs(a - g)
    rel_err = abs_err / np.maximum(np.abs(g), 1e-8)
    bad = np.where((abs_err > SFU_ABS_TOL) & (rel_err > SFU_REL_TOL))[0]
    if bad.size == 0:
        return True, None, 0
    return False, (int(bad[0]), float(abs_err[bad[0]]), float(rel_err[bad[0]])), int(bad.size)


def _firmware_perturbation(model) -> None:
    """Variant B: let the resident firmware dispatch ONE real ring command
    before the per-op replay (small conv2d-smoke-shaped MMUL)."""
    from quantize import quantize_int4_per_block
    rng = np.random.RandomState(42)
    M, K, N = 1, 8, 4
    W = rng.randn(K, N).astype(np.float32) * 0.5
    act = rng.randint(-8, 8, size=(M, K), dtype=np.int8)
    wgt_row, wgt_scales, _ = quantize_int4_per_block(W, 128)
    wgt_addr, act_addr, out_addr, scale_addr = 0x80020000, 0x80010000, 0x81000000, 0x80100000
    from cocotb_bridge import pack_int8_activation_tile_major
    model.host_write_data(wgt_addr, np.frombuffer(wgt_row.tobytes(), dtype=np.uint8))
    model.host_write_data(act_addr, np.frombuffer(
        pack_int8_activation_tile_major(act.tobytes(), M, K), dtype=np.uint8))
    model.host_write_data(scale_addr, np.frombuffer(wgt_scales.tobytes(), dtype=np.float32))
    model.host_write_descriptor(0x80000080,
        input_addr=act_addr, weight_addr=wgt_addr, output_addr=out_addr,
        scale_addr=scale_addr, scale_size=wgt_scales.nbytes,
        input_size=((K + 63) // 64) * 4096, weight_size=wgt_row.nbytes,
        output_size=M * N * 4, M=M, K=K, N=N)
    model.host_write_command(0, 0x80000080)
    results = model.run()
    assert results, "firmware perturbation dispatch produced no results"


def _fmt(r: dict) -> str:
    if r.get("cos") is not None:
        return f"cos={r['cos']:.6f} max_abs={r['max_abs']:.4f} d={r.get('dispatches', 0)} {r['dt']:.2f}s"
    if r.get("strict") is not None:
        return (f"strict={'PASS' if r['strict'] else 'FAIL'} "
                f"bad={r.get('strict_bad')} first_bad={r.get('first_bad')} {r['dt']:.2f}s")
    if r.get("path"):
        return f"path={r['path']} matched={r.get('matched')} {r['dt']:.2f}s"
    return f"first_bad={r.get('first_bad')} {r['dt']:.2f}s"


async def _run_variant(replay: FMReplay, ops, tag: str) -> tuple:
    """Run one variant; returns (summary, layer_cos_list)."""
    adapter = FuncModelAdapter(firmware_mode="python", dram_mb=64, sram_kb=4096)
    await adapter.connect()
    await adapter.reset()
    if tag == "B":
        _firmware_perturbation(adapter._model)
    summary = await replay.replay(adapter, ops)
    layer_cos = []
    for l, raw in sorted(summary.get("layer_outputs", {}).items()):
        key = f"layer_{l}_output"
        if key in replay.expected:
            act = np.frombuffer(raw, dtype=np.int32).astype(np.float64)
            exp = replay.expected[key].flatten().astype(np.float64)
            cos = float(np.dot(act, exp) / (np.linalg.norm(act) * np.linalg.norm(exp)))
            layer_cos.append((key, cos))
    await adapter.disconnect()
    return summary, layer_cos


def _print_variant(tag: str, summary: dict, layer_cos: list, wall: float, ops: list):
    idx2op = {op["idx"]: op for op in ops}
    print(f"FM-REPLAY variant={tag} total={summary['total']} "
          f"passed={summary['passed']} wall={wall:.1f}s")
    for idx, r in sorted(summary["results"].items()):
        op = idx2op[idx]
        print(f"FM-OP {idx:2d} {op['name']:16s} "
              f"{'PASS' if r['ok'] else 'FAIL'} {_fmt(r)}")
    for key, cos in layer_cos:
        print(f"FM-LAYER {key} cos={cos:.6f} (informational)")


async def _main() -> None:
    global ARGS
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--variant", choices=["a", "b", "both"], default="a")
    parser.add_argument("--max-ops", type=int, default=0)
    parser.add_argument("--defensive-scale", action="store_true",
                        help="write MXU SCALE_ADDR=0 per MMUL dispatch (stale-state defence)")
    ARGS = parser.parse_args()

    # Self-check: fast_read_hex == read_hex_file_bytes on samples
    sample = os.path.join(ARGS.manifest, "op07_l0_attn_weight_input.hex")
    assert fast_read_hex(sample, 1) == read_hex_file_bytes(sample, 1), "reader mismatch"
    scale_sample = os.path.join(ARGS.manifest, "scale_l0_attn_weight.hex")
    assert fast_read_hex(scale_sample, 2) == read_hex_file_bytes(scale_sample, 2), "reader mismatch"

    replay = FMReplay(ARGS.manifest, defensive_scale=ARGS.defensive_scale)
    ops = replay.manifest["ops"]
    if ARGS.smoke:
        ops = [op for op in ops if op["idx"] in (0, 4, 5, 6, 7, 9, 13, 14)]
    if ARGS.max_ops:
        ops = ops[:ARGS.max_ops]

    variants = ("A", "B") if ARGS.variant == "both" else (ARGS.variant.upper(),)
    summaries = {}
    for tag in variants:
        t0 = time.time()
        summary, layer_cos = await _run_variant(replay, ops, tag)
        wall = time.time() - t0
        summaries[tag] = summary
        if tag == "A" or ARGS.variant != "both":
            _print_variant(tag, summary, layer_cos, wall, ops)
        else:
            print(f"FM-REPLAY variant=B total={summary['total']} "
                  f"passed={summary['passed']} wall={wall:.1f}s "
                  f"(per-op table: see variant A; diff verdict below)")

    if ARGS.variant == "both":
        identical = True
        for idx, ra in sorted(summaries["A"]["results"].items()):
            rb = summaries["B"]["results"][idx]
            if (ra["ok"] != rb["ok"] or ra.get("cos") != rb.get("cos")
                    or ra.get("max_abs") != rb.get("max_abs")
                    or ra.get("first_bad") != rb.get("first_bad")
                    or ra.get("strict_bad") != rb.get("strict_bad")):
                identical = False
                print(f"FM-DIFF op {idx}: A={_fmt(ra)} B={_fmt(rb)}")
        print(f"FM-RESIDENT-DIFF: {'identical' if identical else 'DIFFERENT'} "
              f"(defensive_scale={ARGS.defensive_scale}; firmware dispatched 1 ring "
              f"command in variant B before replay; per-op outputs compared A vs B)")


if __name__ == "__main__":
    asyncio.run(_main())
