#!/usr/bin/env bash
# =============================================================================
# p10_f4_scope_gate.sh — Phase 10 Final Wave F4 (scope fidelity)
#
# Confirms the delivery set matches the plan's Scope IN/OUT sections
# (.omo/plans/phase10-rtl-verification.md, "Must have" / "Must NOT have"):
#   A. Arc Model frozen     — sim/design_space_explorer.py (legacy copy) and
#                             the Arc Model surface (arc_model.py, dse_scenario.py,
#                             config/*.yaml, sim/engine/, reports/, references/)
#                             must NOT change.  Plan guardrail: "不触碰 Arc Model".
#   B. caduceus_soc_top.v   — diff content check: only the todo-13 doorbell
#                             backdoor ports are expected; any other functional
#                             addition is scope creep.
#   C. requirements.txt     — must NOT change (no new toolchain dependencies).
#   D. RTL whitelist        — only the documented Phase 10 RTL deltas are
#                             allowed; any new .v file or non-whitelisted
#                             RTL source change is scope creep.
#   E. File classification  — every changed/added file (incl. untracked) must
#                             fall into an expected category; anything else
#                             ("out-of-scope") is reported as scope creep.
#
# Baseline: parent of the first p10 commit, auto-detected as the parent of
# the commit that introduced scripts/p10_lib/p10_sz0001.sh (todo 1 skeleton),
# overridable via build/evidence/ph10-base-commit.txt.
#
# Exit code:
#   0 — no scope creep (all checks PASS)
#   1 — scope creep detected (>=1 check FAIL)
#   2 — environment error (baseline/git unusable)
#
# Evidence:
#   build/evidence/task-F4-phase10-rtl-verification.txt
#   build/evidence/ph10-base-commit.txt (baseline, written on first run)
# =============================================================================
set -euo pipefail

source "$(dirname "$0")/p10_lib/p10_sz0001.sh"

EVIDENCE_DIR="$REPO_ROOT/build/evidence"
OUT_FILE="$EVIDENCE_DIR/task-F4-phase10-rtl-verification.txt"
BASE_FILE="$EVIDENCE_DIR/ph10-base-commit.txt"
PLAN_FILE="$REPO_ROOT/.omo/plans/phase10-rtl-verification.md"

mkdir -p "$EVIDENCE_DIR"

COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "?")"
COMMIT_SHORT="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo "?")"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

failures=()
record_failure() { failures+=("$*"); }
notes=()
record_note() { notes+=("$*"); }

# =============================================================================
# Baseline detection
# =============================================================================
if [[ -f "$BASE_FILE" ]] \
   && BASE_CANDIDATE="$(head -n 1 "$BASE_FILE" | tr -d '[:space:]')" \
   && [[ -n "$BASE_CANDIDATE" ]] \
   && git -C "$REPO_ROOT" rev-parse --verify --quiet "${BASE_CANDIDATE}^{commit}" >/dev/null 2>&1; then
    BASE="$BASE_CANDIDATE"
    record_note "baseline_source=file($(basename "$BASE_FILE"))"
else
    # Parent of the commit that first added the p10 script library (todo 1).
    FIRST_P10="$(git -C "$REPO_ROOT" log --diff-filter=A --format=%H -- scripts/p10_lib/p10_sz0001.sh 2>/dev/null | tail -n 1 || true)"
    if [[ -z "$FIRST_P10" ]]; then
        echo "[p10_f4_scope_gate] FATAL: cannot locate p10 skeleton commit (scripts/p10_lib/p10_sz0001.sh)"
        exit 2
    fi
    BASE="$(git -C "$REPO_ROOT" rev-parse "${FIRST_P10}~1")"
    echo "$BASE" > "$BASE_FILE"
    record_note "baseline_source=auto-detect (parent of first p10 commit ${FIRST_P10:0:8})"
fi

echo "[p10_f4_scope_gate] Baseline: ${BASE}"

# =============================================================================
# Collect changed files: baseline -> working tree (committed + uncommitted
# tracked), plus untracked files.  Untracked build/evidence and agent notes
# are legitimate (F1-F3 run in parallel), so they go through the same
# classification, not a blanket rejection.
# =============================================================================
CHANGED_LIST="$(mktemp)"
trap 'rm -f "$CHANGED_LIST"' EXIT

# status<TAB>path lines
git -C "$REPO_ROOT" diff --name-status "$BASE" > "$CHANGED_LIST" 2>/dev/null || true
# untracked files (status A)
while IFS= read -r uf; do
    [[ -z "$uf" ]] && continue
    printf 'A\t%s\n' "$uf" >> "$CHANGED_LIST"
done < <(git -C "$REPO_ROOT" ls-files --others --exclude-standard 2>/dev/null || true)
sort -u -k2 "$CHANGED_LIST" -o "$CHANGED_LIST"

