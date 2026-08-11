#!/usr/bin/env python3
"""Func Model Performance Spec Checker — validates normative spec JSON.

Usage:
    python3 check_func_model_perf_spec.py --spec config/func_model_perf_spec_v1.json
    python3 check_func_model_perf_spec.py --matrix config/func_model_perf_matrix_v1.json
    python3 check_func_model_perf_spec.py --negative-fixtures fixtures.json
    python3 check_func_model_perf_spec.py --spec spec.json --matrix matrix.json --negative-fixtures bad1.json,bad2.json

The checker validates the spec JSON against the normative schema and business rules, and/or
validates the frozen matrix JSON against hard-gate constraints (provider rows, workloads, sweeps, limits).
- All parameters must have: parameter_id, domain, formula, estimated_cycles, units, owner, basis, uncertainty, rationale.
- basis must be "architecture_assumption"; "rtl_measurement" is rejected.
- estimated_cycles must be non-NaN, non-Inf, non-negative integer (zero allowed for expected_noop).
- units must be in the approved set.
- owner must be non-empty.
- parameter_ids must be unique.
- Spec hash is computed from content only (no timestamp/mutable fields).
- Negative fixture mode validates each fixture is correctly rejected.

Output: structured JSON verdict with per-parameter and aggregate results.
Exit code: 0 on success (spec valid / fixtures rejected as expected), non-zero on failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── typed unit helpers with overflow detection ──────────────────────────

_VALID_UNITS = frozenset({
    "cycles", "ns", "us", "ms", "s",
    "bytes", "KB", "MB", "GB",
    "bits",
    "Hz", "kHz", "MHz", "GHz",
    "cycles/ns", "bytes/cycle", "bits/cycle",
    "GB/s", "MB/s",
    "tokens/s", "FPS",
})

_APPROVED_DOMAINS = frozenset({
    "mxu", "sfu", "vector", "dma", "dram", "noc", "kv_cache", "sw_overhead",
})

_REQUIRED_PARAMETER_FIELDS = frozenset({
    "parameter_id", "domain", "description", "formula", "inputs",
    "estimated_cycles", "units", "owner", "basis", "uncertainty", "rationale",
})

# Fields excluded from content hash (mutable / timestamp-sensitive)
_HASH_EXCLUDE_FIELDS = frozenset({"content_hash", "created", "updated", "timestamp"})


def _is_valid_unit(unit: str) -> bool:
    return unit in _VALID_UNITS


def _is_valid_domain(domain: str) -> bool:
    return domain in _APPROVED_DOMAINS


def _check_not_nan_inf(value: Any) -> bool:
    """Return True if value is a finite number (int or float), not NaN or Inf."""
    if not isinstance(value, (int, float)):
        return False
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return False
    return True


def _to_int_cycles(value: Any) -> Optional[int]:
    """Convert a finite numeric value to integer cycles using ceiling.

    Returns None if value is not finite-numeric or is negative.
    """
    if not _check_not_nan_inf(value):
        return None
    if value < 0:
        return None
    return int(math.ceil(float(value)))


# ── unit conversion helpers (typed, overflow-checked) ──────────────────

def bytes_to_bits(n_bytes: int) -> int:
    """Convert bytes to bits. Raises OverflowError on overflow."""
    if n_bytes < 0:
        raise ValueError(f"bytes_to_bits: negative input {n_bytes}")
    result = n_bytes * 8
    if n_bytes != 0 and result // n_bytes != 8:
        raise OverflowError(f"bytes_to_bits: overflow {n_bytes} * 8")
    return result


def hz_to_mhz(hz_val: float) -> float:
    """Convert Hz to MHz."""
    return hz_val / 1_000_000.0


def mhz_to_hz(mhz_val: float) -> float:
    """Convert MHz to Hz."""
    return mhz_val * 1_000_000.0


def cycles_to_ns(cycles: int, freq_mhz: float) -> float:
    """Convert cycles to nanoseconds at given frequency (MHz)."""
    if freq_mhz <= 0:
        raise ValueError(f"cycles_to_ns: invalid frequency {freq_mhz} MHz")
    return float(cycles) / freq_mhz * 1000.0


def ns_to_cycles(ns_val: float, freq_mhz: float) -> int:
    """Convert nanoseconds to cycles (ceiled) at given frequency (MHz)."""
    if freq_mhz <= 0:
        raise ValueError(f"ns_to_cycles: invalid frequency {freq_mhz} MHz")
    return int(math.ceil(ns_val * freq_mhz / 1000.0))


def bandwidth_bytes_per_cycle(gbps: float, freq_mhz: float) -> float:
    """Convert bandwidth in GB/s to bytes per cycle at given frequency."""
    if freq_mhz <= 0:
        raise ValueError(f"bandwidth_bytes_per_cycle: invalid frequency {freq_mhz}")
    # GB/s → bytes/s: * 1e9; bytes/s → bytes/cycle: / (freq_mhz * 1e6)
    return gbps * 1e9 / (freq_mhz * 1e6)


# ── content hash (no timestamp) ────────────────────────────────────────

def _compute_content_hash(data: Any) -> str:
    """Compute SHA-256 hash of spec content, excluding mutable fields.

    All fields in _HASH_EXCLUDE_FIELDS are stripped before hashing.
    The hash is deterministic: identical content → identical hash.

    If JSON cannot be canonicalized (e.g. NaN/Inf in data), returns
    a sentinel error-hash that will never match a valid spec.
    """
    def _strip_excluded(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: _strip_excluded(v)
                for k, v in obj.items()
                if k not in _HASH_EXCLUDE_FIELDS
            }
        elif isinstance(obj, list):
            return [_strip_excluded(item) for item in obj]
        return obj

    clean = _strip_excluded(data)
    # sort_keys for determinism
    try:
        canonical = json.dumps(clean, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError):
        # NaN/Inf or other unrepresentable values → sentinel hash
        return "ERROR:invalid-content-for-hash"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── matrix validation logic ───────────────────────────────────────────

_MATRIX_REQUIRED_FIELDS = frozenset({
    "schema_version", "matrix_id", "seed",
    "frozen_policies", "provider_matrix", "workloads", "sweep_grids",
})

_MATRIX_KNOWN_WORKLOADS = frozenset({
    "qwen25-3b-blk0-decode", "qwen25-3b-decode-c128-g1",
    "qwen-prefill-16", "qwen-prefill-128",
    "mobilenetv3", "resnet50", "yolov8n",
})

_MATRIX_REQUIRED_SWEEP_GRID_IDS = frozenset({
    "bandwidth", "array", "dma_channels", "prompt", "context", "noc_hop",
})

_MATRIX_REQUIRED_ENDPOINT_IDS = frozenset({
    "bottleneck_mem_bound", "bottleneck_compute_bound",
})

_MATRIX_REQUIRED_BW_VALUES = frozenset({6.4, 12.8, 25.6, 51.2, 102.4})


class MatrixValidator:
    """Validates a frozen func_model_perf_matrix JSON document.

    Checks:
    - Required top-level fields present
    - seed == 42
    - No skip/skipped/silent flags enabled
    - Provider row counts match expected domain counts
    - No duplicate case_ids
    - Workload IDs match known set; no duplicate workload_ids
    - Sweep grids have required IDs and correct values
    - Bottleneck endpoints present (6.4 GB/s mem-bound, 102.4 GB/s compute-bound)
    - Runtime limits encoded and non-zero
    """

    def __init__(self, data: Dict[str, Any]):
        self.data = data
        self.errors: List[ValidationError] = []
        self.provider_counts: Dict[str, int] = {}

    def validate(self) -> bool:
        self._validate_structure()
        self._validate_seed()
        self._validate_no_silent_skip()
        self._validate_provider_matrix()
        self._validate_workloads()
        self._validate_sweep_grids()
        self._validate_runtime_limits()
        return len(self.errors) == 0

    def _add_error(self, param_id: str, field: str, msg: str) -> None:
        self.errors.append(ValidationError(param_id, field, msg))

    def _validate_structure(self) -> None:
        for field in _MATRIX_REQUIRED_FIELDS:
            if field not in self.data:
                self._add_error("(root)", field, f"missing required field '{field}'")
            elif self.data[field] is None:
                self._add_error("(root)", field, f"field '{field}' is null")

        schema_ver = self.data.get("schema_version")
        if schema_ver and not isinstance(schema_ver, str):
            self._add_error("(root)", "schema_version", "must be a string")

        matrix_id = self.data.get("matrix_id")
        if matrix_id and not isinstance(matrix_id, str):
            self._add_error("(root)", "matrix_id", "must be a string")

    def _validate_seed(self) -> None:
        seed = self.data.get("seed")
        if seed is None:
            self._add_error("(root)", "seed", "seed is required and must be 42")
        elif seed != 42:
            self._add_error("(root)", "seed", f"seed must be 42, got {seed}")

    def _validate_no_silent_skip(self) -> None:
        """Reject skip flags, skipped markers, or empty-run deception anywhere in matrix."""
        policies = self.data.get("frozen_policies", {})

        # Reject skip/silent flags at policy level
        for skip_key in ("skip", "skipped", "silent_skip", "skip_allowed"):
            if policies.get(skip_key) is True:
                self._add_error("frozen_policies", skip_key,
                                f"'{skip_key}=true' is forbidden: no case may silently skip")
            # Also reject falsy-but-present to avoid confusion
            if skip_key in policies and policies[skip_key] is not False:
                self._add_error("frozen_policies", skip_key,
                                f"'{skip_key}' must be explicitly false or absent")

        # Check no_silent_skip policy is present and explicit
        no_skip = policies.get("no_silent_skip")
        if no_skip is None:
            self._add_error("frozen_policies", "no_silent_skip",
                            "no_silent_skip policy must be explicitly declared")
        elif isinstance(no_skip, str) and "Every case" not in str(no_skip):
            self._add_error("frozen_policies", "no_silent_skip",
                            "no_silent_skip text does not commit to 'Every case'")

        # Recursively search for skip/skipped anywhere in the data
        def _find_skip_flags(obj: Any, path: str) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in ("skip", "skipped", "silent_skip") and v is True:
                        self._add_error(f"{path}.{k}", k,
                                        f"skip flag '{k}=true' found at {path}.{k}: no case may silently skip")
                    _find_skip_flags(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    _find_skip_flags(item, f"{path}[{i}]")

        _find_skip_flags(self.data, "(root)")

    def _validate_provider_matrix(self) -> None:
        pm = self.data.get("provider_matrix", {})
        if not pm or not isinstance(pm, dict):
            self._add_error("provider_matrix", "", "provider_matrix must be a dict with 'rows'")
            return

        expected = pm.get("expected_domain_counts", {})
        if not expected or not isinstance(expected, dict):
            self._add_error("provider_matrix.expected_domain_counts", "",
                            "expected_domain_counts is required")

        rows = pm.get("rows", {})
        if not rows or not isinstance(rows, dict):
            self._add_error("provider_matrix.rows", "",
                            "rows must be a dict of domain->case_list")
            return

        # Check each domain exists in rows
        for domain in expected:
            if domain not in rows:
                self._add_error(f"provider_matrix.rows.{domain}", "",
                                f"domain '{domain}' declared in expected_domain_counts but missing from rows")
            elif not isinstance(rows[domain], list):
                self._add_error(f"provider_matrix.rows.{domain}", "",
                                f"rows.{domain} must be a list")
            else:
                actual_count = len(rows[domain])
                expected_count = expected[domain]
                if actual_count != expected_count:
                    self._add_error(f"provider_matrix.rows.{domain}", "",
                                    f"row count mismatch: expected {expected_count}, got {actual_count}")

        # Check no extra domains in rows
        for domain in rows:
            if domain not in expected:
                self._add_error(f"provider_matrix.rows.{domain}", "",
                                f"extra domain '{domain}' not in expected_domain_counts")

        # Check all case_ids are unique across all domains
        seen_ids: set = set()
        for domain, cases in rows.items():
            if not isinstance(cases, list):
                continue
            for i, case in enumerate(cases):
                cid = case.get("case_id", f"{domain}[{i}]")
                if cid in seen_ids:
                    self._add_error(f"provider_matrix.rows.{domain}[{i}]", "case_id",
                                    f"duplicate case_id '{cid}'")
                seen_ids.add(cid)

        self.provider_counts = {d: len(rows[d]) if d in rows and isinstance(rows[d], list) else 0
                                for d in expected}

    def _validate_workloads(self) -> None:
        wl = self.data.get("workloads", {})
        if not wl or not isinstance(wl, dict):
            self._add_error("workloads", "", "workloads must be a dict with 'entries'")
            return

        entries = wl.get("entries", [])
        if not entries or not isinstance(entries, list):
            self._add_error("workloads.entries", "", "workloads.entries must be a non-empty list")
            return

        seen_ids: set = set()
        for i, entry in enumerate(entries):
            wid = entry.get("workload_id", f"entries[{i}]")
            if wid in seen_ids:
                self._add_error(f"workloads.entries[{i}]", "workload_id",
                                f"duplicate workload_id '{wid}'")
            seen_ids.add(wid)

            if wid not in _MATRIX_KNOWN_WORKLOADS:
                self._add_error(f"workloads.entries[{i}]", "workload_id",
                                f"unknown workload_id '{wid}'; known: {sorted(_MATRIX_KNOWN_WORKLOADS)}")

            # Verify seed=42 per workload
            wseed = entry.get("seed")
            if wseed is not None and wseed != 42:
                self._add_error(f"workloads.entries[{i}]", "seed",
                                f"per-workload seed must be 42, got {wseed}")

        # Check count of known workloads present
        present_ids = {e.get("workload_id") for e in entries if e.get("workload_id")}
        missing = _MATRIX_KNOWN_WORKLOADS - present_ids
        if missing:
            self._add_error("workloads", "entries",
                            f"missing hard workloads: {sorted(missing)}")

    def _validate_sweep_grids(self) -> None:
        sg = self.data.get("sweep_grids", {})
        if not sg or not isinstance(sg, dict):
            self._add_error("sweep_grids", "", "sweep_grids must be a dict with 'grids' and 'bottleneck_endpoints'")
            return

        grids = sg.get("grids", [])
        if not grids or not isinstance(grids, list):
            self._add_error("sweep_grids.grids", "", "grids must be a non-empty list")
        else:
            seen_sids: set = set()
            for i, grid in enumerate(grids):
                sid = grid.get("sweep_id", f"grids[{i}]")
                if sid in seen_sids:
                    self._add_error(f"sweep_grids.grids[{i}]", "sweep_id",
                                    f"duplicate sweep_id '{sid}'")
                seen_sids.add(sid)

                # Validate values are non-empty list
                vals = grid.get("values", [])
                if not vals or not isinstance(vals, list):
                    self._add_error(f"sweep_grids.grids[{i}]", "values",
                                    f"sweep grid '{sid}' has empty or non-list values")
                elif len(vals) < 2:
                    self._add_error(f"sweep_grids.grids[{i}]", "values",
                                    f"sweep grid '{sid}' needs at least 2 values, got {len(vals)}")

            # Verify required grid IDs present
            present_sids = {g.get("sweep_id") for g in grids if g.get("sweep_id")}
            missing = _MATRIX_REQUIRED_SWEEP_GRID_IDS - present_sids
            if missing:
                self._add_error("sweep_grids.grids", "sweep_ids",
                                f"missing required sweep grids: {sorted(missing)}")

            # Verify specific grid values match frozen sets
            for grid in grids:
                sid = grid.get("sweep_id", "")
                vals = grid.get("values", [])
                if sid == "bandwidth":
                    actual = set(float(v) for v in vals)
                    if actual != _MATRIX_REQUIRED_BW_VALUES:
                        self._add_error("sweep_grids.grids", f"bandwidth.values",
                                        f"bandwidth values mismatch: got {sorted(actual)}, expected {sorted(_MATRIX_REQUIRED_BW_VALUES)}")
                    # Verify 6.4 is the lowest
                    if vals and min(float(v) for v in vals) != 6.4:
                        self._add_error("sweep_grids.grids", "bandwidth.values",
                                        "bandwidth must include 6.4 GB/s as minimum")
                elif sid == "array":
                    if set(int(v) for v in vals) != {32, 64, 128}:
                        self._add_error("sweep_grids.grids", "array.values",
                                        f"array values must be [32, 64, 128]")
                elif sid == "dma_channels":
                    if set(int(v) for v in vals) != {1, 2, 4, 8}:
                        self._add_error("sweep_grids.grids", "dma_channels.values",
                                        f"dma_channels values must be [1, 2, 4, 8]")

        # Validate bottleneck endpoints
        endpoints = sg.get("bottleneck_endpoints", [])
        if not endpoints or not isinstance(endpoints, list):
            self._add_error("sweep_grids", "bottleneck_endpoints",
                            "bottleneck_endpoints must be a non-empty list with both mem-bound and compute-bound")
        else:
            seen_eids: set = set()
            for i, ep in enumerate(endpoints):
                eid = ep.get("endpoint_id", f"endpoints[{i}]")
                if eid in seen_eids:
                    self._add_error(f"sweep_grids.bottleneck_endpoints[{i}]", "endpoint_id",
                                    f"duplicate endpoint_id '{eid}'")
                seen_eids.add(eid)

                config = ep.get("config", {})
                if eid == "bottleneck_mem_bound":
                    if config.get("bandwidth") != 6.4:
                        self._add_error(f"sweep_grids.bottleneck_endpoints[{i}]", "config.bandwidth",
                                        "memory-bound endpoint must use BW=6.4 GB/s")
                    if config.get("array") != 128:
                        self._add_error(f"sweep_grids.bottleneck_endpoints[{i}]", "config.array",
                                        "memory-bound endpoint must use array=128")
                elif eid == "bottleneck_compute_bound":
                    if config.get("bandwidth") != 102.4:
                        self._add_error(f"sweep_grids.bottleneck_endpoints[{i}]", "config.bandwidth",
                                        "compute-bound endpoint must use BW=102.4 GB/s")
                    if config.get("array") != 32:
                        self._add_error(f"sweep_grids.bottleneck_endpoints[{i}]", "config.array",
                                        "compute-bound endpoint must use array=32")

            present_eids = {e.get("endpoint_id") for e in endpoints if e.get("endpoint_id")}
            missing_eps = _MATRIX_REQUIRED_ENDPOINT_IDS - present_eids
            if missing_eps:
                self._add_error("sweep_grids.bottleneck_endpoints", "endpoint_ids",
                                f"missing required bottleneck endpoints: {sorted(missing_eps)}")

    def _validate_runtime_limits(self) -> None:
        policies = self.data.get("frozen_policies", {})
        limits = policies.get("runtime_limits", {})
        if not limits or not isinstance(limits, dict):
            self._add_error("frozen_policies.runtime_limits", "",
                            "runtime_limits is required")
            return

        required_limits = {
            "provider_case_seconds": 30,
            "workload_seconds": 120,
            "full_signoff_seconds": 1800,
            "peak_rss_mb": 4096,
        }

        for limit_name, expected_max in required_limits.items():
            entry = limits.get(limit_name)
            if not entry or not isinstance(entry, dict):
                self._add_error("frozen_policies.runtime_limits", limit_name,
                                f"missing or invalid {limit_name}")
            else:
                actual = entry.get("max")
                if actual is None:
                    self._add_error("frozen_policies.runtime_limits", f"{limit_name}.max",
                                    f"{limit_name} must have 'max' field")
                elif actual != expected_max:
                    self._add_error("frozen_policies.runtime_limits", f"{limit_name}.max",
                                    f"{limit_name} max must be {expected_max}, got {actual}")

    def verdict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "valid": len(self.errors) == 0,
            "accepted": 0,
            "rejected": sum(1 for e in self.errors if e.parameter_id != "(root)"),
            "total_parameters": sum(self.provider_counts.values()),
            "errors": len(self.errors),
            "warnings": 0,
            "error_details": [e.to_dict() for e in self.errors],
            "warning_details": [],
            "domain_counts": self.provider_counts,
            "matrix_id": self.data.get("matrix_id", "unknown"),
            "schema_version": self.data.get("schema_version", "unknown"),
            "seed_check": self.data.get("seed") == 42,
        }
        return result


# ── validation logic ───────────────────────────────────────────────────

class ValidationError:
    """A single validation error for a parameter or global issue."""

    def __init__(self, parameter_id: str, field: str, message: str):
        self.parameter_id = parameter_id
        self.field = field
        self.message = message

    def to_dict(self) -> Dict[str, str]:
        return {
            "parameter_id": self.parameter_id,
            "field": self.field,
            "message": self.message,
        }


class SpecValidator:
    """Validates a func_model_perf_spec JSON document."""

    def __init__(self, data: Dict[str, Any]):
        self.data = data
        self.errors: List[ValidationError] = []
        self.warnings: List[ValidationError] = []
        self.parameter_count = 0
        self.domain_counts: Dict[str, int] = {}
        self.seen_ids: set = set()

    def validate(self) -> bool:
        """Run full validation. Returns True if no errors found."""
        self._validate_structure()
        self._validate_parameters()
        self._validate_content_hash()
        return len(self.errors) == 0

    def _add_error(self, param_id: str, field: str, msg: str) -> None:
        self.errors.append(ValidationError(param_id, field, msg))

    def _add_warning(self, param_id: str, field: str, msg: str) -> None:
        self.warnings.append(ValidationError(param_id, field, msg))

    def _validate_structure(self) -> None:
        """Validate top-level structure and required fields."""
        # Check schema_version
        schema_ver = self.data.get("schema_version")
        if not schema_ver:
            self._add_error("(root)", "schema_version", "missing schema_version")
        elif not isinstance(schema_ver, str):
            self._add_error("(root)", "schema_version", "must be a string")

        # Check spec_id
        spec_id = self.data.get("spec_id")
        if not spec_id:
            self._add_error("(root)", "spec_id", "missing spec_id")

        # Check domains
        domains = self.data.get("domains")
        if not domains:
            self._add_error("(root)", "domains", "missing domains object")
        elif not isinstance(domains, dict):
            self._add_error("(root)", "domains", "domains must be a dict")
        else:
            for domain_name in domains:
                if domain_name not in _APPROVED_DOMAINS:
                    self._add_error("(root)", f"domains.{domain_name}",
                                    f"unknown domain '{domain_name}'; allowed: {sorted(_APPROVED_DOMAINS)}")

        # Check frozen_policies
        policies = self.data.get("frozen_policies")
        if not policies:
            self._add_error("(root)", "frozen_policies", "missing frozen_policies")
        elif not isinstance(policies, dict):
            self._add_error("(root)", "frozen_policies", "frozen_policies must be a dict")

    def _validate_parameters(self) -> None:
        """Validate all parameters across all domains."""
        domains = self.data.get("domains", {})

        for domain_name, params in domains.items():
            if not isinstance(params, list):
                self._add_error(f"domains.{domain_name}", "", "domain value must be a list of parameters")
                continue

            for idx, param in enumerate(params):
                if not isinstance(param, dict):
                    self._add_error(f"{domain_name}[{idx}]", "", "parameter must be a dict")
                    continue

                param_id = param.get("parameter_id", f"{domain_name}[{idx}]")
                self.parameter_count += 1
                self.domain_counts[domain_name] = self.domain_counts.get(domain_name, 0) + 1
                self._validate_parameter(param_id, param, domain_name)

    def _validate_parameter(self, param_id: str, param: Dict[str, Any], domain: str) -> None:
        """Validate a single parameter entry."""

        # ── required fields ──
        for field in _REQUIRED_PARAMETER_FIELDS:
            if field not in param:
                self._add_error(param_id, field, f"missing required field '{field}'")
            elif param[field] is None:
                self._add_error(param_id, field, f"field '{field}' is null")

        # ── parameter_id uniqueness ──
        if param_id in self.seen_ids:
            self._add_error(param_id, "parameter_id", f"duplicate parameter_id '{param_id}'")
        self.seen_ids.add(param_id)

        # ── domain check ──
        actual_domain = param.get("domain", "")
        if actual_domain != domain:
            self._add_error(param_id, "domain",
                            f"domain '{actual_domain}' does not match container domain '{domain}'")

        # ── basis check (must be architecture_assumption) ──
        basis = param.get("basis", "")
        if basis == "rtl_measurement":
            self._add_error(param_id, "basis",
                            "basis='rtl_measurement' is forbidden; only 'architecture_assumption' allowed in v1")
        elif basis != "architecture_assumption":
            self._add_error(param_id, "basis",
                            f"basis must be 'architecture_assumption', got '{basis}'")

        # ── estimated_cycles check ──
        cycles = param.get("estimated_cycles")
        if cycles is not None:
            if not isinstance(cycles, (int, float)):
                self._add_error(param_id, "estimated_cycles",
                                f"must be numeric, got {type(cycles).__name__}")
            elif isinstance(cycles, float) and (math.isnan(cycles) or math.isinf(cycles)):
                self._add_error(param_id, "estimated_cycles",
                                "NaN/Inf not allowed in estimated_cycles")
            elif cycles < 0:
                self._add_error(param_id, "estimated_cycles",
                                f"negative value {cycles} not allowed (zero only for expected_noop)")
            elif cycles == 0:
                # Zero cycles: allowed only if expected_noop is true
                is_noop = param.get("expected_noop", False)
                if not is_noop:
                    self._add_error(param_id, "estimated_cycles",
                                    "zero cycles only allowed when expected_noop=true")
            else:
                # Check that value is integer (no fractions for cycles)
                if isinstance(cycles, float) and cycles != math.floor(cycles):
                    self._add_warning(param_id, "estimated_cycles",
                                      f"non-integer cycle value {cycles}; should use ceil")
            # Also check if estimated_cycles is int-like
            if isinstance(cycles, float) and not math.isinf(cycles) and not math.isnan(cycles):
                if abs(cycles - round(cycles)) > 1e-9:
                    self._add_warning(param_id, "estimated_cycles",
                                      f"fractional cycle value {cycles}; expected integer (ceiled)")

        # ── units check ──
        units = param.get("units", "")
        if units and not _is_valid_unit(units):
            self._add_error(param_id, "units",
                            f"invalid unit '{units}'; approved: {sorted(_VALID_UNITS)}")

        # ── owner check ──
        owner = param.get("owner", "")
        if owner is not None and owner == "":
            self._add_error(param_id, "owner", "owner must not be empty")

        # ── uncertainty check ──
        uncertainty = param.get("uncertainty")
        if uncertainty is not None:
            if isinstance(uncertainty, str) and not uncertainty.startswith("["):
                self._add_warning(param_id, "uncertainty",
                                  f"uncertainty should be a [low, high] range string, got '{uncertainty}'")

        # ── rationale check ──
        rationale = param.get("rationale", "")
        if rationale is not None and len(rationale) < 20:
            self._add_warning(param_id, "rationale",
                              f"rationale too short ({len(rationale)} chars); should explain the value")

        # ── formula check ──
        formula = param.get("formula", "")
        if formula is not None and len(formula) < 5:
            self._add_warning(param_id, "formula",
                              "formula is suspiciously short or empty")

        # ── inputs check ──
        inputs = param.get("inputs")
        if inputs is not None and not isinstance(inputs, dict):
            self._add_error(param_id, "inputs", "inputs must be a dict")

        # ── expected_noop consistency ──
        is_noop = param.get("expected_noop", False)
        if is_noop:
            if cycles != 0:
                self._add_error(param_id, "expected_noop",
                                "expected_noop=true but estimated_cycles != 0")
            # Also check that uncertainty reflects exact zero
            unc = param.get("uncertainty", "")
            if isinstance(unc, str) and unc != "[0, 0]" and "0, 0" not in unc:
                self._add_warning(param_id, "uncertainty",
                                  "expected_noop=true but uncertainty band is not [0, 0]")

        # ── monotonicity_annotations structure check ──
        mono = param.get("monotonicity_annotations")
        if mono is not None:
            if not isinstance(mono, dict):
                self._add_error(param_id, "monotonicity_annotations",
                                "must be a dict if present")
            else:
                # Check that expected_zero_derivatives is a list of strings
                zds = mono.get("expected_zero_derivatives")
                if zds is not None:
                    if not isinstance(zds, list):
                        self._add_error(param_id, "monotonicity_annotations.expected_zero_derivatives",
                                        "must be a list")
                    else:
                        for i, zd in enumerate(zds):
                            if not isinstance(zd, str):
                                self._add_error(param_id,
                                                f"monotonicity_annotations.expected_zero_derivatives[{i}]",
                                                "must be a string")

                # Check saturation_annotations structure
                sat = mono.get("saturation_annotations")
                if sat is not None and not isinstance(sat, dict):
                    self._add_error(param_id, "monotonicity_annotations.saturation_annotations",
                                    "must be a dict")

    def _validate_content_hash(self) -> None:
        """Validate that if content_hash is present, it matches computed hash."""
        stored_hash = self.data.get("content_hash")
        if stored_hash:
            computed = _compute_content_hash(self.data)
            if stored_hash != computed:
                self._add_error("(root)", "content_hash",
                                f"hash mismatch: stored={stored_hash[:16]}..., computed={computed[:16]}...")
        # Note: we do NOT require content_hash to be present;
        # it can be added later. We only verify if present.

    def verdict(self) -> Dict[str, Any]:
        """Produce structured JSON verdict."""
        result: Dict[str, Any] = {
            "valid": len(self.errors) == 0,
            "accepted": self.parameter_count - len(set(e.parameter_id for e in self.errors if e.parameter_id != "(root)")),
            "rejected": len(set(e.parameter_id for e in self.errors if e.parameter_id != "(root)")),
            "total_parameters": self.parameter_count,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "error_details": [e.to_dict() for e in self.errors],
            "warning_details": [w.to_dict() for w in self.warnings],
            "domain_counts": self.domain_counts,
            "spec_id": self.data.get("spec_id", "unknown"),
            "schema_version": self.data.get("schema_version", "unknown"),
            "content_hash": _compute_content_hash(self.data),
        }
        return result


# ── negative fixture support ───────────────────────────────────────────

def validate_negative_fixture(filepath: str) -> Dict[str, Any]:
    """Validate a negative fixture file (should be rejected).

    Auto-detects matrix vs spec fixtures:
       matrix fixtures have a 'matrix_id' or 'provider_matrix' field
       spec fixtures have a 'spec_id' or 'domains' field

    Returns a verdict dict with 'expected_reject': True and the actual
    validation result. A negative fixture PASSES when it is correctly
    rejected (valid=False, errors > 0). It FAILS when it is accepted
    (valid=True).
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        return {
            "file": filepath,
            "expected_reject": True,
            "fixture_passes": True,
            "reason": f"Fixture not valid JSON (expected behavior): {e}",
            "valid": False,
            "errors": 1,
        }

    is_matrix = "matrix_id" in data or "provider_matrix" in data

    if is_matrix:
        validator = MatrixValidator(data)  # type: ignore[assignment]
    else:
        validator = SpecValidator(data)

    is_valid = validator.validate()
    verdict = validator.verdict()
    verdict["file"] = filepath
    verdict["expected_reject"] = True
    verdict["fixture_passes"] = not is_valid
    if is_valid:
        verdict["fail_reason"] = "Negative fixture was incorrectly accepted (should have been rejected)"
    return verdict


