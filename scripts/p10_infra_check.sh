#!/usr/bin/env bash
# p10_infra_check.sh — Phase 10: validate sz0001 EDA infra + resources before RTL verification.
#
# Runs 5 checks ON sz0001 (via p10_ssh):
#   1. vcs version ok      — `vcs -ID` reports a Compiler version (module load already done by p10_ssh)
#   2. firmware build ok   — firmware/build/npu_firmware.elf exists, else `make -C firmware` rebuilds it
#   3. license ok          — lmstat reports license server UP + snpslmd UP for $SNPSLMD_LICENSE_FILE
#   4. cpu ok              — /proc/loadavg 1-min load < 0.8 * nproc
#   5. disk ok             — >= 50 GB free on the partition holding build/
#
# Evidence: build/evidence/task-2-phase10-rtl-verification.txt (written on sz0001,
# shared to the local tree via NFS).
set -euo pipefail
source "$(dirname "$0")/p10_lib/p10_sz0001.sh"

EVID="build/evidence/task-2-phase10-rtl-verification.txt"

echo "[p10_infra_check] validating sz0001 infra (${ZHENGS}@${SZ0001}) ..."

REMOTE_CMD=$(cat <<'REMOTE_EOF'
set +e   # p10_ssh sets -e; we track failures explicitly via $status
EVID="build/evidence/task-2-phase10-rtl-verification.txt"
mkdir -p "$(dirname "$EVID")"
status=0

{
  echo "Task 2 - Phase 10 RTL Verification: sz0001 infra and resource check"
  echo "==================================================================="
  echo "Timestamp : $(date '+%Y-%m-%d %H:%M:%S %Z') (sz0001 local)"
  echo "Host      : $(hostname)"
  echo "Repo root : $PWD"
  echo ""

  # --- Check 1: VCS version ---
  echo "--- Check 1: VCS module + version (vcs -ID) ---"
  VCS_ID=$(timeout 60 vcs -ID 2>&1)
  if echo "$VCS_ID" | grep -q "Compiler version = VCS"; then
    echo "$VCS_ID" | grep -E "vcs script version|Compiler version"
    echo "vcs version ok"
  else
    echo "vcs version FAIL (vcs -ID did not report a compiler version)"
    echo "$VCS_ID" | head -5
    status=1
  fi
  echo ""

  # --- Check 2: firmware build ---
  echo "--- Check 2: firmware build (firmware/build/npu_firmware.elf) ---"
  if [ -f firmware/build/npu_firmware.elf ]; then
    echo "found firmware/build/npu_firmware.elf ($(stat -c%s firmware/build/npu_firmware.elf) bytes)"
    echo "firmware build ok"
  elif timeout 600 make -C firmware >/dev/null 2>&1 && [ -f firmware/build/npu_firmware.elf ]; then
    echo "npu_firmware.elf was missing; make -C firmware rebuilt it"
    echo "firmware build ok"
  else
    echo "firmware build FAIL (npu_firmware.elf missing and make -C firmware failed)"
    status=1
  fi
  echo ""

  # --- Check 3: VCS license ---
  echo "--- Check 3: VCS license availability (lmstat) ---"
  LMSTAT_BIN=$(command -v lmstat || echo /home/EDA/license/t/bin/lmstat)
  if [ -x "$LMSTAT_BIN" ]; then
    LMSTAT_OUT=$(timeout 40 "$LMSTAT_BIN" -a -c "$SNPSLMD_LICENSE_FILE" 2>&1)
    if echo "$LMSTAT_OUT" | grep -q "license server UP" && echo "$LMSTAT_OUT" | grep -q "snpslmd: UP"; then
      echo "$LMSTAT_OUT" | grep -E "license server UP|snpslmd: UP"
      echo "license ok"
    else
      echo "license FAIL (lmstat did not report a UP license server / snpslmd daemon)"
      echo "$LMSTAT_OUT" | head -8
      status=1
    fi
  else
    echo "license FAIL (lmstat not found at $LMSTAT_BIN)"
    status=1
  fi
  echo ""

  # --- Check 4: CPU load ---
  echo "--- Check 4: CPU load (< 0.8 * nproc) ---"
  CORES=$(nproc)
  LOAD=$(awk '{print $1}' /proc/loadavg)
  LIMIT=$(awk -v c="$CORES" 'BEGIN{printf "%.1f", c*0.8}')
  OK=$(awk -v l="$LOAD" -v lim="$LIMIT" 'BEGIN{print (l < lim) ? 1 : 0}')
  if [ "$OK" = "1" ]; then
    echo "load=$LOAD nproc=$CORES limit=$LIMIT"
    echo "cpu ok"
  else
    echo "cpu FAIL (load=$LOAD >= limit=$LIMIT with nproc=$CORES)"
    status=1
  fi
  echo ""

  # --- Check 5: build/ partition disk space ---
  echo "--- Check 5: build/ partition free space (>= 50 GB) ---"
  DISK_FREE_GB=$(df -BG "$PWD/build" 2>/dev/null | awk 'NR==2{print $4}' | tr -d 'G')
  if [ -n "$DISK_FREE_GB" ] && [ "$DISK_FREE_GB" -ge 50 ]; then
    echo "$(df -h "$PWD/build" | tail -1)"
    echo "disk ok (${DISK_FREE_GB} GB free)"
  else
    echo "disk FAIL (free=${DISK_FREE_GB:-?} GB < 50 GB; partition: $(df -h "$PWD/build" 2>/dev/null | tail -1 | awk '{print $1}'))"
    status=1
  fi
  echo ""

  if [ "$status" -eq 0 ]; then
    echo "OVERALL: PASS"
  else
    echo "OVERALL: FAIL"
  fi
} > "$EVID" 2>&1

cat "$EVID"
[ "$status" -eq 0 ]
REMOTE_EOF
)

export -f p10_ssh  # make the sourced shell function available to `timeout bash -c`
if OUT=$(timeout 600 bash -c 'p10_ssh "$1"' _ "$REMOTE_CMD" 2>&1); then
  printf '%s\n' "$OUT"
  echo ""
  echo "[p10_infra_check] all checks passed (evidence: $EVID)"
else
  RC=$?
  printf '%s\n' "$OUT"
  echo ""
  echo "[p10_infra_check] FAILED on sz0001 (exit $RC)" >&2
  exit 1
fi

# Evidence was written on sz0001 (NFS-shared to the local tree).
if [ ! -f "$EVID" ]; then
  echo "[p10_infra_check] evidence file missing: $EVID" >&2
  exit 1
fi
