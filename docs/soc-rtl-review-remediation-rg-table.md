# soc-rtl-review-remediation 红转绿汇总表

**Plan:** `.omo/plans/soc-rtl-review-remediation.md` todo 15
**Branch:** `soc-rtl-review-remediation`
**Date:** 2026-08-31
**原则:** 每组负测必须同时具备修复前 RED 与修复后 GREEN 两条独立证据路径（引用同一 evidence 文件不同区段的亦须逐条 grep 验证），缺任一证据则如实标 MISSING-EVIDENCE。所有路径与 marker 均于本表生成当日经 live grep 验证（见附录 A，逐条记录实际 grep 输出，非凭记忆复述）。

## 汇总表

| # | 负测名称 | 修复前 RED 证据路径 | 修复后 GREEN 证据路径 | 对应修复 todo | mutation 保持 RED 确认 |
|---|---|---|---|---|---|
| 1 | Timeout exit-code + SEG_TIMEOUT_S validation（`sim/regression/test_timeout_behavior.sh`） | `.omo/evidence/task-1-soc-rtl-review-remediation.txt:302,308,325`（`FAIL [RED]: RUN_RC=124 maps to exit 0`、`SEG_TIMEOUT_S='--help' NOT rejected`、`TEST RESULT: RED — 4 assertion(s) failed`） | 同文件 task-9 证据 `.omo/evidence/task-9-soc-rtl-review-remediation.txt:21,116,230`（`GREEN now: rc=0, "assertions passed: 8 / failed: 0"`、`[V4] test_timeout_behavior.sh -> rc=0 GREEN`、`TASK-9 RESULT: GREEN (expected)`） | todo 9（runner fail-closed） | n/a — 本组无 mutation 注入设计；RED/GREEN 契约由脚本自身断言直接证明（todo 1 证据 :77-79 明示"仅靠修好的脚本在自身改坏时仍会断言失败"的自校验规则，即脚本即 mutation 载体） |
| 2 | Crossbar real-contention fairness（`rtl/tb/axi_crossbar_fairness_tb.sv`，P4 窗口） | `.omo/evidence/task-2-soc-rtl-review-remediation.txt:81-88`（`FAIRNESS: FAIL` + `WATCHDOG: master 0 AR accepted at cycle 385, R not complete within 10000 cycles (phantom-accept deadlock)` + `[soc-run] RESULT: FAIL for run_crossbar_fairness`） | `.omo/evidence/task-7-soc-rtl-review-remediation.txt:136-141`（`P4 AR grants per master: 393 393 393 393 393 393 393`、`CHECK P4 AR grant fairness max-min=0 <= 1: PASS`，AW 同 393×7 diff=0） | todo 7（crossbar fairness 修复） | **RED 确认**：`.omo/evidence/task-7-soc-rtl-review-remediation.txt:157-166`（fixed-priority mutation run：`P4 AR grants: 2751 0 0 0 0 0 0`、`CHECK ... max-min=2751 > 1: FAIL`、`:164 FAIRNESS: FAIL`、`CROSSBAR_FAIRNESS_MUTATION: RED as expected`） |
| 3 | APB conformance real peripherals（`run_apb_conformance_real`，真实外设语义） | `.omo/evidence/task-3-soc-rtl-review-remediation.txt:106,108-109`（`Divergences  : 40 (real RTL != model-slave expectation)`、`APB_CONFORMANCE_REAL: RED (40 divergences vs model-slave oracle)`、`TASK-3 RESULT: RED (expected)`） | `.omo/evidence/task-12-soc-rtl-review-remediation.txt:116-117,131-132`（`Total checks : 214`、`Passes       : 214`、`APB_CONFORMANCE_REAL: GREEN (7/7 peripherals, 214 checks, 8 doc-div [BUG-RTL-SOC-009/010/011])`、`TASK-12 RESULT: GREEN (expected)`） | todo 12（APB conformance 接真实外设 RTL + 独立 oracle） | n/a — 本组以 bug-filing 机制替代 mutation：真实外设暴露的 8 处文档/RTL 偏差未静默改 oracle，而是如实建档 `docs/bugs/bugs-soc-rtl.md:646 (BUG-RTL-SOC-009)、:699 (BUG-RTL-SOC-010)、:748 (BUG-RTL-SOC-011)`，TB 以 [DOC-DIV] tag 断言真实行为（task-12 :142-149） |
| 4 | Regression statistics accounting（`run_ibex_full_rtl.sh` 统计口径） | `.omo/evidence/task-4-soc-rtl-review-remediation.txt:69,83,87,149`（`ASSERT 1: FAIL - superseded cases misclassified (must be SKIP, not PASS)`、`ASSERT 4: FAIL - fake simulator exited 124 but exit code was swallowed by || true`、`OVERALL: RED - 2 assertion(s) failed`、`TASK-4 RESULT: RED (expected)`） | todo 9 runner 修复：`.omo/evidence/task-9-soc-rtl-review-remediation.txt:122`（`[V5] bash sim/regression/test_regression_stats.sh -> rc=0 GREEN`）+ todo 13 全量重审：`.omo/evidence/task-13-soc-rtl-review-remediation.txt:80-83,96`（`{"executed": 25, "failed": 0, "matched": 33, "mismatches": []`、`(25 executed / 6 superseded / 2 na / 0 failed / 0 timeout)`、`TASK-13 RESULT: GREEN (expected)`） | todo 9（+13 重审） | n/a — A1/A4 断言用 fake simulator（exit 124 / superseded 注入样本）即为内置对抗校验；`|| true` 吞掉 exit 124 的 RED 与修复后 `TIMEOUT` 分类在 task-4 :129-135 明示同一断言对正确语义的 pin 定 |
| 5 | Firmware allowlist/size/completion（`sim/tests/test_firmware_addr_allowlist.py`） | `.omo/evidence/task-5-soc-rtl-review-remediation.txt:6,181`（`Result    : 6 FAILED (RED, expected) + 1 PASSED (near-end source-pin control). pytest exit code = 1.`、`TASK-5 RESULT: RED (expected)`） | `.omo/evidence/task-8-soc-rtl-review-remediation.txt:22,195`（`→ 7 passed in 1.26s (all six previously-RED now GREEN; the near-end`、`TASK-8 RESULT: GREEN (expected)`） | todo 8（firmware allowlist + actual-size + completion-bounds 修复） | n/a — 控制测试（near-end source-pin，`addr >= DRAM_END`/`size > DRAM_SIZE` 等）在 RED 阶段即 PASS，修复后保持 PASS（task-8 :22 明示 6 fixes + 1 control），证明测试未被"改到全红再改到全绿"的游戏化 |
| 6 | Evidence provenance/pickle（`sim/regression/test_evidence_provenance.sh`） | `.omo/evidence/task-6-soc-rtl-review-remediation.txt:41,352,354`（`Assertions       : 17 failed [RED] / 2 passed (env sanity + timestamp field`、`TEST RESULT: RED — 17 assertion(s) failed`、`TASK-6 RESULT: RED (expected)`） | `.omo/evidence/task-11-soc-rtl-review-remediation.txt:146,185`（`bash sim/regression/test_evidence_provenance.sh   -> exit 0 (17 PASS / 0 FAIL)`、`TASK-11 RESULT: GREEN (expected)`） | todo 11（evidence provenance + pickle 安全） | n/a — 对抗守卫内置：`.omo/evidence/task-11-soc-rtl-review-remediation.txt:74-75`（`forged (simv tampered after write) -> exit 1 REJECTED (sha mismatch)`、`truncated (provenance_end removed) -> exit 1 REJECTED`），即 `scripts/check_evidence_provenance.py` 对伪造/截断证据保持 REJECTED rc=1，等价于 mutation 保持 RED |

