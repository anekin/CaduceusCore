#!/usr/bin/env python3
"""
Authoritative Func Model signoff evidence runner.

Usage:
    python3 scripts/run_func_model_signoff.py run --case <id>
    python3 scripts/run_func_model_signoff.py validate --case <id>
    python3 scripts/run_func_model_signoff.py validate --all-functional
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / ".omo" / "evidence"
SIM_DIR = REPO_ROOT / "sim"
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Ensure sim/ is on sys.path for module-imports inside spawned subprocesses
if str(SIM_DIR) not in sys.path:
    sys.path.insert(0, str(SIM_DIR))


# ---------------------------------------------------------------------------
# Exclusions for source_fingerprint
# ---------------------------------------------------------------------------
FINGERPRINT_EXCLUDE_GLOBS = [
    ".omo/evidence/**",
    "build/evidence/**",
    "**/.pytest_cache/**",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/results.xml",
    "**/junit*.xml",
    "**/.venv*/**",
    "**/node_modules/**",
    "**/.git/**",
]


def _git_root() -> Path:
    """Return the git repository root (may differ from REPO_ROOT for submodules)."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=10,
    )
    if result.returncode == 0:
        return Path(result.stdout.strip())
    return REPO_ROOT


def path_matches_globs(filepath: Path, globs: List[str], root: Path) -> bool:
    """Check whether *filepath* relative to *root* matches any glob in *globs*.

    Supports `**` glob patterns via fnmatch-style matching.
    """
    try:
        rel = filepath.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return False
    for g in globs:
        # Convert glob to regex-like pattern: ** matches anything
        regex = re.escape(g)
        regex = regex.replace(r"\*\*", "___DOUBLESTAR___")
        regex = regex.replace(r"\*", r"[^/]*")
        regex = regex.replace("___DOUBLESTAR___", r".*")
        if re.fullmatch(regex, rel):
            return True
    return False


def compute_source_fingerprint(case_globs: List[str]) -> Tuple[str, List[str]]:
    """Compute SHA-256 fingerprint over all in-scope source files.

    Returns (fingerprint_hex, sorted_file_list).
    """
    git_root = _git_root()
    # Collect all tracked files
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        capture_output=True, text=True, cwd=str(git_root), timeout=30,
    )
    all_files: List[Path] = []
    if tracked.returncode == 0:
        for f in tracked.stdout.split("\0"):
            f = f.strip()
            if f:
                all_files.append(git_root / f)

    # Collect untracked files
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True, text=True, cwd=str(git_root), timeout=30,
    )
    if untracked.returncode == 0:
        for f in untracked.stdout.split("\0"):
            f = f.strip()
            if f:
                all_files.append(git_root / f)

    all_files = sorted(set(all_files))

    # Filter: must match case_globs AND must not match exclusion globs
    in_scope: List[Path] = []
    for fp in all_files:
        if not fp.is_file():
            continue
        if path_matches_globs(fp, FINGERPRINT_EXCLUDE_GLOBS, git_root):
            continue
        if case_globs:
            if path_matches_globs(fp, case_globs, git_root):
                in_scope.append(fp)
        else:
            in_scope.append(fp)

    in_scope.sort()
    hasher = hashlib.sha256()
    for fp in in_scope:
        rel = fp.resolve().relative_to(git_root.resolve()).as_posix()
        hasher.update(rel.encode())
        try:
            content = fp.read_bytes()
        except OSError:
            content = b"<unreadable>"
        file_hash = hashlib.sha256(content).hexdigest()
        hasher.update(file_hash.encode())

    return hasher.hexdigest(), [str(p) for p in in_scope]


# ---------------------------------------------------------------------------
# Metric parsing
# ---------------------------------------------------------------------------
METRIC_LINE_RE = re.compile(r'^SIGNOFF_METRIC\s+(.+)$')


def parse_metrics_from_stdout(stdout: str) -> List[Dict[str, Any]]:
    """Extract SIGNOFF_METRIC lines from process stdout."""
    metrics: List[Dict[str, Any]] = []
    for line in stdout.splitlines():
        m = METRIC_LINE_RE.match(line.strip())
        if m:
            try:
                metric = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            if isinstance(metric, dict):
                metrics.append(metric)
    return metrics


# ---------------------------------------------------------------------------
# JUnit XML parsing
# ---------------------------------------------------------------------------
@dataclass
class PytestResult:
    collected: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    xfailed: int = 0
    deselected: int = 0
    # Raw JUnit attributes (used during parsing)
    tests: int = 0
    errors: int = 0
    failures: int = 0

    @property
    def any_skip(self) -> bool:
        return self.skipped > 0

    @property
    def any_xfail(self) -> bool:
        return self.xfailed > 0

    @property
    def zero_tests(self) -> bool:
        return self.collected == 0


def parse_junit_xml(xml_path: Path) -> Optional[PytestResult]:
    """Parse a pytest-generated JUnit XML report.

    Returns None if the file does not exist or is unreadable.
    """
    if not xml_path.is_file():
        return None
    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except ET.ParseError:
        return None

    result = PytestResult()

    for ts in root.iter("testsuite"):
        for attr_name, conv_fn in [("tests", int), ("errors", int),
                                     ("failures", int), ("skipped", int)]:
            val = ts.attrib.get(attr_name)
            if val is not None:
                try:
                    setattr(result, attr_name, conv_fn(val))
                except (ValueError, TypeError):
                    pass
        # xfail may be in attributes or properties
        xf = ts.attrib.get("xfail")
        if xf is not None:
            try:
                result.xfailed = int(xf)
            except (ValueError, TypeError):
                pass

    # Read properties for passed and xfail counts
    for ts in root.iter("testsuite"):
        for prop in ts.findall("properties/property"):
            name = prop.attrib.get("name", "")
            value = prop.attrib.get("value", "0")
            try:
                if name == "passed":
                    result.passed = int(value)
                elif name == "xfailed":
                    result.xfailed = int(value)
                elif name == "deselected":
                    result.deselected = int(value)
            except (ValueError, TypeError):
                pass

    # Compute synthetic counts
    result.collected = result.tests
    result.failed = result.errors + result.failures

    # If passed not set via properties, derive it
    if result.passed == 0 and result.collected > 0:
        derived = result.collected - result.failed - result.skipped - result.xfailed
        result.passed = max(derived, 0)

    return result


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------
def git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=10,
    )
    return result.stdout.strip() if result.returncode == 0 else "<unknown>"


