#!/usr/bin/env bash
# p10_fm3_report.sh — todo 17: FM-3 weight-streaming overlap calibration report.
#
# Wave 4 todo 17 (blocked by todo 16, blocks nothing). Reads the todo 15
# measurement evidence (RTL overlap ratio + raw cycle trace) and the todo 16
# calibration evidence (rtl_overlap vs fm_overlap, delta, updated parameter
# values), then generates build/evidence/ph10-fm3-calibration-report.md with
# the four required sections:
#
#   ## Method          — how the measurement and calibration were performed
#   ## Measurement     — RTL vs Func Model overlap data (from todo 15/16)
#   ## Calibration     — parameters adjusted in todo 16
#   ## Residual Error  — |rtl_overlap - fm_overlap| vs the 0.05 threshold
#
# The script then verifies the four section headers exist and writes
# build/evidence/task-17-phase10-rtl-verification.txt with PASS/FAIL.
#
# Exit codes:
#   0 — report generated and section validation passed
#   1 — input evidence missing (report NOT generated), report generation
#       failed, or a required section header is absent
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/p10_lib/p10_sz0001.sh"

EVIDENCE_DIR="$REPO_ROOT/build/evidence"
T15="$EVIDENCE_DIR/task-15-phase10-rtl-verification.txt"
T16="$EVIDENCE_DIR/task-16-phase10-rtl-verification.txt"
T17="$EVIDENCE_DIR/task-17-phase10-rtl-verification.txt"
REPORT="$EVIDENCE_DIR/ph10-fm3-calibration-report.md"

REQUIRED_SECTIONS=(Method Measurement Calibration "Residual Error")

mkdir -p "$EVIDENCE_DIR"

NOW="$(date '+%Y-%m-%d %H:%M:%S')"
HOST="$(hostname 2>/dev/null || echo unknown)"
COMMIT="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"

log_info() { echo "[p10_fm3_report] $*"; }

# extract <file> <key> — print the value of the first "^<key>=" line, empty
# if the key is absent (never fails the script).
extract() {
    grep -E "^${2}=" "$1" 2>/dev/null | head -1 | sed -E "s/^${2}=//" || true
}

# v <value> — render a value or N/A placeholder.
v() {
    if [ -n "$1" ]; then printf '%s' "$1"; else printf 'N/A (not found in evidence)'; fi
}

write_evidence() { # <STATUS> <summary> [<key=value> ...]
    local status="$1" summary="$2"
    shift 2
    {
        echo "# Phase 10 T17: FM-3 weight-streaming overlap calibration report"
        echo "# Generated: $NOW"
        echo "# Host: $HOST"
        echo "# Commit: $COMMIT"
        echo "# Command: bash scripts/p10_fm3_report.sh"
        echo "# Status: $status"
        echo "# Summary: $summary"
        echo "#"
        echo "# Inputs:"
        echo "#   $T15"
        echo "#   $T16"
        echo "# Outputs:"
        echo "#   $REPORT"
        echo "#   $T17"
        echo ""
        echo "STATUS=$status"
        echo "commit=$COMMIT"
        echo "command=bash scripts/p10_fm3_report.sh"
        for kv in "$@"; do echo "$kv"; done
    } > "$T17"
}

