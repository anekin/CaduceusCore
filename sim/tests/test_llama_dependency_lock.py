"""
Test suite for the llama.cpp dependency lock and fetch infrastructure.

Verifies:
  1. Lock file exists and is valid JSON with required fields
  2. Commit hash matches the expected pinned value
  3. Changing lock commit without refreshes causes check failure (negative test)
  4. Fetch script exits cleanly in --check mode when lock matches

See: .omo/plans/func-model-soc-software-stack.md Todo 5
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOCK_PATH = PROJECT_ROOT / "deps" / "llama-cpp.lock"
FETCH_SCRIPT = PROJECT_ROOT / "scripts" / "fetch_llama_cpp.py"
PINNED_COMMIT = "88b47a755c72fed4b22fba0fd262e2d7b7d01583"


# ── Fixtures ──────────────────────────────────────────────────────────

def _load_lock():
    """Load and return the lock file as a dict."""
    return json.loads(LOCK_PATH.read_text())


# ── Happy-path tests ──────────────────────────────────────────────────

def test_lock_file_exists():
    """The dependency lock file exists at the expected path."""
    assert LOCK_PATH.exists(), f"Lock file not found: {LOCK_PATH}"


def test_lock_file_is_valid_json():
    """The lock file contains valid JSON."""
    data = _load_lock()
    assert isinstance(data, dict), "Lock file must be a JSON object"


def test_lock_has_required_fields():
    """The lock file contains all required metadata fields."""
    data = _load_lock()
    required = ["name", "repository", "commit", "retrieval_method", "license"]
    for field in required:
        assert field in data, f"Missing required field: {field}"
        assert isinstance(data[field], str), f"Field {field} must be a string"
        assert data[field], f"Field {field} must not be empty"


def test_lock_commit_is_pinned():
    """The lock file records the exact pinned commit, not a branch ref."""
    data = _load_lock()
    commit = data["commit"]
    # Must be a full 40-hex-char SHA, not a branch name or tag
    assert len(commit) == 40, f"Commit must be a full 40-char SHA: {commit}"
    assert all(c in "0123456789abcdef" for c in commit), \
        f"Commit must be lowercase hex: {commit}"
    assert commit == PINNED_COMMIT, \
        f"Lock commit does not match pinned: expected {PINNED_COMMIT}, got {commit}"


def test_lock_does_not_track_branch():
    """The repository URL must not contain a branch or tag reference."""
    data = _load_lock()
    repo = data["repository"]
    assert "@" not in repo, f"Repository URL must not pin a branch/ref: {repo}"
    assert repo.startswith("https://github.com/"), \
        f"Repository must be a GitHub HTTPS URL: {repo}"


def test_lock_license_is_mit():
    """llama.cpp is MIT-licensed."""
    data = _load_lock()
    assert data["license"] == "MIT", \
        f"Expected MIT license, got: {data['license']}"


def test_lock_retrieval_is_git_clone():
    """Retrieval method must be a standard git clone."""
    data = _load_lock()
    assert "git clone" in data["retrieval_method"], \
        f"Retrieval method must be git clone based: {data['retrieval_method']}"


def test_fetch_script_exists():
    """The reproducible fetch script exists."""
    assert FETCH_SCRIPT.exists(), f"Fetch script not found: {FETCH_SCRIPT}"


def test_fetch_script_is_executable_or_runnable():
    """The fetch script can be invoked by python."""
    assert FETCH_SCRIPT.suffix == ".py", "Fetch script must be a Python file"


def test_ggml_npu_cmake_exists():
    """The ggml-npu CMakeLists.txt exists at the local backend source."""
    cmake = PROJECT_ROOT / "ggml-npu" / "CMakeLists.txt"
    assert cmake.exists(), f"ggml-npu/CMakeLists.txt not found: {cmake}"


def test_ggml_npu_source_exists():
    """The ggml-npu source files exist."""
    cpp = PROJECT_ROOT / "ggml-npu" / "ggml-npu.cpp"
    hdr = PROJECT_ROOT / "ggml-npu" / "ggml-npu.h"
    assert cpp.exists(), f"ggml-npu/ggml-npu.cpp not found: {cpp}"
    assert hdr.exists(), f"ggml-npu/ggml-npu.h not found: {hdr}"


def test_fetch_script_check_exits_zero_when_commit_matches():
    """fetch_llama_cpp.py --check exits 0 when lock matches state, non-zero when mismatched.

    This test verifies that --check correctly validates or rejects the current state.
    If the pinned commit is checked out, exits 0; if not, exits non-zero so the
    user knows to re-fetch. Both outcomes are correct behavior.
    """
    third_party = PROJECT_ROOT / "third_party" / "llama.cpp"
    if not third_party.exists():
        pytest.skip("third_party/llama.cpp not cloned — run fetch_llama_cpp.py first")
    if not (third_party / ".git").exists():
        pytest.skip("third_party/llama.cpp is not a git checkout")

    result = subprocess.run(
        [sys.executable, str(FETCH_SCRIPT), "--lock", str(LOCK_PATH), "--check"],
        capture_output=True, text=True, timeout=30,
    )

    # Determine whether the commit matches and assert the expected exit code
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(third_party),
        capture_output=True, text=True, timeout=10,
    )
    if commit.returncode == 0 and commit.stdout.strip() == PINNED_COMMIT:
        assert result.returncode == 0, \
            f"--check should exit 0 when commit matches, got {result.returncode}"
    else:
        assert result.returncode != 0, \
            f"--check should exit non-zero when commit does not match, got {result.returncode}"


# ── Negative tests ────────────────────────────────────────────────────

def test_rejects_wrong_commit():
    """Changing the lock commit without refreshing metadata causes check failure.

    This is the canonical negative test: mutate the commit hash in a lock copy,
    then verify --check detects it and exits non-zero.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_lock = Path(tmp_dir) / "llama-cpp.lock"
        data = _load_lock()

        # Mutate: replace the pinned commit with a wrong one
        data["commit"] = "0000000000000000000000000000000000000000"
        tmp_lock.write_text(json.dumps(data, indent=4))

        # The check should fail because the commit doesn't match
        result = subprocess.run(
            [sys.executable, str(FETCH_SCRIPT), "--lock", str(tmp_lock), "--check"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode != 0, \
            "Expected non-zero exit when commit is wrong, but got 0"
        assert "FAIL" in (result.stdout + result.stderr), \
            "Expected FAIL message in output for wrong commit"


def test_rejects_missing_lock():
    """Passing a nonexistent lock file causes non-zero exit."""
    result = subprocess.run(
        [sys.executable, str(FETCH_SCRIPT), "--lock", "/nonexistent/path.lock", "--check"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0, \
        "Expected non-zero exit for missing lock file"


def test_rejects_malformed_lock():
    """A lock file containing invalid JSON causes non-zero exit."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_lock = Path(tmp_dir) / "broken.lock"
        tmp_lock.write_text("{not valid json")

        result = subprocess.run(
            [sys.executable, str(FETCH_SCRIPT), "--lock", str(tmp_lock), "--check"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0, \
            "Expected non-zero exit for malformed lock JSON"


def test_rejects_missing_required_field():
    """A lock file missing a required field (e.g., commit) causes check failure."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_lock = Path(tmp_dir) / "partial.lock"
        data = _load_lock()
        del data["commit"]
        tmp_lock.write_text(json.dumps(data, indent=4))

        result = subprocess.run(
            [sys.executable, str(FETCH_SCRIPT), "--lock", str(tmp_lock), "--check"],
            capture_output=True, text=True, timeout=10,
        )
        # The check will fail because commit verification can't happen
        # (verify_state calls verify_commit on the nonexistent checkout)
        assert result.returncode != 0, \
            "Expected non-zero exit for lock missing commit field"
