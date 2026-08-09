#!/usr/bin/env python3
"""Subprocess, filesystem, and device-server helpers for the software signoff."""

from __future__ import annotations

import contextlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterator

import numpy as np

from signoff.qwen3b_signoff_config import (
    CPU_BACKEND_NAME,
    NPU_BACKEND_NAME,
    REPO_ROOT,
    BackendBundle,
    SignoffConfig,
    SignoffError,
)


@contextlib.contextmanager
def _backend_workdir(bundle: BackendBundle, backend: str) -> Iterator[Path]:
    """Create an isolated directory containing only the requested backend .so set."""
    tmp = Path(tempfile.mkdtemp(prefix=f"qwen3b_{backend.lower()}_"))
    try:
        bin_dir = bundle.llama_cli.parent
        shutil.copy2(bundle.llama_cli, tmp / "llama")
        shutil.copy2(bundle.test_backend_ops, tmp / "test-backend-ops")
        shutil.copy2(bundle.dump_hidden_states, tmp / "dump_hidden_states")
        if backend == NPU_BACKEND_NAME:
            (tmp / bundle.npu_so.name).symlink_to(bundle.npu_so)
        for src in bin_dir.glob("libggml-cpu-*.so"):
            (tmp / src.name).symlink_to(src)
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(
    cmd: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 600.0,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and return its completed process."""
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _llama_env(base: dict[str, str], device_uri: str | None) -> dict[str, str]:
    env = dict(base)
    env["LD_LIBRARY_PATH"] = str(REPO_ROOT / "build" / "llama" / "bin")
    if device_uri is not None:
        env["CADUCEUS_DEVICE"] = device_uri
    elif "CADUCEUS_DEVICE" in env:
        del env["CADUCEUS_DEVICE"]
    return env


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _parse_generated_text(stdout: str, prompt: str) -> str:
    """Extract the generated line that follows the prompt echo in llama cli output."""
    clean = _strip_ansi(stdout)
    marker = f"> {prompt}"
    idx = clean.find(marker)
    if idx < 0:
        return ""
    after = clean[idx + len(marker) :]
    for line in after.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _parse_generated_text_full(stdout: str, prompt: str) -> str:
    """Extract the *full* generated region that follows the prompt echo.

    Unlike :func:`_parse_generated_text` (first line only), this spans every
    line between the prompt echo and the trailing ``[ Prompt: ... ]`` perf
    summary, so multi-token decode comparisons cover the whole generated
    sequence rather than just its first line.
    """
    clean = _strip_ansi(stdout)
    marker = f"> {prompt}"
    idx = clean.find(marker)
    if idx < 0:
        return ""
    after = clean[idx + len(marker) :]
    end = after.find("[ Prompt:")
    if end >= 0:
        after = after[:end]
    parts = [line.strip() for line in after.splitlines() if line.strip()]
    return " ".join(parts)


def _count_generated_tokens(text: str, model_path: Path) -> int | None:
    """Best-effort token count of *text* via the GGUF-backed Python tokenizer.

    Returns None when the tokenizer cannot be loaded (missing ``tokenizers``
    package or GGUF read failure) so callers can fall back to the requested
    ``n_predict`` value without failing the gate.
    """
    if not text:
        return 0
    try:
        from tokenizer import tokenize

        return len(tokenize(text, str(model_path)))
    except Exception:
        return None


def _run_dump_hidden_states(
    config: SignoffConfig,
    workdir: Path,
    env: dict[str, str],
    prompt: str,
    n_predict: int,
) -> tuple[Path, str]:
    """Run dump_hidden_states and convert the raw dumps into an .npz file.

    Returns the path to the generated .npz and the raw stderr of
    dump_hidden_states so callers can parse backend per-op dispatch logs.
    """
    refs_dir = workdir / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(workdir / "dump_hidden_states"),
        "-m", str(config.model_path),
        "-p", prompt,
        "-n", str(n_predict),
        "-s", str(config.seed),
        "--temp", "0",
    ]
    proc = _run(cmd, workdir, env)
    if proc.returncode != 0:
        raise SignoffError(f"dump_hidden_states failed: {proc.stderr[-1000:]}")
    save_npz = REPO_ROOT / "llama_ref" / "save_npz.py"
    npz_proc = _run([sys.executable, str(save_npz)], workdir, env)
    if npz_proc.returncode != 0:
        raise SignoffError(f"save_npz.py failed: {npz_proc.stderr[-1000:]}")
    return workdir / "refs" / "qwen_l0_l1_hidden.npz", proc.stderr


def _compare_hidden(
    cpu_npz: Path, npu_npz: Path, layer_key: str, tol_abs: float, tol_cos: float
) -> dict[str, str | int | float | bool]:
    """Compare a single hidden-state tensor between CPU and NPU runs."""
    cpu_arr = np.load(cpu_npz)[layer_key]
    npu_arr = np.load(npu_npz)[layer_key]
    diff = np.abs(cpu_arr.astype(np.float64) - npu_arr.astype(np.float64))
    max_abs = float(np.max(diff))
    norm_cpu = np.linalg.norm(cpu_arr)
    norm_npu = np.linalg.norm(npu_arr)
    cos_sim = (
        float(np.dot(cpu_arr.flatten(), npu_arr.flatten()) / (norm_cpu * norm_npu))
        if norm_cpu and norm_npu
        else 0.0
    )
    return {
        "layer": layer_key,
        "shape": str(list(cpu_arr.shape)),
        "max_abs_diff": max_abs,
        "cos_sim": cos_sim,
        "within_abs_tol": max_abs <= tol_abs,
        "within_cos_tol": cos_sim >= tol_cos,
        "passed": max_abs <= tol_abs and cos_sim >= tol_cos,
    }


def _run_llama_cli_decode(
    config: SignoffConfig,
    workdir: Path,
    env: dict[str, str],
    prompt: str,
    n_predict: int,
    timeout: float = 900.0,
    return_proc: bool = False,
    ignore_eos: bool = False,
) -> str | subprocess.CompletedProcess[str]:
    """Run llama cli in single-turn mode and return the generated text.

    If *return_proc* is True, returns the completed process so callers can
    inspect stderr (e.g. for per-fence execution stats).  When *ignore_eos*
    is True the ``--ignore-eos`` flag is added so generation runs for the
    full *n_predict* tokens (required to stress the KV cache at long
    sequences instead of stopping at the first EOS).
    """
    cmd = [
        str(workdir / "llama"), "cli",
        "-m", str(config.model_path),
        "-p", prompt,
        "-n", str(n_predict),
        "-s", str(config.seed),
        "--temp", str(config.temperature),
        "--top-k", str(config.top_k),
        "--top-p", str(config.top_p),
        "--single-turn",
        # Basic console IO: llama-cli's advanced display suppresses stdout in
        # limited-console environments (e.g. tmux), which would empty the
        # captured output and break text comparison.  The gate always runs in
        # a captured subprocess, so plain output is the correct mode.
        "--simple-io",
    ]
    if ignore_eos:
        cmd.append("--ignore-eos")
    proc = _run(cmd, workdir, env, timeout=timeout)
    if proc.returncode != 0:
        raise SignoffError(f"llama cli failed: {proc.stderr[-1000:]}")
    if return_proc:
        return proc
    return _parse_generated_text(proc.stdout, prompt)


