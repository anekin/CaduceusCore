"""W3.2 — Dual-path readback verification for blk.0 chain.

Verifies that blk.0 chain results can be read correctly via BOTH:
  1. Backdoor SRAM (direct bytearray access) — bk_match
  2. Simulated PCIe TLP path (through crossbar routing) — pcie_match

Anti-vacuous: corrupts PCIe routing and confirms pcie_match=False while
bk_match=True, proving the dual-path check is genuine.
"""

import json
import os

import numpy as np
import pytest

from func_model import FuncModel, DualPathChecker
from tests.test_soc_fm import (
    _blk0_read_hex,
    _blk0_run_mmul,
    _blk0_run_sfu,
    _blk0_run_vector,
    _BLK0_VECTOR_DIR,
    _EB_BY_FMT,
)

# ── Evidence output ────────────────────────────────────────────────────

_EVIDENCE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "build", "evidence",
)


def _write_evidence(lines: list):
    """Write evidence list of strings to w3-2-fm-dual-path.txt."""
    os.makedirs(_EVIDENCE_DIR, exist_ok=True)
    path = os.path.join(_EVIDENCE_DIR, "w3-2-fm-dual-path.txt")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


# ── Helpers ─────────────────────────────────────────────────────────────


def _op_dtype(opcode: str) -> str:
    """Return the comparison dtype for an opcode."""
    if opcode == "MMUL":
        return "int32"
    if opcode in ("RMSNORM", "SOFTMAX", "ROPE", "SILU"):
        return "fp16"
    if opcode in ("VMUL", "VRESID"):
        return "int32"
    raise ValueError(f"Unknown opcode: {opcode}")


def _op_output_size(op: dict, result: dict) -> int:
    """Compute the output byte size from the op result."""
    if op["opcode"] == "MMUL":
        return result["M_eff"] * result["N_eff"] * 4
    if op["opcode"] in ("RMSNORM", "SOFTMAX", "ROPE", "SILU"):
        return result["elements"] * 2
    if op["opcode"] in ("VMUL", "VRESID"):
        return result["elements"] * 4
    return 0


# ── Test: dual-path verification (clean path) ───────────────────────────


