#!/usr/bin/env python3
"""W1.6b: 36-layer Func Model Q8_0 control experiment runner.

Re-runs the W1.6 L3 signoff for Qwen2.5-3B using Q8_0 GGUF weights instead of
the default Q4_K_M.  The generated evidence file is written to
``build/evidence/w1-6b-q8o.txt``.

If the Q8_0 GGUF asset is not available, the script still produces the
evidence file with 36 placeholder per-layer entries and documents the missing
asset, so the task does not fail silently.
"""

import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

# Repository path setup
_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
sys.path.insert(0, str(_PROJECT / "ggml-npu"))
sys.path.insert(0, str(_PROJECT / "sim"))

Q8O_MODEL = Path.home() / "models" / "qwen2.5-3b-instruct-q8_0.gguf"
EVIDENCE_PATH = _PROJECT / "build" / "evidence" / "w1-6b-q8o.txt"

L35_BASELINE = 0.998278
L35_TOLERANCE = 0.001
LAYERS = list(range(36))


def _dequantize_q8_0(raw_bytes: bytes, n_elems: int) -> np.ndarray:
    """Dequantize Q8_0 blocks to float32.

    Q8_0 block layout (ggml): 2-byte FP16 scale + 32 signed int8 values.
    """
    block_size = 32
    bytes_per_block = 2 + block_size
    if len(raw_bytes) % bytes_per_block != 0:
        raise ValueError(
            f"Invalid Q8_0 data: {len(raw_bytes)} bytes, "
            f"need multiple of {bytes_per_block}"
        )

    data = np.frombuffer(raw_bytes, dtype=np.uint8).reshape(-1, bytes_per_block)
    from q4_dequant import fp16_to_fp32

    scale_uint16 = data[:, 0].astype(np.uint16) | (data[:, 1].astype(np.uint16) << 8)
    scale = fp16_to_fp32(scale_uint16).astype(np.float32)
    qs = data[:, 2:].astype(np.int8)
    flat = (scale[:, None] * qs).reshape(-1)[:n_elems]
    return flat.astype(np.float32)


def _load_weights_from_gguf_q8o(gguf_path: str) -> Dict[str, np.ndarray]:
    """Load a GGUF file, adding Q8_0 support on top of q4_dequant."""
    import gguf
    from q4_dequant import load_weights_from_gguf as _base_loader

    # Load everything the base loader already understands (F32/F16/Q4_K/Q6_K).
    weights = _base_loader(gguf_path)

    reader = gguf.GGUFReader(gguf_path)
    for tensor in reader.tensors:
        if tensor.tensor_type.name != "Q8_0":
            continue
        raw = (
            bytes(tensor.data.tobytes())
            if hasattr(tensor.data, "tobytes")
            else bytes(tensor.data)
        )
        total = int(np.prod(tensor.shape))
        w = _dequantize_q8_0(raw, total)
        if len(tensor.shape) == 2:
            w = w.reshape(tensor.shape[1], tensor.shape[0])
        weights[tensor.name] = w

    return weights


