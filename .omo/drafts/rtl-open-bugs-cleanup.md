---
slug: rtl-open-bugs-cleanup
status: awaiting-start-decision
intent: clear
review_required: false
pending-action: deliver plan summary; wait for user explicit start (`/start-work`) or further review request
approach: 三个遗留 open bug 一并清理。BUG-RTL-SOC-010 → 文档侧（pcie_ep_wrapper 头注释 2 行 + 退役 conformance TB 的 PCIE DOC-DIV）；BUG-RTL-SOC-011 → RTL 侧（dma_wrapper CMD 总线只写、去掉 STATUS 读清、TB DOC-DIV 退役、README/mmio-spec 同步）；BUG-MXU-WDT-001 → wrapper AXI 层看门狗（localparam 固定阈值、sticky WRP_STATUS[1]、回 IDLE、共用 MXU irq）+ 新 cocotb 用例。回归 = conformance(doc_div→0) + wrapper + e2e mxu + FM-SOC 33；台账 SoC 15F/0O、模块级 4F/0O。
---

# Draft: rtl-open-bugs-cleanup

## Components (topology ledger)
| id | outcome | status | evidence path |
| --- | --- | --- | --- |
| 0-p0-baseline | branch rtl-open-bugs-cleanup + provenance + 7-dirty snapshot | active | .omo/evidence/task-0-rtl-open-bugs-cleanup.txt |
| 1-bug010-doc | pcie_ep_wrapper 注释对齐 + PCIE DOC-DIV 退役（doc_div 4→2） | active | task-1 |
| 2-bug011-rtl | DMA CMD 总线只写 + STATUS 读无副作用 + DMA DOC-DIV 退役（doc_div→0） | active | task-2 |
| 3-wdt001 | wrapper AXI 看门狗 + sticky 状态 + 新 cocotb 用例（7/7） | active | task-3 |
| 4-regression | conformance + wrapper + e2e mxu + FM-SOC 33 | active | task-4 |
| 5-ledger | 010/011/WDT → Fixed，SoC 15F/0O + 模块级 4F/0O + README + WDT residual | active | task-5 |
| F1-F4 | final review wave all APPROVE | active | (plan file) |

## Decisions (user: "按照你推荐的来" = all recommendations adopted, 2026-09-23)
1. **BUG-010 → 文档侧**。决定性理由：vendored `pcie_axi_master` **无顶层 `enable` 端口**（`rtl/ip/verilog-pcie/pcie_axi_master.v:58-130`；唯一 enable 是内部 `:217` 的 `.enable(1'b1)`）→ 台账原记的"wire to the IP enable input"不可实现；且 RTL 产品改动需新 exception（`docs/waivers/REMEDIATION-RTL-EXCEPTION-2026-08-28.md:20-31` 仅授权 axi_crossbar.v + npu_firmware.c）。错误文本只在 header（spec/gen/README 均无）。
2. **BUG-011 → RTL 侧**。理由：ABI（`spec/npu_abi.json:404` `"access":"wo"`）+ Func Model（`sim/models/apb_peripheral.py:288-290`）+ 文档三方一致说 CMD 只写，**只有 RTL 异类**（`dma_wrapper.v:285` 存储 + `:128/:310` 读回）；改 RTL 对齐 ABI（单一事实源）→ **零 spec/gen 改动、零 regen**。STATUS 读清 DONE（`:299-301`）一并移除。
3. **WDT-001 挂点 (c) wrapper AXI 层**。理由：controller FSM 完全自定时（`controller.v:234/268/285/306` 的 compute_timer/store_counter），**不可能因数据通路无响应而卡住**；真实无界等待在 wrapper PL_*/SO_* 的 AXI 握手（`mxu_soc_wrapper.v:388-392/414-418/420-436/639-651/676-687`）与固件 preload 自旋（`npu_firmware.c:257`）。故偏离台账原记的"controller 内计数器"方案（已在 todo 5 要求写明理由）。
4. **WDT 固定阈值**：localparam `WDT_TIMEOUT = 20'd1_000_000`（cocotb 预算公式 `M*N*K//64+20000` 对 K=N=2048 约 85k → >10× 余量）；不新增 MMIO 寄存器（避免 ABI 级联）。
5. **WDT 动作**：sticky `WRP_STATUS[1]`（注意 controller 的 status_error 是单周期脉冲 `controller.v:144`，APB 轮询会漏）+ FSM 回 IDLE 释放 AXI + OR 进 irq；sticky 清除 = 写 `WRP_CMD` bit0 时一并清。
6. **中断**：共用 INTC bit0=MXU（`intc_top.v:12,76-77`）——8 源已全分配，新增源是更大的 SoC 改动。
7. **定级归一**：WDT 以台账为准 = Major（`bugs-module-level.md:30` 标题），同步独立文件的过时 Medium（`BUG-MXU-WDT-001.md:4`）。
8. **三 bug 合一个 plan**，分 6 个 todo（todo 1/2 同改 conformance TB 故串行）。

