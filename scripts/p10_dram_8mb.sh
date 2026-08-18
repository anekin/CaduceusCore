#!/usr/bin/env bash
# =============================================================================
# p10_dram_8mb.sh — Phase 10 Todo 19 (Wave 5): BUG-RTL-SOC-002 DRAM 8MB
# window constraint — low-regression-risk fix + FM-SOC regression gate
#
# What this script does:
#   Stage 0 (local sz0002): rebuild firmware (RISC-V toolchain lives on
#     sz0002; NFS-shared) and run a static DRAM-window audit:
#       - .data_dram section must lie entirely inside [0x80000000, 0x80800000)
#       - every DRAM-region pointer word embedded in dram_init.hex
#         (the pre-loaded test data) must be < 0x80800000
#       - the new dram_range_ok constraint must be present in the built ELF
#   Stage 1 (sz0001 via p10_ssh): full 33-case FM-SOC RTL regression with the
#     freshly built firmware hex (+BOOTROM_HEX is loaded at runtime, so the
#     existing simv_soc_ibex is reused; recompiled only if missing).
#
# Constraint policy (BUG-RTL-SOC-002, todo 19):
#   REJECT, not wrap. The firmware's dispatch_cmd now validates every
#   descriptor address range (desc, MMUL I/W/O/scale, SFU/ROPE I/O, Vector
#   A/B/O, DMA_COPY src/dst, PCIe_DMA axi) against the 8 MB window
#   [DRAM_BASE, DRAM_BASE+0x00800000). An out-of-window DRAM range marks the
#   command failed (status=1, LAST_STATUS marker 0x000070xx) without issuing
#   the transaction. Wrapping was deliberately avoided: it would silently
#   alias two distinct buffers into the same physical window.
#
# Verdict:
#   FM-SOC 33/33 PASS, or 32/33 with ONLY the known pre-existing FM-SOC-10X
#   residual ("op00 RMSNORM pre-attn: SFU mismatch") → exit 0. Any other
#   FM-SOC failure, any fired constraint marker, or any audit failure → exit 1.
#
# Usage:
#   bash scripts/p10_dram_8mb.sh
#
# Evidence:
#   build/evidence/task-19-phase10-rtl-verification.txt   (final report)
#   build/evidence/task-19-phase10-regression-run.log     (full run log)
# =============================================================================
set -u

source "$(dirname "$0")/p10_lib/p10_sz0001.sh"

# The p10 lib sets `set -euo pipefail`. This runner tracks failures explicitly
# (evidence must be written even when a stage fails, and parse greps that find
# no match must not kill the run), so relax errexit and pipefail here.
set +e
set +o pipefail

ROOT="$REPO_ROOT"
EVIDENCE="$ROOT/build/evidence"
OUT_FILE="$EVIDENCE/task-19-phase10-rtl-verification.txt"
RUN_LOG="$EVIDENCE/task-19-phase10-regression-run.log"
mkdir -p "$EVIDENCE"

# Single-instance guard: two concurrent runners would corrupt each other's
# stage logs and evidence. Fail fast (exit 3) if another runner is active.
LOCK_FILE="$EVIDENCE/task-19.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[p10_dram8mb] ABORT: another p10_dram_8mb instance holds $LOCK_FILE (pid $(cat "$LOCK_FILE" 2>/dev/null || echo unknown))"
  exit 3
fi
echo "$$" > "$LOCK_FILE"

# log() prints to stdout AND appends to the run log directly (no tee: GNU tee
# fully buffers file output, which would hide progress from pollers).
log() { echo "[p10_dram8mb] $*"; echo "[p10_dram8mb] $*" >> "$RUN_LOG"; }
ts()  { date -u +%Y-%m-%dT%H:%M:%SZ; }

COMMIT="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo "?")"
TS_START="$(ts)"

failures=()
record_failure() { failures+=("$*"); log "FAIL: $*"; }

