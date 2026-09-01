# soc-rtl-review-remediation — Issues / Blockers

## Issue 1 (todo 13, 2026-08-31): SKIP accounting dead code — 8 designed-skip cases counted PASS

- **Where**: `sim/rtl_soc_runner.py:4402` + `sim/regression/run_ibex_full_rtl.sh:126-129`
  + `scripts/audit_fm_soc_statistics.py:29-33`
- **What**: The superseded/N-A verdicts (FM-SOC-014/015/016/021/022/023 →
  "superseded by FM-SOC-027/032/10X"; FM-SOC-017/019 → "skipped: direct
  APB/AXI case not applicable to Ibex RTL mode") are emitted via Python
  `logger.info` and never reach the VCS case log under cocotb 1.9.0+VCS.
  The todo-9 shell grep and the audit's superseded/na branches therefore
  can never match: the clean-commit full regression (RUN_ID
  20260831T131503-89771, HEAD be3bf04) produced PASS=33 SKIP=0 and audit
  rc=1 (executed=33, superseded=0, na=0, matched=25, 8 mismatches).
- **Evidence**: "IbexRunner" occurs 0 times in all 33 case logs;
  `logger.warning` lines (BOOT_ASSERT/SP_INIT/BOOT_ROM) DO appear.
  Tracked `build/evidence/fm-soc-regression.txt` (pre-remediation,
  09f753e2) also shows PASS=33 SKIP=0 — the defect pre-dates the
  remediation; todo 9 (6230e214) did not touch rtl_soc_runner.py.
- **Impact**: todo-13 acceptance "6 superseded + 2 N/A 计 SKIP" not met;
  the four-class accounting is not trustworthy until fixed. Functional
  RTL outcome unaffected (25 executed cases genuinely PASS, 0 FAIL, 0
  TIMEOUT; the 8 cases are 4.50 ns no-ops by design).
- **Suggested fix**: one line — `logger.info` → `logger.warning` at
  `sim/rtl_soc_runner.py:4402` (consistent with surrounding usage), then
  re-run full regression and re-audit.
- **Status**: OPEN — disposition to todo 15 (red→green summary) / F2.

## Issue 2 (todo 14 F3, 2026-08-31): run_e2e_blk0 op05 attn_score MMUL FAIL — PRE-EXISTING, NOT a crossbar regression

- **Where**: `rtl/test_vectors/qwen_blk0/op05_attn_score_MMUL_golden.hex` vs RTL
  output SRAM @0x20020000; fails in `make run_e2e_blk0` (TESTCASE=test_qwen_blk0).
- **What**: op5 attn_score MMUL (M=32 K=128 N=2 tiles=2) FAILs with
  62/64 INT32 mismatches — first mismatch @ byte[8] (actual=0x00, golden=0xD0);
  words 0-1 correct, words 2-63 zero; 634 cycles; 1/17 ops failed.
- **Attribution (this investigation)**: single-variable A/B on sz0001 —
  post-fix HEAD e73e28e vs pre-fix crossbar `c478ae5~1:rtl/soc/axi_crossbar.v`,
  same firmware/tb/test/vectors, /tmp builds, identical Makefile recipe.
  Both runs FAIL **byte-identically** (log diff = only 4 environmental lines:
  compiler timestamp, python seed, wall-clock real time/ratio, CPU time).
  Crossbar fix c478ae5 is behaviorally neutral on this path.
  => **PRE-EXISTING. Rollback gate (plan Metis m4) NOT triggered; c478ae5 stays.**
- **Stale-golden hypothesis (OPEN)**: blk0 golden vectors + manifest generated
  2026-07-07 (a29e93c), ~8 weeks before the fix and before the firmware
  allowlist fix (cee6697) + firmware rebuild (hex mtime 2026-08-31 13:08).
  No DESC_BASE warnings and no X-propagation in either run (0 hits).
- **Attention-family tie**: BUG-RTL-SOC-007 is op07 attn_weight (cycles=0,
  op never executes, still Open). This is op05 attn_score — same attention
  chain, DISTINCT signature (op executes; output rows beyond row 0 are zero).
- **Evidence**: `.omo/evidence/task-14-blk0-investigation.txt` (+ full logs
  `task-14-blk0-repro.log`, `task-14-blk0-baseline.log` — 227902 B each,
  `task-14-blk0-build-notes.log`; F3 original `_tmp_task14_f3c_blk0.log`).
- **Suggested next steps**: `scripts/verify_ops_func_model.py` op05 golden
  sanity; MXU accumulator-drain / writeback for multi-tile M-loop.
- **Status**: attribution RESOLVED (PRE-EXISTING); root cause OPEN — not a
  crossbar defect, does not block c478ae5.
