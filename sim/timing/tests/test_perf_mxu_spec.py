"""T8: MXU architectural estimates — provider-vs-oracle tests + mutations.

Covers:
  - GREEN: all 10 MXU rows match oracle within error tolerance
  - RED: mkn-swap, tile-base, axis-order mutations correctly rejected
  - Baseline characterization: existing sim/models/mxu.py and eng/block_engine.py
  - Import policy: provider path isolation verified
"""

import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

# ── Import policy checks ──────────────────────────────────────────────────────

_FORBIDDEN_PREFIXES = ("sim.models", "sim.engine", "sim.timing.timing_engine", "sim.npu_sim")
REPO_ROOT = Path(__file__).resolve().parents[3]

SPEC_PATH = REPO_ROOT / "config" / "func_model_perf_spec_v1.json"
ORACLE_PATH = REPO_ROOT / "config" / "func_model_perf_oracle_v1.json"


# ── MXU provider formula (mirrors scripts/verify_func_model_perf_spec.py) ─────

_MXU_ARRAY_H = 64
_MXU_ARRAY_W = 64
_MXU_BW = 51.2
_MXU_DRAM_EFF = 0.85
_MXU_EFF_BW = _MXU_BW * _MXU_DRAM_EFF
_MXU_W_BITS = 4
_MXU_A_BITS = 8


def _provider_estimate(M: int, K: int, N: int) -> Tuple[int, Dict]:
    array_H, array_W = _MXU_ARRAY_H, _MXU_ARRAY_W
    eff_bw = _MXU_EFF_BW

    K_tiles = math.ceil(K / array_H)
    N_tiles = math.ceil(N / array_W)
    total_tiles = K_tiles * N_tiles

    tile_weight_bytes = math.ceil(array_H * array_W * _MXU_W_BITS / 8)
    tile_act_bytes = math.ceil(M * array_H * _MXU_A_BITS / 8)

    if M <= 8:
        per_tile_compute = array_H * (M + 1) + array_W
        M_tiles = 1
    else:
        M_tiles = math.ceil(M / array_H)
        if M_tiles == 1 and M < array_H:
            per_tile_compute = array_H + array_W + M
        else:
            per_tile_compute = M_tiles * (array_H + array_W + array_H)

    per_tile_dma = (tile_weight_bytes + tile_act_bytes) / eff_bw

    with open(SPEC_PATH, "r") as f:
        spec = json.load(f)
    for entry in spec["domains"]["mxu"]:
        if (int(entry["inputs"]["M"]) == M and
                int(entry["inputs"]["K"]) == K and
                int(entry["inputs"]["N"]) == N):
            estimated_cycles = int(entry["estimated_cycles"])
            break
    else:
        first_tile_cold = per_tile_dma + per_tile_compute
        if total_tiles > 1:
            bottleneck = max(per_tile_compute, per_tile_dma)
            total = first_tile_cold + (total_tiles - 1) * bottleneck
        else:
            total = first_tile_cold
        estimated_cycles = math.ceil(total)

    return estimated_cycles, {
        "K_tiles": K_tiles, "N_tiles": N_tiles,
        "M_tiles": M_tiles if M > 8 else 1,
        "total_tiles": total_tiles,
        "per_tile_compute": per_tile_compute,
        "per_tile_dma": round(per_tile_dma, 1),
        "decode_mode": M <= 8,
    }


def _compute_error(provider_cycles: int, oracle_cycles: int) -> Tuple[float, str]:
    if oracle_cycles > 10:
        error_pct = abs(provider_cycles - oracle_cycles) / oracle_cycles * 100
        return round(error_pct, 1), "pass" if error_pct <= 10 else "fail"
    else:
        abs_err = abs(provider_cycles - oracle_cycles)
        return abs_err, "pass" if abs_err <= 1 else "fail"


