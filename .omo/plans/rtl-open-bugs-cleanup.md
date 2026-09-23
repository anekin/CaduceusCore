# rtl-open-bugs-cleanup - Work Plan

## TL;DR (For humans)

**What you'll get:** 三个遗留 open bug 一次清干净——两个"文档与硬件说法不一致"的小缺陷（PCIe 头注释、DMA 访问类别）和一个真实的功能缺口（MXU 看门狗缺失）。做完后 SoC RTL 台账 **0 open**、模块级台账 **0 open**。

**Why this approach:** 每个 bug 都按"哪一侧才是对的"来修，不搞一刀切。PCIe 那两个字段（CTRL[3]=enable、BAR1_MASK bit31）**在硬件上根本无法实现**——vendored PCIe IP 没有顶层 enable 端口，所以改注释让它说实话。DMA 的访问类别则是**硬件与 ABI / Func Model / 文档四方不一致，只有硬件是异类**，所以改硬件去对齐 ABI（single source of truth），零 spec/gen 改动。MXU 看门狗挂在实际会无界等待的地方（wrapper 的 AXI FSM），而不是挂在完全自定时、根本不会卡住的 controller 上。

**What it will NOT do:** 不改 vendored IP；不改 ABI 结构字段（不需要 regen gen/）；不给看门狗加可配置超时寄存器（避免 ABI 级联）；不碰 `rtl/mxu/*`（看门狗只在 wrapper）；不修无关的陈旧 TB（`tb_controller.v`）；不动那 7 个并行会话 dirty 文件；不自动 push。

**Effort:** Medium
**Risk:** Low-Medium - 011 改的是 DMA wrapper 的读写语义（固件不依赖被删的行为，有 conformance TB 兜底）；WDT 加在真实有隐患的 AXI 等待路径上（有 wrapper cocotb 与新用例兜底）。

**Decisions to sanity-check:** (1) 010 改文档而非改 RTL（vendored IP 无 enable 端口 + RTL 改动需新 exception 文件）；(2) 011 改 RTL 而非改文档/ABI（对齐 ABI+Func Model+文档，零 spec/gen 改动）；(3) WDT 挂在 wrapper AXI 层、固定阈值、sticky error + 回 IDLE、共用 INTC bit0=MXU；(4) WDT 定级以台账为准（Major），同步独立文件里过时的 Medium。

Your next move: 计划已生成（Metis 审查后交付）；执行需你 `/start-work rtl-open-bugs-cleanup` 或明示开始。

---

> TL;DR (machine): Medium | Low-Medium | B10 = pcie_ep_wrapper 头注释 2 行（去掉未实现的 [3]=enable / bit31=writable）+ TB PCIE DOC-DIV 退役；B11 = dma_wrapper CMD 总线只写（读回 0，内部 dma_reg[1] 保留给 FSM）+ 去掉 STATUS 读清 DONE + README/mmio-spec 状态行同步 + TB DMA DOC-DIV 退役；WDT = mxu_soc_wrapper 加 AXI 层看门狗（localparam 固定阈值、sticky WRP_STATUS[1]、回 IDLE、OR 进 MXU irq）+ 新 cocotb 用例 + run_wrapper_mxu；回归 = conformance GREEN（doc_div==0）+ wrapper + e2e mxu + FM-SOC 33；台账 SoC 15F/0O、模块级 4F/0O；不 push。

