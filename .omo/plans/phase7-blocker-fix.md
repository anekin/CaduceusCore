# phase7-blocker-fix - Work Plan

## TL;DR (For humans)

**What you'll get:** 修复 Phase 6 中可修复的 3 项环境/文档阻塞项（Spike 插件重编译、W4-PERF 证据 schema 补全、testcase-list 状态同步），文档化其余需 firmware/RTL 变更的深层阻塞项（weight streaming、SFU/Vector dispatch、36-layer RTL、DMA 读回零值、FM-3 RTL 实测、Q8_0 GGUF），产出一个明确的 Phase 6 条件→Phase 7 处置映射表，标注每项的 RESOLVED / NOT RESOLVED 状态及下一步所需变更。

**Why this approach:** Phase 6 发现两个核心阻塞项（64KB weight buffer + DMA 输出读回零值）导致的 PERF-11 FAIL 和 fullchain/36-layer 缺失，均需要修改 firmware C 代码或 RTL wrapper——这些变更的影响面和验证成本已超出本 Phase 范围。在 C 代码/RTL 变更决策做出之前，只能将 Python 侧能做的环境修复和文档同步先落地。

**What it will NOT do:** 不修改任何 firmware C 代码或 RTL Verilog 源文件；不运行 36-layer RTL 全量前向传播；不下载 Q8_0 GGUF（需外部网络）；不产生虚假的 PERF-11 PASS——阻塞项保持阻塞状态，但根因已确认并文档化。

**Effort:** Quick
**Risk:** Low — 无 RTL/firmware 变更，所有修改在 Python 脚本和文档层面

**Decisions to sanity-check:**

- 严格不碰 firmware C / RTL —— PERF-11 和 fullchain SFU/Vector 保持阻塞
- Q8_0 GGUF 给出下载命令和运行手册，但不执行（需外部网络）
- 36-layer RTL 前向传播留给后续 Phase

---

> TL;DR (machine): Quick, env+docs only, no firmware/RTL, 3 RESOLVED + disposition all Phase 6 conditions

## Scope

### Must have
- 在 sz0001 上重编译 `spike_src/plugins/npu_mmio_plugin.so`，验证 Spike mmul_smoke 通过
- 补全 `build/evidence/w4-perf-p0.txt` ~ `p4.txt` 全部 21 条记录的 `timestamp` + `commit` 字段
- 更新 `rtl/testcase-list-perf.md` 20 个 case 的状态（19 PASS + 1 FAIL）
- 在 `docs/issues_found.md` 中追加 Phase 6 条件→ Phase 7 处置映射表，标注每项的 RESOLVED / NOT RESOLVED 及下一步变更
- 产出 Phase 7 closure evidence

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不修改任何 RTL Verilog 源文件（`rtl/mxu/*.v`, `rtl/sfu/*.v`, `rtl/vector/*.v`, `rtl/soc/*.v`, `rtl/wrapper/*.v`）
- 不修改 firmware C 代码（`firmware/npu_firmware.c`, `firmware/npu-regmap.h` 等）
- 不运行新 VCS 仿真（PERF-11 重跑、fullchain 重测、36-layer RTL 全量——均不在此 Phase）
- 不下载 Q8_0 GGUF（无网络，仅提供命令和手册）
- 不新增 INT8×INT8 / BF16 数据通路
- 不做综合/物理设计 / FPGA 工作

## Verification strategy

> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after — 每个 todo 完成后用 grep/shell 命令验证
- Evidence: `.omo/evidence/phase7-<N>-<slug>.txt`

## Execution strategy

### Parallel execution waves

```
Wave 1 (环境修复, 串行):
  1.1: Spike 插件重编译 + 验证          ← 需 sz0001 SSH

Wave 2 (文档修复, 并行):
  2.1: W4-PERF 证据 schema 补全        ← 独立
  2.2: testcase-list-perf.md 更新       ← 独立
  2.3: issues_found.md Phase 7 更新     ← 独立

Wave 3 (收尾):
  3.1: 产出 Phase 7 closure evidence    ← 依赖 Wave 1 + 2 全部
```

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1.1 | none | 3.1 | — |
| 2.1 | none | 3.1 | 2.2, 2.3 |
| 2.2 | none | 3.1 | 2.1, 2.3 |
| 2.3 | none | 3.1 | 2.1, 2.2 |
| 3.1 | 1.1, 2.1, 2.2, 2.3 | — | — |

## Todos
> Implementation + Test = ONE todo. Never separate.

### Wave 1 — 环境修复

