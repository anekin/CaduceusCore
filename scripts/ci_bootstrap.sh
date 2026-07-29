#!/usr/bin/env bash
#
# ci_bootstrap.sh — CaduceusCore clean-checkout reproducibility baseline
#
# Idempotent bootstrap that brings a fresh Ubuntu 22.04 machine (with
# git, cmake, gcc, python3, and pip pre-installed) to a passing
# software build + release install.  Steps:
#   1. Install missing system packages (cmake, g++, flatc).
#   2. Install Python dependencies (pip -r requirements.txt).
#   3. CMake configure → build (software/ C/C++ runtime + tests).
#   4. Reproducible release build & install.
#
# Firmware (`make -C firmware`) is handled separately by
#   scripts/ci_bootstrap_firmware.sh
# because the RISC-V cross-compiler is an optional prerequisite.
#
# Exit code is the maximum (worst) exit code across all steps.
# Intended usage:
#   bash scripts/ci_bootstrap.sh 2>&1 | tee .omo/evidence/task-w1t5-bootstrap.log
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OVERALL_RC=0

# ── helpers ──────────────────────────────────────────────────────────

# Print a step header and run a command.
# Accumulates the worst exit code in OVERALL_RC.
# Never aborts the caller — even with set -e.
run_step() {
    local label="$1"
    shift
    echo ""
    echo "──────────────────────────────────────────────────────"
    echo "▶ [${label}] $*"
    echo "──────────────────────────────────────────────────────"
    local rc=0
    "$@" || rc=$?
    if [ "$rc" -eq 0 ]; then
        echo "✔ [PASS] ${label}"
    else
        echo "✘ [FAIL] ${label} (exit=${rc})"
        OVERALL_RC="$rc"
    fi
    return 0
}

# Merge a fail-only exit code — used when a step must NOT be fatal but
# we still want to record the failure.
note_fail() {
    local rc="$1"
    local label="$2"
    if [ "$rc" -ne 0 ]; then
        OVERALL_RC="$rc"
        echo "✘ [FAIL] ${label} (exit=${rc})"
    fi
}

# ── step 0: ensure we are at REPO_ROOT ──────────────────────────────

cd "$REPO_ROOT"
echo "REPO_ROOT = ${REPO_ROOT}"
echo "Started at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Detect OS / package manager
PKG_MGR=""
if command -v apt-get &>/dev/null; then
    PKG_MGR="apt"
elif command -v yum &>/dev/null; then
    PKG_MGR="yum"
elif command -v dnf &>/dev/null; then
    PKG_MGR="dnf"
elif command -v brew &>/dev/null; then
    PKG_MGR="brew"
fi
echo "Package manager: ${PKG_MGR:-none}"

# ── step 1: install missing system packages ──────────────────────────

echo ""
echo "── Checking system packages ──"

# cmake
if command -v cmake &>/dev/null; then
    echo "✔ cmake found: $(cmake --version | head -1)"
else
    echo "⚠ cmake NOT found — installing …"
    if [ "$PKG_MGR" = "apt" ]; then
        run_step "apt-install-cmake" sudo apt-get update -qq
        run_step "apt-install-cmake" sudo apt-get install -y -qq cmake
    elif [ "$PKG_MGR" = "yum" ] || [ "$PKG_MGR" = "dnf" ]; then
        run_step "yum-install-cmake" sudo "${PKG_MGR}" install -y cmake
    elif [ "$PKG_MGR" = "brew" ]; then
        run_step "brew-install-cmake" brew install cmake
    else
        echo "✘ Unknown package manager — install cmake manually"
        OVERALL_RC=1
    fi
fi

# g++ (required by cmake C++ build)
if command -v g++ &>/dev/null; then
    echo "✔ g++ found: $(g++ --version | head -1)"
else
    echo "⚠ g++ NOT found — installing …"
    if [ "$PKG_MGR" = "apt" ]; then
        run_step "apt-install-g++" sudo apt-get install -y -qq g++
    elif [ "$PKG_MGR" = "yum" ] || [ "$PKG_MGR" = "dnf" ]; then
        run_step "yum-install-gcc-c++" sudo "${PKG_MGR}" install -y gcc-c++
    elif [ "$PKG_MGR" = "brew" ]; then
        run_step "brew-install-gcc" brew install gcc
    fi
fi

