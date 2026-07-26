# CaduceusCore Agentic IC 验证方案

> 版本：v0.2（复盘前草案）  
> 日期：2026-07-18  
> 状态：**冻结，暂不执行**  
> 启动条件：当前既定验证全部结束并完成复盘后，再调整和批准本方案  

## 1. 方案定位

本方案定义 CaduceusCore 后续 Agentic IC Verification 的目标架构、方法、证据模型和门禁。当前只用于统一复盘框架，不替代 Phase 6，不授权启动新任务，也不授权修改 RTL、Func Model、testbench、firmware、验收阈值或 waiver。

推荐方法为：

> 以 Func Model 和结构化场景 IR 为可执行规格；以 cocotb/Verilator、Formal、差分验证、性质测试和 coverage-guided fuzzing 作为 Agent 内循环；以 VCS/UVM/VIP 作为 SoC 集成与签核后端；以统一 Evidence/Coverage Store、mutation 和独立 Review Gate 保证可信度。

Agent 负责提出、执行、分析和迭代；仿真器、形式验证器、断言、独立参考模型和等价检查负责判真。Agent 不能自我认证。

## 2. 与当前验证的关系

当前验证继续完成：Func timing/streaming、W3-RTL、PERF-01～20、full-chain、Spike-first/Ibex-final、36 层 checkpoint、regression、Bug 和 evidence 收口。

本方案后续解决：

- 需求、测试、属性、coverage、Bug、证据统一追踪；
- Caduceus 自有 SVA/Formal 和 coverage closure；
- fixed-seed random 向 coverage-guided exploration 演进；
- 多工具 Evidence/Coverage Store；
- 验证环境 mutation/signoff；
- UVM/VIP、PSS/场景 IR、formal equivalence；
- Agent 自身评价和权限控制；
- 区分 MEASURED、CHARACTERIZED、CALIBRATED 和 SIGNED_OFF。

## 3. 复盘启动门禁

本方案执行前必须完成当前验证复盘。

### 3.1 前置条件

- 当前验证阶段已关闭；
- 日志、JSON、波形、配置和工具版本已冻结；
- regression baseline、Bug、未执行项、超时、环境失败和 waiver 已汇总；
- Func、RTL、Spike、Ibex 和性能结果已对齐；
- testcase 状态与 evidence 无未解释冲突。

### 3.2 复盘必须回答

1. 人工和 VCS 时间分别花在哪里？
2. 每类 Bug 由什么方法发现，什么方法漏掉？
3. 哪些 Bug 可被 Formal、fuzzing、property-based 或 mutation 更早发现？
4. Func/RTL 两套 build 是否发生偏差？
5. 是否出现摘要 PASS 但原始日志失败、零测试或 evidence 缺失？
6. Golden、scoreboard、assertion 和 testbench 自身有哪些错误？
7. 哪些 coverage hole 与产品风险相关？
8. Spike-first/Ibex-final 实际节省多少时间？
9. engine、wrapper、SoC、workload 各层性能模型误差是多少？
10. CV、36 层和 full-chain 结果分别能支持和不能支持什么结论？

### 3.3 复盘输出

- `current-verification-retrospective.md`；
- `verification-gap-matrix.json`；
- `evidence-quality-report.json`；
- `agentic-pilot-selection.md`；
- 本方案 v0.3 执行版。

## 4. 分层方法架构

| 对象 | Agent 快速内循环 | 集成/签核 | Oracle |
|---|---|---|---|
| MXU/SFU/Vector | cocotb + Verilator + property-based | VCS module/SoC | Func Model、数值规范 |
| DMA/FIFO/doorbell/arbiter | SVA + Formal | VCS directed/random | 不变量、活性属性 |
| command/descriptor | coverage-guided fuzzing | firmware/VCS regression | command model |
| AXI/PCIe/DDR | assertion + cocotb smoke | UVM/VIP | protocol checker |
| 多 engine 场景 | 项目 IR/PSS | SoC UVM/VCS | transaction scoreboard |
| RTL 修复/重构 | targeted regression | formal equivalence | 原 RTL/参考实现 |
| 验证环境 | mutation + anti-vacuous | 独立 review | mutation 检出率 |

