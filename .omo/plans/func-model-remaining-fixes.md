# func-model-remaining-fixes - Work Plan

## TL;DR (For humans)

**What you'll get:** Func Model 唯一的运行时 bug（INTC 中断控制器在 ACK 先于 PENDING 时 KeyError 崩溃）被一句话修复并加测试覆盖。两个遗留问题（mmul_smoke 容差、DSE 引擎模型 8 个失败）在 issues.md 记录但不在本计划修复。DSE 引擎模型详细报告见 `reports/dse-engine-model-bugs-2026-07-27.md`。

**Why this approach:** INTC KeyError 是 Func Model 运行时路径上唯一的崩溃 bug，修复成本极低（一行 `.get()` 防御）。mmul_smoke 已被 BUG-SOC-FM-005 修复（确认在 commit `67de684`+`78a3a37` 之后）。DSE 引擎模型 bug 只影响架构选型对比的 cycle 估算，不影响 Func Model golden reference 或 RTL 验证。

**What it will NOT do:** 不修 DSE 引擎模型（OS-Systolic/SystolicEngine/TensorCoreEngine/GMMAEngine），不改 RTL，不改固件，不改 Spike plugin

**Effort:** Quick (1 todo)
**Risk:** Low — 单行 dict `.get()` 防御，不改 INTC 语义
**Decisions to sanity-check:** 无

Your next move: approve to start execution. Full execution detail follows below.

---

> TL;DR (machine): Quick effort, Low risk, 1 todo fixes INTC KeyError in Func Model runtime + records 2 pre-existing issues as documented-not-fixed.

## Scope

### Must have
- `sim/mmio_bridge.py:590` — 修复 `_handle_intc` 的 `&=` read-modify-write on missing dict key
- `sim/tests/test_intc_keyerror_fix.py` — 新建 INTC KeyError 回归测试（ACK-before-PENDING 不崩溃 + 正常流程不受影响）
- `.omo/notepads/func-model-gap-closure/issues.md` — 标记 Issue 002 (mmul_smoke) Resolved + 标记 Issue 003 (INTC KeyError) Fixed + 更新 Issue 004 为 "Documented, not fixed" 附 `reports/dse-engine-model-bugs-2026-07-27.md` 交叉引用

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不修改 DSE 引擎模型（`sim/engine/os_systolic_engine.py`、`sim/engine/systolic_engine.py`、`sim/engine/tensor_core_engine.py`、`sim/engine/gmma_engine.py`）——这些是 DSE 时序模型，不影响 Func Model golden reference
- 不修改 `sim/tests/test_engines.py`——DSE 测试期望值不在本计划 re-baseline
- 不修改 RTL 源码（`rtl/`）
- 不修改 C 固件源码（`firmware/`）
- 不修改 Spike plugin C++ 源码（`spike_src/`）
- 不修改 INTC 寄存器语义或 `_set_irq` 方法
- 不修改已有 pytest 回归的通过数（sim/tests/ 868 passed + sim/timing/tests/ 89 passed = 957 total 保持，只是 8 个 test_engines.py 失败继续为 known）

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD（先写测试确认 KeyError 再现，再修代码）
- Evidence: .omo/evidence/task-1-func-model-remaining-fixes.txt
- 回归基线: `PYTHONPATH=sim python -m pytest sim/tests/test_intc_keyerror_fix.py sim/tests/test_func_model_signoff_v3_intc.py -v` — 全部 PASS
- 全量回归: `PYTHONPATH=sim python -m pytest sim/tests/ sim/timing/tests/ -q` — 957 passed + 8 known failed (test_engines.py, 不变) + 5 cocotb collection errors (环境依赖, 不变)

## Execution strategy
### Parallel execution waves
> Wave 1 (1 todo): Todo 1 (INTC fix + tests + issues.md update)

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | — | F1-F4 | — |

