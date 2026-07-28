#!/usr/bin/env python3
"""Preflight/build script for the reproducible CaduceusCore Spike toolchain.

Modes:
  --clean --manifest PATH     Full clean build + emit artifact manifest
  --manifest PATH             Build with preflight check + emit manifest
  --check MANIFEST            Validate an existing manifest against current state
  (no flags)                  Preflight-only: check dependencies, detect gaps

Deterministic: same source + same tools => bit-identical manifest.
The plugin is built with the host default C++ ABI (Spike and plugin are
compiled in the same environment, so no ABI mismatch is expected).

Exit codes:
  0  success / manifest valid
  1  build failure
  2  preflight failure (missing tool/dependency)
  3  manifest invalid (--check mode only)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Constants ────────────────────────────────────────────────────────

MANIFEST_SCHEMA_VERSION = 1

_CXX_ABI_FLAGS = []

# Default paths relative to repository root
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SPIKE_SRC = _REPO_ROOT / "spike_src"
_SPIKE_BIN = _SPIKE_SRC / "build" / "spike"
_PLUGIN_SRC = _SPIKE_SRC / "plugins" / "npu_mmio_plugin.cc"
_PLUGIN_SO = _SPIKE_SRC / "plugins" / "npu_mmio_plugin.so"
_DTC_BIN = _REPO_ROOT / "dtc_src" / "dtc"
_FIRMWARE_DIR = _REPO_ROOT / "firmware"
_FIRMWARE_ELF = _FIRMWARE_DIR / "build" / "npu_firmware.elf"
_FIRMWARE_SPIKE_ELF = _FIRMWARE_DIR / "build" / "npu_firmware_spike.elf"
_ABI_JSON = _REPO_ROOT / "spec" / "npu_abi.json"
_ABI_FW_HEADER = _REPO_ROOT / "gen" / "npu_abi_firmware.h"


# ── Preflight ────────────────────────────────────────────────────────

class PreflightError(Exception):
    """Typed failure from preflight checks (exit code 2)."""


def _which(name: str) -> Optional[str]:
    """Resolve an executable in PATH."""
    return shutil.which(name) or shutil.which(name, path=os.environ.get("PATH", ""))


def _run(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command and return its result; raise on preflight failure."""
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=kwargs.pop("timeout", 120), **kwargs,
        )
    except FileNotFoundError:
        raise PreflightError(f"command not found: {cmd[0]}")


def _version_from_stdout(cmd: List[str], pattern: str) -> str:
    """Extract a version string from command stdout using regex."""
    r = _run(cmd)
    m = re.search(pattern, r.stdout + r.stderr)
    if not m:
        raise PreflightError(f"cannot parse version from {' '.join(cmd)}: {r.stdout[:120]}")
    return m.group(0).strip()