def git_dirty_summary() -> str:
    """Return a short summary of dirty worktree state."""
    result = subprocess.run(
        ["git", "diff", "--stat"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=30,
    )
    dirty = ""
    if result.stdout.strip():
        dirty = "dirty:" + result.stdout.strip()[:200]
    unstaged = subprocess.run(
        ["git", "diff", "--stat", "--cached"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=30,
    )
    if unstaged.stdout.strip():
        dirty += " | staged:" + unstaged.stdout.strip()[:200]
    return dirty if dirty else "clean"


def git_is_ancestor(ancestor: str, descendant: str) -> bool:
    """Return True if *ancestor* is an ancestor commit of *descendant*."""
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True, cwd=str(REPO_ROOT), timeout=10,
    )
    return result.returncode == 0


def git_branch() -> str:
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=10,
    )
    return result.stdout.strip() if result.returncode == 0 else "<unknown>"


# ---------------------------------------------------------------------------
# Case registry
# ---------------------------------------------------------------------------
@dataclass
class CaseDef:
    case_id: str
    argv: List[str]
    evidence_path: str  # relative to .omo/evidence/
    expected_exit: Optional[int]  # None = any exit code is fine
    min_collected: int = 0
    min_passed: int = 0
    forbid_skip: bool = True
    forbid_xfail: bool = True
    source_fingerprint_globs: List[str] = field(default_factory=list)
    required_metrics: List[str] = field(default_factory=list)
    is_pytest: bool = True  # True = parse JUnit XML; False = raw subprocess only
    expected_failure: bool = False  # True for task-1-comparator-red
    expected_failure_pattern: str = ""  # regex pattern expected in failure output


# Default globs for common test files
_DEFAULT_TEST_GLOBS = [
    "sim/tests/**",
    "sim/**/*.py",
    "scripts/**/*.py",
    "scripts/**/*.sh",
]