UVM 不被替代，继续承担成熟协议 VIP、RAL、复杂 SoC 场景和最终回归。Agent 日常内循环通过稳定 CLI/API、项目 IR 或 PSS 调用后端，不每次重建 UVM environment。

复盘后先评估轻量 `scenario.yaml/json`，再决定是否采用正式 PSS。同一场景应能生成 Func test、cocotb test、fuzz seed、firmware command 和 UVM sequence。

## 5. 验证对象与证据

```text
REQ-* 需求
  ├── MODEL-* Golden/架构模型
  ├── TEST-* 定向、随机、差分测试
  ├── PROP-* SVA/Formal 属性
  ├── COV-* 需求、功能、FSM、代码覆盖点
  ├── PERF-* 周期、带宽、延迟、吞吐契约
  ├── BUG-* 失败、根因、修复
  └── EVID-* 日志、波形、JSON、版本、哈希
```

每次执行保存 requirement/test ID、Git SHA、dirty diff、工具和 firmware 版本、seed、配置/输入/Golden 哈希、退出码、metrics、coverage delta、反例、波形、日志和 Bug ID。

工具状态固定为：`PASS | FAIL | UNKNOWN | TIMEOUT | INFRA_ERROR | NOT_RUN`。禁止把 UNKNOWN、TIMEOUT、INFRA_ERROR 或零测试折算成 PASS。

```text
TODO → READY → RUNNING → EVIDENCE_READY → REVIEW
                                      ├→ PASS
                                      ├→ FAIL → FIX → RE-RUN
                                      ├→ CHARACTERIZED → CALIBRATION_PENDING
                                      └→ WAIVED（负责人、理由、到期日）
```

执行 Agent 只能提交 `EVIDENCE_READY`，最终状态由独立 reviewer 决定。

## 6. Agent 角色和权限

角色包括规格/计划、测试、执行、coverage、归因、性能、review Agent 和人类负责人。

Agent 不得自行：

- 降低 coverage、性能或 signoff 阈值；
- 删除、跳过或弱化失败测试；
- 扩大 Formal assumption 以隐藏反例；
- 把 UNKNOWN/TIMEOUT/INFRA_ERROR 改成 PASS；
- 批准 waiver 或修改 Signoff Gate；
- 在受保护分支提交未经 review 的 RTL 修复；
- 仅根据自己生成的 Golden 判断 RTL 正确。

## 7. 复盘后候选实施阶段

以下阶段当前均未批准执行。

### AIV-0：基础设施标准化

建立 ID、统一 runner、JSON/JUnit、artifact manifest、Evidence/Coverage Store、evidence verifier 和 replay。Coverage schema 兼容 UCIS 思路。

退出条件：同一 testcase 可由人类和 Agent 重放，结果一致。

### AIV-1：两个 Block 试点

**数据通路试点：**候选 MXU quantization 或 SFU normalization；使用 Func oracle、cocotb/Verilator、property/metamorphic、boundary 和 mutation。

**控制试点：**候选 DMA descriptor、doorbell/ring 或 FIFO/arbiter；使用 SVA/Formal、reachability、vacuity、counterexample replay 和 assertion mutation。

退出条件：Agent 能将失败缩减为可重放最小用例；人工确认未把 Oracle 错误误判为 RTL Bug。

### AIV-2：Coverage-Guided Fuzzing

候选对象：descriptor、command ring、shape/stride/padding、DMA backpressure、多 engine 顺序、reset/abort/IRQ、SRAM conflict 和 Weight Cache 状态。

```text
seed corpus → 结构化变异 → Verilator/VCS
→ coverage feedback → Golden/assertion 判错
→ testcase minimization → regression corpus
```

Formal cover trace 可转为难达状态的 fuzz seed。

退出条件：相对现有 random regression 发现新状态、coverage hole 或真实 Bug，且全部可重放。

