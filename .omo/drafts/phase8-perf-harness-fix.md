---
slug: phase8-perf-harness-fix
status: approved
intent: clear
pending-action: write .omo/plans/phase8-perf-harness-fix.md (scaffolded, todos being APPENDED below)
approach: Fix Python harness only (sim/perf_tests.py), no RTL/firmware, validate on sz0001 VCS, keep all NOT-RESOLVED items deferred per A1+B1+D1
---

# Draft: phase8-perf-harness-fix

## Components (topology ledger)
| id | outcome (one line) | status | evidence path |
|:---|:---|:---|:---|
| 8.0 | Fail-first diagnostic proves data-layout mismatch hypothesis on a single 64×64 tile | active | `build/evidence/ph8-diagnostic.txt` |
| 8.1 | Apply tile-major packing + correct descriptor sizes in perf_tests.py | active | git diff on `sim/perf_tests.py` |
| 8.2 | Add SFU/Vector op=1/2 dispatch in fullchain test with real golden | active | git diff on `sim/perf_tests.py` |
| 8.3 | Re-run PERF-01..P20 + fullchain on sz0001 VCS; chunked with ssh + retry | active | `build/evidence/w4-perf-p*.txt` (post-fix), `build/evidence/fullchain-pipeline.txt`, `build/evidence/ph8-perf-11-before-after.txt` |
| 8.4 | Re-run 33/33 FM-SOC regression via `bash sim/regression/run_fm_soc_all.sh`; full log artifact | active | `build/evidence/ph8-fm-soc-33.log` |
| 8.5 | Sync rtl/testcase-list-perf.md status column (PERF-11 → PASS if confirmed) | active | `rtl/testcase-list-perf.md` |
| 8.6 | Update docs/issues_found.md Phase 8 Resolution Status + Root Cause Verdict matrix | active | `docs/issues_found.md` |
| 8.7 | Closure: build/evidence/ph8-closure.txt with FIXED vs NOT-RESOLVED, Phase 9 forward | active | `build/evidence/ph8-closure.txt` |
| F1-F4 | Plan compliance / scope fidelity / evidence consistency / issues rollup | forward | final wave receipts |

## Open assumptions (announced defaults)
| assumption | adopted default | rationale | reversible? |
|:---|:---|:---|:---|
| Data-layout hypothesis (explore claim) is true | Adopted as the working hypothesis, BUT must be proven by 8.0 fail-first diagnostic before code change | Metis G1; unverified explore claim | YES — 8.0 falsifies it and triggers NOT-RESOLVED fallback |
| PERF-01..P04 backward compatibility | Fix must keep them PASS post-change at cos_sim≥0.999; halt otherwise | Metis G3 | YES — fallback records new NOT-RESOLVED if regression appears |
| pack helpers live in `sim/cocotb_bridge.py` | Import them read-only from `sim/perf_tests.py`; do NOT reimplement | Metis G5; avoids duplicate bugs | YES — switch to copy if import causes circular dep |
| Synthetic PASS entries in evidence | Tag with `source="analytical"` field; do not count as measured for F1 | Metis G7 | YES |
| 36-layer + FM-3 RTL + Q8_0 + plan 6b | All remain NOT RESOLVED; deferred to Phase 9+ | User decisions A1, D1 | NO (owner-decision) |
| RTL / firmware changes | Strictly forbidden; if root cause is genuinely in RTL, document only | User decision B1 | NO (owner-decision) |

## Findings (cited - path:lines)
- `firmware/npu_firmware.c:419-455` — firmware already streams weights per K-block (`wbuf[0]/wbuf[1]` ping-pong, `num_blocks=(K+63)/64`); the "64KB weight buffer" name is a misnomer — `wbuf[0]` is at SRAM offset 0x00010000 = 64KB, the per-tile weight is only 2KB (`TILE_WEIGHT_BYTES = TILE_H*TILE_W/2`).
- `firmware/npu_firmware.c:458-483` — SFU (op=0x01-0x06,0x17) and Vector (op=0x0F-0x14) are present and dispatched by `dispatch_cmd()`. FM-SOC-004/005 already use this path (33/33 PASS). Phase 6 "SFU/Vector fullchain blocked" is a PERF-harness gap, not a firmware gap.
- `sim/rtl_soc_runner.py` FM-SOC path uses `pack_int8_activation_tile_major()` + `pack_int4_tile_major()` before `_dram_backdoor_write`; `sim/perf_tests.py:115-119` skips this, writing raw row-major `act.tobytes()`.
- `firmware/npu_firmware.c:323-361` — `read_mmul_desc`/`read_sfu_desc`/`read_vector_desc` field offsets: MMUL descriptor 15 words; SFU uses src[0]/src[2]/src[8]; Vector uses src[0]/src[1]/src[2]/src[8].
- `build/evidence/fullchain-pipeline.txt` currently says `"status":"PASS"` with `cos_sim=0.998`, but the note text says "0.999 threshold NOT met due to DMA zeros". Pre-existing contradiction must be reconciled (Metis G2).
- `sim/regression/run_fm_soc_all.sh:37` lists all 33 FM-SOC case IDs; the runner is the canonical regression script for 8.4.
- `build/evidence/w4-perf-p0.txt` records show PERF-02/03/07/12/14/15/16/18/19 are synthetic (copies of measurement notes, not actual VCS runs) — Metis G7.

