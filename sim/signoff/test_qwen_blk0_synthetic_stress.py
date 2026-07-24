"""Preflight assertions for synthetic Qwen blk.0 test-vector assets.

Validates manifest integrity, SHA-256 match for all 46 hex files, synthetic
dimensions (2560/9728, NOT canonical 2048/11008), and non-overlapping DRAM
window placement under FuncModel(dram_mb=256).

This is the synthetic half of Wave 1 T0B. The real-GGUF half is in
test_qwen25_3b_real_blk0.py.
"""

from __future__ import annotations

import json

from sim.qwen_blk0_synthetic_vectors import (
    get_dram_windows,
    assert_non_overlapping_windows,
    load_manifest,
    verify_manifest_integrity,
    PUBLIC_NUM_OPS,
    PUBLIC_NUM_FILES,
    PUBLIC_DIMS,
)
from sim.func_model import FuncModel

CASE_ID = "task-0b-qwen3b-synthetic-and-real-preflight"


def _emit_metric(capsys, key: str, value) -> None:
    """Emit a SIGNOFF_METRIC line. The leading newline ensures the line
    starts at column 0 even when interleaved with pytest's progress dots."""
    line = json.dumps({"case": CASE_ID, "key": key, "value": value})
    with capsys.disabled():
        print(f"\nSIGNOFF_METRIC {line}")


def test_qwen_blk0_synthetic_assets_preflight(capsys) -> None:
    """Verify synthetic manifest integrity, SHA-256, dims, and DRAM layout."""
    manifest = load_manifest()
    assert manifest is not None

    ops = manifest.get("ops", [])
    assert len(ops) == PUBLIC_NUM_OPS

    files = manifest.get("files", {})
    assert len(files) == PUBLIC_NUM_FILES

    ok, errors = verify_manifest_integrity(manifest)
    assert ok, f"SHA-256 integrity failed: {errors}"

    dims = manifest.get("dimensions", {})
    assert dims.get("hidden") == PUBLIC_DIMS["hidden"]
    assert dims.get("intermediate") == PUBLIC_DIMS["intermediate"]

    model = FuncModel(dram_mb=256)
    dram_size = len(model.dram)
    assert dram_size == 256 * 1024 * 1024

    windows = get_dram_windows()
    assert len(windows) == PUBLIC_NUM_OPS
    assert_non_overlapping_windows(windows)
    for _op_idx, offset, size in windows:
        assert offset + size <= dram_size
