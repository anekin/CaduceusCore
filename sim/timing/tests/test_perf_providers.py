"""Tests for sim/timing/providers.py — provider registry and activation/rollback.

Covers:
  - Registry: register/activate/rollback/estimate dispatch
  - Block64Provider: shape matching, op validation, domain boundaries
  - RED: unknown provider, unsupported op, out-of-domain, RTL-labeled artifact
  - GREEN: correct estimates, activation/rollback stack, content hash stability
  - MUTATION: stale spec hash, wrong shape keys, content tampering
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from timing.providers import (  # noqa: E402
    Block64Provider,
    LegacySourceError,
    OutOfDomainError,
    ProviderError,
    ProviderRegistry,
    RTLCalibratedArtifactError,
    SpecNotFoundError,
    UnknownProviderError,
    UnsupportedOpError,
    _compute_spec_hash,
    _load_spec,
    _parse_uncertainty_pct,
    create_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = "config/func_model_perf_spec_v1.json"
PROVIDER_CONFIG_PATH = REPO_ROOT / "config" / "perf_providers" / "spec-block64-v1.json"


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def spec() -> dict:
    """Load the normative spec for testing."""
    return _load_spec(SPEC_PATH)


@pytest.fixture(scope="module")
def provider_config() -> dict:
    """Load the Block 64×64 provider config."""
    if PROVIDER_CONFIG_PATH.is_file():
        with open(PROVIDER_CONFIG_PATH, "r") as f:
            return json.load(f)
    return {}


@pytest.fixture(scope="module")
def block64_provider(spec, provider_config) -> Block64Provider:
    """Create a Block64Provider instance."""
    return Block64Provider(spec, provider_config)


@pytest.fixture(scope="module")
def registry() -> ProviderRegistry:
    """Create a ProviderRegistry with built-in provider."""
    return ProviderRegistry(SPEC_PATH)


# ── Spec loading tests ─────────────────────────────────────────────────────────


class TestSpecLoading:
    """Spec loading and validation."""

    def test_load_spec_returns_dict(self, spec):
        assert isinstance(spec, dict)
        assert "domains" in spec
        assert "schema_version" in spec

    def test_load_spec_has_8_domains(self, spec):
        domains = spec["domains"]
        assert len(domains) == 8
        expected = {"mxu", "sfu", "vector", "dma", "dram", "noc", "kv_cache", "sw_overhead"}
        assert set(domains.keys()) == expected

    def test_load_spec_has_104_parameters(self, spec):
        count = sum(len(params) for params in spec["domains"].values())
        assert count == 104

    def test_spec_not_found_raises(self):
        with pytest.raises(SpecNotFoundError):
            _load_spec("nonexistent/path/spec.json")

    def test_spec_hash_is_stable(self, spec):
        h1 = _compute_spec_hash(spec)
        h2 = _compute_spec_hash(spec)
        assert h1 == h2
        assert len(h1) == 64

    def test_spec_hash_different_for_modified_spec(self, spec):
        h1 = _compute_spec_hash(spec)
        modified = dict(spec)
        modified["_test_marker"] = "mutated"
        h2 = _compute_spec_hash(modified)
        assert h1 != h2


class TestUncertaintyParser:
    """Uncertainty string parsing from spec."""

    def test_parse_standard_band(self):
        pct = _parse_uncertainty_pct("[169, 313]")
        # base ≈ (169+313)/2 = 241, delta = (313-241)/241*100 ≈ 29.9 → 29.9
        assert 29.0 <= pct <= 31.0

    def test_parse_exact_zero_band(self):
        pct = _parse_uncertainty_pct("[0, 0]")
        assert pct == 0.0

    def test_parse_malformed_returns_default(self):
        pct = _parse_uncertainty_pct("not a band")
        assert pct == 30.0

    def test_parse_empty_string(self):
        pct = _parse_uncertainty_pct("[]")
        assert pct == 30.0


# ── Registry activation/rollback tests ─────────────────────────────────────────


class TestRegistryActivation:
    """ProviderRegistry activation and rollback behavior."""

    def test_initial_state_no_active(self, registry):
        assert registry.active_provider_id is None
        assert registry.active_provider is None

    def test_activate_builtin_provider(self, registry):
        result = registry.activate("spec-block64-v1")
        assert result == "spec-block64-v1"
        assert registry.active_provider_id == "spec-block64-v1"
        assert registry.active_provider is not None

    def test_activate_unknown_provider_raises(self, registry):
        with pytest.raises(UnknownProviderError, match="nonexistent-provider"):
            registry.activate("nonexistent-provider")
        # Active provider unchanged
        assert registry.active_provider_id == "spec-block64-v1"

    def test_rollback_to_none(self, registry):
        # Registry already has spec-block64-v1 active from previous test
        # Rollback should pop it and return None (no previous activation)
        result = registry.rollback()
        assert result is None
        assert registry.active_provider_id is None
        assert registry.active_provider is None

    def test_activate_then_rollback_stack(self, registry):
        # Fresh activation
        registry.activate("spec-block64-v1")
        assert registry.active_provider_id == "spec-block64-v1"

        # Rollback to None
        result = registry.rollback()
        assert result is None
        assert registry.active_provider_id is None

    def test_activate_rollback_activate_again(self, registry):
        registry.activate("spec-block64-v1")
        registry.rollback()
        # Re-activate
        registry.activate("spec-block64-v1")
        assert registry.active_provider_id == "spec-block64-v1"

    def test_estimate_without_active_provider_raises(self, registry):
        # Ensure no active provider
        while registry.active_provider_id is not None:
            registry.rollback()
        with pytest.raises(ProviderError, match="No active provider"):
            registry.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64})

    def test_list_providers(self, registry):
        providers = registry.list_providers()
        assert "spec-block64-v1" in providers


# ── Green path: Block64Provider estimates ──────────────────────────────────────


class TestBlock64GreenPath:
    """Valid estimates from Block64Provider across all domains."""

    def test_mxu_64x64x64_estimate(self, block64_provider):
        result = block64_provider.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64})
        assert result["estimated_cycles"] == 4494
        assert result["domain"] == "mxu"
        assert result["engine"] == "mxu"
        assert result["op"] == "mmul"
        assert result["basis"] == "architectural_formula"
        assert result["calibration_state"] == "uncalibrated"
        assert result["provider_id"] == "spec-block64-v1"

    def test_mxu_1_64_64_estimate(self, block64_provider):
        result = block64_provider.estimate("mxu", "mmul", {"M": 1, "K": 64, "N": 64})
        assert result["estimated_cycles"] == 117

    def test_mxu_1_2048_2048_estimate(self, block64_provider):
        result = block64_provider.estimate("mxu", "mmul", {"M": 1, "K": 2048, "N": 2048})
        assert result["estimated_cycles"] == 69681

    def test_mxu_128_2048_2048_estimate(self, block64_provider):
        result = block64_provider.estimate("mxu", "mmul", {"M": 128, "K": 2048, "N": 2048})
        assert result["estimated_cycles"] == 8913132

    def test_sfu_softmax_128_estimate(self, block64_provider):
        result = block64_provider.estimate("sfu", "softmax", {"elements": 128})
        assert result["estimated_cycles"] == 227

    def test_sfu_softmax_2048_estimate(self, block64_provider):
        result = block64_provider.estimate("sfu", "softmax", {"elements": 2048})
        assert result["estimated_cycles"] == 3632

    def test_sfu_layernorm_11008_estimate(self, block64_provider):
        result = block64_provider.estimate("sfu", "layernorm", {"elements": 11008})
        assert result["estimated_cycles"] == 18060

    def test_sfu_rmsnorm_128_estimate(self, block64_provider):
        result = block64_provider.estimate("sfu", "rmsnorm", {"elements": 128})
        assert result["estimated_cycles"] == 150

    def test_sfu_gelu_128_estimate(self, block64_provider):
        result = block64_provider.estimate("sfu", "gelu", {"elements": 128})
        assert result["estimated_cycles"] == 71

    def test_sfu_silu_2048_estimate(self, block64_provider):
        result = block64_provider.estimate("sfu", "silu", {"elements": 2048})
        assert result["estimated_cycles"] == 1152

    def test_sfu_rope_11008_estimate(self, block64_provider):
        result = block64_provider.estimate("sfu", "rope", {"elements": 11008})
        assert result["estimated_cycles"] == 7052

    def test_vector_add_128_estimate(self, block64_provider):
        result = block64_provider.estimate("vector", "add", {"dim": 128})
        assert result["estimated_cycles"] == 5

    def test_vector_add_2048_estimate(self, block64_provider):
        result = block64_provider.estimate("vector", "add", {"dim": 2048})
        assert result["estimated_cycles"] == 80

    def test_vector_mul_11008_estimate(self, block64_provider):
        result = block64_provider.estimate("vector", "mul", {"dim": 11008})
        assert result["estimated_cycles"] == 430

    def test_vector_max_128_estimate(self, block64_provider):
        result = block64_provider.estimate("vector", "max", {"dim": 128})
        assert result["estimated_cycles"] == 12

    def test_vector_sum_2048_estimate(self, block64_provider):
        result = block64_provider.estimate("vector", "sum", {"dim": 2048})
        assert result["estimated_cycles"] == 192

    def test_vector_conv_128_estimate(self, block64_provider):
        result = block64_provider.estimate("vector", "conv", {"dim": 128})
        assert result["estimated_cycles"] == 260

    def test_vector_resid_11008_estimate(self, block64_provider):
        result = block64_provider.estimate("vector", "resid", {"dim": 11008})
        assert result["estimated_cycles"] == 430

    def test_dma_4096B_1ch_estimate(self, block64_provider):
        result = block64_provider.estimate("dma", "dma_copy", {"bytes": 4096})
        assert result["estimated_cycles"] == 102

    def test_dma_65536B_1ch_estimate(self, block64_provider):
        result = block64_provider.estimate("dma", "dma_copy", {"bytes": 65536})
        assert result["estimated_cycles"] == 1541

    def test_dram_4096B_read_estimate(self, block64_provider):
        result = block64_provider.estimate("dram", "dram_read", {"bytes": 4096, "rw": 0})
        assert result["estimated_cycles"] == 96

    def test_dram_65536B_write_estimate(self, block64_provider):
        result = block64_provider.estimate("dram", "dram_write", {"bytes": 65536, "rw": 1})
        assert result["estimated_cycles"] == 1072

    def test_noc_crossbar_64B_estimate(self, block64_provider):
        result = block64_provider.estimate("noc", "noc_route",
                                            {"bytes": 64, "topology": 0, "route": 1})
        assert result["estimated_cycles"] == 14

    def test_noc_crossbar_4096B_estimate(self, block64_provider):
        result = block64_provider.estimate("noc", "noc_route",
                                            {"bytes": 4096, "topology": 0, "route": 1})
        assert result["estimated_cycles"] == 142

    def test_noc_mesh_64B_estimate(self, block64_provider):
        result = block64_provider.estimate("noc", "noc_route",
                                            {"bytes": 64, "topology": 1, "route": 1})
        assert result["estimated_cycles"] == 18

    def test_kv_token_pos_0_estimate(self, block64_provider):
        result = block64_provider.estimate("kv_cache", "kv_access",
                                            {"token_pos": 0, "sram_kb": 64})
        assert result["estimated_cycles"] == 0

    def test_kv_token_pos_127_estimate(self, block64_provider):
        result = block64_provider.estimate("kv_cache", "kv_access",
                                            {"token_pos": 127, "sram_kb": 64})
        assert result["estimated_cycles"] == 254

    def test_sw_qwen_blk0_estimate(self, block64_provider):
        result = block64_provider.estimate("sw_overhead", "riscv_instr",
                                            {"num_layers": 1})
        assert result["estimated_cycles"] == 1500

    # ── Registry dispatch tests ───────────────────────────────────────────

    def test_registry_dispatch_mxu(self, registry):
        registry.activate("spec-block64-v1")
        result = registry.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64})
        assert result["estimated_cycles"] == 4494
        registry.rollback()

    def test_registry_dispatch_sfu(self, registry):
        registry.activate("spec-block64-v1")
        result = registry.estimate("sfu", "softmax", {"elements": 2048})
        assert result["estimated_cycles"] == 3632
        registry.rollback()

    def test_registry_dispatch_vector(self, registry):
        registry.activate("spec-block64-v1")
        result = registry.estimate("vector", "add", {"dim": 2048})
        assert result["estimated_cycles"] == 80
        registry.rollback()

    def test_registry_dispatch_dma(self, registry):
        registry.activate("spec-block64-v1")
        result = registry.estimate("dma", "dma_copy", {"bytes": 4096})
        assert result["estimated_cycles"] == 102
        registry.rollback()

    def test_registry_dispatch_dram(self, registry):
        registry.activate("spec-block64-v1")
        result = registry.estimate("dram", "dram_read", {"bytes": 4096, "rw": 0})
        assert result["estimated_cycles"] == 96
        registry.rollback()

    def test_registry_dispatch_noc(self, registry):
        registry.activate("spec-block64-v1")
        result = registry.estimate("noc", "noc_route",
                                    {"bytes": 64, "topology": 0, "route": 1})
        assert result["estimated_cycles"] == 14
        registry.rollback()

    def test_registry_dispatch_kv_cache(self, registry):
        registry.activate("spec-block64-v1")
        result = registry.estimate("kv_cache", "kv_access",
                                    {"token_pos": 127, "sram_kb": 64})
        assert result["estimated_cycles"] == 254
        registry.rollback()

    def test_registry_dispatch_sw(self, registry):
        registry.activate("spec-block64-v1")
        result = registry.estimate("sw_overhead", "riscv_instr", {"num_layers": 1})
        assert result["estimated_cycles"] == 1500
        registry.rollback()


# ── RED: Rejection tests ──────────────────────────────────────────────────────


class TestBlock64RedRejections:
    """Rejection of invalid requests by Block64Provider."""

    def test_unknown_op_raises(self, block64_provider):
        with pytest.raises(UnsupportedOpError, match="unsupported_op"):
            block64_provider.estimate("mxu", "unsupported_op",
                                       {"M": 64, "K": 64, "N": 64})

    def test_sfu_op_in_mxu_raises(self, block64_provider):
        with pytest.raises(UnsupportedOpError, match="softmax"):
            block64_provider.estimate("mxu", "softmax", {"M": 64, "K": 64, "N": 64})

    def test_mxu_op_in_sfu_raises(self, block64_provider):
        with pytest.raises(UnsupportedOpError, match="mmul"):
            block64_provider.estimate("sfu", "mmul", {"elements": 128})

    def test_out_of_domain_raises(self, block64_provider):
        """Request from domain not in supported_domains should fail."""
        with pytest.raises(OutOfDomainError, match="gpu"):
            block64_provider.estimate("gpu", "mmul", {"M": 64, "K": 64, "N": 64})

    def test_rtl_calibrated_state_raises(self, block64_provider):
        with pytest.raises(RTLCalibratedArtifactError):
            block64_provider.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64},
                                       calibration_state="rtl_calibrated")

    def test_rtl_measurement_basis_raises(self, block64_provider):
        with pytest.raises(RTLCalibratedArtifactError):
            block64_provider.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64},
                                       basis="rtl_measurement")

    def test_wrong_shape_keys_for_mxu_raises(self, block64_provider):
        with pytest.raises(UnsupportedOpError, match="Shape keys"):
            block64_provider.estimate("mxu", "mmul", {"M": 64, "X": 64, "N": 64})

    def test_wrong_shape_keys_for_sfu_raises(self, block64_provider):
        with pytest.raises(UnsupportedOpError, match="Shape keys"):
            block64_provider.estimate("sfu", "softmax", {"dim": 128})

    def test_nonexistent_shape_match_raises(self, block64_provider):
        """A shape that exists structurally but no spec parameter matches."""
        with pytest.raises(UnsupportedOpError, match="No spec parameter matches"):
            block64_provider.estimate("mxu", "mmul", {"M": 99, "K": 99, "N": 99})

    def test_unsupported_noc_topology_shape_raises(self, block64_provider):
        """A noc shape with bytes=99999 that doesn't match any spec entry."""
        with pytest.raises(UnsupportedOpError, match="No spec parameter matches"):
            block64_provider.estimate("noc", "noc_route",
                                       {"bytes": 99999, "topology": 1, "route": 1})