## Scope
### Must have
0. **P0 基线**：前置门（`git log --oneline main -3` 含 `8ba7af1`）+ 分支 `rtl-open-bugs-cleanup` + provenance（HEAD / firmware hex sha / 本计划 4 个触碰文件的基线哈希 / **内联** porcelain 快照）+ 原子提交
1. **BUG-010（文档侧）**：`rtl/ip/pcie_ep_wrapper.v:258`（`[2:0]=max_payload_size, [3]=enable`）与 `:264`（`0x8000_0000 (2 GB, bit31=writable)`）两行头注释改为与 RTL 一致（明确 **[3] 未实现**、BAR1_MASK 为**只读常量**）；**退役 conformance TB 的 PCIE DOC-DIV 标签**：`rtl/tb/apb_conformance_real_tb.sv` 的 `REG_DOCDIV` PCIE 行（`:756`）index0/index6 归零、`doc_bug` mux（`:784`）的 slave-4 条目移除、头注释（`:43-53`、`:64`）与 `REG_EXP_F` 注释（`:719-720`）改为"已对齐"口径
2. **BUG-011（RTL 侧）**：`rtl/ip/dma_wrapper.v`：(a) **CMD 对总线变为只写**——读回 `32'h0`（**内部 `dma_reg[1]` 必须保留**：FSM 靠它做 START/ABORT 检测与 `:209` 的 START 自清，删掉会破坏功能）；(b) **去掉 STATUS 读清 DONE 的副作用**（删 `:298-301` 的读清分支）；(c) 更新 `:120-123` 头注释与 `:13-16` 寄存器表访问类别；(d) **同步文档**：`rtl/ip/README.md:35-36`（CMD 保持 `W` ✓；STATUS 行补"读不清除"语义）与 `docs/func-model-mmio-spec.md:281-282` STATUS 行；(e) **退役 TB 的 DMA DOC-DIV 标签**：`REG_ACC` DMA 行（`:667`）index1 `ACC_WOS`→`ACC_WO`、`REG_DOCDIV` DMA 行（`:755`）index1 归零、`doc_bug`（`:783`）slave-3 条目移除、注释（`:665-666`）更新
3. **BUG-MXU-WDT-001（wrapper 看门狗）**：`rtl/wrapper/mxu_soc_wrapper.v` 新增看门狗——监测 PL_*/SO_* FSM 的无界 AXI 等待（`:388-392`、`:414-418`、`:420-436`、`:639-651`、`:676-687`）：固定阈值用 **localparam**（建议 `WDT_TIMEOUT = 20'd1_000_000` 周期，理由：cocotb 既有预算公式 `M*N*K//64 + 20000` 对最大形状（K=N=2048）约 85k 周期，1M 有 >10× 余量；**不新增 MMIO 寄存器**）；超时后置 **sticky** `WRP_STATUS[1]`（`:271` 现仅回 `wrp_load_done`）、把 wrapper FSM 复位回 IDLE 并释放 AXI 等待、**OR 进 wrapper `irq`**（`:106`/`:317`，共用 INTC bit0=MXU）；sticky 清除 = **任意 `WRP_CMD` 写即清**（不得只在 bit0=1 时清——否则 ack 会重新触发 load）；**新增 cocotb 超时用例**（在 `sim/tests/wrapper/test_mxu_wrapper.py` 既有 6 个测试函数后新增第 7 个 `test_mxu_wrapper_watchdog_timeout`：构造**不响应 AXI 的从机**（不用 `create_axi_ram()`；自定义从机恒不置 `arready`/`rvalid`）→ 以 `ClockCycles` 等满阈值 → 断言 `WRP_STATUS[1]` 置位 + FSM 恢复 IDLE + irq 置位 + 写 `WRP_CMD` 清位）；运行器 `scripts/wv_run_mxu.sh` 是固定 `TESTS=(...)` **5 项**列表（`test_bug007_consecutive_dispatch` 属 `wv_run_bug007.sh`，不在本 runner）→ **必须把新用例名加入 `TESTS`（仅此一处脚本追加）**，`run_wrapper_mxu` 由 5→**6 例**
4. **回归（sz0001 串行；先清 stale simv）**：`run_apb_conformance_real` → **`APB_CONFORMANCE_REAL: GREEN` 且 `doc_div_cnt == 0`**、`slv_docdivs[3]==0`、`slv_docdivs[4]==0`、doorbell per-slave 60 不变；`run_wrapper_mxu`（runner **5→6 例**全绿）；`run_e2e_mxu_single` + `run_e2e_mxu_multi`；`run_fm_soc_all.sh`（33，含固件 DMA/MXU 全链；预期同基线 25-pass-8-skip，8 个 SKIP case ID 逐一核对）。**per-slave 检查计数与总数按实测记录，不硬编码**（DMA 行 `ACC_WOS`→`ACC_WO` 两分支各 2 项检查 → per-slave 计数不变、仅 `doc_div_cnt` 下降）
5. **台账**：(a) `docs/bugs/bugs-soc-rtl.md` BUG-010 与 BUG-011 → **Fixed**（各加 commit 引证 + 机制一句话 + Verification 证据路径）；By-Status Fixed 13→**15**、Open 2→**0**；By-Severity Major/Minor 行的**成员列表**更新但**计数不变**（010/011 仍 Minor，行内移除它们即可——按 :407 现状重写为只剩已修项）；Quality Metrics `13 (76.5%)`→**15 (88.2%)**、`2 (11.8%)`→**0 (0.0%)**；(b) `docs/bugs/bugs-module-level.md` BUG-MXU-WDT-001 → **Fixed**（**Fix 段改写**：实际实现是 wrapper AXI 层看门狗 + sticky WRP_STATUS[1] + 回 IDLE + 共用 MXU irq；**并说明为何偏离原记录的 controller 内计数器方案**——controller 完全自定时（`compute_timer`/`store_counter`）、不会因数据通路无响应而卡住，真实无界等待在 wrapper 的 AXI FSM；+ commit 引证）+ 定级归一（`docs/bugs/BUG-MXU-WDT-001.md:4` 的 `Medium`→`Major` 以台账为准）；统计 Fixed 3→**4**、Open 1→**0**；(c) `README.md` 状态快照：SoC RTL 行 → `17 = 15 Fixed / 1 Pending（waiver 待签）/ 1 Accepted（reconstruction-failure）/ 0 Open`；模块级行（若有）→ `4 = 4 Fixed / 0 Open`；(d) **Residual 记录**（写进 WDT-001 条目的 Residual 段，不改代码）：`rtl/tb/tb_controller.v` 陈旧（force 不存在的 `u_dut.state_r`，对现行 RTL 编译不过）；`timer_irq` 无片上源（`caduceus_soc_top.v:93` 输入、TB 恒 0）；wrapper 寄存器（0x30-0x48）不在 `spec/npu_abi.json`（只在 `firmware/npu-regmap.h`）；无硬件看门狗先例（本计划为首次）
6. F1-F4 终审