# flatc (FlatBuffers compiler) — best-effort, never fatal
# The CMake build uses FlatBuffers headers from the expected include path
# (/tmp/flatbuffers-25.2.10/include).  flatc the CLI tool is only needed
# for schema regeneration and is optional for the baseline build.
if command -v flatc &>/dev/null; then
    echo "✔ flatc found: $(flatc --version 2>&1 || true)"
else
    echo "⚠ flatc NOT found — attempting best-effort install …"
    FLATC_INSTALLED=false
    if [ "$PKG_MGR" = "apt" ]; then
        # Suppress stderr; don't use run_step so failure doesn't affect OVERALL_RC
        if sudo apt-get install -y -qq flatbuffers-compiler 2>/dev/null; then
            FLATC_INSTALLED=true
        else
            echo "  (flatbuffers-compiler package not available — expected on some distros)"
        fi
    fi
    if [ "$FLATC_INSTALLED" = false ]; then
        # Best-effort: download a prebuilt flatc binary
        FLATC_VER="25.2.10"
        FLATC_URL="https://github.com/google/flatbuffers/releases/download/v${FLATC_VER}/Linux.flatc.binary.clang++-18.zip"
        echo "  → downloading flatc ${FLATC_VER} from GitHub (best-effort) …"
        if command -v curl &>/dev/null; then
            curl -sSL "$FLATC_URL" -o /tmp/flatc.zip 2>/dev/null || true
        elif command -v wget &>/dev/null; then
            wget -q "$FLATC_URL" -O /tmp/flatc.zip 2>/dev/null || true
        fi
        if [ -f /tmp/flatc.zip ]; then
            unzip -o -q /tmp/flatc.zip -d /tmp/flatc-bin 2>/dev/null || true
            if [ -f /tmp/flatc-bin/flatc ]; then
                sudo cp /tmp/flatc-bin/flatc /usr/local/bin/flatc 2>/dev/null || true
                sudo chmod +x /usr/local/bin/flatc 2>/dev/null || true
                echo "✔ flatc installed to /usr/local/bin/flatc"
            fi
            rm -f /tmp/flatc.zip
            rm -rf /tmp/flatc-bin
        fi
    fi
    if command -v flatc &>/dev/null; then
        echo "✔ flatc now available"
    else
        echo "⚠ flatc still not found — continuing (not required for baseline build)"
    fi
fi

# ── step 2: install Python dependencies ──────────────────────────────

run_step "pip-install" python3 -m pip install --quiet -r requirements.txt

# ── step 3: cmake configure + build ──────────────────────────────────

run_step "cmake-configure" cmake -S software -B build/software \
    -DCADUCEUS_BUILD_TESTS=ON \
    -DCMAKE_BUILD_TYPE=Release

N_JOBS="$(nproc 2>/dev/null || echo 4)"
run_step "cmake-build" cmake --build build/software -- -j"${N_JOBS}"

# ── step 4: run CTest ────────────────────────────────────────────────

run_step "ctest" ctest --test-dir build/software --output-on-failure

# ── step 5: reproducible release build & install ─────────────────────

run_step "release-build" python3 scripts/build_software_release.py \
    --clean --install-prefix build/install

# ── step 6: check key output artifacts exist ─────────────────────────

echo ""
echo "── Verifying output artifacts ──"
ARTIFACTS=(
    "build/install/lib/libcaduceus_runtime.so"
    "build/install/include/caduceus/runtime.h"
)

all_ok=true
for artifact in "${ARTIFACTS[@]}"; do
    if [ -f "$artifact" ]; then
        echo "✔ ${artifact}"
    else
        echo "✘ ${artifact} MISSING"
        all_ok=false
    fi
done

# Symlink guard: ensure the old software/build/ symlink is NOT a broken
# pointer (it was removed from git in W1-T3, recreated by CMake POST_BUILD).
if [ -L "software/build/libcaduceus_runtime.so" ]; then
    if [ -e "software/build/libcaduceus_runtime.so" ]; then
        echo "✔ software/build/libcaduceus_runtime.so → valid symlink"
    else
        echo "✘ software/build/libcaduceus_runtime.so is a BROKEN symlink"
        all_ok=false
    fi
fi

if [ "$all_ok" = false ]; then
    note_fail 1 "artifact-check"
fi

# ── summary ──────────────────────────────────────────────────────────

echo ""
echo "══════════════════════════════════════════════════════"
echo "ci_bootstrap.sh finished at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Overall exit code: ${OVERALL_RC}"
echo "══════════════════════════════════════════════════════"

exit "$OVERALL_RC"