# ── CLI ────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Func Model performance spec against normative schema",
    )
    parser.add_argument(
        "--spec",
        type=str,
        default=None,
        help="Path to the spec JSON file to validate",
    )
    parser.add_argument(
        "--matrix",
        type=str,
        default=None,
        help="Path to the frozen matrix JSON file to validate",
    )
    parser.add_argument(
        "--negative-fixtures",
        type=str,
        default=None,
        help="Comma-separated paths to negative fixture JSON files (expected to be rejected)",
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        default=False,
        help="Output verdict as JSON (otherwise pretty-print)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress non-verdict output",
    )
    args = parser.parse_args()

    exit_code = 0
    results: Dict[str, Any] = {}

    # ── validate spec ──
    if args.spec:
        try:
            with open(args.spec, "r", encoding="utf-8") as f:
                spec_data = json.load(f)
        except FileNotFoundError:
            print(f"ERROR: Spec file not found: {args.spec}", file=sys.stderr)
            return 1
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in spec file: {e}", file=sys.stderr)
            return 1

        validator = SpecValidator(spec_data)
        is_valid = validator.validate()
        verdict = validator.verdict()
        results["spec"] = verdict

        if not is_valid:
            exit_code = 1

    # ── validate matrix ──
    if args.matrix:
        try:
            with open(args.matrix, "r", encoding="utf-8") as f:
                matrix_data = json.load(f)
        except FileNotFoundError:
            print(f"ERROR: Matrix file not found: {args.matrix}", file=sys.stderr)
            return 1
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in matrix file: {e}", file=sys.stderr)
            return 1

        mvalidator = MatrixValidator(matrix_data)
        m_is_valid = mvalidator.validate()
        mverdict = mvalidator.verdict()
        results["matrix"] = mverdict

        if not m_is_valid:
            exit_code = 1

    # ── validate negative fixtures ──
    if args.negative_fixtures:
        fixture_paths = [p.strip() for p in args.negative_fixtures.split(",") if p.strip()]
        fixture_results = []
        for fp in fixture_paths:
            fv = validate_negative_fixture(fp)
            fixture_results.append(fv)
            if not fv["fixture_passes"]:
                exit_code = 1
        results["negative_fixtures"] = fixture_results

    # ── output ──
    if args.json_output:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        if not args.quiet:
            _print_pretty(results)
        else:
            # Silent mode: only print JSON verdict
            print(json.dumps(results, indent=2, ensure_ascii=False, sort_keys=True))

    return exit_code