### Must NOT have (guardrails, anti-slop, scope boundaries)
- **vendored IP 零改动**：`rtl/ip/verilog-pcie/`、`rtl/ip/verilog-axi/`、`rtl/cpu/ibex/`、`llama_ref/`、`spike_src/`、`software/executorch/`
- **RTL 产品改动白名单**（仅 3 文件）：`rtl/ip/pcie_ep_wrapper.v`（**仅注释**）、`rtl/ip/dma_wrapper.v`、`rtl/wrapper/mxu_soc_wrapper.v`；**`rtl/mxu/*` 零改动**（含 `controller.v`——看门狗按 hook (c) 只在 wrapper）、`rtl/soc/*` 零改动、`firmware/` 零改动
- **spec 结构字段零改动**（offset/width/access/reset/array_size）→ **gen/ 不许出现 diff**（011 走 RTL 侧正是为此；若 `git diff -- gen/` 非空 → STOP）
- conformance TB：`check_docdiv` task 本体（`:926-950`）与 `MAX_REGS`、pcie/dma 之外的表不动；只动 010/011 相关的行
- `sim/models/apb_peripheral.py`（Func Model）零改动（本就与 ABI 一致，011 修 RTL 后四方收敛）；`sim/regmap.py` 零改动
- **7 个并行会话 dirty 文件不动不提交**：`.omo/evidence/task-0-signoff-v3-runner.txt`、`.omo/evidence/task-20-uncertainty-kpis.json`、`.omo/evidence/task-23-perf-spec-ci.txt`、`.omo/notepads/fm-e2e-qwen-cv-software-stack/learnings.md`、`.omo/notepads/phase6-rtl-verification/learnings.md`、`build/evidence/fm-cv-chain.txt`、`build/evidence/w3-4-mobilenetv3-fm.txt`；禁 `git add .`/`-A`/`commit -a`
- VCS 仅 sz0001 经 `sim/regression/soc-verification-run.sh`，**串行**；不 push；no_silent_skip（任一回归 FAIL → 签名落档 STOP）

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **tests-after**（先改注释/RTL，后以 conformance 翻绿 + 新看门狗用例 + 回归验收）
- 框架：VCS 自检 TB（`apb_conformance_real`）+ cocotb（wrapper mxu：`run_wrapper_mxu`；e2e mxu：`run_e2e_mxu_single`/`run_e2e_mxu_multi`）+ 33-case FM-SOC（`run_fm_soc_all.sh`）+ pytest（sz0001 fmpytest venv + readline shim + 15 ignores，用于 FM 域 sanity）
- Evidence: `.omo/evidence/task-{0..5}-rtl-open-bugs-cleanup.txt`（随对应 todo commit 入库）；每份 sz0001 evidence 含 **provenance 块**（git HEAD / simv 标识 + VCS 版本 / 完整命令）
- **grep-able 判定行**：`P0-SNAPSHOT:` / `B10-DOC:` / `B10-TB-RETIRED:` / `B11-RTL:` / `B11-TB-RETIRED:` / `WDT-IMPL:` / `WDT-TEST:` / `REG-CONF:` / `REG-WRAPPER:` / `REG-E2EMXU:` / `REG-FMSOC:` / `LEDGER-STATUS:` / `STATS:`

## Execution strategy
### Parallel execution waves
> 本计划多波次很窄：todo 1 与 todo 2 改同一个 TB 文件（必须串行）；sz0001 VCS 也必须串行。

- **Wave 1**：todo 0（P0，阻塞全部）
- **Wave 2**：todo 1（010 文档侧 + TB 退役）
- **Wave 3**：todo 2（011 RTL 侧 + TB 退役 + 文档同步）
- **Wave 4**：todo 3（WDT 实现 + 新用例 + runner）
- **Wave 5**：todo 4（回归，sz0001 串行）
- **Wave 6**：todo 5（台账 + README + 残留）
- **终审波**：F1-F4 并行

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 0 | —（前置门 8ba7af1） | 1,2,3,4,5 | — |
| 1 | 0 | 2（同文件串行） | — |
| 2 | 1 | 3,4 | — |
| 3 | 2 | 4 | — |
| 4 | 3 | 5 | —（sz0001 串行） |
| 5 | 4 | F1-F4 | — |
| F1-F4 | 5 | merge gate | 彼此并行 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [ ] 0. P0 基线：前置门 + 分支 + provenance + 7 行终拍断言
  What to do / Must NOT do: (1) **前置门**：`git log --oneline main -3` 必须含 `8ba7af1`——不含则 STOP。(2) `git checkout -b rtl-open-bugs-cleanup main`（当前目录，禁止 worktree）。(3) provenance：git HEAD sha + branch；`sha256sum firmware/build/npu_firmware.hex`；`sha256sum rtl/ip/pcie_ep_wrapper.v rtl/ip/dma_wrapper.v rtl/wrapper/mxu_soc_wrapper.v rtl/tb/apb_conformance_real_tb.sv`（本计划触碰文件基线）；(4) 落档 `.omo/evidence/task-0-rtl-open-bugs-cleanup.txt`，**内联** `git status --porcelain` 快照。(5) pathspec 提交 plan + draft（`git add -f`）+ evidence。(6) 终拍：`git status --porcelain | grep -cE '^ ?M'` == **7**（`??` 未跟踪项不计：树中另有 `?? .omo/tmp/` 与未跟踪 plan 草稿，属预期）。Must NOT：不动 7 dirty；不重建固件。
  Parallelization: Wave 1 | Blocked by: none（前置门 8ba7af1） | Blocks: 1,2,3,4,5
  References: 7 dirty 清单见 Scope Must-NOT；分支/提交先例 `.omo/plans/bug-009-doorbell-fix.md` 的 todo 0 与其 evidence 格式
  Acceptance criteria (agent-executable): `git branch --show-current` == `rtl-open-bugs-cleanup`；evidence 含 HEAD + hex sha + 4 文件哈希 + 内联快照；`grep -E '^ ?M'` 行数 == 7 且 ⊆ 清单（`??` 不计）；判定行 `P0-SNAPSHOT: pass`。
  QA scenarios: happy=provenance + 快照 PASS；failure=main 无 8ba7af1 或多余 M 行 → STOP 记录。Evidence `.omo/evidence/task-0-rtl-open-bugs-cleanup.txt`
  Commit: Y | chore(omo): P0 baseline — branch + provenance snapshot (rtl-open-bugs-cleanup)

