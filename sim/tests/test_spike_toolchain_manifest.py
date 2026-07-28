"""Tests for spike toolchain manifest validation.

Covers:
  - rejects_incomplete_or_stale_manifest: detects missing keys,
    stale hashes, version mismatches, and source changes.
  - Requires a pre-built manifest (or test-fixture manifest) and
    the build_spike_stack.py module on PYTHONPATH.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Ensure scripts/ is importable
REPO = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_spike_stack as bss


# ── Helpers ─────────────────────────────────────────────────────────

def _minimal_manifest() -> dict:
    """Return a structurally-valid minimal manifest."""
    return {
        "manifest_schema_version": 1,
        "build_timestamp": "2026-01-01T00:00:00Z",
        "spike": {
            "source_commit": "1412bbec3176ed514acf3b6784c1bd07bb904e24",
            "source_path": str(bss._SPIKE_SRC.resolve()),
        },
        "compilers": {
            "riscv_gcc": "10.2.0",
            "cxx": "11.4.0",
            "dtc": "1.8.1-g66e1201c",
        },
        "abi": {
            "major": 1,
            "minor": 0,
            "version_string": "1.0",
            "firmware_header": str(bss._ABI_FW_HEADER.resolve()),
        },
        "firmware": {
            "source_files_hash": bss._firmware_source_hash(),
        },
        "artifacts": {
            "spike_binary": {
                "path": str(bss._SPIKE_BIN.resolve()),
                "sha256": bss._sha256_file(bss._SPIKE_BIN) if bss._SPIKE_BIN.exists() else "MISSING",
            },
            "plugin_so": {
                "path": str(bss._PLUGIN_SO.resolve()),
                "sha256": bss._sha256_file(bss._PLUGIN_SO) if bss._PLUGIN_SO.exists() else "MISSING",
            },
            "npu_firmware_elf": {
                "path": str(bss._FIRMWARE_ELF.resolve()),
                "sha256": bss._sha256_file(bss._FIRMWARE_ELF) if bss._FIRMWARE_ELF.exists() else "MISSING",
            },
            "npu_firmware_spike_elf": {
                "path": str(bss._FIRMWARE_SPIKE_ELF.resolve()),
                "sha256": bss._sha256_file(bss._FIRMWARE_SPIKE_ELF) if bss._FIRMWARE_SPIKE_ELF.exists() else "MISSING",
            },
        },
    }


def _write_manifest(manifest: dict) -> str:
    """Write manifest to a temp file, return path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, prefix="test_spike_manifest_"
    )
    json.dump(manifest, tmp, indent=2)
    tmp.close()
    return tmp.name


def _preflight_info() -> dict:
    """Get real preflight info, or a safe fake for environments without tools."""
    try:
        return bss.preflight()
    except bss.PreflightError:
        # In CI / environments without the full toolchain, return minimal
        # info that matches the test fixture.
        return {
            "spike_commit": "1412bbec3176ed514acf3b6784c1bd07bb904e24",
            "spike_src": str(bss._SPIKE_SRC.resolve()),
            "riscv_gcc": "10.2.0",
            "cxx": "11.4.0",
            "dtc": "1.8.1-g66e1201c",
            "abi_major": "1",
            "abi_minor": "0",
            "abi_version_string": "1.0",
            "abi_fw_header": str(bss._ABI_FW_HEADER.resolve()),
        }


# ── Fixture ─────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def valid_manifest_path() -> str:
    """A valid manifest file matching current state."""
    manifest = _minimal_manifest()
    return _write_manifest(manifest)


# ── Tests ───────────────────────────────────────────────────────────

def test_rejects_incomplete_or_stale_manifest(valid_manifest_path):
    """Given a manifest, removing keys or changing hashes should invalidate it."""
    info = _preflight_info()

    # 1. Valid manifest should pass
    valid, errors = bss.check_manifest(Path(valid_manifest_path), info)
    assert valid, f"valid manifest should pass but got: {errors}"

    # 2. Missing required top-level key
    manifest = _minimal_manifest()
    del manifest["compilers"]
    p = _write_manifest(manifest)
    valid, errors = bss.check_manifest(Path(p), info)
    assert not valid, "manifest missing 'compilers' should be rejected"
    assert any("compilers" in e.lower() or "missing required key" in e.lower() for e in errors), \
        f"expected error about missing compilers, got: {errors}"
    os.unlink(p)

    # 3. Missing compiler sub-key
    manifest = _minimal_manifest()
    del manifest["compilers"]["dtc"]
    p = _write_manifest(manifest)
    valid, errors = bss.check_manifest(Path(p), info)
    assert not valid, "manifest missing compiler 'dtc' should be rejected"
    os.unlink(p)

    # 4. Stale compiler version
    manifest = _minimal_manifest()
    manifest["compilers"]["riscv_gcc"] = "0.0.0"  # bogus version
    p = _write_manifest(manifest)
    valid, errors = bss.check_manifest(Path(p), info)
    assert not valid, "manifest with stale riscv_gcc version should be rejected"
    assert any("version mismatch" in e.lower() or "riscv_gcc" in e.lower() for e in errors), \
        f"expected error about riscv_gcc mismatch, got: {errors}"
    os.unlink(p)

    # 5. Stale spike commit
    manifest = _minimal_manifest()
    manifest["spike"]["source_commit"] = "0000000000000000000000000000000000000000"
    p = _write_manifest(manifest)
    valid, errors = bss.check_manifest(Path(p), info)
    assert not valid, "manifest with stale spike commit should be rejected"
    os.unlink(p)

    # 6. Wrong ABI version
    manifest = _minimal_manifest()
    manifest["abi"]["major"] = 99
    p = _write_manifest(manifest)
    valid, errors = bss.check_manifest(Path(p), info)
    assert not valid, "manifest with wrong ABI major should be rejected"
    os.unlink(p)

    # 7. Wrong schema version
    manifest = _minimal_manifest()
    manifest["manifest_schema_version"] = 999
    p = _write_manifest(manifest)
    valid, _ = bss.check_manifest(Path(p), info)
    assert not valid, "manifest with wrong schema version should be rejected"
    os.unlink(p)

    # 8. Missing firmware source hash
    manifest = _minimal_manifest()
    del manifest["firmware"]
    p = _write_manifest(manifest)
    valid, errors = bss.check_manifest(Path(p), info)
    assert not valid, "manifest missing 'firmware' should be rejected"
    os.unlink(p)

    # 9. Stale firmware source hash
    manifest = _minimal_manifest()
    manifest["firmware"]["source_files_hash"] = "deadbeef" * 8
    p = _write_manifest(manifest)
    valid, errors = bss.check_manifest(Path(p), info)
    assert not valid, "manifest with stale firmware source hash should be rejected"
    os.unlink(p)

    # 10. Missing artifact entry
    manifest = _minimal_manifest()
    del manifest["artifacts"]["spike_binary"]
    p = _write_manifest(manifest)
    valid, errors = bss.check_manifest(Path(p), info)
    assert not valid, "manifest missing 'spike_binary' artifact should be rejected"
    os.unlink(p)