**统计：** 6/6 组 RED→GREEN 双证据齐全；3 组带显式 mutation（组 2 真 mutation run RED 确认；组 4、6 内置对抗校验充当 mutation 等价物并如实标注）；组 1、3、5 无 mutation 设计，原因逐行标注，未虚构 mutation。

## 附录 A — 逐条 grep 验证记录

以下为 2026-08-31 实际执行的 grep 命令与关键输出（截取证据行，未编造）：

### 组 1（task-1 RED / task-9 GREEN）

```bash
$ grep -n -E "RED|FAIL|RESULT" .omo/evidence/task-1-soc-rtl-review-remediation.txt
302:FAIL [RED]: timeout exit-code mapping: RUN_RC=124 maps to exit 0 (0) — a timed-out run reports SUCCESS (runner run_ibex_segment_run.sh:68..EOF)
308:FAIL [RED]: SEG_TIMEOUT_S='--help' NOT rejected with exit 2 (region exited 0) — no SEG_TIMEOUT_S validation in run_ibex_segment_run.sh:59..EOF
311:FAIL [RED]: SEG_TIMEOUT_S='abc' NOT rejected with exit 2 (region exited 125) — no SEG_TIMEOUT_S validation in run_ibex_segment_run.sh:59..EOF
314:FAIL [RED]: SEG_TIMEOUT_S='-5' NOT rejected with exit 2 (region exited 125) — no SEG_TIMEOUT_S validation in run_ibex_segment_run.sh:59..EOF
325:TEST RESULT: RED — 4 assertion(s) failed (expected before todo 9 fix; must turn GREEN after the fix)

$ grep -n -E "8/8|GREEN|RESULT" .omo/evidence/task-9-soc-rtl-review-remediation.txt
21:  -> GREEN now: rc=0, "assertions passed: 8 / failed: 0",
116:[V4] bash sim/regression/test_timeout_behavior.sh          -> rc=0 GREEN
230:TASK-9 RESULT: GREEN (expected)
```

