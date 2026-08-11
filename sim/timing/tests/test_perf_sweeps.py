"""T18 monotonicity and bottleneck-transition sweep tests."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from timing.sweeps import (
    _check_monotonic,
    run_negative_sweeps,
    run_sweeps,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = REPO_ROOT / "scripts" / "run_func_model_perf_signoff.py"


class TestCheckMonotonic:
    def test_resource_dimension_decreases(self):
        rows = [
            {"bandwidth": 6.4, "total_cycles": 100},
            {"bandwidth": 12.8, "total_cycles": 60},
            {"bandwidth": 25.6, "total_cycles": 40},
        ]
        assert _check_monotonic(rows, "bandwidth", strict=False) == []

    def test_workload_dimension_increases(self):
        rows = [
            {"prompt": 16, "prefill_cycles": 100},
            {"prompt": 128, "prefill_cycles": 400},
            {"prompt": 512, "prefill_cycles": 900},
        ]
        assert _check_monotonic(rows, "prompt", strict=False) == []

    def test_resource_positive_slope_fails(self):
        rows = [
            {"bandwidth": 6.4, "total_cycles": 100},
            {"bandwidth": 12.8, "total_cycles": 120},
        ]
        failures = _check_monotonic(rows, "bandwidth", strict=False)
        assert failures[0]["fault"] == "resource-positive-slope"

    def test_workload_negative_slope_fails(self):
        rows = [
            {"prompt": 16, "prefill_cycles": 100},
            {"prompt": 128, "prefill_cycles": 80},
        ]
        failures = _check_monotonic(rows, "prompt", strict=False)
        assert failures[0]["fault"] == "workload-negative-slope"

    def test_nan_slope_fails(self):
        rows = [
            {"bandwidth": 6.4, "total_cycles": 100},
            {"bandwidth": 12.8, "total_cycles": float("nan")},
        ]
        failures = _check_monotonic(rows, "bandwidth", strict=False)
        assert failures[0]["fault"] == "nan-slope"

    def test_unreachable_transition_detected_in_strict_mode(self):
        rows = [
            {"bandwidth": 6.4, "total_cycles": 100},
            {"bandwidth": 12.8, "total_cycles": 100},
        ]
        failures = _check_monotonic(rows, "bandwidth", strict=True)
        assert failures[0]["fault"] == "unreachable-transition"


class TestRunSweepsGreen:
    def test_all_grids_pass(self):
        report = run_sweeps(
            ["bandwidth", "array", "dma-channels", "prompt", "context", "noc-hop"],
            require_endpoints=["memory", "compute"],
        )
        assert report["verdict"] == "pass"
        assert set(report["sweeps"].keys()) == {
            "bandwidth",
            "array",
            "dma-channels",
            "prompt",
            "context",
            "noc-hop",
        }
        assert report["monotonicity_failures"] == []
        assert report["endpoint_failures"] == []
        for ep in report["endpoints"]:
            assert ep["passed"]

    def test_bandwidth_rows_show_bottleneck_transition(self):
        report = run_sweeps(["bandwidth"])
        rows = report["sweeps"]["bandwidth"]["rows"]
        assert len(rows) == 5
        first = rows[0]["total_cycles"]
        last = rows[-1]["total_cycles"]
        assert last < first
        assert rows[0]["dram_bw_share_pct"] > rows[-1]["dram_bw_share_pct"]


class TestRunNegativeSweeps:
    def test_all_faults_rejected(self):
        report = run_negative_sweeps(
            [
                "resource-positive-slope",
                "workload-negative-slope",
                "nan-slope",
                "missing-6p4-endpoint",
                "unreachable-transition",
            ]
        )
        assert report["rejected"] == 5
        assert report["accepted"] == 0
        assert report["verdict"] == "pass"


class TestSweepCLI:
    def test_negative_case_sweeps_exits_zero(self):
        proc = subprocess.run(
            [
                sys.executable,
                str(RUNNER),
                "negative",
                "--case", "sweeps",
                "--faults",
                "resource-positive-slope,workload-negative-slope,nan-slope,missing-6p4-endpoint,unreachable-transition",
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert proc.returncode == 0, proc.stderr
        report = json.loads(proc.stdout)
        assert report["rejected"] == 5
        assert report["accepted"] == 0