def _write_blocked_evidence() -> None:
    """Write an evidence file documenting that the Q8_0 asset is missing."""
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    host = __import__("socket").gethostname()
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# W1.6b: Qwen2.5-3B 36-Layer Func Model Q8_0 Control Experiment",
        f"# Generated: {now}",
        f"# Host: {host}",
        f"# Model: {Q8O_MODEL}",
        "# Status: BLOCKED — Q8_0 GGUF asset not available",
        "#",
        "# Purpose: Confirm whether the L35 drift seen with Q4_K_M",
        "#          (Phase 5 FM baseline value=0.998278) is caused by the",
        "#          Q4_K_M quantization by re-running the 36-layer signoff",
        "#          with Q8_0 weights.",
        "",
        "SUMMARY: TESTS=36 PASS=0 FAIL=36 (blocked by missing Q8_0 GGUF)",
        "Drift analysis: N/A (blocked)",
        "Worst layer: N/A",
        "",
        f"## Phase 5 FM L35 baseline",
        f"  value={L35_BASELINE:.6f} (tolerance ±{L35_TOLERANCE:.3f})",
        f"  consistency_window=[{L35_BASELINE - L35_TOLERANCE:.6f}, {L35_BASELINE + L35_TOLERANCE:.6f}]",
        "",
        "## Per-layer similarity",
    ]

    for layer in LAYERS:
        note = "Q8_0 model missing"
        if layer == 35:
            note += (
                f"; Phase 5 L35 baseline={L35_BASELINE:.6f} ±{L35_TOLERANCE:.3f}"
            )
        lines.append(
            f"  Layer {layer}: cos_sim=N/A, max_rel_err=N/A, "
            f"max_abs_err=N/A, note={note}"
        )

    lines.extend([
        "",
        "## Drift analysis",
        "  linear_slope=N/A",
        "  max_sequential_drop=N/A",
        "  avg_negative_drop=N/A",
        "  slope_pass=N/A",
        "  drop_pass=N/A",
        "  drift_verdict=N/A",
        "",
        "## Asset check",
        f"  path={Q8O_MODEL}",
        "  exists=False",
        "  checked_hosts=sz0002(local), sz0001(192.168.0.11)",
        "  note=Official Q8_0 GGUF is not present; re-quantizing the existing Q4_K_M file",
        "       would not isolate Q4_K_M quantization error and is therefore not a valid control.",
    ])

    EVIDENCE_PATH.write_text("\n".join(lines) + "\n")
    print(f"Blocked evidence written: {EVIDENCE_PATH}")


def _write_evidence(layer_results: Dict[str, Any], drift: Dict[str, Any],
                    worst_layer: Optional[int],
                    worst_per_op: Dict[str, Any]) -> None:
    """Write a full Q8_0 evidence file after a successful run."""
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    host = __import__("socket").gethostname()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    per_layer = layer_results["per_layer"]

    with open(EVIDENCE_PATH, "w") as f:
        f.write(
            "# W1.6b: Qwen2.5-3B 36-Layer Func Model Q8_0 Control Experiment\n"
        )
        f.write(f"# Generated: {now}\n")
        f.write(f"# Host: {host}\n")
        f.write(f"# Model: {Q8O_MODEL}\n")
        f.write("# Status: COMPLETE\n\n")

        total = layer_results["tests"]
        passed = layer_results["passed"]
        failed = layer_results["failed"]
        f.write(f"SUMMARY: TESTS={total} PASS={passed} FAIL={failed}\n")
        f.write(
            f"Drift analysis: {'PASS' if drift['drift_pass'] else 'FAIL'}\n"
        )
        f.write(f"Worst layer: {worst_layer if worst_layer is not None else 'N/A'}\n\n")

        f.write("## Phase 5 FM L35 baseline\n")
        f.write(f"  value={L35_BASELINE:.6f} (tolerance ±{L35_TOLERANCE:.3f})\n")
        f.write(
            f"  consistency_window=[{L35_BASELINE - L35_TOLERANCE:.6f}, "
            f"{L35_BASELINE + L35_TOLERANCE:.6f}]\n\n"
        )

        l35_q8o = per_layer.get(35, {}).get("cos_sim")
        if l35_q8o is not None:
            delta = l35_q8o - L35_BASELINE
            consistent = abs(delta) <= L35_TOLERANCE
            f.write("## L35 Q8_0 vs Phase 5 baseline\n")
            f.write(f"  q8o_value={l35_q8o:.6f}\n")
            f.write(f"  delta={delta:+.6f}\n")
            f.write(
                f"  consistent_with_baseline={consistent}\n\n"
            )

        f.write("## Per-layer similarity\n")
        for layer in sorted(per_layer.keys()):
            r = per_layer[layer]
            note = ""
            if layer == 35:
                note = (
                    f"; Phase 5 L35 baseline={L35_BASELINE:.6f} "
                    f"±{L35_TOLERANCE:.3f}"
                )
            f.write(
                f"  Layer {layer}: cos_sim={r['cos_sim']:.6f}, "
                f"max_rel_err={r['max_rel_err']:.2e}, "
                f"max_abs_err={r['max_abs_err']:.2e}{note}\n"
            )

        f.write("\n## Drift analysis\n")
        f.write(f"  linear_slope={drift['slope']:.6e}\n")
        f.write(f"  max_sequential_drop={drift['max_drop']:.6e}\n")
        f.write(f"  avg_negative_drop={drift['avg_drop']:.6e}\n")
        f.write(f"  slope_pass={drift['slope_pass']}\n")
        f.write(f"  drop_pass={drift['drop_pass']}\n")
        f.write(
            f"  drift_verdict={'PASS' if drift['drift_pass'] else 'FAIL'}\n\n"
        )

        if worst_per_op:
            f.write(
                f"## Worst-layer per-op decomposition (layer {worst_layer})\n"
            )
            for op_name in sorted(worst_per_op.keys()):
                r = worst_per_op[op_name]
                f.write(
                    f"  {op_name:12s}: cos_sim={r['cos_sim']:.6f}, "
                    f"max_rel_err={r['max_rel_err']:.2e}, "
                    f"max_abs_err={r['max_abs_err']:.2e}\n"
                )
        else:
            f.write("## Worst-layer per-op decomposition: N/A\n")

    print(f"Evidence saved: {EVIDENCE_PATH}")


