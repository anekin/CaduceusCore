"""Architectural provider registry — explicit activation, rollback, and fail-closed dispatch.

Providers read the normative T1 spec and return T2 PerfEstimate objects.
The registry enforces domain/boundary/uncertainty provenance and rejects
unsupported, out-of-domain, legacy-source, and RTL-calibrated artifacts.

No numerical kernel imports (sim.models, sim.engine) — this module is
pure architectural dispatch.

Usage:
    from sim.timing.providers import ProviderRegistry
    reg = ProviderRegistry("config/func_model_perf_spec_v1.json")
    reg.activate("spec-block64-v1")
    est = reg.estimate("mxu", "mmul", {"M": 64, "K": 64, "N": 64})
    reg.rollback()
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# ── Error types ────────────────────────────────────────────────────────────────


class ProviderError(Exception):
    """Base error for provider registry operations."""


class UnknownProviderError(ProviderError):
    """Provider ID not found in registry."""


class UnsupportedOpError(ProviderError):
    """Operation not supported by the active provider."""


class OutOfDomainError(ProviderError):
    """Domain not covered by the active provider."""


class LegacySourceError(ProviderError):
    """Legacy numerical-kernel source detected (sim.models import)."""


class RTLCalibratedArtifactError(ProviderError):
    """Artifact has RTL calibration but current phase rejects RTL sources."""


class SpecNotFoundError(ProviderError):
    """Normative spec file not found at the given path."""


# ── Shape-key mapping (engine → expected shape keys) ───────────────────────────

ENGINE_SHAPE_KEYS: Dict[str, frozenset[str]] = {
    "mxu": frozenset({"M", "K", "N"}),
    "sfu": frozenset({"elements"}),
    "vector": frozenset({"dim"}),
    "dma": frozenset({"bytes"}),
    "dram": frozenset({"bytes", "rw"}),
    "kv_cache": frozenset({"token_pos", "sram_kb"}),
    "noc": frozenset({"bytes", "topology", "route"}),
    "sw_overhead": frozenset({"num_layers"}),
}

# Map domain names (mxu, sfu, ...) to engine enum values (MXU, SFU, ...)
DOMAIN_TO_ENGINE: Dict[str, str] = {
    "mxu": "mxu",
    "sfu": "sfu",
    "vector": "vector",
    "dma": "dma",
    "dram": "dram",
    "noc": "noc",
    "kv_cache": "kv_cache",
    "sw_overhead": "sw_overhead",
}

# Op mapping from spec to T2 OpType enum values
DOMAIN_OP_MAP: Dict[str, Dict[str, str]] = {
    "mxu": {},
    "sfu": {"softmax": "softmax", "layernorm": "layernorm", "rmsnorm": "rmsnorm",
            "gelu": "gelu", "silu": "silu", "rope": "rope"},
    "vector": {"add": "add", "mul": "mul", "max": "max", "sum": "sum",
               "conv": "conv", "resid": "resid"},
    "dma": {"dma_copy": "dma_copy"},
    "dram": {"dram_read": "dram_read", "dram_write": "dram_write"},
    "noc": {"noc_route": "noc_route"},
    "kv_cache": {"kv_access": "kv_access", "kv_layer_switch": "kv_layer_switch"},
    "sw_overhead": {"riscv_instr": "riscv_instr"},
}

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── Legacy source detection ───────────────────────────────────────────────────

_FORBIDDEN_MODULES: frozenset[str] = frozenset({
    "sim.models", "sim.engine",
})

_LEGACY_DETECTED: bool = False


def _check_legacy_imports() -> None:
    """Check if forbidden numerical-kernel modules are imported in sys.modules.

    Called at provider activation. Raises LegacySourceError if found.
    """
    for mod_name in sys.modules:
        for forbidden in _FORBIDDEN_MODULES:
            if mod_name == forbidden or mod_name.startswith(forbidden + "."):
                raise LegacySourceError(
                    f"Forbidden numerical-kernel module imported: {mod_name}. "
                    f"Provider registry rejects legacy numerical-kernel sources."
                )


# ── Spec loader ───────────────────────────────────────────────────────────────


def _load_spec(spec_path: str) -> Dict[str, Any]:
    """Load and validate the normative performance spec."""
    full_path = REPO_ROOT / spec_path
    if not full_path.is_file():
        raise SpecNotFoundError(f"Spec not found: {full_path}")
    with open(full_path, "r") as f:
        spec = json.load(f)
    if "domains" not in spec:
        raise SpecNotFoundError("Spec missing 'domains' key")
    if "schema_version" not in spec:
        raise SpecNotFoundError("Spec missing 'schema_version'")
    return spec


def _compute_spec_hash(spec: Dict[str, Any]) -> str:
    """Compute canonical content hash of the spec (excluding timestamps)."""
    data = {k: v for k, v in spec.items() if k not in ("created", "timestamp", "date")}
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Uncertainty parser ─────────────────────────────────────────────────────────


def _parse_uncertainty_pct(uncertainty_str: str) -> float:
    """Extract uncertainty percentage from spec uncertainty string (e.g. '[169, 313]').

    For low/base/high with base=estimated_cycles and band = [low, high],
    uncertainty_pct = (high - base) / base * 100.
    Returns 0.0 for exact-zero bands [0, 0].
    """
    try:
        parts = uncertainty_str.strip("[]").split(",")
        if len(parts) != 2:
            return 30.0
        low = float(parts[0].strip())
        high = float(parts[1].strip())
        # Exact-zero band (e.g., kv_token_pos_0 = [0,0])
        if low == 0.0 and high == 0.0:
            return 0.0
        # Estimate base from band center: base = (low + high) / 2
        base_est = (low + high) / 2
        if base_est > 0:
            return round(abs(high - base_est) / base_est * 100, 1)
        return 30.0
    except (ValueError, IndexError):
        return 30.0


# ── Domain → DomainType mapping ────────────────────────────────────────────────

DOMAIN_ENUM_MAP: Dict[str, str] = {
    "mxu": "mxu", "sfu": "sfu", "vector": "vector",
    "dma": "dma", "dram": "dram", "noc": "noc",
    "kv_cache": "kv_cache", "sw_overhead": "sw_overhead",
}


def _extract_op_from_spec_param(param: Dict[str, Any]) -> Optional[str]:
    """Extract the op type from a spec parameter's inputs dict."""
    inputs = param.get("inputs", {})
    op_val = inputs.get("op")
    if op_val:
        return op_val
    # For domain-specific inference: MXU always mmul, DMA always dma_copy, etc.
    domain = param.get("domain", "")
    if domain == "mxu":
        return "mmul"
    if domain == "dma":
        return "dma_copy"
    if domain == "dram":
        direction = inputs.get("direction", "read")
        return f"dram_{direction}"
    if domain == "noc":
        return "noc_route"
    if domain == "kv_cache":
        if "operation" in inputs:
            return "kv_layer_switch"
        return "kv_access"
    if domain == "sw_overhead":
        return "riscv_instr"
    return None