# =============================================================================
# classify <path> <status> — prints:
#   PROHIBITED  hard prohibition (always scope creep)
#   ARC_MODEL   Arc Model surface (frozen)
#   SOC_TOP     rtl/soc/caduceus_soc_top.v (content-checked separately)
#   OK[:reason] expected / documented change
#   UNEXPECTED  out-of-scope
# Bash `case` patterns match `/` too, so `dir/*` covers nested paths.
# =============================================================================
classify() {
    local path="$1"
    case "$path" in
        # ── Hard prohibitions (plan "Must NOT have") ──
        requirements.txt)
            echo "PROHIBITED:requirements.txt changed (no new toolchain dependencies allowed)" ;;
        sim/design_space_explorer.py)
            echo "PROHIBITED:sim/design_space_explorer.py changed (Arc Model legacy copy is frozen)" ;;

        # ── Arc Model surface (frozen; moved to npu_arc_model repo) ──
        sim/arc_model.py|sim/dse_scenario.py|sim/config/scenarios.yaml|sim/config/design_space.yaml|sim/engine/*|reports/*|references/*)
            echo "ARC_MODEL:${path} (Arc Model surface must stay frozen)" ;;

        # ── Phase 10 scripts + library (incl. parallel F1-F4 scripts) ──
        scripts/p10_*|scripts/p10_lib/*)
            echo "OK:p10 script namespace" ;;

        # ── Expected sim/Python changes ──
        sim/cocotb_bridge.py)
            echo "OK:todo 5/13 DMA readback + segment-run control layer" ;;
        sim/spike_host.py)
            echo "OK:todo 12 Spike-first 36-layer forward (--mode forward, npz dump)" ;;
        sim/p10_*.py)
            echo "OK:p10 helper module" ;;
        sim/rtl_soc_segment_run.py)
            echo "OK:todo 13 Ibex segment-run cocotb driver" ;;
        sim/regression/*)
            echo "OK:SoC regression harness (todo 13 run_ibex_segment_run.sh, result xml)" ;;
        sim/tests/*)
            echo "OK:verification test suite (todo 18 test_sfu_wrapper.py)" ;;
        scripts/run_36layer_checkpoint.py)
            echo "OK:todo 10 FM-SOC-001 smoke gate fix (checkpoint runner)" ;;

        # ── RTL: content-checked SoC top ──
        rtl/soc/caduceus_soc_top.v)
            echo "SOC_TOP" ;;

        # ── RTL: documented Phase 10 deltas ──
        rtl/soc/doorbell.v)
            echo "OK:todo 13 cocotb backdoor registers (host_tail/npu_head/host_head/npu_tail)" ;;
        rtl/soc/sram_ctrl.v)
            echo "OK:todo 13 SRAM_DEBUG ifdef guard on \$display (cosmetic)" ;;
        rtl/wrapper/sfu_soc_wrapper.v)
            echo "OK:todo 18 SFU wrapper output mismatch fixes (status/gelu/width/line_buffer)" ;;
        rtl/tb/*)
            echo "OK:testbench doorbell backdoor port tie-offs (todo 13 follow-up)" ;;
        rtl/test_vectors/*)
            echo "OK:generated test vectors" ;;
        rtl/*.md|rtl/*/*.md)
            echo "OK:RTL documentation (todo 9 testcase-list-perf.md)" ;;

        # ── Firmware ──
        firmware/npu_firmware.c)
            echo "OK:todo 8 PERF-06 dispatch fix + todo 19 DRAM 8MB window constraint" ;;
        firmware/build/*)
            echo "OK:firmware build artifacts" ;;

        # ── Evidence, docs, agent workspace, test artifacts ──
        build/*)
            echo "OK:build artifacts and evidence (build/evidence/, build/ibex_segment_rtl/)" ;;
        docs/*)
            echo "OK:documentation (todo 20 MMIO spec, todo 22 bug ledger)" ;;
        .omo/*)
            echo "OK:agent workspace (plans/evidence/notepads/notes)" ;;
        results.xml)
            echo "OK:pytest results artifact" ;;

        *)
            echo "UNEXPECTED:${path}" ;;
    esac
}

# =============================================================================
# Run classification over all changed files
# =============================================================================
CHANGED_COUNT=0
UNEXPECTED_FILES=()
REQ_FILES=()
DSE_FILES=()
ARC_FILES=()
RTL_FILES=()
SOC_TOP_CHANGED=0

while IFS=$'\t' read -r status path; do
    [[ -z "$path" ]] && continue
    CHANGED_COUNT=$((CHANGED_COUNT + 1))
    verdict="$(classify "$path" "$status")"
    case "$verdict" in
        PROHIBITED:*)
            if [[ "$path" == "requirements.txt" ]]; then
                REQ_FILES+=("${verdict#PROHIBITED:}")
            else
                DSE_FILES+=("${verdict#PROHIBITED:}")
            fi ;;
        ARC_MODEL:*)
            ARC_FILES+=("${verdict#ARC_MODEL:}") ;;
        SOC_TOP)
            SOC_TOP_CHANGED=1 ;;
        OK:*)
            : ;;
        UNEXPECTED:*)
            UNEXPECTED_FILES+=("${verdict#UNEXPECTED:}") ;;
        *)
            UNEXPECTED_FILES+=("${path} (classifier error: ${verdict})") ;;
    esac
    # keep a note of every RTL file for the evidence section
    if [[ "$path" == rtl/*.v ]]; then
        RTL_FILES+=("$path")
    fi
done < "$CHANGED_LIST"

# =============================================================================
# CHECK A — Arc Model frozen
# =============================================================================
if [[ ${#DSE_FILES[@]} -eq 0 && ${#ARC_FILES[@]} -eq 0 ]]; then
    ARC_DSE_CHECK="PASS"
else
    ARC_DSE_CHECK="FAIL"
    for f in "${DSE_FILES[@]}"; do
        record_failure "Arc Model prohibition: $f"
    done
    for f in "${ARC_FILES[@]}"; do
        record_failure "Arc Model surface changed: $f"
    done
fi
echo "[p10_f4_scope_gate] ARC_DSE_CHECK=${ARC_DSE_CHECK}"

# =============================================================================
# CHECK B — caduceus_soc_top.v content: only todo-13 doorbell backdoor ports
# =============================================================================
SOC_TOP_CHECK="PASS"
SOC_TOP_NOTE=""
if [[ "$SOC_TOP_CHANGED" == "1" ]]; then
    SOC_TOP_DIFF="$(git -C "$REPO_ROOT" diff "$BASE" -- rtl/soc/caduceus_soc_top.v 2>/dev/null || true)"
    SOC_TOP_VIOLATIONS=""
    while IFS= read -r line; do
        # diff header line "+++ b/..." — skip before stripping
        [[ "$line" =~ ^\+\+\+ ]] && continue
        content="${line#+}"          # strip the leading '+'
        # blank lines
        [[ -z "$(echo "$content" | tr -d '[:space:]')" ]] && continue
        # comment lines
        [[ "$content" =~ ^[[:space:]]*// ]] && continue
        # expected: doorbell backdoor port list + instantiation wiring (todo 13)
        if [[ "$content" =~ (db_bkdoor_|bkdoor_|timer_irq_i|doorbell_irq) ]]; then
            continue
        fi
        SOC_TOP_VIOLATIONS+="${line}
