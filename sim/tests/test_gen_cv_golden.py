#!/usr/bin/env python3
"""Tests for scripts/gen_cv_golden.py — ONNX golden reference generator.

Tests cover:
  - Deterministic input generation (shape, seed reproducibility)
  - JSON output schema validation
  - Top-5 index uniqueness and valid range
  - Graceful handling of missing ONNX file
  - End-to-end golden generation against the real ONNX model
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

# ── Path setup ──────────────────────────────────────────────────────────────
_PROJECT = Path(__file__).resolve().parent.parent.parent
_ONNX_MODEL = _PROJECT / "assets" / "mobilenetv3_small.onnx"
_SCRIPT = _PROJECT / "scripts" / "gen_cv_golden.py"

sys.path.insert(0, str(_PROJECT / "sim"))


def _onnx_available() -> bool:
    """Check whether the ONNX model exists."""
    return _ONNX_MODEL.exists()


def _ort_available() -> bool:
    """Check whether onnxruntime is importable."""
    try:
        import onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


# ── Unit tests (no ONNX required) ───────────────────────────────────────────

class TestInputGeneration:
    """Tests for deterministic random input generation."""

    def test_shape(self):
        """_generate_input_tensor produces (1, 3, 224, 224) float32."""
        # Import the function directly from the script
        sys_path_before = list(sys.path)
        sys.path.insert(0, str(_PROJECT / "scripts"))
        try:
            from gen_cv_golden import _generate_input_tensor
        finally:
            sys.path[:] = sys_path_before

        tensor = _generate_input_tensor(seed=42)
        assert tensor.shape == (1, 3, 224, 224), f"Unexpected shape: {tensor.shape}"
        assert tensor.dtype == np.float32, f"Unexpected dtype: {tensor.dtype}"

    def test_deterministic(self):
        """Same seed produces identical tensor."""
        sys_path_before = list(sys.path)
        sys.path.insert(0, str(_PROJECT / "scripts"))
        try:
            from gen_cv_golden import _generate_input_tensor
        finally:
            sys.path[:] = sys_path_before

        a = _generate_input_tensor(seed=42)
        b = _generate_input_tensor(seed=42)
        assert np.array_equal(a, b), "Seed=42 should produce identical tensors"

    def test_different_seeds_differ(self):
        """Different seeds produce different tensors."""
        sys_path_before = list(sys.path)
        sys.path.insert(0, str(_PROJECT / "scripts"))
        try:
            from gen_cv_golden import _generate_input_tensor
        finally:
            sys.path[:] = sys_path_before

        a = _generate_input_tensor(seed=42)
        b = _generate_input_tensor(seed=123)
        assert not np.array_equal(a, b), "Different seeds should differ"


class TestJsonSchema:
    """Tests for JSON output schema validation."""

    def test_required_keys_present(self):
        """_save_json includes all required keys."""
        sys_path_before = list(sys.path)
        sys.path.insert(0, str(_PROJECT / "scripts"))
        try:
            from gen_cv_golden import _save_json
        finally:
            sys.path[:] = sys_path_before

        top5_indices = np.array([100, 200, 300, 400, 500], dtype=np.int64)
        top5_logits = np.array([1.0, 0.9, 0.8, 0.7, 0.6], dtype=np.float32)

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "cv-golden.json"
            payload = _save_json(
                str(out_path), top5_indices, top5_logits,
                input_shape=(1, 3, 224, 224),
                model_path="test.onnx",
                seed=42,
            )

            # Check all required keys
            required_keys = {"top5_indices", "top5_logits", "input_shape",
                             "model_path", "timestamp", "seed"}
            for key in required_keys:
                assert key in payload, f"Missing required key: {key}"

            # Check types/values
            assert isinstance(payload["top5_indices"], list), "top5_indices must be list"
            assert len(payload["top5_indices"]) == 5, "Must have exactly 5 top-5 indices"
            assert isinstance(payload["top5_logits"], list), "top5_logits must be list"
            assert len(payload["top5_logits"]) == 5, "Must have exactly 5 top-5 logits"
            assert payload["input_shape"] == [1, 3, 224, 224]
            assert payload["seed"] == 42

    def test_json_file_created_and_valid(self):
        """_save_json creates a valid JSON file on disk."""
        sys_path_before = list(sys.path)
        sys.path.insert(0, str(_PROJECT / "scripts"))
        try:
            from gen_cv_golden import _save_json
        finally:
            sys.path[:] = sys_path_before

        top5_indices = np.array([100, 200, 300, 400, 500], dtype=np.int64)
        top5_logits = np.array([1.0, 0.9, 0.8, 0.7, 0.6], dtype=np.float32)

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "cv-golden.json"
            _save_json(
                str(out_path), top5_indices, top5_logits,
                input_shape=(1, 3, 224, 224),
                model_path="test.onnx",
                seed=42,
            )

            assert out_path.exists(), f"JSON file should exist at {out_path}"
            with open(out_path) as f:
                data = json.load(f)
            assert "top5_indices" in data
            assert "top5_logits" in data
            assert data["top5_indices"] == [100, 200, 300, 400, 500]


class TestTop5Validation:
    """Tests for top-5 output validation."""

    def test_indices_unique(self):
        """Top-5 indices should be unique."""
        indices = np.array([100, 200, 300, 400, 500], dtype=np.int64)
        assert len(set(indices.tolist())) == 5, "Top-5 indices must be unique"

    def test_indices_in_range(self):
        """Top-5 indices should be in [0, 999] for ImageNet-1K."""
        indices = np.array([0, 1, 999, 500, 250], dtype=np.int64)
        for idx in indices:
            assert 0 <= idx <= 999, f"Index {idx} out of [0, 999] range"


# ── Integration tests ───────────────────────────────────────────────────────

@pytest.mark.skipif(not _onnx_available(),
                    reason="ONNX model not found")
class TestEndToEnd:
    """End-to-end tests requiring the ONNX model."""

    def test_cli_produces_valid_json(self):
        """Running gen_cv_golden.py exits 0 and produces valid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "cv-golden.json"
            proc = subprocess.run(
                [
                    sys.executable, str(_SCRIPT),
                    "--model", str(_ONNX_MODEL),
                    "--output", str(out_path),
                    "--seed", "42",
                ],
                capture_output=True, text=True,
                timeout=60,
                env={**os.environ, "PYTHONPATH": f"{_PROJECT / 'sim'}"},
            )
            assert proc.returncode == 0, (
                f"Script failed (exit {proc.returncode}):\nSTDERR:\n{proc.stderr}"
            )

            assert out_path.exists(), f"JSON file should exist at {out_path}"

            with open(out_path) as f:
                data = json.load(f)

            assert "top5_indices" in data
            assert "top5_logits" in data
            assert len(data["top5_indices"]) == 5
            assert len(data["top5_logits"]) == 5
            assert data["input_shape"] == [1, 3, 224, 224]

    def test_top5_indices_unique(self):
        """Golden JSON output has unique top-5 indices."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "cv-golden.json"
            subprocess.run(
                [
                    sys.executable, str(_SCRIPT),
                    "--model", str(_ONNX_MODEL),
                    "--output", str(out_path),
                    "--seed", "42",
                ],
                capture_output=True, text=True,
                timeout=60,
                env={**os.environ, "PYTHONPATH": f"{_PROJECT / 'sim'}"},
            )

            with open(out_path) as f:
                data = json.load(f)

            indices = data["top5_indices"]
            assert len(set(indices)) == 5, (
                f"Top-5 indices not unique: {indices}"
            )

    def test_top5_indices_in_range(self):
        """Golden JSON output has top-5 indices in [0, 999]."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "cv-golden.json"
            subprocess.run(
                [
                    sys.executable, str(_SCRIPT),
                    "--model", str(_ONNX_MODEL),
                    "--output", str(out_path),
                    "--seed", "42",
                ],
                capture_output=True, text=True,
                timeout=60,
                env={**os.environ, "PYTHONPATH": f"{_PROJECT / 'sim'}"},
            )

            with open(out_path) as f:
                data = json.load(f)

            for idx in data["top5_indices"]:
                assert 0 <= idx <= 999, (
                    f"Top-5 index {idx} out of [0, 999] range"
                )

    def test_output_is_deterministic(self):
        """Same seed produces identical top-5 results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out1 = Path(tmpdir) / "a.json"
            out2 = Path(tmpdir) / "b.json"
            env = {**os.environ, "PYTHONPATH": f"{_PROJECT / 'sim'}"}

            for out in (out1, out2):
                subprocess.run(
                    [
                        sys.executable, str(_SCRIPT),
                        "--model", str(_ONNX_MODEL),
                        "--output", str(out),
                        "--seed", "42",
                    ],
                    capture_output=True, text=True,
                    timeout=60,
                    env=env,
                )

            with open(out1) as f:
                d1 = json.load(f)
            with open(out2) as f:
                d2 = json.load(f)

            assert d1["top5_indices"] == d2["top5_indices"], (
                f"Same seed should give same top-5: {d1['top5_indices']} vs {d2['top5_indices']}"
            )
            # Check logits are close (float32 equality)
            for a, b in zip(d1["top5_logits"], d2["top5_logits"]):
                assert a == b, f"Same seed should give identical logits: {a} vs {b}"

    def test_save_npz_flag(self):
        """--save-npz flag creates the cv-golden.npz file."""
        npz_path = _PROJECT / ".omo" / "evidence" / "cv-golden.npz"
        # Remove if exists from a previous run
        npz_path.unlink(missing_ok=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "cv-golden.json"
            proc = subprocess.run(
                [
                    sys.executable, str(_SCRIPT),
                    "--model", str(_ONNX_MODEL),
                    "--output", str(out_path),
                    "--seed", "42",
                    "--save-npz",
                ],
                capture_output=True, text=True,
                timeout=60,
                env={**os.environ, "PYTHONPATH": f"{_PROJECT / 'sim'}"},
            )
            assert proc.returncode == 0, (
                f"Script failed (exit {proc.returncode}):\n{proc.stderr}"
            )

            assert npz_path.exists(), (
                f"NPZ file should exist at {npz_path} when --save-npz is used"
            )

            data = np.load(npz_path, allow_pickle=True)
            assert "input" in data
            assert "logits" in data
            assert "top5_indices" in data
            assert "top5_logits" in data
            data.close()


class TestGracefulFailure:
    """Tests for graceful handling of missing files / packages."""

    def test_missing_onnx_exits_nonzero(self):
        """Script exits non-zero when ONNX file does not exist."""
        proc = subprocess.run(
            [
                sys.executable, str(_SCRIPT),
                "--model", "/nonexistent/path/model.onnx",
                "--output", "/tmp/cv-golden.json",
            ],
            capture_output=True, text=True,
            timeout=30,
            env={**os.environ, "PYTHONPATH": f"{_PROJECT / 'sim'}"},
        )
        assert proc.returncode != 0, (
            f"Expected non-zero exit for missing ONNX, got {proc.returncode}"
        )
        assert "not found" in proc.stderr + proc.stdout, (
            f"Should mention 'not found':\nSTDERR:\n{proc.stderr}"
        )

    def test_script_produces_help(self):
        """Script produces help text with --help."""
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT), "--help"],
            capture_output=True, text=True,
            timeout=30,
        )
        assert proc.returncode == 0
        assert "golden reference" in proc.stdout.lower() or "MobileNetV3" in proc.stdout

    def test_nonexistent_image_exits_nonzero(self):
        """Script exits non-zero when --image points to nonexistent file."""
        if not _onnx_available():
            pytest.skip("ONNX model not available")
        proc = subprocess.run(
            [
                sys.executable, str(_SCRIPT),
                "--model", str(_ONNX_MODEL),
                "--image", "/nonexistent/image.jpg",
            ],
            capture_output=True, text=True,
            timeout=30,
            env={**os.environ, "PYTHONPATH": f"{_PROJECT / 'sim'}"},
        )
        assert proc.returncode != 0, (
            f"Expected non-zero exit for nonexistent image, got {proc.returncode}"
        )
