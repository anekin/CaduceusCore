"""Long-sequence persistent-offset Func Model gate (todo 5, fm-hardening-phase10).

Implements proposal S1 from
``.omo/notepads/phase10-rtl-verification/func-model-verification-gap-report.md``:
a multi-layer chain scheduled through the host doorbell command ring with a
**persistent ring offset** (never reset per layer) must run correctly at
Func Model speed — the cumulative offset wraps, no descriptor is clobbered by
ring writes, every layer output matches golden, and a corrupted mid-chain
descriptor address (ISSUE-13D / BUG-RTL-SOC-008 class) is caught as a layer
output mismatch.

Design notes
------------
- The 28-block scaled chain fixture (``tests/test_soc_fm.py``) is reused for the
  manifest, per-op input cache, scaled INT4 weights, and the DIRECT bridge path
  (``_chain_run_block``) which serves as the per-op-golden-validated reference.
- The ring path schedules EVERY op of every layer as a doorbell ring command
  (``FuncModel.host_write_command`` + ``firmware.run_loop``) and never touches
  the doorbell between layers. The FuncModel firmware emulator ring is 16
  entries, so 11 layers x 19 commands = 208 commands wrap the offset 13 times
  (208 % 16 == 0).
- MMUL split (forced by firmware semantics, not by choice): the firmware
  dispatcher routes MMUL to ``tile_mmul``, which always applies an FP32 scale
  (``matmul_int4_per_block`` semantics); the chain's VRESID consumes the output
  buffer as INT32. The firmware emulator has no INT32 MMUL dispatch and the
  scheduling algorithm must not change, so the data-flow MMUL keeps using the
  direct bridge path (INT32, fixture semantics) while an additional genuine
  MMUL ring command is dispatched for every MMUL op with its output pointed at
  a scratch region. Every ring command is therefore really executed by the
  firmware dispatcher at the persistent offset.
- Per-layer final output: VCONV ring command (INT32 -> FP16) of the output
  buffer into a per-layer result region, same as the fixture.
- Failure injection: one mid-chain command's descriptor ADDRESS (the
  ``desc_addr`` field of the ring entry) is corrupted to the next descriptor
  slot, which holds a valid-but-wrong (SILU-shaped) descriptor — the silent
  wrong-op execution shape of BUG-RTL-SOC-008. The corresponding layer output
  must mismatch golden while earlier layers stay bit-identical.

No VCS, no scheduling-algorithm change, no new dependencies.
"""

import hashlib
import json
import os

import numpy as np

from cocotb_bridge import pack_int8_activation_tile_major
from engine.isa import OpCode
from func_model import FuncModel
from golden_executor import GoldenMXU, GoldenSFU, GoldenVector
from regmap import MXU, SFU, VECTOR
from address_space import contract_check
from command_ring import assert_desc_clear_of_used_regions

from tests import test_soc_fm as tsf

# ── Scenario constants ──────────────────────────────────────────────────
_NUM_LAYERS = 11            # 11 layers x 19 commands = 208 >= 200 (block 0 has 18)
_BASELINE_LAYERS = 3        # baseline characterization run length
_CORRUPT_LAYER = 5          # 0-based layer whose op14 VMUL descriptor address is corrupted
_CORRUPT_OP_IDX = 14        # VMUL gate*up

_DESC_BASE = 0x8071_0000    # descriptor pool: right after block 27, below scratch/results
_SCRATCH_MMUL_OUT = 0x8072_0000   # ring-MMUL FP32 output scratch (not part of layer data flow)
_SCRATCH_SCALE = 0x8072_4000      # ones-scale scratch for ring MMUL commands
_ACT_BASE = 0x8080_0000           # DRAM window end (results start here)

_ELEMS_FINAL = 2560         # final per-layer FP16 vector length (fixture convention)


def _expected_command_count(n_layers: int) -> int:
    """Ring commands per layer: 18 for layer 0 (no prev-block VCONV), 19 after."""
    if n_layers < 1:
        return 0
    return 18 + 19 * (n_layers - 1)


