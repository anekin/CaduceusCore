
## [2026-07-23 15:00] Bug BUG-RTL-SOC-WV-001 logged

- **Bug ID**: BUG-RTL-SOC-WV-001
- **Summary**: SFU wrapper never asserts STATUS.DONE after processing completes
- **Block**: wrapper-level-verification T2 (Wave 1)
- **Severity**: Major
- **Status**: Open
- **Evidence**: build/evidence/wrap-sfu-regression.txt, build/evidence/wv-sfu-run.log
- **Logged to**: docs/bugs/bugs-soc-rtl.md (lines 518-574)

### Key facts

- 5 cocotb tests; only test_apb_regmap_rw PASS.
- APB and AXI data paths work correctly; SFU produces correct output (verified via AXI AW bursts).
- STATUS.DONE never transitions to 1, even after 5M cycles.
- IP-level SFU regression 319/319 PASS — root cause is in the wrapper glue, not sfu_top arithmetic.

### Next steps

1. Waveform-level debug: trace sfu_top.done vs wrapper STATUS.DONE propagation.
2. Investigate whether start_hold, post_start_stall, or width-converter FSM is suppressing the DONE transition.
3. See learnings.md lines 23-86 for full debug history.