main() {
    log_info "Phase 10 T17: FM-3 calibration report"

    # ---- 1. Input guard: never generate the report without upstream evidence.
    local missing=""
    if [ ! -f "$T15" ]; then missing="$missing task-15-phase10-rtl-verification.txt"; fi
    if [ ! -f "$T16" ]; then missing="$missing task-16-phase10-rtl-verification.txt"; fi
    if [ -n "$missing" ]; then
        log_info "FAIL: input evidence missing:$missing"
        write_evidence "FAIL" "input evidence missing:$missing" \
            "report_generated=no" \
            "note=Do not generate the FM-3 report without upstream evidence (todos 15/16). Re-run after those todos land."
        log_info "evidence written: $T17 (FAIL)"
        exit 1
    fi

    # ---- 2. Extract key fields from todo 15 / todo 16 evidence.
    local t15_overlap t15_trace t16_rtl t16_fm t16_delta params
    t15_overlap="$(extract "$T15" 'overlap_ratio')"
    t15_trace="$(extract "$T15" 'cycle_trace')"
    if [ -z "$t15_trace" ]; then t15_trace="$(extract "$T15" 'trace')"; fi
    t16_rtl="$(extract "$T16" 'rtl_overlap')"
    t16_fm="$(extract "$T16" 'fm_overlap')"
    t16_delta="$(extract "$T16" 'delta')"
    if [ -z "$t16_delta" ]; then
        t16_delta="$(grep -E '^\|?delta\|' "$T16" 2>/dev/null | head -1 | sed -E 's/^\|?delta\|//; s/^[[:space:]]*//' || true)"
    fi
    params="$(grep -E 'broadcast_sync|bw_bytes_per_cycle|cross_engine_gap|_accumulate|param' "$T16" 2>/dev/null | grep -v '^#' | head -20 || true)"

    # ---- 3. Derive the residual-error line and verdict.
    local delta_line verdict res_delta
    delta_line="$(v "$t16_delta")"
    res_delta="$(v "$t16_delta")"
    verdict="cannot determine (overlap values missing from evidence)"
    if [ -n "$t16_rtl" ] && [ -n "$t16_fm" ]; then
        local calc
        calc="$(awk -v r="$t16_rtl" -v f="$t16_fm" \
            'BEGIN { d = r - f; if (d < 0) d = -d; printf "%.4f", d }' 2>/dev/null || true)"
        if [ -n "$calc" ]; then
            delta_line="$calc (computed as |rtl_overlap - fm_overlap|)"
            res_delta="$calc"
            verdict="$(awk -v d="$calc" 'BEGIN { print (d <= 0.05) ? "PASS: |delta| <= 0.05" : "FAIL: |delta| > 0.05" }')"
        fi
    fi

    # ---- 4. Generate the report.
    cat > "$REPORT" <<REPORT_EOF
# FM-3 Weight-Streaming Overlap Calibration Report

> Phase 10 todo 17 deliverable — generated by \`scripts/p10_fm3_report.sh\`.
>
> Generated: ${NOW} | Host: ${HOST} | Commit: ${COMMIT}
>
> Input evidence:
> - \`build/evidence/task-15-phase10-rtl-verification.txt\` (todo 15, RTL overlap measurement)
> - \`build/evidence/task-16-phase10-rtl-verification.txt\` (todo 16, Func Model calibration update)

## Method

FM-3 calibrates the Func Model weight-streaming overlap prediction against
cycle-accurate RTL measurement. The calibration loop is:

1. **RTL measurement (todo 15).** On the Wave 3 per-layer cycle trace data,
   the weight-streaming scenario (Q4_K_M weights) is replayed and the
   overlap between DMA weight preload and MXU compute is measured directly:
   \`overlap_ratio = DMA cycles hidden by MXU compute / total DMA cycles\`.
   The raw cycle trace is preserved as evidence for the ratio.
2. **Func Model prediction (todo 16).** The Func Model computes the same
   ratio through \`estimate_tile_double_buffer_overlap()\`
   (\`sim/models/dma.py\` L225), whose prediction depends on the DMAModel
   configuration \`bw_bytes_per_cycle\` (\`sim/models/dma.py\` L67), the
   per-tile compute constants \`broadcast_sync = 2\` and \`_accumulate()\`
   (\`sim/timing/benchmark.py\` L87-90), and the calibrated same-engine gap
   annotation \`cross_engine_gap\` (\`sim/perf_tests.py\` L261).
3. **Calibration (todo 16).** The internal model constants above — not the
   derived quantity \`weight_streaming_overlap_ratio\` — are adjusted so the
   computed overlap approaches the RTL measurement, followed by a Func Model
   re-run to confirm the change.
4. **Convergence criterion.** \`|rtl_overlap - fm_overlap| <= 0.05\`.

## Measurement

| Quantity | Value | Source |
|---|---|---|
| RTL overlap ratio (todo 15) | \`$(v "$t15_overlap")\` | task-15-phase10-rtl-verification.txt |
| Raw cycle trace | \`$(v "$t15_trace")\` | task-15-phase10-rtl-verification.txt |
| RTL overlap (todo 16) | \`$(v "$t16_rtl")\` | task-16-phase10-rtl-verification.txt |
| Func Model overlap (todo 16) | \`$(v "$t16_fm")\` | task-16-phase10-rtl-verification.txt |
| \|delta\| | \`$(v "$t16_delta")\` | task-16-phase10-rtl-verification.txt |

## Calibration

Parameters adjusted in todo 16 to bring the Func Model overlap prediction in
line with the RTL measurement (values below are quoted verbatim from the
todo 16 evidence):

\`\`\`text
$(v "$params")
\`\`\`

Note: \`weight_streaming_overlap_ratio\` is a derived quantity, not a stored
knob. The actual tuning levers are the DMAModel configuration
\`bw_bytes_per_cycle\` and the compute-path constants
\`broadcast_sync\` / \`_accumulate\` in \`sim/timing/benchmark.py\`; the
\`cross_engine_gap\` annotation in \`sim/perf_tests.py\` L261 is updated if
the RTL measurement differs from the previously calibrated value (FM-1 = 4).

## Residual Error

- \`|delta| = |rtl_overlap - fm_overlap|\` = \`${delta_line}\`
- Threshold: \`0.05\`
- Verdict: \`${verdict}\`

The residual captures the remaining disagreement between the cycle-accurate
RTL overlap measurement and the analytical Func Model after the todo 16
parameter update. Attribution of any non-zero residual (DMA arbitration
model granularity, per-tile accumulation constants, or trace-extraction
windowing) is documented in the todo 15/16 evidence files referenced above.

## Appendix: Source Evidence

### build/evidence/task-15-phase10-rtl-verification.txt

\`\`\`text
$(cat "$T15")
\`\`\`

### build/evidence/task-16-phase10-rtl-verification.txt

\`\`\`text
$(cat "$T16")
\`\`\`
REPORT_EOF

    # ---- 5. Verify the required section headers exist.
    local fail=0 sec
    for sec in "${REQUIRED_SECTIONS[@]}"; do
        if grep -q "^## ${sec}$" "$REPORT"; then
            log_info "SECTION OK  : ${sec}"
        else
            log_info "SECTION MISS: ${sec}"
            fail=1
        fi
    done
    if [ ! -s "$REPORT" ]; then
        log_info "REPORT EMPTY: $REPORT"
        fail=1
    fi

    # ---- 6. Evidence + exit code.
    if [ "$fail" -ne 0 ]; then
        write_evidence "FAIL" "report section verification failed" \
            "report_generated=yes" \
            "sections_verified=no" \
            "report=$REPORT"
        log_info "FAIL: report validation failed — evidence written to $T17"
        exit 1
    fi

    write_evidence "PASS" "report generated with Method/Measurement/Calibration/Residual Error sections" \
        "report_generated=yes" \
        "sections_verified=yes" \
        "overlap_ratio=$(v "$t15_overlap")" \
        "rtl_overlap=$(v "$t16_rtl")" \
        "fm_overlap=$(v "$t16_fm")" \
        "delta=$(v "$res_delta")" \
        "report=$REPORT"
    log_info "PASS: report generated and validated — $REPORT"
    exit 0
}

main "$@"
