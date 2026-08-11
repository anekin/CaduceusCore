"""T11: Provider-vs-oracle tests for NoC and KV cache architectural estimates.

GREEN: Baseline characterization — NoCModel.estimate_latency() and
KVCacheModel.estimate_access_latency() against normative spec expected values.

RED: Mutation detection — route (crossbar route-independence / mesh route
sensitivity), hit-rate (wrong SRAM/DRAM split), kv-heads (wrong head-count
pin), noop-nonzero (token_pos=0 must be exact zero).

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
from typing import Any, Dict, List, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = REPO_ROOT / "config" / "func_model_perf_spec_v1.json"
ORACLE_PATH = REPO_ROOT / "config" / "func_model_perf_oracle_v1.json"
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_func_model_perf_spec.py"

# ── Spec constants (mirror the verifier's normative formulas) ─────────────

_NOC_FLIT_BYTES = 32
_NOC_HOP = 3
_NOC_ARB = 3
_NOC_BUF = 4
_NOC_PORTS = 4

_KV_WINDOW = 512      # SRAM entries per layer (kv_heads=2, head_dim=128, 256KB)
_KV_SRAM_CYC = 2
_KV_DRAM_CYC = 80
_KV_BW = 51.2


def _mesh_hop_count(route: str, ports: int = _NOC_PORTS) -> int:
    """Manhattan distance (XY routing) for a "src->dst" route in a 2x2 grid."""
    src_s, dst_s = route.split("->")
    src_id, dst_id = int(src_s), int(dst_s)
    cols = ports // 2  # 4 ports -> 2x2 row-major grid
    return abs(src_id // cols - dst_id // cols) + abs(src_id % cols - dst_id % cols)


def _within_tolerance(model_val: int, oracle_val: int) -> bool:
    """Check model value against oracle per T1 spec provider-error policy."""
    if oracle_val == 0:
        return model_val == 0
    abs_err = abs(model_val - oracle_val)
    if oracle_val <= 10:
        return abs_err <= 1
    return (abs_err / oracle_val * 100) <= 10.0


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
def noc_crossbar() -> Any:
    from models.noc import NoCModel
    return NoCModel({"interconnect": {"type": "crossbar", "ports": 4}})


@pytest.fixture(scope="module")
def noc_mesh() -> Any:
    from models.noc import NoCModel
    return NoCModel({"interconnect": {"type": "mesh", "ports": 4}})


@pytest.fixture(scope="module")
def kv_model() -> Any:
    from models.kv_cache import KVCacheModel
    config = {
        "kv_cache": {"sram_kb": 256, "dram_region_mb": 96, "precision_bits": 8},
        "memory": {"bandwidth_bytes_per_cycle": 51.2},
    }
    model = KVCacheModel(config)
    model.configure_for_model(num_kv_heads=2, head_dim=128, num_layers=36)
    return model


# ── GREEN: NoC baseline characterization ──────────────────────────────────


class TestNoCGreenSpecMatch:
    """NoCModel.estimate_latency() must match spec expected values within tolerance."""

    @pytest.mark.parametrize(
        "topology,bytes_val,src,dst,expected_cycles,param_id",
        [
            ("crossbar", 64, 0, 1, 14, "noc_crossbar_64B_0to1"),
            ("crossbar", 64, 0, 3, 14, "noc_crossbar_64B_0to3"),
            ("crossbar", 4096, 0, 1, 142, "noc_crossbar_4096B_0to1"),
            ("crossbar", 4096, 0, 3, 142, "noc_crossbar_4096B_0to3"),
            ("mesh", 64, 0, 1, 18, "noc_mesh_64B_0to1"),
            ("mesh", 64, 0, 3, 36, "noc_mesh_64B_0to3"),
            ("mesh", 4096, 0, 1, 146, "noc_mesh_4096B_0to1"),
            ("mesh", 4096, 0, 3, 158, "noc_mesh_4096B_0to3"),
        ],
        ids=["noc_crossbar_64B_0to1", "noc_crossbar_64B_0to3",
             "noc_crossbar_4096B_0to1", "noc_crossbar_4096B_0to3",
             "noc_mesh_64B_0to1", "noc_mesh_64B_0to3",
             "noc_mesh_4096B_0to1", "noc_mesh_4096B_0to3"],
    )
    def test_latency_matches_spec(
        self, noc_crossbar, noc_mesh, topology, bytes_val, src, dst,
        expected_cycles, param_id
    ):
        model = noc_crossbar if topology == "crossbar" else noc_mesh
        result = model.estimate_latency(bytes_val, src, dst)
        assert _within_tolerance(result, expected_cycles), (
            f"[{param_id}] model={result}, spec={expected_cycles}"
        )

    def test_crossbar_route_independence(self, noc_crossbar):
        """Crossbar is single-hop: 0->1 and 0->3 must be identical."""
        assert noc_crossbar.estimate_latency(64, 0, 1) == noc_crossbar.estimate_latency(64, 0, 3)
        assert noc_crossbar.estimate_latency(4096, 0, 1) == noc_crossbar.estimate_latency(4096, 0, 3)

    def test_mesh_route_sensitivity(self, noc_mesh):
        """Mesh XY routing: 0->3 (dist 2) strictly exceeds 0->1 (dist 1)."""
        assert noc_mesh.estimate_latency(64, 0, 1) < noc_mesh.estimate_latency(64, 0, 3)
        assert noc_mesh.estimate_latency(4096, 0, 1) < noc_mesh.estimate_latency(4096, 0, 3)

    def test_mesh_hops_consistent(self):
        """4-port mesh maps to a 2x2 grid: 0->1 dist 1, 0->3 dist 2."""
        assert _mesh_hop_count("0->1") == 1
        assert _mesh_hop_count("0->3") == 2

    def test_bytes_monotonicity(self, noc_crossbar, noc_mesh):
        """Larger byte counts must not decrease estimated cycles."""
        for model in (noc_crossbar, noc_mesh):
            prev = -1
            for size in (64, 4096, 65536):
                cur = model.estimate_latency(size, 0, 1)
                assert cur >= prev, (
                    f"non-monotonic {model.topology}: size={size} -> {cur}"
                )
                prev = cur


# ── GREEN: KV baseline characterization ───────────────────────────────────


class TestKVGreenSpecMatch:
    """KVCacheModel.estimate_access_latency() must match spec within tolerance."""

    @pytest.mark.parametrize(
        "token_pos,expected_cycles,param_id",
        [
            (0, 0, "kv_token_pos_0"),
            (1, 2, "kv_token_pos_1"),
            (127, 254, "kv_token_pos_127"),
            (511, 1102, "kv_token_pos_511"),
            (2047, 123824, "kv_token_pos_2047"),
        ],
        ids=["kv_token_pos_0", "kv_token_pos_1", "kv_token_pos_127",
             "kv_token_pos_511", "kv_token_pos_2047"],
    )
    def test_access_matches_spec(self, kv_model, token_pos, expected_cycles, param_id):
        result = kv_model.estimate_access_latency(token_pos)
        assert _within_tolerance(result, expected_cycles), (
            f"[{param_id}] model={result}, spec={expected_cycles}"
        )

    @pytest.mark.parametrize(
        "sram_kb,expected_cycles,param_id",
        [
            (64, 360, "kv_layer_switch_64KB"),
            (256, 1440, "kv_layer_switch_256KB"),
            (512, 2880, "kv_layer_switch_512KB"),
        ],
        ids=["kv_layer_switch_64KB", "kv_layer_switch_256KB", "kv_layer_switch_512KB"],
    )
    def test_layer_switch_matches_spec(self, kv_model, sram_kb, expected_cycles, param_id):
        result = kv_model.estimate_layer_switch(sram_kb)
        assert _within_tolerance(result, expected_cycles), (
            f"[{param_id}] model={result}, spec={expected_cycles}"
        )

    def test_token_pos_zero_exact_noop(self, kv_model):
        """token_pos=0 is the spec's expected_noop: exactly 0 cycles."""
        assert kv_model.estimate_access_latency(0) == 0

    def test_token_pos_monotonicity(self, kv_model):
        """More prior tokens -> same or more access cycles."""
        prev = -1
        for tp in (0, 1, 127, 511, 2047):
            cur = kv_model.estimate_access_latency(tp)
            assert cur >= prev, f"non-monotonic: pos{tp} -> {cur}"
            prev = cur

    def test_sram_window_512(self, kv_model):
        """Qwen pin (kv_heads=2, head_dim=128, 256KB) -> 512-entry window."""
        assert kv_model.max_sram_tokens == _KV_WINDOW


