#!/usr/bin/env python3
"""Qwen2.5-3B per-layer hidden-state comparison against the A1 golden reference.

Loads the golden ``.npz`` produced by ``scripts/gen_qwen_full_golden.py``,
runs the full NPU forward pass via ``run_full_forward()`` from A2, computes
``cos_sim`` and ``max_abs_diff`` for every layer, and writes JSON evidence.

Usage::

    PYTHONPATH=sim python3 sim/signoff/qwen3b_per_layer_compare.py \
        --golden .omo/evidence/qwen-36l-golden.npz \
        --device fm://python
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

# ── Path setup ───────────────────────────────────────────────────────
_THIS = Path(__file__).resolve()
_PROJECT = _THIS.parents[2]
sys.path.insert(0, str(_PROJECT / "sim"))
sys.path.insert(0, str(_PROJECT / "ggml-npu"))

from signoff._ensure_pythonpath import ensure_repo_pythonpath  # noqa: E402

ensure_repo_pythonpath(_PROJECT)

from qwen25_forward import cosine_similarity  # noqa: E402
from signoff.qwen3b_full_forward import (  # noqa: E402
    _read_gguf_layer_count,
    run_full_forward,
)
from signoff.qwen3b_signoff_config import (  # noqa: E402
    CPU_BACKEND_NAME,
    REPO_ROOT,
    SignoffConfig,
    load_config,
)
from signoff.qwen3b_signoff_io import (  # noqa: E402
    _backend_workdir,
    _llama_env,
    _run_dump_hidden_states,
)

# ── Constants ────────────────────────────────────────────────────────
_DEFAULT_GOLDEN: str = str(REPO_ROOT / ".omo" / "evidence" / "qwen-36l-golden.npz")
_DEFAULT_CONFIG: Path = REPO_ROOT / "config" / "qwen3b-signoff.json"
_EVIDENCE_DIR: Path = REPO_ROOT / ".omo" / "evidence"
_LEARNINGS: Path = (
    REPO_ROOT / ".omo" / "notepads" / "fm-e2e-qwen-cv-software-stack" / "learnings.md"
)
_COS_MIN: float = 0.99
_ABS_MAX: float = 1e-3


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Qwen2.5-3B per-layer hidden-state comparison against golden reference"
    )
    parser.add_argument(
        "--golden",
        type=str,
        default=_DEFAULT_GOLDEN,
        help=f"Path to golden .npz (default: {_DEFAULT_GOLDEN})",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="fm://python",
        help="Device URI (fm://python, fm://spike, mock://)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override GGUF model path (default: read from golden metadata)",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default=None,
        help="Override prompt text (default: read from golden metadata)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="RNG seed for deterministic sampling",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(_EVIDENCE_DIR),
        help=f"Directory for evidence JSON (default: {_EVIDENCE_DIR})",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stdout output (still writes evidence JSON)",
    )
    return parser.parse_args(argv)


def _load_golden(golden_path: str) -> Optional[Dict[str, np.ndarray]]:
    p = Path(golden_path)
    if not p.is_file():
        return None
    with np.load(p, allow_pickle=True) as data:
        return {str(k): data[k] for k in data.keys()}


def _golden_metadata(golden: Dict[str, np.ndarray]) -> Dict[str, Any]:
    metadata = golden.get("metadata")
    if metadata is not None and len(metadata) > 0:
        try:
            return json.loads(str(metadata[0]))
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _resolve_model_path(model_override: Optional[str], metadata: Dict[str, Any]) -> str:
    if model_override:
        return model_override
    mp = metadata.get("model")
    if mp:
        return str(mp)
    cfg = load_config(_DEFAULT_CONFIG)
    return str(cfg.model_path)


def _resolve_prompt(prompt_override: Optional[str], metadata: Dict[str, Any]) -> str:
    if prompt_override:
        return prompt_override
    p = metadata.get("prompt")
    if p:
        return str(p)
    return "Hello"


def _resolve_layer_count(model_path: str, metadata: Dict[str, Any]) -> int:
    try:
        if Path(model_path).is_file():
            return _read_gguf_layer_count(model_path)
    except Exception:
        pass
    layers = metadata.get("layers")
    if isinstance(layers, int):
        return layers
    return 36


def _compute_layer_metrics(
    ref_arr: np.ndarray, npu_arr: np.ndarray
) -> Dict[str, Any]:
    g = np.asarray(ref_arr).astype(np.float64).flatten()
    n = np.asarray(npu_arr).astype(np.float64).flatten()
    max_abs_diff = float(np.max(np.abs(g - n)))
    cos_sim = cosine_similarity(ref_arr, npu_arr)
    passed = bool(cos_sim >= _COS_MIN and max_abs_diff <= _ABS_MAX)
    return {
        "cos_sim": float(cos_sim),
        "max_abs_diff": max_abs_diff,
        "passed": passed,
        "shape_reference": list(np.asarray(ref_arr).shape),
        "shape_npu": list(np.asarray(npu_arr).shape),
    }


def _capture_cpu_hidden_states(
    config: SignoffConfig,
    base_env: dict[str, str],
    prompt: str,
    n_layers: int,
) -> Dict[int, np.ndarray]:
    """Capture per-layer hidden states from the llama.cpp CPU backend.

    This is the canonical software reference used for the per-layer
    comparison.  The returned dict maps layer index to the ``l_out_{idx}``
    ndarray.
    """
    reference: Dict[int, np.ndarray] = {}
    with _backend_workdir(config.bundle, CPU_BACKEND_NAME) as cpu_wd:
        cpu_env = _llama_env(base_env, None)
        cpu_npz, _ = _run_dump_hidden_states(
            config, cpu_wd, cpu_env, prompt, n_predict=1
        )
        with np.load(cpu_npz, allow_pickle=True) as data:
            for i in range(n_layers):
                key = f"l_out_{i}"
                if key in data:
                    reference[i] = data[key]
    return reference


def _write_evidence(
    evidence: Dict[str, Any], output_dir: Path
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    path = output_dir / f"qwen-per-layer-compare-{ts}.json"
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True))
    return path


def _append_learnings(evidence: Dict[str, Any], path: Path) -> None:
    _LEARNINGS.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    n_layers = evidence.get("n_layers", 0)
    first = evidence.get("summary", {}).get("first_layer", {})
    last = evidence.get("summary", {}).get("last_layer", {})
    block = (
        f"\n## {ts} A3 — per-layer hidden-state comparison\n\n"
        f"### Completed\n\n"
        f"- **Ran A3 per-layer compare**: golden `{evidence['golden_path']}`, "
        f"device `{evidence['device']}`, model `{evidence['model']}`.\n"
        f"- **Generated token text**: `{evidence.get('generated_token_text', '')}`.\n"
        f"- **First layer (l_out_0)**: cos_sim={first.get('cos_sim', 0.0):.6f}, "
        f"max_abs_diff={first.get('max_abs_diff', 0.0):.4e}, "
        f"passed={first.get('passed', False)}.\n"
        f"- **Last layer (l_out_{n_layers - 1})**: cos_sim={last.get('cos_sim', 0.0):.6f}, "
        f"max_abs_diff={last.get('max_abs_diff', 0.0):.4e}, "
        f"passed={last.get('passed', False)}.\n"
        f"- **Overall passed**: {evidence.get('passed', False)}.\n"
        f"- **Evidence**: {path}\n\n"
        f"### Thresholds\n\n"
        f"- First and last layers must satisfy cos_sim >= {_COS_MIN} "
        f"and max_abs_diff <= {_ABS_MAX}.\n"
        f"- Intermediate layers are recorded but do not affect the overall verdict.\n\n"
    )
    with open(_LEARNINGS, "a", encoding="utf-8") as f:
        f.write(block)


def run_per_layer_compare(
    golden_path: str,
    device: str,
    model_path: Optional[str] = None,
    prompt: Optional[str] = None,
    seed: int = 42,
    output_dir: Optional[Path] = None,
    quiet: bool = False,
) -> Tuple[Dict[str, Any], Path]:
    """Run the full NPU forward pass and compare each layer to the golden reference.

    Returns the evidence dict and the path to the written JSON file.
    """
    start = time.perf_counter()
    golden = _load_golden(golden_path)
    if golden is None:
        raise FileNotFoundError(
            f"Golden file not found: {golden_path}. "
            "Run A1 first:\n"
            "  PYTHONPATH=sim:ggml-npu python3 scripts/gen_qwen_full_golden.py "
            "--model <model> --output .omo/evidence/"
        )

    metadata = _golden_metadata(golden)
    model_path = model_path or _resolve_model_path(None, metadata)
    prompt = prompt or _resolve_prompt(None, metadata)
    n_layers = _resolve_layer_count(model_path, metadata)

    if not quiet:
        print(f"Golden:   {golden_path}")
        print(f"Model:    {model_path}")
        print(f"Device:   {device}")
        print(f"Prompt:   {prompt!r}")
        print(f"Layers:   {n_layers}")
        print("Running full NPU forward pass (this may take a while)...")

    forward_evidence = run_full_forward(
        model_path=model_path,
        prompt=prompt,
        device=device,
        layers=n_layers,
        golden_path=golden_path,
        seed=seed,
        capture_hidden_states=True,
    )

    hidden_states = forward_evidence.get("hidden_states", {})

    # Capture the CPU llama.cpp reference hidden states.  The A1 golden .npz
    # provides model/prompt/logits/tokens; the per-layer comparison uses the
    # CPU backend as the numerical reference because it exercises the same
    # quantized GGUF graph as the NPU path.
    if not quiet:
        print("Capturing CPU reference hidden states...")
    base_env = os.environ.copy()
    base_env["PYTHONPATH"] = "sim:gen:software"
    cfg = load_config(_DEFAULT_CONFIG)
    cfg = dataclasses.replace(
        cfg, model_path=Path(model_path), seed=seed, bundle=cfg.bundle
    )
    reference_hidden = _capture_cpu_hidden_states(cfg, base_env, prompt, n_layers)

    layer_metrics: Dict[str, Dict[str, Any]] = {}
    passed_count = 0
    failed_layers: list[int] = []

    for i in range(n_layers):
        ref_arr = reference_hidden.get(i)
        npu_arr = hidden_states.get(i)
        if ref_arr is None or npu_arr is None:
            layer_metrics[str(i)] = {
                "layer": i,
                "cos_sim": None,
                "max_abs_diff": None,
                "passed": False,
                "error": "missing reference or npu hidden state",
            }
            failed_layers.append(i)
            continue
        metrics = _compute_layer_metrics(ref_arr, npu_arr)
        metrics["layer"] = i
        layer_metrics[str(i)] = metrics
        if metrics["passed"]:
            passed_count += 1
        else:
            failed_layers.append(i)

    first = layer_metrics.get("0", {})
    last = layer_metrics.get(str(n_layers - 1), {})
    overall_passed = bool(first.get("passed", False) and last.get("passed", False))

    summary = {
        "first_layer": {
            "cos_sim": first.get("cos_sim"),
            "max_abs_diff": first.get("max_abs_diff"),
            "passed": first.get("passed", False),
        },
        "last_layer": {
            "cos_sim": last.get("cos_sim"),
            "max_abs_diff": last.get("max_abs_diff"),
            "passed": last.get("passed", False),
        },
        "total_layers": n_layers,
        "passed_layers": passed_count,
        "failed_layers": failed_layers,
        "thresholds": {
            "cos_sim_min": _COS_MIN,
            "max_abs_diff_max": _ABS_MAX,
        },
    }

    evidence: Dict[str, Any] = {
        "prompt": prompt,
        "device": device,
        "golden_path": str(golden_path),
        "generated_token_text": forward_evidence.get("generated_token_text", ""),
        "model": model_path,
        "n_layers": n_layers,
        "seed": seed,
        "layer_metrics": layer_metrics,
        "passed": overall_passed,
        "summary": summary,
        "elapsed_sec": time.perf_counter() - start,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    dest = output_dir or _EVIDENCE_DIR
    ev_path = _write_evidence(evidence, Path(dest))

    if not quiet:
        print(f"\n{'=' * 60}")
        print("Per-Layer Hidden-State Comparison Complete")
        print(f"  First layer:  cos_sim={first.get('cos_sim')}, "
              f"max_abs_diff={first.get('max_abs_diff')}, "
              f"passed={first.get('passed', False)}")
        print(f"  Last layer:   cos_sim={last.get('cos_sim')}, "
              f"max_abs_diff={last.get('max_abs_diff')}, "
              f"passed={last.get('passed', False)}")
        print(f"  Passed layers: {passed_count}/{n_layers}")
        print(f"  Overall passed: {overall_passed}")
        print(f"  Evidence: {ev_path}")
        print(f"{'=' * 60}")

    _append_learnings(evidence, ev_path)
    return evidence, ev_path


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        evidence, _ = run_per_layer_compare(
            golden_path=args.golden,
            device=args.device,
            model_path=args.model,
            prompt=args.prompt,
            seed=args.seed,
            output_dir=Path(args.output_dir),
            quiet=args.quiet,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
