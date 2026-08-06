#!/usr/bin/env python3
"""
Reproducible fetch/build script for the pinned llama.cpp integration surface.

Materialises the locked source at third_party/llama.cpp,
integrates ggml-npu/ as a backend library, and verifies commit identity.

Usage:
  python3 scripts/fetch_llama_cpp.py --lock deps/llama-cpp.lock          # clone + integrate
  python3 scripts/fetch_llama_cpp.py --lock deps/llama-cpp.lock --check  # verify only
  python3 scripts/fetch_llama_cpp.py --lock deps/llama-cpp.lock --build  # clone + integrate + build
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict


PROJECT_ROOT = Path(__file__).resolve().parent.parent
THIRD_PARTY = PROJECT_ROOT / "third_party"
LLAMA_CPP = THIRD_PARTY / "llama.cpp"
GGML_NPU_SRC = PROJECT_ROOT / "ggml-npu"
BACKEND_TARGET = LLAMA_CPP / "ggml" / "src" / "ggml-npu"
BACKEND_REG_CPP = LLAMA_CPP / "ggml" / "src" / "ggml-backend-reg.cpp"
BUILD_DIR = PROJECT_ROOT / "build" / "llama"
CMAKE_LISTS = LLAMA_CPP / "ggml" / "src" / "CMakeLists.txt"


def load_lock(lock_path: Path) -> Dict:
    with open(lock_path) as f:
        return json.load(f)


def verify_commit(checkout_dir: Path, expected_commit: str) -> bool:
    """Verify the checkout HEAD matches the expected commit."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(checkout_dir),
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"ERROR: git rev-parse failed in {checkout_dir}: {result.stderr}")
            return False
        actual = result.stdout.strip()
        if actual != expected_commit:
            print(f"ERROR: commit mismatch in {checkout_dir}")
            print(f"  Expected: {expected_commit}")
            print(f"  Actual:   {actual}")
            return False
        print(f"OK: commit verified: {actual[:12]}")
        return True
    except subprocess.TimeoutExpired:
        print(f"ERROR: git rev-parse timed out in {checkout_dir}")
        return False
    except FileNotFoundError:
        print(f"ERROR: git not found; cannot verify commit")
        return False


def verify_state(lock: Dict) -> bool:
    """Verify the current state against the lock file (--check mode)."""
    errors = []

    if not LLAMA_CPP.exists():
        errors.append(f"third_party/llama.cpp does not exist; run without --check first")
    elif not (LLAMA_CPP / ".git").exists():
        errors.append("third_party/llama.cpp is not a git checkout")
    else:
        if not verify_commit(LLAMA_CPP, lock["commit"]):
            errors.append("commit mismatch")
        if not BACKEND_TARGET.exists():
            errors.append(f"backend not integrated at {BACKEND_TARGET.relative_to(PROJECT_ROOT)}")
        else:
            # Verify ggml-npu CMakeLists.txt content hash matches our source
            src_hash = _file_sha256(GGML_NPU_SRC / "CMakeLists.txt")
            tgt_hash = _file_sha256(BACKEND_TARGET / "CMakeLists.txt")
            if src_hash != tgt_hash:
                errors.append("ggml-npu/CMakeLists.txt differs from source; re-run without --check")
        # Verify backend reg patch
        if BACKEND_REG_CPP.exists():
            if 'ggml_backend_load_best("npu"' not in BACKEND_REG_CPP.read_text():
                errors.append("ggml-backend-reg.cpp not patched for NPU; re-run without --check")

    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        return False
    print("OK: all checks passed — dependency is locked and integrated")
    return True