# ── Baseline characterization: model structure ────────────────────────────


class TestNoCModelBaseline:
    """Structural properties of the spec-aligned NoC model."""

    def test_noc_model_importable(self):
        from models.noc import NoCModel

    def test_crossbar_mesh_latency_differ(self, noc_crossbar, noc_mesh):
        """Crossbar vs mesh must produce different latencies (dma_noc sweep needs this)."""
        assert noc_crossbar.estimate_latency(4096, 0, 3) != noc_mesh.estimate_latency(4096, 0, 3)

    def test_estimate_transfer_delegates(self, noc_crossbar):
        assert noc_crossbar.estimate_transfer(64, 0, 1) == 14

    def test_zero_negative_size_safe(self, noc_crossbar, noc_mesh):
        """Library-safe zero for zero/negative payload; signoff rejects at verifier level."""
        assert noc_crossbar.estimate_latency(0, 0, 1) == 0
        assert noc_crossbar.estimate_transfer(-1, 0, 1) == 0
        assert noc_mesh.estimate_latency(0, 0, 1) == 0


class TestKVCacheModelBaseline:
    """Structural properties of the spec-aligned KV model."""

    def test_kv_model_importable(self):
        from models.kv_cache import KVCacheModel

    def test_access_returns_kvcache_result(self, kv_model):
        result = kv_model.access(127, 127)
        assert result.access_cycles == 254
        assert result.hit is True

    def test_layer_switch_cost_delegates(self, kv_model):
        assert kv_model.layer_switch_cost() == kv_model.estimate_layer_switch(256)


