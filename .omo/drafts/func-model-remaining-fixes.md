# func-model-remaining-fixes — Draft

## Intent
- **Routing**: CLEAR
- **Review required**: true (user requested)
- **Intent**: Fix Func Model runtime bug (INTC KeyError only). DSE engine model bugs recorded but not fixed.
- **User decision**: Only fix Func Model, DSE record not fix

## Approval gate
- **Status**: plan-written (dual review done, findings folded in)
- **Pending action**: present summary, wait for approval

## High-accuracy review receipts
### Momus (ses_05e78c246ffezz6Et5avKlEJgt): REJECT → 3 blockers folded in
1. Baseline 866 → 957 (sim/tests/ + sim/timing/tests/) — FIXED in plan
2. Issue 007 duplicates Issue 004 — FIXED: update Issue 004 instead, add DSE report cross-ref
3. Issue 002 status text conflates numerical fix and F3 skip — FIXED: separated two concerns

### Oracle (ses_05e7838a7ffeFVeE19Luj6ZJgc): APPROVE → 3 LOW + 2 INFO folded in
1. ACK value=0 on missing key creates entry (benign) — test should verify PENDING reads 0 — NOTED in test 1
2. Missing multi-bit ACK test — NOTED (test 3 covers serial; concurrent multi-bit is optional enhancement)
3. Issue 002 status text ambiguity — same as Momus #3, FIXED
4. Issue 007 should reference DSE report — FIXED: Issue 004 update includes report link
5. F2 missing compileall on new test file — FIXED: F2 now includes both files
6. F4 scope too coarse — FIXED: F4 now includes git diff --name-only sub-check

## Findings
### Issue 002 (mmul_smoke)
- ALREADY FIXED by BUG-SOC-FM-005 (commits 67de684 + 78a3a37)
- Baseline d6b1adc postdates fix commits
- mmul_smoke was SKIPPED on F3 (model file missing), NOT FAILED
- Action: mark Resolved in issues.md (separating numerical fix from F3 environment skip)

### Issue 003 (INTC KeyError)
- `sim/mmio_bridge.py:590` — `self._status[INTC.BASE + INTC.PENDING] &= ~value`
- Fix: `.get(INTC.BASE + INTC.PENDING, 0) & ~value` — only instance in codebase
- Oracle confirmed semantic correctness for all edge cases (value=0, PENDING=0, value>PENDING)
- Action: FIX (1 todo)

### Issue 004 (8 test_engines.py failures) → reclassified as DSE model issues
- These are DSE timing model tests, NOT Func Model golden reference tests
- Func Model golden uses GoldenMXU.matmul_int4_per_block, independent of DSE engine library
- Current hardware = Block Engine; DSE engine bugs don't affect golden or RTL verification
- Detailed report: reports/dse-engine-model-bugs-2026-07-27.md (8 bugs, 3 model fixes + 4 stale tests + 1 incomplete model)
- Action: Update Issue 004 to "Documented, not fixed" with DSE report cross-ref (do NOT create new Issue 007)

### FM-SOC-032 (Issue 006)
- RTL test requiring VCS simv (not Python)
- Pure Python equivalent exists: test_28block_scaled_chain in test_soc_fm.py
- The hang is RTL-specific (doorbell/handshake/timing), cannot reproduce in Python
- Action: Out of scope for this plan (requires RTL environment)