def _extract_shape_from_spec_param(param: Dict[str, Any]) -> Dict[str, int]:
    """Extract shape dict from a spec parameter inputs."""
    inputs = param.get("inputs", {})
    domain = param.get("domain", "")
    shape: Dict[str, int] = {}

    if domain == "mxu":
        for key in ("M", "K", "N"):
            if key in inputs:
                shape[key] = int(inputs[key])
    elif domain == "sfu":
        if "elements" in inputs:
            shape["elements"] = int(inputs["elements"])
    elif domain == "vector":
        if "dim" in inputs:
            shape["dim"] = int(inputs["dim"])
    elif domain == "dma":
        if "bytes" in inputs:
            shape["bytes"] = int(inputs["bytes"])
    elif domain == "dram":
        if "bytes" in inputs:
            shape["bytes"] = int(inputs["bytes"])
        direction = inputs.get("direction", "read")
        shape["rw"] = 0 if direction == "read" else 1
    elif domain == "noc":
        if "bytes" in inputs:
            shape["bytes"] = int(inputs["bytes"])
        if "topology" in inputs:
            shape["topology"] = _topology_id(inputs["topology"])
        if "route" in inputs:
            shape["route"] = _route_id(inputs["route"])
    elif domain == "kv_cache":
        if "token_pos" in inputs:
            shape["token_pos"] = int(inputs["token_pos"])
            shape["sram_kb"] = 64  # default for token access
        elif "sram_kb" in inputs:
            shape["token_pos"] = 0
            shape["sram_kb"] = int(inputs["sram_kb"])
    elif domain == "sw_overhead":
        if "num_layers" in inputs:
            shape["num_layers"] = int(inputs["num_layers"])
        elif "num_ops" in inputs:
            shape["num_layers"] = int(inputs["num_ops"])

    return shape