def _file_sha256(path: Path) -> str:
    if not path.exists():
        return ""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def clone_and_checkout(lock: Dict) -> bool:
    """Clone llama.cpp at the pinned commit."""
    THIRD_PARTY.mkdir(parents=True, exist_ok=True)

    if LLAMA_CPP.exists():
        print(f"INFO: {LLAMA_CPP} already exists — skipping clone")
        return verify_commit(LLAMA_CPP, lock["commit"])

    repo = lock["repository"]
    commit = lock["commit"]

    print(f"Cloning {repo} ...")
    result = subprocess.run(
        ["git", "clone", "--recurse-submodules", repo, str(LLAMA_CPP)],
        capture_output=False, text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: git clone failed (returncode={result.returncode})")
        return False

    print(f"Checking out commit {commit[:12]} ...")
    result = subprocess.run(
        ["git", "checkout", commit],
        cwd=str(LLAMA_CPP),
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"ERROR: git checkout failed: {result.stderr}")
        if "fatal: reference is not a tree" in result.stderr:
            print("  The pinned commit may not exist in the cloned history.")
            print("  Try: cd third_party/llama.cpp && git fetch --unshallow")
        return False

    return verify_commit(LLAMA_CPP, commit)


def integrate_backend(lock: Dict) -> bool:
    """Copy ggml-npu/ into the llama.cpp source tree and patch cmake."""
    target = BACKEND_TARGET

    # Remove stale integration
    if target.exists():
        if target.is_symlink():
            target.unlink()
        else:
            shutil.rmtree(str(target))

    # Copy our backend source files (not the README, not __pycache__, not Python files)
    target.mkdir(parents=True, exist_ok=True)
    for src_file in GGML_NPU_SRC.iterdir():
        if src_file.name in ("README.md", "__pycache__"):
            continue
        if src_file.name == "ggml-npu.cpp":
            # Handled as a symlink below — the build always picks up the canonical source
            continue
        if src_file.suffix == ".py" and src_file.name != "ggml-npu.cpp":
            # Keep only the backend source files; Python helpers stay in ggml-npu/
            continue
        dst = target / src_file.name
        if src_file.is_file():
            shutil.copy2(str(src_file), str(dst))

    # Create a symlink for ggml-npu.cpp so the build always uses the canonical source
    # without requiring a manual copy after editing ggml-npu/ggml-npu.cpp.
    src_cpp = GGML_NPU_SRC / "ggml-npu.cpp"
    dst_cpp = target / "ggml-npu.cpp"
    if src_cpp.exists():
        if dst_cpp.exists() or dst_cpp.is_symlink():
            dst_cpp.unlink()
        rel_path = os.path.relpath(str(src_cpp), str(target))
        dst_cpp.symlink_to(rel_path)
        print(f"  symlink: {dst_cpp.relative_to(PROJECT_ROOT)} -> {rel_path}")

    # Also copy .h explicitly (needed for include path resolution)
    for name in ["ggml-npu.h"]:
        src = GGML_NPU_SRC / name
        dst = target / name
        if src.exists():
            shutil.copy2(str(src), str(dst))

    print(f"Integrated backend at {BACKEND_TARGET.relative_to(PROJECT_ROOT)}")

    # Patch ggml/src/CMakeLists.txt to add NPU backend if not already present
    _patch_cmake_for_npu(lock)
    # Patch ggml/src/ggml-backend-reg.cpp to load NPU backend via DL
    _patch_backend_reg(lock)
    return True


def _patch_backend_reg(lock: Dict) -> bool:
    """Add ggml_backend_load_best("npu", ...) to ggml_backend_load_all_from_path."""
    if not BACKEND_REG_CPP.exists():
        print(f"ERROR: {BACKEND_REG_CPP} not found — is llama.cpp cloned?")
        return False

    content = BACKEND_REG_CPP.read_text()

    marker = 'ggml_backend_load_best("npu"'
    if marker in content:
        print("INFO: NPU backend already registered in backend-reg")
        return True

    # Insert before the CPU line (the last one before the env var check)
    old = '    ggml_backend_load_best("cpu", silent, dir_path);'
    new = '    ggml_backend_load_best("npu",  silent, dir_path);\n' + old
    if old not in content:
        print(f"ERROR: could not find CPU load line in {BACKEND_REG_CPP}")
        return False

    content = content.replace(old, new)
    BACKEND_REG_CPP.write_text(content)
    print(f"INFO: Patched {BACKEND_REG_CPP.relative_to(PROJECT_ROOT)} for NPU backend loading")
    return True


def _patch_cmake_for_npu(lock: Dict) -> bool:
    """Add ggml_add_backend(NPU) to the llama.cpp ggml/src/CMakeLists.txt."""
    if not CMAKE_LISTS.exists():
        print(f"ERROR: {CMAKE_LISTS} not found — is llama.cpp cloned?")
        return False

    content = CMAKE_LISTS.read_text()

    marker = "ggml_add_backend(NPU)"
    if marker in content:
        print("INFO: NPU backend already registered in CMakeLists.txt")
        return True

    # Find the last ggml_add_backend call and insert after it
    lines = content.split("\n")
    insert_after = None
    for i, line in enumerate(lines):
        if line.strip().startswith("ggml_add_backend("):
            insert_after = i

    if insert_after is None:
        print("ERROR: could not find existing ggml_add_backend() calls in CMakeLists.txt")
        return False

    new_lines = lines[:insert_after + 1] + [
        "ggml_add_backend(NPU)",
    ] + lines[insert_after + 1:]

    CMAKE_LISTS.write_text("\n".join(new_lines))
    print(f"INFO: Patched {CMAKE_LISTS.relative_to(PROJECT_ROOT)} for NPU backend")
    return True


def build_backend(lock: Dict) -> bool:
    """Build the empty lifecycle backend."""
    build_flags = lock.get("build_flags", {})
    ggml_npu = build_flags.get("GGML_NPU", "ON")
    backend_dl = build_flags.get("GGML_BACKEND_DL", "ON")

    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    cmake_cmd = [
        "cmake", "-S", str(LLAMA_CPP), "-B", str(BUILD_DIR),
        f"-DGGML_NPU={ggml_npu}",
        f"-DGGML_BACKEND_DL={backend_dl}",
        "-DGGML_CUDA=OFF",
        "-DGGML_METAL=OFF",
        "-DGGML_VULKAN=OFF",
        "-DGGML_SYCL=OFF",
        "-DGGML_HIP=OFF",
        "-DGGML_CANN=OFF",
        "-DGGML_OPENCL=OFF",
    ]

    print(f"Configuring with cmake ...")
    result = subprocess.run(cmake_cmd, capture_output=False, text=True)
    if result.returncode != 0:
        print(f"ERROR: cmake configure failed (returncode={result.returncode})")
        return False

    targets = ["test-backend-ops", "llama-app", "llama-cli"]
    for target in targets:
        print(f"Building target {target} ...")
        result = subprocess.run(
            ["cmake", "--build", str(BUILD_DIR), "--target", target],
            capture_output=False, text=True,
        )
        if result.returncode != 0:
            print(f"ERROR: build failed for target {target} (returncode={result.returncode})")
            return False

    print("OK: backend and llama binaries built successfully")
    return True


def main():
    parser = argparse.ArgumentParser(description="Fetch and integrate pinned llama.cpp")
    parser.add_argument("--lock", required=True,
                        help="Path to dependency lock file (e.g., deps/llama-cpp.lock)")
    parser.add_argument("--check", action="store_true",
                        help="Verify existing checkout against lock (exit 0 on success)")
    parser.add_argument("--build", action="store_true",
                        help="Also configure and build the backend")
    args = parser.parse_args()

    lock_path = Path(args.lock)
    if not lock_path.exists():
        print(f"ERROR: lock file not found: {lock_path}")
        sys.exit(1)

    lock = load_lock(lock_path)

    if args.check:
        ok = verify_state(lock)
        sys.exit(0 if ok else 1)

    # Clone + checkout
    if not clone_and_checkout(lock):
        sys.exit(1)

    # Integrate backend
    if not integrate_backend(lock):
        sys.exit(1)

    print(f"\nDone: llama.cpp pinned at {lock['commit'][:12]} with ggml-npu backend")
    print(f"  Source: {LLAMA_CPP}")
    print(f"  Backend: {BACKEND_TARGET.relative_to(PROJECT_ROOT)}")

    if args.build:
        if not build_backend(lock):
            sys.exit(1)
        print(f"  Build: {BUILD_DIR}")

    sys.exit(0)


if __name__ == "__main__":
    main()
