#!/usr/bin/env python3
"""Verify op05/op07 golden correctness using Func Model (GoldenMXU)."""

import json
import sys
from pathlib import Path
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sim"))
from golden_executor import GoldenMXU, GoldenExecutor
from engine.isa import NPUInstruction, OpCode

VECDIR = REPO_ROOT / "rtl" / "test_vectors" / "qwen_blk0"


# ══════════════════════════════════════════════════════════════════════
# Hex file readers (matching gen_blk0_golden.py _write_hex format)
# ══════════════════════════════════════════════════════════════════════

def read_hex_int8(path: Path) -> np.ndarray:
    """Read hex file (2 hex digits per line) → INT8 array."""
    with open(path) as f:
        vals = [int(line.strip(), 16) for line in f if line.strip()]
    return np.array(vals, dtype=np.uint8).view(np.int8)


def read_hex_uint8(path: Path) -> np.ndarray:
    """Read hex file (2 hex digits per line) → uint8 array (for packed INT4)."""
    with open(path) as f:
        vals = [int(line.strip(), 16) for line in f if line.strip()]
    return np.array(vals, dtype=np.uint8)


def read_hex_int32(path: Path) -> np.ndarray:
    """Read hex file (8 hex digits per line) → INT32 array."""
    with open(path) as f:
        vals = [int(line.strip(), 16) for line in f if line.strip()]
    return np.array(vals, dtype=np.uint32).view(np.int32)


def read_hex_float16(path: Path) -> np.ndarray:
    """Read hex file (4 hex digits per line) → float16 array."""
    import struct
    with open(path) as f:
        vals = [int(line.strip(), 16) for line in f if line.strip()]
    raw = b"".join(struct.pack("<H", v) for v in vals)
    return np.frombuffer(raw, dtype=np.float16).copy()


# ══════════════════════════════════════════════════════════════════════
# Compare helpers
# ══════════════════════════════════════════════════════════════════════

def compare_int32(got: np.ndarray, expected: np.ndarray,
                  name: str, show_first: int = 8) -> bool:
    """Compare two INT32 arrays.  Returns True when exact match."""
    got_f = got.flatten()
    exp_f = expected.flatten()
    if len(got_f) != len(exp_f):
        print(f"  SIZE MISMATCH: got {len(got_f)}, expected {len(exp_f)}")
        return False

    if np.array_equal(got_f, exp_f):
        # Print first few values for visual confirmation
        print(f"  First {show_first} values (got):  {list(got_f[:show_first])}")
        print(f"  First {show_first} values (gold): {list(exp_f[:show_first])}")
        print(f"  Result: ALL {len(got_f)} VALUES MATCH — PASS")
        return True

    mismatches = np.where(got_f != exp_f)[0]
    print(f"  First {show_first} values (got):  {list(got_f[:show_first])}")
    print(f"  First {show_first} values (gold): {list(exp_f[:show_first])}")
    print(f"  MISMATCHES: {len(mismatches)} / {len(got_f)}")
    for idx in mismatches[:10]:
        delta = int(got_f[idx]) - int(exp_f[idx])
        print(f"    [{idx}] got={got_f[idx]:12d} (0x{got_f[idx] & 0xFFFFFFFF:08x}), "
              f"exp={exp_f[idx]:12d} (0x{exp_f[idx] & 0xFFFFFFFF:08x}), "
              f"delta={delta:+d}")
    return False


def compare_float16(got: np.ndarray, expected: np.ndarray,
                    name: str, show_first: int = 8,
                    atol: float = 2e-3, rtol: float = 1e-2) -> bool:
    """Compare two float16 arrays with tolerances.  Returns True when close enough."""
    got_f = got.flatten().astype(np.float64)
    exp_f = expected.flatten().astype(np.float64)
    if len(got_f) != len(exp_f):
        print(f"  SIZE MISMATCH: got {len(got_f)}, expected {len(exp_f)}")
        return False

    abs_diff = np.abs(got_f - exp_f)
    rel_diff = abs_diff / (np.abs(exp_f) + 1e-12)
    max_abs = float(np.max(abs_diff))
    max_rel = float(np.max(rel_diff))
    any_bad = np.any((abs_diff > atol) & (rel_diff > rtol))

    print(f"  First {show_first} values (got):  {[f'{v:.6f}' for v in got_f[:show_first]]}")
    print(f"  First {show_first} values (gold): {[f'{v:.6f}' for v in exp_f[:show_first]]}")
    print(f"  max_abs_err={max_abs:.6f}, max_rel_err={max_rel:.6f}")

    if not any_bad:
        print(f"  Result: ALL {len(got_f)} VALUES WITHIN TOLERANCE (atol={atol}, rtol={rtol}) — PASS")
        return True

    # Find worst offenders
    bad = np.where((abs_diff > atol) & (rel_diff > rtol))[0]
    print(f"  OUT OF TOLERANCE: {len(bad)} / {len(got_f)}")
    for idx in bad[:10]:
        print(f"    [{idx}] got={got_f[idx]:.6f}, exp={exp_f[idx]:.6f}, "
              f"abs={abs_diff[idx]:.6f}, rel={rel_diff[idx]:.6f}")
    return False


# ══════════════════════════════════════════════════════════════════════
# Per-op verification
# ══════════════════════════════════════════════════════════════════════

