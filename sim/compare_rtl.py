#!/usr/bin/env python3
"""
RTL Output Comparator.

Compares RTL simulation output against golden reference.
Supports: INT32 (MXU), float16 (SFU), chained multi-output tests.

Usage:
    python3 compare_rtl.py <test_dir> [result.hex]      # single test
    python3 compare_rtl.py --batch <test_vectors_root>  # batch compare all tests
"""

import sys, os, json, struct, hashlib
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple

import numpy as np


# ══════════════════════════════════════════════════════════════════════
# Hex file readers ($readmemh compatible)
# ══════════════════════════════════════════════════════════════════════

def read_hex_int8(path: Path) -> np.ndarray:
    """Read hex file (2 hex digits per line) → INT8 array."""
    with open(path) as f:
        vals = [int(line.strip(), 16) for line in f if line.strip()]
    # INT8 values: 0x00-0xFF as unsigned, then view as signed
    return np.array(vals, dtype=np.uint8).view(np.int8)

def read_hex_int32(path: Path) -> np.ndarray:
    """Read hex file (8 hex digits per line) → INT32 array."""
    with open(path) as f:
        vals = [int(line.strip(), 16) for line in f if line.strip()]
    # INT32 values: 0x00000000-0xFFFFFFFF as unsigned, then view as signed
    return np.array(vals, dtype=np.uint32).view(np.int32)

def read_hex_float16(path: Path) -> np.ndarray:
    """Read hex file (4 hex digits per line) → float16 array."""
    with open(path) as f:
        vals = [int(line.strip(), 16) for line in f if line.strip()]
    raw = b"".join(struct.pack("<H", v) for v in vals)
    return np.frombuffer(raw, dtype=np.float16).copy()


# ══════════════════════════════════════════════════════════════════════
# Comparator
# ══════════════════════════════════════════════════════════════════════

@dataclass
class CompareResult:
    """Result of comparing one test output."""
    test_name: str
    passed: bool
    golden_shape: Tuple[int, ...]
    result_shape: Tuple[int, ...]
    max_abs_diff: float = 0.0
    max_rel_diff: float = 0.0
    mean_abs_diff: float = 0.0
    num_mismatches: int = 0
    total_elements: int = 0
    first_diff: Optional[Tuple[int, ...]] = None
    details: str = ""


def compare_int32(golden: np.ndarray, result: np.ndarray,
                  abs_tol: int = 0) -> CompareResult:
    """Compare INT32 arrays (bit-exact by default)."""
    if golden.shape != result.shape:
        return CompareResult(
            test_name="",
            passed=False,
            golden_shape=golden.shape,
            result_shape=result.shape,
            details=f"Shape mismatch: golden {golden.shape} vs result {result.shape}"
        )

    diff = np.abs(golden.astype(np.int64) - result.astype(np.int64))
    mismatches = np.where(diff > abs_tol)
    n_mismatch = len(mismatches[0])

    if n_mismatch == 0:
        return CompareResult(
            test_name="",
            passed=True,
            golden_shape=golden.shape,
            result_shape=result.shape,
            total_elements=golden.size,
        )

    max_abs = int(np.max(diff))
    mean_abs = float(np.mean(diff))
    rel = diff.astype(np.float64) / (np.abs(golden.astype(np.float64)) + 1e-12)
    max_rel = float(np.max(rel))

    # Collect first 5 mismatches
    first = None
    detail_lines = []
    for idx in range(min(5, n_mismatch)):
        pos = tuple(mismatches[i][idx] for i in range(len(mismatches)))
        if first is None:
            first = pos
        gv = golden[pos]
        rv = result[pos]
        detail_lines.append(f"    [{pos}]: golden={gv}, result={rv}, diff={abs(int(gv)-int(rv))}")

    details = f"{n_mismatch} mismatches (first 5 shown):\n" + "\n".join(detail_lines)

    return CompareResult(
        test_name="",
        passed=False,
        golden_shape=golden.shape,
        result_shape=result.shape,
        max_abs_diff=max_abs,
        max_rel_diff=max_rel,
        mean_abs_diff=mean_abs,
        num_mismatches=n_mismatch,
        total_elements=golden.size,
        first_diff=first,
        details=details,
    )