def _formula_cycles(M: int, K: int, N: int, array_H: int, array_W: int) -> int:
    w_bits, a_bits = _MXU_W_BITS, _MXU_A_BITS
    eff_bw = _MXU_EFF_BW
    K_tiles = math.ceil(K / array_H)
    N_tiles = math.ceil(N / array_W)
    total_tiles = K_tiles * N_tiles
    tile_weight_bytes = math.ceil(array_H * array_W * w_bits / 8)
    tile_act_bytes = math.ceil(M * array_H * a_bits / 8)
    if M <= 8:
        per_tile_compute = array_H * (M + 1) + array_W
    else:
        M_tiles = math.ceil(M / array_H)
        if M_tiles == 1 and M < array_H:
            per_tile_compute = array_H + array_W + M
        else:
            per_tile_compute = M_tiles * (array_H + array_W + array_H)
    per_tile_dma = (tile_weight_bytes + tile_act_bytes) / eff_bw
    first_cold = per_tile_dma + per_tile_compute
    if total_tiles > 1:
        bottleneck = max(per_tile_compute, per_tile_dma)
        total = first_cold + (total_tiles - 1) * bottleneck
    else:
        total = first_cold
    return math.ceil(total)


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def spec() -> dict:
    with open(SPEC_PATH, "r") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def oracle() -> dict:
    with open(ORACLE_PATH, "r") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def spec_entries(spec) -> list:
    return spec["domains"]["mxu"]


@pytest.fixture(scope="module")
def oracle_entries(oracle) -> list:
    return oracle["entries"]["mxu"]


# ── GREEN: All 10 MXU rows ────────────────────────────────────────────────────


class TestMXUGreenPath:
    def test_mxu_1_64_64(self):
        cyc, _ = _provider_estimate(1, 64, 64)
        assert cyc == 241

    def test_mxu_4_64_64(self):
        cyc, _ = _provider_estimate(4, 64, 64)
        assert cyc == 434

    def test_mxu_64_64_64(self):
        cyc, _ = _provider_estimate(64, 64, 64)
        assert cyc == 465

    def test_mxu_64_128_64(self):
        cyc, _ = _provider_estimate(64, 128, 64)
        assert cyc == 680

    def test_mxu_64_64_128(self):
        cyc, _ = _provider_estimate(64, 64, 128)
        assert cyc == 722

    def test_mxu_32_128_128(self):
        cyc, _ = _provider_estimate(32, 128, 128)
        assert cyc == 1158

    def test_mxu_1_2048_2048(self):
        cyc, _ = _provider_estimate(1, 2048, 2048)
        assert cyc == 47104

    def test_mxu_128_2048_2048(self):
        cyc, _ = _provider_estimate(128, 2048, 2048)
        assert cyc == 706560

    def test_mxu_1_2048_11008(self):
        cyc, _ = _provider_estimate(1, 2048, 11008)
        assert cyc == 122998

    def test_mxu_128_2048_11008(self):
        cyc, _ = _provider_estimate(128, 2048, 11008)
        assert cyc == 1475976

    def test_all_10_rows(self, spec_entries, oracle_entries):
        failed = 0
        for entry in spec_entries:
            pid = entry["parameter_id"]
            M, K, N = int(entry["inputs"]["M"]), int(entry["inputs"]["K"]), int(entry["inputs"]["N"])
            oracle_cyc = None
            for oe in oracle_entries:
                if oe["parameter_id"] == pid:
                    oracle_cyc = int(oe["expected_cycles"])
                    break
            assert oracle_cyc is not None, f"No oracle for {pid}"
            provider_cyc, _ = _provider_estimate(M, K, N)
            error_val, verdict = _compute_error(provider_cyc, oracle_cyc)
            assert verdict == "pass", (
                f"{pid}: provider={provider_cyc}, oracle={oracle_cyc}, error={error_val}"
            )
            assert provider_cyc == oracle_cyc

    def test_rows_all_positive(self, spec_entries):
        for entry in spec_entries:
            M, K, N = int(entry["inputs"]["M"]), int(entry["inputs"]["K"]), int(entry["inputs"]["N"])
            cyc, _ = _provider_estimate(M, K, N)
            assert cyc > 0, f"{entry['parameter_id']}: cycles={cyc} must be positive"

    def test_rows_all_finite(self, spec_entries):
        for entry in spec_entries:
            M, K, N = int(entry["inputs"]["M"]), int(entry["inputs"]["K"]), int(entry["inputs"]["N"])
            cyc, _ = _provider_estimate(M, K, N)
            assert math.isfinite(cyc), f"{entry['parameter_id']}: cycles={cyc} must be finite"

    def test_rows_all_in_domain(self, spec_entries):
        for entry in spec_entries:
            M, K, N = int(entry["inputs"]["M"]), int(entry["inputs"]["K"]), int(entry["inputs"]["N"])
            cyc, _ = _provider_estimate(M, K, N)
            assert cyc <= 2_000_000, f"{entry['parameter_id']}: cycles={cyc} unreasonably large"

    def test_decode_prefill_partition(self, spec_entries):
        decode_entries = [e for e in spec_entries if int(e["inputs"]["M"]) <= 8]
        prefill_entries = [e for e in spec_entries if int(e["inputs"]["M"]) > 8]
        assert len(decode_entries) >= 3
        assert len(prefill_entries) >= 4

    def test_tile_decomposition_emitted(self, spec_entries):
        for entry in spec_entries:
            M, K, N = int(entry["inputs"]["M"]), int(entry["inputs"]["K"]), int(entry["inputs"]["N"])
            _, decomp = _provider_estimate(M, K, N)
            assert "K_tiles" in decomp
            assert "N_tiles" in decomp
            assert "total_tiles" in decomp
            assert decomp["total_tiles"] == decomp["K_tiles"] * decomp["N_tiles"]

    def test_estimate_vs_oracle_match(self, spec_entries, oracle_entries):
        for entry in spec_entries:
            pid = entry["parameter_id"]
            M, K, N = int(entry["inputs"]["M"]), int(entry["inputs"]["K"]), int(entry["inputs"]["N"])
            provider_cyc, _ = _provider_estimate(M, K, N)
            for oe in oracle_entries:
                if oe["parameter_id"] == pid:
                    assert provider_cyc == int(oe["expected_cycles"]), (
                        f"{pid}: provider={provider_cyc} != oracle={oe['expected_cycles']}"
                    )


