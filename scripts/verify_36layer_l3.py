#!/usr/bin/env python3
"""
W1.6: Standalone L3 signoff script for Qwen2.5-3B 36-layer Func Model.

Runs all 36 layers, compares against llama.cpp at checkpoints L0/L10/L20/L35,
performs drift analysis, and decomposes the worst layer into per-op cos_sim.

Usage:
    PYTHONPATH=sim python3 scripts/verify_36layer_l3.py
"""

import os
import sys
from pathlib import Path

_PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT / "sim"))

from qwen25_forward import (  # noqa: E402
    DEFAULT_L3_GOLDEN_DIR,
    DEFAULT_MODEL_PATH,
    DEFAULT_PROMPT,
    EVIDENCE_DIR,
    FALLBACK_MODEL_PATH,
    LLAMA_REF_DIR,
    compare_layer_outputs,
    run_forward_pass,
    run_llamacpp_reference,
    save_golden_npz,
)
from qwen25_l3 import (  # noqa: E402
    drift_analysis,
    worst_layer_decomposition,
    write_l3_evidence,
)


def verify_l3_signoff(model_path: str = DEFAULT_MODEL_PATH,
                      prompt: str = DEFAULT_PROMPT,
                      golden_dir: Path = DEFAULT_L3_GOLDEN_DIR,
                      evidence_path: Path = EVIDENCE_DIR / "w1-6-fm-l3-signoff.txt",
                      skip_llamacpp: bool = False) -> bool:
    """Run the full 36-layer L3 signoff flow."""
    if not os.path.exists(model_path):
        if os.path.exists(FALLBACK_MODEL_PATH):
            print(f"Model {model_path} not found, using fallback: {FALLBACK_MODEL_PATH}")
            model_path = FALLBACK_MODEL_PATH
        else:
            raise FileNotFoundError(f"GGUF model not found: {model_path}")

    print("=" * 70)
    print("W1.6: Qwen2.5-3B 36-Layer Func Model L3 Signoff")
    print("=" * 70)

    layers = list(range(36))
    results = run_forward_pass(
        model_path, layers, prompt,
        capture_intermediates=True
    )

    print(f"\nSaving 36-layer golden .npz to: {golden_dir}")
    save_golden_npz(results, golden_dir, include_intermediates=True)

    if skip_llamacpp:
        print("\nSkipping llama.cpp comparison (--skip-llamacpp)")
        return True

    llama_outputs = run_llamacpp_reference(model_path, prompt, LLAMA_REF_DIR, n_tokens=1)
    if not llama_outputs:
        print("ERROR: llama.cpp reference generation failed")
        return False

    layer_results = compare_layer_outputs(
        results["hidden_states"],
        llama_outputs.get("per_layer", {}),
        layers
    )

    cos_per_layer = {L: r["cos_sim"] for L, r in layer_results["per_layer"].items()}
    drift = drift_analysis(cos_per_layer)

    worst_layer = min(cos_per_layer, key=cos_per_layer.get) if cos_per_layer else None
    worst_per_op = {}
    if worst_layer is not None and "intermediates" in results:
        worst_per_op = worst_layer_decomposition(
            worst_layer,
            results["intermediates"][worst_layer],
            llama_outputs.get("per_layer", {})
        )

    checkpoint_layers = [0, 10, 20, 35]
    write_l3_evidence(
        evidence_path, layer_results, drift,
        worst_layer, worst_per_op, checkpoint_layers
    )

    print(f"\nSUMMARY: TESTS={layer_results['tests']} "
          f"PASS={layer_results['passed']} FAIL={layer_results['failed']}")
    print(f"Drift: {'PASS' if drift['drift_pass'] else 'FAIL'}")
    if worst_layer is not None:
        print(f"Worst layer: {worst_layer} (cos_sim={cos_per_layer[worst_layer]:.6f})")

    return layer_results["failed"] == 0 and drift["drift_pass"]


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="W1.6 36-layer Func Model L3 signoff")
    parser.add_argument("--model", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--golden-dir", default=str(DEFAULT_L3_GOLDEN_DIR))
    parser.add_argument("--evidence", default=str(EVIDENCE_DIR / "w1-6-fm-l3-signoff.txt"))
    parser.add_argument("--skip-llamacpp", action="store_true")
    args = parser.parse_args()

    ok = verify_l3_signoff(
        model_path=args.model,
        prompt=args.prompt,
        golden_dir=Path(args.golden_dir),
        evidence_path=Path(args.evidence),
        skip_llamacpp=args.skip_llamacpp,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
