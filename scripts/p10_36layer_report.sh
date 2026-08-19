#!/usr/bin/env bash
# p10_36layer_report.sh — todo 14: 36-layer per-layer analysis report.
#
# Merges the Spike full 36-layer evidence (todo 12) with the Ibex 9-layer
# segment-run evidence (todo 13, when available) into:
#   build/evidence/task-14-phase10-rtl-verification.txt
#   build/evidence/ph10-36layer-report.md
#
# Guards implemented here:
#   - exit 1 if the REQUIRED Spike evidence (task-12) is missing;
#   - Ibex evidence (task-13 txt + ph10-36layer-ibex-checkpoints.npz) is
#     optional ("when available"): when absent the report is still generated
#     with Spike-only sources and Ibex columns explicitly marked n/a — Ibex
#     data is never fabricated;
#   - every cos_sim row carries an evidence-source label
#     (spike / ibex-checkpoint / ibex-segment-run) and every cycle column
#     carries an engine attribution (spike host-cycles vs Ibex VCS cycles);
#   - engine labels are asserted per line (task-12 => engine=spike only,
#     task-13 => engine=ibex only, npz metadata => engine=ibex) so sources
#     can never be mixed up;
#   - tolerance-ladder thresholds are validated per line against the plan
#     ladder (L0-19 >= 0.999, L20-29 >= 0.998, L30-35 >= 0.997).
#
# Input/output paths are overridable via environment variables (EVIDENCE_T12,
# EVIDENCE_T13, IBEX_NPZ, IBEX_RUN_LOG, EVIDENCE_OUT, REPORT_OUT) for isolated
# testing without touching the canonical evidence files.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/p10_lib/p10_sz0001.sh"

EVIDENCE_T12="${EVIDENCE_T12:-$REPO_ROOT/build/evidence/task-12-phase10-rtl-verification.txt}"
EVIDENCE_T13="${EVIDENCE_T13:-$REPO_ROOT/build/evidence/task-13-phase10-rtl-verification.txt}"
IBEX_NPZ="${IBEX_NPZ:-$REPO_ROOT/build/evidence/ph10-36layer-ibex-checkpoints.npz}"
IBEX_RUN_LOG="${IBEX_RUN_LOG:-$REPO_ROOT/build/evidence/task-13-phase10-run.log}"
EVIDENCE_OUT="${EVIDENCE_OUT:-$REPO_ROOT/build/evidence/task-14-phase10-rtl-verification.txt}"
REPORT_OUT="${REPORT_OUT:-$REPO_ROOT/build/evidence/ph10-36layer-report.md}"

EXPECTED_EXECUTED="L0,L9,L10,L19,L20,L29,L30,L34,L35"
EXPECTED_CHECKPOINTS="L0,L10,L20,L30,L35"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

ladder_threshold() {
    local L=$1
    if [ "$L" -le 19 ]; then printf '0.999'
    elif [ "$L" -le 29 ]; then printf '0.998'
    else printf '0.997'; fi
}

fail() { echo "ERROR: $*" >&2; exit 1; }

mkdir -p "$(dirname "$EVIDENCE_OUT")" "$(dirname "$REPORT_OUT")"

echo "=== p10_36layer_report: input validation ==="
[ -f "$EVIDENCE_T12" ] || fail "required Spike evidence missing: $EVIDENCE_T12"
grep -qx 'engine=spike' "$EVIDENCE_T12" || fail "task-12 evidence does not declare engine=spike"
grep -qx 'layers_run=36' "$EVIDENCE_T12" || fail "task-12 evidence does not declare layers_run=36"
if grep -q 'engine=ibex' "$EVIDENCE_T12"; then
    fail "task-12 evidence contains engine=ibex lines (source mixing)"
fi

# ---- parse Spike 36-layer cos_sim lines -----------------------------------
grep -E '^layer=[0-9]+ engine=spike cos_sim=' "$EVIDENCE_T12" > "$WORK_DIR/spike_lines.txt"
N_SPIKE="$(wc -l < "$WORK_DIR/spike_lines.txt")"
[ "$N_SPIKE" -eq 36 ] || fail "expected 36 spike layer lines, found $N_SPIKE"

declare -A SP_CS SP_THR SP_ST SP_HW SP_HWMAX
while read -r L ENG CS THR ST; do
    [ "$ENG" = "spike" ] || fail "task-12 layer L$L engine=$ENG (expected spike)"
    [ "$THR" = "$(ladder_threshold "$L")" ] \
        || fail "task-12 layer L$L threshold=$THR does not match ladder $(ladder_threshold "$L")"
    SP_CS[$L]="$CS"; SP_THR[$L]="$THR"; SP_ST[$L]="$ST"
