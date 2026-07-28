#!/usr/bin/env python3
"""Typed configuration and provenance helpers for the Qwen2.5-3B software signoff."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
CPU_BACKEND_NAME: Final = "CPU"
NPU_BACKEND_NAME: Final = "NPU"


class SignoffError(Exception):
    """Raised when a signoff gate cannot be satisfied."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class BackendBundle:
    """Paths to the llama.cpp binaries and backend shared objects."""

    llama_cli: Path
    test_backend_ops: Path
    dump_hidden_states: Path
    npu_so: Path
    cpu_so_glob: str


@dataclass(frozen=True, slots=True)
class SignoffConfig:
    """Typed subset of config/qwen3b-signoff.json used by the runner."""

    model_path: Path
    model_sha256: str
    llama_commit: str
    abi_version: str
    bundle: BackendBundle
    prompts: dict[str, str]
    seed: int
    temperature: float
    top_k: int
    top_p: float
    hidden_max_abs_diff: float
    hidden_cos_sim_min: float
    gates: dict[str, dict]
    negative_checks: dict[str, dict]


def load_config(path: Path) -> SignoffConfig:
    """Parse the signoff manifest into a typed value."""
    raw = json.loads(path.read_text())
    model = raw["model"]
    paths = raw["paths"]
    det = raw["determinism"]
    tol = raw["tolerances"]
    return SignoffConfig(
        model_path=Path(os.environ.get("QWEN3B_GGUF", model["default_path"])),
        model_sha256=model["sha256"],
        llama_commit=raw["llama_cpp"]["commit"],
        abi_version=raw["llama_cpp"]["abi_version"],
        bundle=BackendBundle(
            llama_cli=REPO_ROOT / paths["llama_cli"],
            test_backend_ops=REPO_ROOT / paths["test_backend_ops"],
            dump_hidden_states=REPO_ROOT / paths["dump_hidden_states"],
            npu_so=REPO_ROOT / paths["npu_backend_so"],
            cpu_so_glob=paths["cpu_backend_so_glob"],
        ),
        prompts=raw["prompts"],
        seed=det["seed"],
        temperature=det["temperature"],
        top_k=det["top_k"],
        top_p=det["top_p"],
        hidden_max_abs_diff=tol["hidden_max_abs_diff"],
        hidden_cos_sim_min=tol["hidden_cos_sim_min"],
        gates=raw["gates"],
        negative_checks=raw["negative_checks"],
    )


def _file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    hasher.update(path.read_bytes())
    return hasher.hexdigest()


def verify_model_hash(model_path: Path, expected: str) -> None:
    """Fail fast if the pinned GGUF model does not match the manifest hash."""
    if not model_path.is_file():
        raise SignoffError(f"model not found: {model_path}")
    actual = _file_hash(model_path)
    if actual != expected:
        raise SignoffError(
            f"model hash mismatch: expected {expected[:16]}... got {actual[:16]}..."
        )


def compute_backend_hash() -> str:
    """Fingerprint the NPU backend sources that determine software behavior."""
    files = [
        REPO_ROOT / "ggml-npu" / "ggml-npu.cpp",
        REPO_ROOT / "ggml-npu" / "ggml-npu.h",
        REPO_ROOT / "ggml-npu" / "CMakeLists.txt",
        REPO_ROOT / "software" / "src" / "transport_fm.cpp",
    ]
    hasher = hashlib.sha256()
    for fp in files:
        hasher.update(fp.read_bytes())
    return hasher.hexdigest()[:32]