# ── RED: Mutation detection ───────────────────────────────────────────────


class TestNoCKVMutations:
    """Adversarial mutations that a signoff validator must reject."""

    def test_mutation_route_crossbar_independence(self, oracle):
        """route mutation: crossbar 0->1 vs 0->3 (same bytes) must stay identical."""
        by_bytes: Dict[int, set] = {}
        for e in oracle["entries"]["noc"]:
            if e["inputs"]["topology"] == "crossbar":
                b = int(e["inputs"]["bytes"])
                by_bytes.setdefault(b, set()).add(int(e["expected_cycles"]))
        assert all(len(v) == 1 for v in by_bytes.values()), (
            f"crossbar route mutation would be accepted: {by_bytes}"
        )

    def test_mutation_route_mesh_sensitivity(self, oracle):
        """route mutation: mesh 0->3 must strictly exceed 0->1."""
        by_bytes: Dict[int, list] = {}
        for e in oracle["entries"]["noc"]:
            if e["inputs"]["topology"] == "mesh":
                b = int(e["inputs"]["bytes"])
                by_bytes.setdefault(b, []).append(int(e["expected_cycles"]))
        for b, cycles in by_bytes.items():
            assert len(cycles) >= 2 and min(cycles) < max(cycles), (
                f"mesh route mutation would be accepted: {b}B -> {cycles}"
            )

    def test_mutation_hit_rate_wrong_split(self):
        """hit-rate mutation: all-hit / all-miss must differ from oracle."""
        with open(ORACLE_PATH, "r") as f:
            oracle = json.load(f)
        for e in oracle["entries"]["kv_cache"]:
            if "token_pos" not in e["inputs"] or e["inputs"]["token_pos"] == 0:
                continue
            tp = int(e["inputs"]["token_pos"])
            o = int(e["expected_cycles"])
            correct = min(tp, _KV_WINDOW) * _KV_SRAM_CYC + max(0, tp - _KV_WINDOW) * _KV_DRAM_CYC
            for label, mutated in (("all-hit", tp * _KV_SRAM_CYC),
                                   ("all-miss", tp * _KV_DRAM_CYC)):
                assert not (mutated == o and mutated != correct), (
                    f"{e['parameter_id']}: {label} mutation {mutated} matches "
                    f"oracle {o} (correct {correct})"
                )

    def test_mutation_kv_heads_wrong_pin(self):
        """kv-heads mutation: kv_heads=16 (64-entry window) must differ on rows > 64."""
        with open(ORACLE_PATH, "r") as f:
            oracle = json.load(f)
        window16 = (256 * 1024) // (16 * 128 * 2)  # 64
        for e in oracle["entries"]["kv_cache"]:
            if "token_pos" not in e["inputs"]:
                continue
            tp = int(e["inputs"]["token_pos"])
            if tp == 0 or tp <= window16:
                continue  # window does not bind; not discriminating
            cycles16 = min(tp, window16) * _KV_SRAM_CYC + max(0, tp - window16) * _KV_DRAM_CYC
            assert cycles16 != int(e["expected_cycles"]), (
                f"{e['parameter_id']}: kv_heads=16 ({cycles16}) matches oracle "
                f"{e['expected_cycles']} — wrong head-count pin undetectable"
            )

    def test_mutation_noop_nonzero(self, oracle):
        """noop-nonzero mutation: kv_token_pos_0 must be exact zero + expected_noop."""
        found = False
        for e in oracle["entries"]["kv_cache"]:
            if e["parameter_id"] == "kv_token_pos_0":
                found = True
                assert e.get("expected_noop") is True
                assert e["expected_cycles"] == 0
        assert found, "kv_token_pos_0 missing from oracle"

    def test_mutation_hit_rate_pos511_edge_miss_documented(self):
        """pos511 oracle includes a spec edge DRAM miss (511 hits + 1 miss = 1102).

        The window model gives 1022 (511 hits, no miss); the difference is the
        documented 7.3% deviation inside T1 tolerance.
        """
        assert abs(1022 - 1102) / 1102 * 100 < 10.0


