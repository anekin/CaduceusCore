#!/usr/bin/env python3
"""Public re-exports for the Qwen2.5-3B software signoff runner."""

from __future__ import annotations

from signoff.qwen3b_signoff_config import (
    BackendBundle,
    SignoffConfig,
    SignoffError,
    compute_backend_hash,
    load_config,
    verify_model_hash,
)
from signoff.qwen3b_signoff_gates import (
    GateResult,
    gate_cpu_fallback_mixed_graph,
    gate_decode_tokens,
    gate_full_shape_blk0,
    gate_supported_single_ops,
)
from signoff.qwen3b_signoff_runner import (
    run_negative_signoff,
    run_positive_signoff,
    write_combined_evidence,
)
from signoff.qwen3b_signoff_io import (
    _backend_workdir,
    _compare_hidden,
    _llama_env,
    _parse_generated_text,
    managed_device_server,
)

__all__ = [
    "BackendBundle",
    "GateResult",
    "SignoffConfig",
    "SignoffError",
    "_backend_workdir",
    "_compare_hidden",
    "_llama_env",
    "_parse_generated_text",
    "compute_backend_hash",
    "gate_cpu_fallback_mixed_graph",
    "gate_decode_tokens",
    "gate_full_shape_blk0",
    "gate_supported_single_ops",
    "load_config",
    "managed_device_server",
    "run_negative_signoff",
    "run_positive_signoff",
    "verify_model_hash",
    "write_combined_evidence",
]
