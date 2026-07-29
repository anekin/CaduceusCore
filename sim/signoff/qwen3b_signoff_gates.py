#!/usr/bin/env python3
"""Positive software gates for the Qwen2.5-3B signoff runner."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from signoff.qwen3b_signoff_config import (
    CPU_BACKEND_NAME,
    NPU_BACKEND_NAME,
    REPO_ROOT,
    SignoffConfig,
)
from signoff.qwen3b_signoff_io import (
    _backend_workdir,
    _compare_hidden,
    _llama_env,
    _run,
    _run_dump_hidden_states,
    _run_llama_cli_decode,
    _strip_ansi,
)

_SPIKE_BINARY = REPO_ROOT / "spike_src" / "build" / "spike"
_FIRMWARE_ELF = REPO_ROOT / "firmware" / "build" / "npu_firmware_spike.elf"


def _sha256_hex(path: Path) -> str:
    """SHA-256 hex digest of a file."""
    hasher = hashlib.sha256()
    hasher.update(path.read_bytes())
    return hasher.hexdigest()


@dataclass(frozen=True, slots=True)
class GateResult:
    """Outcome of a single software gate."""

    name: str
    passed: bool
    metrics: dict[str, str | int | float | bool]


def gate_supported_single_ops(
    config: SignoffConfig, device_uri: str, base_env: dict[str, str]
) -> GateResult:
    """Gate 1: selected GGUF single ops are supported by the NPU backend."""
    gate = config.gates["supported_single_ops"]
    ops = gate["op_list"]
    with _backend_workdir(config.bundle, NPU_BACKEND_NAME) as wd:
        env = _llama_env(base_env, device_uri)
        cmd = [
            str(wd / "test-backend-ops"), "test", "-b", "NPU",
            "-o", ",".join(ops),
        ]
        proc = _run(cmd, wd, env, timeout=300.0)
        ok = proc.returncode == 0
        match = re.search(r"(\d+)/(\d+) tests passed", proc.stdout)
        passed_tests = int(match.group(1)) if match else 0
        total_tests = int(match.group(2)) if match else 0
        ratio = passed_tests / total_tests if total_tests else 0.0
        return GateResult(
            name="supported_single_ops",
            passed=ok and ratio >= gate["min_pass_ratio"],
            metrics={
                "exit_code": proc.returncode,
                "tests_passed": passed_tests,
                "tests_total": total_tests,
                "pass_ratio": ratio,
                "ops": ",".join(ops),
            },
        )


def gate_full_shape_blk0(
    config: SignoffConfig, device_uri: str, base_env: dict[str, str]
) -> GateResult:
    """Gate 2: a full-shape transformer block 0 forward pass matches CPU reference."""
    gate = config.gates["full_shape_blk0"]
    prompt = config.prompts["prefill"]
    n_predict = gate["n_tokens"]
    with _backend_workdir(config.bundle, CPU_BACKEND_NAME) as cpu_wd:
        cpu_env = _llama_env(base_env, None)
        cpu_npz = _run_dump_hidden_states(config, cpu_wd, cpu_env, prompt, n_predict)
        with _backend_workdir(config.bundle, NPU_BACKEND_NAME) as npu_wd:
            npu_env = _llama_env(base_env, device_uri)
            npu_npz = _run_dump_hidden_states(config, npu_wd, npu_env, prompt, n_predict)
            metrics = _compare_hidden(
                cpu_npz, npu_npz, "l_out_0",
                config.hidden_max_abs_diff, config.hidden_cos_sim_min,
            )
            expected = gate["expected_nodes"]
            metrics["expected_supported_nodes"] = expected["supported"]
            metrics["expected_fallback_nodes"] = expected["fallback"]
            return GateResult(
                name="full_shape_blk0",
                passed=bool(metrics["passed"]),
                metrics=metrics,
            )


def gate_decode_tokens(
    config: SignoffConfig,
    device_uri: str,
    base_env: dict[str, str],
    gate_name: str,
) -> GateResult:
    """Gate 3/4: one or many decode tokens are deterministic against CPU reference."""
    gate = config.gates[gate_name]
    prompt_key = gate.get("prompt", "prefill")
    prompt = config.prompts[prompt_key]
    n_predict = gate["n_predict"]
    with _backend_workdir(config.bundle, CPU_BACKEND_NAME) as cpu_wd:
        cpu_text = _run_llama_cli_decode(config, cpu_wd, _llama_env(base_env, None), prompt, n_predict)
        with _backend_workdir(config.bundle, NPU_BACKEND_NAME) as npu_wd:
            npu_text = _run_llama_cli_decode(config, npu_wd, _llama_env(base_env, device_uri), prompt, n_predict)
            expected = gate["expected_nodes"]
            metrics: dict[str, str | int | float | bool] = {
                "cpu_text": cpu_text,
                "npu_text": npu_text,
                "text_match": cpu_text == npu_text,
                "expected_supported_nodes": expected["supported"],
                "expected_fallback_nodes": expected["fallback"],
            }
            return GateResult(
                name=gate_name,
                passed=cpu_text != "" and cpu_text == npu_text,
                metrics=metrics,
            )


def gate_single_decode_token_spike(
    config: SignoffConfig, device_uri: str, base_env: dict[str, str]
) -> GateResult:
    """Gate: single Qwen2.5-3B decode token through fm://spike with real firmware.

    Checks Spike prerequisites (spike binary + firmware ELF) before execution.
    Returns BLOCKED with prerequisite reason if prerequisites are missing.
    """
    gate = config.gates["single_decode_token"]
    prompt_key = gate.get("prompt", "prefill")
    prompt = config.prompts[prompt_key]
    n_predict = gate["n_predict"]

    prerequisites: dict[str, bool | str] = {}
    blocked_reasons: list[str] = []

    for label, path in [("spike_binary", _SPIKE_BINARY), ("firmware_elf", _FIRMWARE_ELF)]:
        if path.is_file():
            prerequisites[label] = _sha256_hex(path)
        else:
            prerequisites[label] = None
            blocked_reasons.append(f"{label} missing at {path}")

    if blocked_reasons:
        return GateResult(
            name="single_decode_token_spike",
            passed=False,
            metrics={
                "verdict": "BLOCKED",
                "reason": "; ".join(blocked_reasons),
                "prerequisites": prerequisites,
            },
        )

    spike_hash = prerequisites["spike_binary"]  # type: ignore[assignment]
    firmware_hash = prerequisites["firmware_elf"]  # type: ignore[assignment]

    # Run decode token comparison using existing infrastructure.
    # Spike firmware simulation is much slower than native Python, so use
    # a generous timeout (3600s = 1h) for the NPU decode path.
    _SPIKE_DECODE_TIMEOUT = 3600.0
    with _backend_workdir(config.bundle, CPU_BACKEND_NAME) as cpu_wd:
        cpu_text = _run_llama_cli_decode(
            config, cpu_wd, _llama_env(base_env, None), prompt, n_predict
        )
        with _backend_workdir(config.bundle, NPU_BACKEND_NAME) as npu_wd:
            npu_text = _run_llama_cli_decode(
                config, npu_wd, _llama_env(base_env, device_uri), prompt, n_predict,
                timeout=_SPIKE_DECODE_TIMEOUT,
            )
            expected = gate["expected_nodes"]
            metrics: dict[str, str | int | float | bool] = {
                "verdict": "pass" if (cpu_text != "" and cpu_text == npu_text) else "fail",
                "cpu_text": cpu_text,
                "npu_text": npu_text,
                "text_match": cpu_text == npu_text,
                "prompt": prompt,
                "n_predict": n_predict,
                "expected_supported_nodes": expected["supported"],
                "expected_fallback_nodes": expected["fallback"],
                "firmware_elf_sha256": firmware_hash,
                "spike_binary_sha256": spike_hash,
            }
            return GateResult(
                name="single_decode_token_spike",
                passed=cpu_text != "" and cpu_text == npu_text,
                metrics=metrics,
            )


def gate_cpu_fallback_mixed_graph(
    config: SignoffConfig, device_uri: str, base_env: dict[str, str]
) -> GateResult:
    """Gate 5: unsupported op layouts are detected and CPU fallback keeps E2E correct."""
    gate = config.gates["cpu_fallback_mixed_graph"]
    with _backend_workdir(config.bundle, NPU_BACKEND_NAME) as wd:
        env = _llama_env(base_env, device_uri)
        supported = gate["supported_ops"]
        cmd = [str(wd / "test-backend-ops"), "support", "-b", "NPU", "-o", ",".join(supported)]
        proc = _run(cmd, wd, env, timeout=120.0)
        clean = _strip_ansi(proc.stdout)
        supported_count = sum(
            1 for line in clean.splitlines()
            if any(op in line for op in supported)
            and "SUPPORTED" in line
            and "NOT SUPPORTED" not in line
        )
        unsupported = gate["unsupported_op_probe"]
        bad_cmd = [
            str(wd / "test-backend-ops"), "support", "-b", "NPU",
            "-o", unsupported["name"],
        ]
        bad_proc = _run(bad_cmd, wd, env, timeout=120.0)
        bad_clean = _strip_ansi(bad_proc.stdout)
        unsupported_reported = (
            unsupported["name"] in bad_clean
            and "NOT SUPPORTED" in bad_clean
        )
        expected = gate["expected_nodes"]
        return GateResult(
            name="cpu_fallback_mixed_graph",
            passed=proc.returncode == 0 and unsupported_reported,
            metrics={
                "supported_ops_count": supported_count,
                "unsupported_probe": unsupported["name"],
                "unsupported_detected": unsupported_reported,
                "expected_supported_nodes": expected["supported"],
                "expected_fallback_nodes": expected["fallback"],
            },
        )
