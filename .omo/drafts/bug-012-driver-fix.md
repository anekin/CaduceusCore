---
slug: bug-012-driver-fix
status: approved-started
intent: clear
review_required: false
pending-action: dispatch-execution-worker
approach: BUG-012 driver-side light fix — DIM1=real-N in cocotb driver + diagnose + test direct-write + padding-audit contract flip; sz0001 regression (attn_score RED→GREEN + layout + dense varN + 33 FM-SOC + blk0 + op05/07 + W4-PERF + P9 + full pytest); ledger Fixed with residual pow2 constraint; wrapper W1/W2 hardening deferred to bug-012-fix.md remaining todos.
---

# Draft: bug-012-driver-fix

## Components (topology ledger)
| id | outcome | status | evidence path |
| --- | --- | --- | --- |
| 0-p0-baseline | branch bug-012-driver-fix + provenance + 7-dirty snapshot | active | .omo/evidence/task-0-bug-012-driver-fix.txt |
| 1-d1-driver-fix | DIM1=real-N in 3 code sites + audit-test flip + scoped pytest | active | .omo/evidence/task-1-bug-012-driver-fix.txt |
| 2-t5-red-green | run_e2e_attn_score 64/64 PASS (RED→GREEN) | active | .omo/evidence/task-2-bug-012-driver-fix.txt |
| 3-full-regression | layout+dense+33 FM-SOC+blk0+op05/07+W4+P9+pytest all green | active | .omo/evidence/task-3-bug-012-driver-fix.txt |
| 4-ledger-fixed | BUG-012 Fixed + stats 12/3 + README + HTML correction | active | .omo/evidence/task-4-bug-012-driver-fix.txt |
| F1-F4 | final review wave all APPROVE | active | (plan file) |

## Open assumptions (announced defaults)
| assumption | adopted default | rationale | reversible? |
| --- | --- | --- | --- |
| fix scope | driver-side only (light); wrapper W1/W2 deferred | ledger Fix section prefers driver-side (low-risk, test-infra); Phase B probe (H-TRIGGER-DIM1: actual-N-sufficient) proves real-N works on current wrapper; user chose "先把bug012真正修掉" matching offered option B (driver-side) | yes — W1/W2 remain in bug-012-fix.md as follow-up |
| heavy-plan precondition | bug-012-fm-audit completed+merged (10/10 todos checked; test_fm_abi_contract.py tracked in main) → light plan unrestricted | verified via plan file grep + git status of test file | yes |
| padding-audit test | must flip expected→hits==[] in same commit as driver change | test asserts pad formula EXISTS in exactly 2 driver files (test_fm_abi_contract.py:117-137); removing formula without flipping = guaranteed pytest FAIL — heavy plan missed this | yes |
| non-pow2 N regression risk | full 33 FM-SOC + W4 + P9 must all pass; any non-pow2-N driver case failing on wrapper pow2 constraint → STOP and escalate (light path insufficient, user decides W1/W2) | wrapper store-out FSM still assumes N*4 pow2 for N≤64 (mxu_soc_wrapper.v:729-735); known N values in suite are pow2 (2/64/128/2048) so expected green | yes — escalation route defined in todo 3 failure QA |

## Findings (cited - path:lines)
- `sim/cocotb_bridge.py:2104` — `engine_n = ((instr.dim_n + 63) // 64) * 64` pad formula STILL PRESENT (grep verified 2026-09-21)
- `rtl/wrapper/mxu_soc_wrapper.v:221` — `wrp_n_derived = (dim1_n != 16'd0) ? dim1_n : wrp_n` (latched DIM1 wins over WRP_DIM_N); :719-720 row_bytes driven by wrp_n_derived
- `sim/cocotb_bridge.py:3958` — test_e2e_attn_score direct-write `base + 0x10, 64`; :3916-3922 stale docstring
- `sim/diagnose_data_layout.py:151,154` — same pad formula + DIM1 write
- `sim/tests/test_fm_abi_contract.py:117-137` — audit test asserts pad pattern present in exactly {cocotb_bridge.py, diagnose_data_layout.py}
- `.omo/plans/bug-012-fm-audit.md` — 10/10 todos checked (precondition satisfied); its A3 created the audit test
- `.omo/plans/bug-012-fix.md` — heavy plan 0/10 todos checked (never executed); contains W1/W2 hardening todos 2/3 deferred by this plan
- `docs/xverif-debug-case-bug012.html:338-369` — section 9 overclaims (Phase B probe results presented as post-fix regression); :343 WRP_DIM_N mechanism wrong; fix retroactively legitimizes table after T5 green, only wording corrections needed
- `firmware/npu_firmware.c:273` — firmware already writes real N to DIM1 (semantic baseline)
- BUG-012 ledger entry: docs/bugs/bugs-soc-rtl.md:826-964, Status=Open, Fix="NOT fixed — investigation-only"

## Decisions (with rationale)
1. Light path (driver-only) over heavy plan full scope — user selected option B ("先把bug012真正修掉" after my A/B offer where B = driver-side fix + flip ledger); ledger Fix section prefers driver-side; product RTL untouched = lower risk
2. Audit-test contract flip folded into todo 1 (same atomic commit) — "Implementation + Test = ONE todo"; without it pytest breaks
3. Ledger residual constraints record wrapper pow2 limitation + W1/W2 defer pointer — honest Fixed wording per F2 no-overclaim rule
4. HTML wording corrections (not rewrite) folded into todo 4 — after real fix + evidence, section 9 table becomes accurate; only :343 mechanism line and :368 status citation need touches
5. sz0001 serial VCS (todo 2→3) — shared simv rebuild race prevention (project convention)

## Scope IN
todos 0-4 + F1-F4 as written in .omo/plans/bug-012-driver-fix.md

## Scope OUT (Must NOT have)
- rtl/ ANY change (W1/W2 deferred); firmware/; gen/; config/; vendored; qwen_blk0 net diff; 7 dirty files; push without user okay

## Open questions
(none — user approved approach + start in one message: "先把bug012真正修掉")

## Approval gate
status: approved-started
User decision 2026-09-21: "先把bug012 真正修掉" = approval of driver-side light approach AND explicit start instruction. Plan written. Metis gap analysis next, then dispatch execution worker (todos 0-4 + F1-F4, STOP conditions per plan). Merge to main + push only after F-wave all-APPROVE + user explicit okay.

## Review receipts
- Metis gap analysis (mandatory, ses_f3b8b0407ffeL3iLIk6Qrk4OKp, 2026-09-21): verdict **SOUND-WITH-FINDINGS** — 1 BLOCKER + 1 MAJOR + 3 MINOR, all 5 findings independently verified by planner against source and FOLDED into the plan:
  - #1 BLOCKER: todo-1 acceptance grep would false-hit golden_executor.py:1630/1639/1643/1645 scratch alignment → replaced with PAD_PATTERN-equivalent regex + pytest audit test as authoritative gate
  - #2 MAJOR: _mxu_preload docstring :2259-2262 stale WRP_DIM_N myth → added to todo 1 item (4)
  - #3 MINOR: Makefile op07 gate at :729-741 (reference range was truncated) → todo 3 References corrected
  - #4 MINOR: todo 2 acceptance now requires BOTH make gate (`test_e2e_attn_score.*PASS`, Makefile:683) and test PASS line
  - #5 MINOR: test_fm_abi_contract.py:12-14 module header comment → added to todo 1 item (6)
- High-accuracy review: NOT requested (review_required: false) — user may opt in at delivery.