CASE_REGISTRY: Dict[str, CaseDef] = {
    # Wave 0
    "task-0a-signoff-runner": CaseDef(
        case_id="task-0a-signoff-runner",
        argv=["python3", "-m", "pytest", "sim/tests/test_func_model_signoff_runner.py", "-q"],
        evidence_path="task-0a-signoff-runner.txt",
        expected_exit=0,
        min_collected=1,
        min_passed=1,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=[
            "scripts/run_func_model_signoff.py",
            "scripts/run_fm_env.sh",
            "sim/tests/test_func_model_signoff_runner.py",
            "sim/tests/conftest.py",
        ],
        required_metrics=["tests.collected", "tests.passed", "tests.failed",
                          "tests.skipped", "tests.xfailed", "evidence.verdict"],
        is_pytest=True,
    ),
    # Wave 0B
    "task-0b-qwen3b-synthetic-and-real-preflight": CaseDef(
        case_id="task-0b-qwen3b-synthetic-and-real-preflight",
        argv=["python3", "-m", "pytest",
              "sim/signoff/test_qwen_blk0_synthetic_stress.py::test_qwen_blk0_synthetic_assets_preflight",
              "sim/signoff/test_qwen25_3b_real_blk0.py::test_qwen25_3b_real_model_provenance_and_shapes",
              "-q"],
        evidence_path="task-0b-qwen3b-synthetic-and-real-preflight.txt",
        expected_exit=0,
        min_collected=2,
        min_passed=2,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=["model.sha256", "model.hidden", "model.intermediate",
                          "model.num_heads", "model.num_kv_heads", "model.head_dim",
                          "tests.collected", "tests.passed", "evidence.verdict"],
        is_pytest=True,
    ),
    # Task 1 - comparator RED (expected failure before Task 2)
    "task-1-comparator-red": CaseDef(
        case_id="task-1-comparator-red",
        argv=["python3", "-m", "pytest",
              "sim/tests/test_golden_sfu_compare.py::test_compare_mixed_abs_rel_pass",
              "-q"],
        evidence_path="task-1-comparator-red.txt",
        expected_exit=1,  # must fail
        min_collected=1,
        min_passed=0,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=[
            "sim/tests/test_golden_sfu_compare.py",
            "sim/golden_executor.py",
            "sim/compare_rtl.py",
        ],
        required_metrics=["tests.collected", "tests.passed", "tests.failed",
                          "evidence.verdict"],
        is_pytest=True,
        expected_failure=True,
        expected_failure_pattern="mixed.*abs.*rel",
    ),
    # Task 2 - comparator GREEN
    "task-2-comparator-green": CaseDef(
        case_id="task-2-comparator-green",
        argv=["python3", "-m", "pytest",
              "sim/tests/test_golden_sfu_compare.py",
              "sim/tests/test_golden_sfu.py",
              "sim/tests/test_golden_sfu_gaps.py",
              "-q"],
        evidence_path="task-2-comparator-green.txt",
        expected_exit=0,
        min_collected=1,
        min_passed=1,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=["tests.collected", "tests.passed", "tests.failed",
                          "tests.skipped", "tests.xfailed", "evidence.verdict"],
        is_pytest=True,
    ),
    # Task 2 - W2.2 golden vectors
    "task-2-w2-2-golden-vectors": CaseDef(
        case_id="task-2-w2-2-golden-vectors",
        argv=["python3", "scripts/verify_w2_2_fm_golden_vectors.py", "--skip-dry-run"],
        evidence_path="task-2-w2-2-golden-vectors.txt",
        expected_exit=0,
        min_collected=0,
        min_passed=0,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=[
            "scripts/verify_w2_2_fm_golden_vectors.py",
            "sim/**/*.py",
        ],
        required_metrics=["tests.collected", "tests.passed", "evidence.verdict"],
        is_pytest=False,
    ),
    # Task 3 - scaled Qwen regressions
    "task-3-scaled-qwen-regressions": CaseDef(
        case_id="task-3-scaled-qwen-regressions",
        argv=["python3", "-m", "pytest",
              "sim/tests/test_soc_fm.py::test_blk0_scaled_single_tile_manifest_replay",
              "sim/tests/test_soc_fm.py::test_28block_scaled_chain",
              "sim/tests/test_soc_fm.py::test_e2e_host_pcie_doorbell_firmware_scaled_blk0",
              "-q"],
        evidence_path="task-3-scaled-qwen-regressions.txt",
        expected_exit=0,
        min_collected=3,
        min_passed=3,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=["tests.collected", "tests.passed", "evidence.verdict"],
        is_pytest=True,
    ),
    # Task 4A - Qwen3B direct MMIO
    "task-4a-qwen3b-direct-mmio": CaseDef(
        case_id="task-4a-qwen3b-direct-mmio",
        argv=["python3", "-m", "pytest",
              "sim/signoff/test_qwen_blk0_synthetic_stress.py::test_qwen_blk0_synthetic_direct_mmio_manifest_ops",
              "-q"],
        evidence_path="task-4a-qwen3b-direct-mmio.txt",
        expected_exit=0,
        min_collected=1,
        min_passed=1,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=["tests.collected", "tests.passed", "evidence.verdict"],
        is_pytest=True,
    ),
    # Task 4B - Qwen3B tiled MMUL
    "task-4b-qwen3b-tiled-mmul": CaseDef(
        case_id="task-4b-qwen3b-tiled-mmul",
        argv=["python3", "-m", "pytest",
              "sim/signoff/test_qwen_blk0_synthetic_stress.py::test_qwen_blk0_synthetic_tiled_mmul_manifest_ops",
              "-q"],
        evidence_path="task-4b-qwen3b-tiled-mmul.txt",
        expected_exit=0,
        min_collected=1,
        min_passed=1,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=["tests.collected", "tests.passed", "evidence.verdict"],
        is_pytest=True,
    ),
    # Task 4C1 - Qwen2.5-3B selective load and reference inputs
    "task-4c1-qwen25-3b-selective-load-and-reference-inputs": CaseDef(
        case_id="task-4c1-qwen25-3b-selective-load-and-reference-inputs",
        argv=["python3", "-m", "pytest",
              "sim/signoff/test_qwen25_3b_real_blk0.py::test_qwen25_3b_selective_loading_and_reference_inputs",
              "-q"],
        evidence_path="task-4c1-qwen25-3b-selective-load-and-reference-inputs.txt",
        expected_exit=0,
        min_collected=1,
        min_passed=1,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=["model.sha256", "tests.collected", "tests.passed",
                          "evidence.verdict"],
        is_pytest=True,
    ),
    # Task 4C2 - Qwen2.5-3B real direct projections
    "task-4c2-qwen25-3b-real-direct-projections": CaseDef(
        case_id="task-4c2-qwen25-3b-real-direct-projections",
        argv=["python3", "-m", "pytest",
              "sim/signoff/test_qwen25_3b_real_blk0.py::test_qwen25_3b_real_direct_projections",
              "-q"],
        evidence_path="task-4c2-qwen25-3b-real-direct-projections.txt",
        expected_exit=0,
        min_collected=1,
        min_passed=1,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=["tests.collected", "tests.passed", "evidence.verdict"],
        is_pytest=True,
    ),
    # Task 4C3 - Qwen2.5-3B real tiled projections
    "task-4c3-qwen25-3b-real-tiled-projections": CaseDef(
        case_id="task-4c3-qwen25-3b-real-tiled-projections",
        argv=["python3", "-m", "pytest",
              "sim/signoff/test_qwen25_3b_real_blk0.py::test_qwen25_3b_real_tiled_projections",
              "-q"],
        evidence_path="task-4c3-qwen25-3b-real-tiled-projections.txt",
        expected_exit=0,
        min_collected=1,
        min_passed=1,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=["tests.collected", "tests.passed", "evidence.verdict"],
        is_pytest=True,
    ),
    # Task 4C4 - Qwen2.5-3B real connected blk0
    "task-4c4-qwen25-3b-real-connected-blk0": CaseDef(
        case_id="task-4c4-qwen25-3b-real-connected-blk0",
        argv=["python3", "-m", "pytest",
              "sim/signoff/test_qwen25_3b_real_blk0.py::test_qwen25_3b_real_connected_blk0",
              "-q"],
        evidence_path="task-4c4-qwen25-3b-real-connected-blk0.txt",
        expected_exit=0,
        min_collected=1,
        min_passed=1,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=["tests.collected", "tests.passed", "evidence.verdict"],
        is_pytest=True,
    ),
    # Task 5 - Qwen3B robustness
    "task-5-qwen3b-robustness": CaseDef(
        case_id="task-5-qwen3b-robustness",
        argv=["python3", "-m", "pytest",
              "sim/signoff/test_qwen_blk0_synthetic_stress.py::test_qwen_blk0_synthetic_validation_rejects_corruption",
              "sim/signoff/test_qwen_blk0_synthetic_stress.py::test_qwen_blk0_synthetic_validation_rejects_invalid_descriptor",
              "sim/signoff/test_qwen_blk0_synthetic_stress.py::test_qwen_blk0_synthetic_tiled_boundary_coverage",
              "sim/signoff/test_qwen25_3b_real_blk0.py::test_qwen25_3b_real_blk0_rejects_corruption_and_shape_substitution",
              "-q"],
        evidence_path="task-5-qwen3b-robustness.txt",
        expected_exit=0,
        min_collected=4,
        min_passed=4,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=[],
        is_pytest=True,
    ),
    # Task 6 - Signoff doc consistency
    "task-6-signoff-doc-consistency": CaseDef(
        case_id="task-6-signoff-doc-consistency",
        argv=["python3", "-m", "pytest", "sim/tests/test_func_model_signoff_docs.py", "-q"],
        evidence_path="task-6-signoff-doc-consistency.txt",
        expected_exit=0,
        min_collected=1,
        min_passed=1,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=[
            "sim/tests/test_func_model_signoff_docs.py",
            "scripts/check_func_model_signoff_docs.py",
            "docs/func_model_architecture.md",
            "docs/func-model-plan.md",
        ],
        required_metrics=["tests.collected", "tests.passed", "evidence.verdict"],
        is_pytest=True,
    ),
    # Task 7 - functional selected regression
    "task-7-functional-selected-regression": CaseDef(
        case_id="task-7-functional-selected-regression",
        argv=["python3", "-m", "pytest",
              "sim/tests/test_golden_smoke.py",
              "sim/tests/test_golden_mxu_quant.py",
              "sim/tests/test_golden_mxu_edges.py",
              "sim/tests/test_golden_sfu.py",
              "sim/tests/test_golden_sfu_gaps.py",
              "sim/tests/test_golden_vector.py",
              "sim/tests/test_golden_dma.py",
              "sim/tests/test_golden_noc.py",
              "sim/tests/test_golden_cross_module.py",
              "sim/tests/test_golden_corruption.py",
              "sim/tests/test_golden_sfu_compare.py",
              "-q"],
        evidence_path="task-7-functional-selected-regression.txt",
        expected_exit=0,
        min_collected=11,
        min_passed=11,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=["tests.collected", "tests.passed", "tests.failed",
                          "tests.skipped", "tests.xfailed", "evidence.verdict"],
        is_pytest=True,
    ),
    # Task 7 - functional full sweep
    "task-7-functional-full-sweep": CaseDef(
        case_id="task-7-functional-full-sweep",
        argv=["python3", "-m", "pytest", "sim/tests/", "-q",
              "--ignore=sim/tests/test_soc_pcie_dma.py",
              "--ignore=sim/tests/test_engines.py",
              "--ignore=sim/tests/test_cv_conv2d_rtl.py",
              "--ignore=sim/tests/wrapper"],
        evidence_path="task-7-functional-full-sweep.txt",
        expected_exit=0,
        min_collected=638,
        min_passed=638,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=["tests.collected", "tests.passed", "tests.failed",
                          "tests.skipped", "tests.xfailed", "evidence.verdict"],
        is_pytest=True,
    ),
    # Task 7 - Qwen3B synthetic stress gates
    "task-7-qwen3b-synthetic-stress-gates": CaseDef(
        case_id="task-7-qwen3b-synthetic-stress-gates",
        argv=["python3", "-m", "pytest",
              "sim/signoff/test_qwen_blk0_synthetic_stress.py",
              "-q"],
        evidence_path="task-7-qwen3b-synthetic-stress-gates.txt",
        expected_exit=0,
        min_collected=1,
        min_passed=1,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=["tests.collected", "tests.passed", "evidence.verdict"],
        is_pytest=True,
    ),
    # Task 7 - Qwen2.5-3B real blk0 hard gate
    "task-7-qwen25-3b-real-blk0-hard-gate": CaseDef(
        case_id="task-7-qwen25-3b-real-blk0-hard-gate",
        argv=["python3", "-m", "pytest",
              "sim/signoff/test_qwen25_3b_real_blk0.py",
              "-q"],
        evidence_path="task-7-qwen25-3b-real-blk0-hard-gate.txt",
        expected_exit=0,
        min_collected=1,
        min_passed=1,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=["model.sha256", "tests.collected", "tests.passed",
                          "evidence.verdict"],
        is_pytest=True,
    ),
    # Task 7 - W2.2 golden vectors (same command as task-2)
    "task-7-w2-2-golden-vectors": CaseDef(
        case_id="task-7-w2-2-golden-vectors",
        argv=["python3", "scripts/verify_w2_2_fm_golden_vectors.py", "--skip-dry-run"],
        evidence_path="task-7-w2-2-golden-vectors.txt",
        expected_exit=0,
        min_collected=0,
        min_passed=0,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=[
            "scripts/verify_w2_2_fm_golden_vectors.py",
            "sim/**/*.py",
        ],
        required_metrics=["tests.collected", "tests.passed", "evidence.verdict"],
        is_pytest=False,
    ),
    # Final gates
    "final-plan-compliance": CaseDef(
        case_id="final-plan-compliance",
        argv=[],  # validate-only, no separate command
        evidence_path="final-plan-compliance.txt",
        expected_exit=0,
        min_collected=0,
        min_passed=0,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=["evidence.verdict"],
        is_pytest=False,
    ),
    "final-code-quality": CaseDef(
        case_id="final-code-quality",
        argv=[],  # compile + test, handled specially
        evidence_path="final-code-quality.txt",
        expected_exit=0,
        min_collected=1,
        min_passed=1,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=["tests.collected", "tests.passed", "evidence.verdict"],
        is_pytest=True,
    ),
    "final-real-qa": CaseDef(
        case_id="final-real-qa",
        argv=[],  # composite payload
        evidence_path="final-real-qa.txt",
        expected_exit=0,
        min_collected=4,
        min_passed=4,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=["tests.collected", "tests.passed", "evidence.verdict"],
        is_pytest=True,
    ),
    "final-scope-fidelity": CaseDef(
        case_id="final-scope-fidelity",
        argv=[],  # scope checker, handled separately
        evidence_path="final-scope-fidelity.txt",
        expected_exit=0,
        min_collected=0,
        min_passed=0,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=_DEFAULT_TEST_GLOBS,
        required_metrics=["evidence.verdict"],
        is_pytest=False,
    ),

    # -----------------------------------------------------------------------
    # V3 SoC Integration Cases (Wave 0 — registry + runner)
    # -----------------------------------------------------------------------

    # T0 — V3 signoff runner self-test
    "task-0-v3-signoff-runner": CaseDef(
        case_id="task-0-v3-signoff-runner",
        argv=["python3", "-m", "pytest", "sim/tests/test_func_model_signoff_v3.py", "-q"],
        evidence_path="task-0-signoff-v3-runner.txt",
        expected_exit=0,
        min_collected=1,
        min_passed=1,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=[
            "scripts/run_func_model_signoff.py",
            "sim/tests/test_func_model_signoff_v3.py",
            "sim/tests/conftest.py",
        ],
        required_metrics=["tests.collected", "tests.passed", "tests.failed",
                          "tests.skipped", "tests.xfailed", "evidence.verdict"],
        is_pytest=True,
    ),

    # T1 — Spike + firmware E2E forward pass (4 modes, split into independent cases
    # to avoid shell=True and to give per-mode pass/fail visibility).
    "task-1a-v3-spike-mmul-smoke": CaseDef(
        case_id="task-1a-v3-spike-mmul-smoke",
        argv=["bash", "scripts/run_fm_env.sh", "--", "python3",
              "sim/spike_host.py", "--mode", "mmul_smoke"],
        evidence_path="task-1a-spike-mmul-smoke.txt",
        expected_exit=0,
        min_collected=0,
        min_passed=0,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=[
            "scripts/run_fm_env.sh",
            "sim/spike_host.py",
            "scripts/run_func_model_signoff.py",
        ],
        required_metrics=["spike.mode", "spike.exit_code",
                          "spike.tolerance_result", "spike.elapsed_s",
                          "evidence.verdict"],
        is_pytest=False,
    ),
    "task-1b-v3-spike-chain": CaseDef(
        case_id="task-1b-v3-spike-chain",
        argv=["bash", "scripts/run_fm_env.sh", "--", "python3",
              "sim/spike_host.py", "--mode", "chain"],
        evidence_path="task-1b-spike-chain.txt",
        expected_exit=0,
        min_collected=0,
        min_passed=0,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=[
            "scripts/run_fm_env.sh",
            "sim/spike_host.py",
            "scripts/run_func_model_signoff.py",
        ],
        required_metrics=["spike.mode", "spike.exit_code",
                          "spike.tolerance_result", "spike.elapsed_s",
                          "evidence.verdict"],
        is_pytest=False,
    ),
    "task-1c-v3-spike-forward": CaseDef(
        case_id="task-1c-v3-spike-forward",
        argv=["bash", "scripts/run_fm_env.sh", "--", "python3",
              "sim/spike_host.py", "--mode", "forward"],
        evidence_path="task-1c-spike-forward.txt",
        expected_exit=0,
        min_collected=0,
        min_passed=0,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=[
            "scripts/run_fm_env.sh",
            "sim/spike_host.py",
            "scripts/run_func_model_signoff.py",
        ],
        required_metrics=["spike.mode", "spike.exit_code",
                          "spike.tolerance_result", "spike.elapsed_s",
                          "evidence.verdict"],
        is_pytest=False,
    ),
    "task-1d-v3-spike-pcie-dma": CaseDef(
        case_id="task-1d-v3-spike-pcie-dma",
        argv=["bash", "scripts/run_fm_env.sh", "--", "python3",
              "sim/spike_host.py", "--mode", "pcie_dma"],
        evidence_path="task-1d-spike-pcie-dma.txt",
        expected_exit=0,
        min_collected=0,
        min_passed=0,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=[
            "scripts/run_fm_env.sh",
            "sim/spike_host.py",
            "scripts/run_func_model_signoff.py",
        ],
        required_metrics=["spike.mode", "spike.exit_code",
                          "spike.tolerance_result", "spike.elapsed_s",
                          "evidence.verdict"],
        is_pytest=False,
    ),

    # T2 — PCIe DMA functional end-to-end (pytest-based)
    "task-2-v3-pcie-dma": CaseDef(
        case_id="task-2-v3-pcie-dma",
        argv=["python3", "-m", "pytest",
              "sim/tests/test_func_model_signoff_v3_pcie.py", "-q"],
        evidence_path="task-2-pcie-dma.txt",
        expected_exit=0,
        min_collected=1,
        min_passed=1,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=[
            "sim/tests/test_func_model_signoff_v3_pcie.py",
            "scripts/run_func_model_signoff.py",
        ],
        required_metrics=["tests.collected", "tests.passed", "tests.failed",
                          "tests.skipped", "tests.xfailed", "evidence.verdict"],
        is_pytest=True,
    ),

    # T3 — AXI Crossbar functional verification (pytest-based)
    "task-3-v3-crossbar": CaseDef(
        case_id="task-3-v3-crossbar",
        argv=["python3", "-m", "pytest",
              "sim/tests/test_func_model_signoff_v3_crossbar.py", "-q"],
        evidence_path="task-3-crossbar.txt",
        expected_exit=0,
        min_collected=1,
        min_passed=1,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=[
            "sim/tests/test_func_model_signoff_v3_crossbar.py",
            "scripts/run_func_model_signoff.py",
        ],
        required_metrics=["tests.collected", "tests.passed", "tests.failed",
                          "tests.skipped", "tests.xfailed", "evidence.verdict"],
        is_pytest=True,
    ),

    # T4 — Doorbell mechanism verification (pytest-based)
    "task-4-v3-doorbell": CaseDef(
        case_id="task-4-v3-doorbell",
        argv=["python3", "-m", "pytest",
              "sim/tests/test_func_model_signoff_v3_doorbell.py", "-q"],
        evidence_path="task-4-doorbell.txt",
        expected_exit=0,
        min_collected=1,
        min_passed=1,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=[
            "sim/tests/test_func_model_signoff_v3_doorbell.py",
            "scripts/run_func_model_signoff.py",
        ],
        required_metrics=["tests.collected", "tests.passed", "tests.failed",
                          "tests.skipped", "tests.xfailed", "evidence.verdict"],
        is_pytest=True,
    ),

    # T5 — Interrupt controller verification (pytest-based)
    "task-5-v3-intc": CaseDef(
        case_id="task-5-v3-intc",
        argv=["python3", "-m", "pytest",
              "sim/tests/test_func_model_signoff_v3_intc.py", "-q"],
        evidence_path="task-5-intc.txt",
        expected_exit=0,
        min_collected=9,
        min_passed=9,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=[
            "sim/tests/test_func_model_signoff_v3_intc.py",
            "scripts/run_func_model_signoff.py",
        ],
        required_metrics=["tests.collected", "tests.passed", "tests.failed",
                          "tests.skipped", "tests.xfailed", "evidence.verdict"],
        is_pytest=True,
    ),

    # T6 — Host CPU (Ibex) verification (pytest-based)
    "task-6-v3-host-cpu": CaseDef(
        case_id="task-6-v3-host-cpu",
        argv=["python3", "-m", "pytest",
              "sim/tests/test_func_model_signoff_v3_host.py", "-q"],
        evidence_path="task-6-host-cpu.txt",
        expected_exit=0,
        min_collected=4,
        min_passed=4,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=[
            "sim/tests/test_func_model_signoff_v3_host.py",
            "scripts/run_func_model_signoff.py",
        ],
        required_metrics=["tests.collected", "tests.passed", "tests.failed",
                          "tests.skipped", "tests.xfailed", "evidence.verdict"],
        is_pytest=True,
    ),

    # T7 — Full SoC integration chain verification (4 test scenarios)
    "task-7-v3-soc-integration": CaseDef(
        case_id="task-7-v3-soc-integration",
        argv=["python3", "-m", "pytest",
              "sim/tests/test_func_model_signoff_v3_integration.py", "-q"],
        evidence_path="task-7-soc-integration.txt",
        expected_exit=0,
        min_collected=4,
        min_passed=4,
        forbid_skip=True,
        forbid_xfail=True,
        source_fingerprint_globs=[
            "sim/tests/test_func_model_signoff_v3_integration.py",
            "scripts/run_func_model_signoff.py",
        ],
        required_metrics=["tests.collected", "tests.passed", "tests.failed",
                          "tests.skipped", "tests.xfailed", "evidence.verdict"],
        is_pytest=True,
    ),
}


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
def build_env() -> Dict[str, str]:
    """Return the subprocess environment with PYTHONPATH=sim and QWEN3B_GGUF set."""
    env = os.environ.copy()
    # PYTHONPATH: sim + .venv_pytest (pytest) + .venv_deps (gguf/pyyaml/requests/tqdm) + existing
    parts = [str(SIM_DIR)]
    venv_pytest = REPO_ROOT / ".venv_pytest"
    if venv_pytest.is_dir():
        parts.append(str(venv_pytest))
    venv_deps = REPO_ROOT / ".venv_deps"
    if venv_deps.is_dir():
        parts.append(str(venv_deps))
    existing = env.get("PYTHONPATH", "")
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = ":".join(parts)
    # QWEN3B_GGUF default
    if "QWEN3B_GGUF" not in env:
        env["QWEN3B_GGUF"] = "/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf"
    # Propagate the runner's own Python interpreter so subprocess wrappers
    # (e.g. run_fm_env.sh) can use it via FM_PYTHON.
    env["FM_PYTHON"] = sys.executable
    return env