- [ ] 1. BUG-010 文档侧：pcie_ep_wrapper 头注释对齐 RTL + conformance TB 的 PCIE DOC-DIV 退役
  What to do / Must NOT do: (1) `rtl/ip/pcie_ep_wrapper.v:258` 改为 RTL 实况：`0x00    RW/field  PCIE_CTRL  [2:0]=max_payload_size（[3]=enable 未实现：硬件不存储该位、读恒 0）`；`:264` 改为 `0x18    RO       PCIE_BAR1_MASK  常量 0x8000_0000（只读常量；bit31 不可写）`。（**只改注释，零行为改动**——`.enable` 不存在于 vendored IP 顶层端口，见 `rtl/ip/verilog-pcie/pcie_axi_master.v:58-130`；其唯一 enable 是内部硬连 `:217` 的 `.enable(1'b1)`。）(2) `rtl/tb/apb_conformance_real_tb.sv`：`REG_DOCDIV` PCIE 行（`:756`）index0/index6 由 1 改 0；`doc_bug` mux（`:779-790`）移除 `4: ... "BUG-RTL-SOC-010"` 分支（**保留** `3: ...011`，待 todo 2 处理）；头注释 `:43-53`（PCIE 段）与 `:64`（`BUG-RTL-SOC-010` 行）改为"已对齐"口径；`REG_EXP_F` 注释 `:719-720` 改为"bit3 NOT implemented（by design）"。(2b) **主块无条件汇总串**（`:1235` 的 `each bug-filed: BUG-RTL-SOC-010/011` 与 `:1263` 的 `doc-div [BUG-RTL-SOC-010/011]`）改为 PCIE 退役口径（`BUG-RTL-SOC-010:retired / 011:active`）——这两行是主块 display、**不是** `check_docdiv` task 本体，允许改（todo 2 再改为 both retired）。(3) 运行 `bash sim/regression/soc-verification-run.sh run_apb_conformance_real`（sz0001）→ 门 `APB_CONFORMANCE_REAL: GREEN`；**此时 `doc_div_cnt` 应为 2**（DMA 2 项仍在，PCIE 2 项已退役）。**per-slave 计数与总数按实测记录，不硬编码。**Must NOT：改 `check()`/`check_docdiv` task 本体；改 `MAX_REGS`；动 DMA/其它 slave 的行；动 RTL 行为；改 spec/gen。
  Parallelization: Wave 2 | Blocked by: 0 | Blocks: 2（同文件串行）
  References: `rtl/ip/pcie_ep_wrapper.v:258`/`:264`（待改注释）、`:304-306`（仅存 mps[2:0]）、`:387`（读回 `{28'h0,mps,1'b0}`）、`:393`（BAR1_MASK 常量）；`rtl/tb/apb_conformance_real_tb.sv:756`（REG_DOCDIV PCIE 行）、`:779-790`（doc_bug mux）、`:43-53`/`:64`（头注释）、`:718-720`（REG_EXP_F 注释）、`:926-950`（check_docdiv，读懂即可不动）；`docs/bugs/bugs-soc-rtl.md:795-840`（BUG-010 条目）
  Acceptance criteria (agent-executable): run 日志 `APB_CONFORMANCE_REAL: GREEN`；`doc_div_cnt == 2`（PCIE 归零、DMA 仍在）；`slv_docdivs[4] == 0`；**精确 grep `\[DOC-DIV\].*BUG-RTL-SOC-010` 无命中**（判定以该精确 grep 为准；主块汇总串已按 retired 口径更新，`BUG-RTL-SOC-010` 单独出现在汇总串不算失败）；判定行 `B10-DOC: aligned` + `B10-TB-RETIRED: pcie-docdiv-0`；`git diff HEAD^ HEAD --name-only` ⊆ {rtl/ip/pcie_ep_wrapper.v, rtl/tb/apb_conformance_real_tb.sv} + evidence。
  QA scenarios: happy=GREEN + doc_div=2 + PCIE docdiv=0；failure=GREEN 未达或 doc_div 非 2 → 保留完整日志签名 STOP（no_silent_skip）。Evidence `.omo/evidence/task-1-rtl-open-bugs-cleanup.txt`
  Commit: Y | docs(rtl/ip): align pcie_ep_wrapper header with RTL + retire PCIe DOC-DIV (BUG-RTL-SOC-010)