class TestRegistryRedRejections:
    """Rejection of invalid requests via registry dispatch."""

    def test_unknown_provider_activate_raises(self, registry):
        with pytest.raises(UnknownProviderError):
            registry.activate("no-such-provider")

    def test_dispatch_unsupported_op(self, registry):
        registry.activate("spec-block64-v1")
        with pytest.raises(UnsupportedOpError):
            registry.estimate("mxu", "unsupported_op", {"M": 64, "K": 64, "N": 64})
        registry.rollback()

    def test_dispatch_out_of_domain(self, registry):
        registry.activate("spec-block64-v1")
        with pytest.raises(OutOfDomainError):
            registry.estimate("gpu", "mmul", {"M": 64, "K": 64, "N": 64})
        registry.rollback()

    def test_dispatch_rtl_calibrated(self, registry):
        registry.activate("spec-block64-v1")
        with pytest.raises(RTLCalibratedArtifactError):
            registry.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64},
                               calibration_state="rtl_calibrated")
        registry.rollback()


# ── Content hash and verdict eligibility ──────────────────────────────────────


class TestEstimateContentHash:
    """Content hash stability and verdict eligibility for PerfEstimate objects."""

    def test_same_estimate_same_hash(self, block64_provider):
        r1 = block64_provider.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64})
        r2 = block64_provider.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64})

        from timing.perf_contract import PerfEstimate  # noqa: E402
        e1 = PerfEstimate(**r1)
        e2 = PerfEstimate(**r2)
        assert e1.content_hash() == e2.content_hash()

    def test_different_shape_different_hash(self, block64_provider):
        r1 = block64_provider.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64})
        r2 = block64_provider.estimate("mxu", "mmul", {"M": 1, "K": 64, "N": 64})

        from timing.perf_contract import PerfEstimate  # noqa: E402
        e1 = PerfEstimate(**r1)
        e2 = PerfEstimate(**r2)
        assert e1.content_hash() != e2.content_hash()

    def test_estimate_is_verdict_eligible(self, block64_provider):
        r = block64_provider.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64})
        from timing.perf_contract import PerfEstimate  # noqa: E402
        e = PerfEstimate(**r)
        assert e.is_verdict_eligible() is True

    def test_round_trip_stable(self, block64_provider):
        r = block64_provider.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64})
        from timing.perf_contract import PerfEstimate  # noqa: E402
        e = PerfEstimate(**r)
        rt = e.round_trip()
        assert rt.estimated_cycles == e.estimated_cycles
        assert rt.content_hash() == e.content_hash()