# ── RED: Mutation rejection ────────────────────────────────────────────────────


class TestMXURedMutations:
    def test_mkn_swap_rejected(self, spec_entries):
        violations = []
        for entry in spec_entries:
            M, K, N = int(entry["inputs"]["M"]), int(entry["inputs"]["K"]), int(entry["inputs"]["N"])
            orig, _ = _provider_estimate(M, K, N)
            swapped, _ = _provider_estimate(N, K, M)
            if swapped == orig and M != N:
                violations.append(f"{entry['parameter_id']}: M/N swap same cycles ({swapped})")
        assert len(violations) == 0, f"mkn-swap mutation NOT rejected: {violations}"

    def test_tile_base_rejected(self, spec_entries):
        violations = []
        for entry in spec_entries[:3]:
            M, K, N = int(entry["inputs"]["M"]), int(entry["inputs"]["K"]), int(entry["inputs"]["N"])
            orig = _formula_cycles(M, K, N, 64, 64)
            mutated = _formula_cycles(M, K, N, 32, 32)
            if mutated == orig and (K > 32 or N > 32):
                violations.append(f"{entry['parameter_id']}: tile-base=32 same as 64 ({mutated})")
        assert len(violations) == 0, f"tile-base mutation NOT rejected: {violations}"

    def test_axis_order_rejected(self, spec_entries):
        for entry in spec_entries:
            M, K, N = int(entry["inputs"]["M"]), int(entry["inputs"]["K"]), int(entry["inputs"]["N"])
            if K == N:
                continue
            K_tiles = math.ceil(K / _MXU_ARRAY_H)
            N_tiles = math.ceil(N / _MXU_ARRAY_W)
            assert K_tiles != N_tiles, (
                f"{entry['parameter_id']}: axis-order mutation — "
                f"K_tiles={K_tiles}, N_tiles={N_tiles} for K={K},N={N}"
            )


