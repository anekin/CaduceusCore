#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/p9_lib/p9_sz0001.sh"

ID=""
TYPE=""
SYMPTOM=""
ROOT_CAUSE=""
EVIDENCE=""
VERDICT=""
RTL_REPORT=""

usage() {
  cat <<EOF
Usage:
  p9_log_bug.sh --id BUG-RTL-SOC-NNN --type <fw|rtl|integ> \\
                --symptom "..." --root_cause "..." \\
                --evidence <path> --verdict <resolved|open|rtl-suspect>
  p9_log_bug.sh --rtl-report <slug>
  p9_log_bug.sh --help

--rtl-report generates docs/bugs/<slug>.md and appends a tracker entry to
docs/bugs/bugs-soc-rtl.md. The caller provides the complete slug (e.g.
BUG-MXU-P9-001-doorbell-divergence).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --id) ID="$2"; shift 2 ;;
    --type) TYPE="$2"; shift 2 ;;
    --symptom) SYMPTOM="$2"; shift 2 ;;
    --root_cause) ROOT_CAUSE="$2"; shift 2 ;;
    --evidence) EVIDENCE="$2"; shift 2 ;;
    --verdict) VERDICT="$2"; shift 2 ;;
    --rtl-report) RTL_REPORT="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

BUGS_MD="${REPO_ROOT}/docs/bugs/bugs-soc-rtl.md"
mkdir -p "$(dirname "$BUGS_MD")"
touch "$BUGS_MD"

TODAY=$(date -u +%Y-%m-%d)

append_bugs_md() {
  local bid="$1"
  local btype="$2"
  local severity="$3"
  local symptom="$4"
  local root="$5"
  local evidence="$6"
  local status="$7"

  cat >> "$BUGS_MD" <<EOF

### ${bid}

| 字段 | 内容 |
|------|------|
| **Date** | ${TODAY} |
| **Block** | Phase 9 T3 |
| **Severity** | ${severity} |
| **Type** | ${btype} |
| **Status** | ${status} |

#### Symptom

${symptom}

#### Root Cause

${root}

#### Fix

Pending fix per Phase 9 plan.

#### Verification

Evidence: ${evidence}
EOF
}

if [[ -n "$RTL_REPORT" ]]; then
  REPORT_MD="${REPO_ROOT}/docs/bugs/${RTL_REPORT}.md"
  mkdir -p "$(dirname "$REPORT_MD")"

  # Pull the latest conclusion/citation from the divergence report if present.
  REPORT_TXT="${REPO_ROOT}/build/evidence/ph9-divergence-report.txt"
  CONCLUSION_LINE=""
  CITATION_LINE=""
  if [[ -s "$REPORT_TXT" ]]; then
    CONCLUSION_LINE=$(grep -m1 -E '^CONCLUSION: \([ABC]\): ' "$REPORT_TXT" || true)
    CITATION_LINE=$(grep -m1 -E 'Citation: ' "$REPORT_TXT" || true)
  fi

  cat > "$REPORT_MD" <<EOF
# ${RTL_REPORT} — Doorbell Divergence Diagnostic

**Date:** ${TODAY}
**Phase:** Phase 9 Wave 1
**Trigger:** T3 divergence sweep

## Symptom

M=1 multi-tile MMUL via firmware doorbell dispatch shows cos_sim < 0.999,
while the same (M,K,N) executed through direct wrapper preload reaches
cos_sim ~1.0.

${CONCLUSION_LINE}
${CITATION_LINE}

## Probe Evidence

Probe snapshots were captured in:

- \`build/evidence/ph9-probe-case{1,2,3}-direct-K*.jsonl\`
- \`build/evidence/ph9-probe-case{1,2,3}-firmware-K*.jsonl\`

Each snapshot contains >=5 wrapper/internal signal samples (preload FSM,
broadcast driver, store-out FIFO, AXI channels, MXU debug) recorded via
cocotb VPI backdoor with no RTL/firmware modification.

## Root Cause Verdict

${CONCLUSION_LINE:-CONCLUSION: (C): not yet determined — see build/evidence/ph9-divergence-report.txt}

${CITATION_LINE:-Citation: npu_firmware.c:199-201}

## Recommended Fix

- If verdict (A): remove or gate the redundant I/W/O_ADDR writes in
  \`firmware/npu_firmware.c:199-201\` so that \`mxu_wrapper_preload\` is the
  single source of preload address state.
- If verdict (B): correct the broadcast/store-out geometry in
  \`rtl/wrapper/mxu_soc_wrapper.v\` around the cited lines.
- If verdict (C): run additional focused probes before Wave 2.

## Verification Plan

1. Re-run \`bash scripts/p9_divergence_sweep.sh\` after the chosen fix.
2. Confirm all three M=1 cases reach cos_sim >= 0.999 via firmware doorbell.
3. Run \`bash scripts/p9_causality.sh\` to prove the fix is causal.
EOF

  # Append tracker entry
  append_bugs_md \
    "$RTL_REPORT" \
    "RTL Wrapper / Firmware Interaction" \
    "Major" \
    "M=1 multi-tile MMUL cos_sim < 0.999 via firmware doorbell; direct wrapper preload passes." \
    "See independent report ${REPORT_MD}." \
    "build/evidence/ph9-divergence-report.txt, build/evidence/ph9-probe-*.jsonl" \
    "rtl-suspect"

  echo "[p9_log_bug] RTL report written: ${REPORT_MD}"
  echo "[p9_log_bug] Tracker appended: ${BUGS_MD}"
  exit 0
fi

if [[ -z "$ID" || -z "$TYPE" || -z "$SYMPTOM" || -z "$ROOT_CAUSE" || -z "$EVIDENCE" || -z "$VERDICT" ]]; then
  echo "Missing required args for --id mode."
  usage
  exit 1
fi

append_bugs_md "$ID" "$TYPE" "Major" "$SYMPTOM" "$ROOT_CAUSE" "$EVIDENCE" "$VERDICT"
echo "[p9_log_bug] Bug logged: ${ID} -> ${BUGS_MD}"
