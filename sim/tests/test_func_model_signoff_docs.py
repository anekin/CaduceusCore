"""Pytest wrapper for the signoff documentation consistency checker."""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKER_PATH = _REPO_ROOT / "scripts" / "check_func_model_signoff_docs.py"


def test_checker_cli_help() -> None:
    """Verify checker CLI is functional (--help exits 0)."""
    result = subprocess.run(
        ["python3", str(_CHECKER_PATH), "--help"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0


def test_check_scaled_labels_pass() -> None:
    """Verify --check-scaled-labels exits 0 on current docs."""
    result = subprocess.run(
        ["python3", str(_CHECKER_PATH), "--check-scaled-labels"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Checker failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