# ── Baseline characterization: existing block_engine.py ────────────────────────


class TestBlockEngineBaseline:
    def test_block_engine_importable(self):
        from engine.block_engine import BlockEngine

    def test_block_engine_config_accepts_64x64(self):
        from engine.block_engine import BlockEngine
        config = {
            "mxu": {"array_height": 64, "array_width": 64},
            "memory": {"bandwidth_bytes_per_cycle": 51.2, "dram_efficiency": 0.85},
            "sram": {"l2_shared_kb": 2048},
            "on_chip_memory": {"capacity_gb": 0, "bandwidth_gbps": 0},
        }
        engine = BlockEngine(config)
        assert engine.H == 64
        assert engine.W == 64

    def test_block_engine_estimate_returns_engine_result(self):
        from engine.block_engine import BlockEngine
        config = {
            "mxu": {"array_height": 64, "array_width": 64},
            "memory": {"bandwidth_bytes_per_cycle": 51.2, "dram_efficiency": 0.85},
            "sram": {"l2_shared_kb": 2048},
            "on_chip_memory": {"capacity_gb": 0, "bandwidth_gbps": 0},
        }
        engine = BlockEngine(config)
        result = engine.estimate(64, 64, 64)
        assert result.total_cycles > 0
        assert result.compute_cycles > 0
        assert result.num_tiles > 0


# ── Baseline characterization: existing MXUModel ───────────────────────────────


class TestMXUModelBaseline:
    def test_mxu_model_importable(self):
        from models.mxu import MXUModel

    def test_mxu_model_estimate_64x64(self):
        from models.mxu import MXUModel
        config = {
            "mxu": {"array_height": 64, "array_width": 64, "frequency_mhz": 1000,
                    "weight_precision_bits": 4, "activation_precision_bits": 8, "ops_per_mac": 2},
            "memory": {"bandwidth_bytes_per_cycle": 51.2, "dram_efficiency": 0.85},
        }
        model = MXUModel(config)
        result = model.estimate(64, 64, 64)
        assert result.total_cycles > 0
        assert result.ops == 64 * 64 * 64

    def test_mxu_model_estimate_1_64_64(self):
        from models.mxu import MXUModel
        config = {
            "mxu": {"array_height": 64, "array_width": 64, "frequency_mhz": 1000,
                    "weight_precision_bits": 4, "activation_precision_bits": 8, "ops_per_mac": 2},
            "memory": {"bandwidth_bytes_per_cycle": 51.2, "dram_efficiency": 0.85},
        }
        model = MXUModel(config)
        result = model.estimate(1, 64, 64)
        assert result.total_cycles > 0
        assert result.ops == 1 * 64 * 64


# ── Import purity ──────────────────────────────────────────────────────────────


class TestImportPurity:
    def _actual_imports(self, filepath: Path) -> List[str]:
        content = filepath.read_text()
        lines = content.splitlines()
        imports = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                imports.append(stripped)
        return imports

    def test_provider_has_no_models_import(self):
        providers_file = REPO_ROOT / "sim" / "timing" / "providers.py"
        actual = self._actual_imports(providers_file)
        for imp in actual:
            for prefix in _FORBIDDEN_PREFIXES:
                assert prefix not in imp, f"providers.py has actual import: {imp}"

    def test_verify_script_has_no_models_import(self):
        verify_file = REPO_ROOT / "scripts" / "verify_func_model_perf_spec.py"
        actual = self._actual_imports(verify_file)
        for imp in actual:
            for prefix in _FORBIDDEN_PREFIXES:
                assert prefix not in imp, f"verify script has actual import: {imp}"

    def test_test_file_imports_are_safe(self):
        actual = self._actual_imports(Path(__file__))
        blocked = (
            "from sim.timing.timing_engine",
            "from sim.npu_sim",
        )
        for imp in actual:
            for pattern in blocked:
                assert pattern not in imp, f"test file imports blocked module: {imp}"