### 组 2（task-2 RED / task-7 GREEN + mutation RED）

```bash
$ grep -n -E "FAIRNESS|watchdog|deadlock|RESULT" .omo/evidence/task-2-soc-rtl-review-remediation.txt
81:    FAIRNESS: FAIL
82:    [TB] WATCHDOG: master 0 AR accepted at cycle 385, R not complete within 10000 cycles (phantom-accept deadlock)
86:    CROSSBAR_FAIRNESS: FAIL
88:    [soc-run] RESULT: FAIL for run_crossbar_fairness

$ grep -n -E "393|max-min|2751|FAIRNESS: FAIL|RED as expected" .omo/evidence/task-7-soc-rtl-review-remediation.txt
136:    [TB]   P4 AR grants per master: 393 393 393 393 393 393 393 (total 2751)
137:    [TB]   CHECK P4 AR grant fairness max-min=0 <= 1: PASS
138:    [TB]   P4 AW grants per master: 393 393 393 393 393 393 393 (total 2751)
139:    [TB]   CHECK P4 AW grant fairness max-min=0 <= 1: PASS
157:    [TB]   P4 AR grants per master: 2751 0 0 0 0 0 0 (total 2751)
158:    [TB]   CHECK P4 AR grant fairness max-min=2751 > 1: FAIL
159:    [TB]   P4 AW grants per master: 2751 0 0 0 0 0 0 (total 2751)
160:    [TB]   CHECK P4 AW grant fairness max-min=2751 > 1: FAIL
164:    FAIRNESS: FAIL
165:    CROSSBAR_FAIRNESS_MUTATION: RED as expected (mutation guard holds)
```

### 组 3（task-3 RED / task-12 GREEN + bug ledger）

```bash
$ grep -n -E "Divergences|40|RESULT|RED" .omo/evidence/task-3-soc-rtl-review-remediation.txt
106:  Divergences  : 40   (real RTL != model-slave expectation)
108:  APB_CONFORMANCE_REAL: RED (40 divergences vs model-slave oracle)
109:  TASK-3 RESULT: RED (expected)

$ grep -n -E "214|7/7|RESULT|GREEN" .omo/evidence/task-12-soc-rtl-review-remediation.txt
116:  Total checks : 214
117:  Passes       : 214
131:  APB_CONFORMANCE_REAL: GREEN (7/7 peripherals, 214 checks, 8 doc-div [BUG-RTL-SOC-009/010/011])
132:  TASK-12 RESULT: GREEN (expected)

$ grep -n -E "BUG-RTL-SOC-009|BUG-RTL-SOC-010|BUG-RTL-SOC-011" docs/bugs/bugs-soc-rtl.md
646:### BUG-RTL-SOC-009 — Doorbell ABI window ...
699:### BUG-RTL-SOC-010 — pcie_ep_wrapper header overstates implemented fields ...
748:### BUG-RTL-SOC-011 — rtl/ip/README DMA access classes wrong ...
```

