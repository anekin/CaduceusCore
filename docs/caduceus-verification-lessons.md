# 芯片验证设计原则 — 来自 CaduceusCore 的 14 条经验

> **版本**: v5 | **日期**: 2026-07-05
> **用途**: 新项目验证架构设计时的参考清单，避免重复踩坑
> **来源**: CaduceusCore NPU ASIC 项目 Phase 0~4 全流程复盘，14 个实际 bug/失败案例

---

## 一、核心设计原则（新项目启动时先过一遍）

### 原则 1：Golden Model 是唯一真相来源

- Func Model 在 RTL 开始写之前必须 bit-exact 验证通过
- RTL 验证直接复用 Func Model 的输出做 golden reference，不手工推导期望值
- Golden 本身也要做边界测试——INT32 极值溢出、FP16 subnormal flushing、全 opcode 枚举

> **CaduceusCore 案例**: Golden Reference 自身有 3 个 bug（INT32 wrap-around 应为饱和、FP16 subnormal 未 flush、RMSNORM opcode 未 dispatch），在 Phase 3 才暴露。

### 原则 2：测试数据 build 代码只写一套，两边复用

- Func Model 和 RTL runner **共用同一套** case build 逻辑，或者一次性生成 `.npz` 文件两边加载
- 禁止 RTL runner 另写一套 `_build_*()`——Func Model 侧的 PASS 对 RTL 侧零保证
- 如果短期内做不到合并，至少在进 VCS 之前用纯 Python dry-run 全部 build 方法

> **CaduceusCore 案例**: `rtl_soc_runner.py::_build_10X()` IndexError 在 VCS 仿真中才暴露。T5.5 已用 Func Model 跑通 FM-SOC-10X，但因两套独立 build 代码，Func Model 结果对 RTL 侧无保证。T10.5 dry-run 被跳过直接导致浪费的 VCS 仿真时间。

**推荐的 build 层架构**:
```
gen_soc_test_vectors.py（只写一次）
├── 输出: input.npz + expected.npz（33 cases）
│
├── Func Model runner 消费 → load .npz → 纯 Python 执行 → 比对 expected
└── RTL runner 消费        → load .npz → VCS Cocotb 执行 → 比对 expected
```

### 原则 3：模块测试至少 1 case 走真实数据通路

- 模块级 testbench 如果用 `$readmemh` 直连端口，必须额外加 1 个 case 走完整打包路径
- 关键路径: `pack_*()` → SRAM preload → wrapper AXI4 sequencer → 引擎端口
- hand-crafted 数据能通过不代表真实模型数据能通过（nibble 排序、tile 对齐等）

> **CaduceusCore 案例**: MXU 模块级 16+18 case 全 PASS，但 SoC 上 op05 卡了 23 次，根因是 INT4 nibble 排序。模块测试绕过了 `pack_int4_tile_major()` → SRAM → wrapper 通路。

### 原则 4：性能测试覆盖到 wrapper 集成层，不是裸引擎

- 裸引擎的 LOAD_W/LOAD_A 在 testbench 直连下是 1 cycle，但真实 SoC 的 wrapper AXI4 预取需要多 cycle
- 性能数据必须来自走 APB MMIO 路径 + AXI4 master 的真实 wrapper 集成环境
- 每个 wrapper 先独立过性能关，再进 SoC E2E

> **CaduceusCore 案例**: 模块级性能用 testbench 端口直连测的 cycle 数，与 Func Model Timing Pipeline 预测有系统性偏差，因为 wrapper AXI4 sequencer 开销未计入。

### 原则 5：Func Model 阶段就用真实 CPU + 真实固件 binary

- 不要用 `miniv.py` 这类 mock CPU 替代真实 RISC-V 核执行固件
- Spike + 真实 `npu_firmware.elf` 早在 Func Model 阶段就联调，暴露 descriptor 字段不匹配等固件问题
- CPU RTL（Ibex）放到最后替换——固件 bug 在 Spike 上秒级迭代，在 Ibex RTL 上分钟级

> **CaduceusCore 案例**: `miniv.py` mock CPU 绕过了真实固件 binary，firmware descriptor 12/15 字段不匹配在 Phase 4 才暴露，且最初在 Ibex RTL 上调试浪费大量时间。

### 原则 6：每次 op 结果独立保存比对，不只比最后一次

- 背靠背多 op 执行时，每次都独立保存中间输出
- 只比最后一次 → accumulator 未清零、中间状态污染等静默错误不会被发现
- 独立保存的中间结果也方便定位「第 N 次 op 开始出错」

> **CaduceusCore 案例**: P13/P14 连续 10 次 op，`compare_rtl.py` 只比对了最后一次。前 9 次的 accumulator 残留错误未被检出。

### 原则 7：E2E case 做双路比对——backdoor read + interface read

- backdoor read（直读 SRAM）验证计算是否正确
- interface read（PCIe TLP / APB MMIO）验证接口通路是否正确
- 两者分开比对，不一致时能精确定位是计算引擎 bug 还是接口通路 bug