def _load_chain_context(n_blocks: int) -> dict:
    """Load manifest + per-op input cache + per-block scaled INT4 weights."""
    manifest_path = os.path.join(tsf._BLK0_VECTOR_DIR, "blk0_manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)
    cache = tsf._chain_build_cache(manifest)

    baseline_weights = {}
    for op in manifest["ops"]:
        if op["opcode"] != "MMUL":
            continue
        idx = op["idx"]
        dims = op["dimensions"]
        K_eff = min(dims.get("K", 0), 64)
        N_eff = min(dims.get("N", 0), 64)
        weight_size = (K_eff * N_eff + 1) // 2
        weight_full = tsf._blk0_read_hex(op["weight_hex"], 1)
        weight_bytes = weight_full[:weight_size]
        if len(weight_bytes) < weight_size:
            weight_bytes = weight_bytes + b"\x00" * (weight_size - len(weight_bytes))
        baseline_weights[idx] = weight_bytes

    scales = [0.90 + i * 0.01 for i in range(n_blocks)]
    block_weights = [
        {idx: tsf._chain_scale_int4_weights(w, scale)
         for idx, w in baseline_weights.items()}
        for scale in scales
    ]
    return {"manifest": manifest, "cache": cache, "block_weights": block_weights}


def _run_direct_layers(ctx: dict, n_layers: int) -> list:
    """Golden reference: run layers through the fixture's direct bridge path."""
    manifest = ctx["manifest"]
    model = FuncModel(dram_mb=256)
    outs = []
    prev_out_i32_addr = None
    for b in range(n_layers):
        block_base = tsf._CHAIN_BLOCK_BASE + b * tsf._CHAIN_BLOCK_STRIDE
        result_addr = tsf._chain_run_block(
            model, b, block_base, prev_out_i32_addr,
            ctx["block_weights"][b], manifest, ctx["cache"],
        )
        fp16 = np.frombuffer(
            tsf._chain_dram_read(model, result_addr, _ELEMS_FINAL * 2),
            dtype=np.float16,
        ).copy()
        outs.append(fp16)
        prev_out_i32_addr = block_base + manifest["sram_layout"]["output_buffer"]
    return outs


def _issue(model: FuncModel, opcode: int, desc_addr: int, k: int,
           ring_size: int, corrupt: bool = False):
    """Write one ring command at the persistent offset k % ring_size and drain it."""
    assert model.firmware.doorbell["host_tail"] == k % ring_size, (
        f"command {k}: ring offset reset? host_tail={model.firmware.doorbell['host_tail']}, "
        f"expected {k % ring_size}"
    )
    eff_addr = desc_addr + 64 if corrupt else desc_addr
    model.host_write_command(opcode, eff_addr)
    assert model.firmware.doorbell["host_tail"] == (k + 1) % ring_size
    results = model.firmware.run_loop(max_commands=1)
    assert len(results) == 1, f"command {k}: firmware consumed {len(results)} commands"
    assert results[0]["status"] == "done", f"command {k} failed: {results[0]}"
    assert model.firmware.doorbell["npu_head"] == (k + 1) % ring_size


def _run_ring_layers(ctx: dict, n_layers: int,
                     corrupt_layer: int | None = None,
                     corrupt_op_idx: int | None = None):
    """Schedule every op of every layer through the doorbell ring with a
    persistent offset. Returns (layer_outputs, total_commands, layer_start_offsets)."""
    manifest, cache = ctx["manifest"], ctx["cache"]
    output_buffer_rel = manifest["sram_layout"]["output_buffer"]
    fp16_tol = dict(tol_abs=2e-3, tol_rel=1e-2)

    # Descriptor pool must be disjoint from ring/completion/activation regions.
    contract_check(ring_entries=1024, desc_base=_DESC_BASE,
                   desc_count=_expected_command_count(n_layers), act_base=_ACT_BASE)
    assert_desc_clear_of_used_regions(desc_base=_DESC_BASE,
                                      desc_count=_expected_command_count(n_layers))

    # And from every per-layer block region (the BUG-RTL-SOC-008 layout class:
    # a descriptor pool inside live data regions silently corrupts op inputs).
    # Check ALL 28 fixture block regions (not just the scheduled layers): the
    # desc pool and scratch must be clear of the full 7 MB block span so the
    # 28-layer gate (Todo 11) cannot collide at any block.
    desc_end = _DESC_BASE + _expected_command_count(n_layers) * 64
    scratch_end = _SCRATCH_MMUL_OUT + 0x10000
    for blk in range(tsf._CHAIN_NUM_BLOCKS):
        block_base = tsf._CHAIN_BLOCK_BASE + blk * tsf._CHAIN_BLOCK_STRIDE
        block_end = block_base + tsf._CHAIN_BLOCK_STRIDE
        assert desc_end <= block_base or _DESC_BASE >= block_end, (
            f"descriptor pool [0x{_DESC_BASE:08x}, 0x{desc_end:08x}) overlaps "
            f"block {blk} [0x{block_base:08x}, 0x{block_end:08x})"
        )
        assert scratch_end <= block_base or _SCRATCH_MMUL_OUT >= block_end, (
            f"scratch [0x{_SCRATCH_MMUL_OUT:08x}, 0x{scratch_end:08x}) overlaps "
            f"block {blk} [0x{block_base:08x}, 0x{block_end:08x})"
        )
    assert _SCRATCH_MMUL_OUT >= desc_end, (
        f"scratch [0x{_SCRATCH_MMUL_OUT:08x}, ...) overlaps descriptor pool "
        f"[0x{_DESC_BASE:08x}, 0x{desc_end:08x})"
    )

    model = FuncModel(dram_mb=256)
    ring_size = model.firmware.ring_size
    assert ring_size == 16, "scenario assumes the FuncModel firmware 16-entry ring"

    total_cmds = 0
    layer_start_offsets = []
    layer_outs = []
    prev_out_i32_addr = None

    for blk in range(n_layers):
        # Persistent offset: doorbell counters must carry over from the
        # previous layer — never reset.
        layer_start_offsets.append(total_cmds % ring_size)
        assert model.firmware.doorbell["host_tail"] == total_cmds % ring_size
        assert model.firmware.doorbell["npu_head"] == model.firmware.doorbell["host_tail"]

        block_base = tsf._CHAIN_BLOCK_BASE + blk * tsf._CHAIN_BLOCK_STRIDE
        weights = ctx["block_weights"][blk]
        bridge = model.bridge

        for op in manifest["ops"]:
            idx = op["idx"]
            opcode = op["opcode"]
            label = f"blk{blk} op{idx:02d} {opcode}"
            i_addr = block_base + int(op["sram_input_addr"], 16)
            o_addr = block_base + int(op["sram_output_addr"], 16)

            if opcode == "MMUL":
                # ── data-flow MMUL: direct bridge path (INT32, fixture semantics) ──
                input_bytes, M_eff, K_eff, N_eff, _ = cache["mmul_inputs"][idx]
                act = np.frombuffer(input_bytes, dtype=np.int8).reshape(M_eff, K_eff)
                act_packed = pack_int8_activation_tile_major(input_bytes, M_eff, K_eff)
                tsf._chain_dram_write(model, i_addr, act_packed)
                tsf._chain_dram_write(model, block_base, weights[idx])
                bridge.handle("write", MXU.BASE + MXU.CTRL, 0)
                bridge.handle("write", MXU.BASE + MXU.I_ADDR, i_addr)
                bridge.handle("write", MXU.BASE + MXU.W_ADDR, block_base)
                bridge.handle("write", MXU.BASE + MXU.O_ADDR, o_addr)
                bridge.handle("write", MXU.BASE + MXU.SCALE_ADDR, 0)
                dim0 = (M_eff & 0xFFFF) | ((K_eff & 0xFFFF) << 16)
                bridge.handle("write", MXU.BASE + MXU.DIM0, dim0)
                bridge.handle("write", MXU.BASE + MXU.DIM1, N_eff & 0xFFFF)
                bridge.handle("write", MXU.BASE + MXU.CMD, 1)
                tsf._blk0_assert_status(bridge, MXU.BASE, 2, label)
                out_arr = np.frombuffer(
                    tsf._chain_dram_read(model, o_addr, M_eff * N_eff * 4),
                    dtype=np.int32,
                ).reshape(M_eff, N_eff)
                golden = GoldenMXU().matmul_int32(
                    act, np.frombuffer(weights[idx], dtype=np.uint8),
                    M_eff, K_eff, N_eff,
                )
                assert np.allclose(out_arr, golden, rtol=1e-5), f"{label}: MMUL mismatch"

                # ── genuine MMUL ring command; output diverted to scratch ──
                desc_addr = _DESC_BASE + total_cmds * 64
                model.host_write_data(_SCRATCH_SCALE, np.ones(N_eff, dtype=np.float32))
                model.host_write_descriptor(
                    desc_addr,
                    input_addr=i_addr, weight_addr=block_base,
                    output_addr=_SCRATCH_MMUL_OUT, scale_addr=_SCRATCH_SCALE,
                    input_size=M_eff * K_eff, weight_size=(K_eff * N_eff + 1) // 2,
                    output_size=M_eff * N_eff * 4, scale_size=N_eff * 4,
                    M=M_eff, K=K_eff, N=N_eff,
                )
                _issue(model, OpCode.MMUL, desc_addr, total_cmds, ring_size)
                total_cmds += 1

            elif opcode in ("RMSNORM", "SOFTMAX", "ROPE", "SILU"):
                input_bytes, elements, head_dim = cache["sfu_inputs"][idx]
                tsf._chain_dram_write(model, i_addr, input_bytes)
                desc_addr = _DESC_BASE + total_cmds * 64
                model.host_write_descriptor(
                    desc_addr, input_addr=i_addr, output_addr=o_addr,
                    input_size=elements,
                )
                _issue(model, getattr(OpCode, opcode), desc_addr, total_cmds, ring_size)
                total_cmds += 1

                out_arr = np.frombuffer(
                    tsf._chain_dram_read(model, o_addr, elements * 2),
                    dtype=np.float16,
                ).astype(np.float32)
                sfu = GoldenSFU()
                inp = np.frombuffer(input_bytes, dtype=np.float16).astype(np.float32)
                if opcode == "SOFTMAX":
                    golden = sfu.softmax_hw(inp)
                elif opcode == "RMSNORM":
                    golden = sfu.rmsnorm_hw(inp)
                elif opcode == "SILU":
                    golden = sfu.silu_hw(inp)
                else:  # ROPE — mirror the fixture's q/k split fallback
                    hd = head_dim if head_dim else max(elements // 4, 2)
                    k_len = 2 * hd
                    q_len = elements - k_len
                    if q_len <= 0:
                        q_len = elements // 2
                        k_len = elements - q_len
                    q_in = inp[:q_len]
                    k_in = inp[q_len:elements]
                    nq = max(1, q_len // hd) if hd else 1
                    q_out, k_out = sfu.rope_hw(
                        q_in, k_in,
                        position=op["dimensions"].get("position", 0),
                        num_heads=nq, head_dim=hd,
                    )
                    golden = np.zeros(elements, dtype=np.float32)
                    golden[:q_len] = q_out
                    golden[q_len:elements] = k_out
                cmp = GoldenSFU.compare_hw_vs_ref(out_arr, golden, **fp16_tol)
                assert cmp["within_tolerance"], (
                    f"{label}: SFU mismatch max_abs={cmp['max_abs_err']:.2e} "
                    f"max_rel={cmp['max_rel_err']:.2e}"
                )

            elif opcode in ("VMUL", "VRESID"):
                elements = op["dimensions"]["elements"]
                b_addr = block_base + output_buffer_rel
                corrupt_this = (blk == corrupt_layer and idx == corrupt_op_idx)

                if opcode == "VRESID" and idx == 16 and blk > 0:
                    # prev-block residual: VCONV ring command (INT32 -> FP16) first
                    desc_addr = _DESC_BASE + total_cmds * 64
                    model.host_write_descriptor(
                        desc_addr,
                        input_addr=prev_out_i32_addr, weight_addr=prev_out_i32_addr,
                        output_addr=i_addr, input_size=elements,
                    )
                    _issue(model, OpCode.VCONV, desc_addr, total_cmds, ring_size)
                    total_cmds += 1
                    a_bytes = tsf._chain_dram_read(model, i_addr, elements * 2)
                    b_bytes = tsf._chain_dram_read(model, b_addr, elements * 4)
                else:
                    a_bytes, b_bytes, _, _, _ = cache["vector_inputs"][idx]

                tsf._chain_dram_write(model, i_addr, a_bytes)
                tsf._chain_dram_write(model, b_addr, b_bytes)
                desc_addr = _DESC_BASE + total_cmds * 64
                model.host_write_descriptor(
                    desc_addr,
                    input_addr=i_addr, weight_addr=b_addr, output_addr=o_addr,
                    input_size=elements,
                )
                if corrupt_this:
                    # Fault injection (ISSUE-13D shape): the corrupted command
                    # reads the NEXT descriptor slot, which holds a valid-but-wrong
                    # SILU-shaped descriptor (op13's descriptor content, since op13
                    # and op14 share the 0x30000 input scratch). Plant it before the
                    # corrupted command executes.
                    model.host_write_descriptor(
                        desc_addr + 64,
                        input_addr=i_addr,
                        output_addr=block_base + int("0x32000", 16),
                        input_size=elements,
                    )
                _issue(model, OpCode.VMUL if opcode == "VMUL" else OpCode.VRESID,
                       desc_addr, total_cmds, ring_size, corrupt=corrupt_this)
                total_cmds += 1

                if corrupt_this:
                    # Deliberately corrupted op: skip the per-op golden — the
                    # layer-output divergence is asserted by the caller.
                    continue

                out_arr = np.frombuffer(
                    tsf._chain_dram_read(model, o_addr, elements * 4), dtype=np.int32,
                )
                vec = GoldenVector()
                if opcode == "VMUL":
                    a_op = np.frombuffer(a_bytes, dtype=np.int32)
                    b_op = np.frombuffer(b_bytes, dtype=np.int32)
                    golden = vec.mul(a_op, b_op)
                else:
                    a_op = np.frombuffer(a_bytes, dtype=np.float16).astype(np.float32)
                    b_op = np.frombuffer(b_bytes, dtype=np.int32)
                    golden = vec.residual_add(a_op, b_op)
                assert np.array_equal(out_arr, golden), (
                    f"{label}: Vector mismatch at indices {np.where(out_arr != golden)[0]}"
                )
            else:
                raise ValueError(f"{label}: unsupported opcode {opcode}")

        # ── final per-layer observable: VCONV ring command → result region ──
        final_out_addr = block_base + output_buffer_rel
        result_addr = tsf._CHAIN_RESULT_BASE + blk * tsf._CHAIN_RESULT_STRIDE
        desc_addr = _DESC_BASE + total_cmds * 64
        model.host_write_descriptor(
            desc_addr,
            input_addr=final_out_addr, weight_addr=final_out_addr,
            output_addr=result_addr, input_size=_ELEMS_FINAL,
        )
        _issue(model, OpCode.VCONV, desc_addr, total_cmds, ring_size)
        total_cmds += 1

        fp16 = np.frombuffer(
            tsf._chain_dram_read(model, result_addr, _ELEMS_FINAL * 2),
            dtype=np.float16,
        ).copy()
        layer_outs.append(fp16)
        prev_out_i32_addr = final_out_addr

    return layer_outs, total_cmds, layer_start_offsets


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    a64 = a.astype(np.float64)
    b64 = b.astype(np.float64)
    na, nb = np.linalg.norm(a64), np.linalg.norm(b64)
    if na == 0.0 or nb == 0.0:
        raise AssertionError(f"zero-norm layer output (cosine undefined): |a|={na}, |b|={nb}")
    return float(np.dot(a64, b64) / (na * nb))


# ── Baseline characterization ───────────────────────────────────────────

# MD5 fingerprints of the first 3 direct-chain layer outputs (FP16, 2560 elems),
# captured 2026-08-23 — pins the current scaled-chain behavior (tests-after).
_PINNED_FINGERPRINTS = [
    "13dcec82248de5ca27a5c8d588792693",
    "129bef651ca9c5f18279defd16ec44b5",
    "111aa5d8b5bd63a7a1cba0087e637d0e",
]


def test_scaled_chain_baseline_pinned():
    """Baseline characterization: pin the current 3-layer scaled-chain behavior.

    - Deterministic across runs (same inputs, same fingerprints).
    - Fingerprints equal the pinned values captured at implementation time,
      so a future behavioral drift in the chain fails here.
    - Per-layer fingerprints are distinct (non-trivial data, anti-vacuous).
    """
    ctx = _load_chain_context(_BASELINE_LAYERS)
    fp_a = [hashlib.md5(o.tobytes()).hexdigest()
            for o in _run_direct_layers(ctx, _BASELINE_LAYERS)]
    fp_b = [hashlib.md5(o.tobytes()).hexdigest()
            for o in _run_direct_layers(ctx, _BASELINE_LAYERS)]
    assert fp_a == fp_b, "direct chain must be deterministic across identical runs"
    assert fp_a == _PINNED_FINGERPRINTS, (
        f"baseline pin broken: chain behavior changed\n got={fp_a}\n want={_PINNED_FINGERPRINTS}"
    )
    assert len(set(fp_a)) == _BASELINE_LAYERS, "per-layer outputs must be distinct"


# ── Main gate ───────────────────────────────────────────────────────────


def test_multi_layer_persistent_offset():
    """>=200 ring commands across 11 layers at a persistent (never-reset) ring offset.

    - Every op is scheduled through the doorbell ring and executed by the
      firmware dispatcher; the ring offset accumulates across layers and wraps
      modulo the 16-entry ring (208 commands -> 13 full wraps).
    - Each layer output is bit-identical to the direct-path golden reference.
    - final_cos (cosine similarity of the last layer output vs golden) is
      asserted numerically to be >= 0.999.
    - Failure injection: the layer-5 VMUL command's descriptor address is
      corrupted by one slot (ISSUE-13D shape) -> layer 5 output must mismatch
      golden while layers 0-4 stay bit-identical.
    """
    ctx = _load_chain_context(_NUM_LAYERS)

    golden = _run_direct_layers(ctx, _NUM_LAYERS)

    ring_outs, total_cmds, layer_offsets = _run_ring_layers(ctx, _NUM_LAYERS)
    assert total_cmds >= 200, f"expected >=200 ring commands, got {total_cmds}"
    assert total_cmds == _expected_command_count(_NUM_LAYERS)

    for layer in range(_NUM_LAYERS):
        assert np.array_equal(ring_outs[layer], golden[layer]), (
            f"layer {layer}: ring-path output differs from golden"
        )

    final_cos = _cosine(ring_outs[-1], golden[-1])
    print(f"final_cos={final_cos:.9f}")
    assert final_cos >= 0.999, f"final layer cosine too low: {final_cos:.9f}"

    # Cumulative offset must advance across layers and wrap to 0 after 208 cmds.
    assert len(set(layer_offsets)) > 1, "ring offset must advance across layers (not reset)"
    # 208 % 16 == 0: after the full schedule the ring has wrapped exactly.
    assert total_cmds % 16 == 0, "scenario must end on an exact ring wrap"

    # ── Failure injection: corrupt a mid-chain descriptor address ──
    corrupt_outs, corrupt_cmds, _ = _run_ring_layers(
        ctx, _NUM_LAYERS, corrupt_layer=_CORRUPT_LAYER, corrupt_op_idx=_CORRUPT_OP_IDX,
    )
    assert corrupt_cmds == total_cmds, "corrupted schedule must issue the same command count"
    for layer in range(_CORRUPT_LAYER):
        assert np.array_equal(corrupt_outs[layer], golden[layer]), (
            f"isolation broken: layer {layer} changed after corrupting layer {_CORRUPT_LAYER}"
        )
    assert not np.array_equal(corrupt_outs[_CORRUPT_LAYER], golden[_CORRUPT_LAYER]), (
        f"failure injection not caught: layer {_CORRUPT_LAYER} output still matches golden "
        f"after descriptor-address corruption"
    )