def _topology_id(name: str) -> int:
    """Encode topology name as integer."""
    return 0 if name == "crossbar" else 1


def _route_id(route: str) -> int:
    """Encode route string as integer (shortest path hop count)."""
    try:
        parts = route.split("->")
        return abs(int(parts[1].strip()) - int(parts[0].strip()))
    except (IndexError, ValueError):
        return 1


# ── Block 64×64 Provider ──────────────────────────────────────────────────────


class Block64Provider:
    """Architectural-formula provider for Block 64×64 engine.

    Reads the normative T1 spec (config/func_model_perf_spec_v1.json) and
    returns typed PerfEstimate objects. All estimates are architectural
    formula only; no RTL measurement.

    Provider is covered for all 8 domains declared in the spec:
    mxu, sfu, vector, dma, dram, noc, kv_cache, sw_overhead.
    """

    PROVIDER_ID: str = "spec-block64-v1"
    PROVIDER_VERSION: str = "1.0.0"
    SCHEMA_VERSION: str = "1.0.0"

    def __init__(self, spec: Dict[str, Any], config: Optional[Dict[str, Any]] = None):
        self.spec = spec
        self.config = config or {}
        self.spec_hash = _compute_spec_hash(spec)
        self._lookup: Dict[str, Dict[Any, Dict[str, Any]]] = {}
        self._build_lookup()

    def _build_lookup(self) -> None:
        """Build a lookup table from (domain, shape_key) → spec parameter."""
        domains = self.spec.get("domains", {})
        for domain_name, params in domains.items():
            domain_lookup: Dict[Any, Dict[str, Any]] = {}
            for param in params:
                key = self._make_lookup_key(domain_name, param)
                domain_lookup[key] = param
            self._lookup[domain_name] = domain_lookup

    def _make_lookup_key(self, domain: str, param: Dict[str, Any]) -> Any:
        """Generate a hashable lookup key for a spec parameter."""
        inputs = param.get("inputs", {})
        param_id = param.get("parameter_id", "")
        if domain == "mxu":
            return (int(inputs.get("M", 0)), int(inputs.get("K", 0)), int(inputs.get("N", 0)))
        if domain == "sfu":
            return (str(inputs.get("op", "")), int(inputs.get("elements", 0)))
        if domain == "vector":
            return (str(inputs.get("op", "")), int(inputs.get("dim", 0)))
        if domain == "dma":
            return (int(inputs.get("bytes", 0)), int(inputs.get("channels", 1)))
        if domain == "dram":
            return (int(inputs.get("bytes", 0)), str(inputs.get("direction", "read")))
        if domain == "noc":
            return (
                str(inputs.get("topology", "")),
                int(inputs.get("bytes", 0)),
                str(inputs.get("route", "")),
            )
        if domain == "kv_cache":
            return param_id  # token_pos or layer_switch
        if domain == "sw_overhead":
            return param_id  # workload-specific
        return param_id

    def _find_param(self, domain: str, shape: Dict[str, int],
                     op: Optional[str] = None) -> Dict[str, Any]:
        domain_lookup = self._lookup.get(domain, {})
        if not domain_lookup:
            return {}

        if domain == "mxu":
            key = (
                int(shape.get("M", 0)),
                int(shape.get("K", 0)),
                int(shape.get("N", 0)),
            )
            return domain_lookup.get(key, {})

        if domain == "sfu":
            elements = int(shape.get("elements", 0))
            # SFU lookup key is (op, elements) — match both op and elements
            if op:
                for lookup_key, param in domain_lookup.items():
                    if lookup_key[0] == op and lookup_key[1] == elements:
                        return param
            return {}

        if domain == "vector":
            dim = int(shape.get("dim", 0))
            if op:
                for lookup_key, param in domain_lookup.items():
                    if lookup_key[0] == op and lookup_key[1] == dim:
                        return param
            return {}

        if domain == "dma":
            bytes_val = int(shape.get("bytes", 0))
            # Find by bytes value with channels=1 (default single-channel)
            for lookup_key, param in domain_lookup.items():
                if lookup_key[0] == bytes_val:
                    return param
            return {}

        if domain == "dram":
            bytes_val = int(shape.get("bytes", 0))
            rw_val = int(shape.get("rw", 0))
            direction = "read" if rw_val == 0 else "write"
            for lookup_key, param in domain_lookup.items():
                if lookup_key[0] == bytes_val and lookup_key[1] == direction:
                    return param
            return {}

        if domain == "noc":
            bytes_val = int(shape.get("bytes", 0))
            topology = shape.get("topology", 0)
            topo_name = "crossbar" if topology == 0 else "mesh"
            route = shape.get("route", 1)
            route_str = f"0->{route}"
            for lookup_key, param in domain_lookup.items():
                if (lookup_key[0] == topo_name and
                        lookup_key[1] == bytes_val and
                        lookup_key[2] == route_str):
                    return param
            return {}

        if domain == "kv_cache":
            if "token_pos" in shape:
                token_pos = int(shape["token_pos"])
                for param_id, param in domain_lookup.items():
                    if param_id.startswith("kv_token_pos_"):
                        try:
                            spec_pos = int(param_id.split("_")[3])
                            if spec_pos == token_pos:
                                return param
                        except (IndexError, ValueError):
                            pass
                return {}
            return {}

        if domain == "sw_overhead":
            # Match by parameter_id or workload
            for param_id, param in domain_lookup.items():
                inputs = param.get("inputs", {})
                if shape.get("num_layers") is not None and inputs.get("num_layers") == shape["num_layers"]:
                    return param
            return {}

        return {}

    def estimate(self, domain: str, op: str, shape: Dict[str, int],
                 basis: str = "architectural_formula",
                 calibration_state: str = "uncalibrated") -> Dict[str, Any]:
        """Return a PerfEstimate dict for the given domain, op, and shape.

        Raises:
            UnsupportedOpError: op not supported for domain.
            OutOfDomainError: domain not covered by this provider.
        """
        # Import PerfEstimate from the same package — pure typed schema, not a numerical kernel.
        from .perf_contract import (
            BasisType,
            CalibrationState,
            DomainType,
            EngineType,
            OpType,
            PerfEstimate,
            UnitType,
        )

        # Check domain
        supported = self.config.get("supported_domains", {}) if self.config else {}
        if domain not in supported:
            raise OutOfDomainError(
                f"Domain '{domain}' is not supported by provider '{self.PROVIDER_ID}'. "
                f"Supported domains: {sorted(supported.keys())}"
            )

        # Check op
        domain_config = supported[domain]
        supported_ops = domain_config.get("supported_ops", [])
        if op not in supported_ops:
            raise UnsupportedOpError(
                f"Op '{op}' is not supported for domain '{domain}' "
                f"by provider '{self.PROVIDER_ID}'. "
                f"Supported ops: {supported_ops}"
            )

        # Check shape keys
        expected_shape_keys = ENGINE_SHAPE_KEYS.get(domain)
        if expected_shape_keys:
            actual_keys = frozenset(shape.keys())
            if actual_keys != expected_shape_keys:
                raise UnsupportedOpError(
                    f"Shape keys {sorted(actual_keys)} for domain '{domain}' "
                    f"do not match expected {sorted(expected_shape_keys)}"
                )

        # Check for RTL calibration rejection
        basis_enum = BasisType.ARCHITECTURAL_FORMULA
        if basis == "rtl_measurement":
            basis_enum = BasisType.RTL_MEASUREMENT
        cal_enum = CalibrationState.UNCALIBRATED
        if calibration_state == "rtl_calibrated":
            cal_enum = CalibrationState.RTL_CALIBRATED

        if cal_enum == CalibrationState.RTL_CALIBRATED or basis_enum == BasisType.RTL_MEASUREMENT:
            raise RTLCalibratedArtifactError(
                f"Provider '{self.PROVIDER_ID}' rejects RTL-calibrated artifacts. "
                f"Only basis=architectural_formula + calibration_state=uncalibrated is verdict-eligible."
            )

        # Find matching spec parameter
        param = self._find_param(domain, shape, op=op)
        if not param:
            raise UnsupportedOpError(
                f"No spec parameter matches domain='{domain}' op='{op}' "
                f"shape={shape} in provider '{self.PROVIDER_ID}'"
            )

        # Build PerfEstimate
        domain_enum = DomainType(domain)
        engine_enum = EngineType(DOMAIN_TO_ENGINE.get(domain, domain))
        op_enum = OpType(op)
        boundary_id = domain_config.get("boundary_id", f"{domain}-block64")
        uncertainty_str = param.get("uncertainty", "[0, 0]")
        uncertainty_pct = _parse_uncertainty_pct(uncertainty_str)

        # Handle expected_noop cases (estimated_cycles=0) — return a lightweight dict
        # instead of a full PerfEstimate to avoid Pydantic gt=0 validation failure
        estimated_cycles = int(param["estimated_cycles"])
        if estimated_cycles == 0:
            return {
                "provider_id": self.PROVIDER_ID,
                "provider_version": self.PROVIDER_VERSION,
                "schema_version": self.SCHEMA_VERSION,
                "basis": basis,
                "calibration_state": calibration_state,
                "domain": domain,
                "boundary_id": boundary_id,
                "engine": DOMAIN_TO_ENGINE.get(domain, domain),
                "op": op,
                "shape": shape,
                "estimated_cycles": 0,
                "units": "cycles",
                "assumptions": param.get("rationale", "").split(". "),
                "uncertainty_pct": uncertainty_pct,
                "spec_hash": self.spec_hash,
                "config_hash": "",
                "rtl_head": None,
                "eda_version": None,
                "testbench_hash": None,
                "raw_log_hash": None,
                "fit_matrix_hash": None,
                "_noop": True,
            }

        estimate = PerfEstimate(
            provider_id=self.PROVIDER_ID,
            provider_version=self.PROVIDER_VERSION,
            basis=basis_enum,
            calibration_state=cal_enum,
            domain=domain_enum,
            boundary_id=boundary_id,
            engine=engine_enum,
            op=op_enum,
            shape=shape,
            estimated_cycles=estimated_cycles,
            uncertainty_pct=uncertainty_pct,
            spec_hash=self.spec_hash,
            assumptions=param.get("rationale", "").split(". "),
        )
        return estimate.model_dump(mode="json")

    def supported_domains(self) -> Set[str]:
        """Return set of supported domain names."""
        config_domains = self.config.get("supported_domains", {}) if self.config else {}
        return set(config_domains.keys())