## Todos
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [x] 1. 修复 INTC KeyError：`_handle_intc` ACK-before-PENDING 防御 + 标记 Issue 002 resolved + 记录 DSE 引擎模型 issues

  What to do:
  - **代码修复** `sim/mmio_bridge.py:590`：将 `self._status[INTC.BASE + INTC.PENDING] &= ~value` 改为 `self._status[INTC.BASE + INTC.PENDING] = self._status.get(INTC.BASE + INTC.PENDING, 0) & ~value`，匹配 `_set_irq()` (L625-626) 已有的安全 `.get(..., 0)` 模式
  - **新建测试** `sim/tests/test_intc_keyerror_fix.py`：至少 3 个测试：
    1. ACK 写入前 PENDING key 不存在 → 不抛 KeyError，PENDING 保持 0
    2. `_set_irq(8)` 后 ACK 清除对应 bit → PENDING 正确清除，原有行为不变
    3. 连续多次 ACK（模拟 Spike forward 路径）不崩溃
  - **更新 issues.md**：
    - Issue 002 段：Status 改为 "Resolved — 数值容差 bug (max_diff=1.07e+03) 已被 BUG-SOC-FM-005 修复（commit 67de684 权重 tile 布局 + commit 78a3a37 固件 activation offset），post-fix max_diff=9.16e-05。注意：F3 Spike 集成测试中 mmul_smoke 因 EDA 服务器无 1.5B 模型文件而 SKIPPED（环境问题），非数值回归。"
    - Issue 003 段：Status 改为 "Fixed by func-model-remaining-fixes Task 1: one-line .get() fix in _handle_intc, ACK-before-PENDING 防御闭环。此前标记 'Not in scope for gap-closure' 的限制已由本计划解除。"
    - Issue 004 段：Status 改为 "Documented, not fixed — DSE 时序模型 bug，不影响 Func Model golden reference 或 RTL 验证。当前选定了 Block Engine，短期内不重跑 DSE。详见 `reports/dse-engine-model-bugs-2026-07-27.md` 逐 bug 修复方案。"

  Must NOT do: 不修改 INTC 寄存器语义、不修改 `_set_irq` 方法、不修改 DSE 引擎模型代码、不修改 test_engines.py、不修改固件 C 源码、不修改 RTL

  Parallelization: Wave 1 | Blocked by: none | Blocks: F1-F4

  References:
  - `sim/mmio_bridge.py:587-593` — `_handle_intc` 方法完整代码
  - `sim/mmio_bridge.py:623-628` — `_set_irq` 方法（已用 `.get(..., 0)` 安全模式，作为参考）
  - `sim/mmio_bridge.py:28` — `self._status = {}` 初始化为空 dict
  - `sim/regmap.py` — INTC.BASE=0x40006000, INTC.PENDING=0x00, INTC.ACK=0x0C 定义
  - `sim/tests/test_func_model_signoff_v3_intc.py` — 现有 INTC 测试（9 tests），正常运行不触发 KeyError 因为 `_set_irq` 先初始化 PENDING
  - `firmware/npu_firmware.c:519,554` — 固件 ACK 写入路径
  - `.omo/notepads/func-model-gap-closure/issues.md:29-38` — Issue 002 条目 (mmul_smoke 已修复)
  - `.omo/notepads/func-model-gap-closure/issues.md:40-52` — Issue 003 条目 (INTC KeyError)
  - 探索 agent 报告关于 test_engines.py 8 个失败的详细分析（见 .omo/drafts/func-model-remaining-fixes.md Findings 段）

  Acceptance criteria:
  - `PYTHONPATH=sim python -m pytest sim/tests/test_intc_keyerror_fix.py -v` → 全部 PASS（≥3 tests）
  - `PYTHONPATH=sim python -m pytest sim/tests/test_func_model_signoff_v3_intc.py -q` → 仍然全部 PASS（无回归）
  - `python3 -c "import ast; ast.parse(open('sim/mmio_bridge.py').read())"` — 语法正确
  - `issues.md` Issue 002 标记为 "Resolved"，Issue 003 标记为 "Fixed"，Issue 004 更新为 "Documented, not fixed" 附 DSE 报告交叉引用

  QA scenarios:
  - Happy: `bridge = MMIOBridge(); bridge.handle('write', 0x40006000 + 0x0C, 0x100)` → 不抛 KeyError，返回 0；随后 `bridge._set_irq(8)` → PENDING bit 8 置位；`bridge.handle('write', 0x40006000 + 0x0C, 0x100)` → PENDING bit 8 清除
  - Failure: 移除 fix → `bridge.handle('write', 0x40006000 + 0x0C, 0x100)` on empty dict → `KeyError: 1073766400`（回归）
  - Evidence: `.omo/evidence/task1-intc-keyerror-fix.txt`

  Commit: Y | `fix(mmio_bridge): use .get() in _handle_intc to prevent KeyError when ACK precedes PENDING`

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit: todo checkbox `[x]`；evidence 文件匹配 acceptance criteria；commit message 匹配 commit strategy
- [x] F2. Code quality review: `python3 -m compileall sim/mmio_bridge.py sim/tests/test_intc_keyerror_fix.py` — 零 syntax error；改动仅在 `sim/` 目录内
- [x] F3. Real manual QA: `PYTHONPATH=sim python -m pytest sim/tests/test_intc_keyerror_fix.py sim/tests/test_func_model_signoff_v3_intc.py -v` PASS；全量 `PYTHONPATH=sim python -m pytest sim/tests/ sim/timing/tests/ -q` 不引入新回归（基线: 957 passed, 8 known failed）
- [x] F4. Scope fidelity: `git diff --stat HEAD~1..HEAD` 只在 `sim/` + `.omo/` 目录内；`git diff --name-only HEAD~1..HEAD` 逐行确认不触碰 Must NOT have 列表中的 DSE 引擎文件（`sim/engine/*.py` 除 `mmio_bridge.py` 外不改）

## Commit strategy

| Task | Commit | Message |
|------|--------|---------|
| 1 | Y | `fix(mmio_bridge): use .get() in _handle_intc to prevent KeyError when ACK precedes PENDING` |
| F1-F4 | N | (verification only) |

## Success criteria

- [x] `PYTHONPATH=sim python -m pytest sim/tests/test_intc_keyerror_fix.py -v` — 新增测试全部 PASS
- [x] `PYTHONPATH=sim python -m pytest sim/tests/test_func_model_signoff_v3_intc.py -q` — 现有 INTC 测试无回归
- [x] `PYTHONPATH=sim python -m pytest sim/tests/ sim/timing/tests/ -q` — 957 passed，8 known failed (test_engines.py, 不变)
- [x] `.omo/notepads/func-model-gap-closure/issues.md` — Issue 002 Resolved, Issue 003 Fixed, Issue 004 更新为 Documented 附 DSE 报告引用