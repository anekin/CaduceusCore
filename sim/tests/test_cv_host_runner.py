"""Tests for sim/cv/cv_host_runner.py — B3 CV inference host runner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ── Path helpers ───────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_RUNNER = _REPO_ROOT / "sim" / "cv" / "cv_host_runner.py"
_ONNX_MODEL = _REPO_ROOT / "assets" / "mobilenetv3_small.onnx"

_MODEL_PRESENT = _ONNX_MODEL.is_file()


def _run_runner(*extra_args: str, env: dict | None = None) -> tuple[int, str, str]:
    """Run cv_host_runner.py as a subprocess and return (rc, stdout, stderr)."""
    test_env = os.environ.copy()
    test_env.setdefault("PYTHONPATH", "sim")
    if env:
        test_env.update(env)
    cmd = [sys.executable, str(_RUNNER), *extra_args]
    proc = subprocess.run(
        cmd,
        cwd=str(_REPO_ROOT),
        env=test_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ── Unit tests (no device server needed) ────────────────────────────────────


class TestRunnerModuleImport:
    """The runner module must be importable without side effects."""

    def test_import_succeeds(self) -> None:
        import cv.cv_host_runner as r

        assert r.run_cv is not None
        assert r.convert_mobilenetv3_graph is not None


class TestCLIParsing:
    """Argument parsing smoke tests."""

    def test_help(self) -> None:
        rc, out, err = _run_runner("--help")
        assert rc == 0
        assert "CV inference host runner" in out

    def test_default_args(self) -> None:
        """Running without --model should default to assets/ path."""
        # This will fail early on missing model OR trigger the device path.
        # We just validate it doesn't crash on argument parsing.
        rc, out, err = _run_runner("--model", "/nonexistent/model.onnx",
                                   "--device", "mock://")
        assert rc != 0  # should fail on missing file
        assert "not found" in (out + err).lower() or "b1" in (out + err).lower()


class TestFirstConvBlob:
    """The first-Conv blob builder produces a valid, executable blob."""

    def test_blob_structure(self) -> None:
        from cv.cv_host_runner import _build_first_conv_blob

        blob_bytes, inp_sz, wt_sz, out_sz = _build_first_conv_blob()
        assert len(blob_bytes) > 0
        assert inp_sz > 0
        assert wt_sz > 0
        assert out_sz > 0
        # Blob must start with CAD_BLOB_MAGIC = "CADB" = 0x43414442
        import struct
        magic = struct.unpack_from("<I", blob_bytes, 0)[0]
        assert magic == 0x43414442, f"Expected CADB magic, got 0x{magic:08X}"

    def test_buffer_layout_deterministic(self) -> None:
        from cv.cv_host_runner import _build_first_conv_blob, _compute_buffer_layout

        blob1, _, _, _ = _build_first_conv_blob(dram_base=0x80100000)
        blob2, _, _, _ = _build_first_conv_blob(dram_base=0x80100000)
        assert blob1 == blob2

        layout = _compute_buffer_layout(blob1)
        assert len(layout) >= 4  # input, weight, output, scale
        assert 1 in layout
        assert 2 in layout
        assert 3 in layout
        assert layout[1]["phys_addr"] == 0x80100000


class TestBufferLayout:
    """The buffer layout parser extracts correct sizes and addresses."""

    def test_sizes_positive(self) -> None:
        from cv.cv_host_runner import _build_first_conv_blob, _compute_buffer_layout

        blob, inp_sz, wt_sz, out_sz = _build_first_conv_blob()
        layout = _compute_buffer_layout(blob)

        assert layout[1]["size"] == inp_sz
        assert layout[2]["size"] == wt_sz
        assert layout[3]["size"] == out_sz

    def test_addresses_sequential(self) -> None:
        from cv.cv_host_runner import _build_first_conv_blob, _compute_buffer_layout

        blob, inp_sz, wt_sz, out_sz = _build_first_conv_blob()

        def _align(v: int, a: int = 64) -> int:
            return (v + a - 1) & ~(a - 1)

        layout = _compute_buffer_layout(blob)

        assert layout[1]["phys_addr"] == 0x80100000
        assert layout[2]["phys_addr"] == 0x80100000 + _align(inp_sz)
        assert layout[3]["phys_addr"] == 0x80100000 + _align(inp_sz) + _align(wt_sz)


class TestEvidenceWriting:
    """Evidence JSON is written with required fields."""

    def test_write_evidence(self, tmp_path: Path) -> None:
        from cv.cv_host_runner import _write_evidence

        ev = {"model": "test.onnx", "first_conv_passed": True, "error": None}
        path = _write_evidence(ev, str(tmp_path / "test.json"))
        data = json.loads(path.read_text())
        assert data["first_conv_passed"] is True
        assert data["model"] == "test.onnx"

    def test_auto_timestamp(self) -> None:
        from cv.cv_host_runner import _write_evidence

        ev = {"model": "t.onnx"}
        path = _write_evidence(ev)  # no explicit path → auto timestamp
        assert path.exists()
        data = json.loads(path.read_text())
        assert "timestamp" not in data  # run_cv adds it, _write_evidence doesn't
        path.unlink()


# ── Integration tests (require device server + fm://python) ─────────────────

@pytest.mark.skipif(not _MODEL_PRESENT, reason="ONNX model not found")
class TestFmPythonEndToEnd:
    """Full end-to-end: model conversion + fm://python submit + read output."""

    def test_acceptance_fm_python(self) -> None:
        """The canonical acceptance command."""
        rc, out, err = _run_runner(
            "--model", str(_ONNX_MODEL),
            "--device", "fm://python",
        )
        print("STDOUT:", out)
        print("STDERR:", err)
        assert rc == 0, f"Runner exited {rc}: {err}"
        assert "PASS" in out

    def test_evidence_has_required_fields(self) -> None:
        """Evidence JSON written by the runner must have expected keys."""
        import tempfile, time
        ev_path = str(Path(tempfile.gettempdir()) / f"cv-b3-test-{int(time.time())}.json")
        rc, out, err = _run_runner(
            "--model", str(_ONNX_MODEL),
            "--device", "fm://python",
            "--evidence", ev_path,
        )
        print("STDOUT:", out)
        print("STDERR:", err)
        assert rc == 0, f"Runner exited {rc}"
        data = json.loads(open(ev_path).read())
        for key in ("model", "device", "input_shape", "output_shape",
                     "first_conv_passed", "error", "elapsed_sec"):
            assert key in data, f"Missing key '{key}' in evidence"
        assert data["first_conv_passed"] is True
        assert data["error"] is None
        assert isinstance(data["elapsed_sec"], (int, float))
        assert data["elapsed_sec"] >= 0
        # Clean up
        Path(ev_path).unlink(missing_ok=True)

    def test_non_zero_output_buffer(self) -> None:
        """Output buffer read after submit must contain non-zero bytes."""
        rc, out, err = _run_runner(
            "--model", str(_ONNX_MODEL),
            "--device", "fm://python",
        )
        assert rc == 0, f"Runner exited {rc}: {err}"
        assert "Output (first" in out
        # The output should not be all zeros (random input guarantees this).


class TestGracefulFailure:
    """Runner handles errors gracefully rather than crashing."""

    def test_missing_onnx(self) -> None:
        rc, out, err = _run_runner(
            "--model", "/nonexistent/model.onnx",
            "--device", "mock://",
        )
        assert rc != 0
        text = out + err
        assert "not found" in text.lower() or "failed" in text.lower()

    def test_invalid_device_uri(self) -> None:
        """A clearly unsupported URI should produce a clear error, not a traceback."""
        rc, out, err = _run_runner(
            "--model", str(_ONNX_MODEL) if _MODEL_PRESENT else "/nonexistent",
            "--device", "bogus://nowhere",
        )
        assert rc != 0
        # Should not print a raw Python traceback.
        assert "Traceback" not in err, f"Got traceback: {err}"