done < <(awk '{L="";E="";C="";T="";S="";
    for(i=1;i<=NF;i++){split($i,a,"=");
        if(a[1]=="layer")L=a[2];
        else if(a[1]=="engine")E=a[2];
        else if(a[1]=="cos_sim")C=a[2];
        else if(a[1]=="threshold")T=a[2];
        else if(a[1]=="status")S=a[2]}
    print L,E,C,T,S}' "$WORK_DIR/spike_lines.txt")
for L in $(seq 0 35); do
    [ -n "${SP_CS[$L]:-}" ] || fail "task-12 evidence missing layer L$L"
done
echo "OK: task-12 parsed — 36/36 spike layers, engine=spike per line, ladder thresholds consistent"

# ---- parse Spike hardware l_out transparency (non-gating) -----------------
grep -E '^layer=[0-9]+ hw_l_out_cos_sim=' "$EVIDENCE_T12" > "$WORK_DIR/spike_hw.txt" || true
while read -r L HW MX; do
    SP_HW[$L]="$HW"; SP_HWMAX[$L]="$MX"
done < <(awk '{split($1,a,"=");L=a[2];
    for(i=2;i<=NF;i++){split($i,b,"=");
        if(b[1]=="hw_l_out_cos_sim")HW=b[2];
        else if(b[1]=="max_abs")M=b[2]}
    print L,HW,M}' "$WORK_DIR/spike_hw.txt")

# ---- Ibex evidence (optional) ----------------------------------------------
IBEX_AVAILABLE=no
IBEX_EXEC=""
IBEX_CKPTS=""
IBEX_LADDER="n/a"
IBEX_OVERALL="n/a"
declare -A IBEX_CS IBEX_THR IBEX_ST IBEX_HW IBEX_HWMAX IBEX_CC IBEX_CC_ST IBEX_CC_MISMATCH IBEX_EXEC_MAP IBEX_CYC_END IBEX_CYC_DELTA
IBEX_CYC_SRC="n/a"