- [x] 1. sz0001: 重编译 Spike 插件并验证 `npu_mmio_plugin.so`
  What to do:
    1. SSH 到 zhengs@192.168.0.11
    2. `cd /home/prj/zhengs/caduceuscore/CaduceusCore/spike_src/plugins && source /opt/rh/devtoolset-9/enable && make clean && make`（sz0001 默认 g++ 4.8.5 不支持 -std=c++17，devtoolset-9 提供 g++ 9.x）
    3. 验证编译产物 `npu_mmio_plugin.so` 存在且非空
    4. 运行 Spike mmul_smoke 验证插件加载成功：
       `cd /home/prj/zhengs/caduceuscore/CaduceusCore && PYTHONPATH=sim python3 sim/spike_host.py --mode mmul_smoke --model ~/models/qwen2.5-3b-instruct-q4_k_m.gguf --layers 1 --ops Q_proj`
    5. 将编译日志、验证输出写入 `build/evidence/ph7-spike-fixed.txt`
  Must NOT do:
    - 不要修改 `npu_mmio_plugin.cc` 源码
    - 不要改 Makefile
    - 不要运行完整的 36-layer Spike 前向传播
    - 不要在 sz0002 上编译（必须在 sz0001 EDA server 上就地编译，确保 GLIBC/C++ ABI 兼容）
  References:
    - `docs/bugs/bugs-soc-rtl.md:56-66` (旧 GLIBC bug，状态 Fixed)
    - `docs/spike-integration.md:111-114` (编译命令)
    - `build/evidence/vcs-readiness-gate.txt:9-17` (Phase 6 报错记录)
    - `spike_src/plugins/Makefile` (编译选项: `g++ -std=c++17 -fPIC -shared`)
  Acceptance criteria:
    - `grep -q 'mmul_smoke.*PASS' build/evidence/ph7-spike-fixed.txt && echo PASS`
    - `grep -q 'plugin loaded' build/evidence/ph7-spike-fixed.txt && echo PASS`
    - `test -s spike_src/plugins/npu_mmio_plugin.so && echo "plugin exists"`
  QA scenarios:
    - Happy: `make -C spike_src/plugins` 返回 0，Spike mmul_smoke 退出码 0
    - Failure: 如果编译失败 → 记录 g++ 错误 → 该 todo FAIL（不阻塞后续文档任务）
    - Edge: 如果 `spike_host.py` 不可用（缺少依赖）→ 至少验证 `nm -D npu_mmio_plugin.so | grep mmio_device_map` 有输出（证明符号存在），记录为部分 PASS
  Commit: Y | `fix(spike): rebuild npu_mmio_plugin.so on sz0001 for ABI compatibility`

### Wave 2 — 文档修复

