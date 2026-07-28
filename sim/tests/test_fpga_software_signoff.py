#!/usr/bin/env python3
"""Tests for the FPGA software signoff runner.

Covers the NO-GO evidence path (current phase), config validation,
and preflight failure mode.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

# Ensure scripts/ is on sys.path for the runner import
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "fpga-target.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict[str, Any]:
    """Load the default FPGA target config."""
    return json.loads(DEFAULT_CONFIG.read_text())


def _run_runner(argv: list[str]) -> tuple[int, dict[str, Any]]:
    """Run the FPGA signoff runner with the given argv.

    The argv must NOT include the script name (it goes straight to
    argparse.parse_args).  Returns (exit_code, parsed_evidence).
    """
    from scripts.run_fpga_software_signoff import main

    # Capture evidence to a temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        ev_path = f.name

    try:
        # Replace --evidence path
        runner_argv: list[str] = []
        i = 0
        replaced = False
        while i < len(argv):
            if argv[i] == "--evidence" and i + 1 < len(argv):
                runner_argv.extend(["--evidence", ev_path])
                i += 2
                replaced = True
            else:
                runner_argv.append(argv[i])
                i += 1
        if not replaced:
            runner_argv.extend(["--evidence", ev_path])

        exit_code = main(runner_argv)
        evidence = json.loads(Path(ev_path).read_text())
    finally:
        try:
            Path(ev_path).unlink()
        except FileNotFoundError:
            pass

    return exit_code, evidence


# ---------------------------------------------------------------------------
# NO-GO path tests
# ---------------------------------------------------------------------------


def test_nogo_evidence_verdict_is_blocked() -> None:
    """The NO-GO evidence must have verdict == 'blocked'."""
    exit_code, evidence = _run_runner([
        "--config", str(DEFAULT_CONFIG),
        "--require-board",
        "--expect-no-board",
        "--evidence", "/tmp/test-evidence.json",
    ])
    assert exit_code == 0, f"Expected exit 0, got {exit_code}"
    assert evidence["verdict"] == "blocked", (
        f"Expected verdict='blocked', got '{evidence.get('verdict')}'"
    )


def test_nogo_evidence_has_nonempty_reason() -> None:
    """The NO-GO evidence must have a non-empty reason field."""
    _, evidence = _run_runner([
        "--config", str(DEFAULT_CONFIG),
        "--require-board",
        "--expect-no-board",
        "--evidence", "/tmp/test-evidence.json",
    ])
    reason = evidence.get("reason", "")
    assert isinstance(reason, str) and len(reason) > 0, (
        f"Expected non-empty reason, got '{reason}'"
    )


def test_nogo_evidence_contains_transport_readiness() -> None:
    """NO-GO evidence must document transport interface readiness from Todo 19."""
    _, evidence = _run_runner([
        "--config", str(DEFAULT_CONFIG),
        "--require-board",
        "--expect-no-board",
        "--evidence", "/tmp/test-evidence.json",
    ])
    tr = evidence.get("transport_interface_readiness")
    assert tr is not None, "Missing transport_interface_readiness"
    assert tr.get("status") == "ready", (
        f"Expected status='ready', got '{tr.get('status')}'"
    )
    # Must document all four transport paths
    paths = tr.get("paths", {})
    for key in ("vfio", "uio", "vendor_plugin", "fpga_none"):
        assert key in paths, f"Missing transport path: {key}"
        path_info = paths[key]
        assert path_info.get("ready") is True, (
            f"Transport path {key} should be ready=True"
        )
        assert path_info.get("validated") is True, (
            f"Transport path {key} should be validated=True"
        )


def test_nogo_evidence_contains_config_hash() -> None:
    """NO-GO evidence must include a deterministic config SHA-256."""
    _, evidence = _run_runner([
        "--config", str(DEFAULT_CONFIG),
        "--require-board",
        "--expect-no-board",
        "--evidence", "/tmp/test-evidence.json",
    ])
    tc = evidence.get("target_config", {})
    cfg_hash = tc.get("sha256", "")
    assert isinstance(cfg_hash, str) and len(cfg_hash) == 64, (
        f"Expected 64-char SHA-256, got '{cfg_hash}'"
    )


def test_nogo_evidence_has_task_20() -> None:
    """NO-GO evidence must reference task 20."""
    _, evidence = _run_runner([
        "--config", str(DEFAULT_CONFIG),
        "--require-board",
        "--expect-no-board",
        "--evidence", "/tmp/test-evidence.json",
    ])
    assert evidence.get("task") == 20, (
        f"Expected task=20, got {evidence.get('task')}"
    )


def test_nogo_evidence_has_deferred_items() -> None:
    """NO-GO evidence must list deferred items."""
    _, evidence = _run_runner([
        "--config", str(DEFAULT_CONFIG),
        "--require-board",
        "--expect-no-board",
        "--evidence", "/tmp/test-evidence.json",
    ])
    deferred = evidence.get("deferred_items", [])
    assert isinstance(deferred, list) and len(deferred) > 0, (
        "Expected non-empty deferred_items list"
    )


# ---------------------------------------------------------------------------
# Config validation tests
# ---------------------------------------------------------------------------


def test_valid_config_passes_validation() -> None:
    """Valid fpga-target.json should pass config validation."""
    from scripts.run_fpga_software_signoff import _validate_target_config
    cfg = _load_config()
    errors = _validate_target_config(cfg, str(DEFAULT_CONFIG))
    assert errors == [], f"Unexpected validation errors: {errors}"


def test_config_hash_is_deterministic() -> None:
    """Two SHA-256 hashes of the same config must match."""
    from scripts.run_fpga_software_signoff import _config_hash
    cfg = _load_config()
    h1 = _config_hash(cfg)
    h2 = _config_hash(cfg)
    assert h1 == h2, "Config hash is not deterministic"
    assert len(h1) == 64, "Hash should be 64 hex chars"


def test_config_hash_changes_with_mutation() -> None:
    """Mutating the config must change the hash."""
    from scripts.run_fpga_software_signoff import _config_hash
    cfg = _load_config()
    h1 = _config_hash(cfg)
    cfg["abi"]["abi_minor"] = 99
    h2 = _config_hash(cfg)
    assert h1 != h2, "Hash must change when config is mutated"


def test_config_validation_rejects_missing_manifest() -> None:
    """Missing manifest_version should produce an error."""
    from scripts.run_fpga_software_signoff import _validate_target_config
    cfg = _load_config()
    del cfg["manifest_version"]
    errors = _validate_target_config(cfg, str(DEFAULT_CONFIG))
    assert any("manifest_version" in e for e in errors)


def test_config_validation_rejects_missing_bitstream_hash() -> None:
    """Missing or invalid bitstream.sha256 should produce an error."""
    from scripts.run_fpga_software_signoff import _validate_target_config
    cfg = _load_config()
    cfg["bitstream"]["sha256"] = ""  # wrong length
    errors = _validate_target_config(cfg, str(DEFAULT_CONFIG))
    assert any("bitstream.sha256" in e for e in errors)


def test_config_validation_rejects_missing_bar_map() -> None:
    """Missing bar_map should produce an error."""
    from scripts.run_fpga_software_signoff import _validate_target_config
    cfg = _load_config()
    del cfg["bar_map"]
    errors = _validate_target_config(cfg, str(DEFAULT_CONFIG))
    assert any("bar_map" in e for e in errors)


# ---------------------------------------------------------------------------
# Preflight path tests
# ---------------------------------------------------------------------------


def test_preflight_fails_without_expect_no_board() -> None:
    """Without --expect-no-board and --require-board, preflight should fail."""
    exit_code, evidence = _run_runner([
        "--config", str(DEFAULT_CONFIG),
        "--require-board",
        "--evidence", "/tmp/test-evidence.json",
    ])
    assert exit_code == 1, (
        f"Expected exit 1 for preflight failure, got {exit_code}"
    )
    assert evidence["verdict"] == "fail", (
        f"Expected verdict='fail', got '{evidence.get('verdict')}'"
    )
    assert "board_not_found" in evidence.get("reason", ""), (
        f"Expected reason 'board_not_found', got '{evidence.get('reason')}'"
    )


# ---------------------------------------------------------------------------
# Config-only path test
# ---------------------------------------------------------------------------


def test_config_only_validation_passes() -> None:
    """Running without --require-board or --expect-no-board validates config only."""
    exit_code, evidence = _run_runner([
        "--config", str(DEFAULT_CONFIG),
        "--evidence", "/tmp/test-evidence.json",
    ])
    assert exit_code == 0, f"Expected exit 0, got {exit_code}"
    assert evidence["verdict"] == "pass", (
        f"Expected verdict='pass', got '{evidence.get('verdict')}'"
    )
    assert evidence["reason"] == "config_validated"
