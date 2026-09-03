---
slug: bug-012-fm-audit
status: review-required
intent: clear
review_required: true
pending-action: dual high-accuracy review (momus + independent oracle) → fold → deliver
approach: 两阶段重构（用户 2026-09-03 指令）——Phase A（FM 先行，本计划）：FM 全量基线 + 定向 bug hunt（用 BUG-012 教训反向攻击 FM 自身）+ G4 契约测试 + FM 修复处置；Phase B（RTL 随后）：既有 bug-012-fix 计划 + G3 门禁挂载折入 + Phase A 全绿前置门。
---

# Draft: bug-012-fm-audit

## Components (topology ledger)
<!-- id | outcome (one line) | status: active|deferred | evidence path -->
- A1 FM 基线 | pytest 210 + verify_ops + golden smoke/sfu | active | task-1
- A2 FM bug hunt | 窄 N 矩阵 + pack/tile/scale/acc 攻击 + 独立 numpy oracle | active | task-2/3
- A3 G4 契约 | DIM1=真实 N + dense 输出契约测试 | active | task-4
- A4 FM 修复处置 | bug → fix → ledger → 重跑 | deferred (仅发现时) | task-5
- B0 RTL 计划修订 | G3 门禁挂载 + Phase A gate | deferred (A 完成后) | bug-012-fix.md

## Open assumptions (announced defaults)
<!-- assumption | adopted default | rationale | reversible? -->
- FM bug hunt 攻击面 | 窄 N 矩阵（N∈{2,10,12,20,33,40,64}×M∈{1,4,32,65}×K∈{1,64,128,129}）+ pack 零填充 + tile ceil + scale/acc + 零维 | 直接映射 BUG-012 教训（非 pow2 N / 非 64 倍数 M） | 是
- oracle 独立性 | 独立 numpy 直算（不 import sim.models/sim.engine，符合 oracle 独立性反模式） | 项目反模式明文 | 是
- FM 发现 bug 的处置 | 本计划内修复（golden 修复优先）→ 台账 bugs-soc-func-model.md → 重跑基线；若修复面大 → STOP 上报 | 常规 | 是
- G3 挂载位置 | run_fm_soc_all.sh 尾部追加 run_e2e_attn_score + run_e2e_attn_score_layout（非 run_ibex_full_rtl.sh CASES——后者是 FM-SOC case 族，attn 是独立 cocotb target） | 调用形态 | 是
- timing 域 | 基线含 sim/timing/tests（60 例，pytest 210 口径的一部分） | README 口径 | 是

## Findings (cited - path:lines)
1. FM 无 store-out 布局模型（sim/models 零 wrp_n/row_bytes/store_out）→ BUG-012 结构性不可见于 FM。
2. 混合模式 FM-SOC-010（USE_RTL_MXU，run_dma_mixed.sh:45）MMUL 全 N=1（rtl_soc_runner.py:1935/1969/1976）——pow2 安全点，逃过。
3. 全 RTL 33-case 套件（run_ibex_full_rtl.sh:37）同 N=1 覆盖；attn_score/blk0 不在门禁（task-2-bug-012-root-cause.txt §3a：run_fm_soc_all.sh grep 0 命中）。
4. FM tests 已写真实 N（test_soc_fm.py:161/616/636；test_mmio_perf_events.py:70 DIM1=2）——惯例而非契约。
5. FM 测试面：sim/tests/ 100+ 文件含 test_golden_mxu_edges.py/test_golden_mxu_quant.py/test_golden_corruption.py/test_tile_scheduler.py/test_soc_fm.py(N_odd :2384, N_big :2352, DIM1=0 :2269)。
6. verify_ops_func_model.py 覆盖 op05/op07/op10（:208-264）。
7. FM 台账 docs/bugs/bugs-soc-func-model.md 全 Fixed 零 waiver（BUG-SOC-FM-001/002/003…）。
8. 本地可跑命令：PYTHONPATH=sim python -m pytest sim/tests/ sim/timing/tests/ -q（预期 210）；PYTHONPATH=sim python3 scripts/verify_ops_func_model.py。

## Decisions (with rationale)
- 节奏：FM 先行、RTL 随后（用户拍板）。RTL 计划（bug-012-fix，已完成三轮双评审 APPROVE）在 Phase A 全绿前不启动。
- FM bug hunt 的判据：GoldenMXU 自比对 + 独立 numpy oracle 交叉；任何 mismatch → 定位 FM 层 bug（不是 RTL 域）→ A4 处置。
- G4 契约测试在 FM 侧落地（ABI DIM1=N + dense 契约），RTL 侧由 G3 门禁兜底。