- [ ] 2. BUG-011 RTL 侧：dma_wrapper CMD 总线只写 + 去 STATUS 读清 + TB 退役 + 文档同步
  What to do / Must NOT do: (1) `rtl/ip/dma_wrapper.v` **CMD 对总线只写**：读路径对 `reg_idx==4'd1` 返回 `32'h0`（改 `:128` 的 `reg_rdata` 为按 idx 的条件式，CMD 分支给 0）；**内部 `dma_reg[1]` 保留并可写**（FSM 依赖 `dma_reg[1][0]`/`[1]` 做 START/ABORT 与 `:209` 自清——**删除存储会破坏功能，禁止**）。(2) **去掉 STATUS 读清 DONE**：删除 `:298-301` 的读清分支（`dma_reg[2][1]` 不再因读清零；DONE 由 FSM 在下次启动时清）。(3) 注释同步：`:120-123` 与 `:13-16` 表改为"CMD 总线只写（内部保留供 FSM）；STATUS 只读、读无副作用"。(4) 文档同步：`rtl/ip/README.md:35`（CMD 保持 `W` ✓ 不动）、`:36` STATUS 行补"读不清除 DONE"；`docs/func-model-mmio-spec.md:280-281`（CMD 行 :280 已为 `W` ✓）的 STATUS 行（:281）同样补注。(4b) **主块无条件汇总串**（`:1235`/`:1263`）由 todo 1 的中间口径改为 **both retired**。(5) TB 退役：`REG_ACC` DMA 行（`:667`）index1 `ACC_WOS`→`ACC_WO`；`REG_DOCDIV` DMA 行（`:755`）index1 1→0；`doc_bug` mux（`:783`）移除 `3: ... "BUG-RTL-SOC-011"`；注释 `:665-666` 更新（CMD 总线只写、STATUS 读无副作用）。(6) 运行 `run_apb_conformance_real`（sz0001）→ GREEN 且 **`doc_div_cnt == 0`**、`slv_docdivs[3]==0`；**per-slave 计数与总数按实测记录**（`ACC_WOS`→`ACC_WO` 两分支各 2 项检查 → per-slave 计数不变、仅 `doc_div_cnt` 下降）。Must NOT：改 `check()`/`check_docdiv`/`MAX_REGS`；**不改 `spec/npu_abi.json` 与 `gen/`**（若 `git diff -- gen/` 非空 → STOP）；不改 `sim/models/apb_peripheral.py`、`sim/regmap.py`；不改 vendored `verilog-axi`；不碰 pcie/其它 slave 行。
  Parallelization: Wave 3 | Blocked by: 1（同 TB 文件串行） | Blocks: 3,4
  References: `rtl/ip/dma_wrapper.v:283-286`（CMD 存储，保留）、`:128`（`reg_rdata`，改 CMD 分支）、`:310`（prdata）、`:209`（START 自清）、`:298-301`（读清，删）、`:287-289`（STATUS 写忽略）、`:120-123`/`:13-16`（注释/表）；`rtl/tb/apb_conformance_real_tb.sv:667`（REG_ACC DMA 行）、`:755`（REG_DOCDIV DMA 行）、`:783`（doc_bug slave3）、`:665-666`（注释）、`:1100-1130`（ACC_WO/ACC_WOS 分支，读懂即可不动）；`rtl/ip/README.md:35-36`；`docs/func-model-mmio-spec.md:281-282`；`docs/bugs/bugs-soc-rtl.md:844-888`（BUG-011 条目）；`sim/models/apb_peripheral.py:288-290`（Func Model 已按 ABI 声明 CMD 只写——RTL 修完即四方收敛的判据）
  Acceptance criteria (agent-executable): run 日志 `APB_CONFORMANCE_REAL: GREEN`；`doc_div_cnt == 0`；`slv_docdivs[3] == 0`；**精确 grep `\[DOC-DIV\].*BUG-RTL-SOC-01[01]` 无命中**（判定以该精确 grep 为准；两个 bug ID 允许出现在 both-retired 汇总串中）；**`git diff -- gen/` 为空**（ABI 未动）；判定行 `B11-RTL: cmd-write-only-status-no-clear` + `B11-TB-RETIRED: dma-docdiv-0`；`git diff HEAD^ HEAD --name-only` ⊆ {rtl/ip/dma_wrapper.v, rtl/tb/apb_conformance_real_tb.sv, rtl/ip/README.md, docs/func-model-mmio-spec.md} + evidence。
  QA scenarios: happy=GREEN + doc_div=0 + DMA docdiv=0 + gen/ 零 diff；failure=任一不达 → 保留日志/`git diff` 签名 STOP。Evidence `.omo/evidence/task-2-rtl-open-bugs-cleanup.txt`
  Commit: Y | fix(rtl/ip): dma_wrapper CMD write-only on the bus + drop STATUS read-clear (BUG-RTL-SOC-011)