if [ -f "$EVIDENCE_T13" ]; then
    IBEX_AVAILABLE=yes
    grep -qx 'engine=ibex' "$EVIDENCE_T13" || fail "task-13 evidence does not declare engine=ibex"
    if grep -q 'engine=spike' "$EVIDENCE_T13"; then
        fail "task-13 evidence contains engine=spike lines (source mixing)"
    fi
    IBEX_EXEC="$(sed -n 's/^ibex_executed=//p' "$EVIDENCE_T13" | head -n1)"
    IBEX_CKPTS="$(sed -n 's/^checkpoints=//p' "$EVIDENCE_T13" | head -n1)"
    [ "$IBEX_EXEC" = "$EXPECTED_EXECUTED" ] || fail "task-13 ibex_executed=$IBEX_EXEC (expected $EXPECTED_EXECUTED)"
    [ "$IBEX_CKPTS" = "$EXPECTED_CHECKPOINTS" ] || fail "task-13 checkpoints=$IBEX_CKPTS (expected $EXPECTED_CHECKPOINTS)"
    grep -qx 'chain_restart=true' "$EVIDENCE_T13" || fail "task-13 missing chain_restart=true"
    grep -qx 'chain_restart_state_source=ibex_dram' "$EVIDENCE_T13" || fail "task-13 missing chain_restart_state_source=ibex_dram"
    grep -qx 'segment_input_source=spike_npz' "$EVIDENCE_T13" || fail "task-13 missing segment_input_source=spike_npz"
    IBEX_LADDER="$(grep -m1 'LADDER=' "$EVIDENCE_T13" | awk -F= '{print $2}' || true)"
    IBEX_OVERALL="$(grep -m1 '^  Overall:' "$EVIDENCE_T13" | awk '{print $2}' || true)"

    grep -E '^layer=[0-9]+ engine=ibex cos_sim=' "$EVIDENCE_T13" > "$WORK_DIR/ibex_cp.txt"
    N_CP="$(wc -l < "$WORK_DIR/ibex_cp.txt")"
    [ "$N_CP" -eq 5 ] || fail "expected 5 ibex checkpoint lines, found $N_CP"
    while read -r L ENG CS THR ST; do
        [ "$ENG" = "ibex" ] || fail "task-13 checkpoint L$L engine=$ENG (expected ibex)"
        [ "$THR" = "$(ladder_threshold "$L")" ] \
            || fail "task-13 checkpoint L$L threshold=$THR does not match ladder $(ladder_threshold "$L")"
        IBEX_CS[$L]="$CS"; IBEX_THR[$L]="$THR"; IBEX_ST[$L]="$ST"
    done < <(awk '{L="";E="";C="";T="";S="";
        for(i=1;i<=NF;i++){split($i,a,"=");
            if(a[1]=="layer")L=a[2];
            else if(a[1]=="engine")E=a[2];
            else if(a[1]=="cos_sim")C=a[2];
            else if(a[1]=="threshold")T=a[2];
            else if(a[1]=="status")S=a[2]}
        print L,E,C,T,S}' "$WORK_DIR/ibex_cp.txt")
    for L in $(echo "$IBEX_CKPTS" | tr ',' ' ' | sed 's/L//g'); do
        [ -n "${IBEX_CS[$L]:-}" ] || fail "task-13 evidence missing checkpoint line for L$L"
    done

    grep -E '^layer=[0-9]+ ibex_vs_spike_cos_sim=' "$EVIDENCE_T13" > "$WORK_DIR/ibex_cc.txt" || true
    while read -r L CC THR ST MIS; do
        [ "$THR" = "$(ladder_threshold "$L")" ] \
            || fail "task-13 cross-check L$L threshold=$THR does not match ladder $(ladder_threshold "$L")"
        IBEX_CC[$L]="$CC"; IBEX_CC_ST[$L]="$ST"; IBEX_CC_MISMATCH[$L]="$MIS"
    done < <(awk '{L="";CC="";T="";S="";M="";
        for(i=1;i<=NF;i++){split($i,a,"=");
            if(a[1]=="layer")L=a[2];
            else if(a[1]=="ibex_vs_spike_cos_sim")CC=a[2];
            else if(a[1]=="threshold")T=a[2];
            else if(a[1]=="status")S=a[2];
            else if(a[1]=="cross_check_mismatch")M=a[2]}
        print L,CC,T,S,M}' "$WORK_DIR/ibex_cc.txt")

    grep -E '^layer=[0-9]+ hw_l_out_cos_sim=' "$EVIDENCE_T13" > "$WORK_DIR/ibex_hw.txt" || true
    while read -r L HW MX; do
        IBEX_HW[$L]="$HW"; IBEX_HWMAX[$L]="$MX"
    done < <(awk '{split($1,a,"=");L=a[2];
        for(i=2;i<=NF;i++){split($i,b,"=");
            if(b[1]=="hw_l_out_cos_sim")HW=b[2];
            else if(b[1]=="max_abs")M=b[2]}
        print L,HW,M}' "$WORK_DIR/ibex_hw.txt")

    for L in $(echo "$IBEX_EXEC" | tr ',' ' ' | sed 's/L//g'); do
        IBEX_EXEC_MAP[$L]=1
    done

    # npz consistency (engine label + layer set must agree with the txt)
    [ -f "$IBEX_NPZ" ] || fail "task-13 evidence present but npz missing: $IBEX_NPZ"
    if ! python3 - "$IBEX_NPZ" "$IBEX_EXEC" "$IBEX_CKPTS" <<'PYEOF'
import json
import sys

import numpy as np

path, exec_str, ckpt_str = sys.argv[1], sys.argv[2], sys.argv[3]
expect_exec = [int(t[1:]) for t in exec_str.split(",")]
expect_ckpt = [int(t[1:]) for t in ckpt_str.split(",")]
with np.load(path, allow_pickle=True) as d:
    meta = json.loads(str(d["metadata"][0]))
    assert meta["engine"] == "ibex", f"npz engine={meta['engine']} (expected ibex)"
    assert meta["chain_restart"] is True, "npz chain_restart is not True"
    assert meta["chain_restart_state_source"] == "ibex_dram", \
        f"npz chain_restart_state_source={meta['chain_restart_state_source']}"
    assert meta["segment_input_source"] == "spike_npz", \
        f"npz segment_input_source={meta['segment_input_source']}"
    assert list(meta["layers_saved"]) == expect_exec, \
        f"npz layers_saved={meta['layers_saved']} (evidence says {expect_exec})"
    assert list(meta["checkpoints"]) == expect_ckpt, \
        f"npz checkpoints={meta['checkpoints']} (evidence says {expect_ckpt})"
    for L in expect_exec:
        assert f"layer_{L}_output" in d.files, f"npz missing layer_{L}_output"
        assert f"hw_layer_{L}_output" in d.files, f"npz missing hw_layer_{L}_output"
print("npz OK: engine=ibex, layer set consistent with task-13 evidence")
PYEOF
    then
        fail "ibex npz verification failed (engine/layer mismatch vs evidence)"
    fi
    echo "OK: task-13 parsed — engine=ibex per line, checkpoints/cross-checks/thresholds consistent, npz verified"