## Scope IN
- sim/tests/ 新增 ≤3 个测试文件（fm_audit_narrow_n.py / fm_abi_contract.py / fm_pack_edges.py 或等价命名）
- .omo/*（plan/draft/evidence/notepad）
- （A4 触发时）sim/golden_executor.py 或对应 FM 模型文件 + docs/bugs/bugs-soc-func-model.md
- （B0）sim/regression/run_fm_soc_all.sh + .omo/plans/bug-012-fix.md

## Scope OUT (Must NOT have)
- rtl/、firmware/、gen/、config/、vendored、scripts/ 产品代码零改动（A 阶段）
- 不跑 sz0001/VCS（A 阶段全本地）
- 不改既有测试以图通过；不并案
- 7 个并行会话 dirty 文件不动（同一清单）
- 不 push；不 git add -A；每 todo 一原子 commit

## Open questions
- 无（探索穷尽；默认项已按可逆默认记录）

## Approval gate
status: approved → plan written (.omo/plans/bug-012-fm-audit.md) + bug-012-fix.md 修订（G3 + Phase A 前置门）
<!-- 用户 2026-09-03 回复 "approve" → 已授权写 plan；Metis 折叠完成。 -->

## Metis gap analysis (mandatory, folded)
- 会话：ses_f99137c45ffewaJk7KFQYtWvxH，2 BLOCKER + 6 MAJOR + 6 MINOR，全部折入：
- BLOCKER(1) todo 0 快照漏 `?? .omo/plans/bug-012-fix.md`（现存 untracked → 会自触发 STOP）→ 8 行口径。
- BLOCKER(2) todo 3(a) M=65 超 pack 单 tile 契约（IndexError）→ (a) 限 M∈{4,64} 并钉 K∈{64,129}，M=65 走 (b)。
- MAJOR(3) padding 正则漏 diagnose_data_layout.py:151 → 新正则 + 命中集恰好等于两文件。
- MAJOR(4) GoldenMXU 入口/pack 陷阱（docstring vs 实现矛盾）→ 显式 matmul_int32(act, pack_int4(wgt))。
- MAJOR(5) Wave2 pytest 收集竞态 → todo 1 全量跑先于 2/3 建文件。
- MAJOR(6) 失败路径 4/5 顺序矛盾 → todo 4 失败路径 Blocked by 5 + 正交断言 + 修复后重跑。
- MAJOR(7) todo 4(a) 读写回环空洞 → 加计算级断言（DIM1=33 驱动 CMD，输出 == matmul_int32(M,K,33)）。
- MAJOR(8) accumulate harness 未定 → MMIOBridge 显式 harness（两 K=64 命令 + CTRL bit[2]，bit-exact）。
- MINOR(9-14)：verify_ops 5 行、grep -c || true、行号漂移（:184-205/:208-239/:105-117）、取值域含 +7/+127、210 triage 更新条款、依赖矩阵简化。

## High-accuracy dual review — round 1（fm-audit 计划）
- Momus（原生）ses_f98bc452fffev4W2t0A1ToDAxI：**APPROVE**（0 BLOCKER；1 MAJOR：bug-012-fix G3 exec 死代码；3 MINOR：grep -c || true 输出、F3 四→五判定行、bug-012-fix todo 6 stale 210）。fm-audit 计划本体一致（引用/矩阵/判定行全核）。
- Oracle（独立）ses_f98bc01e8ffeAR2XUtc2FiMaPr：**APPROVE（scoped）**——fm-audit 计划技术主张全过（112 组合 oracle 等价、accumulate 线性、正则实测恰好两命中、_read_scale_hex (2,2)、契约测试健全）；**1 BLOCKER 在耦合的 Phase B**：G3 挂载 exec 死代码 + 6 MINOR（Scope #3 stale 措辞、F3 四判定行、todo 3c harness 三缺、todo 4a staging 未钉、todo 2/A4 docstring 修复未分配、bug-012-fix todo 6 count）。
- 全部折入：fm-audit（Scope#3 措辞、grep `|| echo 0`、F3 五判定行、todo 3c sram 预分配+O_ADDR 同址+K 半段偏移、todo 4a staging 钉死+golden_executor:96 注释勘正入 F4 白名单、success#6 解锁条件含 G3 修订）；bug-012-fix（todo 7(5) exec→普通调用+RC 传播+拓扑断言 grep、Scope#6/todo 6 count 去 210）。
- 确认轮（round 2）：Momus ses_f98aecd58ffeER3eEz1SAurMgi **REJECT**（1 BLOCKER：`|| echo 0` 是坏 bash——实测 `grep -c` 零命中输出 "0" 且 exit 1，`|| echo 0` 拼出 "0\n0" 使 test 报错，正确形式是 `|| true`）→ 已折入（todo 2/3 改回 `|| true` 显式命令）；Oracle ses_f98aeaa6effevXvtNJEV90Civz **APPROVE**（2 MINOR：bug-012-fix todo 6 QA/Commit 残留 "210"、todo 3c sram 措辞）→ 已折入（QA 改基线表述 + Commit 去 210 + 措辞精化）。Oracle 另复核 W_ADDR2=w_off+32*N 对奇 N 字节对齐成立（pack_int4 连续 nibble 打包，64N 恒偶）。
- 确认轮（round 3，终审）：Momus ses_f98a71656ffeTdPdL63pY7m0Z6 **APPROVE**（0 findings）；Oracle ses_f98a6fd24ffeGAF0J7YI1ZAUPr **APPROVE**（2 MINOR cosmetic：mmio_bridge 行号 292-293、todo 6 标题去 "210"——均已折入）。Oracle 实证 `|| true` 输出为 "0" 非空（round-1 Momus 主张不成立），并在记录中明示。
- **结论：高精度评审通过（3 轮，双 receipt 最终均无条件 APPROVE）。** fm-audit 与 bug-012-fix（含 G3 修订）两计划均已就绪。