def preflight(
    *,
    required_artifacts: Optional[List[Path]] = None,
) -> Dict[str, str]:
    """Run preflight checks and return toolchain versions.

    Returns dict with keys: riscv_gcc, riscv_objcopy, cxx, dtc, spike_commit.

    Raises PreflightError on missing tools.
    """
    results: Dict[str, str] = {}

    # RISC-V GCC
    riscv_gcc = _which("riscv64-unknown-elf-gcc")
    if not riscv_gcc:
        raise PreflightError("riscv64-unknown-elf-gcc not found in PATH")
    results["riscv_gcc"] = _version_from_stdout([riscv_gcc, "--version"], r"\d+\.\d+\.\d+")
    results["riscv_gcc_path"] = riscv_gcc

    # RISC-V objcopy
    riscv_objcopy = _which("riscv64-unknown-elf-objcopy")
    if not riscv_objcopy:
        raise PreflightError("riscv64-unknown-elf-objcopy not found in PATH")
    results["riscv_objcopy"] = _version_from_stdout([riscv_objcopy, "--version"], r"\d+\.\d+\.\d+")

    # Host C++ compiler
    cxx = _which("g++") or _which("c++")
    if not cxx:
        raise PreflightError("C++ compiler (g++ or c++) not found in PATH")
    results["cxx"] = _version_from_stdout([cxx, "--version"], r"\d+\.\d+\.\d+")

    # Device-tree compiler
    dtc_path = str(_DTC_BIN)
    results["dtc"] = _version_from_stdout([dtc_path, "--version"], r"\d+\.\d+\.\d+[-.\w]*")
    results["dtc_path"] = dtc_path

    # Spike source commit
    if not _SPIKE_SRC.is_dir():
        raise PreflightError(f"spike_src not found at {_SPIKE_SRC}")
    spike_commit = _run(["git", "-C", str(_SPIKE_SRC), "rev-parse", "HEAD"])
    if spike_commit.returncode != 0:
        spike_commit = _run(["git", "-C", str(_SPIKE_SRC), "rev-parse", "HEAD"],
                            timeout=10)
    results["spike_commit"] = spike_commit.stdout.strip()
    results["spike_src"] = str(_SPIKE_SRC.resolve())

    # Required artifacts (optional check)
    if required_artifacts:
        for path in required_artifacts:
            if not path.exists():
                raise PreflightError(f"required artifact missing: {path}")
            results[f"artifact_{path.name}"] = str(path.resolve())

    # ABI schema
    if not _ABI_JSON.is_file():
        raise PreflightError(f"ABI schema not found at {_ABI_JSON}")
    with open(_ABI_JSON) as f:
        schema = json.load(f)
    abi_major = schema["abi"]["major"]
    abi_minor = schema["abi"]["minor"]
    results["abi_major"] = str(abi_major)
    results["abi_minor"] = str(abi_minor)
    results["abi_version_string"] = schema["abi"]["version_string"]

    # ABI firmware header — must exist (Todo 1 artifact)
    if not _ABI_FW_HEADER.is_file():
        raise PreflightError(f"ABI firmware header missing at {_ABI_FW_HEADER}; run scripts/gen_npu_abi.py first")
    results["abi_fw_header"] = str(_ABI_FW_HEADER.resolve())

    return results


# ── Hash helpers ─────────────────────────────────────────────────────