### 组 4（task-4 RED / task-9 + task-13 GREEN）

```bash
$ grep -n -E "ASSERT|OVERALL|RESULT" .omo/evidence/task-4-soc-rtl-review-remediation.txt
69:ASSERT 1: FAIL - superseded cases misclassified (must be SKIP, not PASS): ...
83:ASSERT 4: FAIL - fake simulator exited 124 but exit code was swallowed by || true: classified 'FAIL', TIMEOUT=0 (expected TIMEOUT)
87:OVERALL: RED - 2 assertion(s) failed (expected pre-todo-9)
149:TASK-4 RESULT: RED (expected)

$ grep -n "test_regression_stats" .omo/evidence/task-9-soc-rtl-review-remediation.txt
122:[V5] bash sim/regression/test_regression_stats.sh          -> rc=0 GREEN

$ grep -n -E "25 executed|mismatches|RESULT" .omo/evidence/task-13-soc-rtl-review-remediation.txt
80:  {"executed": 25, "failed": 0, "matched": 33, "mismatches": [],
83:  (25 executed / 6 superseded / 2 na / 0 failed / 0 timeout)
96:TASK-13 RESULT: GREEN (expected)
```

### 组 5（task-5 RED / task-8 GREEN）

```bash
$ grep -n -E "failed|RESULT" .omo/evidence/task-5-soc-rtl-review-remediation.txt
6:Result    : 6 FAILED (RED, expected) + 1 PASSED (near-end source-pin control). pytest exit code = 1.
181:TASK-5 RESULT: RED (expected)

$ grep -n -E "7 passed|RESULT" .omo/evidence/task-8-soc-rtl-review-remediation.txt
22:      → 7 passed in 1.26s (all six previously-RED now GREEN; the near-end
195:TASK-8 RESULT: GREEN (expected)
```

### 组 6（task-6 RED / task-11 GREEN + 对抗守卫）

```bash
$ grep -n -E "17 failed|RESULT" .omo/evidence/task-6-soc-rtl-review-remediation.txt
41:Assertions       : 17 failed [RED] / 2 passed (env sanity + timestamp field,
352:TEST RESULT: RED — 17 assertion(s) failed (expected before todo 9/11 fixes; ...)
354:TASK-6 RESULT: RED (expected)

$ grep -n -E "17 PASS|forged|truncated|RESULT" .omo/evidence/task-11-soc-rtl-review-remediation.txt
74:      forged (simv tampered after write)   -> exit 1  REJECTED (sha mismatch)
75:      truncated (provenance_end removed)   -> exit 1  REJECTED
146:  bash sim/regression/test_evidence_provenance.sh   -> exit 0 (17 PASS / 0 FAIL)
185:TASK-11 RESULT: GREEN (expected)
```

## 附录 B — 对抗性说明（本表自身的 provenance）

- **misleading_success_output:** 表中每个 marker 均于 2026-08-31 用 grep 在对应文件中实际命中并记录行号（附录 A 为截取的真实输出，非凭记忆复述）；GREEN 声明均锚定 `TASK-{N} RESULT: GREEN (expected)` 行而非终端文本推断。
- **dirty_worktree:** 本任务仅新增 `docs/soc-rtl-review-remediation-rg-table.md` 与 `.omo/evidence/task-15-soc-rtl-review-remediation.txt` 两个文件；不做 git commit（由 orchestrator 统一提交）。
- **诚实标注:** 组 1/3/5 无 mutation 注入设计，表格中如实标注 n/a 及替代机制（自校验脚本 / bug-filing / 控制测试），未虚构 mutation 证据；组 3 的 8 处 doc-div 未计入"转绿"成就，以独立 bug 条目保持可追踪。
