#!/usr/bin/env python3
"""
Reproducible build/install/package script for CaduceusCore Runtime.

Builds the C/C++ runtime libraries, public headers, command IR, and Python
binding; runs CTest; installs all artifacts to a prefix; then runs installed
smoke tests against the installed artifacts.

Usage:
    python3 scripts/build_software_release.py [--clean] [--install-prefix DIR]

Options:
    --clean           Remove old build and install directories before building.
    --install-prefix  Installation prefix (default: build/install).
    --build-dir       CMake build directory (default: build/software).
"""

import argparse
import os
import shutil
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOFTWARE_DIR = os.path.join(REPO_ROOT, "software")


def run(cmd, *, cwd=None, env=None, label=""):
    """Run a command, print its output, return exit code."""
    prefix = f"[{label}] " if label else ""
    stamp = time.strftime("%H:%M:%S")
    full_cmd = cmd if isinstance(cmd, str) else " ".join(cmd)
    print(f"{stamp} {prefix}$ {full_cmd}", flush=True)
    proc = subprocess.run(
        cmd,
        cwd=cwd or REPO_ROOT,
        env=env or os.environ.copy(),
        capture_output=True,
        text=True,
    )
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def check_cmake():
    """Verify cmake is available."""
    rc = run(["cmake", "--version"], label="cmake-check")
    if rc != 0:
        print("ERROR: cmake not found in PATH", file=sys.stderr)
        return False
    return True


def check_flatbuffers():
    """Verify the FlatBuffers headers exist at the expected path."""
    fb_include = "/tmp/flatbuffers-25.2.10/include"
    if not os.path.isdir(fb_include):
        print(f"WARNING: FlatBuffers not found at {fb_include}", file=sys.stderr)
        print("  The build may fail. Install FlatBuffers 25.2.10 to /tmp/flatbuffers-25.2.10", file=sys.stderr)
    return True  # non-fatal


def main():
    parser = argparse.ArgumentParser(description="Build and install CaduceusCore Runtime")
    parser.add_argument("--clean", action="store_true",
                        help="Remove old build and install directories")
    parser.add_argument("--install-prefix", default="build/install",
                        help="Installation prefix (default: build/install)")
    parser.add_argument("--build-dir", default="build/software",
                        help="CMake build directory (default: build/software)")
    args = parser.parse_args()

    install_prefix = os.path.join(REPO_ROOT, args.install_prefix)
    build_dir = os.path.join(REPO_ROOT, args.build_dir)

    env = os.environ.copy()
    # Set the shared library path so ctypes can find libcaduceus_runtime.so
    install_lib = os.path.join(install_prefix, "lib")
    env["LD_LIBRARY_PATH"] = install_lib
    env["CADUCEUS_RUNTIME_LIB"] = os.path.join(install_lib, "libcaduceus_runtime.so")

    evidence_lines = []
    overall_rc = 0

    def log(msg, rc=0):
        evidence_lines.append(f">{' ' if rc == 0 else 'E'} {msg}")
        if rc != 0:
            nonlocal overall_rc
            overall_rc = max(overall_rc, rc)

    # ── Step 0: clean ────────────────────────────────────────────────

    if args.clean:
        for d in [build_dir, install_prefix]:
            if os.path.exists(d):
                print(f"Removing {d} ...", flush=True)
                shutil.rmtree(d)
        log("clean: removed old build and install directories")

    # ── Step 1: prereq checks ────────────────────────────────────────

    if not check_cmake():
        sys.exit(1)
    check_flatbuffers()
    log("prereq-checks: cmake available")

    # ── Step 2: cmake configure ──────────────────────────────────────

    os.makedirs(build_dir, exist_ok=True)
    rc = run([
        "cmake",
        "-S", SOFTWARE_DIR,
        "-B", build_dir,
        "-DCADUCEUS_BUILD_TESTS=ON",
        "-DCMAKE_INSTALL_PREFIX=" + install_prefix,
        "-DCMAKE_BUILD_TYPE=Release",
    ], label="configure")
    log(f"cmake configure: exit={rc}", rc)

    # ── Step 3: cmake build ──────────────────────────────────────────

    import multiprocessing
    n_jobs = str(multiprocessing.cpu_count())
    rc = run([
        "cmake", "--build", build_dir,
        "--", f"-j{n_jobs}",
    ], label="build")
    log(f"cmake build: exit={rc}", rc)

    # ── Step 4: ctest ────────────────────────────────────────────────

    rc = run([
        "ctest", "--test-dir", build_dir, "--output-on-failure",
    ], label="ctest")
    log(f"ctest: exit={rc}", rc)

    # ── Step 5: cmake install ────────────────────────────────────────

    rc = run([
        "cmake", "--install", build_dir,
    ], label="install")
    log(f"cmake install: exit={rc}", rc)

    # ── Step 6: install Python binding ────────────────────────────────

    python_install_dir = os.path.join(install_prefix, "share", "caduceus", "python")
    os.makedirs(python_install_dir, exist_ok=True)

    # Copy the caduceus_runtime.py (already installed by cmake, but ensure
    # setup.py is also available for pip install)
    setup_src = os.path.join(SOFTWARE_DIR, "python", "setup.py")
    setup_dst = os.path.join(python_install_dir, "setup.py")
    if os.path.exists(setup_src):
        shutil.copy2(setup_src, setup_dst)
        log("python: copied setup.py to install prefix", 0)

    # Install the Python package via pip (editable-equivalent: just verify it works)
    rc = run([
        sys.executable, "-m", "pip", "install", "--no-build-isolation", "--upgrade",
        "--target", python_install_dir,
        python_install_dir,
    ], label="pip-install")
    log(f"pip install: exit={rc}", rc)

    # ── Step 7: run installed smoke tests ─────────────────────────────

    smoke_script = os.path.join(REPO_ROOT, "scripts", "run_installed_smoke_tests.py")
    if os.path.exists(smoke_script):
        smoke_env = env.copy()
        smoke_env["CADUCEUS_INSTALL_PREFIX"] = install_prefix
        smoke_env["PYTHONPATH"] = install_prefix
        rc = run([
            sys.executable, smoke_script,
            "--install-prefix", install_prefix,
        ], label="smoke-tests", env=smoke_env)
        log(f"smoke tests: exit={rc}", rc)
    else:
        log("smoke tests: script not found (skipped)", -1)

    # ── Write evidence log ────────────────────────────────────────────

    evidence_dir = os.path.join(REPO_ROOT, ".omo", "evidence")
    os.makedirs(evidence_dir, exist_ok=True)
    evidence_path = os.path.join(evidence_dir, "task-22-release-build.log")
    with open(evidence_path, "w") as f:
        f.write(f"# CaduceusCore Task 22a — Release Build Evidence\n")
        f.write(f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# Install prefix: {install_prefix}\n")
        f.write(f"# Build dir: {build_dir}\n\n")
        for line in evidence_lines:
            f.write(line + "\n")
        f.write(f"\n# Overall exit code: {overall_rc}\n")

    print(f"\nEvidence written to {evidence_path}")
    print(f"Overall exit: {overall_rc}")
    sys.exit(overall_rc)


if __name__ == "__main__":
    main()