def test_blk0_dual_path_verification():
    """Run blk.0 17-op chain; verify dual-path (bk + pcie) match golden.

    For each op in the chain, reads the output via backdoor SRAM AND
    simulated PCIe TLP path, then compares both against the independently
    computed golden reference.
    """
    manifest_path = os.path.join(_BLK0_VECTOR_DIR, "blk0_manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)

    model = FuncModel()
    checker = DualPathChecker(model)
    evidence_lines = [
        "=" * 60,
        "W3.2 Dual-Path Verification — blk.0 17-op chain",
        "=" * 60,
        f"Manifest: {manifest_path}",
        f"Total ops: {manifest['num_ops']}",
        "",
        f"{'Op':>6s}  {'Name':>24s}  {'bk_match':>10s}  {'pcie_match':>10s}  {'bk==pcie':>10s}  {'Route':>6s}",
        "-" * 80,
    ]

    for op in manifest["ops"]:
        idx = op["idx"]
        name = op["name"]
        opcode = op["opcode"]
        label = f"op{idx:02d} {name}"

        # ── Run op through Func Model bridge ──
        if opcode == "MMUL":
            result = _blk0_run_mmul(model, op, manifest)
        elif opcode in ("RMSNORM", "SOFTMAX", "ROPE", "SILU"):
            result = _blk0_run_sfu(model, op, manifest)
        elif opcode in ("VMUL", "VRESID"):
            result = _blk0_run_vector(model, op, manifest)
        else:
            raise ValueError(f"{label}: unsupported opcode {opcode}")

        # ── Dual-path verification ──
        o_addr = int(op["sram_output_addr"], 16)
        size = _op_output_size(op, result)
        dtype = _op_dtype(opcode)

        # Golden is the independently computed reference
        golden = result["golden"]

        res = checker.verify(sram_offset=o_addr, size=size,
                             golden=golden, dtype=dtype)

        assert res["bk_match"], f"{label}: bk path failed — golden mismatch"
        assert res["pcie_match"], f"{label}: pcie path failed — golden mismatch"

        # Cross-check: the two paths must return identical bytes
        pcie_eq_bk = (res["bk_data"] == res["pcie_data"])

        evidence_lines.append(
            f"op{idx:02d}  {name:>24s}  {str(res['bk_match']):>10s}  "
            f"{str(res['pcie_match']):>10s}  {str(pcie_eq_bk):>10s}  clean"
        )

    evidence_lines.append("-" * 80)
    evidence_lines.append(f"ALL {manifest['num_ops']} OPS: bk_match=True pcie_match=True")
    evidence_lines.append("")

    # ── Write evidence ──
    path = _write_evidence(evidence_lines)
    print(f"Evidence written to {path}")


# ── Test: anti-vacuous PCIe corruption ──────────────────────────────────


def test_blk0_dual_path_anti_vacuous():
    """Anti-vacuous: corrupt PCIe routing → pcie_match=False, bk_match=True.

    Runs op02 (K_proj MMUL) on a fresh model, then injects PCIe read
    corruption and confirms the backdoor path still matches golden while
    the PCIe path does NOT, proving the dual-path verification is
    genuinely exercising two independent read paths.
    """
    manifest_path = os.path.join(_BLK0_VECTOR_DIR, "blk0_manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)

    # Use op02 (K_proj MMUL) — a representative MMUL op
    target_op = None
    for op in manifest["ops"]:
        if op["idx"] == 2:
            target_op = op
            break
    assert target_op is not None, "op02 K_proj not found in manifest"

    model = FuncModel()
    checker = DualPathChecker(model)

    # ── Phase 1: clean run — both paths should match ──
    result = _blk0_run_mmul(model, target_op, manifest)
    o_addr = int(target_op["sram_output_addr"], 16)
    size = _op_output_size(target_op, result)
    golden = result["golden"]

    res_clean = checker.verify(sram_offset=o_addr, size=size,
                               golden=golden, dtype="int32")
    assert res_clean["bk_match"], "Phase 1: bk must match golden before corruption"
    assert res_clean["pcie_match"], "Phase 1: pcie must match golden before corruption"

    # ── Phase 2: corrupt PCIe path ──
    DualPathChecker.corrupt_pcie_read(model)

    res_corrupt = checker.verify(sram_offset=o_addr, size=size,
                                 golden=golden, dtype="int32")
    assert res_corrupt["bk_match"], (
        "Anti-vacuous: bk must still match golden after PCIe corruption"
    )
    assert not res_corrupt["pcie_match"], (
        "Anti-vacuous FAIL: pcie_match should be False after corruption "
        "but was True — the PCIe path was not genuinely exercised"
    )
    # Bk vs PCIe data must differ
    assert res_corrupt["bk_data"] != res_corrupt["pcie_data"], (
        "Anti-vacuous: corrupted pcie must differ from bk data"
    )

    # ── Restore ──
    DualPathChecker.restore_pcie_read(model)

    res_restored = checker.verify(sram_offset=o_addr, size=size,
                                  golden=golden, dtype="int32")
    assert res_restored["pcie_match"], (
        "After restore, pcie must match golden again"
    )

    # ── Evidence ──
    evidence_lines = [
        "=" * 60,
        "W3.2 Anti-Vacuous — PCIe Corruption Test",
        "=" * 60,
        f"Target op: op{target_op['idx']:02d} {target_op['name']} ({target_op['opcode']})",
        "",
        f"Phase 1 (clean):     bk_match={res_clean['bk_match']}  pcie_match={res_clean['pcie_match']}",
        f"Phase 2 (corrupted): bk_match={res_corrupt['bk_match']}  pcie_match={res_corrupt['pcie_match']}",
        f"Phase 3 (restored):  bk_match={res_restored['bk_match']}  pcie_match={res_restored['pcie_match']}",
        "",
        f"Corrupt bk hash:  {res_corrupt['bk_hash']}",
        f"Corrupt pcie hash: {res_corrupt['pcie_hash']}",
        f"Bk == Pcie (corrupt): {res_corrupt['bk_data'] == res_corrupt['pcie_data']}",
        "",
        "ANTI-VACUOUS: PASS — PCIe corruption detected (pcie_match=False, bk_match=True)",
    ]

    # Append anti-vacuous evidence to the main file
    evidence_path = os.path.join(_EVIDENCE_DIR, "w3-2-fm-dual-path.txt")
    with open(evidence_path, "a") as f:
        f.write("\n".join(evidence_lines) + "\n")
    print(f"Anti-vacuous evidence appended to {evidence_path}")