```
bk_match=True  + pcie_match=False  → 计算正确，PCIe 通路有问题
bk_match=False + pcie_match=False  → 计算引擎本身算错了
bk_match=True  + pcie_match=True   → 全链路 OK
```

> **CaduceusCore 案例**: FM-SOC-10X op02 出现 `pcie_match=False, bk_match=False`，双路诊断直接指向 MMUL 引擎计算错误或输入 preload 问题，而非 PCIe TLP 路由问题。

### 原则 8：每个 wave 结束设 Review Gate，审计证据后放行

- 单靠执行 agent 自己判断 PASS/FAIL 不可靠——SUMMARY 行写 PASS 但日志全 FAIL 的真实案例已发生
- Review agent 逐项检查: SUMMARY 与 case 日志是否一致、FAIL case 有无对应 bug entry、anti-vacuous case 是否真的检测到 MISMATCH
- OMO ≥ v4.14 使用 Atlas 做 final-review 三态裁决: approve / reject / missing
- Review Gate 在 plan 中作为独立 todo，阻塞下游任务

> **CaduceusCore 案例**: T7 evidence 顶部 SUMMARY 写 "PASS: 7/7"，但 case 日志全显示 GLIBC crash 导致的 `TESTS=3 PASS=0 FAIL=3`。执行 agent 采信 SUMMARY 标记完成，如果不是后续人工发现，T8-T11 就在假结果上跑。

### 原则 9：SRAM 峰值在架构阶段就用真实模型跑

- Phase 0 架构选型时就用目标模型的真实权重跑 tile schedule
- 计算每层 SRAM 峰值用量，确认不超硬件上限
- 不要等到 RTL 集成阶段才暴露容量问题

> **CaduceusCore 案例**: Qwen2.5-3B 完整权重 tile 超过 4MB SRAM，需要 tile streaming。Phase 3 才暴露此架构约束。

### 原则 10：所有编译/运行环境差异在 Phase 3 前解决

- Spike、plugin `.so`、Python 环境统一在 EDA server 上就地编译
- 禁止在开发机编译后 scp 到 EDA server——GLIBC 版本差异、Python 路径差异防不胜防
- Func Model 验证也应在 EDA server 跑一轮，提前暴露环境问题

> **CaduceusCore 案例**: `npu_mmio_plugin.so` 在 Ubuntu 编译，拿到 CentOS EDA server 上 GLIBC_2.32 not found。T7 全 7 case crash。

### 原则 11：按验证阶段拆分 bug 文件，发现即 commit

- `docs/bugs/bugs-module-level.md` / `bugs-soc-func-model.md` / `bugs-soc-rtl.md` 各管各的
- 发现 bug 立即 `git commit`，不攒到阶段结束再批量补记
- 每个 bug 一个 commit，方便 revert 和 blame

> **CaduceusCore 案例**: `docs/bugs.md` 被多次覆盖，MXU 性能和 SOC-RTL bugs 全部丢失。

### 原则 12：Testbench 脚本在进 VCS 之前 dry-run 一遍

- 所有 `_build_*()` 方法在 EDA server 上用纯 Python（不启动 VCS）跑一遍
- 验证: 不抛异常、返回数据非空且维度合理、无 NaN/Inf
- 5 分钟能抓的问题不拖到小时级的 VCS 仿真中

> **CaduceusCore 案例**: `_build_10X()` IndexError 在 VCS 仿真 8.5ns 时崩溃，纯 Python bug 在昂贵仿真中才发现。

### 原则 13：增量替换 RTL，从风险最高的模块开始

- 不要一次性全量替换——根因定位困难
- 先跑混合模式: 单个 RTL 模块 + 其余 Func Model，验证通过后再替换下一个
- 替换顺序按风险排序: 最未经验证的接口模块（PCIe）→ DMA → 引擎 wrapper → CPU（最后）

> **CaduceusCore 案例**: PCIe wrapper 是模块级验证覆盖最弱的环节，增量替换将其作为第一优先级，混合模式下快速定位了一个跨 BAR 路由 bug。

### 原则 14：已知未覆盖点显式记录，不盲目信任覆盖率

- "100% 已测用例通过" ≠ "所有功能都测了"
- MXU INT8 mode、descriptor chain、firmware dispatch 等已知盲区要在 `issues_found.md` 显式标出
- 每个阶段结束更新"未覆盖"列表，作为下一阶段的输入

> **CaduceusCore 案例**: MXU 的 INT8 mode（`ctrl_dtype[1:0]=1`）在整个 Phase 1 中从未测试，但模块级 100/100 随机回归 PASS 的统计数字掩盖了这一点。

---

## 二、验证架构清单（新项目启动时逐项打勾）

