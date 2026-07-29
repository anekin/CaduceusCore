#!/usr/bin/env bash
#
# ci_bootstrap_firmware.sh — CaduceusCore firmware build bootstrap
#
# Builds the NPU firmware (RISC-V RV32IM bare-metal) when the RISC-V
# cross-compiler toolchain is available; gracefully skips otherwise.
#
# Prerequisites (all optional — script exits 0 if any are missing):
#   - riscv64-unknown-elf-gcc   (RISC-V GCC cross-compiler)
#   - riscv64-unknown-elf-objcopy
#   - riscv64-unknown-elf-objdump
#   - riscv64-unknown-elf-size
#
# Typical install on Ubuntu 22.04:
#   sudo apt-get install -y gcc-riscv64-unknown-elf
#
# The firmware build is NOT required for the software baseline
# (ci_bootstrap.sh).  It is a separate, optional step needed only
# for Spike simulation and real-firmware signoff (CI tier L3).
#
# Intended usage:
#   bash scripts/ci_bootstrap_firmware.sh 2>&1 | tee .omo/evidence/task-w1t5-firmware.log
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "REPO_ROOT = ${REPO_ROOT}"
echo "Started at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ── prerequisite check ───────────────────────────────────────────────
#
# The firmware uses the riscv64-unknown-elf-* toolchain to compile
# bare-metal RV32IM code.  On Ubuntu 22.04 this is typically provided
# by the gcc-riscv64-unknown-elf package.
#
# The default prefix in firmware/Makefile is:
#   TOOLCHAIN_DIR ?= /usr
#   PREFIX       ?= riscv64-unknown-elf-
#
# So we check for /usr/bin/riscv64-unknown-elf-gcc.

TOOLCHAIN_PREFIX="${TOOLCHAIN_DIR:-/usr}/bin/${PREFIX:-riscv64-unknown-elf-}"
REQUIRED_BINS=(
    "${TOOLCHAIN_PREFIX}gcc"
    "${TOOLCHAIN_PREFIX}objcopy"
    "${TOOLCHAIN_PREFIX}objdump"
    "${TOOLCHAIN_PREFIX}size"
)

echo ""
echo "── Checking RISC-V toolchain prerequisites ──"
echo "Toolchain prefix: ${TOOLCHAIN_PREFIX}"

MISSING=()
for bin in "${REQUIRED_BINS[@]}"; do
    if command -v "$bin" &>/dev/null; then
        echo "✔ ${bin}"
    else
        echo "✘ ${bin} NOT FOUND"
        MISSING+=("$bin")
    fi
done

if [ "${#MISSING[@]}" -gt 0 ]; then
    echo ""
    echo "══════════════════════════════════════════════════════════"
    echo "  SKIPPED: RISC-V firmware build"
    echo ""
    echo "  The RISC-V cross-compiler toolchain is not installed."
    echo "  Missing binaries:"
    for m in "${MISSING[@]}"; do
        echo "    - ${m}"
    done
    echo ""
    echo "  To install on Ubuntu 22.04:"
    echo "    sudo apt-get install -y gcc-riscv64-unknown-elf"
    echo ""
    echo "  This is expected — the firmware build is NOT required"
    echo "  for the software baseline.  It is only needed for"
    echo "  CI tier L3 (Spike simulation) and real-firmware signoff."
    echo ""
    echo "  Run ci_bootstrap_firmware.sh again after installing"
    echo "  the toolchain to build the firmware."
    echo "══════════════════════════════════════════════════════════"
    echo ""
    echo "Overall exit code: 0 (skipped — no toolchain)"
    exit 0
fi

# ── prerequisites available — build firmware ─────────────────────────

echo ""
echo "── All prerequisites found — building firmware ──"

echo ""
echo "⇒ make -C firmware clean"
make -C firmware clean

echo ""
echo "⇒ make -C firmware all"
make -C firmware all

# ── verify output artifacts ──────────────────────────────────────────

echo ""
echo "── Verifying firmware artifacts ──"
FW_ARTIFACTS=(
    "firmware/build/npu_firmware.elf"
    "firmware/build/npu_firmware.hex"
    "firmware/build/npu_firmware_spike.elf"
)

all_ok=true
for artifact in "${FW_ARTIFACTS[@]}"; do
    if [ -f "$artifact" ]; then
        sz="$(du -h "$artifact" | cut -f1)"
        echo "✔ ${artifact} (${sz})"
    else
        echo "✘ ${artifact} MISSING"
        all_ok=false
    fi
done

echo ""
echo "══════════════════════════════════════════════════════"
echo "ci_bootstrap_firmware.sh finished at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [ "$all_ok" = true ]; then
    echo "Overall exit code: 0  (firmware build PASSED)"
    exit 0
else
    echo "Overall exit code: 1  (firmware build FAILED — missing artifacts)"
    exit 1
fi
echo "══════════════════════════════════════════════════════"