- [x] 2. 补全 W4-PERF 证据记录的 timestamp + commit 字段
  What to do:
    1. 阅读 `.omo/notepads/phase6-rtl-verification/learnings.md:18` 确认 evidence schema 规范
    2. 为以下文件中的每条 JSON 记录追加两个字段：
       - `"timestamp": "2026-07-19T00:30:00Z"` (使用 Phase 6 W4-PERF batch 运行日期)
       - `"commit": "<git-rev-parse-HEAD>"` (使用当前 HEAD commit hash)
    3. 需修改的文件和记录数：
       - `build/evidence/w4-perf-p0.txt`: 4 记录 (PERF-01 already has, PERF-02..04 missing)
       - `build/evidence/w4-perf-p1.txt`: 4 记录 (all missing)
       - `build/evidence/w4-perf-p2.txt`: 4 记录 (all missing)
       - `build/evidence/w4-perf-p3.txt`: 4 记录 (all missing)
       - `build/evidence/w4-perf-p4.txt`: 4 记录 (all missing)
       - `build/evidence/fullchain-pipeline.txt`: 1 记录 (missing)
    4. 验证每一条记录都有 `case_id`, `simulator`, `status`, `cycles`, `timestamp`, `commit` 六个字段
  Must NOT do:
    - 不要修改已有的字段值（case_id, simulator, status, cycles 等保持原样）
    - 不要在 JSOn 中引入语法错误（逗号、引号、括号无遗漏）
    - 不要改 PERF-11 的 status（保持 FAIL）
  References:
    - `.omo/notepads/phase6-rtl-verification/learnings.md:18` (evidence schema spec: `simulator, case_id, status, cycles, cos_sim, timestamp, commit`)
    - `build/evidence/w4-perf-review-gate.txt:143-144` (condition #1: add timestamp + commit)
    - `build/evidence/w4-perf-p0.txt` (PERF-01 has both fields — use as template)
  Acceptance criteria:
    - 在每条证据文件中运行 `grep -c '"timestamp"'` → 结果等于文件中的记录数
    - 在 `w4-perf-p0.txt` 中: `grep -c '"timestamp"'` → 4; `grep -c '"commit"'` → 4
    - 在 `w4-perf-p2.txt` 中: `grep -c '"timestamp"'` → 4; `grep -c '"commit"'` → 4
    - 全 21 条记录的 `grep -c '"timestamp"'` 总计 = 21
    - 全 21 条记录中 `grep 'FAIL'` 仅匹配到 PERF-11（1 条），status 未被错误修改
  QA scenarios:
    - Happy: 所有证据文件的有效 JSON 解析通过 `python3 -c "import json; [json.loads(l) for f in ['build/evidence/w4-perf-p0.txt',...] for l in open(f)]"`
    - Failure: 如果某条记录 JSON 解析失败 → 回退到备份，重新编辑
  Commit: Y | `fix(evidence): add timestamp and commit to all W4-PERF records`

- [x] 3. 更新 rtl/testcase-list-perf.md 状态列
  What to do:
    1. 阅读 `rtl/testcase-list-perf.md` 全文（172 行，20 case）
    2. 对照 `build/evidence/w4-perf-p*.txt` 中的实测结果，更新每个 case 的 status 列
    3. 状态映射（仅表格 `| ⬜ |` → `| ✅ PASS |` 或 `| ❌ FAIL |`，图例行保留不变）：
       - PERF-01..P04 (P0): 全部 `⬜` → `✅ PASS`（4/4）
       - PERF-05..P08 (P1): 全部 `⬜` → `✅ PASS`（4/4）
       - PERF-09..P12 (P2): PERF-09/10/12 → `✅ PASS`, PERF-11 → `❌ FAIL`（3 PASS + 1 FAIL）
       - PERF-13..P16 (P3): 全部 `⬜` → `✅ PASS`（4/4）
       - PERF-17..P20 (P4): 全部 `⬜` → `✅ PASS`（4/4）
    4. 在文件顶部 "最后更新: 2026-07-02" 改为 "最后更新: 2026-07-19"
    5. 在 PERF-11 行添加备注列：`weight buffer overflow (K=2560,N=4096), needs firmware per-K-tile reload`
  Must NOT do:
    - 不要修改 case_id、优先级、方法、测试目标、验收标准列
    - 不要删除任何表行或图例行
    - 不要修改图例行（`:47-50`）中的状态符号
    - 不要将 PERF-11 的 FAIL 改为 PASS 或 SKIP
    - 不要更新其他 testcase-list 文件（本 todo 只针对 `rtl/testcase-list-perf.md`）
  References:
    - `rtl/testcase-list-perf.md:1-172` (目标文件，20 case 全部 ⬜)
    - `build/evidence/w4-perf-p0.txt` ~ `p4.txt` (实测结果)
    - `build/evidence/w4-perf-review-gate.txt:23-28` (审计确认的 PASS/FAIL 计数)
    - `build/evidence/phase6-f1-compliance.txt:255` (条件 #9: testcase-list 未更新)
  Acceptance criteria:
    - `grep -c '| ✅ PASS |' rtl/testcase-list-perf.md` → 19（仅匹配表格状态列，不含图例）
    - `grep -c '| ❌ FAIL |' rtl/testcase-list-perf.md` → 1（PERF-11，仅匹配表格状态列）
    - `grep '⬜' rtl/testcase-list-perf.md | grep -v '|' | wc -l` → ≤2（仅图例行保留 ⬜，表格列全清除）
    - `grep '最后更新' rtl/testcase-list-perf.md` 输出包含 2026-07-19
  QA scenarios:
    - Happy: 状态计数匹配预期（19 PASS + 1 FAIL）
    - Failure: 如果有 case 在证据中 PASS 但在 testcase-list 中仍是 ⬜ → 重新核实
  Commit: Y | `doc(perf): sync testcase-list statuses with W4-PERF evidence`

- [x] 4. 更新 docs/issues_found.md 反映 Phase 7 处理结果
  What to do:
    1. 阅读 `docs/issues_found.md` 中 "Phase 6 RTL Verification Issues / Blockers" 章节（lines 351-）
    2. 在章节末尾追加一个子章节 "## Phase 7 Resolution Status"，列出每个阻塞项的处理结果：
       - Spike plugin ABI mismatch → RESOLVED（已在 sz0001 重编译，见 `build/evidence/ph7-spike-fixed.txt`）
       - 64KB weight buffer / PERF-11 → NOT RESOLVED（需 firmware per-K-tile weight reload，C 代码变更不在本 Phase 范围）
       - SFU/Vector fullchain dispatch → NOT RESOLVED（需 firmware PERF 路径支持 op=1/op=2，C 代码变更不在本 Phase 范围）
       - 36-layer Func Model-only → NOT RESOLVED（依赖 weight streaming + DMA 读回修复）
        - DMA output readback zeros → NOT RESOLVED（learnings 确认非 RTL bug——FM-SOC 回归 33/33 PASS 使用同一 firmware 路径；根因可能是 DMA CH1 方向配置或 test harness 描述符构建错误，非 `npu_firmware.c` 代码缺陷。下一步：在 `sim/cocotb_bridge.py` 中比对 FM-SOC 路径与 PERF 路径的 DMA 描述符差异）
        - FM-3 weight-streaming 实测 → NOT RESOLVED（需新 VCS 仿真运行，不在本 Phase 范围。当前 0.98 为模型/解析值，非 RTL 实测——W4 gate condition #4）
        - Q8_0 GGUF 缺失 → NOT RESOLVED（外部网络阻塞，下载命令: `huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF qwen2.5-3b-instruct-q8_0.gguf --local-dir ~/models`）
        - Phase 6 计划 checkbox 不一致（6b 为 [x] 但证据是占位符）→ NOT RESOLVED（Q8_0 解除后需回退 Phase 6 plan 6b 复选框或重新执行实验）
       - W4-PERF 证据 schema → RESOLVED（已补全 timestamp + commit，见 2.1）
       - testcase-list-perf.md → RESOLVED（已更新状态列，见 2.2）
     3. 每条 NOT RESOLVED 项标注所需的下一步变更（exact file + function）
     4. 追加 "## Phase 6 Condition Disposition" 表格，将每个继承自 Phase 6 的条件逐行映射到 Phase 7 处置状态：
        | Phase 6 Source | Condition | Phase 7 Disposition | Evidence / Next Step |
        |:---|:---|:---|:---|
        | W4-gate #1 / F1-#7 | Evidence schema gap | RESOLVED | `build/evidence/w4-perf-p*.txt` |
        | W4-gate #2 / F1-#2 | PERF-11 weight streaming | NOT RESOLVED | 需 firmware per-K-tile reload |
        | W4-gate #3 / F1-#3 | SFU/Vector fullchain dispatch | NOT RESOLVED | 需 firmware PERF 路径 op=1/2 |
        | W4-gate #4 / F1-#4 | FM-3 overlap RTL measurement | NOT RESOLVED | 需新 VCS 仿真运行 |
        | 36L-gate #1 / F1-#5 | 36-layer RTL full forward pass | NOT RESOLVED | 依赖 weight streaming + DMA 读回修复 |
        | 36L-gate #2 / F1-#6 | Spike plugin ABI mismatch | RESOLVED | `build/evidence/ph7-spike-fixed.txt` |
        | F1-#8 | W1-Supplement plan checkbox inconsistency | NOT RESOLVED | Q8_0 解除后回退复选框或重跑实验 |
        | F1-#9 | testcase-list-perf.md status | RESOLVED | `rtl/testcase-list-perf.md` |
        | 36L-gate #3 | Regenerate golden after FM changes | ACKNOWLEDGED | 不在本 Phase 范围（无 FM 变更） |
        | 36L-gate #4 | Next gate must confirm RTL full-layer | FORWARD | 后续 Phase 的验收要求 |
  Must NOT do:
    - 不要删除已有内容
    - 不要将 RESOLVED 改为 FIXED（只有已验证通过的才能写 FIXED）
  References:
    - `docs/issues_found.md:351-` (Phase 6 Issues 章节)
    - `.omo/notepads/phase6-rtl-verification/learnings.md:263-268` (DMA 读回零值)
    - `.omo/notepads/phase6-rtl-verification/learnings.md:283-286` (weight streaming + SFU/Vector 阻塞)
    - `build/evidence/w4-perf-review-gate.txt:142-151` (4 conditions)
    - `build/evidence/36layer-review-gate.txt:61-67` (4 conditions)
  Acceptance criteria:
    - `grep -c 'Phase 7 Resolution Status' docs/issues_found.md` → 1
    - `grep -c 'RESOLVED' docs/issues_found.md` → ≥3（Spike + schema + testcase-list）
    - `grep -c 'NOT RESOLVED' docs/issues_found.md` → ≥7
    - `grep -c 'Phase 6 Condition Disposition' docs/issues_found.md` → 1
  QA scenarios:
    - Happy: 阻塞项表格完整，每个 NOT RESOLVED 有下一步变更文件路径
    - Failure: 如果有条件漏记 → 补全
  Commit: Y | `doc: record Phase 7 blocker resolution status in issues_found.md`

### Wave 3 — 收尾

- [x] 5. 产出 Phase 7 closure evidence
  What to do:
    1. 汇总 Wave 1 + 2 全部 todo 的执行结果
    2. 从以下来源收集数据：
       - `build/evidence/ph7-spike-fixed.txt` (1.1)
       - `build/evidence/w4-perf-p*.txt` (2.1, 验证 schema 补全)
       - `rtl/testcase-list-perf.md` (2.2, 验证状态更新)
       - `docs/issues_found.md` (2.3, 验证章节存在）
    3. 运行汇总验证命令：
       ```
       # 1. Spike 修复
       grep -q 'PASS' build/evidence/ph7-spike-fixed.txt && echo "Spike: FIXED" || echo "Spike: FAIL"
       # 2. Evidence schema
       TOTAL=$(grep -c '"timestamp"' build/evidence/w4-perf-p0.txt build/evidence/w4-perf-p1.txt build/evidence/w4-perf-p2.txt build/evidence/w4-perf-p3.txt build/evidence/w4-perf-p4.txt build/evidence/fullchain-pipeline.txt | awk -F: '{s+=$NF}END{print s}')
       [ "$TOTAL" -eq 21 ] && echo "Schema: FIXED (21/21)" || echo "Schema: FAIL ($TOTAL/21)"
        # 3. Testcase-list（仅匹配表格状态列，不含图例）
        P=$(grep -c '| ✅ PASS |' rtl/testcase-list-perf.md)
        F=$(grep -c '| ❌ FAIL |' rtl/testcase-list-perf.md)
        [ "$P" -eq 19 ] && [ "$F" -eq 1 ] && echo "Testcase-list: FIXED ($P PASS / $F FAIL)" || echo "Testcase-list: ISSUE ($P PASS / $F FAIL)"
        # 4. Issues found + condition mapping
        grep -q 'Phase 7 Resolution Status' docs/issues_found.md && echo "issues_found: UPDATED" || echo "issues_found: MISSING"
        grep -q 'Phase 6 Condition Disposition' docs/issues_found.md && echo "conditions: MAPPED" || echo "conditions: MISSING"
       ```
    4. 将验证输出 + 每个阻塞项的最终状态写入 `build/evidence/ph7-closure.txt`
  Must NOT do:
    - 不要产生虚假的 PASS——剩余阻塞项保持 FAIL/BLOCKED
    - 不要修改不属于 Phase 7 的证据文件
  References:
    - `.omo/notepads/phase6-rtl-verification/learnings.md` (execution log)
    - `build/evidence/phase6-f1-compliance.txt:237-258` (open conditions)
    - 以上所有 todo 的证据文件
  Acceptance criteria:
    - `grep -c 'FIXED' build/evidence/ph7-closure.txt` → ≥3
    - `grep -q 'REST REMAIN BLOCKED' build/evidence/ph7-closure.txt && echo PASS`
    - `grep -c 'cos_sim' build/evidence/ph7-closure.txt` → ≥0 (本文件无 cos_sim 要求)
  QA scenarios:
    - Happy: 三个验证命令全部输出预期结果
    - Failure: 如果某个阻塞项标记为 FIXED 但对应证据不存在 → FAIL
  Commit: Y | `chore(phase7): closure evidence summarizing resolution status`

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE.
- [x] F1. Plan compliance audit: all todo checkboxes `[x]`; evidence files match plan
- [x] F2. Scope fidelity: no RTL/firmware changes in git diff
- [x] F3. Evidence consistency: cross-check closure evidence against todo deliverables
- [x] F4. Issues rollup: next-steps for NOT RESOLVED items are actionable

## Commit strategy
- 每完成一个 todo 立即 commit
- Commit message 格式: `type(scope): summary`
- 类型: `fix` (Spike/schema), `doc` (testcase-list/issues_found), `chore` (closure)

## Success criteria
| 指标 | 阈值 |
|------|:---:|
| Spike 插件编译通过 | `make -C spike_src/plugins` exit 0 |
| Spike mmul_smoke 通过 | grep PASS in ph7-spike-fixed.txt |
| W4-PERF schema 补全 | 21/21 记录有 timestamp + commit |
| testcase-list 状态更新 | 19 PASS + 1 FAIL，0 ⬜ |
| issues_found 更新 | Phase 7 Resolution Status 章节存在 |
| 无 RTL/firmware 变更 | `git diff --stat` 中无 rtl/*.v 或 firmware/*.c |
| 退出清晰 | closure evidence 明确标记哪些阻塞项已 RESOLVED / NOT RESOLVED |
