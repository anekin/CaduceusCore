#!/usr/bin/env python3
"""Managed `sim.device_server` lifecycle fixture for signoff gates.

The signoff runner must start and stop the FuncModel device server itself so
that `fm://python` gates work without a manually pre-started
`python -m sim.device_server` (plan todo A5).
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

from signoff.qwen3b_signoff_config import REPO_ROOT

# fm:// transport URIs whose device server this fixture owns. `fm://python`
# (alias `fm://`) is the plain Python FuncModel server; `fm://spike` is the
# same server backed by the Spike firmware model.
_MANAGED_URIS: frozenset[str] = frozenset({"fm://", "fm://python", "fm://spike"})

_SPIKE_SOCK_PREFIX = "caduceus_fm_spike_"
_PYTHON_SOCK_PREFIX = "caduceus_fm_python_"

# Each managed server gets its own socket, so two fixtures never collide and a
# stale file from a crashed server cannot break a fresh start.
_SOCK_DIR = Path("/tmp")


def is_spike_device(device_url: str) -> bool:
    """True when *device_url* targets a Spike-backed FM device server.

    Matches both the un-resolved ``fm://spike`` form and the resolved
    ``fm://unix?path=/tmp/caduceus_fm_spike_<pid>.sock`` form.
    """
    return device_url == "fm://spike" or (
        device_url.startswith("fm://unix?path=")
        and _SPIKE_SOCK_PREFIX in device_url
    )


def _socket_path(spike: bool) -> Path:
    prefix = _SPIKE_SOCK_PREFIX if spike else _PYTHON_SOCK_PREFIX
    return _SOCK_DIR / f"{prefix}{os.getpid()}.sock"


def _socket_reachable(sock_path: Path) -> bool:
    """True when the Unix socket accepts a connection."""
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


def _stop_proc(proc: subprocess.Popen[str]) -> None:
    """Terminate the server process and wait for it to exit."""
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=10.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5.0)


def _log_tail(log_path: Path, n: int = 10) -> str:
    """Last *n* lines of the captured server log, for failure diagnostics."""
    try:
        lines = log_path.read_text().splitlines()
    except OSError:
        return "<no server log captured>"
    return "\n".join(lines[-n:])


@contextlib.contextmanager
def managed_device_server(
    device_url: str = "fm://python", timeout: float = 5.0
) -> Iterator[str]:
    """Start/stop `sim.device_server` for fm:// URIs and yield the transport URI.

    For ``fm://python``, ``fm://``, and ``fm://spike`` the device server is
    launched as a subprocess on a dedicated Unix socket and the yielded URI is
    the explicit ``fm://unix?path=...`` form, so the C runtime connects to
    exactly this socket. Every other URI (``mock://``, an already-resolved
    ``fm://unix?path=...``, ...) is yielded unchanged and no process is
    spawned.

    Raises RuntimeError (including the last captured log lines) when the
    socket is not reachable within *timeout* seconds. The server process is
    always terminated and its socket and log removed, on success and failure.
    """
    if device_url not in _MANAGED_URIS:
        yield device_url
        return

    spike = is_spike_device(device_url)
    sock_path = _socket_path(spike)
    log_path = _SOCK_DIR / f"{_SPIKE_SOCK_PREFIX if spike else _PYTHON_SOCK_PREFIX}{os.getpid()}.log"

    cmd = [sys.executable, "-m", "sim.device_server", "--sock", str(sock_path)]
    if spike:
        cmd.append("--spike")
    env = os.environ.copy()
    env["PYTHONPATH"] = "sim:gen"

    with open(log_path, "w") as log_file:
        proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    _stop_proc(proc)
                    raise RuntimeError(
                        f"device_server exited early (rc={proc.returncode})\n"
                        f"{_log_tail(log_path)}"
                    )
                if _socket_reachable(sock_path):
                    break
                time.sleep(0.05)
            else:
                _stop_proc(proc)
                raise RuntimeError(
                    f"device_server did not become reachable at {sock_path} "
                    f"within {timeout:.1f}s\n{_log_tail(log_path)}"
                )
            yield f"fm://unix?path={sock_path}"
        finally:
            _stop_proc(proc)
            sock_path.unlink(missing_ok=True)
            log_path.unlink(missing_ok=True)
