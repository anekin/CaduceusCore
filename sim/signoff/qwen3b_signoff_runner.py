#!/usr/bin/env python3
"""Top-level positive/negative signoff orchestration and evidence writer."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from signoff.qwen3b_signoff_config import (
    NPU_BACKEND_NAME,
    SignoffConfig,
    SignoffError,
    compute_backend_hash,
    verify_model_hash,
)
from signoff.qwen3b_signoff_gates import (
    GateResult,
    gate_cpu_fallback_mixed_graph,
    gate_decode_tokens,
    gate_full_shape_blk0,
    gate_supported_single_ops,
)
from signoff.qwen3b_signoff_io import (
    _backend_workdir,
    _llama_env,
    _run,
    managed_device_server,
)


def run_positive_signoff(
    config: SignoffConfig, device_uri: str, evidence_path: Path
) -> dict[str, object]:
    """Execute all enabled gates and return the evidence payload."""
    verify_model_hash(config.model_path, config.model_sha256)
    base_env = os.environ.copy()
    base_env["PYTHONPATH"] = "sim:gen"

    with managed_device_server(device_uri) as resolved_uri:
        gates: list[GateResult] = []
        if config.gates["supported_single_ops"].get("enabled", True):
            gates.append(gate_supported_single_ops(config, resolved_uri, base_env))
        if config.gates["full_shape_blk0"].get("enabled", True):
            gates.append(gate_full_shape_blk0(config, resolved_uri, base_env))
        if config.gates["single_decode_token"].get("enabled", True):
            gates.append(gate_decode_tokens(config, resolved_uri, base_env, "single_decode_token"))
        if config.gates["multi_token_decode_with_kv"].get("enabled", True):
            gates.append(gate_decode_tokens(config, resolved_uri, base_env, "multi_token_decode_with_kv"))
        if config.gates["cpu_fallback_mixed_graph"].get("enabled", True):
            gates.append(gate_cpu_fallback_mixed_graph(config, resolved_uri, base_env))

    all_passed = all(g.passed for g in gates)
    payload: dict[str, object] = {
        "manifest": str(config.model_path),
        "model_sha256": config.model_sha256,
        "llama_commit": config.llama_commit,
        "abi_version": config.abi_version,
        "backend_hash": compute_backend_hash(),
        "device_uri": device_uri,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "verdict": "pass" if all_passed else "fail",
        "gates": [
            {"name": g.name, "passed": g.passed, "metrics": g.metrics}
            for g in gates
        ],
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def run_negative_signoff(
    config: SignoffConfig, evidence_path: Path
) -> dict[str, object]:
    """Run anti-vacuous checks and write the negative evidence payload."""
    checks: list[dict[str, object]] = []
    negative = config.negative_checks

    if negative.get("model_hash_mismatch", {}).get("enabled", True):
        try:
            verify_model_hash(config.model_path, "0" * 64)
            detected = False
        except SignoffError:
            detected = True
        checks.append({
            "name": "model_hash_mismatch",
            "detected": detected,
            "description": negative["model_hash_mismatch"]["description"],
        })

    if negative.get("unsupported_device_uri", {}).get("enabled", True):
        bad_uri = negative["unsupported_device_uri"]["uri"]
        with _backend_workdir(config.bundle, NPU_BACKEND_NAME) as wd:
            env = _llama_env(os.environ.copy(), bad_uri)
            cmd = [
                str(wd / "llama"), "cli",
                "-m", str(config.model_path),
                "-p", config.prompts["prefill"],
                "-n", "1",
                "--single-turn",
            ]
            proc = _run(cmd, wd, env, timeout=120.0)
            detected = proc.returncode != 0 or "failed to initialize" in proc.stderr.lower()
            checks.append({
                "name": "unsupported_device_uri",
                "detected": detected,
                "uri": bad_uri,
                "description": negative["unsupported_device_uri"]["description"],
            })

    all_detected = all(bool(c["detected"]) for c in checks)
    payload: dict[str, object] = {
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "verdict": "pass" if all_detected else "fail",
        "checks": checks,
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def write_combined_evidence(combined_path: Path) -> None:
    """Merge positive and negative evidence into the authoritative signoff JSON."""
    positive_path = combined_path.parent / "task-17-qwen3b-software-positive.json"
    negative_path = combined_path.parent / "task-17-qwen3b-software-negative.json"
    payload: dict[str, object] = {"positive": None, "negative": None}
    if positive_path.is_file():
        payload["positive"] = json.loads(positive_path.read_text())
    if negative_path.is_file():
        payload["negative"] = json.loads(negative_path.read_text())
    positive_ok = isinstance(payload["positive"], dict) and payload["positive"].get("verdict") == "pass"
    negative_ok = isinstance(payload["negative"], dict) and payload["negative"].get("verdict") == "pass"
    payload["verdict"] = "pass" if positive_ok and negative_ok else "fail"
    payload["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    combined_path.parent.mkdir(parents=True, exist_ok=True)
    combined_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