else
    if [ -f "$IBEX_NPZ" ]; then
        fail "ibex npz present but task-13 evidence missing (inconsistent state): $IBEX_NPZ"
    fi
    echo "INFO: task-13 evidence not available — Ibex sections marked n/a (no fabrication)"
fi

# ---- Ibex per-layer VCS cycles --------------------------------------------
# Preference: explicit cycles= fields in the evidence (future-proof), then the
# [WAVE L{N}] ... sim_cycle= lines of the VCS run log, else n/a.
if [ "$IBEX_AVAILABLE" = yes ]; then
    grep -E '^layer=[0-9]+ .*cycles=[0-9]+' "$EVIDENCE_T13" > "$WORK_DIR/ibex_cycles.txt" || true
    if [ -s "$WORK_DIR/ibex_cycles.txt" ]; then
        IBEX_CYC_SRC="task-13 evidence cycles= fields"
        while read -r L CYC; do
            IBEX_CYC_END[$L]="$CYC"
        done < <(awk '{split($1,a,"=");L=a[2];
            for(i=2;i<=NF;i++){split($i,b,"=");if(b[1]=="cycles")C=b[2]}
            print L,C}' "$WORK_DIR/ibex_cycles.txt")
    elif [ -f "$IBEX_RUN_LOG" ]; then
        sed -nE 's/.*\[WAVE L([0-9]+)\].*sim_cycle=([0-9]+).*/\1 \2/p' "$IBEX_RUN_LOG" > "$WORK_DIR/ibex_waves.txt" || true
        if [ -s "$WORK_DIR/ibex_waves.txt" ]; then
            IBEX_CYC_SRC="task-13 run log [WAVE] sim_cycle (VCS cycle counter)"
            while read -r L CYC; do
                IBEX_CYC_END[$L]="$CYC"
            done < <(sort -n -k1,1 -k2,2 "$WORK_DIR/ibex_waves.txt" \
                | awk '{end[$1]=$2} END{for(L in end) print L,end[L]}')
        fi
    fi
    if [ "$IBEX_CYC_SRC" != "n/a" ]; then
        while read -r L D; do
            IBEX_CYC_DELTA[$L]="$D"
        done < <(
            for L in $(echo "$IBEX_EXEC" | tr ',' ' ' | sed 's/L//g'); do
                echo "$L ${IBEX_CYC_END[$L]:-0}"
            done | python3 -c '
import sys
ends = {}
for line in sys.stdin:
    L, c = line.split()
    ends[int(L)] = int(c)
out, prev = [], 0
for L in sorted(ends):
    out.append((L, ends[L] - prev))
    prev = ends[L]
for L, d in out:
    print(L, d)
')
    fi
fi
echo "OK: ibex cycle source = $IBEX_CYC_SRC"

# ---- uncovered layers ------------------------------------------------------
read -r UNCOVERED UNCOVERED_COUNT < <(python3 - "$IBEX_EXEC" <<'PYEOF'
import sys

exec_str = sys.argv[1]
covered = set() if exec_str in ("", "none") else {int(t[1:]) for t in exec_str.split(",")}
uncovered = [L for L in range(36) if L not in covered]
parts, start = [], None
for L in uncovered:
    if start is None:
        start = L
    if (L + 1) in uncovered:
        continue
    parts.append(f"L{start}" if start == L else f"L{start}-L{L}")
    start = None
print(",".join(parts), len(uncovered))
PYEOF
)
echo "OK: ibex_uncovered_layers=$UNCOVERED ($UNCOVERED_COUNT layers)"

# ---- anomalies and cross-check mismatches ----------------------------------
ANOMALIES=()
for L in $(seq 0 35); do
    if [ "${SP_ST[$L]:-FAIL}" != "PASS" ]; then
        ANOMALIES+=("L$L spike ${SP_ST[$L]} cos_sim=${SP_CS[$L]:-n/a} thr=${SP_THR[$L]:-n/a}")
    fi
done
if [ "$IBEX_AVAILABLE" = yes ]; then
    for L in $(echo "$IBEX_CKPTS" | tr ',' ' ' | sed 's/L//g'); do
        if [ "${IBEX_ST[$L]:-FAIL}" != "PASS" ]; then
            ANOMALIES+=("L$L ibex-checkpoint ${IBEX_ST[$L]} cos_sim=${IBEX_CS[$L]:-n/a} thr=${IBEX_THR[$L]:-n/a}")
        fi
    done
fi
MISMATCHES=""
if [ "$IBEX_AVAILABLE" = yes ]; then
    for L in $(echo "$IBEX_EXEC" | tr ',' ' ' | sed 's/L//g'); do
        if [ -n "${IBEX_CC_MISMATCH[$L]:-}" ]; then
            MISMATCHES="${MISMATCHES}${MISMATCHES:+,}L$L"
        fi
    done
fi

# ============================================================================
# Generate Markdown report
# ============================================================================
TS_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"
REPORT="$REPORT_OUT"
if [ "$IBEX_AVAILABLE" = yes ]; then
    INPUTS_LINE="task-12 ($(basename "$EVIDENCE_T12"), Spike 36-layer, engine=spike) + task-13 ($(basename "$EVIDENCE_T13"), Ibex 9-layer segment run, engine=ibex) + $(basename "$IBEX_NPZ")"
else
    INPUTS_LINE="task-12 ($(basename "$EVIDENCE_T12"), Spike 36-layer, engine=spike); task-13 not available"
fi

: > "$REPORT"
cat >> "$REPORT" <<EOF
# Phase 10 — 36-Layer Per-Layer RTL Analysis Report

> **Generated**: $TS_START | **Commit**: $COMMIT | **Script**: scripts/p10_36layer_report.sh
> **Inputs**: $INPUTS_LINE

## 1. Summary

| Metric | Value |
|---|---|
| Spike full 36-layer forward | $([ "$N_SPIKE" -eq 36 ] && echo "36/36 layers parsed, engine=spike") |
| Spike ladder status | see per-layer table (anomalies: $([ "${#ANOMALIES[@]}" -gt 0 ] && echo "${ANOMALIES[*]}" || echo "none")) |
| Ibex segment run | $([ "$IBEX_AVAILABLE" = yes ] && echo "available: $IBEX_EXEC executed, checkpoints $IBEX_CKPTS, LADDER=$IBEX_LADDER" || echo "NOT AVAILABLE — task-13 evidence missing (todo 13 pending); no Ibex data fabricated") |
| ibex_uncovered_layers | $UNCOVERED ($UNCOVERED_COUNT layers — deferred to FPGA phase) |
| Cycle engine attribution | spike: n/a (host path, no cycle counter); ibex: VCS sim_cycle ($IBEX_CYC_SRC) |

## 2. Tolerance ladder

Per the plan (todo 12/13/14 acceptance): deeper layers get progressively relaxed
thresholds because quantization drift accumulates through the chain (Phase 5 FM
L35 baseline was 0.998278 — later layers legitimately sit below 0.999).

| Layers | Threshold | Rationale |
|---|---|---|
| L0–L19 | ≥ 0.999 | early layers, drift is negligible |
| L20–L29 | ≥ 0.998 | mid-chain quantization drift becomes visible |
| L30–L35 | ≥ 0.997 | deep layers; L35 FM baseline 0.998278 establishes the 0.997 floor |

Every layer/checkpoint row below carries its own ladder threshold and a
PASS/FAIL judgement against it. Threshold fields in BOTH evidence files were
asserted to match this ladder exactly (a mismatch aborts the report — see §7).

## 3. 36-layer cos_sim table (evidence-source column)

Evidence-source legend:

- **spike** — cos_sim from the Spike full forward (task-12, engine=spike);
- **ibex-checkpoint** — cos_sim from the Ibex segment run compared against the
  Func Model golden (task-13, checkpoint layers L0/L10/L20/L30/L35);
- **ibex-segment-run** — layer executed on the Ibex RTL SoC in the segment run
  but without a golden checkpoint comparison (task-13; pre-layer rows
  L9/L19/L29/L34 carry a non-gating Ibex-vs-Spike cross-check in §5).

| Layer | Ladder | Spike cos_sim | Spike status | Ibex cos_sim | Ibex status | Evidence source(s) |
|---|---|---|---|---|---|---|
EOF

for L in $(seq 0 35); do
    THR="${SP_THR[$L]}"
    SCS="${SP_CS[$L]}"
    SST="${SP_ST[$L]}"
    SRCS="spike"
    ICS="—"
    IST="—"
    ANN=""
    [ "$SST" = "PASS" ] || ANN=" ⚠ ANOMALY"
    if [ "$IBEX_AVAILABLE" = yes ]; then
        if [ -n "${IBEX_CS[$L]:-}" ]; then
            ICS="${IBEX_CS[$L]}"
            IST="${IBEX_ST[$L]}"
            SRCS="spike, ibex-checkpoint"
            [ "$IST" = "PASS" ] || ANN=" ⚠ ANOMALY"
        elif [ -n "${IBEX_EXEC_MAP[$L]:-}" ]; then
            SRCS="spike, ibex-segment-run"
        fi
    fi
    printf '| L%-2s | ≥%s | %s | %s%s | %s | %s | %s |\n' \
        "$L" "$THR" "$SCS" "$SST" "$ANN" "$ICS" "$IST" "$SRCS" >> "$REPORT"
done

cat >> "$REPORT" <<EOF

**Legend**: "—" = not executed on that engine / no comparison of that kind.
Spike-only layers are labeled with source "spike" only — they are never
presented as Ibex evidence. If the Ibex segment run is unavailable, all 36
rows carry source "spike" and the Ibex columns stay "—".

## 4. Per-layer cycles (engine-attributed)

> **Engine attribution — do not mix these columns:**
> - **Spike host-cycles**: n/a. The Spike path is a host-side RISC-V ISA
>   emulation (MMIO bridge) with no cycle counter (task-12 evidence declares
>   \`cycles=n/a (spike host path has no cycle counter)\`).
> - **Ibex VCS cycles**: cycle counts from the Ibex RTL SoC simulation
>   (VCS \`sim_cycle\` counter, source: $IBEX_CYC_SRC). Only the 9 executed
>   layers have Ibex cycle data; L0's count includes session boot overhead.

| Layer | Spike host-cycles | Ibex VCS cycles (per-layer delta) | Notes |
|---|---|---|---|
EOF

for L in $(seq 0 35); do
    SC="n/a"
    IC="—"
    NOTE=""
    if [ "$IBEX_AVAILABLE" = yes ]; then
        if [ -n "${IBEX_CYC_DELTA[$L]:-}" ]; then
            IC="${IBEX_CYC_DELTA[$L]}"
            if [ "$L" -eq 0 ]; then NOTE=" includes boot"; fi
        elif [ -n "${IBEX_EXEC_MAP[$L]:-}" ]; then
            IC="n/a (no cycle line)"
        else
            NOTE="not executed"
        fi
    else
        NOTE="ibex run not available"
    fi
    printf '| L%-2s | %s | %s | %s |\n' "$L" "$SC" "$IC" "$NOTE" >> "$REPORT"
done

cat >> "$REPORT" <<EOF

The two engines' cycle figures are not comparable and are kept in separate
columns precisely to prevent mixing: Spike rows carry no cycle number at all.

## 5. Spike vs Ibex differences

### 5.1 Engine nature

- **Spike (engine=spike, task-12)** is the RISC-V ISA simulator driving the
  real firmware through the MMIO bridge on sz0001. The FP32 data path makes
  hidden-state cos_sim ≈ 1.0 for all 36 layers (see table above). It has no
  cycle counter, so it contributes correctness evidence only.
- **Ibex (engine=ibex, task-13)** is the actual RTL SoC (\`caduceus_soc_top\`)
  in VCS with the on-chip Ibex core. Data flows through the INT8 activation /
  INT4 weight quantization path with INT32 VRESID in DRAM, so its cos_sim is
  the authority on hardware numerical fidelity. It also provides real VCS
  cycle counts.
- **Chain-restart discipline**: within each segment (L9→L10, L19→L20, L29→L30,
  L34→L35) the pre-layer's hidden state stays in Ibex DRAM and feeds the
  checkpoint layer directly (\`chain_restart_state_source=ibex_dram\`); only a
  segment's FIRST-layer input is loaded from the Spike npz
  (\`segment_input_source=spike_npz\`). This preserves real Ibex inter-layer
  state propagation for 4 of the 9 executed layers' transitions.

### 5.2 Checkpoint comparison (both engines, where available)
EOF

if [ "$IBEX_AVAILABLE" = yes ]; then
    cat >> "$REPORT" <<EOF

| Checkpoint | Spike cos_sim | Spike hw transparency | Ibex cos_sim | Ibex hw transparency |
|---|---|---|---|---|
EOF
    for L in $(echo "$IBEX_CKPTS" | tr ',' ' ' | sed 's/L//g'); do
        printf '| L%-2s | %s | %s | %s | %s |\n' \
            "$L" "${SP_CS[$L]:-—}" "${SP_HW[$L]:-—}" "${IBEX_CS[$L]:-—}" "${IBEX_HW[$L]:-—}" >> "$REPORT"
    done
    cat >> "$REPORT" <<EOF

Interpretation: the "hw transparency" values are the cos_sim of the INT32
VRESID hidden state actually residing in DRAM (non-gating diagnostic). A
transparency noticeably below the layer's fp32 cos_sim means the residual
quantization of that layer contributes most of the numerical drift.
EOF
else
    cat >> "$REPORT" <<EOF

Ibex checkpoint data is not yet available (todo 13 pending). The comparison
table will be populated when \`$(basename "$EVIDENCE_T13")\` exists; until then
no Ibex numbers are fabricated.
EOF
fi

cat >> "$REPORT" <<EOF

### 5.3 Ibex-vs-Spike pre-layer cross-checks (non-gating)
EOF

if [ "$IBEX_AVAILABLE" = yes ]; then
    if [ "$(wc -l < "$WORK_DIR/ibex_cc.txt")" -gt 0 ]; then
        cat >> "$REPORT" <<EOF

| Pre-layer | Ibex vs Spike cos_sim | Ladder | Status | cross_check_mismatch |
|---|---|---|---|---|
EOF
        for L in 9 19 29 34; do
            if [ -n "${IBEX_CC[$L]:-}" ]; then
                printf '| L%-2s | %s | ≥%s | %s | %s |\n' \
                    "$L" "${IBEX_CC[$L]}" "$(ladder_threshold "$L")" "${IBEX_CC_ST[$L]:-—}" \
                    "${IBEX_CC_MISMATCH[$L]:-(no)}" >> "$REPORT"
            fi
        done
        cat >> "$REPORT" <<EOF

These cross-checks verify that the Ibex pre-layer output entering a checkpoint
layer agrees with the Spike output of the same layer. A mismatch is recorded
as \`cross_check_mismatch=<layer>\` and is **non-gating** (todo 13 semantics):
it flags a divergence to analyze, not a ladder failure. Mismatches observed in
this evidence: $([ -n "$MISMATCHES" ] && echo "$MISMATCHES" || echo "(none)").
EOF
    else
        echo "
No cross-check lines present in the Ibex evidence." >> "$REPORT"
    fi
else
    echo "
Ibex cross-check data is not yet available (todo 13 pending)." >> "$REPORT"
fi

# --- spike transparency pattern (quantization drift discussion) -------------
MIN_HW_LINE="$(awk '{split($1,a,"="); L=a[2];
    for(i=2;i<=NF;i++){split($i,b,"="); if(b[1]=="hw_l_out_cos_sim")HW=b[2]}
    print HW, L}' "$WORK_DIR/spike_hw.txt" | sort -n | head -n1)"
MIN_HW_CS="$(echo "$MIN_HW_LINE" | awk '{print $1}')"
MIN_HW_L="$(echo "$MIN_HW_LINE" | awk '{print $2}')"
if [ -z "$MIN_HW_LINE" ]; then
    MIN_HW_CS="n/a"
    MIN_HW_L="n/a"
fi

cat >> "$REPORT" <<EOF

### 5.4 Hardware l_out transparency pattern (Spike, all 36 layers)

| Layer | Spike hw transparency | max_abs |
|---|---|---|
EOF
for L in $(seq 0 35); do
    printf '| L%-2s | %s | %s |\n' "$L" "${SP_HW[$L]:-—}" "${SP_HWMAX[$L]:-—}" >> "$REPORT"
done

cat >> "$REPORT" <<EOF

The transparency metric (INT32 VRESID in DRAM vs the fp32 golden) tracks the
residual quantization per layer. In the Spike evidence the weakest layer is
L$MIN_HW_L (cos_sim $MIN_HW_CS), i.e. mid/deep-chain layers accumulate residual
rounding before the final VRESID write-back — the same phenomenon that
motivates the relaxed deep-layer ladder thresholds in §2. This is a diagnostic
trend, not a gate: the gating metric is the fp32 hidden-state cos_sim in §3.

### 5.5 What differs between engines (summary)

1. **Precision path**: Spike compares the fp32 hidden state directly; Ibex
   compares the same fp32-reconstructed state produced through the INT8/INT4
   quantized datapath with INT32 VRESID. Ibex cos_sim at checkpoints is the
   strictest hardware-fidelity number we have for those layers.
2. **Cycle accounting**: Spike has no cycle counter; Ibex provides real VCS
   cycles for its 9 executed layers only. No cross-engine cycle comparison is
   made anywhere in this report (see §4).
3. **Coverage**: Spike covers all 36 layers; Ibex covers 9 (5 with golden
   comparison). The 27 remaining layers are Spike-only evidence — labeled
   "spike" in §3 and never presented as Ibex evidence.
EOF

cat >> "$REPORT" <<EOF

## 6. ibex_uncovered_layers

\`ibex_uncovered_layers=$UNCOVERED\` ($UNCOVERED_COUNT layers)

These layers have Spike evidence only. Their Ibex verification is deferred to
the FPGA phase, whose prerequisites (recorded here per the plan's
"Deferred to next phase" section):

1. per-layer state export facility (halt-and-dump or equivalent) so per-layer
   cos_sim comparison and per-layer localization are possible on FPGA;
2. FPGA per-layer comparison must reuse the §2 tolerance ladder;
3. fallback clause: if the FPGA run can only compare the final output and
   cannot export per-layer state, it does NOT count as C3 evidence — the full
   Ibex 36-layer simulation must be executed instead.

## 7. Assertions and provenance

- engine labels asserted per line: task-12 rows are all \`engine=spike\`;
  task-13 rows are all \`engine=ibex\`; npz metadata \`engine=ibex\` — **no
  source mixing possible**.
- ladder thresholds in both evidence files asserted to equal the §2 ladder
  per layer.
- Ibex evidence header asserted: \`ibex_executed=$EXPECTED_EXECUTED\`,
  \`checkpoints=$EXPECTED_CHECKPOINTS\`, \`chain_restart=true\`,
  \`chain_restart_state_source=ibex_dram\`, \`segment_input_source=spike_npz\`.
- Ibex npz asserted consistent with the evidence (same engine, same layer
  set, same checkpoints).
- Cycle figures carry engine attribution in the §4 headers and are never
  merged across engines.
- Anomalies (layers below their ladder threshold) are annotated with ⚠ in §3
  and listed in the evidence file.
EOF

# ============================================================================
# Generate evidence file
# ============================================================================
SP_PASSED=0
for L in $(seq 0 35); do
    [ "${SP_ST[$L]:-FAIL}" = "PASS" ] && SP_PASSED=$((SP_PASSED + 1))
done
CP_PASSED="n/a"
if [ "$IBEX_AVAILABLE" = yes ]; then
    N=0
    for L in $(echo "$IBEX_CKPTS" | tr ',' ' ' | sed 's/L//g'); do
        [ "${IBEX_ST[$L]:-FAIL}" = "PASS" ] && N=$((N + 1))
    done
    CP_PASSED="$N/5"
fi

{
    echo "Task 14 - Phase 10 RTL Verification: 36-layer per-layer analysis report"
    echo "======================================================================"
    echo "Timestamp start : $TS_START"
    echo "Commit          : $COMMIT"
    echo "Command         : scripts/p10_36layer_report.sh"
    echo "report=ph10-36layer-report.md"
    echo "input_task12=$EVIDENCE_T12 (engine=spike, layers_run=36)"
    echo "input_task13=$([ "$IBEX_AVAILABLE" = yes ] && echo "$EVIDENCE_T13 (engine=ibex)" || echo "missing")"
    echo "ibex_evidence_available=$IBEX_AVAILABLE"
    echo "ibex_executed=$([ "$IBEX_AVAILABLE" = yes ] && echo "$IBEX_EXEC" || echo "none")"
    echo "ibex_checkpoints=$([ "$IBEX_AVAILABLE" = yes ] && echo "$IBEX_CKPTS" || echo "none")"
    echo "ibex_ladder=$IBEX_LADDER"
    echo "ibex_overall=$IBEX_OVERALL"
    echo "spike_layers_passed=$SP_PASSED/36"
    echo "ibex_checkpoints_passed=$CP_PASSED"
    echo "ibex_uncovered_layers=$UNCOVERED"
    echo "ibex_uncovered_count=$UNCOVERED_COUNT"
    echo "cross_check_mismatch=$([ -n "$MISMATCHES" ] && echo "$MISMATCHES" || echo "(none)")"
    echo "cycle_engine_attribution=spike:n/a(host-path-no-cycle-counter);ibex:vcs_sim_cycle"
    echo "cycle_source=$IBEX_CYC_SRC"
    echo "engine_labels_verified=yes"
    echo "evidence_sources_consistent=yes"
    if [ "${#ANOMALIES[@]}" -gt 0 ]; then
        echo "anomalies=$(IFS=';'; echo "${ANOMALIES[*]}")"
        echo "Overall: FAIL"
    else
        echo "anomalies=(none)"
        echo "Overall: PASS"
    fi
    echo "Timestamp end : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$EVIDENCE_OUT"

echo "=== p10_36layer_report: outputs ==="
echo "report   : $REPORT_OUT"
echo "evidence : $EVIDENCE_OUT"

if [ "${#ANOMALIES[@]}" -gt 0 ]; then
    echo "FAIL: ${#ANOMALIES[@]} gating anomaly(ies) — annotated in the report"
    exit 1
fi
echo "=== p10_36layer_report: report complete (exit 0) ==="
exit 0