def compare_float16(golden: np.ndarray, result: np.ndarray,
                    abs_tol: float = 1e-3, rel_tol: float = 1e-2) -> CompareResult:
    """Compare float16 arrays with tolerance."""
    if golden.shape != result.shape:
        return CompareResult(
            test_name="",
            passed=False,
            golden_shape=golden.shape,
            result_shape=result.shape,
            details=f"Shape mismatch: golden {golden.shape} vs result {result.shape}"
        )

    g = golden.astype(np.float64)
    r = result.astype(np.float64)
    abs_diff = np.abs(g - r)
    rel_diff = abs_diff / (np.abs(g) + 1e-12)

    # Check: within absolute OR relative tolerance
    ok = (abs_diff <= abs_tol) | (rel_diff <= rel_tol)
    n_mismatch = int(np.sum(~ok))

    if n_mismatch == 0:
        return CompareResult(
            test_name="",
            passed=True,
            golden_shape=golden.shape,
            result_shape=result.shape,
            max_abs_diff=float(np.max(abs_diff)),
            max_rel_diff=float(np.max(rel_diff)),
            total_elements=golden.size,
        )

    max_abs = float(np.max(abs_diff))
    max_rel = float(np.max(rel_diff))
    mean_abs = float(np.mean(abs_diff))

    mismatches = np.where(~ok)
    first = None
    detail_lines = []
    for idx in range(min(5, n_mismatch)):
        pos = tuple(mismatches[i][idx] for i in range(len(mismatches)))
        if first is None:
            first = pos
        detail_lines.append(
            f"    [{pos}]: golden={g[pos]:.6e}, result={r[pos]:.6e}, "
            f"abs={abs_diff[pos]:.2e}, rel={rel_diff[pos]:.2e}"
        )

    return CompareResult(
        test_name="",
        passed=False,
        golden_shape=golden.shape,
        result_shape=result.shape,
        max_abs_diff=max_abs,
        max_rel_diff=max_rel,
        mean_abs_diff=mean_abs,
        num_mismatches=n_mismatch,
        total_elements=golden.size,
        first_diff=first,
        details=f"{n_mismatch} mismatches (first 5 shown):\n" + "\n".join(detail_lines),
    )


# ══════════════════════════════════════════════════════════════════════
# Single test comparison
# ══════════════════════════════════════════════════════════════════════

def compare_test(test_dir: Path, result_files: Dict[str, Path] = None) -> List[CompareResult]:
    """Compare all results for a test directory.

    Args:
        test_dir: Path to test directory with manifest.json and golden.hex
        result_files: Dict mapping output name → result hex file path.
                     If None, uses default 'result.hex' for single-output tests.
    """
    test_dir = Path(test_dir)
    manifest_path = test_dir / "manifest.json"

    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest.json in {test_dir}")

    manifest = json.loads(manifest_path.read_text())
    test_name = manifest.get("name", test_dir.name)
    results = []

    # Determine what kind of test this is
    if manifest.get("chain"):
        # Chained test: compare each intermediate output
        r = _compare_chain(test_dir, manifest, test_name, result_files)
        results.extend(r)
    elif manifest.get("sfu_op"):
        # SFU test: float16 comparison
        r = _compare_sfu(test_dir, manifest, test_name, result_files)
        results.append(r)
    else:
        # MMUL test: INT32 comparison
        r = _compare_mmul(test_dir, manifest, test_name, result_files)
        results.append(r)

    return results


def _compare_mmul(test_dir, manifest, test_name, result_files) -> CompareResult:
    """Compare MMUL (INT32) output."""
    golden_path = test_dir / manifest["files"]["golden"]
    golden = read_hex_int32(golden_path)

    expected_shape = tuple(manifest["results"]["golden_shape"])
    golden = golden.reshape(expected_shape)

    # Find result file
    if result_files and "mmul" in result_files:
        result_path = result_files["mmul"]
    elif result_files and len(result_files) == 1:
        result_path = list(result_files.values())[0]
    else:
        result_path = test_dir / "result.hex"

    if not result_path.exists():
        return CompareResult(
            test_name=test_name,
            passed=False,
            golden_shape=golden.shape,
            result_shape=(0,),
            details=f"Result file not found: {result_path}",
        )

    result = read_hex_int32(result_path)
    try:
        result = result.reshape(expected_shape)
    except ValueError:
        pass  # Will fail in compare_int32

    r = compare_int32(golden, result)
    r.test_name = test_name
    return r


def _compare_sfu(test_dir, manifest, test_name, result_files) -> CompareResult:
    """Compare SFU (float16) output."""
    golden_path = test_dir / manifest["files"]["golden"]
    golden = read_hex_float16(golden_path)

    if result_files and "sfu" in result_files:
        result_path = result_files["sfu"]
    elif result_files and len(result_files) == 1:
        result_path = list(result_files.values())[0]
    else:
        result_path = test_dir / "result.hex"

    if not result_path.exists():
        return CompareResult(
            test_name=test_name,
            passed=False,
            golden_shape=golden.shape,
            result_shape=(0,),
            details=f"Result file not found: {result_path}",
        )

    result = read_hex_float16(result_path)

    r = compare_float16(golden, result)
    r.test_name = test_name
    return r


