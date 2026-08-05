"""Tests for B4: CV model execution path in device_server."""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_RUNNER = _REPO_ROOT / "sim" / "cv" / "cv_host_runner.py"
_ONNX_MODEL = _REPO_ROOT / "assets" / "mobilenetv3_small.onnx"
_MODEL_PRESENT = _ONNX_MODEL.is_file()


def _run_runner(*extra_args: str, env: dict | None = None) -> tuple[int, str, str]:
    test_env = os.environ.copy()
    test_env.setdefault("PYTHONPATH", "sim")
    if env:
        test_env.update(env)
    cmd = [sys.executable, str(_RUNNER), *extra_args]
    proc = subprocess.run(
        cmd, cwd=str(_REPO_ROOT), env=test_env,
        capture_output=True, text=True, timeout=120,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ── Unit tests: _is_cv_blob detection ───────────────────────────────────────


class TestIsCVBlobDetection:

    _DRAM_BASE = 0x80000000  # Addr.DRAM
    _DESC_AT_DRAM = struct.pack("<15I", _DRAM_BASE, *([0] * 14))
    _DESC_ABOVE_DRAM = struct.pack("<15I", 0x80100000, *([0] * 14))

    def test_mmul_only_is_cv(self) -> None:
        from sim.device_server import FmDeviceServer, RING_ENTRY_SIZE
        ring = struct.pack("<III", 0x00, 0, 0) + b"\x00" * 12
        assert FmDeviceServer._is_cv_blob(ring, self._DESC_AT_DRAM, 1) is True

    def test_dma_copy_is_not_cv(self) -> None:
        from sim.device_server import FmDeviceServer, RING_ENTRY_SIZE
        ring = struct.pack("<III", 0x09, 0, 0) + b"\x00" * 12
        assert FmDeviceServer._is_cv_blob(ring, self._DESC_AT_DRAM, 1) is False

    def test_pcie_dma_is_not_cv(self) -> None:
        from sim.device_server import FmDeviceServer, RING_ENTRY_SIZE
        ring = struct.pack("<III", 0x07, 0, 0) + b"\x00" * 12
        assert FmDeviceServer._is_cv_blob(ring, self._DESC_AT_DRAM, 1) is False

    def test_dma_store_is_not_cv(self) -> None:
        from sim.device_server import FmDeviceServer, RING_ENTRY_SIZE
        ring = struct.pack("<III", 0x0A, 0, 0) + b"\x00" * 12
        assert FmDeviceServer._is_cv_blob(ring, self._DESC_AT_DRAM, 1) is False

    def test_mixed_cv_ops_is_cv(self) -> None:
        from sim.device_server import FmDeviceServer, RING_ENTRY_SIZE
        ring = bytearray()
        for op in (0x00, 0x04, 0x0F, 0x10, 0x12):
            ring.extend(struct.pack("<III", op, 0, 0) + b"\x00" * 12)
        assert FmDeviceServer._is_cv_blob(bytes(ring), self._DESC_AT_DRAM, 5) is True

    def test_unique_address_cv_blob_is_cv(self) -> None:
        """Blobs with only CV ops (no DMA) at unique addresses are still CV blobs."""
        from sim.device_server import FmDeviceServer, RING_ENTRY_SIZE
        ring = struct.pack("<III", 0x00, 0, 0) + b"\x00" * 12
        assert FmDeviceServer._is_cv_blob(ring, self._DESC_ABOVE_DRAM, 1) is True

    def test_barrier_with_cv_ops_is_cv(self) -> None:
        from sim.device_server import FmDeviceServer, RING_ENTRY_SIZE
        ring = struct.pack("<III", 0x00, 0, 0) + b"\x00" * 12
        ring += struct.pack("<III", 0xFF, 0, 0) + b"\x00" * 12
        assert FmDeviceServer._is_cv_blob(ring, self._DESC_AT_DRAM, 2) is True


# ── Unit tests: standalone CV blob builders ──────────────────────────────────


class TestCVBlobBuilders:

    def test_mmul_blob_has_cadb_magic(self) -> None:
        blob = _build_cv_blob_mmul()
        magic = struct.unpack_from("<I", blob, 0)[0]
        assert magic == 0x43414442

    def test_unsupported_sfu_blob_has_cadb_magic(self) -> None:
        blob = _build_cv_blob_unsupported_sfu()
        magic = struct.unpack_from("<I", blob, 0)[0]
        assert magic == 0x43414442

    def test_multi_op_blob_has_cadb_magic(self) -> None:
        blob = _build_cv_blob_multi_op()
        magic = struct.unpack_from("<I", blob, 0)[0]
        assert magic == 0x43414442


# ── Integration tests: full-graph runner ────────────────────────────────────


_RUNTIME_LIB = _REPO_ROOT / "build" / "software" / "libcaduceus_runtime.so"
_RUNTIME_AVAILABLE = _RUNTIME_LIB.is_file()


@pytest.mark.skipif(not _RUNTIME_AVAILABLE,
                    reason="libcaduceus_runtime.so not built")
class TestFullGraphRunner:

    def test_full_graph_flag_exits_zero(self) -> None:
        if not _MODEL_PRESENT:
            pytest.skip("MobileNetV3 ONNX model not found")
        rc, stdout, stderr = _run_runner(
            "--model", str(_ONNX_MODEL),
            "--device", "fm://python",
            "--full-graph",
        )
        assert rc == 0, (
            f"full-graph failed: rc={rc}\nstdout={stdout}\nstderr={stderr}"
        )
        assert "Full graph PASS" in stdout

    def test_full_graph_evidence_written(self) -> None:
        import tempfile
        if not _MODEL_PRESENT:
            pytest.skip("MobileNetV3 ONNX model not found")
        fd, ev_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            rc, stdout, stderr = _run_runner(
                "--model", str(_ONNX_MODEL),
                "--device", "fm://python",
                "--full-graph",
                "--evidence", ev_path,
            )
            assert rc == 0
            evidence = json.loads(Path(ev_path).read_text())
            assert evidence["full_graph_passed"] is True, f"evidence={evidence}"
        finally:
            Path(ev_path).unlink(missing_ok=True)

    def test_full_graph_fence_completed(self) -> None:
        if not _MODEL_PRESENT:
            pytest.skip("MobileNetV3 ONNX model not found")
        rc, stdout, stderr = _run_runner(
            "--model", str(_ONNX_MODEL),
            "--device", "fm://python",
            "--full-graph",
        )
        assert rc == 0
        assert "COMPLETED" in stdout, f"Missing COMPLETED:\n{stdout}"


# ── Standalone blob builders ────────────────────────────────────────────────


def _build_cv_blob_mmul() -> bytes:
    import gen.npu_abi as _abi
    from software.compiler.command_ir import CommandBlob, LowerStatus
    from software.compiler.command_ir_types import (
        CAD_CAP_MXU, CAD_CAP_SFU, CAD_CAP_VECTOR,
    )
    M, K, N = 4, 8, 4
    caps = CAD_CAP_MXU | CAD_CAP_SFU | CAD_CAP_VECTOR
    blob = CommandBlob(caps=caps)
    blob.declare_buffer(M * K, 64, host_addr=_abi.Addr.DRAM)
    blob.declare_buffer(max((K * N) // 2, 1), 64, host_addr=_abi.Addr.DRAM)
    blob.declare_buffer(M * N * 4, 64, host_addr=_abi.Addr.DRAM)
    blob.add_mmul(1, 2, 3, 0, M, K, N)
    blob.add_barrier()
    assert blob.lower() == LowerStatus.OK
    return blob.encode()


def _build_cv_blob_unsupported_sfu() -> bytes:
    import gen.npu_abi as _abi
    from software.compiler.command_ir import CommandBlob, LowerStatus
    from software.compiler.command_ir_types import CAD_CAP_SFU
    elements = 16
    caps = CAD_CAP_SFU
    blob = CommandBlob(caps=caps)
    blob.declare_buffer(elements * 2, 64, host_addr=_abi.Addr.DRAM)
    blob.declare_buffer(elements * 2, 64, host_addr=_abi.Addr.DRAM)
    blob.add_sfu(1, 1, 2, elements)
    blob.add_barrier()
    assert blob.lower() == LowerStatus.OK
    return blob.encode()


def _build_cv_blob_multi_op() -> bytes:
    import gen.npu_abi as _abi
    from software.compiler.command_ir import CommandBlob, LowerStatus
    from software.compiler.command_ir_types import (
        CAD_CAP_MXU, CAD_CAP_SFU, CAD_CAP_VECTOR,
    )
    M, K, N = 4, 8, 4
    elements = M * N
    caps = CAD_CAP_MXU | CAD_CAP_SFU | CAD_CAP_VECTOR
    blob = CommandBlob(caps=caps)
    blob.declare_buffer(M * K, 64, host_addr=_abi.Addr.DRAM)
    blob.declare_buffer(max((K * N) // 2, 1), 64, host_addr=_abi.Addr.DRAM)
    blob.declare_buffer(elements * 4, 64, host_addr=_abi.Addr.DRAM)
    blob.add_mmul(1, 2, 3, 0, M, K, N)
    blob.declare_buffer(elements * 2, 64, host_addr=_abi.Addr.DRAM)
    blob.add_sfu(3, 3, 4, elements)
    blob.declare_buffer(elements * 4, 64, host_addr=_abi.Addr.DRAM)
    blob.add_vector(0, 3, 4, 5, elements)
    blob.add_barrier()
    assert blob.lower() == LowerStatus.OK
    return blob.encode()
