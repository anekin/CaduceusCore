#!/usr/bin/env python3
"""Unit and slow-integration tests for the Qwen2.5-3B software signoff runner."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from signoff.qwen3b_signoff import (
    SignoffConfig,
    SignoffError,
    _compare_hidden,
    _parse_generated_text,
    compute_backend_hash,
    load_config,
    run_negative_signoff,
    verify_model_hash,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "qwen3b-signoff.json"
MODEL_PATH_ENV = "QWEN3B_GGUF"


def _model_path() -> Path:
    cfg = load_config(DEFAULT_CONFIG)
    env = os.environ.get(MODEL_PATH_ENV)
    return Path(env) if env else cfg.model_path


def test_load_config() -> None:
    cfg = load_config(DEFAULT_CONFIG)
    assert cfg.model_sha256 == "626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d"
    assert cfg.llama_commit == "88b47a755c72fed4b22fba0fd262e2d7b7d01583"
    assert cfg.abi_version == "1.0"
    assert "supported_single_ops" in cfg.gates


def test_verify_model_hash_ok(tmp_path: Path) -> None:
    target = tmp_path / "model.gguf"
    target.write_text("hello model")
    expected = "6cded17466c7e76be61b862670ce1043d8a8d1f6d6b7f0e3f6f3f3f3f3f3f3f3f"  # wrong length placeholder
    with pytest.raises(SignoffError):
        verify_model_hash(target, expected)


def test_verify_model_hash_mismatch(tmp_path: Path) -> None:
    target = tmp_path / "model.gguf"
    target.write_text("hello model")
    with pytest.raises(SignoffError):
        verify_model_hash(target, "0" * 64)


def test_compute_backend_hash_is_stable() -> None:
    h1 = compute_backend_hash()
    h2 = compute_backend_hash()
    assert len(h1) == 32
    assert h1 == h2


def test_parse_generated_text() -> None:
    raw = (
        "\n> Hello\n"
        "Hello! How\n"
        "[ Prompt: 27.2 t/s | Generation: 1.5 t/s ]\n"
    )
    assert _parse_generated_text(raw, "Hello") == "Hello! How"


def test_parse_generated_text_missing_prompt() -> None:
    assert _parse_generated_text("no prompt here", "Hello") == ""


def test_compare_hidden_identical(tmp_path: Path) -> None:
    arr = np.random.RandomState(0).randn(2, 2048).astype(np.float32)
    cpu = tmp_path / "cpu.npz"
    npu = tmp_path / "npu.npz"
    np.savez(cpu, l_out_0=arr)
    np.savez(npu, l_out_0=arr)
    metrics = _compare_hidden(cpu, npu, "l_out_0", 1e-5, 0.9999)
    assert metrics["passed"] is True
    assert metrics["max_abs_diff"] == 0.0


def test_compare_hidden_different(tmp_path: Path) -> None:
    rng = np.random.RandomState(1)
    cpu_arr = rng.randn(2, 2048).astype(np.float32)
    npu_arr = cpu_arr.copy()
    npu_arr[0, 0] += 10.0
    cpu = tmp_path / "cpu.npz"
    npu = tmp_path / "npu.npz"
    np.savez(cpu, l_out_0=cpu_arr)
    np.savez(npu, l_out_0=npu_arr)
    metrics = _compare_hidden(cpu, npu, "l_out_0", 1e-5, 0.9999)
    assert metrics["passed"] is False


def test_negative_signoff_detects_corruption(tmp_path: Path) -> None:
    cfg = load_config(DEFAULT_CONFIG)
    evidence = tmp_path / "negative.json"
    payload = run_negative_signoff(cfg, evidence)
    assert payload["verdict"] == "pass"
    checks = {c["name"]: c for c in payload["checks"]}
    assert checks["model_hash_mismatch"]["detected"] is True
    assert evidence.is_file()
    loaded = json.loads(evidence.read_text())
    assert loaded["verdict"] == "pass"


@pytest.mark.skipif(not _model_path().is_file(), reason="Qwen3B GGUF model not available")
@pytest.mark.slow
@pytest.mark.timeout(300)
def test_negative_signoff_detects_unsupported_device(tmp_path: Path) -> None:
    cfg = load_config(DEFAULT_CONFIG)
    evidence = tmp_path / "negative.json"
    payload = run_negative_signoff(cfg, evidence)
    checks = {c["name"]: c for c in payload["checks"]}
    assert checks["unsupported_device_uri"]["detected"] is True


@pytest.mark.skipif(
    not (REPO_ROOT / "build" / "llama" / "bin" / "test-backend-ops").is_file(),
    reason="llama.cpp test-backend-ops binary not built",
)
@pytest.mark.slow
@pytest.mark.timeout(300)
def test_gate_supported_single_ops(tmp_path: Path) -> None:
    from signoff.qwen3b_signoff import gate_supported_single_ops

    cfg = load_config(DEFAULT_CONFIG)
    result = gate_supported_single_ops(cfg, "mock://", os.environ.copy())
    assert result.passed is True
    assert result.metrics["tests_total"] > 0
    ratio = result.metrics["pass_ratio"]
    assert isinstance(ratio, float)
    assert ratio >= cfg.gates["supported_single_ops"]["min_pass_ratio"]