"
    done < <(echo "$SOC_TOP_DIFF" | grep -E '^\+' || true)

    SOC_TOP_REMOVAL_VIOLATIONS=""
    while IFS= read -r line; do
        [[ "$line" =~ ^--- ]] && continue
        content="${line#-}"
        [[ -z "$(echo "$content" | tr -d '[:space:]')" ]] && continue
        [[ "$content" =~ ^[[:space:]]*// ]] && continue
        if [[ "$content" =~ (db_bkdoor_|bkdoor_|timer_irq_i|doorbell_irq) ]]; then
            continue
        fi
        SOC_TOP_REMOVAL_VIOLATIONS+="${line}
"
    done < <(echo "$SOC_TOP_DIFF" | grep -E '^-' || true)

    if [[ -n "$SOC_TOP_VIOLATIONS" || -n "$SOC_TOP_REMOVAL_VIOLATIONS" ]]; then
        SOC_TOP_CHECK="FAIL"
        record_failure "caduceus_soc_top.v functional change outside doorbell backdoor ports:"
        while IFS= read -r v; do [[ -n "$v" ]] && record_failure "  ${v}"; done <<< "${SOC_TOP_VIOLATIONS}${SOC_TOP_REMOVAL_VIOLATIONS}"
    fi
    # Document the expected delta (plan MUST: backdoor ports are expected)
    SOC_TOP_NOTE="doorbell backdoor ports (todo 13): db_bkdoor_we/sel/wdata/rdata added to port list; \
.bkdoor_we/sel/wdata/rdata wired to doorbell instance; timer_irq_i line gained trailing comma"
else
    SOC_TOP_NOTE="unchanged since baseline"
fi
echo "[p10_f4_scope_gate] SOC_TOP_CHECK=${SOC_TOP_CHECK}"

# =============================================================================
# CHECK C — requirements.txt untouched
# =============================================================================
REQ_CHECK="PASS"
if [[ ${#REQ_FILES[@]} -gt 0 ]]; then
    REQ_CHECK="FAIL"
    for f in "${REQ_FILES[@]}"; do
        record_failure "$f"
    done
fi
echo "[p10_f4_scope_gate] REQ_CHECK=${REQ_CHECK}"

# =============================================================================
# CHECK D — RTL whitelist (classifier already routes every .v; UNEXPECTED
# under rtl/ plus PROHIBITED/ARC cover it, but keep an explicit summary)
# =============================================================================
RTL_CHECK="PASS"
RTL_VIOLATIONS=()
for f in "${UNEXPECTED_FILES[@]}"; do
    case "$f" in
        rtl/*) RTL_VIOLATIONS+=("$f") ;;
    esac
done
if [[ ${#RTL_VIOLATIONS[@]} -gt 0 ]]; then
    RTL_CHECK="FAIL"
    for f in "${RTL_VIOLATIONS[@]}"; do
        record_failure "RTL out of scope (no new RTL features allowed): $f"
    done
fi
echo "[p10_f4_scope_gate] RTL_CHECK=${RTL_CHECK}"

# =============================================================================
# CHECK E — no out-of-scope files anywhere else
# =============================================================================
UNEXPECTED_CHECK="PASS"
if [[ ${#UNEXPECTED_FILES[@]} -gt 0 ]]; then
    UNEXPECTED_CHECK="FAIL"
    for f in "${UNEXPECTED_FILES[@]}"; do
        record_failure "Out-of-scope file: $f"
    done
fi
echo "[p10_f4_scope_gate] UNEXPECTED_CHECK=${UNEXPECTED_CHECK}"

# =============================================================================
# Verdict
# =============================================================================
if [[ "$ARC_DSE_CHECK" == "PASS" && "$SOC_TOP_CHECK" == "PASS" \
   && "$REQ_CHECK" == "PASS" && "$RTL_CHECK" == "PASS" \
   && "$UNEXPECTED_CHECK" == "PASS" ]]; then
    SCOPE_VERDICT="PASS"
else
    SCOPE_VERDICT="FAIL"
fi

# =============================================================================
# Evidence
# =============================================================================
{
    echo "# Phase 10 Final Wave F4 — Scope Fidelity Gate"
    echo "# Date: ${TS}"
    echo "# Commit: ${COMMIT} (${COMMIT_SHORT})"
    echo "# Baseline: ${BASE}"
    echo "# Command: bash scripts/p10_f4_scope_gate.sh"
    echo "# Plan: ${PLAN_FILE#${REPO_ROOT}/} (Scope: Must have / Must NOT have)"
    echo ""
    echo "SCOPE_VERDICT=${SCOPE_VERDICT}"
    echo "arc_model_frozen=${ARC_DSE_CHECK}"
    echo "requirements_unchanged=${REQ_CHECK}"
    echo "soc_top_functional_additions=${SOC_TOP_CHECK}"
    echo "rtl_whitelist=${RTL_CHECK}"
    echo "unexpected_files=${UNEXPECTED_CHECK}"
    echo "changed_file_count=${CHANGED_COUNT}"
    echo ""
    echo "# caduceus_soc_top.v delta (expected, todo 13):"
    echo "#   ${SOC_TOP_NOTE}"
    echo ""
    echo "# Failures:"
    if [[ ${#failures[@]} -eq 0 ]]; then
        echo "#   (none)"
    else
        for f in "${failures[@]}"; do
            echo "#   ${f}"
        done
    fi
    echo ""
    echo "# RTL source files changed from baseline:"
    if [[ ${#RTL_FILES[@]} -eq 0 ]]; then
        echo "#   (none)"
    else
        for f in "${RTL_FILES[@]}"; do
            echo "#   ${f}"
        done
    fi
    echo ""
    echo "# Notes:"
    for n in "${notes[@]}"; do
        echo "#   ${n}"
    done
    echo "#   Scope boundaries (plan Must NOT have): no new RTL features;"
    echo "#   no Arc Model changes (design_space_explorer.py frozen legacy copy);"
    echo "#   no new toolchain dependencies; all out-of-scope items recorded."
    echo "#   Expected deviations documented in-scope: doorbell backdoor ports"
    echo "#   (todo 13), SFU wrapper fixes (todo 18), sram_ctrl SRAM_DEBUG ifdef,"
    echo "#   testbench tie-offs, firmware PERF-06/DRAM-window fixes."
} > "$OUT_FILE"

echo "[p10_f4_scope_gate] Evidence written to ${OUT_FILE#${REPO_ROOT}/}"

if [[ "$SCOPE_VERDICT" == "PASS" ]]; then
    echo "[p10_f4_scope_gate] SCOPE_VERDICT=PASS — no scope creep detected"
    exit 0
else
    echo "[p10_f4_scope_gate] SCOPE_VERDICT=FAIL — scope creep detected:"
    for f in "${failures[@]}"; do
        echo "[p10_f4_scope_gate]   ${f}"
    done
    exit 1
fi
