#!/usr/bin/env python3
"""Subprocess, filesystem, and device-server helpers for the software signoff."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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


def _run_dump_hidden_states(
    config: SignoffConfig,
    workdir: Path,
    env: dict[str, str],
    prompt: str,
    n_predict: int,
) -> Path:
    """Run dump_hidden_states and convert the raw dumps into an .npz file."""
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
    return workdir / "refs" / "qwen_l0_l1_hidden.npz"


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
) -> str:
    """Run llama cli in single-turn mode and return the generated text."""
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
    ]
    proc = _run(cmd, workdir, env, timeout=900.0)
    if proc.returncode != 0:
        raise SignoffError(f"llama cli failed: {proc.stderr[-1000:]}")
    return _parse_generated_text(proc.stdout, prompt)


@contextlib.contextmanager
def managed_device_server(uri: str) -> Iterator[str]:
    """Translate fm://spike to a unix socket and start the device server."""
    if uri == "fm://spike":
        sock = Path("/tmp/caduceus_fm_spike.sock")
        sock.unlink(missing_ok=True)
        server_cmd = [
            sys.executable, "-m", "sim.device_server",
            "--spike", "--sock", str(sock),
        ]
        env = os.environ.copy()
        env["PYTHONPATH"] = "sim:gen"
        proc = subprocess.Popen(
            server_cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.time() + 30.0
            while time.time() < deadline and not sock.exists():
                if proc.poll() is not None:
                    raise SignoffError("device server exited before socket was ready")
                time.sleep(0.1)
            if not sock.exists():
                raise SignoffError("timed out waiting for device server socket")
            yield f"fm://unix?path={sock}"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                proc.kill()
            sock.unlink(missing_ok=True)
    else:
        yield uri