# Trap: guarantee the evidence file exists even if the script is interrupted
# or a stage behaves unexpectedly. The flag also overwrites any stale
# evidence from a previous aborted run so a fresh crash is never masked.
EVIDENCE_WRITTEN=0
trap 'if [ "$EVIDENCE_WRITTEN" = "0" ]; then
  {
    echo "Task 19 - Phase 10 RTL Verification: INCOMPLETE"
    echo "=============================================="
    echo "Timestamp : $(ts)"
    echo "Commit    : ${COMMIT:-?}"
    echo "Status    : interrupted before final evidence write"
    echo "Run log   : build/evidence/task-19-phase10-regression-run.log"
  } > "${OUT_FILE}" 2>/dev/null || true
fi' EXIT

# =============================================================================
# run_remote_stage <name> <timeout_s> <logfile> <body>
#
# Writes <body> to a temp script on sz0001, executes it under a remote
# watchdog (setsid process-group kill after <timeout_s> → no stray simv),
# captures STAGE_EXIT.
# =============================================================================
run_remote_stage() {
  local name="$1" timeout_s="$2" logfile="$3" body="$4"
  local remote_cmd stage_rc ssh_rc
  local t_start t_end

  # log() lines go to stderr so the caller's $(...) captures only the rc.
  log "Stage ${name}: start ($(ts))" >&2
  t_start=$(date +%s)

  body=${body//__ROOT__/$ROOT}

  remote_cmd="set +e
TMPSTAGE=/tmp/p10dram8mb_${name}_\$\$.sh
cat > \"\$TMPSTAGE\" <<'STAGE_EOF'
${body}
STAGE_EOF
# Run the stage in its own process group (setsid) with a detached watchdog
# (own group, stdio closed) that kills the WHOLE stage group after the
# timeout. The watchdog itself is group-killed when the stage finishes, so
# neither orphaned simv nor orphaned sleep can linger or hold the SSH
# channel open after the stage completes.
setsid bash \"\$TMPSTAGE\" &
SPID=\$!
setsid bash -c 'sleep ${timeout_s}; kill -TERM -\$1 2>/dev/null; sleep 10; kill -KILL -\$1 2>/dev/null' _ \$SPID </dev/null >/dev/null 2>&1 &
KILLER=\$!
wait \$SPID
rc=\$?
kill -TERM -\$KILLER 2>/dev/null
sleep 1
kill -KILL -\$KILLER 2>/dev/null
echo \"STAGE_EXIT=\$rc\"
rm -f \"\$TMPSTAGE\"
exit 0"

  p10_ssh "$remote_cmd" > "$logfile" 2>&1
  ssh_rc=$?

  stage_rc=$(grep -oE '^STAGE_EXIT=[0-9]+' "$logfile" | tail -1 | cut -d= -f2)
  if [ -z "$stage_rc" ]; then
    stage_rc="ssh-error-${ssh_rc}"
  fi

  t_end=$(date +%s)
  log "Stage ${name}: done ($(ts), STAGE_EXIT=${stage_rc}, elapsed=$((t_end - t_start))s, log=$logfile)" >&2
  echo "$stage_rc"
}

# =============================================================================
# Pre-flight: the constraint must exist in the firmware source (guards against
# running this gate on a branch where the todo-19 fix is absent).
# =============================================================================
if ! grep -q "dram_range_ok" "$ROOT/firmware/npu_firmware.c"; then
  record_failure "firmware/npu_firmware.c does not contain the dram_range_ok constraint (todo 19 fix missing)"
  verdict_early=1
fi

# =============================================================================
# Stage 0 — firmware rebuild + static DRAM-window audit (local sz0002)
# =============================================================================
FW_LOG="$EVIDENCE/task-19-fw-build.log"
log "Stage fwbuild: start (local sz0002, RISC-V toolchain host)"
FW_RC=0
if [ "${verdict_early:-0}" = "0" ]; then
  {
    cd "$ROOT/firmware"
    make clean
    make
  } > "$FW_LOG" 2>&1
  FW_RC=$?
fi
log "Stage fwbuild: make rc=${FW_RC} (log: $FW_LOG)"

if [ "$FW_RC" -ne 0 ]; then
  record_failure "firmware rebuild failed (rc=$FW_RC) — see $FW_LOG"
else
  elf_ts=$(stat -c %Y "$ROOT/firmware/build/npu_firmware.elf" 2>/dev/null || echo 0)
  src_ts=$(stat -c %Y "$ROOT/firmware/npu_firmware.c" 2>/dev/null || echo 0)
  if [ "$elf_ts" -le "$src_ts" ]; then
    record_failure "firmware ELF not newer than source (stale build)"
  fi
  if ! riscv64-unknown-elf-nm "$ROOT/firmware/build/npu_firmware.elf" 2>/dev/null | grep -q "dram_range_ok"; then
    record_failure "dram_range_ok symbol missing from built ELF (constraint not compiled in)"
  fi

  # Audit A: .data_dram section must sit entirely inside the 8 MB window.
  map_line=$(grep -E '^ *\.data_dram +0x0000000080000000' "$ROOT/firmware/build/npu_firmware.map" | head -1)
  map_size=$(echo "$map_line" | awk '{print $3}')
  if [ -z "$map_size" ]; then
    record_failure ".data_dram audit: section not found in npu_firmware.map"
  else
    map_end=$(( 0x80000000 + map_size ))
    if [ "$map_end" -gt $((0x80800000)) ]; then
      record_failure ".data_dram audit: end=0x$(printf '%X' "$map_end") beyond 0x80800000"
    else
      log "Stage fwbuild audit: .data_dram [0x80000000, 0x$(printf '%X' "$map_end")) inside 8 MB window"
    fi
  fi

  # Audit B: every DRAM-region pointer word embedded in the pre-loaded test
  # data (dram_init.hex) must be < 0x80800000.
  AUDIT_OUT=$(python3 - "$ROOT/firmware/build/dram_init.hex" <<'PYEOF'
import sys
path = sys.argv[1]
dram_words, bad = [], []
for line in open(path, encoding="utf-8"):
    v = int(line.strip(), 16)
    if 0x80000000 <= v < 0x90000000:
        dram_words.append(v)
        if v >= 0x80800000:
            bad.append(v)
print(f"embedded_dram_pointer_words={len(dram_words)} out_of_8mb_window={len(bad)}")
for v in bad[:8]:
    print(f"  OUT-OF-WINDOW: {v:#010x}")
sys.exit(1 if bad else 0)
PYEOF
)
  AUDIT_RC=$?
  log "Stage fwbuild audit: $AUDIT_OUT"
  if [ "$AUDIT_RC" -ne 0 ]; then
    record_failure "dram_init.hex contains embedded DRAM pointers beyond 0x80800000"
  fi
fi

# =============================================================================
# Stage 1 — FM-SOC 33-case regression (sz0001 via p10_ssh)
# =============================================================================
FM_SOC_LOG="$EVIDENCE/task-19-fm-soc.log"
FM_SOC_BODY=$(cat <<'STAGE_EOF'
set -euo pipefail
cd __ROOT__
source sim/regression/run_env.sh >/dev/null 2>&1
# simv_soc_ibex reuse: no RTL change in this todo; the firmware hex is loaded
# at runtime via +BOOTROM_HEX, so the rebuilt firmware is picked up.
# run_ibex_full_rtl.sh recompiles the binary only if it is missing.
bash sim/regression/run_fm_soc_all.sh
STAGE_EOF
)
FM_SOC_RC=$(run_remote_stage fmsoc 9000 "$FM_SOC_LOG" "$FM_SOC_BODY")

fm_soc_pass=$(grep -cE '^\[PASS\] FM-SOC-' "$FM_SOC_LOG" || true)
fm_soc_fail=$(grep -cE '^\[FAIL\] FM-SOC-' "$FM_SOC_LOG" || true)
fm_fail_list=$(grep -oE '^\[FAIL\] FM-SOC-[0-9X]+' "$FM_SOC_LOG" 2>/dev/null | sed 's/^\[FAIL\] //' | sort -u | tr '\n' ' ')
[ -n "$fm_soc_pass" ] || fm_soc_pass="UNPARSED"
[ -n "$fm_soc_fail" ] || fm_soc_fail="UNPARSED"
log "Stage fmsoc: PASS=${fm_soc_pass} FAIL=${fm_soc_fail} rc=${FM_SOC_RC} failed_cases=[${fm_fail_list:-none}]"

# Classify the ONLY tolerated residual: FM-SOC-10X with the exact pre-existing
# signature "op00 RMSNORM pre-attn: SFU mismatch" (introduced by the Jul 26
# firmware commits, before any Phase 10 work; deterministic on isolated re-run
# per build/evidence/task-3-phase10-rtl-verification.txt).
fm_10x_rmsnorm=""
fm_fail_details=""
if [ "$fm_soc_fail" -gt 0 ] 2>/dev/null; then
  for case_id in $fm_fail_list; do
    case_log="$ROOT/build/ibex_full_rtl/evidence/${case_id}.log"
    reason=$(grep -oE "${case_id} failed: [^|]*" "$case_log" 2>/dev/null | head -1)
    [ -n "$reason" ] || reason="${case_id}: see build/ibex_full_rtl/evidence/${case_id}.log"
    fm_fail_details="${fm_fail_details}${reason}
"
    if [ "$case_id" = "FM-SOC-10X" ] && grep -E "FM-SOC-10X failed: op00 RMSNORM pre-attn: SFU mismatch" "$case_log" >/dev/null 2>&1; then
      fm_10x_rmsnorm="yes"
    fi
  done
fi

# Self-check: the new constraint marks rejections with LAST_STATUS=0x000070xx.
# Any such marker in a case log means a real out-of-window access was rejected
# — a finding to fix, never to mask.
fw_reject_logs=$(grep -rl "LAST_STATUS=0x000070" "$ROOT/build/ibex_full_rtl/evidence/" 2>/dev/null | tr '\n' ' ')
if [ -n "$fw_reject_logs" ]; then
  record_failure "DRAM-window constraint FIRED during FM-SOC (LAST_STATUS=0x000070xx in: $fw_reject_logs)"
else
  log "Stage fmsoc self-check: no 0x000070xx rejection marker in any case log (constraint never fired)"
fi

# =============================================================================
# Verdict
# =============================================================================
verdict_fail=0
[ "${verdict_early:-0}" -ne 0 ] && verdict_fail=1

if [ "$fm_soc_pass" = "UNPARSED" ] || [ "$fm_soc_fail" = "UNPARSED" ]; then
  record_failure "FM-SOC counts unparsed (log missing or stage failed) — see $FM_SOC_LOG"
  verdict_fail=1
else
  if [ "$fm_10x_rmsnorm" = "yes" ]; then
    fm_expected_pass=32
  else
    fm_expected_pass=33
  fi
  fm_unexpected_fail=0
  [ "$fm_soc_fail" -gt 0 ] && [ "$fm_10x_rmsnorm" != "yes" ] && fm_unexpected_fail=1
  if [ "$fm_soc_pass" -ne "$fm_expected_pass" ] || [ "$fm_unexpected_fail" -eq 1 ]; then
    record_failure "FM-SOC PASS=$fm_soc_pass FAIL=$fm_soc_fail != 33/0 (only the known FM-SOC-10X residual is tolerated)"
    verdict_fail=1
  fi
fi

case "$FM_SOC_RC" in
  124|137|143)
    record_failure "stage fmsoc TIMED OUT (remote watchdog killed it; exit=$FM_SOC_RC)"
    verdict_fail=1
    ;;
  ssh-error-*)
    record_failure "stage fmsoc SSH failure ($FM_SOC_RC) — counts may be unparsed"
    verdict_fail=1
    ;;
  "")
    record_failure "stage fmsoc produced no exit code (log missing)"
    verdict_fail=1
    ;;
  *)
    if [ "$FM_SOC_RC" != "0" ]; then
      log "NOTE: stage fmsoc exit=$FM_SOC_RC (see $FM_SOC_LOG; counts above are the source of truth)"
    fi
    ;;