# ---------------------------------------------------------------------------
# Command hash
# ---------------------------------------------------------------------------
def command_hash(argv: List[str]) -> str:
    """SHA-256 hash of the command line (argv as sorted JSON)."""
    return hashlib.sha256(json.dumps(argv, sort_keys=True).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Execute a case
# ---------------------------------------------------------------------------
def run_case(case: CaseDef, evidence_file: Path) -> bool:
    """Execute a single case and write evidence to *evidence_file*.

    Returns True if the case passes, False otherwise.
    """
    start_utc = datetime.now(timezone.utc)
    env = build_env()

    # Compute fingerprint before running (running might change files)
    fingerprint, file_list = compute_source_fingerprint(case.source_fingerprint_globs)

    head_before = git_head()
    branch_before = git_branch()
    dirty_before = git_dirty_summary()

    # Prepare JUnit XML temp file for pytest cases
    junit_xml: Optional[Path] = None
    argv = list(case.argv)

    if case.is_pytest and case.argv:
        junit_fd, junit_path = tempfile.mkstemp(suffix=".xml", prefix="junit_")
        os.close(junit_fd)
        junit_xml = Path(junit_path)
        argv = []
        junit_inserted = False
        for a in case.argv:
            argv.append(a)
            if not junit_inserted and (a == "pytest" or a.endswith("/pytest")):
                argv.append(f"--junitxml={junit_xml}")
                junit_inserted = True

        # Guard against recursive runner invocations: when the spawned pytest
        # session itself imports and re-invokes the runner, stop the chain.
        env["_FM_SIGNOFF_RECURSE_GUARD"] = "1"

    # Tell test files which case ID they're running under, so emit wrappers
    # can tag metrics correctly when the same test file serves multiple cases.
    env["_FM_CASE_ID"] = case.case_id

    # Normalize argv: replace "python3" with sys.executable for environments
    # where python3 is not in PATH (e.g. sz0001 EDA server).
    normalized = False
    for i, a in enumerate(argv):
        if a == "python3":
            argv[i] = sys.executable
            normalized = True
    if normalized:
        print(f"[runner] argv normalized: python3 -> {sys.executable}")

    # Run subprocess
    try:
        result = subprocess.run(
            argv,
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=1200 if case.case_id.startswith("task-1") else 600,
        )
    except subprocess.TimeoutExpired:
        result = None
        exit_code = -1
        stdout = ""
        stderr = "TIMEOUT after 600s"
    except Exception as exc:
        result = None
        exit_code = -2
        stdout = ""
        stderr = f"EXCEPTION: {exc}"

    if result is not None:
        exit_code = result.returncode
        stdout = result.stdout
        stderr = result.stderr

    end_utc = datetime.now(timezone.utc)
    elapsed_s = (end_utc - start_utc).total_seconds()

    # Parse JUnit XML
    pytest_result: Optional[PytestResult] = None
    if case.is_pytest and junit_xml is not None:
        pytest_result = parse_junit_xml(junit_xml)
        # Clean up
        try:
            junit_xml.unlink()
        except OSError:
            pass

    # Parse metrics from stdout
    metrics = parse_metrics_from_stdout(stdout)

    # Build result structure
    collected = pytest_result.collected if pytest_result else 0
    passed = pytest_result.passed if pytest_result else 0
    failed = pytest_result.failed if pytest_result else 0
    skipped = pytest_result.skipped if pytest_result else 0
    xfailed = pytest_result.xfailed if pytest_result else 0

    # Push test metrics into metrics list
    metrics.append({"case": case.case_id, "key": "tests.collected", "value": collected})
    metrics.append({"case": case.case_id, "key": "tests.passed", "value": passed})
    metrics.append({"case": case.case_id, "key": "tests.failed", "value": failed})
    metrics.append({"case": case.case_id, "key": "tests.skipped", "value": skipped})
    metrics.append({"case": case.case_id, "key": "tests.xfailed", "value": xfailed})

    # Add evidence.verdict as a tentative metric so it is present when
    # _determine_verdict checks required_metrics; update value afterward.
    verdict_idx = len(metrics)
    metrics.append({"case": case.case_id, "key": "evidence.verdict", "value": "pending"})

    # Determine verdict
    verdict = _determine_verdict(case, exit_code, pytest_result, metrics, stdout)

    # Update the verdict metric with the actual result
    metrics[verdict_idx] = {"case": case.case_id, "key": "evidence.verdict",
                             "value": "pass" if verdict == "pass" else "fail"}

    # Build evidence document
    evidence_lines = [
        f"case_id: {case.case_id}",
        f"utc_start: {start_utc.isoformat()}",
        f"utc_end: {end_utc.isoformat()}",
        f"elapsed_s: {elapsed_s:.3f}",
        f"branch: {branch_before}",
        f"head: {head_before}",
        f"dirty_worktree: {dirty_before}",
        f"argv: {json.dumps(case.argv)}",
        f"command_hash: {command_hash(case.argv)}",
        f"exit_code: {exit_code}",
        f"source_fingerprint: {fingerprint}",
        f"source_files ({len(file_list)}):",
    ]
    for f in file_list:
        evidence_lines.append(f"  - {f}")
    evidence_lines.append(f"verdict: {verdict}")

    if case.is_pytest and pytest_result is not None:
        evidence_lines.append(f"test_collected: {collected}")
        evidence_lines.append(f"test_passed: {passed}")
        evidence_lines.append(f"test_failed: {failed}")
        evidence_lines.append(f"test_skipped: {skipped}")
        evidence_lines.append(f"test_xfailed: {xfailed}")

    # Attach metrics
    for m in metrics:
        evidence_lines.append(f"SIGNOFF_METRIC {json.dumps(m, sort_keys=True)}")

    evidence_lines.append("")
    evidence_lines.append("--- STDOUT (first 8000 chars) ---")
    evidence_lines.append(stdout[:8000])
    if stderr:
        evidence_lines.append("--- STDERR (first 4000 chars) ---")
        evidence_lines.append(stderr[:4000])
    evidence_lines.append("--- END ---")
    evidence_lines.append("")

    # Atomic write
    _atomic_write(evidence_file, "\n".join(evidence_lines))

    return verdict == "pass"


def _atomic_write(target: Path, content: str) -> None:
    """Write content to target atomically via temp file + rename."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=target.name + ".tmp.")
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    os.rename(tmp, str(target))


def _determine_verdict(
    case: CaseDef,
    exit_code: int,
    pytest_result: Optional[PytestResult],
    metrics: List[Dict[str, Any]],
    stdout: str,
) -> str:
    """Determine whether a case passes or fails."""
    reasons: List[str] = []

    # Expected failure case (task-1-comparator-red)
    if case.expected_failure:
        if exit_code == 0:
            reasons.append("expected_failure_case_exited_0")
        if case.expected_failure_pattern:
            if not re.search(case.expected_failure_pattern, stdout, re.IGNORECASE):
                reasons.append("expected_failure_pattern_not_found")
        if not reasons:
            return "pass"
        for r in reasons:
            print(f"  FAIL [{case.case_id}]: {r}", file=sys.stderr)
        return "fail"

    # Exit code check
    if case.expected_exit is not None and exit_code != case.expected_exit:
        reasons.append(f"exit_code:{exit_code}!={case.expected_exit}")

    # Pytest-specific checks
    if pytest_result is not None:
        if case_zero_tests(pytest_result):
            reasons.append("zero_tests_collected")
        if case.forbid_skip and pytest_result.any_skip:
            reasons.append(f"skip_forbidden:{pytest_result.skipped}")
        if case.forbid_xfail and pytest_result.any_xfail:
            reasons.append(f"xfail_forbidden:{pytest_result.xfailed}")
        if case.min_collected > 0 and pytest_result.collected < case.min_collected:
            reasons.append(f"collected:{pytest_result.collected}<{case.min_collected}")
        if case.min_passed > 0 and pytest_result.passed < case.min_passed:
            reasons.append(f"passed:{pytest_result.passed}<{case.min_passed}")

    # Metric checks
    present_keys = {m["key"] for m in metrics if isinstance(m.get("key"), str)}
    for req in case.required_metrics:
        if req not in present_keys:
            reasons.append(f"missing_metric:{req}")

    if reasons:
        for r in reasons:
            print(f"  FAIL [{case.case_id}]: {r}", file=sys.stderr)
        return "fail"
    return "pass"


def case_zero_tests(pytest_result: Optional[PytestResult]) -> bool:
    """Check if a pytest case collected zero tests."""
    return pytest_result is not None and pytest_result.collected == 0


# ---------------------------------------------------------------------------
# Validate evidence
# ---------------------------------------------------------------------------
def validate_case(case: CaseDef) -> bool:
    """Validate existing evidence for a case. Returns True if valid."""
    evidence_file = EVIDENCE_DIR / case.evidence_path
    if not evidence_file.is_file():
        print(f"  MISSING evidence: {evidence_file}", file=sys.stderr)
        return False

    content = evidence_file.read_text()
    lines = content.splitlines()
    evidence: Dict[str, str] = {}
    metrics: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if line.startswith("SIGNOFF_METRIC "):
            try:
                m = json.loads(line[len("SIGNOFF_METRIC "):])
                if isinstance(m, dict):
                    metrics.append(m)
            except json.JSONDecodeError:
                pass
        elif ": " in line and not line.startswith(("  ", "-", "---")):
            key, _, val = line.partition(": ")
            evidence[key.strip()] = val.strip()

    # Staleness checks
    current_head = git_head()
    recorded_head = evidence.get("head", "")
    if current_head and recorded_head and current_head != recorded_head:
        if git_is_ancestor(recorded_head, current_head):
            pass  # ancestor HEAD is valid — source may not have changed
        else:
            print(f"  STALE: HEAD mismatch (recorded={recorded_head[:12]}"
                  f" current={current_head[:12]}, not an ancestor)",
                  file=sys.stderr)
            return False

    current_fp, _ = compute_source_fingerprint(case.source_fingerprint_globs)
    recorded_fp = evidence.get("source_fingerprint", "")
    if current_fp and recorded_fp and current_fp != recorded_fp:
        # Exception: task-1-comparator-red is intentionally historical
        if case.case_id == "task-1-comparator-red":
            pass  # allow stale for this case
        else:
            print(f"  STALE: source_fingerprint mismatch",
                  file=sys.stderr)
            return False

    current_ch = command_hash(case.argv)
    recorded_ch = evidence.get("command_hash", "")
    if current_ch and recorded_ch and current_ch != recorded_ch:
        print(f"  STALE: command_hash mismatch",
              file=sys.stderr)
        return False

    # Verdict check
    verdict = evidence.get("verdict", "")
    if verdict != "pass":
        print(f"  FAIL: verdict={verdict}", file=sys.stderr)
        return False

    # Metric checks
    present_keys = {m.get("key") for m in metrics if isinstance(m.get("key"), str)}
    for req in case.required_metrics:
        if req not in present_keys:
            print(f"  FAIL: missing_metric:{req}", file=sys.stderr)
            return False

    # Validate metric JSON
    for m in metrics:
        # Check well-formedness
        if not isinstance(m, dict):
            print(f"  FAIL: malformed_metric_not_dict", file=sys.stderr)
            return False
        case_id = m.get("case")
        if case_id and case_id != case.case_id:
            print(f"  FAIL: metric_case_id_mismatch:{case_id}!={case.case_id}", file=sys.stderr)
            return False
        value = m.get("value")
        if value is None:
            print(f"  FAIL: metric_value_is_null for key={m.get('key')}", file=sys.stderr)
            return False
        # Reject non-finite numeric values
        if isinstance(value, float):
            import math
            if math.isnan(value) or math.isinf(value):
                print(f"  FAIL: non_finite_metric_value for key={m.get('key')}", file=sys.stderr)
                return False

    # Duplicate key detection
    seen_keys: Dict[str, Any] = {}
    for m in metrics:
        key = m.get("key")
        if key and key in seen_keys:
            if seen_keys[key] != m.get("value"):
                print(f"  FAIL: duplicate_metric_key_with_conflicting_value:{key}",
                      file=sys.stderr)
                return False
        elif key:
            seen_keys[key] = m.get("value")

    print(f"  OK: {case.case_id}")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Authoritative Func Model signoff evidence runner",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Execute a case and write evidence")
    run_parser.add_argument("--case", required=True, help="Case ID to execute")

    validate_parser = sub.add_parser("validate", help="Validate existing evidence")
    validate_parser.add_argument("--case", help="Case ID to validate")
    validate_parser.add_argument("--all-functional", action="store_true",
                                  help="Validate all functional cases (v1/v2, excludes v3)")
    validate_parser.add_argument("--v3", action="store_true",
                                  help="Validate all v3 SoC integration cases")

    args = parser.parse_args()

    if args.command == "run":
        case_id = args.case
        if case_id not in CASE_REGISTRY:
            print(f"ERROR: unknown case '{case_id}'", file=sys.stderr)
            print(f"Known cases: {', '.join(sorted(CASE_REGISTRY.keys()))}", file=sys.stderr)
            sys.exit(1)

        case = CASE_REGISTRY[case_id]
        if not case.argv:
            print(f"ERROR: case '{case_id}' has no argv (validate-only)", file=sys.stderr)
            sys.exit(1)

        evidence_file = EVIDENCE_DIR / case.evidence_path
        print(f"Running: {case_id}")
        print(f"  argv: {case.argv}")
        passed = run_case(case, evidence_file)
        print(f"  evidence: {evidence_file}")
        print(f"  verdict: {'PASS' if passed else 'FAIL'}")
        sys.exit(0 if passed else 1)

    elif args.command == "validate":
        if args.v3:
            all_ok = True
            v3_found = 0
            for case_id, case in CASE_REGISTRY.items():
                if "-v3-" not in case_id:
                    continue
                if not case.argv:
                    continue
                v3_found += 1
                ok = validate_case(case)
                if not ok:
                    all_ok = False
            print(f"V3 cases discovered: {v3_found}")
            sys.exit(0 if all_ok else 1)
        elif args.all_functional:
            all_ok = True
            for case_id, case in CASE_REGISTRY.items():
                if not case.argv:
                    continue
                if "-v3-" in case_id:
                    continue
                ok = validate_case(case)
                if not ok:
                    all_ok = False
            sys.exit(0 if all_ok else 1)
        elif args.case:
            case_id = args.case
            if case_id not in CASE_REGISTRY:
                print(f"ERROR: unknown case '{case_id}'", file=sys.stderr)
                sys.exit(1)
            ok = validate_case(CASE_REGISTRY[case_id])
            sys.exit(0 if ok else 1)
        else:
            print("ERROR: specify --case or --all-functional", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
