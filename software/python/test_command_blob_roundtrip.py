"""Round-trip and manifest comparison tests for the command IR blob."""

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SOFTWARE_DIR = REPO_ROOT / "software"
sys.path.insert(0, str(SOFTWARE_DIR))
sys.path.insert(0, str(REPO_ROOT / "gen"))

from compiler import command_ir  # noqa: E402


@pytest.fixture
def simple_blob():
    blob = command_ir.CommandBlob(caps=command_ir.CAD_CAP_MXU | command_ir.CAD_CAP_SFU)
    in_buf = blob.declare_buffer(2560, 64, 0x80000000)
    w_buf = blob.declare_buffer(12800, 64, 0x80010000)
    out_buf = blob.declare_buffer(10240, 64, 0x80020000)
    blob.add_mmul(in_buf, w_buf, out_buf, 0, 1, 2560, 2560)
    return blob


def test_python_encode_c_decode(simple_blob):
    simple_blob.lower()
    data = simple_blob.encode()
    decoded = command_ir.CommandBlob.decode(data)
    assert decoded.version_major == command_ir.CAD_BLOB_MAJOR
    assert decoded.version_minor == command_ir.CAD_BLOB_MINOR
    assert len(decoded.commands) == 1
    assert decoded.commands[0].opcode == command_ir.CAD_OP_MMUL


def test_c_blob_decode_in_python():
    """A blob produced by the C encoder must decode in Python identically."""
    build_dir = REPO_ROOT / "build" / "software"
    lib_path = build_dir / "libcaduceus_command_ir.so"
    if not lib_path.exists():
        pytest.skip(f"shared compiler library not built: {lib_path}")

    import ctypes

    lib = ctypes.CDLL(str(lib_path))
    lib.cad_command_blob_create.argtypes = [ctypes.c_uint32]
    lib.cad_command_blob_create.restype = ctypes.c_void_p
    lib.cad_command_blob_destroy.argtypes = [ctypes.c_void_p]
    lib.cad_buffer_declare.argtypes = [
        ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_uint64
    ]
    lib.cad_buffer_declare.restype = ctypes.c_uint32
    lib.cad_op_mmul.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)
    ]
    lib.cad_op_mmul.restype = ctypes.c_int
    lib.cad_command_blob_lower.argtypes = [ctypes.c_void_p]
    lib.cad_command_blob_lower.restype = ctypes.c_int
    lib.cad_command_blob_encode.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t)
    ]
    lib.cad_command_blob_encode.restype = ctypes.c_int
    lib.cad_command_blob_encoded_free.argtypes = [ctypes.c_void_p]

    blob = lib.cad_command_blob_create(command_ir.CAD_CAP_MXU)
    in_buf = lib.cad_buffer_declare(blob, 2560, 64, 0x80000000)
    w_buf = lib.cad_buffer_declare(blob, 12800, 64, 0x80010000)
    out_buf = lib.cad_buffer_declare(blob, 10240, 64, 0x80020000)
    assert lib.cad_op_mmul(blob, in_buf, w_buf, out_buf, 0, 1, 2560, 2560, 0, None) == 0
    assert lib.cad_command_blob_lower(blob) == 0

    ptr = ctypes.c_void_p()
    size = ctypes.c_size_t()
    assert lib.cad_command_blob_encode(blob, ctypes.byref(ptr), ctypes.byref(size)) == 0
    data = ctypes.string_at(ptr, size.value)
    lib.cad_command_blob_encoded_free(ptr)
    lib.cad_command_blob_destroy(blob)

    decoded = command_ir.CommandBlob.decode(data)
    assert decoded.num_commands() == 1
    cmd = decoded.commands[0]
    assert cmd.opcode == command_ir.CAD_OP_MMUL
    assert cmd.mmul == (1, 2560, 2560)


def test_qwen_manifest_semantic_match():
    manifest_path = REPO_ROOT / "rtl" / "test_vectors" / "qwen_blk0" / "blk0_manifest.json"
    if not manifest_path.exists():
        pytest.skip("Qwen blk.0 manifest not found")

    manifest = command_ir.load_manifest(manifest_path)
    ops = manifest["ops"]

    expected_sequence = [
        "RMSNORM", "MMUL", "MMUL", "MMUL", "ROPE", "MMUL",
        "SOFTMAX", "MMUL", "MMUL", "VRESID", "RMSNORM", "MMUL",
        "MMUL", "SILU", "VMUL", "MMUL", "VRESID",
    ]
    assert [op["opcode"] for op in ops] == expected_sequence

    mmul_ops = [op for op in ops if op["opcode"] == "MMUL"]
    for op in mmul_ops:
        dims = op["dimensions"]
        M, K, N = dims["M"], dims["K"], dims["N"]
        num_k = (K + command_ir.TILE_H - 1) // command_ir.TILE_H
        num_n = (N + command_ir.TILE_W - 1) // command_ir.TILE_W
        expected_tiles = num_k * num_n
        assert op["tiles"] == expected_tiles, f"{op['name']} tile mismatch"