esac

# =============================================================================
# Cleanup receipt — no stray processes from this run on sz0001
# =============================================================================
log "Cleanup: checking for stray processes from this repo on sz0001"
STRAY_CHECK=$(p10_ssh "pgrep -af '/home/prj/zhengs/caduceuscore/CaduceusCore' 2>/dev/null | grep -E 'simv|p10dram8mb|run_ibex|run_fm_soc' || echo 'NO_STRAY_PROCESSES'" 2>&1 | tail -5)
log "Cleanup check: $STRAY_CHECK"

# =============================================================================
# Evidence file
# =============================================================================
TS_END="$(ts)"
VERDICT="PASS"
[ "$verdict_fail" -ne 0 ] && VERDICT="FAIL"

{
  echo "Task 19 - Phase 10 RTL Verification: BUG-RTL-SOC-002 DRAM 8MB window constraint"
  echo "==============================================================================="
  echo "Timestamp start : ${TS_START}"
  echo "Timestamp end   : ${TS_END}"
  echo "Commit          : ${COMMIT}"
  echo "Driver host     : $(hostname) (sz0002) — firmware build/audit local;"
  echo "                  FM-SOC regression executed on sz0001 via p10_ssh"
  echo "                  (ssh ${ZHENGS}@${SZ0001})"
  echo ""
  echo "dram_window_constraint_applied = yes"
  echo "  (checked before start: no prior task-11 pre-application — no"
  echo "   scripts/p10_dram_8mb.sh and no task-11 DRAM-window evidence existed;"
  echo "   no 'dram' constraint commit in git log at HEAD)"
  echo ""
  echo "Constraint method (low-regression-risk, per todo 19):"
  echo "  1. Policy: REJECT, not wrap. dispatch_cmd() in firmware/npu_firmware.c"
  echo "     validates every descriptor address range before issuing any"
  echo "     transaction. An out-of-window DRAM range marks the command failed"
  echo "     (status=1, completion error, LAST_STATUS marker 0x000070xx)."
  echo "     Wrapping was deliberately avoided: it would silently alias two"
  echo "     distinct buffers into the same physical window (adversarial-QA"
  echo "     'address wrap aliasing' probe — answered by choosing reject)."
  echo "  2. Window: DRAM_BASE=0x80000000, DRAM_SIZE=0x00800000 (8 MB),"
  echo "     DRAM_END=0x80800000 (was 0x0FF00000, an unused documentary macro)."
  echo "     dram_range_ok(addr,size) passes addresses below DRAM_BASE"
  echo "     (SRAM/MMIO/ROM are not DRAM) and rejects DRAM ranges outside"
  echo "     [DRAM_BASE, DRAM_END)."
  echo "  3. Coverage: command desc_addr + MMUL input/weight/output/scale +"
  echo "     SFU input/output + ROPE input/output + Vector a/b/o + DMA_COPY"
  echo "     src/dst + PCIe_DMA axi (sizes match the exact byte counts the"
  echo "     firmware transfers, incl. SFU/Vector scratch rounding)."
  echo "  4. Test configs: NOT modified. Audit of the FM-SOC RTL builders"
  echo "     (sim/rtl_soc_runner.py) shows every case already stays inside the"
  echo "     8 MB window (max P4 result end 0x8075_8000; wgt_dram 0x80200000;"
  echo "     descriptors 0x80000080-0x80001000). The firmware now enforces the"
  echo "     window, so future test configs that escape it fail loudly instead"
  echo "     of triggering backdoor/DRAM errors (the BUG-RTL-SOC-002 symptom)."
  echo "     dram_model.v untouched (16 MB RTL array, 8 MB harness backdoor cap"
  echo "     unchanged — no RTL recompilation risk)."
  echo ""
  echo "Firmware rebuild + static audit (local sz0002, log: ${FW_LOG}):"
  echo "  make clean && make rc=${FW_RC}"
  echo "  dram_range_ok symbol in ELF : $(riscv64-unknown-elf-nm "$ROOT/firmware/build/npu_firmware.elf" 2>/dev/null | grep -q dram_range_ok && echo yes || echo NO)"
  echo "  .data_dram                   : $(grep -E '^ *\.data_dram +0x0000000080000000' "$ROOT/firmware/build/npu_firmware.map" | head -1 | awk '{print "[0x80000000 + " $3 "]"}')"
  echo "  embedded DRAM pointer audit  : ${AUDIT_OUT:-skipped}"
  echo ""
  echo "Commands executed (exact, via p10_ssh on sz0001):"
  echo "  FM-SOC   : bash sim/regression/run_fm_soc_all.sh   (33 cases)"
  echo "             (log: build/evidence/task-19-fm-soc.log)"
  echo ""
  echo "FM-SOC regression (todo 19 acceptance: 33/33, or 32/33 + known 10X residual):"
  if [ "$fm_10x_rmsnorm" = "yes" ]; then
    echo "  fm_soc_pass    = ${fm_soc_pass}    [MATCH(32/33 + 1 known pre-existing FM-SOC-10X residual)]"
  else
    echo "  fm_soc_pass    = ${fm_soc_pass}    [$( [ "$fm_soc_pass" = "33" ] && echo MATCH || echo MISMATCH )]"
  fi
  echo "  fm_soc_fail    = ${fm_soc_fail}"
  if [ "$fm_soc_fail" -gt 0 ] 2>/dev/null; then
    echo "  FM-SOC failure details:"
    echo "    failed case(s): ${fm_fail_list}"
    while IFS= read -r d; do
      [ -n "$d" ] && echo "    reason: ${d}"
    done <<< "$fm_fail_details"
    if [ "$fm_10x_rmsnorm" = "yes" ]; then
      echo "    FM-SOC-10X signature matches the pre-existing failure documented in"
      echo "    build/evidence/f3-final-summary.txt (Phase 9 F3, 2026-08-06,"
      echo "    'CONFIRMED PRE-EXISTING', fails on d6b1adc too) and reproduced"
      echo "    deterministically in Wave 0 (task-3) and todo 8 (task-8)."
      echo "    NOT introduced by the todo 19 constraint."
    fi
  fi
  echo "  constraint self-check: $( [ -z "$fw_reject_logs" ] && echo "no 0x000070xx rejection marker in any case log (constraint never fired)" || echo "CONSTRAINT FIRED in $fw_reject_logs" )"
  echo ""
  echo "Cleanup receipt:"
  echo "  - FM-SOC stage ran synchronously under a remote timeout guard;"
  echo "    no background jobs started by this script."
  echo "  - Stray-process check on sz0001 (repo-scoped): ${STRAY_CHECK}"
  echo ""
  echo "Verification:"
  if [ "$verdict_fail" -ne 0 ]; then
    echo "  FAIL — one or more gates did not pass:"
    for f in "${failures[@]}"; do
      echo "    - $f"
    done
  else
    echo "  PASS — firmware constraint compiled in; static audit clean;"
    echo "  FM-SOC regression matches the todo 19 acceptance criterion."
  fi
  echo ""
  echo "Result: ${VERDICT}"
  echo ""
  echo "Per-stage logs:"
  echo "  ${FW_LOG}"
  echo "  ${FM_SOC_LOG}"
  echo "  ${RUN_LOG}"
} > "$OUT_FILE"
EVIDENCE_WRITTEN=1

log "Evidence written: $OUT_FILE"
cat "$OUT_FILE" >> "$RUN_LOG"

if [ "$verdict_fail" -ne 0 ]; then
  log "DRAM 8MB constraint gate FAILED — see failures above."
  exit 1
fi

log "DRAM 8MB window constraint verified. FM-SOC regression gate passed. Exit 0."
exit 0