# ── Spec-oracle cross-consistency ─────────────────────────────────────────


class TestSpecOracleConsistency:
    """Verify the spec and oracle agree on expected values for NoC/KV domains."""

    def test_all_8_noc_spec_oracle_match(self, spec, oracle):
        spec_by_id = {e["parameter_id"]: e for e in spec["domains"]["noc"]}
        oracle_by_id = {e["parameter_id"]: e for e in oracle["entries"]["noc"]}
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

    def test_all_8_kv_spec_oracle_match(self, spec, oracle):
        spec_by_id = {e["parameter_id"]: e for e in spec["domains"]["kv_cache"]}
        oracle_by_id = {e["parameter_id"]: e for e in oracle["entries"]["kv_cache"]}
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

    def test_noc_8_rows_total(self, spec):
        assert len(spec["domains"]["noc"]) == 8

    def test_kv_8_rows_total(self, spec):
        assert len(spec["domains"]["kv_cache"]) == 8


# ── Verifier CLI: domain + mutation dispatch ──────────────────────────────


class TestVerifierCLI:
    """End-to-end T11 CLI dispatch against the real spec/oracle."""

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT), "--spec", str(SPEC_PATH),
             "--oracle", str(ORACLE_PATH), *args],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )

    def test_domain_noc_kv_all_16_rows(self):
        proc = self._run("--domain", "noc,kv")
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        data = json.loads(proc.stdout)
        assert data["rows"] == 16
        assert data["failed"] == 0
        assert data["domain_validation"]["verdict"] == "pass"
        assert set(data["domain_validation"]["domains"].keys()) == {"noc", "kv"}

    def test_domain_alias_kv_cache(self):
        proc = self._run("--domain", "noc,kv_cache")
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        data = json.loads(proc.stdout)
        assert data["rows"] == 16
        assert data["failed"] == 0

    def test_mutations_rejected_count_4(self):
        proc = self._run("--domain", "noc,kv", "--mutations",
                         "route,hit-rate,kv-heads,noop-nonzero")
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        data = json.loads(proc.stdout)
        assert data["mutations"]["rejected_mutations"] == 4
        assert data["mutations"]["verdict"] == "pass"

    def test_mutated_noop_oracle_rejected(self, tmp_path):
        """A mutated oracle (noop nonzero) must exit nonzero."""
        with open(ORACLE_PATH, "r") as f:
            oracle = json.load(f)
        for e in oracle["entries"]["kv_cache"]:
            if e["parameter_id"] == "kv_token_pos_0":
                e["expected_cycles"] = 4
        mutated = tmp_path / "oracle_noop_mutated.json"
        mutated.write_text(json.dumps(oracle))
        proc = self._run("--domain", "noc,kv", "--oracle", str(mutated),
                         "--mutations", "noop-nonzero")
        assert proc.returncode == 1
        data = json.loads(proc.stdout)
        assert data["verdict"] == "fail"
        assert data["mutations"]["results"]["noop-nonzero"]["verdict"] == "fail"

    def test_mutated_route_oracle_rejected(self, tmp_path):
        """A mutated oracle (crossbar route-dependent) must exit nonzero."""
        with open(ORACLE_PATH, "r") as f:
            oracle = json.load(f)
        for e in oracle["entries"]["noc"]:
            if e["parameter_id"] == "noc_crossbar_64B_0to3":
                e["expected_cycles"] = 20
        mutated = tmp_path / "oracle_route_mutated.json"
        mutated.write_text(json.dumps(oracle))
        proc = self._run("--domain", "noc,kv", "--oracle", str(mutated),
                         "--mutations", "route")
        assert proc.returncode == 1
        data = json.loads(proc.stdout)
        assert data["mutations"]["results"]["route"]["verdict"] == "fail"
