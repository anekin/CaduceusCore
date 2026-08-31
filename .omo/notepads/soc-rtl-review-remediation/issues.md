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