- [ ] 3. BUG-MXU-WDT-001：wrapper AXI 层看门狗 + sticky 状态 + 新 cocotb 用例
  What to do / Must NOT do: (1) `rtl/wrapper/mxu_soc_wrapper.v` 新增看门狗：**localparam `WDT_TIMEOUT = 20'd1_000_000`**（固定阈值；理由：cocotb 预算公式 `M*N*K//64 + 20000` 对最大形状 K=N=2048 约 85k 周期，1M 有 >10× 余量——**不新增 MMIO 寄存器**）；计数器在 wrapper 处于 **PL_\*/SO_\* 非 IDLE 且等待 AXI**（`:388-392`/`:414-418`/`:420-436`/`:639-651`/`:676-687` 所覆盖的等待）时递增，FSM 前进即清零；达到阈值 → **置 sticky `wrp_wdt_timeout`**、把 wrapper FSM 复位回 IDLE（释放 AXI 等待）、**OR 进 `irq`**（`:106`/`:317`）。(2) **状态位**：`WRP_STATUS` 读回由 `{31'd0, wrp_load_done}`（`:271`）改为 **bit0=load_done, bit1=wdt_timeout(sticky)**；sticky 的清除方式定为**任意 `WRP_CMD` 写即清**（不区分 bit0——若只在 bit0 清，ack 写会重新触发 load；在注释中写明）。(3) 头注释寄存器表（`:16-21`）更新 `WRP_STATUS` 位定义。(4) **新 cocotb 用例**：`sim/tests/wrapper/test_mxu_wrapper.py` 新增第 7 个测试函数 `test_mxu_wrapper_watchdog_timeout`——**具体构造**：不用 `create_axi_ram()`（AxiRam 会自动应答），改为自定义**永不应答的 AXI 从机**（恒不置 `arready`/`rvalid`）使 wrapper 落入无界等待；等待用 `ClockCycles` 直接跑满阈值周期（**禁止逐周期 APB 轮询**——1M 周期会拖爆仿真时间）；断言 `WRP_STATUS[1]==1`、FSM 回 IDLE、`irq` 置位；再写 `WRP_CMD`（任一值）断言 sticky 清零。用例 `$display` 看门狗计数峰值供阈值余量判断。(5) **运行器**：`bash sim/regression/soc-verification-run.sh run_wrapper_mxu`（Makefile `:1317-1319` → `scripts/wv_run_mxu.sh`）；该脚本是固定 `TESTS=(...)` **5 项**列表（`test_bug007_consecutive_dispatch` 由 `wv_run_bug007.sh` 驱动，不在本 runner）→ **必须**把新用例名加入 `TESTS`（**仅此一处脚本追加**，落档说明），期望 **6 例全 PASS**。(6) Must NOT：**不改 `rtl/mxu/**`**（含 `controller.v`——本修复偏离台账原记录的"controller 内计数器"方案，理由与证据写在 todo 5 的 Fix 段：controller 完全自定时 `compute_timer`/`store_counter`（`controller.v:234/268/285/306`），不会因数据通路无响应卡住；真实无界等待在 wrapper AXI FSM）；不改 `rtl/soc/*`、firmware、spec/gen；不改 INTC（共用 bit0，`intc_top.v:12,76-77`）。
  Parallelization: Wave 4 | Blocked by: 2 | Blocks: 4
  References: `rtl/wrapper/mxu_soc_wrapper.v:106`/`:317`（irq 直通）、`:169`（wrp 寄存器区）、`:16-21`（头表）、`:271`（WRP_STATUS 读回）、`:296-329`（mxu_top 实例化）、`:349-452`（PL_\* FSM 与无界 AXI 等待 `:388-392`/`:414-418`/`:420-436`）、`:592-693`（SO_\* 与 `:639-651`/`:676-687`）；`rtl/mxu/controller.v:234/268/285/306`（自定时证据）、`:144`（status_error 为单周期脉冲——故 WDT 状态必须 sticky）；`sim/tests/wrapper/test_mxu_wrapper.py`（既有 6 例）；`sim/regression/Makefile:1317-1319`（run_wrapper_mxu）、`scripts/wv_run_mxu.sh`；`sim/cocotb_bridge.py:2370-2390`（预算公式）；`docs/bugs/BUG-MXU-WDT-001.md` + `docs/bugs/bugs-module-level.md:30-49`（条目）
  Acceptance criteria (agent-executable): `run_wrapper_mxu` 日志 **6 例全 PASS**（runner 既有 5 + 新 1；`test_bug007` 不在本 runner），新例日志含 `WRP_STATUS[1]` 置位 + 回 IDLE + irq + 清位断言；判定行 `WDT-IMPL: wrapper-axi-wdt-sticky-bit` + `WDT-TEST: 6-pass`；`git diff HEAD^ HEAD --name-only` ⊆ {rtl/wrapper/mxu_soc_wrapper.v, sim/tests/wrapper/test_mxu_wrapper.py, scripts/wv_run_mxu.sh（仅当需要加用例名）} + evidence。
  QA scenarios: happy=6/6 PASS；failure=任一 FAIL → 完整日志签名 STOP（重点：阈值是否被合法长等待误触发、sticky 清除是否生效）。1M 阈值对真实链路的余量由 todo 4 背书：若看门狗在 FM-SOC/e2e 的合法等待上误触发，那些 case 会因 wrapper 错误而 FAIL。Evidence `.omo/evidence/task-3-rtl-open-bugs-cleanup.txt`
  Commit: Y | feat(rtl/wrapper): MXU wrapper AXI watchdog — sticky timeout + FSM recovery (BUG-MXU-WDT-001)

- [ ] 4. 回归（sz0001 串行）：conformance 终态 + wrapper + e2e mxu + FM-SOC 33
  What to do / Must NOT do: **前置（必须）**：先清 stale simv——删除 FM-SOC 与 e2e-mxu 目标所用的全部 `build/ibex_full_rtl/simv_soc_*`（含 `.daidir`/`csrc`）再编译，**禁止复用任何旧 simv**（否则改过的 DMA/MXU 行为可能根本没被跑到）；重建日志 + simv 二进制 sha256 入 provenance。**然后串行**：(1) `bash sim/regression/soc-verification-run.sh run_apb_conformance_real` → `REG-CONF: GREEN-docdiv-0`（`doc_div_cnt==0`、`slv_docdivs[3]==0`、`slv_docdivs[4]==0`、doorbell per-slave **60**（与基线一致；`ACC_WOS`→`ACC_WO` 两分支各 2 项检查 → 计数不变）——**按实测记录**，预期与 todo 1/2 一致）。(2) `bash sim/regression/soc-verification-run.sh run_wrapper_mxu` → `REG-WRAPPER: 6-pass`。(3) `run_e2e_mxu_single` + `run_e2e_mxu_multi` → `REG-E2EMXU: single-pass, multi-pass`。(4) `bash sim/regression/run_fm_soc_all.sh` → `REG-FMSOC: 25-pass-8-skip-0-fail-0-timeout-total-33`（8 个 SKIP case ID 逐一核对：FM-SOC-014/015/016/017/019/021/022/023）；**重点**：011 改了 DMA 行为、WDT 改了 MXU wrapper——固件全链（DMA/MXU）必须零回归；同时这是看门狗阈值余量的实证（全绿 = 1M 阈值未被合法等待触发）。每项 evidence 含 provenance。Must NOT：并发 sz0001；改任何断言；跳过任何 case；`reg` 判定行造假。
  Parallelization: Wave 5 | Blocked by: 3 | Blocks: 5 | sz0001 串行
  References: `sim/regression/Makefile`（conformance `:194-228`、wrapper `:1317-1319`、e2e mxu `:571-596`）；`sim/regression/run_fm_soc_all.sh`；`.omo/evidence/task-3-bug-009-doorbell-fix.txt`（FM-SOC 8 SKIP ID 与基线口径）
  Acceptance criteria (agent-executable): evidence 含 stale-simv 重建证（simv 二进制 sha256 + 重建日志）/ `REG-CONF:`（GREEN + doc_div==0 + 两个 per-slave 0 + 计数实测值）/ `REG-WRAPPER: 6-pass` / `REG-E2EMXU:` 两条 pass / `REG-FMSOC: 25-pass-8-skip-0-fail-0-timeout-total-33` 判定行；8 个 SKIP ID 逐一列出。
  QA scenarios: happy=四组全绿；failure=任一 FAIL → 日志签名 STOP 回 todo 1/2/3 归因。Evidence `.omo/evidence/task-4-rtl-open-bugs-cleanup.txt`
  Commit: Y | test(cleanup): conformance doc-div-0 + wrapper + e2e mxu + FM-SOC 33 regression evidence

