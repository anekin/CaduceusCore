"""Tests for arc_model and validate_quant."""
import sys
import pytest
from arc_model import ArcModel


# ── QKV Dimension Bug (B1) ─────────────────────────────────────────────

HEAD_DIM = 128  # Qwen2.5 series head dimension


def test_qkv_dimension_1_5b():
    """1.5B: hidden=1536, num_heads=12 → qkv=1536 (coincidentally correct)."""
    model = ArcModel()
    spec = model.MODELS["qwen2.5-1.5b"]
    num_heads = 12
    expected_qkv = num_heads * HEAD_DIM  # 1536
    actual_qkv = spec[0]
    assert actual_qkv == expected_qkv, (
        f"qkv={actual_qkv} != expected={expected_qkv} "
        f"(num_heads={num_heads} * head_dim={HEAD_DIM})"
    )


def test_qkv_dimension_3b():
    """3B: hidden=2560, num_heads=32 → qkv should be 4096, code gives 2560. RED."""
    model = ArcModel()
    spec = model.MODELS["qwen2.5-3b"]
    num_heads = 32
    expected_qkv = num_heads * HEAD_DIM  # 4096
    actual_qkv = spec[0]  # BUG: spec[0] is hidden (2560), not num_heads * head_dim
    assert actual_qkv == expected_qkv, (
        f"QKV dimension mismatch: got {actual_qkv}, expected {expected_qkv} "
        f"(num_heads={num_heads} * head_dim={HEAD_DIM}). "
        f"Bug: code uses spec[0]={actual_qkv} (hidden) instead of num_heads * head_dim."
    )


def test_qkv_dimension_7b():
    """7B: hidden=3584, num_heads=28 → qkv=3584 (coincidentally correct)."""
    model = ArcModel()
    spec = model.MODELS["qwen2.5-7b"]
    num_heads = 28
    expected_qkv = num_heads * HEAD_DIM  # 3584
    actual_qkv = spec[0]
    assert actual_qkv == expected_qkv, (
        f"qkv={actual_qkv} != expected={expected_qkv} "
        f"(num_heads={num_heads} * head_dim={HEAD_DIM})"
    )


# ── Legacy tests ───────────────────────────────────────────────────────

def test_validate_quant_path_flexible():
    """RED: validate_quant.py should use cross-platform path resolution, not a
    hardcoded macOS path like /Users/zheng/npu/sim.  Importing the module
    triggers sys.path.insert(0, ...) with that literal path — this test asserts
    it is NOT there, which will fail as long as the hardcoded path remains."""
    import validate_quant  # noqa: F401  — triggers sys.path.insert(0, "/Users/zheng/npu/sim")

    # If validate_quant.py used proper __file__-relative resolution, the hardcoded
    # macOS path would not appear in sys.path.  This assertion will fail (RED)
    # until the bug is fixed.
    assert not any("/Users/zheng" in p for p in sys.path), (
        "validate_quant.py hardcodes '/Users/zheng/npu/sim' in sys.path.insert "
        "instead of using cross-platform resolution (e.g. Path(__file__).parent)"
    )
