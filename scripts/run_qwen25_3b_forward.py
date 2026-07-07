#!/usr/bin/env python3
"""
W1.2 / W1.6: Func Model forward pass for Qwen2.5-3B-Instruct-Q4_K_M.

Generates per-layer hidden states as golden .npz vectors and compares against
llama.cpp reference.  Core logic lives in sim.qwen25_forward / sim.qwen25_l3.
"""

import argparse
import os
import sys
from pathlib import Path

# Make sim/ importable when run as a script
_PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT / "sim"))

from qwen25_forward import (  # noqa: E402
    DEFAULT_GOLDEN_DIR,
    DEFAULT_L3_GOLDEN_DIR,
    DEFAULT_MODEL_PATH,
    DEFAULT_PROMPT,
    EVIDENCE_DIR,
    FALLBACK_MODEL_PATH,
    LLAMA_REF_DIR,
    compare_and_report,
    run_forward_pass,
    run_llamacpp_reference,
    save_golden_npz,
)
from qwen25_l3 import drift_analysis, parse_layers_arg, write_l3_evidence  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description="W1.2/W1.6: Qwen2.5-3B Func Model forward pass"
    )
    parser.add_argument("--model", default=DEFAULT_MODEL_PATH,
                        help=f"Path to Qwen2.5 GGUF model (default: {DEFAULT_MODEL_PATH})")
    parser.add_argument("--layers", nargs="*", default=None,
                        help="Layer indices: 0 1 2, 0..35, or omit for default 3-layer")
    parser.add_argument("--all-layers", action="store_true",
                        help="Run all 36 layers (shorthand for --layers 0..35)")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT,
                        help=f"Prompt for tokenization (default: {DEFAULT_PROMPT})")
    parser.add_argument("--golden-dir", default=None,
                        help="Output directory for golden .npz files")
    parser.add_argument("--skip-llamacpp", action="store_true",
                        help="Skip llama.cpp reference comparison")
    parser.add_argument("--evidence", default=None,
                        help="Path for evidence log")
    parser.add_argument("--drift-analysis", action="store_true",
                        help="Enable 36-layer drift analysis (implied by --all-layers)")
    parser.add_argument("--include-intermediates", action="store_true",
                        help="Store per-op intermediate outputs in combined .npz")

    args = parser.parse_args()

    model_path = args.model
    if not os.path.exists(model_path):
        if os.path.exists(FALLBACK_MODEL_PATH):
            print(f"Model {model_path} not found, using fallback: {FALLBACK_MODEL_PATH}")
            model_path = FALLBACK_MODEL_PATH
        else:
            print(f"ERROR: Model not found at {model_path} or {FALLBACK_MODEL_PATH}")
            sys.exit(1)

    if not os.path.exists(model_path):
        print(f"ERROR: Model file not found: {model_path}")
        sys.exit(1)

    # Determine layer range
    if args.all_layers:
        layers_arg = "all"
        default_golden = DEFAULT_L3_GOLDEN_DIR
        default_evidence = EVIDENCE_DIR / "w1-6-fm-l3-signoff.txt"
        enable_drift = True
    else:
        layers_arg = args.layers if args.layers else "0 1 2"
        default_golden = DEFAULT_GOLDEN_DIR
        default_evidence = EVIDENCE_DIR / "w1-2-fm-3layer.txt"
        enable_drift = args.drift_analysis

    golden_dir = Path(args.golden_dir) if args.golden_dir else default_golden
    evidence_path = Path(args.evidence) if args.evidence else default_evidence

    # Need num_hidden_layers before parsing layers; run a quick metadata read.
    import gguf
    reader = gguf.GGUFReader(model_path)

    def _get_field(key, default=None):
        try:
            return reader.fields[key].parts[-1][0]
        except (KeyError, IndexError, AttributeError):
            return default

    num_hidden_layers = int(_get_field("qwen2.block_count", default=36))
    layers = parse_layers_arg(layers_arg, num_hidden_layers)

    print(f"Qwen2.5-3B Func Model Forward Pass")
    print(f"  Model: {model_path}")
    print(f"  Layers: {layers}")
    print(f"  Prompt: '{args.prompt}'")

    results = run_forward_pass(
        model_path, layers, args.prompt,
        capture_intermediates=args.include_intermediates or enable_drift
    )

    print(f"\n{'=' * 60}")
    print(f"Saving golden .npz to: {golden_dir}")
    save_golden_npz(results, golden_dir,
                    include_intermediates=args.include_intermediates or enable_drift)

    if not args.skip_llamacpp:
        llama_outputs = run_llamacpp_reference(
            model_path, args.prompt, LLAMA_REF_DIR, n_tokens=1
        )
        if llama_outputs:
            layer_results = compare_and_report(
                results["hidden_states"], llama_outputs.get("per_layer", {}),
                layers, evidence_path
            )

            if enable_drift:
                cos_per_layer = {
                    L: r["cos_sim"] for L, r in layer_results["per_layer"].items()
                }
                drift = drift_analysis(cos_per_layer)

                # Worst layer = lowest cos_sim
                worst_layer = min(cos_per_layer, key=cos_per_layer.get) if cos_per_layer else None
                worst_per_op = {}
                if worst_layer is not None and "intermediates" in results:
                    from qwen25_l3 import worst_layer_decomposition
                    worst_per_op = worst_layer_decomposition(
                        worst_layer,
                        results["intermediates"][worst_layer],
                        llama_outputs.get("per_layer", {})
                    )

                checkpoint_layers = [L for L in [0, 10, 20, 35] if L in layers]
                write_l3_evidence(
                    evidence_path, layer_results, drift,
                    worst_layer, worst_per_op, checkpoint_layers
                )

                all_pass = layer_results["failed"] == 0 and drift["drift_pass"]
                if not all_pass:
                    print("\nL3 signoff FAILED")
                    sys.exit(1)
                print("\nL3 signoff PASSED")
        else:
            print("\nWARNING: Could not generate llama.cpp reference.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
