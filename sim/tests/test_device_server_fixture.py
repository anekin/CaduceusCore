#!/usr/bin/env python3
"""Tests for the managed device_server lifecycle fixture.

The fixture must start/stop `sim.device_server` itself for fm:// URIs so the
signoff runner no longer requires a manually pre-started server (plan todo A5).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from signoff.device_server_fixture import is_spike_device, managed_device_server
from signoff.qwen3b_signoff_config import REPO_ROOT
from signoff.qwen3b_signoff import load_config

_DEFAULT_CONFIG = REPO_ROOT / "config" / "qwen3b-signoff.json"


def _socket_reachable(sock_path: Path) -> bool:
    """True when a Unix socket path accepts a connection."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(0.2)
            sock.connect(str(sock_path))
            return True
        finally:
            sock.close()
    except OSError:
        return False


def _leftover_socks(marker: str) -> list[Path]:
    return list(Path("/tmp").glob(f"{marker}_{os.getpid()}*.sock"))


def _signoff_prereqs() -> bool:
    """True when the Qwen3B GGUF model and llama binaries are available."""
    cfg = load_config(_DEFAULT_CONFIG)
    return (
        cfg.model_path.is_file()
        and cfg.bundle.llama_cli.is_file()
        and cfg.bundle.dump_hidden_states.is_file()
        and cfg.bundle.npu_so.is_file()
    )


# ── Fixture unit tests ─────────────────────────────────────────────────────


def test_managed_fm_python_server_starts_and_stops() -> None:
    """The fixture starts the server and tears it down on exit."""
    with managed_device_server("fm://python", timeout=10.0) as uri:
        assert uri.startswith("fm://unix?path=")
        sock_path = Path(uri.split("=", 1)[1])
        assert _socket_reachable(sock_path), f"socket {sock_path} not reachable"
    assert not sock_path.exists(), "socket must be removed on exit"
    assert _leftover_socks("caduceus_fm_python") == [], "orphan socket left behind"


def test_second_instantiation_no_address_in_use() -> None:
    """A second fixture run must not hit 'address already in use'."""
    with managed_device_server("fm://python", timeout=10.0) as uri1:
        p1 = Path(uri1.split("=", 1)[1])
        assert _socket_reachable(p1)
    with managed_device_server("fm://python", timeout=10.0) as uri2:
        p2 = Path(uri2.split("=", 1)[1])
        assert _socket_reachable(p2), "second server failed to bind"
    assert not p1.exists() and not p2.exists()


def test_mock_passthrough_starts_no_server() -> None:
    """mock:// must not be managed by the fixture."""
    with managed_device_server("mock://", timeout=1.0) as uri:
        assert uri == "mock://"
    assert _leftover_socks("caduceus_fm_python") == []
    assert _leftover_socks("caduceus_fm_spike") == []


def test_resolved_unix_uri_passthrough() -> None:
    """An already-resolved fm://unix URI is yielded unchanged."""
    with managed_device_server("fm://unix?path=/tmp/some.sock") as uri:
        assert uri == "fm://unix?path=/tmp/some.sock"


def test_timeout_raises_runtime_error_with_logs() -> None:
    """A server that cannot come up in time raises RuntimeError."""
    with pytest.raises(RuntimeError, match="did not become reachable"):
        with managed_device_server("fm://python", timeout=0.005):
            pass  # pragma: no cover
    assert _leftover_socks("caduceus_fm_python") == []


def test_is_spike_device() -> None:
    assert is_spike_device("fm://spike")
    assert is_spike_device("fm://unix?path=/tmp/caduceus_fm_spike_123.sock")
    assert not is_spike_device("fm://python")
    assert not is_spike_device("fm://")
    assert not is_spike_device("mock://")
    assert not is_spike_device("fm://unix?path=/tmp/caduceus_fm_python_1.sock")


# ── Acceptance characterization (baseline: fails without a pre-started server) ──


@pytest.mark.slow
@pytest.mark.skipif(
    not _signoff_prereqs(),
    reason="Qwen3B GGUF model or llama.cpp binaries not available",
)
def test_full_shape_blk0_passes_without_preserving_server(tmp_path: Path) -> None:
    """The signoff script must pass full_shape_blk0 on fm://python with no
    manually pre-started device_server (plan A5 acceptance)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = "sim:gen"
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "run_qwen3b_software_signoff.py"),
        "positive",
        "--device", "fm://python",
        "--gate", "full_shape_blk0",
        "--evidence", str(tmp_path / "positive.json"),
    ]
    proc = subprocess.run(
        cmd, cwd=str(REPO_ROOT), env=env,
        capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, (
        f"signoff exit={proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout[-2000:]}\n--- stderr ---\n{proc.stderr[-2000:]}"
    )
    assert "full_shape_blk0" in proc.stdout
    assert _leftover_socks("caduceus_fm_python") == []
