"""Tests for NPUFirmware deprecation warnings.

Verifies:
1. Importing NPUFirmware triggers DeprecationWarning when warnings are enabled.
2. Instantiating NPUFirmware({}) emits DeprecationWarning with the correct message.
3. NPUFirmware public methods remain callable (deprecated != removed).
4. _dispatch docstring contains DEPRECATED marker pointing to spike_host.py.
"""

import warnings

import pytest

from sim.miniv import NPUFirmware


def _make_minimal_modules() -> dict:
    """Return a bare-bones sim_modules dict for NPUFirmware construction.

    Running run_loop() with this setup will produce an empty result list
    (no doorbell entries), which is sufficient to test callability without
    triggering full engine dispatch.
    """
    return {
        "dram": bytearray(64 * 1024 * 1024),
        "sram": bytearray(512 * 1024),
    }


# ── DeprecationWarning behaviour ─────────────────────────────────────


def test_import_and_construct_triggers_deprecation_warning():
    """Importing and constructing NPUFirmware triggers a DeprecationWarning.

    The warning is emitted from __init__, not at module level, so we must
    instantiate to see it. Mirrors the CLI check:
        python3 -W default::DeprecationWarning -c "from sim.miniv import NPUFirmware; NPUFirmware({})"
    """
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        from sim.miniv import NPUFirmware as npf

        npf(sim_modules=_make_minimal_modules())
    deprecation_msgs = [
        x.message for x in w if issubclass(x.category, DeprecationWarning)
    ]
    assert len(deprecation_msgs) >= 1, (
        f"Expected at least 1 DeprecationWarning, got {len(deprecation_msgs)}"
    )
    msg = str(deprecation_msgs[0])
    assert "NPUFirmware is deprecated" in msg, (
        f"Warning message unexpected: {msg}"
    )
    assert "Spike" in msg and "golden" in msg


def test_instantiation_emits_deprecation_warning():
    """Instantiating NPUFirmware({}) emits a DeprecationWarning."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        fw = NPUFirmware(sim_modules=_make_minimal_modules())
        fw  # silence unused-variable

    deprecation_msgs = [
        x.message for x in w if issubclass(x.category, DeprecationWarning)
    ]
    assert len(deprecation_msgs) >= 1
    msg = str(deprecation_msgs[0])
    assert "NPUFirmware is deprecated" in msg


def test_stacklevel_points_to_caller():
    """The DeprecationWarning stacklevel=2 points to the caller's frame.

    When invoked from a test function, the warning's filename should be this
    test file, not miniv.py.
    """
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        NPUFirmware(sim_modules=_make_minimal_modules())

    dep_msgs = [x for x in w if issubclass(x.category, DeprecationWarning)]
    assert len(dep_msgs) >= 1
    filename = dep_msgs[0].filename
    assert "test_npu_firmware_deprecation" in filename, (
        f"Expected caller frame to be this test file, got {filename}"
    )


# ── Behaviour preservation (deprecated != removed) ──────────────────


def test_object_can_be_constructed():
    """NPUFirmware can be constructed without error (ignoring warning)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        fw = NPUFirmware(sim_modules=_make_minimal_modules())
    assert fw is not None


def test_dispatch_is_callable():
    """NPUFirmware._dispatch is still callable and returns a result dict."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        fw = NPUFirmware(sim_modules=_make_minimal_modules())

    # Write a valid 15-uint32 descriptor into DRAM so _read_descriptor succeeds.
    desc_addr = 0x8000_0080
    import struct
    dram = fw.mod["dram"]
    off = desc_addr - 0x8000_0000
    struct.pack_into("<15I", dram, off, *([0] * 15))

    # An unknown opcode (0xFF) hits the try/except in _dispatch → 'error'.
    cmd = {"opcode": 0xFF, "desc_addr": desc_addr, "flags": 0}
    result = fw._dispatch(cmd)
    assert result is not None
    assert isinstance(result, dict)
    assert "status" in result


def test_run_loop_is_callable():
    """NPUFirmware.run_loop can be called and returns a list."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        fw = NPUFirmware(sim_modules=_make_minimal_modules())

    results = fw.run_loop(max_commands=3)
    assert isinstance(results, list)
    # No doorbell entries → empty results (not an error).
    assert len(results) <= 3


def test_bind_riscv_and_boot():
    """NPUFirmware.bind_riscv and boot are callable.

    bind_riscv accepts None without raising.
    boot requires a non-None riscv argument; we pass a minimal dummy.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        fw = NPUFirmware(sim_modules=_make_minimal_modules())

    # bind_riscv(None) — should not raise (valid use: no emulator)
    fw.bind_riscv(None)

    # boot with a minimal state object for callability check
    class _DummyRiscv:
        class state:
            pc = 0

            @staticmethod
            def write(*_):
                pass

    dummy = _DummyRiscv()
    fw.boot(dummy)


# ── Docstring verification ──────────────────────────────────────────


def test_dispatch_docstring_has_deprecated_marker():
    """_dispatch docstring contains 'DEPRECATED' and points to spike_host.py."""
    doc = NPUFirmware._dispatch.__doc__
    assert doc is not None, "_dispatch has no docstring"
    assert "DEPRECATED" in doc, (
        "_dispatch docstring should contain DEPRECATED marker"
    )
    assert "spike_host.py" in doc, (
        "_dispatch docstring should reference sim/spike_host.py"
    )
    assert "golden" in doc.lower(), (
        "_dispatch docstring should mention golden reference"
    )


# ── Smoke: CLI equivalent ───────────────────────────────────────────


def test_cli_import_equivalent():
    """Equivalent to: python3 -W default::DeprecationWarning -c "from sim.miniv import NPUFirmware"

    This test exists as a programmatic mirror of the manual QA step in the
    acceptance criteria. The warning fires on construction, not import.
    """
    import os
    import subprocess
    import sys

    code = (
        "from sim.miniv import NPUFirmware; "
        "NPUFirmware({'dram': bytearray(1024), 'sram': bytearray(1024)}); "
        "print('OK')"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(os.path.dirname(__file__), "..")
    result = subprocess.run(
        [sys.executable, "-W", "default::DeprecationWarning", "-c", code],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"Subprocess exited with code {result.returncode}:\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "DeprecationWarning" in result.stderr, (
        "Expected DeprecationWarning in stderr when running with "
        "-W default::DeprecationWarning:\n"
        f"stderr: {result.stderr}"
    )
    assert "NPUFirmware is deprecated" in result.stderr