# ── Provider Registry ─────────────────────────────────────────────────────────


class ProviderRegistry:
    """Central registry for performance providers with explicit activation/rollback.

    Providers are registered by ID. Only one provider is active at a time.
    activate() pushes the current provider onto a stack; rollback() restores
    the previous provider. The initial state has no active provider.
    """

    def __init__(self, spec_path: str):
        """Initialize the registry with the normative spec path.

        Args:
            spec_path: Path to config/func_model_perf_spec_v1.json (relative to REPO_ROOT).
        """
        self._spec_path = spec_path
        self._spec: Dict[str, Any] = _load_spec(spec_path)
        self._providers: Dict[str, Any] = {}
        self._active_id: Optional[str] = None
        self._stack: List[str] = []
        self._register_builtins()

    def _register_builtins(self) -> None:
        """Register the built-in Block 64×64 provider."""
        config_path = REPO_ROOT / "config" / "perf_providers" / "spec-block64-v1.json"
        config: Dict[str, Any] = {}
        if config_path.is_file():
            with open(config_path, "r") as f:
                config = json.load(f)
        provider = Block64Provider(self._spec, config)
        self.register(Block64Provider.PROVIDER_ID, provider)

    def register(self, provider_id: str, provider: Any) -> None:
        """Register a provider instance under the given ID."""
        self._providers[provider_id] = provider

    def activate(self, provider_id: str) -> str:
        """Activate a provider by ID. Pushes current onto rollback stack.

        Args:
            provider_id: The provider ID to activate.

        Returns:
            The provider ID that is now active.

        Raises:
            UnknownProviderError: provider_id not found.
            LegacySourceError: forbidden numerical-kernel modules detected.
        """
        if provider_id not in self._providers:
            raise UnknownProviderError(
                f"Provider '{provider_id}' not found. "
                f"Available: {sorted(self._providers.keys())}"
            )

        # Legacy source check
        _check_legacy_imports()

        # Push current to stack for rollback
        if self._active_id is not None:
            self._stack.append(self._active_id)
        self._active_id = provider_id
        return self._active_id

    def rollback(self) -> Optional[str]:
        """Rollback to the previous provider on the stack.

        If no provider is on the stack, deactivates (returns None).

        Returns:
            The now-active provider ID, or None if no provider is active.
        """
        if self._stack:
            self._active_id = self._stack.pop()
        else:
            self._active_id = None
        return self._active_id

    @property
    def active_provider_id(self) -> Optional[str]:
        """The currently active provider ID, or None."""
        return self._active_id

    @property
    def active_provider(self) -> Optional[Any]:
        """The currently active provider instance, or None."""
        if self._active_id is None:
            return None
        return self._providers.get(self._active_id)

    def estimate(self, domain: str, op: str, shape: Dict[str, int],
                 basis: str = "architectural_formula",
                 calibration_state: str = "uncalibrated") -> Dict[str, Any]:
        """Return a PerfEstimate dict from the active provider.

        Raises:
            ProviderError: if no provider is active.
        """
        provider = self.active_provider
        if provider is None:
            raise ProviderError("No active provider. Call activate() first.")
        return provider.estimate(domain, op, shape, basis, calibration_state)

    @property
    def spec(self) -> Dict[str, Any]:
        """The loaded normative spec dict."""
        return self._spec

    @property
    def spec_hash(self) -> str:
        """Canonical content hash of the loaded spec."""
        return _compute_spec_hash(self._spec)

    def list_providers(self) -> List[str]:
        """Return sorted list of registered provider IDs."""
        return sorted(self._providers.keys())


# ── Convenience factory ───────────────────────────────────────────────────────


def create_registry(spec_path: str = "config/func_model_perf_spec_v1.json") -> ProviderRegistry:
    """Create a ProviderRegistry with built-in providers registered.

    Args:
        spec_path: Path to the normative spec JSON (relative to REPO_ROOT).

    Returns:
        A ready-to-use ProviderRegistry instance.
    """
    return ProviderRegistry(spec_path)
