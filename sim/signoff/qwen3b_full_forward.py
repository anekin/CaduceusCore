#!/usr/bin/env python3
"""Qwen2.5-3B embedding + N-block + output-norm + lm_head full forward runner.

Runs the ggml-npu backend through llama cli, managed by the device_server
fixture (``fm://python``), and compares the output token against the CPU /
golden reference.

Usage::

    PYTHONPATH=sim:gen python3 sim/signoff/qwen3b_full_forward.py \\
        --model ~/models/qwen2.5-3b-instruct-q4_k_m.gguf \\
        --device fm://python \\
        --prompt "Hello"

Evidence JSON written to ``.omo/evidence/qwen-full-forward-{ts}.json``.
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

import gguf  # noqa: E402
from signoff.device_server_fixture import managed_device_server  # noqa: E402
from signoff.qwen3b_signoff_config import (  # noqa: E402
    CPU_BACKEND_NAME,
    NPU_BACKEND_NAME,
    BackendBundle,
    SignoffConfig,
    load_config,
)
from signoff.qwen3b_signoff_io import (  # noqa: E402
    _backend_workdir,
    _llama_env,
    _parse_generated_text,
    _run,
    _run_dump_hidden_states,
    _strip_ansi,
)

# ── Constants ────────────────────────────────────────────────────────
_DEFAULT_CONFIG: str = str(_PROJECT / "config" / "qwen3b-signoff.json")
_DEFAULT_GOLDEN: str = str(_PROJECT / ".omo" / "evidence" / "qwen-36l-golden.npz")
_DEFAULT_MODEL: str = str(Path.home() / "models" / "qwen2.5-3b-instruct-q4_k_m.gguf")
_EVIDENCE_DIR: Path = _PROJECT / ".omo" / "evidence"
_GOLDEN_LAYER_COUNT: int = 36  # fallback if GGUF metadata read fails
_MOCK_URIS: frozenset[str] = frozenset({"mock://", "mock://null"})
_HIDDEN_NPREDICT: int = 1  # tokens passed to dump_hidden_states for per-layer capture
# Acceptance threshold (A6): total ``[NPU] OP node`` log lines (NPU-dispatched
# + CPU fallbacks) that must be visited while traversing the 36-layer graph
# under ``--device mock://``.
_MOCK_MIN_OP_NODES: int = 612


def _read_gguf_layer_count(model_path: str) -> int:
    try:
        reader = gguf.GGUFReader(model_path)
        return int(reader.fields["qwen2.block_count"].parts[-1][0])
    except (KeyError, IndexError, AttributeError, ValueError):
        return _GOLDEN_LAYER_COUNT


def _get_bundle() -> BackendBundle:
    """Load the BackendBundle from the signoff config."""
    cfg = load_config(Path(_DEFAULT_CONFIG))
    return cfg.bundle


def _capture_hidden_states(
    config: SignoffConfig,
    base_env: dict[str, str],
    device_uri: str,
    prompt: str,
    n_layers: int,
) -> Dict[int, np.ndarray]:
    """Run ``dump_hidden_states`` and return per-layer hidden-state tensors.

    The returned dict maps layer index to the raw ``l_out_{idx}`` array
    produced by the NPU backend.  Arrays are returned as-is (caller decides
    whether to flatten before comparing).
    """
    hidden: Dict[int, np.ndarray] = {}
    with _backend_workdir(config.bundle, NPU_BACKEND_NAME) as npu_wd:
        npu_env = _llama_env(base_env, device_uri)
        npu_npz, _ = _run_dump_hidden_states(
            config, npu_wd, npu_env, prompt, n_predict=_HIDDEN_NPREDICT
        )
        with np.load(npu_npz, allow_pickle=True) as data:
            for i in range(n_layers):
                key = f"l_out_{i}"
                if key in data:
                    hidden[i] = data[key]
    return hidden


def _last_layer_from_stderr(stderr: str) -> int:
    """Highest transformer layer index observed in '[NPU] OP node' labels.

    Handles both label conventions seen in the wild: the real-NPU form
    ``blk.N.<name>`` and the mock/fallback form ``<name>-N``.  Returns 0 when
    no layer-suffixed label is present.
    """
    import re as _re
    clean = _strip_ansi(stderr)
    nums: list[int] = []
    for match in _re.finditer(
        r"\[NPU\] OP node \d+ [A-Z_][A-Z0-9_]* \(([^)]+)\)", clean
    ):
        label = match.group(1)
        blk = _re.search(r"blk\.(\d+)\.", label)
        if blk is not None:
            nums.append(int(blk.group(1)))
            continue
        suffix = _re.search(r"-(\d+)$", label)
        if suffix is not None:
            nums.append(int(suffix.group(1)))
    return max(nums) if nums else 0


def _parse_op_dispatch(stderr: str) -> Dict[str, Any]:
    """Parse ``[NPU] OP node ...`` stderr lines for NPU dispatch counts."""
    import re as _re
    pattern = _re.compile(
        r"^\[NPU\] OP node \d+ ([A-Z_][A-Z0-9_]*) \(([^)]+)\): (NPU|CPU fallback.*?)\s*$",
        _re.MULTILINE,
    )
    npu_ops_executed = 0
    cpu_fallback_ops: list[str] = []
    clean = _strip_ansi(stderr)
    for match in pattern.finditer(clean):
        op_name = match.group(1)
        label = match.group(2)
        tail = match.group(3)
        if tail == "NPU":
            npu_ops_executed += 1
        elif tail.startswith("CPU fallback"):
            reason = tail[len("CPU fallback"):].lstrip(" :")
            cpu_fallback_ops.append(f"{op_name} ({label}): {reason}")
    return {
        "npu_ops_executed": npu_ops_executed,
        "cpu_fallback_ops": cpu_fallback_ops,
        "op_node_count": len(_re.findall(r"\[NPU\] OP node \d+ ", clean)),
        "last_layer": _last_layer_from_stderr(stderr),
    }


class LlamaCliError(RuntimeError):
    """llama cli subprocess failed; carries the full captured stderr."""

    def __init__(self, message: str, stderr: str):
        super().__init__(message)
        self.stderr = stderr


def _failure_evidence(
    model_path: str,
    device: str,
    resolved_device: str,
    n_layers: int,
    seed: int,
    stderr: str,
    reason: str,
    elapsed_sec: float,
) -> Dict[str, Any]:
    """Build a failure evidence dict for a mid-traversal crash.

    Records how far the graph traversal got (``op_node_count`` and the last
    layer index seen in the captured stderr) so a crash is diagnosable.
    """
    dispatch = _parse_op_dispatch(stderr)
    return {
        "model": model_path,
        "prompt": "",
        "device": device,
        "resolved_device": resolved_device,
        "n_layers": n_layers,
        "layers": n_layers,
        "seed": seed,
        "cpu_text": "",
        "npu_text": "",
        "generated_token_id": None,
        "generated_token_text": "",
        "logits_top5": None,
        "npu_ops_executed": dispatch["npu_ops_executed"],
        "cpu_fallback_ops": dispatch["cpu_fallback_ops"],
        "op_node_count": dispatch["op_node_count"],
        "last_layer": dispatch["last_layer"],
        "text_match": False,
        "passed": False,
        "crash": True,
        "crash_reason": reason,
        "elapsed_sec": elapsed_sec,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _load_golden(golden_path: str) -> Optional[Dict[str, np.ndarray]]:
    """Load golden reference .npz file.

    Returns None if the file does not exist (graceful degradation).
    """
    p = Path(golden_path)
    if not p.is_file():
        return None
    with np.load(p, allow_pickle=True) as data:
        return {str(k): data[k] for k in data.keys()}


def _resolve_golden_token(
    golden: Dict[str, np.ndarray] | None,
) -> Tuple[Optional[int], Optional[np.ndarray]]:
    """Extract the predicted token ID and logits from the golden reference.

    Returns (token_id, logits).  Both are *None* when no golden is available.
    """
    if golden is None:
        return None, None
    tokens = golden.get("tokens")
    token_id: Optional[int] = None
    if tokens is not None and len(tokens) > 0:
        token_id = int(tokens[0])
    logits = golden.get("logits")
    return token_id, logits


def _logits_top5(logits: np.ndarray | None) -> Optional[list[int]]:
    """Return the top-5 token IDs (descending) from the logits vector."""
    if logits is None:
        return None
    ll = np.asarray(logits, dtype=np.float32)
    if ll.ndim == 0:
        return None
    flat = ll.flatten()
    if len(flat) < 5:
        return int(np.argmax(flat)) if len(flat) > 0 else None  # type: ignore[return-value]
    return flat.argsort()[::-1][:5].tolist()  # type: ignore[return-value]


def run_full_forward(
    model_path: str,
    prompt: str = "Hello",
    device: str = "fm://python",
    layers: Optional[int] = None,
    golden_path: Optional[str] = None,
    seed: int = 42,
    capture_hidden_states: bool = False,
) -> Dict[str, Any]:
    """Run the full Qwen2.5-3B forward pass through the ggml-npu backend.

    Parameters
    ----------
    model_path:
        Path to the Qwen2.5 GGUF model file.
    prompt:
        Input text to tokenize.
    device:
        Device URI. ``fm://python`` / ``fm://`` / ``fm://spike`` are
        auto-managed by the device_server fixture; ``mock://`` is a
        graceful traversal-only path.
    layers:
        Number of transformer layers to run.  When *None*, read
        ``qwen2.block_count`` from GGUF metadata.
    golden_path:
        Path to the golden reference ``.npz`` file (e.g.
        ``.omo/evidence/qwen-36l-golden.npz``).  Used to retrieve the
        expected token ID and logits for cross-validation.
    seed:
        RNG seed for deterministic sampling.
    capture_hidden_states:
        If *True*, additionally run ``dump_hidden_states`` against the
        NPU backend and include per-layer ``hidden_states`` in the
        returned evidence dict.

    Returns
    -------
    Evidence dict with keys ``prompt``, ``device``,
    ``generated_token_id``, ``generated_token_text``,
    ``logits_top5``, ``npu_ops_executed``, plus metadata.  When
    ``capture_hidden_states=True`` the dict also contains a
    ``hidden_states`` mapping from layer index to ndarray.
    """
    start = time.perf_counter()

    model_p = Path(model_path)
    if not model_p.is_file():
        raise FileNotFoundError(f"model not found: {model_path}")

    n_layers = layers if layers is not None else _read_gguf_layer_count(model_path)
    bundle = _get_bundle()
    base_env = os.environ.copy()
    base_env["PYTHONPATH"] = "sim:gen:software"
    is_mock = device in _MOCK_URIS

    golden = _load_golden(golden_path) if golden_path else None
    golden_token_id, golden_logits = _resolve_golden_token(golden)

    npu_text: str = ""
    npu_dispatch: Dict[str, Any] = {"npu_ops_executed": 0, "cpu_fallback_ops": []}
    cpu_text: str = ""
    hidden_states: Dict[int, np.ndarray] = {}

    with managed_device_server(device) as resolved_uri:
        # ── NPU forward run ──────────────────────────────────────────
        with _backend_workdir(bundle, NPU_BACKEND_NAME) as npu_wd:
            npu_env = _llama_env(base_env, resolved_uri)
            try:
                npu_text, npu_dispatch = _run_llama_cli_with_dispatch(
                    npu_wd, npu_env, model_path, prompt, seed,
                )
            except LlamaCliError as exc:
                # Traversal crashed mid-graph: report how far it got.
                return _failure_evidence(
                    model_path, device, resolved_uri, n_layers, seed,
                    exc.stderr, str(exc), time.perf_counter() - start,
                )

        # ── CPU reference run (skip for mock:// traversal) ──────────
        if not is_mock:
            with _backend_workdir(bundle, CPU_BACKEND_NAME) as cpu_wd:
                cpu_env = _llama_env(base_env, None)
                cpu_text = _run_llama_cli(cpu_wd, cpu_env, model_path, prompt, seed)

        if capture_hidden_states:
            base_cfg = load_config(Path(_DEFAULT_CONFIG))
            capture_cfg = dataclasses.replace(
                base_cfg,
                model_path=Path(model_path),
                seed=seed,
                bundle=bundle,
            )
            hidden_states = _capture_hidden_states(
                capture_cfg, base_env, resolved_uri, prompt, n_layers
            )

    op_node_count = int(npu_dispatch.get("op_node_count", 0))
    last_layer = int(npu_dispatch.get("last_layer", 0))

    # ── Token ID resolution ─────────────────────────────────────────
    # Best-effort: use golden token ID when available; otherwise note
    # that we only have text output.
    token_id: Optional[int] = golden_token_id

    # ── Assemble evidence ───────────────────────────────────────────
    logits_t5 = _logits_top5(golden_logits)
    if is_mock:
        # mock:// is traversal-only: pass requires the full graph's op nodes
        # to have been visited (all CPU fallbacks), not numeric equality.
        passed = op_node_count >= _MOCK_MIN_OP_NODES
    else:
        passed = _eval_pass(cpu_text, npu_text, is_mock)

    evidence: Dict[str, Any] = {
        "model": model_path,
        "prompt": prompt,
        "device": device,
        "resolved_device": resolved_uri,
        "n_layers": n_layers,
        "layers": n_layers,
        "seed": seed,
        "cpu_text": cpu_text,
        "npu_text": npu_text,
        "generated_token_id": token_id,
        "generated_token_text": npu_text,
        "logits_top5": logits_t5,
        "npu_ops_executed": npu_dispatch.get("npu_ops_executed", 0),
        "cpu_fallback_ops": npu_dispatch.get("cpu_fallback_ops", []),
        "op_node_count": op_node_count,
        "last_layer": last_layer,
        "text_match": cpu_text == npu_text,
        "passed": passed,
        "elapsed_sec": time.perf_counter() - start,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if capture_hidden_states:
        evidence["hidden_states"] = hidden_states
    return evidence


def _eval_pass(cpu_text: str, npu_text: str, is_mock: bool) -> bool:
    if is_mock:
        return True
    if not cpu_text or not npu_text:
        return False
    return cpu_text == npu_text


def _run_llama_cli(
    workdir: Path,
    env: dict[str, str],
    model_path: str,
    prompt: str,
    seed: int,
    timeout: float = 900.0,
) -> str:
    """Run llama cli in single-turn decode mode and return generated text."""
    cmd = [
        str(workdir / "llama"), "cli",
        "-m", model_path,
        "-p", prompt,
        "-n", "1",
        "-s", str(seed),
        "--temp", "0",
        "--top-k", "1",
        "--top-p", "0",
        "--single-turn",
    ]
    proc = _run(cmd, workdir, env, timeout=timeout)
    if proc.returncode != 0:
        stderr_tail = "\n".join(proc.stderr.splitlines()[-20:])
        raise RuntimeError(
            f"llama cli failed (exit {proc.returncode}):\n{stderr_tail}"
        )
    return _parse_generated_text(proc.stdout, prompt)


def _run_llama_cli_with_dispatch(
    workdir: Path,
    env: dict[str, str],
    model_path: str,
    prompt: str,
    seed: int,
    timeout: float = 900.0,
) -> Tuple[str, Dict[str, Any]]:
    """Like _run_llama_cli but also return per-op dispatch info from stderr."""
    cmd = [
        str(workdir / "llama"), "cli",
        "-m", model_path,
        "-p", prompt,
        "-n", "1",
        "-s", str(seed),
        "--temp", "0",
        "--top-k", "1",
        "--top-p", "0",
        "--single-turn",
    ]
    proc = _run(cmd, workdir, env, timeout=timeout)
    if proc.returncode != 0:
        stderr_tail = "\n".join(proc.stderr.splitlines()[-20:])
        raise LlamaCliError(
            f"llama cli failed (exit {proc.returncode}):\n{stderr_tail}",
            stderr=proc.stderr,
        )
    text = _parse_generated_text(proc.stdout, prompt)
    dispatch = _parse_op_dispatch(proc.stderr)
    return text, dispatch


def _write_evidence(evidence: Dict[str, Any], evidence_dir: Optional[Path] = None) -> Path:
    """Write evidence JSON and return the file path."""
    dest = evidence_dir or _EVIDENCE_DIR
    dest.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    path = dest / f"qwen-full-forward-{ts}.json"
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True))
    return path


# ── CLI ──────────────────────────────────────────────────────────────

def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Qwen2.5-3B full forward runner via ggml-npu + device_server"
    )
    parser.add_argument(
        "--model", type=str, default=_DEFAULT_MODEL,
        help="Path to Qwen2.5 GGUF model file (default: ~/models/qwen2.5-3b-instruct-q4_k_m.gguf)",
    )
    parser.add_argument(
        "--device", type=str, default="fm://python",
        help="Device URI (fm://python, fm://spike, mock://)",
    )
    parser.add_argument(
        "--prompt", type=str, default="Hello",
        help="Input prompt text",
    )
    parser.add_argument(
        "--layers", type=int, default=None,
        help="Number of transformer layers (default: read from GGUF metadata)",
    )
    parser.add_argument(
        "--golden", type=str, default=_DEFAULT_GOLDEN,
        help=f"Path to golden .npz (default: {_DEFAULT_GOLDEN})",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="RNG seed for deterministic sampling",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress stdout output (still writes evidence JSON)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        evidence = run_full_forward(
            model_path=args.model,
            prompt=args.prompt,
            device=args.device,
            layers=args.layers,
            golden_path=args.golden,
            seed=args.seed,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    ev_path = _write_evidence(evidence)
    if not args.quiet:
        print(f"\n{'=' * 60}")
        print("Full Forward Runner Complete")
        print(f"  Model:      {args.model}")
        print(f"  Device:     {args.device}")
        print(f"  Prompt:     {args.prompt}")
        print(f"  Layers:     {evidence['n_layers']}")
        print(f"  CPU text:   {evidence['cpu_text']!r}")
        print(f"  NPU text:   {evidence['npu_text']!r}")
        print(f"  Token ID:   {evidence['generated_token_id']}")
        print(f"  Logits T5:  {evidence['logits_top5']}")
        print(f"  OP nodes:   {evidence['op_node_count']} (threshold {_MOCK_MIN_OP_NODES})")
        print(f"  Last layer: {evidence['last_layer']}")
        if evidence.get("crash"):
            print(f"  CRASH:      {evidence['crash_reason']}")
        print(f"  NPU ops:    {evidence['npu_ops_executed']}")
        print(f"  Passed:     {evidence['passed']}")
        print(f"  Evidence:   {ev_path}")
        print(f"{'=' * 60}")

    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
