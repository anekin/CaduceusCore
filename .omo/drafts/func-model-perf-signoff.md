# func-model-perf-signoff — Planning Draft

## Intent
**CLEAR** — 用户知道目标：Func Model 要兼具 RTL 功能 golden reference + SoC RTL 性能 reference 能力。
功能 golden reference 已由 func-model-signoff-v2 完成。现在做性能部分。

## Key User Clarification
> 我们的设计方法是先有Func model，再根据Func model来产生RTL，所以Func model的性能验证应该不依赖于RTL

含义：
1. Func Model 是 spec，先于 RTL 存在
2. RTL 照 Func Model 写——RTL 对照 Func Model 验证，不是反向
3. 性能模型验证不依赖 RTL 仿真 cycle count
4. W4-PERF 的 RTL 阻塞项（firmware, DMA, SFU/Vector dispatch）NOT in scope
5. 正确性参照物 = 架构设计意图（公式、流水线深度、DMA burst 行为等）

## Scope Decision
### 两条验证路径（用户选择）
1. **纯分析路径**（benchmark.py）：8 个 timing model 公式验证 + CoreTimeline + metrics
2. **Spike+firmware 路径**：firmware MMIO dispatch 开销 / weight management（per-K-tile 重载）/ 调度链 latency。Func Model 通过 Spike 跑真实 firmware (npu_firmware.c)，因此 firmware 路径本身就是 Func Model 的一部分。

### IN
- 8 个 timing model 公式正确性（对照架构设计规格）
- CoreTimeline overlap/串行/非双重计数
- Spike+firmware 调度链：MMIO dispatch / weight streaming 开销验证
- SWOverheadModel（RISC-V 控制路径 cycle）验证
- Metrics（TPS/TTFT/TPOT/ITL）派生正确性
- 跨路径一致性（分析路径 vs firmware 路径 cycle 是否自洽）
- 内部一致性（sweep、scaling、瓶颈转移）
- 架构合理性 sanity check
- 性能签收 checklist + evidence

### OUT
- RTL VCS 仿真 / RTL cycle count 校准（DMA output readback zero 是纯 RTL bug，排除）
- 36 层 RTL forward pass
- 性能不是 RTL-accurate，而是 architectural-spec-accurate

## Review Required
false — 用户未要求高精度审查

## Accuracy Criteria (Adopted Default)
"准确" = timing model 公式与架构设计规格一致 + pipeline 事件记账正确 + metrics 派生无错误 + skill scaling 下瓶颈转移方向合理。
不设 RTL-numeric threshold。对齐 testcase-list_methodology.md 的 Tier 2 理念：记录差异，解释根因。

## Gate
status: plan-written
pending action: user review + approve for execution
plan file: `.omo/plans/func-model-perf-signoff.md` (274 lines, 14 tasks + F1-F4)

## High-Accuracy Review Results

### Round 2 — REJECT (Momus) / APPROVE with caveat (Oracle)
- **Momus**: REJECT — 2 new issues: T15 wrong 3B dimensions, T14/Scope efficientdet→vit-b16
- **Oracle**: APPROVE — T15 data error only

### Round 2 Fixes Applied
1. T15: 3B fixed to 36 layers / hidden=2048 (was 28/2560)
2. T14/Scope: efficientdet-lite0 → vit-b16 (matches model_specs.py)

### Round 3 — ALL OKAY ✅
- **Momus** (`bg_38122160`): OKAY — both fixes verified, no blockers
- **Oracle** (`bg_e78e6e96`): APPROVE — both corrections confirmed, no regressions

### Review Receipts
- Native Momus: Round 1 REJECT → Round 2 REJECT → Round 3 OKAY
- Independent Oracle: Round 1 REJECT → Round 2 APPROVE+caveat → Round 3 APPROVE
- Both final verdicts: unconditional approval ✅

## Topology (7 components)
1. Timing model formula verification — 8 模块 × design spec 公式（分析路径）
2. Pipeline/CoreTimeline overlap model verification — 串行/并行/非双重计数
3. Spike+firmware 路径验证 — firmware dispatch + weight streaming + SWOverheadModel
4. Metrics computation verification — TPS/TTFT/TPOT/ITL/DMA overlap/NoC/BW
5. Cross-model consistency & architectural sanity — sweep/scaling/瓶颈转移 + 跨路径一致性
6. Performance signoff checklist & documentation
7. Evidence runner framework — 沿用 func-model-signoff-v2 模式，扩展 perf case registry

## Test Strategy
adopted: 沿用 func-model-signoff-v2 模式 — signoff runner + atomic evidence + validate
新增 perf case registry 扩展到性能维度。

## Key Facts from Exploration
- 8 timing models: BlockEngine, SFUModel, VectorModel, DMAModel, NoCModel, KVCacheModel, DRAMModel, SWOverheadModel
- CoreTimeline: mxu/sfu/vector/kv 推进 wall clock; dma/noc 是 breakdown-only
- E2E analysis 已修复 5 bugs（双重计数、DMA effective 误进 wall_keys、变量交换、ping-pong model、参数错误）
- 60+ tests 在 sim/timing/tests/ — 框架结构性测试，但可能未覆盖公式精度
- 当前 64×64 Block LPDDR5 配置: 21.59 TPS, MXU tile-bound, DDR BW util 68%
- testcase-list_methodology.md: Tier 2 记录差异不设硬性 PASS/FAIL
- mxu-perf-calibration.md: per-tile formula 匹配 RTL exactly（但这是 bonus，不是验证前提）
- 当前无 FM-SOC-PERF 性能测试用例

## Decisions Log
1. Func Model 性能验证不依赖 RTL 仿真 — 对照架构设计规格验证
2. 精度标准 = architectural spec match，不设 RTL numeric threshold
3. 两条路径都做：分析路径（benchmark.py）+ Spike+firmware 路径
4. RTL 特有 bug（DMA readback zero）排除；firmware 设计缺陷（weight buffer limit、dispatch 覆盖）纳入 Func Model 性能验证范围
5. 测试策略 = 沿用 func-model-signoff-v2 的 signoff runner + atomic evidence + validate 模式