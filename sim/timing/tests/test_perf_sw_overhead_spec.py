"""T12: Provider-vs-oracle tests for SW overhead architectural estimates.

GREEN: Baseline characterization — SWOverheadModel.estimate_for_spec() against
normative spec expected values for the 4 sw_overhead rows.

RED: Mutation detection — include-in-total (assumption_only must be true,
never in a canonical total), stale-28-layers (num_layers=28 default must fail
tolerance for 36-layer workloads).

Per T1 spec tolerance gate:
- oracle > 10 cycles -> abs(model - oracle) / oracle * 100 <= 10%
- 0 < oracle <= 10 -> abs(model - oracle) <= 1 cycle
- oracle == 0 -> model must also be 0
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = REPO_ROOT / "config" / "func_model_perf_spec_v1.json"
ORACLE_PATH = REPO_ROOT / "config" / "func_model_perf_oracle_v1.json"
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_func_model_perf_spec.py"

# ── Spec constants (mirror the verifier's normative formulas) ─────────────

_SW_FIXED = 200
_SW_BARRIER = 18
_SW_DESC = 10
_SW_ISA_PER_INST = 4.8
_SW_CYCLE_RATIO = 5
_SW_CPI = 1.2
_SW_TILES_PER_LAYER = 5500
_SW_QWEN_ISA_PER_LAYER = 17

# (workload, dma_chain) -> (num_layers, num_ops, expected_cycles, param_id)
_ORACLE_ROWS = [
    ("qwen_blk0", True, 1, 0, 1500, "sw_qwen_blk0"),
    ("qwen_decode_36L", True, 36, 0, 12000, "sw_qwen_decode_36L_dma_chain"),
    ("qwen_decode_36L", False, 36, 0, 180000, "sw_qwen_decode_36L_no_dma_chain"),
    ("resnet50", True, 1, 105, 3500, "sw_resnet50"),
]


def _within_tolerance(model_val: int, oracle_val: int) -> bool:
    """Check model value against oracle per T1 spec provider-error policy."""
    if oracle_val == 0:
        return model_val == 0
    abs_err = abs(model_val - oracle_val)
    if oracle_val <= 10:
        return abs_err <= 1
    return (abs_err / oracle_val * 100) <= 10.0


def _sw_raw_mxu_equiv(workload: str, num_layers: int, num_ops: int,
                      dma_chain: bool) -> int:
    """Analytic raw RISC-V -> MXU-equivalent estimate (no amortization)."""
    if workload == "resnet50":
        riscv = _SW_FIXED + num_ops * _SW_ISA_PER_INST
    elif dma_chain:
        barrier = num_layers * _SW_BARRIER
        desc = num_layers * _SW_DESC
        isa = round(num_layers * _SW_QWEN_ISA_PER_LAYER * _SW_ISA_PER_INST)
        riscv = _SW_FIXED + barrier + desc + isa
    else:
        barrier = num_layers * _SW_BARRIER
        per_tile = _SW_TILES_PER_LAYER * num_layers * 3 * _SW_CPI
        riscv = _SW_FIXED + barrier + per_tile
    return int(riscv) * _SW_CYCLE_RATIO


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def spec() -> dict:
    with open(SPEC_PATH, "r") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def oracle() -> dict:
    with open(ORACLE_PATH, "r") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def sw_model() -> Any:
    from models.sw_overhead import SWOverheadModel
    return SWOverheadModel()


# ── GREEN: baseline characterization ──────────────────────────────────────


class TestSWOverheadGreenSpecMatch:
    """estimate_for_spec() must match the spec/oracle expected_cycles."""

    @pytest.mark.parametrize(
        "workload,dma_chain,expected_cycles,param_id",
        [(w, dc, ec, pid) for w, dc, _nl, _no, ec, pid in _ORACLE_ROWS],
        ids=[pid for _w, _dc, _nl, _no, _ec, pid in _ORACLE_ROWS],
    )
    def test_estimate_matches_spec(self, sw_model, workload, dma_chain,
                                   expected_cycles, param_id):
        result = sw_model.estimate_for_spec(workload, dma_chain=dma_chain)
        assert _within_tolerance(result.expected_cycles, expected_cycles), (
            f"[{param_id}] model={result.expected_cycles}, spec={expected_cycles}"
        )

    @pytest.mark.parametrize(
        "workload,dma_chain,num_layers,num_ops,expected_cycles,param_id",
        _ORACLE_ROWS,
        ids=[pid for _w, _dc, _nl, _no, _ec, pid in _ORACLE_ROWS],
    )
    def test_raw_decomposition_matches_spec_arithmetic(
        self, sw_model, workload, dma_chain, num_layers, num_ops,
        expected_cycles, param_id
    ):
        """The analytic raw MXU-equivalent must match the spec's own arithmetic."""
        result = sw_model.estimate_for_spec(workload, dma_chain=dma_chain)
        expected_raw = _sw_raw_mxu_equiv(workload, num_layers, num_ops, dma_chain)
        assert result.mxu_equiv_raw == expected_raw, (
            f"[{param_id}] raw={result.mxu_equiv_raw}, expected={expected_raw}"
        )
        assert result.num_layers == num_layers
        assert result.dma_chain is dma_chain

    def test_no_dma_chain_exceeds_dma_chain(self, sw_model):
        """Removing the DMA descriptor chain must strictly increase overhead."""
        with_chain = sw_model.estimate_for_spec("qwen_decode_36L", dma_chain=True)
        without_chain = sw_model.estimate_for_spec("qwen_decode_36L", dma_chain=False)
        assert without_chain.expected_cycles > with_chain.expected_cycles
        assert without_chain.mxu_equiv_raw > with_chain.mxu_equiv_raw

    def test_monotonicity_across_workloads(self, sw_model):
        """More work -> same or more expected cycles."""
        blk0 = sw_model.estimate_for_spec("qwen_blk0").expected_cycles
        decode = sw_model.estimate_for_spec("qwen_decode_36L").expected_cycles
        assert decode > blk0


