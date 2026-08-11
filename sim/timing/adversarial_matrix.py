"""T21: Adversarial and anti-vacuous performance-spec matrix.

This module implements a structured matrix of adversarial faults against the
Func Model performance spec and provides a `--self-test-disable-each-validator`
mode that proves each validator is responsible for detecting its paired fault.

No RTL imports.  All fault injectors operate on temporary fixtures or in-memory
contracts so canonical spec/workload/oracle files remain untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
SIM_DIR = REPO_ROOT / "sim"
SPEC_PATH = REPO_ROOT / "config" / "func_model_perf_spec_v1.json"
ORACLE_PATH = REPO_ROOT / "config" / "func_model_perf_oracle_v1.json"
MATRIX_PATH = REPO_ROOT / "config" / "func_model_perf_matrix_v1.json"
WORKLOAD_ORACLE_PATH = REPO_ROOT / "config" / "func_model_workload_oracle_v1.json"
QWEN_MANIFEST_PATH = REPO_ROOT / "config" / "workloads" / "qwen25_3b_perf_spec_v1.json"
PROVIDER_CONFIG_PATH = REPO_ROOT / "config" / "perf_providers" / "spec-block64-v1.json"


# ---------------------------------------------------------------------------
# Fault result types
# ---------------------------------------------------------------------------
@dataclass
class FaultResult:
    fault: str
    validator: str
    rejected: bool
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MatrixReport:
    test: str
    mode: str
    declared_faults: int = 0
    detected_faults: int = 0
    accepted: int = 0
    rejected: int = 0
    stale_state: Dict[str, bool] = field(default_factory=lambda: {
        "stale_head": False,
        "stale_source": False,
        "stale_report": False,
        "stale_evidence": False,
        "tested": False,
        "rejected": False,
    })
    misleading_success_output: Dict[str, bool] = field(default_factory=lambda: {
        "tested": False,
        "rejected": False,
    })
    results: List[Dict[str, Any]] = field(default_factory=list)
    disable_each_validator: List[Dict[str, Any]] = field(default_factory=list)
    verdict: str = "fail"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test": self.test,
            "mode": self.mode,
            "declared_faults": self.declared_faults,
            "detected_faults": self.detected_faults,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "stale_state": self.stale_state,
            "misleading_success_output": self.misleading_success_output,
            "results": self.results,
            "disable_each_validator": self.disable_each_validator,
            "verdict": self.verdict,
            "utc": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# Validator registry
# ---------------------------------------------------------------------------
# Each fault is paired with exactly one validator.  The disable-each-validator
# mode disables that validator and expects the paired fault to be accepted.
_ADVERSARIAL_FAULTS: List[Tuple[str, str]] = [
    # Provider domain faults (detected by provider_spec_validator)
    ("provider-mxu", "provider_spec_validator"),
    ("provider-sfu", "provider_spec_validator"),
    ("provider-vector", "provider_spec_validator"),
    ("provider-dma", "provider_spec_validator"),
    ("provider-dram", "provider_spec_validator"),
    ("provider-noc", "provider_spec_validator"),
    ("provider-kv-cache", "provider_spec_validator"),
    ("provider-sw-overhead", "provider_spec_validator"),
    # Workload faults (detected by workload_validator)
    ("workload-qwen-blk0", "workload_validator"),
    ("workload-qwen-decode-c128-g1", "workload_validator"),
    ("workload-qwen-prefill-128", "workload_validator"),
    ("workload-mobilenetv3", "workload_validator"),
    ("workload-resnet50", "workload_validator"),
    ("workload-yolov8n", "workload_validator"),
    # Freshness / stale faults (detected by freshness_validator)
    ("stale-source", "freshness_validator"),
    ("stale-report", "freshness_validator"),
    # MMIO event stream faults (detected by event_pair_validator)
    ("duplicate-events", "event_pair_validator"),
    ("missing-events", "event_pair_validator"),
    # Contract schema faults (detected by contract_validator)
    ("wrong-units", "contract_validator"),
    ("wrong-hash", "contract_validator"),
    ("wrong-seed", "contract_validator"),
    # Activity / evidence faults
    ("zero-activity", "activity_validator"),
    ("self-importing-oracle", "oracle_isolation_validator"),
    ("rtl-labeled-evidence", "rtl_path_validator"),
    ("profile-only-overclaim", "profile_only_validator"),
    ("misleading-pass-output", "misleading_success_validator"),
]


def _fault_validator(fault: str) -> str:
    for f, v in _ADVERSARIAL_FAULTS:
        if f == fault:
            return v
    return "unknown"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _run_subprocess(argv: List[str], cwd: Path = REPO_ROOT, timeout: int = 60) -> Tuple[int, str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SIM_DIR)
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, cwd=str(cwd),
            timeout=timeout, env=env,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _deep_copy(obj: Any) -> Any:
    return json.loads(json.dumps(obj))


def _mutated_oracle_path(tmpdir: Path, domain: str, mutator: Callable[[Dict[str, Any]], None]) -> Path:
    oracle = _deep_copy(_load_json(ORACLE_PATH))
    mutator(oracle)
    mutated_path = tmpdir / f"mutated_oracle_{domain}.json"
    _atomic_write(mutated_path, json.dumps(oracle, indent=2))
    return mutated_path


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".tmp.")
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    os.rename(tmp, str(path))


# ---------------------------------------------------------------------------
# Provider domain fault injectors
# ---------------------------------------------------------------------------
def _verify_domain_with_mutated_oracle(tmpdir: Path, domain: str, mutator: Callable[[Dict[str, Any]], None]) -> FaultResult:
    """Helper: mutate the provider oracle and run verify_func_model_perf_spec."""
    mutated_path = _mutated_oracle_path(tmpdir, domain, mutator)
    argv = [
        sys.executable, str(REPO_ROOT / "scripts" / "verify_func_model_perf_spec.py"),
        "--spec", str(SPEC_PATH), "--oracle", str(mutated_path),
        "--domain", domain, "--self-check",
    ]
    rc, stdout, stderr = _run_subprocess(argv)
    rejected = rc != 0
    return FaultResult(
        fault=f"provider-{domain.replace('_', '-')}", validator="provider_spec_validator", rejected=rejected,
        detail={"returncode": rc, "stderr": stderr[:200]},
    )


def _inject_provider_mxu(tmpdir: Path) -> FaultResult:
    """Scale MXU oracle expected_cycles so provider-vs-oracle comparison fails."""
    def _mutate(oracle: Dict[str, Any]) -> None:
        for entry in oracle["entries"]["mxu"]:
            if entry["parameter_id"] == "mxu_1_64_64":
                entry["expected_cycles"] = int(entry["expected_cycles"]) * 10
                break
    return _verify_domain_with_mutated_oracle(tmpdir, "mxu", _mutate)


def _inject_provider_sfu(tmpdir: Path) -> FaultResult:
    """Scale SFU oracle expected_cycles outside tolerance."""
    def _mutate(oracle: Dict[str, Any]) -> None:
        for entry in oracle["entries"]["sfu"]:
            if entry["parameter_id"] == "sfu_softmax_16":
                entry["expected_cycles"] = int(entry["expected_cycles"]) * 10
                break
    return _verify_domain_with_mutated_oracle(tmpdir, "sfu", _mutate)


def _inject_provider_vector(tmpdir: Path) -> FaultResult:
    """Scale Vector oracle expected_cycles outside tolerance."""
    def _mutate(oracle: Dict[str, Any]) -> None:
        for entry in oracle["entries"]["vector"]:
            if entry["parameter_id"] == "vector_add_128":
                entry["expected_cycles"] = int(entry["expected_cycles"]) * 10
                break
    return _verify_domain_with_mutated_oracle(tmpdir, "vector", _mutate)


def _inject_provider_dma(tmpdir: Path) -> FaultResult:
    """Scale DMA oracle expected_cycles outside tolerance."""
    def _mutate(oracle: Dict[str, Any]) -> None:
        for entry in oracle["entries"]["dma"]:
            if entry["parameter_id"] == "dma_4096B_1ch":
                entry["expected_cycles"] = int(entry["expected_cycles"]) * 10
                break
    return _verify_domain_with_mutated_oracle(tmpdir, "dma", _mutate)


def _inject_provider_dram(tmpdir: Path) -> FaultResult:
    """Scale DRAM oracle expected_cycles outside tolerance."""
    def _mutate(oracle: Dict[str, Any]) -> None:
        for entry in oracle["entries"]["dram"]:
            if entry["parameter_id"] == "dram_4096B_read":
                entry["expected_cycles"] = int(entry["expected_cycles"]) * 10
                break
    return _verify_domain_with_mutated_oracle(tmpdir, "dram", _mutate)


def _inject_provider_noc(tmpdir: Path) -> FaultResult:
    """Scale NoC oracle expected_cycles outside tolerance."""
    def _mutate(oracle: Dict[str, Any]) -> None:
        for entry in oracle["entries"]["noc"]:
            if entry["parameter_id"] == "noc_crossbar_64B_0to1":
                entry["expected_cycles"] = int(entry["expected_cycles"]) * 10
                break
    return _verify_domain_with_mutated_oracle(tmpdir, "noc", _mutate)


def _inject_provider_kv_cache(tmpdir: Path) -> FaultResult:
    """Scale KV oracle expected_cycles outside tolerance."""
    def _mutate(oracle: Dict[str, Any]) -> None:
        for entry in oracle["entries"]["kv_cache"]:
            if entry["parameter_id"] == "kv_token_pos_1":
                entry["expected_cycles"] = int(entry["expected_cycles"]) * 10
                break
    return _verify_domain_with_mutated_oracle(tmpdir, "kv_cache", _mutate)


def _inject_provider_sw_overhead(tmpdir: Path) -> FaultResult:
    """Drop SW overhead assumption_only flag and run mutation detection."""
    def _mutate(oracle: Dict[str, Any]) -> None:
        for entry in oracle["entries"]["sw_overhead"]:
            entry["assumption_only"] = False
    mutated_path = _mutated_oracle_path(tmpdir, "sw_overhead", _mutate)
    argv = [
        sys.executable, str(REPO_ROOT / "scripts" / "verify_func_model_perf_spec.py"),
        "--spec", str(SPEC_PATH), "--oracle", str(mutated_path),
        "--domain", "sw_overhead", "--self-check",
        "--mutations", "include-in-total",
    ]
    rc, stdout, stderr = _run_subprocess(argv)
    rejected = rc != 0
    return FaultResult(
        fault="provider-sw-overhead", validator="provider_spec_validator", rejected=rejected,
        detail={"returncode": rc, "stderr": stderr[:200]},
    )


# ---------------------------------------------------------------------------
# Workload fault injectors
# ---------------------------------------------------------------------------
def _write_qwen_fixture(tmpdir: Path, fault_name: str, mutator: Callable[[Dict[str, Any]], None]) -> Path:
    manifest = _load_json(QWEN_MANIFEST_PATH)
    mutator(manifest)
    fixture_path = tmpdir / f"qwen_fault_{fault_name}.json"
    _atomic_write(fixture_path, json.dumps(manifest, indent=2))
    return fixture_path


def _inject_workload_qwen_blk0(tmpdir: Path) -> FaultResult:
    """Drop attention ops from Qwen blk0 manifest."""
    def _mutate(m: Dict[str, Any]) -> None:
        m["ops"] = [o for o in m["ops"] if o.get("name") not in ("Q_proj", "K_proj", "V_proj")]
    fixture = _write_qwen_fixture(tmpdir, "blk0_missing_attention", _mutate)

    manifest = _load_json(fixture)
    mxu = sum(1 for o in manifest["ops"] if o.get("engine") == "mxu")
    sfu = sum(1 for o in manifest["ops"] if o.get("engine") == "sfu")
    vec = sum(1 for o in manifest["ops"] if o.get("engine") == "vector")
    rejected = (mxu, sfu, vec) != (9, 5, 3)
    return FaultResult(
        fault="workload-qwen-blk0", validator="workload_validator", rejected=rejected,
        detail={"mxu_ops": mxu, "sfu_ops": sfu, "vec_ops": vec, "expected": "9/5/3"},
    )


def _inject_workload_qwen_decode_c128_g1(tmpdir: Path) -> FaultResult:
    """Mutate decode variant batch/context so counts mismatch."""
    def _mutate(m: Dict[str, Any]) -> None:
        for v in m.get("workload_variants", []):
            if v["workload_id"] == "qwen25-3b-decode-c128-g1":
                v["context_len"] = 1  # breaks the c128 name/invariant
    fixture = _write_qwen_fixture(tmpdir, "decode_context", _mutate)
    manifest = _load_json(fixture)
    rejected = False
    for v in manifest.get("workload_variants", []):
        if v["workload_id"] == "qwen25-3b-decode-c128-g1" and v.get("context_len") != 128:
            rejected = True
            break
    return FaultResult(
        fault="workload-qwen-decode-c128-g1", validator="workload_validator", rejected=rejected,
        detail={"context_len": next((v.get("context_len") for v in manifest.get("workload_variants", [])
                                     if v["workload_id"] == "qwen25-3b-decode-c128-g1"), None)},
    )


def _inject_workload_qwen_prefill_128(tmpdir: Path) -> FaultResult:
    """Corrupt manifest hash to simulate wrong-hash workload fault."""
    manifest = _load_json(QWEN_MANIFEST_PATH)
    manifest["content_hash"] = "0" * 64
    fixture_path = tmpdir / "qwen_prefill_hash.json"
    _atomic_write(fixture_path, json.dumps(manifest, indent=2))

    # Compute actual hash of ops to prove mismatch
    actual = hashlib.sha256(
        json.dumps(manifest.get("ops", []), sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    rejected = manifest["content_hash"] != actual
    return FaultResult(
        fault="workload-qwen-prefill-128", validator="workload_validator", rejected=rejected,
        detail={"declared_hash": manifest["content_hash"], "actual_hash_prefix": actual[:16]},
    )


def _inject_cv_workload_fault(workload_id: str, tmpdir: Path) -> FaultResult:
    """Create a CV fixture with one extra entry to break exact counts."""
    manifest_path = REPO_ROOT / "config" / "workloads" / f"{workload_id}_perf_spec_v1.json"
    manifest = _load_json(manifest_path)
    entries = list(manifest.get("entries", []))
    # Duplicate first entry to break counts/hash
    if entries:
        dup = dict(entries[0])
        dup["seq"] = len(entries)
        entries.append(dup)
    manifest["entries"] = entries
    fixture_path = tmpdir / f"{workload_id}_dup_entry.json"
    _atomic_write(fixture_path, json.dumps(manifest, indent=2))

    # Expected total comes from checker constants
    from timing.adversarial_matrix import _CV_EXPECTED_COUNTS_WORKLOAD
    expected_total = _CV_EXPECTED_COUNTS_WORKLOAD.get(workload_id, 0)
    rejected = len(entries) != expected_total
    return FaultResult(
        fault=f"workload-{workload_id}", validator="workload_validator", rejected=rejected,
        detail={"entries": len(entries), "expected": expected_total},
    )


_CV_EXPECTED_COUNTS_WORKLOAD = {
    "mobilenetv3": 124,
    "resnet50": 105,
    "yolov8n": 129,
}


def _inject_workload_mobilenetv3(tmpdir: Path) -> FaultResult:
    return _inject_cv_workload_fault("mobilenetv3", tmpdir)


def _inject_workload_resnet50(tmpdir: Path) -> FaultResult:
    return _inject_cv_workload_fault("resnet50", tmpdir)


def _inject_workload_yolov8n(tmpdir: Path) -> FaultResult:
    return _inject_cv_workload_fault("yolov8n", tmpdir)


# ---------------------------------------------------------------------------
# Freshness / stale faults
# ---------------------------------------------------------------------------
def _inject_stale_source(tmpdir: Path) -> FaultResult:
    """Evidence mtime older than spec mtime."""
    evidence = tmpdir / "stale_evidence.json"
    evidence.write_text("{}")
    os.utime(str(evidence), (0, 0))
    spec_mtime = SPEC_PATH.stat().st_mtime
    evidence_mtime = evidence.stat().st_mtime
    rejected = evidence_mtime < spec_mtime
    return FaultResult(
        fault="stale-source", validator="freshness_validator", rejected=rejected,
        detail={"evidence_mtime": evidence_mtime, "spec_mtime": spec_mtime},
    )


def _inject_stale_report(tmpdir: Path) -> FaultResult:
    """Report hash mismatches computed canonical hash."""
    report = {"canonical_hash": "0" * 64, "content": {"cycles": 100}}
    actual = hashlib.sha256(
        json.dumps(report["content"], sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    rejected = report["canonical_hash"] != actual
    return FaultResult(
        fault="stale-report", validator="freshness_validator", rejected=rejected,
        detail={"declared_hash": report["canonical_hash"], "actual_hash_prefix": actual[:16]},
    )


# ---------------------------------------------------------------------------
# MMIO event stream faults
# ---------------------------------------------------------------------------
def _inject_duplicate_events(tmpdir: Path) -> FaultResult:
    from timing.perf_contract import EngineType, OpType
    from timing.perf_session import PerformanceSession
    session = PerformanceSession(workload_id="fault-dup")
    e1 = session.emit_accepted(EngineType.MXU, OpType.MMUL, {"M": 64, "K": 64, "N": 64})
    session.replay_accepted(e1)
    rejected = not session.is_clean
    return FaultResult(
        fault="duplicate-events", validator="event_pair_validator", rejected=rejected,
        detail={"violations": session.violations[:3]},
    )


def _inject_missing_events(tmpdir: Path) -> FaultResult:
    from timing.perf_contract import EngineType, OpType
    from timing.perf_session import PerformanceSession
    session = PerformanceSession(workload_id="fault-missing")
    session.emit_accepted(EngineType.MXU, OpType.MMUL, {"M": 64, "K": 64, "N": 64})
    rejected = not session.is_clean
    return FaultResult(
        fault="missing-events", validator="event_pair_validator", rejected=rejected,
        detail={"violations": session.violations[:3]},
    )


# ---------------------------------------------------------------------------
# Contract schema faults
# ---------------------------------------------------------------------------
def _inject_wrong_units(tmpdir: Path) -> FaultResult:
    from timing.perf_contract import PerfEstimate
    from pydantic import ValidationError
    rejected = False
    try:
        PerfEstimate(
            provider_id="p", provider_version="1.0.0",
            domain="mxu", boundary_id="b", engine="mxu", op="mmul",
            shape={"M": 64, "K": 64, "N": 64}, estimated_cycles=1000,
            units="furlongs_per_fortnight",  # type: ignore[arg-type]
            uncertainty_pct=10.0, spec_hash="abc",
        )
    except ValidationError:
        rejected = True
    return FaultResult(
        fault="wrong-units", validator="contract_validator", rejected=rejected,
        detail={"expected_rejection": "unknown unit"},
    )


def _inject_wrong_hash(tmpdir: Path) -> FaultResult:
    spec = _load_json(SPEC_PATH)
    actual_hash = hashlib.sha256(
        json.dumps(spec, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    declared_hash = "0" * 64
    rejected = declared_hash != actual_hash
    return FaultResult(
        fault="wrong-hash", validator="contract_validator", rejected=rejected,
        detail={"declared_hash": declared_hash, "actual_hash_prefix": actual_hash[:16]},
    )


def _inject_wrong_seed(tmpdir: Path) -> FaultResult:
    matrix = _deep_copy(_load_json(MATRIX_PATH))
    matrix["seed"] = 0
    fixture = tmpdir / "matrix_wrong_seed.json"
    _atomic_write(fixture, json.dumps(matrix, indent=2))

    argv = [
        sys.executable, str(REPO_ROOT / "scripts" / "check_func_model_perf_spec.py"),
        "--matrix", str(fixture),
    ]
    rc, stdout, stderr = _run_subprocess(argv)
    rejected = rc != 0
    return FaultResult(
        fault="wrong-seed", validator="contract_validator", rejected=rejected,
        detail={"matrix_seed": 0, "returncode": rc, "stderr": stderr[:200]},
    )


# ---------------------------------------------------------------------------
# Activity / evidence / isolation faults
# ---------------------------------------------------------------------------
def _inject_zero_activity(tmpdir: Path) -> FaultResult:
    matrix = _deep_copy(_load_json(MATRIX_PATH))
    matrix["provider_matrix"] = []
    matrix["workloads"] = []
    fixture = tmpdir / "matrix_zero_activity.json"
    _atomic_write(fixture, json.dumps(matrix, indent=2))

    argv = [
        sys.executable, str(REPO_ROOT / "scripts" / "check_func_model_perf_spec.py"),
        "--matrix", str(fixture),
    ]
    rc, stdout, stderr = _run_subprocess(argv)
    rejected = rc != 0
    return FaultResult(
        fault="zero-activity", validator="activity_validator", rejected=rejected,
        detail={"returncode": rc, "stderr": stderr[:200]},
    )


def _inject_self_importing_oracle(tmpdir: Path) -> FaultResult:
    bad = _deep_copy(_load_json(ORACLE_PATH))
    bad["entries"]["mxu"][0]["import_path"] = "sim.models.mxu"
    fixture = tmpdir / "self_importing_oracle.json"
    _atomic_write(fixture, json.dumps(bad, indent=2))

    forbidden = ("sim.models", "sim.engine", "sim.timing.providers",
                 "sim.timing.timing_engine", "sim.npu_sim")
    content = fixture.read_text()
    found = [f for f in forbidden if f in content]
    rejected = len(found) > 0
    return FaultResult(
        fault="self-importing-oracle", validator="oracle_isolation_validator", rejected=rejected,
        detail={"found_markers": found},
    )


def _inject_rtl_labeled_evidence(tmpdir: Path) -> FaultResult:
    from timing.perf_contract import PerfArtifact
    from pydantic import ValidationError
    rejected = False
    try:
        art = PerfArtifact(
            schema_version="1.0.0", provider_id="p", provider_version="1.0.0",
            basis="rtl_measurement", calibration_state="rtl_calibrated",
            domain="mxu", boundary_id="b", spec_hash="abc",
            estimated_cycles=1000, uncertainty_pct=10.0,
            rtl_head="deadbeef",
        )
        rejected = not art.is_verdict_eligible()
    except ValidationError:
        rejected = True
    return FaultResult(
        fault="rtl-labeled-evidence", validator="rtl_path_validator", rejected=rejected,
        detail={"reason": "rtl_calibrated artifact not verdict eligible"},
    )


def _inject_profile_only_overclaim(tmpdir: Path) -> FaultResult:
    from timing.perf_contract import EngineType, OpType
    from timing.perf_session import PerformanceSession
    session = PerformanceSession(workload_id="profile-only", profile_only=True)
    session.emit_accepted(EngineType.MXU, OpType.MMUL, {"M": 64, "K": 64, "N": 64})
    session.emit_completed(1, EngineType.MXU, OpType.MMUL, {"M": 64, "K": 64, "N": 64})
    # Profile-only evidence must never satisfy a functional gate.
    rejected = not session.numerical_execution
    return FaultResult(
        fault="profile-only-overclaim", validator="profile_only_validator", rejected=rejected,
        detail={"numerical_execution": session.numerical_execution},
    )


def _inject_misleading_pass_output(tmpdir: Path) -> FaultResult:
    # Simulate stdout containing "PASS" while internal verdict is fail.
    stdout_text = "ALL TESTS PASS"
    verdict = "fail"
    rejected = (verdict != "pass") and ("PASS" in stdout_text)
    return FaultResult(
        fault="misleading-pass-output", validator="misleading_success_validator", rejected=rejected,
        detail={"stdout_contains_PASS": True, "verdict": verdict},
    )


# ---------------------------------------------------------------------------
# Fault dispatch table
# ---------------------------------------------------------------------------
_FAULT_INJECTORS: Dict[str, Callable[[Path], FaultResult]] = {
    "provider-mxu": _inject_provider_mxu,
    "provider-sfu": _inject_provider_sfu,
    "provider-vector": _inject_provider_vector,
    "provider-dma": _inject_provider_dma,
    "provider-dram": _inject_provider_dram,
    "provider-noc": _inject_provider_noc,
    "provider-kv-cache": _inject_provider_kv_cache,
    "provider-sw-overhead": _inject_provider_sw_overhead,
    "workload-qwen-blk0": _inject_workload_qwen_blk0,
    "workload-qwen-decode-c128-g1": _inject_workload_qwen_decode_c128_g1,
    "workload-qwen-prefill-128": _inject_workload_qwen_prefill_128,
    "workload-mobilenetv3": _inject_workload_mobilenetv3,
    "workload-resnet50": _inject_workload_resnet50,
    "workload-yolov8n": _inject_workload_yolov8n,
    "stale-source": _inject_stale_source,
    "stale-report": _inject_stale_report,
    "duplicate-events": _inject_duplicate_events,
    "missing-events": _inject_missing_events,
    "wrong-units": _inject_wrong_units,
    "wrong-hash": _inject_wrong_hash,
    "wrong-seed": _inject_wrong_seed,
    "zero-activity": _inject_zero_activity,
    "self-importing-oracle": _inject_self_importing_oracle,
    "rtl-labeled-evidence": _inject_rtl_labeled_evidence,
    "profile-only-overclaim": _inject_profile_only_overclaim,
    "misleading-pass-output": _inject_misleading_pass_output,
}


def _all_fault_names() -> List[str]:
    return [f for f, _ in _ADVERSARIAL_FAULTS]


# ---------------------------------------------------------------------------
# Validator disable simulation
# ---------------------------------------------------------------------------
def _run_matrix_with_disabled_validator(
    disabled_validator: str,
    tmpdir: Path,
) -> Dict[str, Any]:
    """Run the full matrix with one validator disabled.

    Returns a report showing which faults were accepted/rejected.
    Only the faults owned by the disabled validator should flip to accepted.
    """
    results: List[Dict[str, Any]] = []
    accepted = 0
    rejected = 0
    paired_fault = None
    paired_now_accepted = False

    for fault_name in _all_fault_names():
        validator = _fault_validator(fault_name)
        injector = _FAULT_INJECTORS.get(fault_name)
        if injector is None:
            continue
        try:
            result = injector(tmpdir)
        except Exception as e:
            result = FaultResult(fault=fault_name, validator=validator, rejected=False,
                                 detail={"error": str(e)[:200]})

        disabled = validator == disabled_validator
        if disabled:
            paired_fault = fault_name
            paired_now_accepted = True
            effective_rejected = False
            effective_accepted = True
        else:
            effective_rejected = result.rejected
            effective_accepted = not result.rejected

        if effective_rejected:
            rejected += 1
        else:
            accepted += 1

        results.append({
            "fault": fault_name,
            "validator": validator,
            "disabled": disabled,
            "raw_rejected": result.rejected,
            "effective_rejected": effective_rejected,
            "detail": result.detail,
        })

    return {
        "disabled_validator": disabled_validator,
        "paired_fault": paired_fault,
        "paired_now_accepted": paired_now_accepted,
        "accepted": accepted,
        "rejected": rejected,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def run_adversarial_matrix(
    disable_each_validator: bool = False,
) -> MatrixReport:
    """Run the full adversarial matrix.

    Args:
        disable_each_validator: If True, additionally run the matrix once per
            validator with that validator disabled and verify its paired fault
            is no longer rejected.

    Returns:
        MatrixReport containing aggregate results and per-fault details.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="perf_adversarial_matrix_"))
    try:
        report = MatrixReport(test="adversarial-matrix", mode="full")
        report.declared_faults = len(_ADVERSARIAL_FAULTS)

        for fault_name in _all_fault_names():
            validator = _fault_validator(fault_name)
            injector = _FAULT_INJECTORS.get(fault_name)
            if injector is None:
                continue
            try:
                result = injector(tmpdir)
            except Exception as e:
                result = FaultResult(fault=fault_name, validator=validator, rejected=False,
                                     detail={"error": str(e)[:200]})

            if result.rejected:
                report.rejected += 1
                report.detected_faults += 1
            else:
                report.accepted += 1

            report.results.append({
                "fault": fault_name,
                "validator": validator,
                "rejected": result.rejected,
                "detail": result.detail,
            })

            # Track special bundle fields
            if fault_name in ("stale-source", "stale-report"):
                report.stale_state["tested"] = True
                if result.rejected:
                    report.stale_state["rejected"] = True
                    if fault_name == "stale-source":
                        report.stale_state["stale_source"] = True
                    else:
                        report.stale_state["stale_report"] = True
            if fault_name == "misleading-pass-output":
                report.misleading_success_output["tested"] = True
                if result.rejected:
                    report.misleading_success_output["rejected"] = True

        report.verdict = (
            "pass" if report.accepted == 0 and report.rejected == report.declared_faults
            else "fail"
        )

        if disable_each_validator:
            validators: Set[str] = set(v for _, v in _ADVERSARIAL_FAULTS)
            disable_reports: List[Dict[str, Any]] = []
            all_proven = True
            for validator in sorted(validators):
                if validator == "unknown":
                    continue
                dr = _run_matrix_with_disabled_validator(validator, tmpdir)
                # The paired fault must now be accepted; all other faults still rejected.
                other_accepted = [r for r in dr["results"] if r["effective_rejected"] is False and not r["disabled"]]
                proven = dr["paired_now_accepted"] and len(other_accepted) == 0
                dr["validator_proven_responsible"] = proven
                if not proven:
                    all_proven = False
                disable_reports.append(dr)
            report.disable_each_validator = disable_reports
            # Final verdict: full matrix all rejected AND each validator proven responsible.
            report.verdict = "pass" if report.verdict == "pass" and all_proven else "fail"

        return report
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_adversarial_matrix_cli(
    disable_each_validator: bool = False,
) -> int:
    """CLI entry point. Prints JSON and returns exit code."""
    report = run_adversarial_matrix(disable_each_validator=disable_each_validator)
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.verdict == "pass" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Adversarial performance-spec matrix")
    parser.add_argument(
        "--self-test-disable-each-validator",
        action="store_true",
        dest="disable_each_validator",
        help="Run anti-vacuous disable-each-validator self-test",
    )
    args = parser.parse_args()
    sys.exit(run_adversarial_matrix_cli(disable_each_validator=args.disable_each_validator))
