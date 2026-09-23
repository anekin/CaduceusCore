# decisions

## [2026-09-23] session start

## [2026-09-23] F 波（F1-F4）——全部 APPROVE；完成与合并阻塞于用户签署

**评审结论**（证据 `.omo/evidence/f{1..4}-rtl-open-bugs-cleanup.txt`）
- **F1 合规审计：APPROVE**，`DEVIATION-CONCUR: yes`。6 个 commit message 与计划 `Commit:` 行逐字一致；13 个判定行齐全；台账算术复算通过（15/17=88.2%、0/17=0.0%）；By-Severity 未动；无 overclaim；无 silent skip（8 个 FM-SOC SKIP 逐 ID 有因）。独立重derive 了偏离：WDT 前后失败签名逐字节相同；HEAD 控制跑证明原 "5/5 基线"本就虚假（`ApbMaster` 缺 `clk=` 崩溃 + runner 判定假阳性）。
- **F2 代码质量：APPROVE**。PCIe 文件非注释改动 = 0；DMA wrapper 恰好 2 处行为改动且 `dma_reg[1]` 存储/FSM 消费链完整；看门狗纯增量、`rtl/mxu|soc|intc|sfu|vector` 零改动；`check()`/`check_docdiv`/`MAX_REGS` 未动、删除的 mux 臂不可达；`gen/ spec/` diff 空。唯一 MEDIUM：看门狗"无误触发"注释论证写错（计数器是 per-phase 而非 per-wait-stretch），但 10× 余量论证 + FM-SOC 25 case 全绿仍成立 → 注释措辞问题，**未修**。
- **F3 真实手工 QA：APPROVE**。sz0001 强制清编译（10 路径 ABSENT 验证）后独立复跑：conformance `GREEN 263/263/0 doc-div`（$finish 1291）；wrapper 逐用例 **4 PASS / 2 FAIL 签名逐字节一致 + 看门狗用例 PASS**；e2e mxu multi **PASS（923 cycles）**。三个 simv 均为本次新编译（sha256 在案）。
- **F4 范围保真：APPROVE**。20/20 变更文件 ⊆ 白名单、**0 越界**；vendored / `rtl/mxu` / `rtl/soc` / `rtl/intc` / `rtl/sfu` / `rtl/vector` / firmware / spec / gen 零命中；每 commit 路径集与声明一致；**0/7 受保护文件入库**；未 push；单 worktree。

**Orchestrator 依 F1 建议所做的注记修正（bookkeeping，非产品改动）**
- 计划 todo 3 的 DEVIATION 记录保留；把仍带旧口径的三处（F1 指令行、Success criteria #3/#4 的 `6-pass`）改为实测口径 `4-pass-2-preexisting-fail` 并指向 DEVIATION；Scope 中与"By-Severity 零改动"规则冲突的早期措辞已修正。

**阻塞（用户-only 决策）→ 计划 F1-F4 已标记 `- [~]`，不视为完成**
1. 批准本 wave 并 `--no-ff` 合并回 main（**仍不 push**）——合并前需还原被 runner 改写的两个 tracked 生成物（`build/evidence/wrap-mxu-regression.txt`、`results.xml`）。
2. 两个既有缺陷（DEFECT-WRP-1 store-out drain 早于 `STATUS.DONE`，firmware 可见；DEFECT-WRP-2 accumulate 模式 `m_axi_wdata` X）与三项过程债（Makefile target 缺 `cd $(REPO_ROOT)`；runner 判定 grep 假阳性且真失败仍 exit 0；Makefile 只跟踪 flist 不跟踪 RTL → stale-binary 假 PASS）是否立案 / 出修复计划。
3. F2 的非阻塞意见（注释论证措辞 + 4 条 LOW：行号混版引用、sticky 清除漏 `penable`、恢复分支单周期窗口、测试探测 `except` 过宽）是否修。
4. 未推送清单处理 A/B/C/D。