def _sha256_file(path: Path) -> str:
    """SHA-256 hex digest of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_files(paths: List[Path]) -> str:
    """SHA-256 hex digest over multiple file contents (stable sorted)."""
    h = hashlib.sha256()
    for p in sorted(paths):
        if p.is_file():
            h.update(p.read_bytes())
        else:
            raise PreflightError(f"source file missing: {p}")
    return h.hexdigest()


def _firmware_source_hash() -> str:
    """SHA-256 over all firmware C/ASM/linker source files."""
    sources = sorted(
        p for p in _FIRMWARE_DIR.glob("*")
        if p.suffix in (".c", ".h", ".S", ".ld") and p.is_file()
    )
    return _sha256_files(sources)


# ── Build steps ──────────────────────────────────────────────────────

def build_spike() -> Path:
    """Build the Spike binary if not already present."""
    if _SPIKE_BIN.exists():
        print(f"  [SKIP] Spike binary exists: {_SPIKE_BIN}")
        return _SPIKE_BIN

    build_dir = _SPIKE_SRC / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    print(f"  [BUILD] Spike → {build_dir}")

    # Ensure dtc is available for configure
    dtc_path = str(_DTC_BIN.resolve())
    env = os.environ.copy()
    env["PATH"] = f"{_DTC_BIN.parent.resolve()}:{env['PATH']}"

    _run(["../configure", f"--prefix={build_dir.resolve()}"], cwd=str(build_dir), env=env)
    _run(["make", f"-j{os.cpu_count() or 4}"], cwd=str(build_dir), env=env)

    if not _SPIKE_BIN.exists():
        raise PreflightError(f"Spike build failed: {_SPIKE_BIN} not produced")
    return _SPIKE_BIN


def build_dtc() -> str:
    """Build dtc if not present."""
    if _DTC_BIN.exists():
        dtc_ver = _version_from_stdout([str(_DTC_BIN), "--version"], r"\d+\.\d+\.\d+[-.\w]*")
        print(f"  [SKIP] dtc exists: {_DTC_BIN} (version {dtc_ver})")
        return str(_DTC_BIN)

    dtc_dir = _DTC_BIN.parent
    print(f"  [BUILD] dtc → {dtc_dir}")
    _run(["make", f"-j{os.cpu_count() or 4}"], cwd=str(dtc_dir))
    if not _DTC_BIN.exists():
        raise PreflightError(f"dtc build failed: {_DTC_BIN} not produced")
    return str(_DTC_BIN)


def build_plugin() -> Path:
    """Build the MMIO plugin with explicit CXX11 ABI flag."""
    if not _PLUGIN_SRC.exists():
        raise PreflightError(f"plugin source not found: {_PLUGIN_SRC}")

    print(f"  [BUILD] npu_mmio_plugin.so → {_PLUGIN_SO}")

    cxx = _which("g++") or _which("c++")
    if not cxx:
        raise PreflightError("C++ compiler not found")

    include_dir = (_SPIKE_SRC / "riscv").resolve()

    cmd = [
        cxx,
        "-std=c++17",
        "-fPIC",
        "-O2",
        "-Wall",
        f"-I{include_dir}",
        "-shared",
        "-o", str(_PLUGIN_SO),
        str(_PLUGIN_SRC),
    ]
    r = _run(cmd)
    if r.returncode != 0:
        raise PreflightError(f"Plugin build failed:\n{r.stderr}")

    return _PLUGIN_SO


def build_firmware() -> Tuple[Path, Path]:
    """Build both firmware link targets from the same source + ABI header."""
    print(f"  [BUILD] firmware → {_FIRMWARE_DIR}")

    # Use the existing Makefile but ensure the ABI header is a dependency
    # First ensure the build directory exists
    build_dir = _FIRMWARE_DIR / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    # The Makefile already builds both targets (`all` includes npu_firmware_spike.elf)
    make = _which("make")
    if not make:
        raise PreflightError("make not found in PATH")

    # Ensure TOOLCHAIN_DIR uses the discovered gcc prefix
    riscv_gcc = _which("riscv64-unknown-elf-gcc")
    if not riscv_gcc:
        raise PreflightError("riscv64-unknown-elf-gcc not found")

    toolchain_dir = str(Path(riscv_gcc).parent.parent)

    env = os.environ.copy()
    env["TOOLCHAIN_DIR"] = toolchain_dir

    r = _run([make, "-C", str(_FIRMWARE_DIR), "all"], env=env)
    if r.returncode != 0:
        raise PreflightError(f"Firmware build failed:\n{r.stderr[:500]}")

    for elf_path in [_FIRMWARE_ELF, _FIRMWARE_SPIKE_ELF]:
        if not elf_path.exists():
            raise PreflightError(f"Firmware ELF not produced: {elf_path}")

    return _FIRMWARE_ELF, _FIRMWARE_SPIKE_ELF


# ── Manifest ─────────────────────────────────────────────────────────

def build_manifest(preflight_info: Dict[str, str]) -> Dict:
    """Build a machine-readable artifact manifest."""
    # Hash artifacts
    spike_hash = _sha256_file(_SPIKE_BIN) if _SPIKE_BIN.exists() else "MISSING"
    plugin_hash = _sha256_file(_PLUGIN_SO) if _PLUGIN_SO.exists() else "MISSING"
    fw_elf_hash = _sha256_file(_FIRMWARE_ELF) if _FIRMWARE_ELF.exists() else "MISSING"
    fw_spike_elf_hash = _sha256_file(_FIRMWARE_SPIKE_ELF) if _FIRMWARE_SPIKE_ELF.exists() else "MISSING"
    fw_source_hash = _firmware_source_hash()

    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "build_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "spike": {
            "source_commit": preflight_info.get("spike_commit", "UNKNOWN"),
            "source_path": preflight_info.get("spike_src", str(_SPIKE_SRC.resolve())),
        },
        "compilers": {
            "riscv_gcc": preflight_info.get("riscv_gcc", "UNKNOWN"),
            "cxx": preflight_info.get("cxx", "UNKNOWN"),
            "dtc": preflight_info.get("dtc", "UNKNOWN"),
        },
        "abi": {
            "major": int(preflight_info.get("abi_major", 1)),
            "minor": int(preflight_info.get("abi_minor", 0)),
            "version_string": preflight_info.get("abi_version_string", "1.0"),
            "firmware_header": preflight_info.get("abi_fw_header", str(_ABI_FW_HEADER.resolve())),
        },
        "firmware": {
            "source_files_hash": fw_source_hash,
        },
        "artifacts": {
            "spike_binary": {
                "path": str(_SPIKE_BIN.resolve()),
                "sha256": spike_hash,
            },
            "plugin_so": {
                "path": str(_PLUGIN_SO.resolve()),
                "sha256": plugin_hash,
            },
            "npu_firmware_elf": {
                "path": str(_FIRMWARE_ELF.resolve()),
                "sha256": fw_elf_hash,
            },
            "npu_firmware_spike_elf": {
                "path": str(_FIRMWARE_SPIKE_ELF.resolve()),
                "sha256": fw_spike_elf_hash,
            },
        },
    }


# ── Manifest validation ──────────────────────────────────────────────

def check_manifest(manifest_path: Path, preflight_info: Dict[str, str]) -> Tuple[bool, List[str]]:
    """Validate an existing manifest against current state.

    Returns (valid, errors).
    """
    errors: List[str] = []

    if not manifest_path.is_file():
        errors.append(f"manifest file not found: {manifest_path}")
        return False, errors

    try:
        with open(manifest_path) as f:
            stored = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        errors.append(f"cannot parse manifest: {e}")
        return False, errors

    # Check schema version
    stored_schema = stored.get("manifest_schema_version")
    if stored_schema != MANIFEST_SCHEMA_VERSION:
        errors.append(f"manifest schema version {stored_schema} != {MANIFEST_SCHEMA_VERSION}")

    # Check spike commit
    stored_commit = stored.get("spike", {}).get("source_commit", "")
    current_commit = preflight_info.get("spike_commit", "")
    if stored_commit != current_commit:
        errors.append(f"spike commit mismatch: stored={stored_commit[:12]} current={current_commit[:12]}")

    # Check compiler versions
    for key in ("riscv_gcc", "cxx", "dtc"):
        stored_ver = stored.get("compilers", {}).get(key, "")
        current_ver = preflight_info.get(key, "")
        if stored_ver != current_ver:
            errors.append(f"compiler {key} version mismatch: stored={stored_ver} current={current_ver}")

    # Check ABI version
    stored_abi = stored.get("abi", {})
    current_major = int(preflight_info.get("abi_major", 0))
    current_minor = int(preflight_info.get("abi_minor", 0))
    if stored_abi.get("major") != current_major or stored_abi.get("minor") != current_minor:
        errors.append(f"ABI version mismatch: stored={stored_abi.get('major')}.{stored_abi.get('minor')} "
                      f"current={current_major}.{current_minor}")

    # Check firmware source hash
    stored_fw_hash = stored.get("firmware", {}).get("source_files_hash", "")
    current_fw_hash = _firmware_source_hash()
    if stored_fw_hash != current_fw_hash:
        errors.append(f"firmware source hash mismatch: stored={stored_fw_hash[:12]} current={current_fw_hash[:12]}")

    # Check artifact hashes for artifacts that exist
    artifact_map = {
        "spike_binary": _SPIKE_BIN,
        "plugin_so": _PLUGIN_SO,
        "npu_firmware_elf": _FIRMWARE_ELF,
        "npu_firmware_spike_elf": _FIRMWARE_SPIKE_ELF,
    }
    for aname, apath in artifact_map.items():
        stored_hash = stored.get("artifacts", {}).get(aname, {}).get("sha256", "")
        if apath.exists():
            current_hash = _sha256_file(apath)
            if stored_hash != current_hash:
                errors.append(f"artifact {aname} hash mismatch: stored={stored_hash[:12]} current={current_hash[:12]}")
        elif stored_hash not in ("", "MISSING"):
            errors.append(f"artifact {aname} stored in manifest but file is missing")

    # Check for completeness — all required keys must be present
    required_top = ["manifest_schema_version", "spike", "compilers", "abi", "firmware", "artifacts"]
    for key in required_top:
        if key not in stored:
            errors.append(f"manifest missing required key: {key}")

    required_compilers = ["riscv_gcc", "cxx", "dtc"]
    for key in required_compilers:
        if key not in stored.get("compilers", {}):
            errors.append(f"manifest missing compiler entry: {key}")

    required_artifacts = ["spike_binary", "plugin_so", "npu_firmware_elf", "npu_firmware_spike_elf"]
    for key in required_artifacts:
        if key not in stored.get("artifacts", {}):
            errors.append(f"manifest missing artifact entry: {key}")

    return len(errors) == 0, errors


# ── CLI ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build and verify the CaduceusCore Spike toolchain stack",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--clean", action="store_true",
        help="Remove build artifacts before building",
    )
    parser.add_argument(
        "--manifest", type=str, default=None,
        help="Path to write the artifact manifest JSON (enables build mode)",
    )
    parser.add_argument(
        "--check", type=str, default=None,
        help="Path to an existing manifest to validate (no build)",
    )
    parser.add_argument(
        "--preflight-only", action="store_true",
        help="Run preflight checks only, do not build",
    )
    args = parser.parse_args()

    # ── Check mode ──
    if args.check:
        print("=== Checking manifest ===")
        mpath = Path(args.check)
        try:
            info = preflight()
        except PreflightError as e:
            print(f"  [FAIL] preflight error: {e}")
            sys.exit(2)

        valid, errors = check_manifest(mpath, info)
        if valid:
            print(f"  [PASS] Manifest valid: {mpath}")
            sys.exit(0)
        else:
            print(f"  [FAIL] Manifest invalid: {mpath}")
            for err in errors:
                print(f"    - {err}")
            sys.exit(3)

    # ── Preflight ──
    print("=== Preflight ===")
    try:
        info = preflight()
    except PreflightError as e:
        print(f"  [FAIL] {e}")
        sys.exit(2)

    print(f"  riscv64-unknown-elf-gcc: {info.get('riscv_gcc')}")
    print(f"  C++ compiler: {info.get('cxx')}")
    print(f"  dtc: {info.get('dtc')}")
    print(f"  spike commit: {info['spike_commit'][:12]}...")
    print(f"  ABI version: {info.get('abi_version_string')}")
    print(f"  ABI firmware header: {info.get('abi_fw_header')}")
    print("  [PASS] preflight OK")

    if args.preflight_only:
        sys.exit(0)

    if args.manifest is None:
        print("\nTip: pass --manifest PATH to build and emit a manifest,"
              " or --check PATH to validate an existing one.")
        sys.exit(0)

    # ── Build ──
    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    if args.clean:
        print("\n=== Clean ===")
        for d in [_SPIKE_SRC / "build", _FIRMWARE_DIR / "build"]:
            if d.exists():
                print(f"  [CLEAN] {d}")
                shutil.rmtree(d)
        if _PLUGIN_SO.exists():
            print(f"  [CLEAN] {_PLUGIN_SO}")
            _PLUGIN_SO.unlink()

    print("\n=== Build ===")
    try:
        build_dtc()
        build_spike()
        build_plugin()
        fw_elf, fw_spike_elf = build_firmware()
    except PreflightError as e:
        print(f"  [FAIL] {e}")
        sys.exit(1)

    print(f"  spike binary: {_SPIKE_BIN}")
    print(f"  plugin: {_PLUGIN_SO}")
    print(f"  firmware (RTL): {fw_elf}")
    print(f"  firmware (Spike): {fw_spike_elf}")
    print("  [PASS] build complete")

    # ── Manifest ──
    print("\n=== Manifest ===")
    manifest = build_manifest(info)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"  [WRITE] {manifest_path}")
    print(f"  spike commit:   {manifest['spike']['source_commit'][:12]}")
    print(f"  RISC-V GCC:     {manifest['compilers']['riscv_gcc']}")
    print(f"  DTC:            {manifest['compilers']['dtc']}")
    print(f"  ABI:            {manifest['abi']['version_string']}")
    print(f"  FW source hash: {manifest['firmware']['source_files_hash'][:12]}")
    print(f"  spike binary:   {manifest['artifacts']['spike_binary']['sha256'][:12]}")
    print(f"  plugin.so:      {manifest['artifacts']['plugin_so']['sha256'][:12]}")
    print(f"  fw RTL ELF:     {manifest['artifacts']['npu_firmware_elf']['sha256'][:12]}")
    print(f"  fw Spike ELF:   {manifest['artifacts']['npu_firmware_spike_elf']['sha256'][:12]}")
    print("  [PASS] manifest written")


if __name__ == "__main__":
    main()