# ── Structure checks: assumption-only, never canonical ────────────────────


class TestSWOverheadStructure:
    """SW overhead rows must be marked assumption-only, excluded from totals."""

    def test_all_spec_entries_assumption_only(self, spec):
        for entry in spec["domains"]["sw_overhead"]:
            mono = entry.get("monotonicity_annotations", {})
            assert mono.get("assumption_only") is True, entry["parameter_id"]

    def test_all_oracle_entries_assumption_only(self, oracle):
        for entry in oracle["entries"]["sw_overhead"]:
            assert entry.get("assumption_only") is True, entry["parameter_id"]

    def test_no_oracle_entry_implies_canonical_inclusion(self, oracle):
        for entry in oracle["entries"]["sw_overhead"]:
            assert entry.get("included_in_canonical_total") is not True, (
                f"{entry['parameter_id']} would add SW overhead to a canonical total"
            )

    def test_model_marks_assumption_only_and_not_canonical(self, sw_model):
        for workload, dma_chain in (("qwen_blk0", True),
                                    ("qwen_decode_36L", True),
                                    ("qwen_decode_36L", False),
                                    ("resnet50", True)):
            result = sw_model.estimate_for_spec(workload, dma_chain=dma_chain)
            assert result.assumption_only is True
            assert result.included_in_canonical_total is False

    def test_unknown_workload_rejected(self, sw_model):
        with pytest.raises(ValueError):
            sw_model.estimate_for_spec("unknown_model")


# ── RED: Mutation detection ───────────────────────────────────────────────


class TestSWOverheadMutations:
    """Adversarial mutations that a signoff validator must reject."""

    def test_mutation_include_in_total_drop_assumption_only(self, oracle):
        """include-in-total mutation: dropping assumption_only must be caught."""
        for entry in oracle["entries"]["sw_overhead"]:
            assert entry.get("assumption_only") is True, (
                f"{entry['parameter_id']} missing assumption_only=true — "
                f"SW overhead would enter a canonical total"
            )

    def test_mutation_include_in_total_canonical_flag(self, oracle):
        """include-in-total mutation: included_in_canonical_total=true must be caught."""
        for entry in oracle["entries"]["sw_overhead"]:
            assert entry.get("included_in_canonical_total") is not True, (
                f"{entry['parameter_id']} claims canonical-total inclusion"
            )

    def test_mutation_stale_28_layers_fails_tolerance(self):
        """stale-28-layers mutation: num_layers=28 must fail 36-layer oracle rows."""
        with open(ORACLE_PATH, "r") as f:
            oracle = json.load(f)
        for entry in oracle["entries"]["sw_overhead"]:
            inputs = entry["inputs"]
            if int(inputs.get("num_layers", 1)) != 36:
                continue
            stale = _sw_raw_mxu_equiv(str(inputs["workload"]), 28, 0, bool(inputs["dma_chain"]))
            assert not _within_tolerance(stale, int(entry["expected_cycles"])), (
                f"{entry['parameter_id']}: stale num_layers=28 estimate {stale} "
                f"passes oracle {entry['expected_cycles']} — stale default undetectable"
            )

    def test_mutation_stale_28_dma_chain_differs_from_36(self):
        """num_layers=28 must produce strictly fewer raw cycles than 36 layers."""
        raw_36 = _sw_raw_mxu_equiv("qwen_decode_36L", 36, 0, True)
        raw_28 = _sw_raw_mxu_equiv("qwen_decode_36L", 28, 0, True)
        assert raw_28 < raw_36


