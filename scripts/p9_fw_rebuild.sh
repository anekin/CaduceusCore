#!/usr/bin/env bash
set -euo pipefail
source "$(dirname $0)/p9_lib/p9_sz0001.sh"
# Build firmware locally (RISC-V toolchain on sz0002, NFS-shared; not on sz0001)
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/firmware" && make clean && make
cd "$ROOT"
elf_ts=$(stat -c %Y firmware/build/npu_firmware.elf)
src_ts=$(stat -c %Y firmware/npu_firmware.c)
test "$elf_ts" -gt "$src_ts"
md5sum firmware/build/npu_firmware.elf > build/evidence/ph9-firmware-baseline.txt
git rev-parse HEAD >> build/evidence/ph9-firmware-baseline.txt