## Decisions (with rationale)
| decision | rationale | fork |
|:---|:---|:---|
| Scope = A1 (narrow Python-only) | Lowest-risk; fixes 3 reachable blockers; defers heavy VCS | Q1 = A1 |
| Auth = B1 (no RTL, no firmware) | User explicitly chose this guardrail | Q2 = B1 |
| Q8_0 = D1 (stays NOT RESOLVED) | External network blocked; 6b + plan 6b defer | Q3 = D1 |
| Import tile-major packers read-only from cocotb_bridge.py | Avoids duplicate bugs (Metis G5) | default |
| Add 8.0 fail-first diagnostic before any code change | Metis G1 — explore claim is a hypothesis, must be proven | default |
| Chunked 8.3 with ssh + 2x retry per chunk | Metis G6 — single VCS bundle is fragile | default |
| Tag synthetic evidence entries with `source="analytical"` | Metis G7 — prevents fake pass counts | default |
| Reconcile FULLCHAIN pre-existing PASS/FAIL contradiction in 8.6 | Metis G2 | default |
| 33/33 FM-SOC must use `bash sim/regression/run_fm_soc_all.sh` + full log | Metis G12 | default |
| B1 RTL-fallback hard stops: (a) zero SRAM_OUT → data hypothesis wrong → NOT RESOLVED; (b) non-zero SRAM_OUT + zero DRAM readback → likely DMA/RTL → NOT RESOLVED, document only | Metis G9 | default |

## Scope IN
- `sim/perf_tests.py` — only product code modified (data layout helpers import OK, SFU/Vector dispatch additions, descriptor sizing)
- `rtl/testcase-list-perf.md` — status column sync
- `build/evidence/w4-perf-p*.txt`, `build/evidence/fullchain-pipeline.txt` — regenerated on sz0001; must keep Phase 7 schema (`simulator, case_id, status, cycles, cos_sim?, timestamp, commit`) and add `source="analytical"` tag where applicable
- `docs/issues_found.md` — add Phase 8 Resolution Status section + Root Cause Verdict matrix (Metis G11)
- new evidence under `build/evidence/ph8-*.txt`
- import helpers (`pack_int8_activation_tile_major`, `pack_int4_tile_major`) from `sim/cocotb_bridge.py` — read-only, do NOT modify cocotb_bridge.py

## Scope OUT (Must NOT have)
- Any RTL Verilog/SystemVerilog modification (`rtl/**/*.v`, `rtl/**/*.sv`) including `rtl/ip/dma_wrapper.v`, `rtl/wrapper/mxu_soc_wrapper.v`, `rtl/soc/sram_ctrl.v`
- Any firmware C modification (`firmware/npu_firmware.c`, `firmware/npu-regmap.h`)
- Q8_0 GGUF download (external network blocked)
- Full 36-layer RTL forward pass (deferred to Phase 9+)
- FM-3 weight-streaming overlap RTL measurement (deferred per A1)
- Phase 6 plan 6b checkbox revert / experiment re-run (depends on Q8_0)
- Reimplementing `pack_int8_activation_tile_major` / `pack_int4_tile_major` in perf_tests.py (must import)
- grep-only completion claims for RTL re-runs (every 8.3/8.4 verdict requires log artifact)
- Auto-marking blockers RESOLVED when only test PASS, without Root Cause Verdict (Metis G11)

## Open questions
NONE — all three owner-decisions resolved in one approval turn ("好的,按照你的推荐来做计划"). Clearance check passed.

## Approval gate
status: **approved**
source: user explicit reply on 2026-07-19 ("好的,按照你的推荐来做计划") accepting the recommended A1+B1+D1 combo after the brief was presented.
pending action: scaffold done; APPEND todo batches into `.omo/plans/phase8-perf-harness-fix.md` `## Todos` region now; fill `## TL;DR (For humans)` last; run Classify=Standard so high-accuracy review is optional (CLEAR path, `review_required=false`); then present summary and ask the start-work-now-or-high-accuracy-review question.