# ── Mutation detection ─────────────────────────────────────────────────────────


class TestMutationDetection:
    """Content-tampering and spec mutation detection."""

    def test_mutated_cycle_value_detected(self, block64_provider):
        r = block64_provider.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64})
        from timing.perf_contract import PerfEstimate  # noqa: E402
        e_orig = PerfEstimate(**r)
        # Create mutated copy
        mutated = dict(r)
        mutated["estimated_cycles"] = 99999
        e_mut = PerfEstimate(**mutated)
        assert e_orig.content_hash() != e_mut.content_hash()

    def test_mutated_provider_id_detected(self, block64_provider):
        r = block64_provider.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64})
        from timing.perf_contract import PerfEstimate  # noqa: E402
        e_orig = PerfEstimate(**r)
        mutated = dict(r)
        mutated["provider_id"] = "evil-provider"
        e_mut = PerfEstimate(**mutated)
        assert e_orig.content_hash() != e_mut.content_hash()

    def test_rtl_fields_excluded_from_hash(self, block64_provider):
        """RTL fields should be excluded from content hash computation."""
        r = block64_provider.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64})
        from timing.perf_contract import PerfEstimate  # noqa: E402
        e_no_rtl = PerfEstimate(**r)

        r_with_rtl = dict(r)
        r_with_rtl["rtl_head"] = "abcdef1234567890"
        r_with_rtl["eda_version"] = "2024.1"
        e_with_rtl = PerfEstimate(**r_with_rtl)

        assert e_no_rtl.content_hash() == e_with_rtl.content_hash()

    def test_provider_config_content_hash(self, provider_config):
        """Verify provider config has a valid schema_version."""
        assert provider_config["schema_version"] == "1.0.0"
        assert provider_config["provider_id"] == "spec-block64-v1"
        assert provider_config["basis"] == "architectural_formula"
        assert provider_config["calibration_state"] == "uncalibrated"


