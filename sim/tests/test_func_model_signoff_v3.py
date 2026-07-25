"""Unit tests verifying v3 SoC integration signoff case registry entries.

Covers: presence of all v3 case IDs, evidence path uniqueness/non-emptiness,
required-metrics completeness, --v3 CLI flag recognition, and backward
compatibility of existing v2 cases.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import run_func_model_signoff as _runner

EXPECTED_V3_CASES: List[str] = [
    "task-0-v3-signoff-runner",
    "task-1a-v3-spike-mmul-smoke",
    "task-1b-v3-spike-chain",
    "task-1c-v3-spike-forward",
    "task-1d-v3-spike-pcie-dma",
    "task-2-v3-pcie-dma",
    "task-3-v3-crossbar",
    "task-4-v3-doorbell",
    "task-5-v3-intc",
    "task-6-v3-host-cpu",
    "task-7-v3-soc-integration",
]

EXPECTED_V2_CASES: List[str] = [
    "task-0a-signoff-runner",
    "task-0b-qwen3b-synthetic-and-real-preflight",
    "task-1-comparator-red",
    "task-2-comparator-green",
    "task-2-w2-2-golden-vectors",
    "task-3-scaled-qwen-regressions",
    "task-4a-qwen3b-direct-mmio",
    "task-4b-qwen3b-tiled-mmul",
    "task-4c1-qwen25-3b-selective-load-and-reference-inputs",
    "task-4c2-qwen25-3b-real-direct-projections",
    "task-4c3-qwen25-3b-real-tiled-projections",
    "task-4c4-qwen25-3b-real-connected-blk0",
    "task-5-qwen3b-robustness",
    "task-6-signoff-doc-consistency",
    "task-7-functional-selected-regression",
    "task-7-functional-full-sweep",
    "task-7-qwen3b-synthetic-stress-gates",
    "task-7-qwen25-3b-real-blk0-hard-gate",
    "task-7-w2-2-golden-vectors",
    "final-plan-compliance",
    "final-code-quality",
    "final-real-qa",
    "final-scope-fidelity",
]


class TestV3CasePresence:
    """Verify all expected v3 cases are registered with correct metadata."""

    def test_all_expected_v3_cases_registered(self) -> None:
        for cid in EXPECTED_V3_CASES:
            assert cid in _runner.CASE_REGISTRY, f"Missing v3 case: {cid}"

    def test_v3_cases_have_matching_case_id_field(self) -> None:
        for cid in EXPECTED_V3_CASES:
            case = _runner.CASE_REGISTRY[cid]
            assert case.case_id == cid, (
                f"Case registry key '{cid}' has mismatched case_id='{case.case_id}'"
            )


class TestV3EvidencePaths:
    """Verify evidence paths are unique and non-empty for all v3 cases."""

    def test_v3_evidence_paths_nonempty(self) -> None:
        for cid in EXPECTED_V3_CASES:
            case = _runner.CASE_REGISTRY[cid]
            assert case.evidence_path, f"Empty evidence_path for {cid}"

    def test_v3_evidence_paths_unique(self) -> None:
        paths: List[str] = []
        for cid in EXPECTED_V3_CASES:
            case = _runner.CASE_REGISTRY[cid]
            paths.append(case.evidence_path)
        assert len(paths) == len(set(paths)), (
            f"Duplicate evidence paths detected among v3 cases"
        )


class TestV3RequiredMetrics:
    """Verify required_metrics are non-empty for each v3 case."""

    def test_v3_required_metrics_nonempty(self) -> None:
        for cid in EXPECTED_V3_CASES:
            case = _runner.CASE_REGISTRY[cid]
            assert len(case.required_metrics) > 0, (
                f"Empty required_metrics for {cid}"
            )

    def test_v3_required_metrics_contain_verdict(self) -> None:
        for cid in EXPECTED_V3_CASES:
            case = _runner.CASE_REGISTRY[cid]
            assert "evidence.verdict" in case.required_metrics, (
                f"Missing evidence.verdict in required_metrics for {cid}"
            )

    def test_v3_spike_cases_have_correct_metrics(self) -> None:
        spike_cases = [
            "task-1a-v3-spike-mmul-smoke",
            "task-1b-v3-spike-chain",
            "task-1c-v3-spike-forward",
            "task-1d-v3-spike-pcie-dma",
        ]
        for cid in spike_cases:
            case = _runner.CASE_REGISTRY[cid]
            for key in ["spike.mode", "spike.exit_code", "spike.tolerance_result",
                        "spike.elapsed_s"]:
                assert key in case.required_metrics, (
                    f"Missing {key} in required_metrics for {cid}"
                )

    def test_v3_pytest_cases_have_standard_metrics(self) -> None:
        pytest_v3_cases = [
            "task-0-v3-signoff-runner",
            "task-2-v3-pcie-dma",
            "task-3-v3-crossbar",
            "task-4-v3-doorbell",
            "task-5-v3-intc",
            "task-6-v3-host-cpu",
            "task-7-v3-soc-integration",
        ]
        for cid in pytest_v3_cases:
            case = _runner.CASE_REGISTRY[cid]
            for key in ["tests.collected", "tests.passed", "tests.failed",
                        "tests.skipped", "tests.xfailed"]:
                assert key in case.required_metrics, (
                    f"Missing {key} in required_metrics for {cid}"
                )


class TestV3SpikeCasesNonPytest:
    """Verify spike cases are marked is_pytest=False."""

    def test_v3_spike_cases_are_non_pytest(self) -> None:
        spike_cases = [
            "task-1a-v3-spike-mmul-smoke",
            "task-1b-v3-spike-chain",
            "task-1c-v3-spike-forward",
            "task-1d-v3-spike-pcie-dma",
        ]
        for cid in spike_cases:
            case = _runner.CASE_REGISTRY[cid]
            assert case.is_pytest is False, (
                f"Spike case {cid} should have is_pytest=False"
            )


class TestV3CLIFlag:
    """Verify --v3 flag is recognized by the validate subcommand."""

    def test_v3_flag_in_validate_help(self) -> None:
        result = subprocess.run(
            [
                "python3",
                str(_SCRIPTS_DIR / "run_func_model_signoff.py"),
                "validate",
                "--help",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0
        assert "--v3" in result.stdout, (
            "Missing --v3 flag in validate help output"
        )
        assert "SoC integration" in result.stdout, (
            "Missing --v3 description in validate help output"
        )

    def test_v3_validate_runs_without_crashing(self) -> None:
        result = subprocess.run(
            [
                "python3",
                str(_SCRIPTS_DIR / "run_func_model_signoff.py"),
                "validate",
                "--v3",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "V3 cases discovered" in result.stdout, (
            "Missing discovery count in --v3 output"
        )


class TestV2BackwardCompatibility:
    """Verify existing v2 cases are still present and unchanged."""

    def test_all_final_cases_still_present(self) -> None:
        final_cases = [
            "final-plan-compliance",
            "final-code-quality",
            "final-real-qa",
            "final-scope-fidelity",
        ]
        for cid in final_cases:
            assert cid in _runner.CASE_REGISTRY, f"Missing final case: {cid}"

    def test_task_0a_still_present(self) -> None:
        assert "task-0a-signoff-runner" in _runner.CASE_REGISTRY
        case = _runner.CASE_REGISTRY["task-0a-signoff-runner"]
        assert case.expected_exit == 0
        assert case.is_pytest is True
        assert case.evidence_path == "task-0a-signoff-runner.txt"

    def test_task_1_comparator_red_still_present(self) -> None:
        assert "task-1-comparator-red" in _runner.CASE_REGISTRY
        case = _runner.CASE_REGISTRY["task-1-comparator-red"]
        assert case.expected_failure is True
        assert case.expected_failure_pattern == "mixed.*abs.*rel"

    def test_v2_case_ids_unchanged(self) -> None:
        for cid in EXPECTED_V2_CASES:
            assert cid in _runner.CASE_REGISTRY, (
                f"V2 case '{cid}' missing — registry may have been altered"
            )

    def test_v2_evidence_paths_unchanged(self) -> None:
        known_v2_paths = {
            "task-0a-signoff-runner": "task-0a-signoff-runner.txt",
            "task-1-comparator-red": "task-1-comparator-red.txt",
            "task-2-comparator-green": "task-2-comparator-green.txt",
            "task-7-functional-full-sweep": "task-7-functional-full-sweep.txt",
            "final-code-quality": "final-code-quality.txt",
        }
        for cid, expected_path in known_v2_paths.items():
            case = _runner.CASE_REGISTRY[cid]
            assert case.evidence_path == expected_path, (
                f"V2 case {cid} evidence_path changed: "
                f"'{case.evidence_path}' != '{expected_path}'"
            )

    def test_expected_failure_cases_have_pattern(self) -> None:
        for cid, case in _runner.CASE_REGISTRY.items():
            if "-v3-" in cid:
                continue
            if case.expected_failure:
                assert case.expected_failure_pattern, (
                    f"Case {cid} has expected_failure=True but no pattern"
                )


class TestV3VsV2Isolation:
    """Verify v3 cases are properly isolated from v2."""

    def test_v3_cases_not_in_v2_list(self) -> None:
        for cid in EXPECTED_V3_CASES:
            assert cid not in EXPECTED_V2_CASES, (
                f"V3 case '{cid}' collides with v2 list"
            )

    def test_v3_cases_have_v3_marker(self) -> None:
        for cid in EXPECTED_V3_CASES:
            assert "-v3-" in cid, (
                f"V3 case '{cid}' missing -v3- marker"
            )

    def test_source_fingerprint_globs_nonempty(self) -> None:
        for cid in EXPECTED_V3_CASES:
            case = _runner.CASE_REGISTRY[cid]
            assert len(case.source_fingerprint_globs) > 0, (
                f"Empty source_fingerprint_globs for {cid}"
            )