### AIV-3：场景 IR 与 UVM/PSS 集成

将复盘确认的跨 engine 场景建模为项目 IR，由适配器生成/调用 Func、cocotb、firmware 和 UVM sequence；保留现有 VIP；根据试点收益决定是否采用正式 PSS。

退出条件：Block 反例能升级为 subsystem/SoC regression，且不重复实现现有资产。

### AIV-4：Formal Equivalence 与受控自治

Agent 修复或重构 RTL 后执行适用的 equivalence、代码 review 和完整回归；Agent 可自动分析 coverage hole、生成 seed、聚类失败和起草 Bug，但不能批准 waiver/signoff。

### AIV-5：持续签核

| 层级 | 内容 |
|---|---|
| R0 | lint、Golden/Func、compile smoke、关键 property |
| R1 | module differential/random/formal、mixed SoC smoke |
| R2 | full RTL、coverage merge、mutation、fuzz corpus、E2E block |
| R3 | full workload、performance、clean rebuild、独立 review |

## 8. Signoff 对象

### 8.1 RTL

- 所有需求有 test/property/review/waiver；
- P0/P1 Bug 清零；
- Formal 明确 PROVEN/FAILED/UNKNOWN；
- requirement、functional、assertion、FSM 和代码 coverage 完成 hole review；
- differential 覆盖支持的类型、shape、边界和异常；
- fuzz corpus 可重放并进入 regression；
- UVM/VIP 回归通过；
- 适用重构完成 equivalence。

### 8.2 验证环境

- Golden/scoreboard 有独立单元测试；
- assertion 完成 reachability/vacuity 检查；
- mutation survivor 全部分析；
- Agent 测试的 mutation score 不低于人工基线；
- runner、parser、schema 有回归；
- coverage merge 可审计。

### 8.3 性能

| 状态 | 定义 |
|---|---|
| MEASURED | 原始数据已采集 |
| CHARACTERIZED | 趋势和瓶颈已分析 |
| CALIBRATED | held-out 数据达到约定误差 |
| REGRESSION_PASS | 跟踪 workload 无未批准退化 |
| SIGNED_OFF | 达到产品性能、频率和功耗目标 |

RTL 仿真不能单独完成产品性能 signoff。Fmax 需要 STA，面积需要 synthesis，功耗/能效需要 activity-aware power estimation 或 silicon data。

## 9. Agent 自身评价

- 编译/elaboration 成功率；
- 独立 Oracle 通过率；
- coverage delta / 仿真时间与 token 成本；
- mutation 检出率；
- false/vacuous assertion 比例；
- testcase 缩减比例；
- failure replay 成功率；
- 重复 Bug、错误归因和无效修改比例。

不能用代码行数、测试数量或 Agent 自评完成作为指标。

## 10. 复盘后的调整规则

| 决策 | 含义 |
|---|---|
| ADOPT | 证据支持，进入执行计划 |
| MODIFY | 保留方向，调整范围、阈值或工具 |
| DEFER | 有价值但收益不足或依赖未满足 |
| DROP | 与实际问题不匹配或重复建设 |

```text
优先级 = 风险降低 × 可提前发现程度 × 复用范围 × 可自动化程度 ÷ 实施与运行成本
```

复盘后重新决定 Formal、fuzzing、Evidence Store 的优先级，两个 Block 试点对象，Verilator 适配范围，UVM/VIP/PSS 投入，coverage/mutation 阈值，Agent 权限和资源预算。

## 11. 当前下一步

当前不执行本方案。当前阶段只继续既定验证，并保留完整 evidence、耗时和失败归因。验证结束后完成复盘，再将本方案升级为 v0.3 执行版。

## 参考资料

- `docs/agentic-ic-verification-research-report-2026-07-17.md`
- `.omo/plans/phase6-rtl-verification.md`
- `.omo/notepads/phase6-fpga-verification/learnings.md`
- `docs/caduceus-verification-lessons.md`
- `docs/verification_methodology.md`

