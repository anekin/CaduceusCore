"""T9: SFU and Vector Spec Alignment Tests — provider-vs-oracle + mutations.

Tests:
- Baseline characterization of SFUModel and VectorModel (before/after changes).
- Provider-vs-oracle formula gate: 24 SFU + 30 Vector rows.
- RED mutations: unknown-default, off-by-one, wrong-block-size rejected.
- Malformed inputs: unknown op, dim<=0 -> typed error.
"""
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SIM_DIR = REPO_ROOT / "sim"
ORACLE_PATH = REPO_ROOT / "config" / "func_model_perf_oracle_v1.json"
SPEC_PATH = REPO_ROOT / "config" / "func_model_perf_spec_v1.json"
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_func_model_perf_spec.py"


def _run_verify(*args: str) -> "subprocess.CompletedProcess[str]":
    cmd = [sys.executable, str(VERIFY_SCRIPT)] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def _parse_verdict(output: str) -> dict:
    return json.loads(output.strip())


# ── Baseline Characterization ──────────────────────────────────────────

class TestSFUModelBaseline:
    """Baseline characterization of SFUModel against spec parameters."""

    def test_sfu_estimate_softmax_128(self):
        from models.sfu import SFUModel
        m = SFUModel({})
        cycles = m.estimate("softmax", 128)
        assert cycles == 227, f"Expected 227, got {cycles}"

    def test_sfu_estimate_layernorm_2048(self):
        from models.sfu import SFUModel
        m = SFUModel({})
        cycles = m.estimate("layernorm", 2048)
        assert cycles == 3360, f"Expected 3360, got {cycles}"

    def test_sfu_estimate_gelu_11008(self):
        from models.sfu import SFUModel
        m = SFUModel({})
        cycles = m.estimate("gelu", 11008)
        assert cycles == 6106, f"Expected 6106, got {cycles}"

    def test_sfu_estimate_rope_16(self):
        from models.sfu import SFUModel
        m = SFUModel({})
        cycles = m.estimate("rope", 16)
        assert cycles == 82, f"Expected 82, got {cycles}"

    def test_sfu_estimate_silu_2048(self):
        from models.sfu import SFUModel
        m = SFUModel({})
        cycles = m.estimate("silu", 2048)
        assert cycles == 1152, f"Expected 1152, got {cycles}"

    def test_sfu_estimate_rmsnorm_128(self):
        from models.sfu import SFUModel
        m = SFUModel({})
        cycles = m.estimate("rmsnorm", 128)
        assert cycles == 150, f"Expected 150, got {cycles}"

    def test_sfu_norm_ops_scaled_for_small_dim(self):
        from models.sfu import SFUModel
        m = SFUModel({})
        assert m.estimate("softmax", 16) == 57
        assert m.estimate("layernorm", 16) == 53
        assert m.estimate("rmsnorm", 16) == 38

    def test_sfu_non_norm_ops_unscaled(self):
        from models.sfu import SFUModel
        m = SFUModel({})
        assert m.estimate("gelu", 16) == 71
        assert m.estimate("silu", 16) == 72
        assert m.estimate("rope", 16) == 82

    def test_sfu_block_boundary_ceil(self):
        from models.sfu import SFUModel
        m = SFUModel({})
        assert m.estimate("gelu", 129) == 2 * 71  # ceil(129/128)=2
        assert m.estimate("gelu", 128) == 71       # ceil(128/128)=1
        assert m.estimate("gelu", 1) == 71         # ceil(1/128)=1

    def test_sfu_unknown_op_raises(self):
        from models.sfu import SFUModel, SFUUnsupportedOpError
        m = SFUModel({})
        with pytest.raises(SFUUnsupportedOpError):
            m.estimate("unknown_op", 128)

    def test_sfu_dim_zero_raises(self):
        from models.sfu import SFUModel, SFUInvalidDimError
        m = SFUModel({})
        with pytest.raises(SFUInvalidDimError):
            m.estimate("gelu", 0)

    def test_sfu_dim_negative_raises(self):
        from models.sfu import SFUModel, SFUInvalidDimError
        m = SFUModel({})
        with pytest.raises(SFUInvalidDimError):
            m.estimate("gelu", -5)

    def test_sfu_latency_map_excludes_non_spec_ops(self):
        from models.sfu import SFUModel, SFU_PIPELINE
        m = SFUModel({})
        assert set(m.latency_map.keys()) == set(SFU_PIPELINE.keys())
        assert "relu" not in m.latency_map
        assert "h_swish" not in m.latency_map
        assert "hard_sigmoid" not in m.latency_map

    def test_sfu_softmax_decomposed_returns_positive(self):
        from models.sfu import SFUModel
        m = SFUModel({})
        dec = m.estimate_softmax_decomposed(2048)
        assert dec["exp"] > 0
        assert dec["div"] > 0

    def test_sfu_estimate_all_layer_returns_total(self):
        from models.sfu import SFUModel
        m = SFUModel({})
        total, breakdown = m.estimate_all_layer(2048, 11008)
        assert total > 0
        assert len(breakdown) >= 5