def _compare_chain(test_dir, manifest, test_name, result_files) -> List[CompareResult]:
    """Compare chained test outputs (one per instruction output)."""
    results = []

    # Q output (INT32)
    if "golden_q" in manifest["files"]:
        golden_q = read_hex_int32(test_dir / manifest["files"]["golden_q"])
        # RTL should produce result_q.hex
        result_q_path = test_dir / "result_q.hex"
        if result_q_path.exists():
            result_q = read_hex_int32(result_q_path)
            r = compare_int32(golden_q, result_q)
            r.test_name = f"{test_name}/Q"
            results.append(r)

    # Softmax output (float16)
    if "golden_softmax" in manifest["files"]:
        golden_sm = read_hex_float16(test_dir / manifest["files"]["golden_softmax"])
        result_sm_path = test_dir / "result_softmax.hex"
        if result_sm_path.exists():
            result_sm = read_hex_float16(result_sm_path)
            r = compare_float16(golden_sm, result_sm)
            r.test_name = f"{test_name}/Softmax"
            results.append(r)

    return results


# ══════════════════════════════════════════════════════════════════════
# Batch compare (all tests in a root directory)
# ══════════════════════════════════════════════════════════════════════

def batch_compare(root: Path) -> Dict[str, List[CompareResult]]:
    """Compare all test directories under root."""
    all_results = {}
    for test_dir in sorted(root.iterdir()):
        if not test_dir.is_dir():
            continue
        manifest = test_dir / "manifest.json"
        if not manifest.exists():
            continue
        try:
            results = compare_test(test_dir)
            all_results[test_dir.name] = results
        except Exception as e:
            all_results[test_dir.name] = [
                CompareResult(
                    test_name=test_dir.name,
                    passed=False,
                    golden_shape=(0,),
                    result_shape=(0,),
                    details=str(e),
                )
            ]
    return all_results


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="RTL Output Comparator — compare RTL results vs golden reference"
    )
    parser.add_argument("test_dir", nargs="?",
                        help="Test directory path (with manifest.json)")
    parser.add_argument("result_file", nargs="?",
                        help="RTL result hex file (default: test_dir/result.hex)")
    parser.add_argument("--batch", action="store_true",
                        help="Batch mode: compare all tests in test_dir")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    args = parser.parse_args()

    if args.batch:
        if not args.test_dir:
            print("ERROR: --batch requires a test_vectors root directory")
            sys.exit(1)

        root = Path(args.test_dir)
        all_results = batch_compare(root)

        total = 0
        passed = 0
        for test_name, results in all_results.items():
            for r in results:
                total += 1
                if r.passed:
                    passed += 1
                status = "PASS" if r.passed else "FAIL"
                print(f"  [{status}] {r.test_name:30s}  "
                      f"shape={r.golden_shape!s:20s}  "
                      f"mismatches={r.num_mismatches}/{r.total_elements}")

        print(f"\n{'='*60}")
        print(f"Summary: {passed}/{total} passed")
        print(f"{'ALL TESTS PASSED' if passed == total else f'{total - passed} TESTS FAILED'}")

        if args.json:
            output = {
                "total": total,
                "passed": passed,
                "failed": total - passed,
                "results": {
                    name: [
                        {
                            "passed": r.passed,
                            "shape": list(r.golden_shape),
                            "mismatches": r.num_mismatches,
                            "max_abs_diff": r.max_abs_diff,
                            "max_rel_diff": r.max_rel_diff,
                            "details": r.details,
                        }
                        for r in results
                    ]
                    for name, results in all_results.items()
                }
            }
            print(json.dumps(output, indent=2))

    else:
        if not args.test_dir:
            parser.print_help()
            sys.exit(1)

        test_dir = Path(args.test_dir)

        result_files = {}
        if args.result_file:
            result_files["default"] = Path(args.result_file)

        results = compare_test(test_dir, result_files)

        for r in results:
            status = "PASS" if r.passed else "FAIL"
            print(f"  [{status}] {r.test_name}")
            print(f"         Shape: golden={r.golden_shape}, result={r.result_shape}")
            if not r.passed:
                print(f"         Mismatches: {r.num_mismatches}/{r.total_elements}")
                print(f"         Max abs diff: {r.max_abs_diff:.6e}")
                print(f"         Max rel diff: {r.max_rel_diff:.6e}")
                if r.details:
                    print(r.details)

        if args.json:
            output = [
                {
                    "test": r.test_name,
                    "passed": r.passed,
                    "shape": list(r.golden_shape),
                    "mismatches": r.num_mismatches,
                    "max_abs_diff": r.max_abs_diff,
                    "max_rel_diff": r.max_rel_diff,
                    "details": r.details,
                }
                for r in results
            ]
            print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