| # | 检查项 | 时机 |
|:--:|------|:--:|
| ☐ | Func Model bit-exact 通过（含边界: INT32 极值、FP16 subnormal、全 opcode） | Phase 1 结束 |
| ☐ | 测试数据生成脚本只写一套，Func Model 和 RTL 两边复用 | Phase 2 开始前 |
| ☐ | 模块测试至少 1 case 走完整打包路径（pack → SRAM → wrapper → 引擎端口） | 每个模块写完 |
| ☐ | 性能测试覆盖到 wrapper 集成层，数据来自 APB MMIO 路径 | 每个 wrapper 写完 |
| ☐ | Func Model 用 Spike + 真实 firmware binary，不用 mock CPU | Phase 3 开始 |
| ☐ | CPU RTL 排在 RTL 替换顺序的最后一步 | Phase 4 规划 |
| ☐ | 多 op 测试每次独立保存输出并比对 | 测试向量生成脚本 |
| ☐ | E2E compare 同时做 backdoor read + interface read，分开记录结果 | RTL runner 实现 |
| ☐ | Plan 中每个 wave 结束设 Review Gate（Atlas final-review） | Plan 编写时 |
| ☐ | 架构阶段用真实模型权重跑 tile schedule，算 SRAM 峰值 | Phase 0 |
| ☐ | Spike + plugin + Python 环境在 EDA server 上就地编译验证 | Phase 3 开始前 |
| ☐ | 按阶段拆分 bug 文件，发现即 commit，不攒批 | 全过程 |
| ☐ | Testbench 全部 `_build_*()` 在进 VCS 前纯 Python dry-run | Phase 3.5 |
| ☐ | `issues_found.md` 显式列出已知未覆盖点 | 每个阶段结束 |

---

## 三、Review Gate 操作参考

单靠执行 agent 自己判断 PASS/FAIL 不可靠。每个 wave 结束设 Review Gate。

| OMO 版本 | Review Agent | 调用方式 |
|---------|-------------|---------|
| < v4.14.0 | 无专用 agent | 兜底: `task(category="ultrabrain", subagent_type="plan", ...)` |
| ≥ v4.14.0 | **Atlas**（final-review 三态裁决） | `task(subagent_type="atlas", load_skills=[], run_in_background=false, ...)` |

Atlas 输出: **approve**（放行）/ **reject**（拒绝，修后重审）/ **missing**（证据不足，补后重审）。

升级命令: `opencode plugin oh-my-openagent@4.15.1 -f`

Plan 中写法:
```markdown
- [ ] R2. Review gate — Atlas 审计 T6+T7 证据
  What to do: task(subagent_type="atlas", ...) 只读 final-review
  Blocked by: 7 | Blocks: 8
  Evidence: task-6-p0-full-rtl.txt, task-7-p1-full-rtl.txt
  Acceptance: 输出 approve
```

---

## 四、CaduceusCore 复盘附录（14 个原始案例）

以下为每一条原则对应的实际案例，供深入理解时参考。

| # | 严重度 | 一句话 | CaduceusCore 实际案例 |
|:--:|:---:|------|------|
| 1 | ★★★★★ | 模块测试输入走真实打包路径 | MXU 模块 16+18 PASS，SoC 上 INT4 nibble 排序卡 23 次 |
| 2 | ★★★★ | bug 按阶段拆分文件，写完就 commit | `docs/bugs.md` 被多次覆盖，数据丢失 |
| 3 | ★★★ | Func Model 用 Spike + 真实固件 | `miniv.py` mock 漏了 12/15 descriptor 字段不匹配 |
| 4 | ★★★ | Golden 也是代码，边界测试不能省 | Golden 自身有 INT32 溢出/FP16 subnormal/opcode 未 dispatch 三个 bug |
| 5 | ★★★ | 每次 op 结果独立保存比对 | 连续 10 op 只比最后一次，中间 accumulator 残留未被发现 |
| 6 | ★★★ | 性能测试覆盖到 wrapper 集成层 | 裸引擎 testbench 直连 LOAD 1 cycle，wrapper AXI4 真实多 cycle |
| 7 | ★★ | CPU RTL 最后换，Spike 陪跑 | firmware bug 在 Ibex RTL 上分钟级 debug，Spike 秒级 |
| 8 | ★★ | Phase 0 跑真实模型做 SRAM 峰值 | Qwen2.5-3B 权重超过 4MB，Phase 3 才暴露 |
| 9 | ★★ | wrapper 先独立过性能关 | wrapper AXI4 sequencer/descriptor FSM 只有功能验证无性能测试 |
| 10 | ★★★ | 环境在 EDA server 就地编译 | `npu_mmio_plugin.so` 跨机器 GLIBC 不兼容，T7 全 crash |
| 11 | ★★★ | Testbench dry-run 在 Func Model 上先跑 | `_build_10X()` IndexError 在 VCS 中才暴露 |
| 12 | ★★★★ | 每 wave spawn review agent 审计 | T7 SUMMARY 写 PASS 但日志全 FAIL，agent 被误导 |
| 13 | ★★★ | RTL runner 和 Func Model build 代码统一 | 两套独立 build，Func Model PASS 对 RTL 侧无保证 |
| 14 | ★★★ | E2E 双路比对（backdoor + interface） | FM-SOC-10X pcie_match/bk_match 双 False 快速定位引擎 bug |