class TestVectorModelBaseline:
    """Baseline characterization of VectorModel against spec parameters."""

    def test_vector_estimate_add_256(self):
        from models.vector import VectorModel
        m = VectorModel({})
        cycles = m.estimate("add", 256)
        assert cycles == 10, f"Expected 10, got {cycles}"

    def test_vector_estimate_mul_2048(self):
        from models.vector import VectorModel
        m = VectorModel({})
        cycles = m.estimate("mul", 2048)
        assert cycles == 80, f"Expected 80, got {cycles}"

    def test_vector_estimate_max_128(self):
        from models.vector import VectorModel
        m = VectorModel({})
        cycles = m.estimate("max", 128)
        assert cycles == 12, f"Expected 12, got {cycles}"

    def test_vector_estimate_sum_11008(self):
        from models.vector import VectorModel
        m = VectorModel({})
        cycles = m.estimate("sum", 11008)
        assert cycles == 1032, f"Expected 1032, got {cycles}"

    def test_vector_estimate_conv_1(self):
        from models.vector import VectorModel
        m = VectorModel({})
        cycles = m.estimate("conv", 1)
        assert cycles == 260, f"Expected 260, got {cycles}"

    def test_vector_estimate_resid_128(self):
        from models.vector import VectorModel
        m = VectorModel({})
        cycles = m.estimate("resid", 128)
        assert cycles == 5, f"Expected 5, got {cycles}"

    def test_vector_block_boundary_ceil(self):
        from models.vector import VectorModel
        m = VectorModel({})
        assert m.estimate("add", 129) == 2 * 5   # ceil(129/128)=2
        assert m.estimate("mul", 128) == 5        # ceil(128/128)=1
        assert m.estimate("max", 1) == 12         # ceil(1/128)=1, reduce_tree latency=12

    def test_vector_unknown_op_raises(self):
        from models.vector import VectorModel, VectorUnsupportedOpError
        m = VectorModel({})
        with pytest.raises(VectorUnsupportedOpError):
            m.estimate("scale", 128)

    def test_vector_dim_zero_raises(self):
        from models.vector import VectorModel, VectorInvalidDimError
        m = VectorModel({})
        with pytest.raises(VectorInvalidDimError):
            m.estimate("add", 0)

    def test_vector_dim_negative_raises(self):
        from models.vector import VectorModel, VectorInvalidDimError
        m = VectorModel({})
        with pytest.raises(VectorInvalidDimError):
            m.estimate("add", -1)

    def test_vector_op_latency_excludes_non_spec_ops(self):
        from models.vector import VectorModel, VECTOR_OPS
        m = VectorModel({})
        assert set(m.op_latency.keys()) == set(VECTOR_OPS.keys())
        assert "scale" not in m.op_latency
        assert "bias" not in m.op_latency
        assert "relu" not in m.op_latency
        assert "mask" not in m.op_latency
        assert "reduce" not in m.op_latency
        assert "conv_f16_i32" not in m.op_latency

    def test_vector_softmax_parts(self):
        from models.vector import VectorModel
        m = VectorModel({})
        parts = m.estimate_softmax_vector_parts(2048)
        assert parts["max_reduce"] > 0
        assert parts["scale_sub"] > 0
        assert parts["sum_reduce"] > 0

    def test_vector_residual_add(self):
        from models.vector import VectorModel
        m = VectorModel({})
        cycles = m.estimate_residual_add(2048)
        assert cycles == 80  # 16 batches * 5


# ── Provider-vs-Oracle GREEN ───────────────────────────────────────────

class TestSFUVectorOracleGREEN:
    """Verify provider estimates match oracle for all 54 SFU+Vector rows."""

    def test_verify_green_rows(self):
        result = _run_verify(
            "--oracle", str(ORACLE_PATH),
            "--spec", str(SPEC_PATH),
            "--domain", "sfu,vector",
        )
        assert result.returncode == 0, f"GREEN failed: {result.stderr}"
        verdict = _parse_verdict(result.stdout)
        assert verdict["rows"] == 54
        assert verdict["failed"] == 0

    def test_verify_green_sfu_only(self):
        result = _run_verify(
            "--oracle", str(ORACLE_PATH),
            "--spec", str(SPEC_PATH),
            "--domain", "sfu",
        )
        assert result.returncode == 0
        verdict = _parse_verdict(result.stdout)
        assert verdict["rows"] == 24
        assert verdict["failed"] == 0

    def test_verify_green_vector_only(self):
        result = _run_verify(
            "--oracle", str(ORACLE_PATH),
            "--spec", str(SPEC_PATH),
            "--domain", "vector",
        )
        assert result.returncode == 0
        verdict = _parse_verdict(result.stdout)
        assert verdict["rows"] == 30
        assert verdict["failed"] == 0


# ── RED Mutations ──────────────────────────────────────────────────────