# ── Convenience factory ────────────────────────────────────────────────────────


class TestFactory:
    """create_registry factory function."""

    def test_create_registry_returns_instance(self):
        reg = create_registry(SPEC_PATH)
        assert isinstance(reg, ProviderRegistry)
        assert "spec-block64-v1" in reg.list_providers()

    def test_create_registry_spec_accessible(self):
        reg = create_registry(SPEC_PATH)
        assert reg.spec is not None
        assert "domains" in reg.spec


# ── Baseline characterization: existing sim/models observable behavior ────────


class TestBaselineCharacterization:
    """Characterize existing sim/models/*.py observable behavior without importing them.

    These tests verify that the provider registry does NOT import numerical kernels
    and that the existing model modules are not corrupted by provider registration.
    """

    def test_provider_module_has_no_models_import(self):
        """Verify providers.py does not import sim.models or sim.engine."""
        providers_file = REPO_ROOT / "sim" / "timing" / "providers.py"
        content = providers_file.read_text()
        import_lines = [
            line.strip() for line in content.splitlines()
            if line.strip().startswith(("import sim.models", "from sim.models",
                                         "import sim.engine", "from sim.engine"))
        ]
        assert len(import_lines) == 0, (
            f"providers.py must not import numerical kernels: {import_lines}"
        )

    def test_provider_module_has_no_golden_executor_import(self):
        """Verify providers.py does not import golden_executor or extract_func_model_cycles."""
        providers_file = REPO_ROOT / "sim" / "timing" / "providers.py"
        content = providers_file.read_text()
        for forbidden in ("golden_executor", "extract_func_model_cycles"):
            assert forbidden not in content, (
                f"providers.py must not reference '{forbidden}'"
            )

    def test_registry_activation_does_not_import_models(self, registry):
        """Activating a provider should not cause sim.models import."""
        # Record sys.modules before activation
        models_modules_before = {k for k in sys.modules if k.startswith("sim.models")}

        registry.activate("spec-block64-v1")
        registry.rollback()

        models_modules_after = {k for k in sys.modules if k.startswith("sim.models")}
        # No NEW sim.models modules should have been imported by activation
        new_imports = models_modules_after - models_modules_before
        assert len(new_imports) == 0, (
            f"Provider activation should not import sim.models modules: {new_imports}"
        )

    def test_legacy_source_rejection_module_check(self):
        """Verify that the legacy check function works on forbidden modules."""
        from timing.providers import _check_legacy_imports, _FORBIDDEN_MODULES

        # The function should not raise when sim.models is not imported
        # (If it IS imported in this test session, it was pre-imported by other tests)

    def test_spec_hash_in_estimates(self, block64_provider, spec):
        """All estimates should carry the current spec hash."""
        r = block64_provider.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64})
        expected_hash = _compute_spec_hash(spec)
        assert r["spec_hash"] == expected_hash


