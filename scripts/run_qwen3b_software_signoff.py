#!/usr/bin/env python3
"""
Qwen2.5-3B llama.cpp functional software signoff runner.

Usage:
    PYTHONPATH=sim python3 scripts/run_qwen3b_software_signoff.py \
        --positive --device mock://
    PYTHONPATH=sim python3 scripts/run_qwen3b_software_signoff.py --negative
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT / "sim"))

from signoff.qwen3b_signoff import (  # noqa: E402
    SignoffConfig,
    SignoffError,
    load_config,
    run_negative_signoff,
    run_positive_signoff,
    write_combined_evidence,
)

_DEFAULT_CONFIG: Path = _PROJECT / "config" / "qwen3b-signoff.json"
_EVIDENCE_DIR: Path = _PROJECT / ".omo" / "evidence"


def _env_device() -> str:
    return os.environ.get("CADUCEUS_DEVICE", "mock://")


def _positive_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--device", default=_env_device(),
        help="Caduceus device URI for the NPU backend (default: $CADUCEUS_DEVICE or mock://)",
    )
    parser.add_argument(
        "--evidence", default=str(_EVIDENCE_DIR / "task-17-qwen3b-software-positive.json"),
        help="Path for the positive evidence JSON file",
    )
    parser.add_argument(
        "--gate", default=None, dest="gate_filter",
        help="Run only the named gate (e.g. single_decode_token, full_shape_blk0). "
             "When omitted, all enabled gates run.",
    )


def _negative_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--device", default=_env_device(),
        help="Caduceus device URI for the NPU backend (default: $CADUCEUS_DEVICE or mock://)",
    )
    parser.add_argument(
        "--evidence", default=str(_EVIDENCE_DIR / "task-17-qwen3b-software-negative.json"),
        help="Path for the negative evidence JSON file",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Qwen2.5-3B llama.cpp functional software signoff runner"
    )
    parser.add_argument(
        "--config", default=str(_DEFAULT_CONFIG),
        help="Path to config/qwen3b-signoff.json",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pos = sub.add_parser("positive", help="Run the five positive software gates")
    _positive_args(pos)

    neg = sub.add_parser("negative", help="Run anti-vacuous negative checks")
    _negative_args(neg)

    args = parser.parse_args(argv)
    config = load_config(Path(args.config))

    combined = _EVIDENCE_DIR / "task-17-qwen3b-software.json"

    if args.command == "positive":
        payload = run_positive_signoff(
            config, args.device, Path(args.evidence),
            gate_filter=getattr(args, "gate_filter", None),
        )
        write_combined_evidence(combined)
        print(f"Positive signoff verdict: {payload['verdict']}")
        print(f"Evidence written to: {args.evidence}")
        return 0 if payload["verdict"] == "pass" else 1

    if args.command == "negative":
        payload = run_negative_signoff(config, Path(args.evidence), args.device)
        write_combined_evidence(combined)
        print(f"Negative signoff verdict: {payload['verdict']}")
        print(f"Evidence written to: {args.evidence}")
        return 0 if payload["verdict"] == "pass" else 1

    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SignoffError as exc:
        print(f"SIGNOFF ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