## Key findings (cited)
- BUG-010 错处：`rtl/ip/pcie_ep_wrapper.v:258`（`[3]=enable`）、`:264`（`bit31=writable`）；RTL 真值 `:304-306`（仅存 mps[2:0]）、`:387`（读回 `{28'h0,mps,1'b0}`）、`:393`（BAR1_MASK 常量忽略写）。TB 钉真值：`apb_conformance_real_tb.sv:671`(REG_ACC)、`:728`(REG_EXP_F=0xE)、`:756`(REG_DOCDIV 0/6 位=1)、`:784`(doc_bug slave4)、`:43-53/:64`(头注释)、`:926-950`(check_docdiv，断言 REAL 行为并打标)。
- BUG-011 错处（比台账记载的广）：`rtl/ip/README.md:35-36`、`dma_wrapper.v:120-123`、`docs/func-model-mmio-spec.md:281-282`、`spec/npu_abi.json:404/411`、`gen/npu_abi.md:48/53`（生成物）、`sim/models/apb_peripheral.py:288-290`。RTL 真值 `:285`（CMD 存储，**必须保留给 FSM**）、`:128/:310`（读回）、`:209`（START 自清）、`:298-301`（STATUS 读清，删）。TB 钉真值：`:667`(ACC_WOS)、`:755`(REG_DOCDIV)、`:783`(doc_bug slave3)。
- WDT：条目 `docs/bugs/BUG-MXU-WDT-001.md`（非 stub，含 Description/Expected/Verification/Impact，但缺 Root Cause/Fix）+ `docs/bugs/bugs-module-level.md:30-49`（含 Fix 方向："watchdog counter ... sets STATUS.ERROR=1"）；severity 矛盾 Medium vs [Major]。现有 TB：`tb_controller.v` **陈旧**（force 不存在的 `u_dut.state_r`，编译不过）、`tb_controller_p1.v`（MX-10 自计 PASS）、`tb_mxu_top.v`、`tb_mxu.v`（TB 侧 1e6 轮询）；**MXU 模块 TB 无任何 Makefile/脚本 target**（ad hoc）；wrapper 侧有 `run_wrapper_mxu`（Makefile:1317-1319 → scripts/wv_run_mxu.sh，既有 6 例在 `sim/tests/wrapper/test_mxu_wrapper.py`）。**硬件看门狗先例 = 无**（grep 全仓未找到；pcie_dma_wrapper 的 vendor timeout/stall 状态输出未连接）。`timer_irq` 无片上源（`caduceus_soc_top.v:93` 输入、TB 恒 0）。

## Open assumptions / announced defaults
| assumption | default | rationale | reversible? |
| --- | --- | --- | --- |
| WDT 阈值 | 1_000_000 周期 localparam | 85k 最大合法预算的 >10× | yes |
| WDT sticky 清除 | 任意 `WRP_CMD` 写即清（不区分 bit0） | 若只在 bit0 清，ack 写会重新触发 load | yes |
| B10 的 TB 退役范围 | 仅 PCIE 相关行 | 与 todo 2 同文件故拆两步 | yes |

## Approval gate
status: plan-written-metis-in-flight
User decision 2026-09-23: "按照你推荐的来" = 全部 7 个分叉按推荐执行（见上 Decisions）→ 计划已写入 `.omo/plans/rtl-open-bugs-cleanup.md` → Metis 审查中 → 折入后交付，等用户 `/start-work` 或明示开始执行。

## Review receipts
- Metis gap analysis (2026-09-23): 1 BLOCKER + 3 HIGH + 5 MEDIUM + 4 LOW — **all folded**:
  - BLOCKER: wrapper runner 实际 5 例（`test_bug007` 走 `wv_run_bug007.sh`）→ 基线改 5→6，`WDT-TEST/REG-WRAPPER: 6-pass`。
  - HIGH: stale-simv（FM-SOC/e2e 可能复用旧二进制）→ todo 4 加前置清理 + simv sha256 入 provenance；F3 同法。
  - HIGH: WDT 覆盖不足原缺陷（stuck-COMPUTE/`STATUS.ERROR` 观测不到）→ todo 5 强制如实声明 + Residual (a)。
  - HIGH: `BUG-RTL-SOC-010/011` 在主块汇总串无条件出现（`:1235`/`:1263`）→ 退役判定改精确 grep `\[DOC-DIV\].*...` + 汇总串按 retired 口径更新。
  - MEDIUM: By-Severity 规则定死（计全部 bug、与状态无关 → 零改动）；README:39 补入同步；sticky 清除改"任意 `WRP_CMD` 写"（防 ack 重新触发 load）；新用例给出具体 stall 构造（永不应答从机）+ `ClockCycles`（禁逐周期轮询）+ 计数峰值 `$display`；1M 阈值余量由 todo 4 FM-SOC/e2e 全绿背书。
  - LOW: doorbell 90?→60；`ACC_WOS`→`ACC_WO` 不改变 per-slave 计数（仅 doc_div 降）的表述修正；M 行过滤 `^ ?M`；F4 `sim/` 白名单例外。
- High-accuracy review: NOT requested (review_required: false).