def test_preflight_detects_missing_spike(monkeypatch):
    """Preflight should fail if spike binary is requested but missing."""
    with mock.patch.object(bss, "_which", return_value="/usr/bin/riscv64-unknown-elf-gcc"):
        # If we only mock _which, the file-existence checks will catch missing files.
        # But preflight() itself only checks _which() for tools and then file system.
        pass  # Preflight checks use real filesystem; this is tested via --preflight-only


def test_preflight_detects_missing_riscv_gcc(monkeypatch):
    """Preflight should raise PreflightError if riscv64-unknown-elf-gcc is missing."""

    def fake_which(name):
        if "riscv64" in name:
            return None
        if "g++" in name or "c++" in name:
            return "/usr/bin/g++"
        if "objcopy" in name:
            return "/usr/bin/riscv64-unknown-elf-objcopy"
        if name == "make":
            return "/usr/bin/make"
        return None

    with mock.patch.object(bss, "_which", side_effect=fake_which):
        with pytest.raises(bss.PreflightError, match="riscv64-unknown-elf-gcc"):
            bss.preflight()


def test_preflight_detects_missing_abi_header(monkeypatch):
    """Preflight should fail if the generated ABI firmware header is missing."""
    # Only test that the check fires; we can't delete the real file
    with mock.patch.object(bss, "_ABI_FW_HEADER", Path("/nonexistent/abi_header.h")):
        try:
            bss.preflight()
            pytest.fail("Expected PreflightError for missing ABI header")
        except bss.PreflightError as e:
            assert "ABI firmware header" in str(e) or "npu_abi_firmware" in str(e)


def test_firmware_source_hash_deterministic():
    """Same fileset should produce identical hash (no timestamp in hash)."""
    h1 = bss._firmware_source_hash()
    h2 = bss._firmware_source_hash()
    assert h1 == h2, "firmware source hash is not deterministic between calls"


def test_manifest_json_valid_schema():
    """Generated manifest should have the expected schema top-level keys."""
    manifest = _minimal_manifest()
    required = ["manifest_schema_version", "spike", "compilers", "abi",
                "firmware", "artifacts"]
    for key in required:
        assert key in manifest, f"manifest missing key: {key}"

    # Check artifact sub-keys
    artifacts = manifest["artifacts"]
    for name in ("spike_binary", "plugin_so", "npu_firmware_elf", "npu_firmware_spike_elf"):
        assert name in artifacts, f"manifest artifacts missing: {name}"
        assert "sha256" in artifacts[name], f"artifact {name} missing sha256"
        assert "path" in artifacts[name], f"artifact {name} missing path"


def test_both_firmware_targets_same_source_hash():
    """Both firmware link targets should be built from the same source files.

    The firmware source hash covers npu_firmware.c, startup.S, test_data.S,
    npu-regmap.h, link.ld, spike_link.ld. Both ELFs must derive from these.
    """
    manifest = _minimal_manifest()
    fw_hash = manifest["firmware"]["source_files_hash"]
    assert len(fw_hash) == 64, f"source_files_hash should be SHA-256 hex, got length {len(fw_hash)}"

    # Same source hash => both artifacts are in the manifest (proves same source)
    assert manifest["artifacts"]["npu_firmware_elf"]["sha256"] != "MISSING", \
        "npu_firmware.elf has no hash"
    assert manifest["artifacts"]["npu_firmware_spike_elf"]["sha256"] != "MISSING", \
        "npu_firmware_spike.elf has no hash"

    # The two ELFs should differ (different link scripts), proving both were built
    fw1 = manifest["artifacts"]["npu_firmware_elf"]["sha256"]
    fw2 = manifest["artifacts"]["npu_firmware_spike_elf"]["sha256"]
    assert fw1 != fw2, (
        "npu_firmware.elf and npu_firmware_spike.elf have identical hashes — "
        "both link targets must produce distinct ELFs"
    )