class TestSFUVectorREDMutations:
    """Mutations must be correctly detected and rejected."""

    def test_unknown_default_mutation_rejected(self):
        result = _run_verify(
            "--oracle", str(ORACLE_PATH),
            "--spec", str(SPEC_PATH),
            "--domain", "sfu,vector",
            "--mutations", "unknown-default",
        )
        assert result.returncode == 0, f"Mutation check failed: {result.stderr}"
        verdict = _parse_verdict(result.stdout)
        m = verdict["mutations"]
        assert m["rejected_mutations"] == 1

    def test_off_by_one_mutation_rejected(self):
        result = _run_verify(
            "--oracle", str(ORACLE_PATH),
            "--spec", str(SPEC_PATH),
            "--domain", "sfu,vector",
            "--mutations", "off-by-one",
        )
        assert result.returncode == 0
        verdict = _parse_verdict(result.stdout)
        m = verdict["mutations"]
        assert m["rejected_mutations"] == 1

    def test_wrong_block_size_mutation_rejected(self):
        result = _run_verify(
            "--oracle", str(ORACLE_PATH),
            "--spec", str(SPEC_PATH),
            "--domain", "sfu,vector",
            "--mutations", "wrong-block-size",
        )
        assert result.returncode == 0
        verdict = _parse_verdict(result.stdout)
        m = verdict["mutations"]
        assert m["rejected_mutations"] == 1

    def test_all_three_mutations_rejected(self):
        result = _run_verify(
            "--oracle", str(ORACLE_PATH),
            "--spec", str(SPEC_PATH),
            "--domain", "sfu,vector",
            "--mutations", "unknown-default,off-by-one,wrong-block-size",
        )
        assert result.returncode == 0
        verdict = _parse_verdict(result.stdout)
        m = verdict["mutations"]
        assert m["rejected_mutations"] == 3
        assert m["checked"] == ["unknown-default", "off-by-one", "wrong-block-size"]


# ── Malformed Input Tests ─────────────────────────────────────────────

class TestMalformedInput:
    """Unsupported ops and dim<=0 must fail with typed errors."""

    def test_sfu_unknown_op(self):
        from models.sfu import SFUModel, SFUUnsupportedOpError
        m = SFUModel({})
        with pytest.raises(SFUUnsupportedOpError, match="not supported"):
            m.estimate("nonexistent_op", 64)

    def test_sfu_zero_elements(self):
        from models.sfu import SFUModel, SFUInvalidDimError
        m = SFUModel({})
        with pytest.raises(SFUInvalidDimError, match="must be > 0"):
            m.estimate("gelu", 0)

    def test_sfu_negative_elements(self):
        from models.sfu import SFUModel, SFUInvalidDimError
        m = SFUModel({})
        with pytest.raises(SFUInvalidDimError, match="must be > 0"):
            m.estimate("layernorm", -10)

    def test_vector_unknown_op(self):
        from models.vector import VectorModel, VectorUnsupportedOpError
        m = VectorModel({})
        with pytest.raises(VectorUnsupportedOpError, match="not supported"):
            m.estimate("scale", 128)

    def test_vector_zero_dim(self):
        from models.vector import VectorModel, VectorInvalidDimError
        m = VectorModel({})
        with pytest.raises(VectorInvalidDimError, match="must be > 0"):
            m.estimate("add", 0)

    def test_vector_negative_dim(self):
        from models.vector import VectorModel, VectorInvalidDimError
        m = VectorModel({})
        with pytest.raises(VectorInvalidDimError, match="must be > 0"):
            m.estimate("mul", -100)


# ── Misleading Success Output Tests ────────────────────────────────────

class TestMisleadingSuccess:
    """Structured JSON verdict only — no misleading success output."""

    def test_verify_output_is_json(self):
        result = _run_verify(
            "--oracle", str(ORACLE_PATH),
            "--spec", str(SPEC_PATH),
            "--domain", "sfu,vector",
        )
        verdict = _parse_verdict(result.stdout)
        assert isinstance(verdict, dict)
        assert "rows" in verdict
        assert "failed" in verdict
        assert "verdict" in verdict

    def test_verify_output_has_domain_validation(self):
        result = _run_verify(
            "--oracle", str(ORACLE_PATH),
            "--spec", str(SPEC_PATH),
            "--domain", "sfu,vector",
        )
        verdict = _parse_verdict(result.stdout)
        dv = verdict["domain_validation"]
        assert "sfu" in dv["domains"]
        assert "vector" in dv["domains"]
        assert dv["domains"]["sfu"]["verdict"] == "pass"
        assert dv["domains"]["vector"]["verdict"] == "pass"

    def test_evidence_json_written(self):
        evidence_path = REPO_ROOT / ".omo" / "evidence" / "task-9-sfu-vector-spec.json"
        assert evidence_path.exists(), "Evidence file not written"
        with open(evidence_path, "r") as f:
            data = json.load(f)
        assert data["rows"] == 54
        assert data["failed"] == 0
        assert data["verdict"] == "pass"