def verify_mmul_op(mxu: GoldenMXU, op_name: str,
                   M: int, K: int, N: int,
                   input_path: Path, weight_path: Path,
                   golden_path: Path) -> bool:
    """Verify one MMUL op by calling matmul_int32 and comparing against golden."""
    print(f"\n── {op_name} (M={M}, K={K}, N={N}) ──")

    # Load hex files
    act_int8 = read_hex_int8(input_path)
    wgt_packed = read_hex_uint8(weight_path)
    golden = read_hex_int32(golden_path)

    # Basic sanity checks
    expected_act_size = M * K
    expected_wgt_size = (K * N + 1) // 2
    expected_golden_size = M * N

    print(f"  Input:    {act_int8.size} INT8 values (expect {expected_act_size})")
    print(f"  Weight:   {wgt_packed.size} packed INT4 bytes (expect {expected_wgt_size})")
    print(f"  Golden:   {golden.size} INT32 values (expect {expected_golden_size})")

    if act_int8.size != expected_act_size:
        print(f"  WARNING: input size mismatch ({act_int8.size} vs {expected_act_size})")
    if wgt_packed.size != expected_wgt_size:
        print(f"  WARNING: weight size mismatch ({wgt_packed.size} vs {expected_wgt_size})")
    if golden.size != expected_golden_size:
        print(f"  WARNING: golden size mismatch ({golden.size} vs {expected_golden_size})")

    # Compute via Func Model
    result = mxu.matmul_int32(act_int8, wgt_packed, M, K, N)
    result_flat = result.flatten()

    # Compare
    ok = compare_int32(result_flat, golden, op_name)
    return ok


def verify_rmsnorm_op(executor: GoldenExecutor, op_name: str,
                      elements: int,
                      sram_input_addr: int, sram_output_addr: int,
                      input_path: Path, golden_path: Path) -> bool:
    """Verify one RMSNORM op by writing input to SRAM, step(), reading back."""
    print(f"\n── {op_name} (elements={elements}) ──")

    # Load hex files
    inp_fp16 = read_hex_float16(input_path)
    golden_fp16 = read_hex_float16(golden_path)

    print(f"  Input:    {inp_fp16.size} FP16 values (expect {elements})")
    print(f"  Golden:   {golden_fp16.size} FP16 values (expect {elements})")

    if inp_fp16.size != elements:
        print(f"  WARNING: input size mismatch ({inp_fp16.size} vs {elements})")
    if golden_fp16.size != elements:
        print(f"  WARNING: golden size mismatch ({golden_fp16.size} vs {elements})")

    # Write input to SRAM
    executor.sram.write_float16(sram_input_addr, inp_fp16)

    # Create and execute RMSNORM instruction
    instr = NPUInstruction(OpCode.RMSNORM, {
        "sa": sram_input_addr,
        "da": sram_output_addr,
        "elements": elements,
    }, comment=f"{op_name} elements={elements}")
    executor.step(instr)

    # Read output from SRAM
    result = executor.sram.read_float16(sram_output_addr, elements)

    # Compare with tolerance (SFU uses fixed-point approximation)
    ok = compare_float16(result, golden_fp16, op_name, atol=2e-3, rtol=1e-2)
    return ok


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    # Load manifest
    manifest_path = VECDIR / "blk0_manifest.json"
    with open(manifest_path) as f:
        manifest = json.load(f)

    ops = manifest["ops"]
    mxu = GoldenMXU()

    # ── op05: attn_score MMUL ──────────────────────────────────────
    op5 = ops[5]
    d5 = op5["dimensions"]
    ok5 = verify_mmul_op(
        mxu, op5["name"],
        d5["M"], d5["K"], d5["N"],
        VECDIR / op5["input_hex"],
        VECDIR / op5["weight_hex"],
        VECDIR / op5["golden_output_hex"],
    )

    # ── op07: attn_weight MMUL ────────────────────────────────────
    op7 = ops[7]
    d7 = op7["dimensions"]
    ok7 = verify_mmul_op(
        mxu, op7["name"],
        d7["M"], d7["K"], d7["N"],
        VECDIR / op7["input_hex"],
        VECDIR / op7["weight_hex"],
        VECDIR / op7["golden_output_hex"],
    )

    # ── Overall verdict ──────────────────────────────────────────
    all_mmul_ok = ok5 and ok7
    print(f"\n{'='*60}")
    print(f"  op05 (attn_score MMUL):  {'PASS' if ok5 else 'FAIL'}")
    print(f"  op07 (attn_weight MMUL): {'PASS' if ok7 else 'FAIL'}")
    print(f"  MMUL VERDICT: {'ALL PASS' if all_mmul_ok else 'SOME FAILED'}")
    print(f"{'='*60}")

    if not all_mmul_ok:
        return 1 if not ok5 else 1

    # ── op10: RMSNORM post-attn (only if MMUL ops pass) ───────────
    print("\n── MMUL ops PASSED — proceeding to op10 RMSNORM check ──")
    op10 = ops[10]
    d10 = op10["dimensions"]
    sram_in = int(op10["sram_input_addr"], 16)
    sram_out = int(op10["sram_output_addr"], 16)

    # Non-MMUL manifest entries don't have input_hex/golden_output_hex keys.
    # The hex filenames are hardcoded in gen_blk0_golden.py (not derived from manifest name).
    input_hex = "op10_rmsnorm_post_input.hex"
    golden_hex = "op10_rmsnorm_post_golden.hex"

    executor = GoldenExecutor()
    ok10 = verify_rmsnorm_op(
        executor, op10["name"],
        d10["elements"],
        sram_in, sram_out,
        VECDIR / input_hex,
        VECDIR / golden_hex,
    )

    print(f"\n{'='*60}")
    print(f"  op10 (RMSNORM post-attn): {'PASS' if ok10 else 'FAIL'}")
    print(f"  FINAL VERDICT: {'ALL PASS' if ok10 else 'RMSNORM FAIL'}")
    print(f"{'='*60}")

    return 0 if ok10 else 1


if __name__ == "__main__":
    sys.exit(main())