- [ ] 5. 台账：010/011 → Fixed（SoC 15F/0O）+ WDT-001 → Fixed（模块级 4F/0O）+ README + 残留
  What to do / Must NOT do: (1) `docs/bugs/bugs-soc-rtl.md`：BUG-010 与 BUG-011 条目 **Status → Fixed**（各加 Fix commit 引证 + 机制一句话 + Verification 证据路径；BUG-010 的 Fix 段写明"选文档侧的决策理由：vendored `pcie_axi_master` 无顶层 enable 端口（`rtl/ip/verilog-pcie/pcie_axi_master.v:58-130`，唯一 enable 是内部 `:217` 硬连）+ RTL 改动需新 exception（`docs/waivers/REMEDIATION-RTL-EXCEPTION-2026-08-28.md:20-31` 仅授权 axi_crossbar.v + npu_firmware.c）；BUG-011 的 Fix 段写明"选 RTL 侧的决策理由：ABI+Func Model+文档三方一致、仅 RTL 异类，改 RTL 对齐 ABI 零 spec/gen 改动"）；By-Status 表 Fixed 13→15、Open 2→0；**By-Severity 表规则（定死）**：严重度表计全部 bug（含已修）、与状态无关 → 010/011 均 Minor 且**本表零改动**；只动 By-Status：Fixed（`:413`）13→15、Open（`:417`）2→0（`:415`/`:416` Pending/Accepted 不可动；`:404` 是 By-Severity Major 行）；Quality Metrics `13 (76.5%)`→`15 (88.2%)`、`2 (11.8%)`→`0 (0.0%)`。(2) `docs/bugs/bugs-module-level.md`：BUG-MXU-WDT-001 **Status → Fixed**；**Fix 段改写**（实际实现：wrapper AXI 层看门狗、localparam 固定阈值、sticky `WRP_STATUS[1]`、FSM 回 IDLE、共用 MXU irq；**并记录为何偏离原记录的 controller 内计数器方案**——controller 自定时、真实无界等待在 wrapper AXI FSM（引 `controller.v:234/268/285/306` 与 `mxu_soc_wrapper.v:388-436, 639-687`）；**且必须如实声明本修复不覆盖台账原描述的"卡在 COMPUTE 时 `STATUS.ERROR` 置位"失效模式**——wrapper-AXI 看门狗观测不到自定时的 controller，MX-10 规划的 `STATUS.ERROR` 未实现）；+ commit 引证 + Verification 证据路径 + **Residual 五条**：(a) 原失效模式（stuck-COMPUTE / `STATUS.ERROR`）未被本修复仪器化；(b) `rtl/tb/tb_controller.v` 陈旧（force 不存在的 `u_dut.state_r`，对现行 RTL 编译不过）；(c) `timer_irq` 无片上源（`caduceus_soc_top.v:93` 输入、TB 恒 0）；(d) wrapper 寄存器（0x30-0x48）不在 `spec/npu_abi.json`（只在 `firmware/npu-regmap.h`）；(e) 本看门狗是仓库首例硬件看门狗、无先例可对照）；统计 Fixed 3→4、Open 1→0；`docs/bugs/BUG-MXU-WDT-001.md:4` 的 `Medium`→`Major`（定级归一，以台账为准）。(3) `README.md` 状态快照**两处**：`:26` SoC RTL 行 → `17 = 15 Fixed / 1 Pending（waiver 待签）/ 1 Accepted（reconstruction-failure）/ 0 Open`；`:39` 模块级行 → `4 = 4 Fixed / 0 Open`。(4) 判定行 `LEDGER-STATUS: Fixed` / `STATS: 15-fixed-0-open-socrtl-4-fixed-0-open-module`。Must NOT：无 todo 4 证据不 claim Fixed；不动 :415/:416 与 BUG-002/007/009/012 字段；不动 docs/ 其它文件；不改代码。
  Parallelization: Wave 6 | Blocked by: 4 | Blocks: F1-F4
  References: `docs/bugs/bugs-soc-rtl.md:795-888`（010/011 条目）、`:405-447`（三表）；`docs/bugs/bugs-module-level.md:30-49`（WDT 条目）、`:128-134`（统计）；`docs/bugs/BUG-MXU-WDT-001.md`（独立文件）；`README.md` 状态快照行；todo 1/2/3 的 Commit: 行与 evidence
  Acceptance criteria (agent-executable): `grep -n "LEDGER-STATUS: Fixed" .omo/evidence/task-5-rtl-open-bugs-cleanup.txt` ≥1；两份台账含 Status Fixed + commit 引证 + （WDT）Residual 五条 + 不覆盖原失效模式声明；`grep -n "Bugs closed Fixed | 15" docs/bugs/bugs-soc-rtl.md` ≥1 且 `grep -n "Open / under investigation | 0" docs/bugs/bugs-soc-rtl.md` ≥1；README `:26` 含 `15 Fixed`/`0 Open` 且 `:39` 含 `4 Fixed`/`0 Open`（`grep -n "3 Fixed / 1 Open" README.md` 无命中）；`git diff main..HEAD --name-only -- docs/ README.md` ⊆ {docs/bugs/bugs-soc-rtl.md, docs/bugs/bugs-module-level.md, docs/bugs/BUG-MXU-WDT-001.md, README.md}。
  QA scenarios: happy=判定行 + 双台账五要素 + 计数一致；failure=计数遗漏（旧值 grep 仍命中）→ 补齐复跑。Evidence `.omo/evidence/task-5-rtl-open-bugs-cleanup.txt`
  Commit: Y | docs(bugs): BUG-RTL-SOC-010/011 + BUG-MXU-WDT-001 Fixed — doc align, DMA bus semantics, MXU watchdog

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit - todos 0-5 逐条：evidence 存在、acceptance 断言复跑（按预声明 message 定位各 commit）、判定行齐全（P0-SNAPSHOT/B10-DOC/B10-TB-RETIRED/B11-RTL/B11-TB-RETIRED/WDT-IMPL/WDT-TEST/REG-CONF/REG-WRAPPER/REG-E2EMXU/REG-FMSOC/LEDGER-STATUS/STATS）、无 silent skip（FM-SOC 8 SKIP 按 case ID）；wrapper 计数按 **6-pass** 口径核对（runner 既有 5 + 新 1；`test_bug007` 不在本 runner）；010/011 退役以精确 grep `\[DOC-DIV\].*BUG-RTL-SOC-01[01]` 判定；stale-simv 重建证据（simv sha256）在案
- [ ] F2. Code quality review - `rtl/ip/pcie_ep_wrapper.v` **仅注释 hunk**（零行为）；`rtl/ip/dma_wrapper.v` 恰好两处行为改动（CMD 读回 0、删读清）+ 注释/文档同步，且**内部 `dma_reg[1]` 仍在**（FSM 依赖）；`rtl/wrapper/mxu_soc_wrapper.v` 仅新增看门狗（计数器/sticky 位/回 IDLE/irq OR）+ 头表更新，**`rtl/mxu/**` 零改动**；conformance TB 仅 010/011 相关行变动、`check_docdiv` 本体与 `MAX_REGS` 未动、pcie/dma 之外的行未动；**`git diff -- gen/` 为空**；台账不 overclaim（Fixed 有 commit + 证据；WDT 的 Fix 段如实记录方案偏离与四条 residual）
- [ ] F3. Real manual QA - fresh 独立复跑（sz0001）：`run_apb_conformance_real`（GREEN + doc_div==0 + 两个 per-slave 0）、`run_wrapper_mxu`（6/6）、`run_e2e_mxu_multi`；输出对照 task-4 evidence。**前置条件：sz0001 可达（不可达 ⇒ F3 判 BLOCK 上报，不得降级 APPROVE）；且须先清 stale simv 重建（与 todo 4 同法）后再复跑。**
- [ ] F4. Scope fidelity - `git diff $(git merge-base main HEAD) HEAD --name-only` ⊆ {rtl/ip/pcie_ep_wrapper.v, rtl/ip/dma_wrapper.v, rtl/wrapper/mxu_soc_wrapper.v, rtl/tb/apb_conformance_real_tb.sv, rtl/ip/README.md, docs/func-model-mmio-spec.md, sim/tests/wrapper/test_mxu_wrapper.py, scripts/wv_run_mxu.sh（仅当加用例名）, docs/bugs/bugs-soc-rtl.md, docs/bugs/bugs-module-level.md, docs/bugs/BUG-MXU-WDT-001.md, README.md, .omo/*}；**vendored / `rtl/mxu/` / `rtl/soc/` / firmware / spec / gen 零命中；`sim/` 除白名单 `sim/tests/wrapper/test_mxu_wrapper.py` 外零命中**；qwen_blk0 零净 diff；每提交不含 7 dirty 文件；未 push；单 worktree

## Commit strategy
- 一个 todo 一个原子 commit（type: chore/docs/fix/feat/test），message 预声明于各 todo `Commit:` 行；evidence 随对应 todo 入库（`.omo/evidence/task-N-rtl-open-bugs-cleanup.txt`，`git add -f`）
- staging 纪律：只允许逐 todo 显式路径 `git add`；**禁止 `git add .`/`-A`/`commit -a`**
- sz0001 VCS 串行（todo 1→2→3→4）
- F1-F4 全 APPROVE + 用户 explicit okay 后 `--no-ff` merge 回 main；**不自动 push**

## Success criteria
1. `B10-DOC: aligned` + `B10-TB-RETIRED: pcie-docdiv-0` —— PCIe 头注释与 RTL 一致、PCIE DOC-DIV 归零（doc_div 由 4→2）
2. `B11-RTL: cmd-write-only-status-no-clear` + `B11-TB-RETIRED: dma-docdiv-0` —— DMA CMD 总线只写（内部保留）、STATUS 读无副作用、DMA DOC-DIV 归零（**doc_div 终态 == 0**）、`gen/` 零 diff
3. `WDT-IMPL: wrapper-axi-wdt-sticky-bit` + `WDT-TEST: 6-pass` —— wrapper AXI 层看门狗（固定阈值、sticky WRP_STATUS[1]、回 IDLE、共用 MXU irq），新增用例通过且 `rtl/mxu/**` 零改动
4. 回归全 PASS：`REG-CONF`（GREEN + doc_div==0）、`REG-WRAPPER: 6-pass`、`REG-E2EMXU`、`REG-FMSOC: 25-pass-8-skip-0-fail-0-timeout-total-33`
5. 台账：SoC RTL **15 Fixed / 0 Open**（010/011 Fixed）+ 模块级 **4 Fixed / 0 Open**（WDT Fixed，定级归一 Major，Fix 段含方案偏离 + 不覆盖原失效模式声明 + 5 条 residual）+ README 两处快照同步；`LEDGER-STATUS: Fixed` / `STATS: 15-fixed-0-open-socrtl-4-fixed-0-open-module` 落档
6. F1-F4 全 APPROVE + 用户 okay → `--no-ff` merge 回 main；变更集 ⊆ 白名单；vendored/`rtl/mxu/`/`rtl/soc/`/firmware/spec/gen 零改动；未 push
