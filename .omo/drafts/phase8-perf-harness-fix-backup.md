# phase8-perf-harness-fix — Planning Draft (BACKUP of hand-written state, preserved for reference)

> Originally hand-written before scaffold run. Kept for reference only — the active draft is `.omo/drafts/phase8-perf-harness-fix.md` after scaffold regeneration. This backup will be removed once integration completes.

## Meta

- **plan slug**: phase8-perf-harness-fix
- **intent**: CLEAR
- **review_required**: false
- **classification**: Standard (1-5 files, firmware C code explicitly OUT of scope per drag B1)
- **route**: intent-clear
- **started**: 2026-07-19T03:35Z

## Scope (resolved)

IN (allowed to modify per user decision):
- `sim/perf_tests.py` — primary fix surface (data layout + op=1/2 dispatch + descriptor sizing)
- `rtl/testcase-list-perf.md` — status column sync after re-runs (per Phase 7 precedent)
- `build/evidence/w4-perf-p*.txt`, `build/evidence/fullchain-pipeline.txt` — re-record RTL results post-fix; must keep Phase 7 evidence schema
- `docs/issues_found.md` — append Phase 8 Resolution Status on each blocker
- new evidence files under `build/evidence/ph8-*.txt`

MUST NOT touch per user decision (B1 strict + D1):
- Any RTL Verilog (`rtl/**/*.v`, `rtl/**/*.sv`)
- Any firmware C (`firmware/npu_firmware.c`, `firmware/npu-regmap.h`)
- Q8_0 GGUF download
- Full 36-layer RTL forward pass
- FM-3 overlap RTL measurement
- Phase 6 plan 6b checkbox / experiment

If verification reveals the root cause is genuinely in RTL, record the finding as evidence only; do NOT modify RTL.

## Residual owner-decisions (FORKS, RESOLVED)

- Q1 (scope): A1 — only sim/perf_tests.py + PERF-11/12 + fullchain + 33/33 + testcase-list
- Q2 (auth): B1 — strict no RTL, no firmware
- Q3 (Q8_0): D1 — stays NOT RESOLVED

User said: "好的,按照你的推荐来做计划"

## Metis Gap Report (12 gaps to fold into todos)

G1: Explore root-cause hypothesis treated as fact — must add Wave-1 fail-first diagnostic (todo 8.0)
G2: Existing FULLCHAIN evidence contradiction (PASS but cos_sim 0.998 <0.999) — reclassify before regeneration (todo 8.6)
G3: PERF-01..P04 backward compat — must still PASS post-fix; halt if any fails (todo 8.3 acceptance)
G4: SFU/Vector descriptor packing under-specified — must reference firmware field offsets and generate real golden (todo 8.2)
G5: pack helpers live in cocotb_bridge.py — explicitly allow import or copy (scope decision in plan)
G6: VCS re-run is one undivided todo — chunk into P0-P1, P2, P3-P4, fullchain, each with ssh + retry policy (todo 8.3 split)
G7: Synthetic PASS entries — must tag `source="analytical"` or re-measure (todo 8.3 acceptance)
G8: F3 needs causal proof — pre-fix vs post-fix hex dump (todo 8.3 evidence)
G9: B1 RTL-fallback decision gate — two hard stop criteria (todo 8.3 fallback)
G10: Manual-QA channel — exact ssh command strings (todo 8.3, 8.4)
G11: Test PASS vs blocker RESOLVED — explicit Root Cause Verdict column (todo 8.6)
G12: 33/33 FM-SOC runner — name exact script + full log artifact (todo 8.4)

## Expected deliverable shape

Wave 1 (diagnostic + fix, serial):
- 8.0 Fail-first diagnostic proving data-layout hypothesis
- 8.1 Apply tile-major packing + correct input_size/weight_size in perf_tests.py
- 8.2 Add SFU/Vector fullchain dispatch with real golden

Wave 2 (verify on sz0001, chunked + ssh, parallel where independent):
- 8.3a Re-run P0+P1 on sz0001
- 8.3b Re-run P2 (PERF-11 + PERF-12) on sz0001, with pre-fix/post-fix hex dump
- 8.3c Re-run P3+P4 on sz0001
- 8.3d Re-run fullchain on sz0001, with new cos_sim ≥0.999 criterion
- 8.4 Re-run 33/33 FM-SOC via `bash sim/regression/run_fm_soc_all.sh`, full log artifact

Wave 3 (document + close, parallel):
- 8.5 Sync rtl/testcase-list-perf.md status (PERF-11 → PASS)
- 8.6 Update docs/issues_found.md Phase 8 Resolution Status + Root Cause Verdict matrix
- 8.7 Generate build/evidence/ph8-closure.txt

Final wave F1-F4.

End of backup.