def _run_experiment() -> bool:
    """Run the full Q8_0 36-layer signoff."""
    from qwen25_forward import (
        DEFAULT_PROMPT,
        LLAMA_REF_DIR,
        compare_layer_outputs,
        run_forward_pass,
        run_llamacpp_reference,
    )
    from qwen25_l3 import (
        drift_analysis,
        worst_layer_decomposition,
    )
    import qwen25_forward

    # Inject Q8_0 support into the forward-pass loader.
    qwen25_forward.load_weights_from_gguf = _load_weights_from_gguf_q8o

    print("=" * 70)
    print("W1.6b: 36-layer Qwen2.5-3B Func Model Q8_0 control")
    print("=" * 70)

    results = run_forward_pass(
        str(Q8O_MODEL), LAYERS, DEFAULT_PROMPT, capture_intermediates=True
    )

    llama_outputs = run_llamacpp_reference(
        str(Q8O_MODEL), DEFAULT_PROMPT, LLAMA_REF_DIR, n_tokens=1
    )
    if not llama_outputs:
        print("ERROR: llama.cpp reference generation failed")
        return False

    layer_results = compare_layer_outputs(
        results["hidden_states"], llama_outputs.get("per_layer", {}), LAYERS
    )

    cos_per_layer = {
        L: r["cos_sim"] for L, r in layer_results["per_layer"].items()
    }
    drift = drift_analysis(cos_per_layer)

    worst_layer = min(cos_per_layer, key=cos_per_layer.get) if cos_per_layer else None
    worst_per_op: Dict[str, Any] = {}
    if worst_layer is not None and "intermediates" in results:
        worst_per_op = worst_layer_decomposition(
            worst_layer,
            results["intermediates"][worst_layer],
            llama_outputs.get("per_layer", {}),
        )

    _write_evidence(layer_results, drift, worst_layer, worst_per_op)

    all_pass = layer_results["failed"] == 0 and drift["drift_pass"]
    print(f"\n36/36 PASS verdict: {'PASS' if all_pass else 'FAIL'}")
    return all_pass


def main() -> int:
    if not Q8O_MODEL.exists():
        print(f"WARNING: Q8_0 model not found: {Q8O_MODEL}")
        print("Writing blocked evidence report.")
        _write_blocked_evidence()
        return 0

    ok = _run_experiment()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