def _print_pretty(results: Dict[str, Any]) -> None:
    """Print human-readable verdict."""
    if "spec" in results:
        spec = results["spec"]
        status = "PASS" if spec["valid"] else "FAIL"
        print(f"Spec: {spec['spec_id']} v{spec['schema_version']} — {status}")
        print(f"  Parameters: {spec['total_parameters']} total, {spec['accepted']} accepted, {spec['rejected']} rejected")
        print(f"  Errors: {spec['errors']}, Warnings: {spec['warnings']}")
        print(f"  Content hash: {spec['content_hash'][:16]}...")
        if spec["error_details"]:
            print("  Error details:")
            for err in spec["error_details"]:
                print(f"    [{err['parameter_id']}] {err['field']}: {err['message']}")
        if spec.get("warning_details"):
            print("  Warnings:")
            for w in spec["warning_details"]:
                print(f"    [{w['parameter_id']}] {w['field']}: {w['message']}")
        print(f"  Domain counts: {json.dumps(spec.get('domain_counts', {}))}")

    if "matrix" in results:
        mat = results["matrix"]
        status = "PASS" if mat["valid"] else "FAIL"
        print(f"\nMatrix: {mat.get('matrix_id', '?')} v{mat.get('schema_version', '?')} — {status}")
        print(f"  Errors: {mat['errors']}")
        print(f"  Seed check: {'OK' if mat.get('seed_check') else 'FAIL'}")
        print(f"  Domain counts: {json.dumps(mat.get('domain_counts', {}))}")
        if mat["error_details"]:
            print("  Error details:")
            for err in mat["error_details"]:
                print(f"    [{err['parameter_id']}] {err['field']}: {err['message']}")

    if "negative_fixtures" in results:
        fixtures = results["negative_fixtures"]
        total = len(fixtures)
        passed = sum(1 for f in fixtures if f.get("fixture_passes", False))
        rejected = sum(1 for f in fixtures if not f.get("fixture_passes", False))
        print(f"\nNegative fixtures: {total} total, {passed} correctly rejected, {rejected} incorrectly accepted")
        for f in fixtures:
            status = "OK" if f.get("fixture_passes") else "BAD"
            print(f"  [{status}] {f.get('file', '?')}: {f.get('reason', f.get('fail_reason', ''))}")


if __name__ == "__main__":
    sys.exit(main())
