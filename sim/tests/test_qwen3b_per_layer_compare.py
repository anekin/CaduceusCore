"""Tests for the Qwen2.5-3B per-layer hidden-state comparison script."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "sim" / "signoff" / "qwen3b_per_layer_compare.py"


def _make_golden(
    tmp_path: Path,
    n_layers: int = 3,
    hidden_size: int = 4,
) -> tuple[Path, Dict[str, np.ndarray]]:
    data: Dict[str, Any] = {}
    for i in range(n_layers):
        data[f"l_out_{i}"] = (np.arange(hidden_size, dtype=np.float32) + i) / 10.0
    data["logits"] = np.random.randn(10).astype(np.float32)
    data["tokens"] = np.array([42], dtype=np.int64)
    meta = {
        "model": str(tmp_path / "dummy.gguf"),
        "layers": n_layers,
        "prompt": "Hello",
        "hidden_size": hidden_size,
    }
    data["metadata"] = np.array([json.dumps(meta)])
    p = tmp_path / "golden.npz"
    np.savez(p, **data)
    return p, data


def test_cli_parse_basic() -> None:
    from signoff.qwen3b_per_layer_compare import _parse_args

    args = _parse_args(["--golden", "/tmp/g.npz"])
    assert args.golden == "/tmp/g.npz"
    assert args.device == "fm://python"
    assert args.seed == 42


def test_cli_parse_full() -> None:
    from signoff.qwen3b_per_layer_compare import _parse_args

    args = _parse_args([
        "--golden", "/tmp/g.npz",
        "--device", "mock://",
        "--model", "/tmp/m.gguf",
        "--prompt", "Hi",
        "--seed", "123",
        "--output-dir", "/tmp/out",
        "--quiet",
    ])
    assert args.golden == "/tmp/g.npz"
    assert args.device == "mock://"
    assert args.model == "/tmp/m.gguf"
    assert args.prompt == "Hi"
    assert args.seed == 123
    assert args.output_dir == "/tmp/out"
    assert args.quiet is True


def test_missing_golden_prints_run_a1_first(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from signoff.qwen3b_per_layer_compare import main

    rc = main(["--golden", str(tmp_path / "missing.npz"), "--device", "mock://"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "Run A1 first" in captured.err
    assert "missing.npz" in captured.err


def test_compute_layer_metrics_perfect_match() -> None:
    from signoff.qwen3b_per_layer_compare import _compute_layer_metrics

    arr = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    metrics = _compute_layer_metrics(arr, arr)
    assert metrics["cos_sim"] == pytest.approx(1.0)
    assert metrics["max_abs_diff"] == pytest.approx(0.0)
    assert metrics["passed"] is True


def test_compute_layer_metrics_fail_thresholds() -> None:
    from signoff.qwen3b_per_layer_compare import _compute_layer_metrics

    golden = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    npu = np.array([1.0, 2.0, 3.0 + 2e-3], dtype=np.float32)
    metrics = _compute_layer_metrics(golden, npu)
    assert metrics["passed"] is False
    assert metrics["max_abs_diff"] == pytest.approx(2e-3, abs=1e-6)


def test_compare_perfect_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from signoff.qwen3b_per_layer_compare import run_per_layer_compare

    golden_path, golden_data = _make_golden(tmp_path, n_layers=3, hidden_size=4)

    def fake_run_full_forward(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {
            "generated_token_text": "Hi",
            "hidden_states": {
                i: golden_data[f"l_out_{i}"].copy() for i in range(3)
            },
        }

    def fake_capture_cpu(*args: Any, **kwargs: Any) -> Dict[int, np.ndarray]:
        return {i: golden_data[f"l_out_{i}"].copy() for i in range(3)}

    monkeypatch.setattr(
        "signoff.qwen3b_per_layer_compare.run_full_forward", fake_run_full_forward
    )
    monkeypatch.setattr(
        "signoff.qwen3b_per_layer_compare._capture_cpu_hidden_states",
        fake_capture_cpu,
    )
    monkeypatch.setattr(
        "signoff.qwen3b_per_layer_compare._read_gguf_layer_count",
        lambda p: 3,
    )

    evidence, path = run_per_layer_compare(
        golden_path=str(golden_path),
        device="fm://python",
        output_dir=tmp_path,
        quiet=True,
    )
    assert evidence["passed"] is True
    assert evidence["n_layers"] == 3
    assert evidence["summary"]["passed_layers"] == 3
    assert "elapsed_sec" in evidence
    assert evidence["elapsed_sec"] >= 0
    assert path.is_file()
    loaded = json.loads(path.read_text())
    assert loaded["passed"] is True
    assert loaded["elapsed_sec"] >= 0
    assert loaded["layer_metrics"]["0"]["cos_sim"] == pytest.approx(1.0)


def test_compare_first_and_last_only_determine_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from signoff.qwen3b_per_layer_compare import run_per_layer_compare

    golden_path, golden_data = _make_golden(tmp_path, n_layers=3, hidden_size=4)

    ref_hidden: Dict[int, np.ndarray] = {}
    npu_hidden: Dict[int, np.ndarray] = {}
    for i in range(3):
        ref_hidden[i] = golden_data[f"l_out_{i}"].copy()
        npu_hidden[i] = golden_data[f"l_out_{i}"].copy()
    # Make the middle layer fail the threshold; first/last stay perfect.
    npu_hidden[1] = npu_hidden[1] + 10.0

    def fake_run_full_forward(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {"generated_token_text": "Hi", "hidden_states": npu_hidden}

    def fake_capture_cpu(*args: Any, **kwargs: Any) -> Dict[int, np.ndarray]:
        return ref_hidden

    monkeypatch.setattr(
        "signoff.qwen3b_per_layer_compare.run_full_forward", fake_run_full_forward
    )
    monkeypatch.setattr(
        "signoff.qwen3b_per_layer_compare._capture_cpu_hidden_states",
        fake_capture_cpu,
    )
    monkeypatch.setattr(
        "signoff.qwen3b_per_layer_compare._read_gguf_layer_count",
        lambda p: 3,
    )

    evidence, _ = run_per_layer_compare(
        golden_path=str(golden_path),
        device="mock://",
        output_dir=tmp_path,
        quiet=True,
    )
    assert evidence["passed"] is True
    assert evidence["summary"]["passed_layers"] == 2
    assert 1 in evidence["summary"]["failed_layers"]
    assert evidence["layer_metrics"]["1"]["passed"] is False


def test_compare_fails_when_first_layer_bad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from signoff.qwen3b_per_layer_compare import run_per_layer_compare

    golden_path, golden_data = _make_golden(tmp_path, n_layers=3, hidden_size=4)

    ref_hidden = {i: golden_data[f"l_out_{i}"].copy() for i in range(3)}
    npu_hidden = {i: golden_data[f"l_out_{i}"].copy() for i in range(3)}
    npu_hidden[0] = npu_hidden[0] + 10.0

    def fake_run_full_forward(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {"generated_token_text": "Hi", "hidden_states": npu_hidden}

    def fake_capture_cpu(*args: Any, **kwargs: Any) -> Dict[int, np.ndarray]:
        return ref_hidden

    monkeypatch.setattr(
        "signoff.qwen3b_per_layer_compare.run_full_forward", fake_run_full_forward
    )
    monkeypatch.setattr(
        "signoff.qwen3b_per_layer_compare._capture_cpu_hidden_states",
        fake_capture_cpu,
    )
    monkeypatch.setattr(
        "signoff.qwen3b_per_layer_compare._read_gguf_layer_count",
        lambda p: 3,
    )

    evidence, _ = run_per_layer_compare(
        golden_path=str(golden_path),
        device="mock://",
        output_dir=tmp_path,
        quiet=True,
    )
    assert evidence["passed"] is False
    assert evidence["layer_metrics"]["0"]["passed"] is False
    assert evidence["layer_metrics"]["2"]["passed"] is True


def test_main_returns_nonzero_when_compare_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from signoff.qwen3b_per_layer_compare import main

    golden_path, golden_data = _make_golden(tmp_path, n_layers=2, hidden_size=4)

    ref_hidden = {i: golden_data[f"l_out_{i}"].copy() for i in range(2)}
    npu_hidden = {i: golden_data[f"l_out_{i}"].copy() for i in range(2)}
    npu_hidden[1] = npu_hidden[1] + 10.0

    def fake_run_full_forward(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {"generated_token_text": "Hi", "hidden_states": npu_hidden}

    def fake_capture_cpu(*args: Any, **kwargs: Any) -> Dict[int, np.ndarray]:
        return ref_hidden

    monkeypatch.setattr(
        "signoff.qwen3b_per_layer_compare.run_full_forward", fake_run_full_forward
    )
    monkeypatch.setattr(
        "signoff.qwen3b_per_layer_compare._capture_cpu_hidden_states",
        fake_capture_cpu,
    )
    monkeypatch.setattr(
        "signoff.qwen3b_per_layer_compare._read_gguf_layer_count",
        lambda p: 2,
    )

    rc = main([
        "--golden", str(golden_path),
        "--device", "mock://",
        "--output-dir", str(tmp_path),
        "--quiet",
    ])
    assert rc == 1


def test_learnings_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from signoff.qwen3b_per_layer_compare import run_per_layer_compare, _LEARNINGS

    learnings_backup = _LEARNINGS.read_text() if _LEARNINGS.is_file() else ""
    golden_path, golden_data = _make_golden(tmp_path, n_layers=2, hidden_size=4)

    def fake_run_full_forward(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {
            "generated_token_text": "X",
            "hidden_states": {i: golden_data[f"l_out_{i}"].copy() for i in range(2)},
        }

    def fake_capture_cpu(*args: Any, **kwargs: Any) -> Dict[int, np.ndarray]:
        return {i: golden_data[f"l_out_{i}"].copy() for i in range(2)}

    monkeypatch.setattr(
        "signoff.qwen3b_per_layer_compare.run_full_forward", fake_run_full_forward
    )
    monkeypatch.setattr(
        "signoff.qwen3b_per_layer_compare._capture_cpu_hidden_states",
        fake_capture_cpu,
    )
    monkeypatch.setattr(
        "signoff.qwen3b_per_layer_compare._read_gguf_layer_count",
        lambda p: 2,
    )

    try:
        _, _ = run_per_layer_compare(
            golden_path=str(golden_path),
            device="mock://",
            output_dir=tmp_path,
            quiet=True,
        )
        text = _LEARNINGS.read_text()
        assert "A3 — per-layer hidden-state comparison" in text
        assert "Overall passed" in text
    finally:
        _LEARNINGS.write_text(learnings_backup, encoding="utf-8")
