"""Tests for the Qwen2.5-3B full forward runner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "sim" / "signoff" / "qwen3b_full_forward.py"
DEFAULT_MODEL = Path.home() / "models" / "qwen2.5-3b-instruct-q4_k_m.gguf"
LLAMA_CLI = REPO_ROOT / "build" / "llama" / "bin" / "llama"
NPU_SO = REPO_ROOT / "build" / "llama" / "bin" / "libggml-npu.so"


def _requires_binaries() -> bool:
    return LLAMA_CLI.is_file() and NPU_SO.is_file()


def _requires_model() -> bool:
    return DEFAULT_MODEL.is_file()


# ── Unit tests (no binaries needed) ──────────────────────────────────


def test_read_gguf_layer_count_returns_int(tmp_path: Path) -> None:
    """_read_gguf_layer_count returns the fallback constant when no real GGUF."""
    from signoff.qwen3b_full_forward import _read_gguf_layer_count

    fake = tmp_path / "fake.gguf"
    fake.write_bytes(b"not a real gguf file")
    result = _read_gguf_layer_count(str(fake))
    assert isinstance(result, int)
    assert result == 36


def test_load_golden_missing_returns_none(tmp_path: Path) -> None:
    from signoff.qwen3b_full_forward import _load_golden

    assert _load_golden(str(tmp_path / "nonexistent.npz")) is None


def test_load_golden_ok(tmp_path: Path) -> None:
    from signoff.qwen3b_full_forward import _load_golden

    p = tmp_path / "test.npz"
    np.savez(p, tokens=np.array([42]), logits=np.random.randn(100).astype(np.float32))
    data = _load_golden(str(p))
    assert data is not None
    assert int(data["tokens"][0]) == 42
    assert data["logits"].shape == (100,)


def test_resolve_golden_token(tmp_path: Path) -> None:
    from signoff.qwen3b_full_forward import _resolve_golden_token

    p = tmp_path / "g.npz"
    np.savez(p, tokens=np.array([12345]), logits=np.array([0.1, 0.2, 0.3]))
    golden = dict(np.load(p, allow_pickle=True))
    tid, logits = _resolve_golden_token(golden)
    assert tid == 12345
    assert logits is not None
    assert logits.shape == (3,)


def test_resolve_golden_token_none() -> None:
    from signoff.qwen3b_full_forward import _resolve_golden_token

    tid, logits = _resolve_golden_token(None)
    assert tid is None
    assert logits is None


def test_logits_top5() -> None:
    from signoff.qwen3b_full_forward import _logits_top5

    logits = np.array([0.1, 9.9, 2.0, 1.5, 0.5, 8.0, 3.0, 0.2, 7.5, 4.0], dtype=np.float32)
    t5 = _logits_top5(logits)
    assert t5 is not None
    assert len(t5) == 5
    assert t5[0] == 1  # 9.9
    assert t5[1] == 5  # 8.0
    assert t5[2] == 8  # 7.5


def test_logits_top5_none() -> None:
    from signoff.qwen3b_full_forward import _logits_top5

    assert _logits_top5(None) is None


def test_logits_top5_short() -> None:
    from signoff.qwen3b_full_forward import _logits_top5

    assert _logits_top5(np.array([1.0, 2.0], dtype=np.float32)) == 1
    assert _logits_top5(np.array([0.5], dtype=np.float32)) == 0


def test_eval_pass_mock() -> None:
    from signoff.qwen3b_full_forward import _eval_pass

    assert _eval_pass("", "", is_mock=True) is True
    assert _eval_pass("Hello", "", is_mock=True) is True


def test_eval_pass_real() -> None:
    from signoff.qwen3b_full_forward import _eval_pass

    assert _eval_pass("Hi", "Hi", is_mock=False) is True
    assert _eval_pass("Hi", "Bye", is_mock=False) is False
    assert _eval_pass("", "Hi", is_mock=False) is False
    assert _eval_pass("Hi", "", is_mock=False) is False


def test_parse_op_dispatch_empty() -> None:
    from signoff.qwen3b_full_forward import _parse_op_dispatch

    result = _parse_op_dispatch("no dispatch info here")
    assert result["npu_ops_executed"] == 0
    assert result["cpu_fallback_ops"] == []


def test_parse_op_dispatch_npu_ops() -> None:
    from signoff.qwen3b_full_forward import _parse_op_dispatch

    stderr = (
        "[NPU] OP node 0 MUL_MAT (blk.0.attn_q): NPU\n"
        "[NPU] OP node 1 RMS_NORM (blk.0.attn_norm): NPU\n"
        "[NPU] OP node 2 ROPE (blk.0.rope): CPU fallback: unsupported mode 40\n"
    )
    result = _parse_op_dispatch(stderr)
    assert result["npu_ops_executed"] == 2
    assert len(result["cpu_fallback_ops"]) == 1
    assert "ROPE" in result["cpu_fallback_ops"][0]


def test_parse_op_dispatch_counts_all_op_nodes() -> None:
    """op_node_count counts every [NPU] OP node line, NPU and fallback alike."""
    from signoff.qwen3b_full_forward import _parse_op_dispatch

    stderr = (
        "[NPU] OP node 0 MUL_MAT (blk.0.attn_q): NPU\n"
        "[NPU] OP node 1 RMS_NORM (norm-0): CPU fallback: mock device (no real NPU)\n"
        "[NPU] OP node 2 ROPE (Qcur-0): CPU fallback: mock device (no real NPU)\n"
    )
    result = _parse_op_dispatch(stderr)
    assert result["op_node_count"] == 3
    assert result["npu_ops_executed"] == 1
    assert len(result["cpu_fallback_ops"]) == 2


def test_parse_op_dispatch_last_layer_suffix() -> None:
    """Layer index is read from trailing -N labels (mock/fallback format)."""
    from signoff.qwen3b_full_forward import _parse_op_dispatch

    stderr = (
        "[NPU] OP node 0 RMS_NORM (norm-0): CPU fallback: mock device (no real NPU)\n"
        "[NPU] OP node 1 MUL (attn_norm-35): CPU fallback: mock device (no real NPU)\n"
        "[NPU] OP node 2 MUL_MAT (result_output): CPU fallback: mock device (no real NPU)\n"
    )
    result = _parse_op_dispatch(stderr)
    assert result["op_node_count"] == 3
    assert result["last_layer"] == 35


def test_parse_op_dispatch_last_layer_blk() -> None:
    """Layer index is read from blk.N. labels (real-NPU format)."""
    from signoff.qwen3b_full_forward import _parse_op_dispatch

    stderr = (
        "[NPU] OP node 0 RMS_NORM (blk.0.attn_norm): NPU\n"
        "[NPU] OP node 1 ADD (blk.7.l_out): NPU\n"
        "[NPU] OP node 2 MUL_MAT (output): NPU\n"
    )
    result = _parse_op_dispatch(stderr)
    assert result["last_layer"] == 7


def test_failure_evidence_reports_last_layer() -> None:
    """A mid-traversal crash records how far the graph traversal got."""
    from signoff.qwen3b_full_forward import _failure_evidence

    partial = (
        "[NPU] OP node 0 RMS_NORM (norm-0): CPU fallback: mock device (no real NPU)\n"
        "[NPU] OP node 1 MUL (attn_norm-0): CPU fallback: mock device (no real NPU)\n"
        "[NPU] OP node 2 ADD (Qcur-1): CPU fallback: mock device (no real NPU)\n"
    )
    ev = _failure_evidence(
        "/tmp/m.gguf", "mock://", "mock://", 36, 42, partial,
        "llama cli failed (exit -11)", 1.234,
    )
    assert ev["passed"] is False
    assert ev["crash"] is True
    assert ev["op_node_count"] == 3
    assert ev["last_layer"] == 1
    assert ev["elapsed_sec"] == 1.234
    assert "exit -11" in ev["crash_reason"]


def test_write_evidence(tmp_path: Path) -> None:
    from signoff.qwen3b_full_forward import _write_evidence

    evidence = {"test": True, "n_layers": 36}
    path = _write_evidence(evidence, evidence_dir=tmp_path)
    assert path.is_file()
    loaded = json.loads(path.read_text())
    assert loaded["test"] is True
    assert loaded["n_layers"] == 36


def test_cli_parse_basic() -> None:
    from signoff.qwen3b_full_forward import _parse_args

    args = _parse_args(["--model", "/tmp/test.gguf"])
    assert args.model == "/tmp/test.gguf"
    assert args.device == "fm://python"
    assert args.prompt == "Hello"
    assert args.layers is None
    assert args.seed == 42


def test_cli_parse_full() -> None:
    from signoff.qwen3b_full_forward import _parse_args

    args = _parse_args([
        "--model", "/tmp/m.gguf",
        "--device", "mock://",
        "--prompt", "Hi",
        "--layers", "10",
        "--golden", "/tmp/g.npz",
        "--seed", "123",
        "--quiet",
    ])
    assert args.model == "/tmp/m.gguf"
    assert args.device == "mock://"
    assert args.prompt == "Hi"
    assert args.layers == 10
    assert args.golden == "/tmp/g.npz"
    assert args.seed == 123
    assert args.quiet is True


# ── Integration tests (require binaries) ─────────────────────────────


@pytest.mark.skipif(not _requires_binaries(), reason="llama cli binary not built")
@pytest.mark.skipif(not _requires_model(), reason=f"model not found: {DEFAULT_MODEL}")
@pytest.mark.slow
def test_mock_device_traversal() -> None:
    """The mock:// path exits 0 without a device server (traversal-only)."""
    env = os.environ.copy()
    env["PYTHONPATH"] = "sim:gen"
    proc = subprocess.run(
        [sys.executable, str(RUNNER),
         "--model", str(DEFAULT_MODEL),
         "--device", "mock://",
         "--prompt", "Hello",
         "--quiet"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"mock runner failed (rc={proc.returncode}):\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr[-1000:]}"
    )


@pytest.mark.skipif(not _requires_binaries(), reason="llama cli binary not built")
@pytest.mark.skipif(not _requires_model(), reason=f"model not found: {DEFAULT_MODEL}")
@pytest.mark.slow
def test_evidence_json_is_valid() -> None:
    """Evidence JSON produced by the runner is well-formed."""
    env = os.environ.copy()
    env["PYTHONPATH"] = "sim:gen"
    proc = subprocess.run(
        [sys.executable, str(RUNNER),
         "--model", str(DEFAULT_MODEL),
         "--device", "mock://",
         "--prompt", "Test",
         "--quiet"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0

    from signoff.qwen3b_full_forward import _EVIDENCE_DIR

    files = sorted(_EVIDENCE_DIR.glob("qwen-full-forward-*.json"))
    assert len(files) >= 1
    latest = files[-1]
    data = json.loads(latest.read_text())
    assert "prompt" in data
    assert "device" in data
    assert "generated_token_id" in data
    assert "generated_token_text" in data
    assert "logits_top5" in data
    assert "npu_ops_executed" in data
    assert "passed" in data
    assert "elapsed_sec" in data
    assert data["elapsed_sec"] >= 0


@pytest.mark.skipif(not _requires_binaries(), reason="llama cli binary not built")
@pytest.mark.skipif(not _requires_model(), reason=f"model not found: {DEFAULT_MODEL}")
@pytest.mark.slow
def test_layers_flag_override() -> None:
    """--layers N overrides GGUF metadata count."""
    env = os.environ.copy()
    env["PYTHONPATH"] = "sim:gen"
    proc = subprocess.run(
        [sys.executable, str(RUNNER),
         "--model", str(DEFAULT_MODEL),
         "--device", "mock://",
         "--prompt", "A",
         "--layers", "1",
         "--quiet"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0

    from signoff.qwen3b_full_forward import _EVIDENCE_DIR

    files = sorted(_EVIDENCE_DIR.glob("qwen-full-forward-*.json"))
    assert len(files) >= 1
    data = json.loads(files[-1].read_text())
    assert data.get("n_layers") == 1


@pytest.mark.skipif(not _requires_binaries(), reason="llama cli binary not built")
@pytest.mark.skipif(not _requires_model(), reason=f"model not found: {DEFAULT_MODEL}")
@pytest.mark.slow
def test_mock_device_op_node_count_meets_threshold() -> None:
    """mock:// traversal visits the full 36-layer graph: op node count >= 612."""
    env = os.environ.copy()
    env["PYTHONPATH"] = "sim:gen"
    proc = subprocess.run(
        [sys.executable, str(RUNNER),
         "--device", "mock://",
         "--layers", "36",
         "--prompt", "Hello"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert proc.returncode == 0, (
        f"mock runner failed (rc={proc.returncode}):\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr[-2000:]}"
    )

    ev_line = next(
        (l for l in proc.stdout.splitlines() if "Evidence:" in l), None
    )
    assert ev_line is not None, proc.stdout
    ev_path = Path(ev_line.split("Evidence:")[1].strip())
    data = json.loads(ev_path.read_text())

    from signoff.qwen3b_full_forward import _MOCK_MIN_OP_NODES

    assert data["passed"] is True
    assert data["op_node_count"] >= _MOCK_MIN_OP_NODES
    assert data["last_layer"] == 35
    assert data["layers"] == 36
    assert data["device"] == "mock://"