# ── Spec-oracle cross-consistency ─────────────────────────────────────────


class TestSpecOracleConsistency:
    """Verify the spec and oracle agree on the 4 sw_overhead rows."""

    def test_all_4_sw_spec_oracle_match(self, spec, oracle):
        spec_by_id = {e["parameter_id"]: e for e in spec["domains"]["sw_overhead"]}
        oracle_by_id = {e["parameter_id"]: e for e in oracle["entries"]["sw_overhead"]}
        mismatches = []
        for pid, se in spec_by_id.items():
            oe = oracle_by_id.get(pid)
            if oe is None:
                mismatches.append(f"{pid}: missing from oracle")
            elif se["estimated_cycles"] != oe["expected_cycles"]:
                mismatches.append(
                    f"{pid}: spec={se['estimated_cycles']}, oracle={oe['expected_cycles']}"
                )
        assert len(mismatches) == 0, f"Spec-oracle mismatches: {mismatches}"

    def test_sw_4_rows_total(self, spec):
        assert len(spec["domains"]["sw_overhead"]) == 4


# ── Verifier CLI: domain + mutation dispatch ──────────────────────────────


class TestVerifierCLI:
    """End-to-end T12 CLI dispatch against the real spec/oracle."""

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT), "--spec", str(SPEC_PATH),
             "--oracle", str(ORACLE_PATH), *args],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )

    def test_domain_sw_overhead_all_4_rows(self):
        proc = self._run("--domain", "sw_overhead")
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        data = json.loads(proc.stdout)
        assert data["rows"] == 4
        assert data["failed"] == 0
        assert data["domain_validation"]["verdict"] == "pass"
        assert set(data["domain_validation"]["domains"].keys()) == {"sw_overhead"}

    def test_mutations_rejected_count_2(self):
        proc = self._run("--domain", "sw_overhead", "--mutations",
                         "include-in-total,stale-28-layers")
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        data = json.loads(proc.stdout)
        assert data["mutations"]["rejected_mutations"] == 2
        assert data["mutations"]["verdict"] == "pass"
        assert data["rows"] == 4

    def test_mutated_include_in_total_oracle_rejected(self, tmp_path):
        """A mutated oracle (assumption_only dropped) must exit nonzero."""
        with open(ORACLE_PATH, "r") as f:
            oracle = json.load(f)
        for e in oracle["entries"]["sw_overhead"]:
            e.pop("assumption_only", None)
        mutated = tmp_path / "oracle_include_in_total_mutated.json"
        mutated.write_text(json.dumps(oracle))
        proc = self._run("--domain", "sw_overhead", "--oracle", str(mutated),
                         "--mutations", "include-in-total")
        assert proc.returncode == 1
        data = json.loads(proc.stdout)
        assert data["verdict"] == "fail"
        assert data["mutations"]["results"]["include-in-total"]["verdict"] == "fail"

    def test_mutated_stale_28_oracle_rejected(self, tmp_path):
        """A mutated oracle (36L row encoding a 28-layer value) must exit nonzero."""
        with open(ORACLE_PATH, "r") as f:
            oracle = json.load(f)
        for e in oracle["entries"]["sw_overhead"]:
            if e["parameter_id"] == "sw_qwen_decode_36L_dma_chain":
                e["expected_cycles"] = _sw_raw_mxu_equiv("qwen_decode_36L", 28, 0, True)
        mutated = tmp_path / "oracle_stale_28_mutated.json"
        mutated.write_text(json.dumps(oracle))
        proc = self._run("--domain", "sw_overhead", "--oracle", str(mutated),
                         "--mutations", "stale-28-layers")
        assert proc.returncode == 1
        data = json.loads(proc.stdout)
        assert data["mutations"]["results"]["stale-28-layers"]["verdict"] == "fail"