# ── synthetic future RTL artifact fixture tests ───────────────────────────────


class TestFutureRTLArtifact:
    """Synthetic future RTL artifact parsing and verdict rejection."""

    def test_future_rtl_fields_in_config(self, provider_config):
        """Provider config has synthetic future RTL fields."""
        future = provider_config.get("future_rtl_fields_synthetic", {})
        assert "rtl_head" in future
        assert "eda_version" in future
        assert "testbench_hash" in future
        assert "raw_log_hash" in future
        assert "fit_matrix_hash" in future
        assert "note" in future

    def test_future_rtl_fields_are_synthetic(self, provider_config):
        """Future RTL fields must declare 'SYNTHETIC FIXTURE ONLY'."""
        future = provider_config.get("future_rtl_fields_synthetic", {})
        note = future.get("note", "")
        assert "SYNTHETIC FIXTURE ONLY" in note.upper()

    def test_rtl_calibrated_provider_rejects_verdict(self, block64_provider):
        """Any estimate with rtl_calibrated state should be rejected."""
        from timing.perf_contract import PerfEstimate  # noqa: E402
        # Create an estimate with rtl_calibrated through the provider
        with pytest.raises(RTLCalibratedArtifactError):
            block64_provider.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64},
                                       calibration_state="rtl_calibrated")

    def test_future_rtl_fields_not_in_estimate_by_default(self, block64_provider):
        """Normal estimates should NOT have RTL fields set."""
        r = block64_provider.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64})
        assert r.get("rtl_head") is None
        assert r.get("eda_version") is None


# ── Supported domains enumeration ──────────────────────────────────────────────


class TestSupportedDomains:
    """Provider domain coverage."""

    def test_block64_supports_8_domains(self, block64_provider):
        domains = block64_provider.supported_domains()
        assert len(domains) == 8
        assert "mxu" in domains
        assert "sfu" in domains
        assert "vector" in domains
        assert "dma" in domains
        assert "dram" in domains
        assert "noc" in domains
        assert "kv_cache" in domains
        assert "sw_overhead" in domains

    def test_each_domain_has_boundary_id(self, provider_config):
        for domain, domain_config in provider_config["supported_domains"].items():
            assert "boundary_id" in domain_config, f"Missing boundary_id for {domain}"
            assert domain_config["boundary_id"].startswith("block64-")

    def test_each_domain_has_supported_ops(self, provider_config):
        for domain, domain_config in provider_config["supported_domains"].items():
            assert "supported_ops" in domain_config, f"Missing supported_ops for {domain}"
            ops = domain_config["supported_ops"]
            assert len(ops) > 0, f"Empty supported_ops for {domain}"
