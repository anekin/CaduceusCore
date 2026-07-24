#!/usr/bin/env python3
"""
Qwen blk.0 synthetic test-vector manifest loader and tile-layout helpers.

Provides:
  - load_manifest() → dict
  - verify_manifest_integrity(manifest, vectors_dir) → (bool, [errors])
  - Non-overlapping DRAM window layout for FuncModel(dram_mb=256)

The synthetic blk.0 manifest uses Qwen 3B dimensions (hidden=2560, intermediate=9728)
which differ from the canonical Qwen2.5-3B dimensions (2048/11008). This is by design:
the synthetic vectors are self-consistent with the manifest and serve as a preflight
for the Func Model pipeline before real GGUF weights are introduced in T4C1-T4C4.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sim.func_model import FuncModel

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "rtl" / "test_vectors" / "qwen_blk0" / "blk0_manifest.json"
VECTORS_DIR = REPO_ROOT / "rtl" / "test_vectors" / "qwen_blk0"

# ---------------------------------------------------------------------------
# Manifest constants (pinned for this task's assertions)
# ---------------------------------------------------------------------------
_MANIFEST_NUM_OPS = 17
_MANIFEST_NUM_FILES = 46
_MANIFEST_DIMS = {"hidden": 2560, "intermediate": 9728}

PUBLIC_NUM_OPS = _MANIFEST_NUM_OPS
PUBLIC_NUM_FILES = _MANIFEST_NUM_FILES
PUBLIC_DIMS = _MANIFEST_DIMS

# ---------------------------------------------------------------------------
# DRAM window layout for FuncModel(dram_mb=256)
#
# Each of the 17 ops gets a 1 MB non-overlapping window in the upper half of
# DRAM. Within each window:
#   offset + 0x00000: input data
#   offset + 0x40000: weight data (MMUL ops only)
#   offset + 0x80000: output data
#   offset + 0xC0000: scale data (MMUL ops only)
#
# Windows start at 16 MB to leave the first region for general-purpose use.
# ---------------------------------------------------------------------------
DRAM_WINDOW_BASE = 0x01000000   # 16 MB offset from DRAM base
DRAM_WINDOW_SIZE = 0x00100000   # 1 MB per op


def load_manifest(manifest_path: Path | None = None) -> Dict[str, Any]:
    """Load the blk.0 synthetic manifest.

    Args:
        manifest_path: Path to blk0_manifest.json.
            Defaults to REPO_ROOT/rtl/test_vectors/qwen_blk0/blk0_manifest.json.

    Returns:
        Parsed manifest dict with keys: model, layer, dimensions, ops, files.
    """
    path = manifest_path or MANIFEST_PATH
    with open(path) as f:
        return json.load(f)


def verify_manifest_integrity(
    manifest: Dict[str, Any],
    vectors_dir: Path | None = None,
) -> Tuple[bool, List[str]]:
    """Verify manifest integrity: op count, file count, SHA-256 of every hex file.

    Reads each hex file referenced in manifest["files"], computes its SHA-256,
    and compares against the recorded hash. Returns (all_ok, error_messages).

    Args:
        manifest: Parsed manifest dict from load_manifest().
        vectors_dir: Directory containing hex files. Defaults to VECTORS_DIR.

    Returns:
        (all_ok, error_messages) — True if every check passes.
    """
    errors: List[str] = []
    vdir = vectors_dir or VECTORS_DIR

    # Op count
    ops = manifest.get("ops", [])
    if len(ops) != _MANIFEST_NUM_OPS:
        errors.append(
            f"op count: expected {_MANIFEST_NUM_OPS}, got {len(ops)}"
        )

    # File count
    files = manifest.get("files", {})
    if len(files) != _MANIFEST_NUM_FILES:
        errors.append(
            f"file count: expected {_MANIFEST_NUM_FILES}, got {len(files)}"
        )

    # Dimensions
    dims = manifest.get("dimensions", {})
    for dim_key, expected in _MANIFEST_DIMS.items():
        actual = dims.get(dim_key)
        if actual != expected:
            errors.append(
                f"dimensions.{dim_key}: expected {expected}, got {actual}"
            )

    # SHA-256 of every hex file
    for fname, finfo in files.items():
        fpath = vdir / fname
        if not fpath.is_file():
            errors.append(f"missing file: {fname}")
            continue
        actual_sha = hashlib.sha256(fpath.read_bytes()).hexdigest()
        expected_sha = finfo.get("sha256", "")
        if actual_sha != expected_sha:
            errors.append(
                f"SHA-256 mismatch: {fname} "
                f"(expected={expected_sha[:16]}..., actual={actual_sha[:16]}...)"
            )

    return len(errors) == 0, errors


def compute_dram_window_addr(op_idx: int) -> int:
    """Compute the base DRAM window address for operation *op_idx* (0–16).

    Returns an offset relative to DRAM_BASE (0x8000_0000), suitable as an
    index into FuncModel.dram[].
    """
    return DRAM_WINDOW_BASE + op_idx * DRAM_WINDOW_SIZE


def get_dram_windows() -> List[Tuple[int, int, int]]:
    """Return non-overlapping DRAM window definitions for all 17 ops.

    Returns:
        List of (op_idx, dram_offset, size_bytes) sorted by op_idx.
        Each window is 1 MB; windows are non-overlapping.
    """
    windows: List[Tuple[int, int, int]] = []
    for i in range(_MANIFEST_NUM_OPS):
        offset = compute_dram_window_addr(i)
        windows.append((i, offset, DRAM_WINDOW_SIZE))
    return windows


def assert_non_overlapping_windows(
    windows: List[Tuple[int, int, int]],
) -> None:
    """Verify that all DRAM windows are strictly non-overlapping.

    Raises AssertionError if any two windows overlap.
    """
    sorted_wins = sorted(windows, key=lambda w: w[1])
    for i in range(1, len(sorted_wins)):
        _, prev_start, prev_size = sorted_wins[i - 1]
        _, curr_start, _ = sorted_wins[i]
        prev_end = prev_start + prev_size
        assert prev_end <= curr_start, (
            f"Overlapping DRAM windows: "
            f"op {sorted_wins[i - 1][0]} [{prev_start:#x}, {prev_end:#x}) "
            f"and op {sorted_wins[i][0]} [{curr_start:#x}, "
            f"{curr_start + sorted_wins[i][2]:#x})"
        )
