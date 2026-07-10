# CaduceusCore 设计方法论

## 核心原则

**Arc Model 决定架构候选，Func Model 固化可执行 Spec，RTL 按 Func Model 的接口和行为实现。**

```
Product Requirement
    ↓
Arc Model DSE → Architecture Candidate
    ↓
Architecture Contract
    ↓
Func Model → Executable Spec + Golden Reference
    ↓
RTL → 按 Func Model 接口实现
    ↓
验证 → RTL 输出/trace/cycle == Func Model Golden Reference
```

Arc Model 是架构沙盘，回答“哪个方向值得做”；Func Model 是可执行规范，回答“硬件应该精确做什么”；RTL 是实现，必须被 Func Model 验证，而不是反过来定义 Func Model。

## 铁律

1. **产品需求先于 DSE**：tok/s、TTFT、功耗、面积、内存形态、量化精度是 Arc Model 的硬约束，不是报告里的事后解释。
2. **Arc Model 输出候选，不直接输出 RTL Spec**：Arc 结果必须先落成 Architecture Contract，再进入 Func Model。
3. **Func Model 定义设计意图**：如果 Func Model 的行为与 RTL 不一致，默认优先修改 RTL；只有在证据证明 Func Model 错误时，才修改 Func Model。
4. **RTL 是实现细节**：RTL 的阵列切分、累加器数量、FSM 状态、pipeline staging 服务于 Func Model 定义的接口和行为。
5. **验证是单向的**：RTL vs Func Model 对比中，Func Model 是答案，RTL 是被测试对象。
6. **性能也必须闭环**：RTL 不只做 bit-exact 对比，还要对齐 Func Model 的 cycle、trace、带宽利用率、stall reason、TTFT/TPS。

## 版本对齐

Arc、Func、RTL 必须按版本链路对齐。不同版本之间的不一致不是 bug，但必须显式标注，禁止把新 Arc DSE 结果误写成旧 Func/RTL 已验证结论。

| 链路 | Arc Model | Func Model | RTL | 状态 | 说明 |
|------|-----------|------------|-----|------|------|
| v1 | Block Engine 方向 | Block/bootstrap 配置 | 64x64 broadcast MAC + SFU + Vector | 已跑通方法链路 | 用于建立接口、golden reference、RTL 验证工具链 |
| v2+ | 引入 FSA，DSE 可选 FSA / Block 等多引擎 | 待按 FSA/新候选迁移 | 待实现 | 架构候选阶段 | FSA 结果尚未完成 Func Spec 化和 RTL 验证闭环 |

当前项目应表述为：

> Func Model/RTL 当前验证的是 Arc v1 Block Engine 路线和 bootstrap 实现方法；Arc v2+ 新增 FSA 后给出了新的架构候选，但尚未完成 Func Model Spec 化和 RTL 验证闭环。

## Architecture Contract

Arc Model 每次选型后，必须生成一份可审查、可复现、可版本化的 Architecture Contract。只有 Contract 被 Func Model 实现并通过验证后，该架构才可以进入 RTL 实现。

Contract 至少包含：

| 类别 | 必填内容 |
|------|----------|
| 产品约束 | 目标模型、seq_len、tok/s、TTFT、功耗、面积、内存类型、成本/封装限制 |
| 架构参数 | engine type、array H/W、dataflow、SRAM 容量/分区、DRAM/3D DRAM 带宽 |
| 数值格式 | activation/weight/accumulator dtype、量化方案、scale 粒度、舍入/饱和规则 |
| 软件接口 | ISA/opcode、descriptor 格式、MMIO regmap、异常/状态码、内存布局 |
| 性能假设 | frequency、DMA overlap、NoC/AXI 假设、cycle model、TTFT/TPS 估算 |
| 验证门禁 | Func unit test、真实模型子图、RTL bit-exact、RTL trace/cycle 对齐、已知限制 |

## Arc -> Func -> RTL 门禁

### 1. Arc Model DSE

Arc Model 用解析公式和 PPA 模型快速扫设计空间。它适合筛掉明显不满足产品约束的方案，并比较 engine、array size、memory bandwidth、precision 的大方向。

Arc 阶段必须输出：

- 通过/失败的产品约束和原因
- 候选架构的 PPA 排名
- sensitivity 分析
- 与上一版 Arc/Func/RTL 的差异
- Architecture Contract 草案

Arc 阶段不能声明 RTL 可实现，也不能声明 golden reference 已验证。

### 2. Func Model Spec 化

Func Model 按 Architecture Contract 实现功能行为、软件接口和 timing model。Func Model 的输出是 RTL 开发的唯一可执行 Spec。

Func 阶段必须验证：

- NumPy/PyTorch/GGUF/ONNX 子图 differential test
- bit-exact golden output
- dtype、rounding、saturation、异常行为
- TTFT/TPS/cycle breakdown
- DMA/NoC/SRAM/DRAM trace

如果 Func Model 只覆盖 bootstrap 配置，文档必须标注它对应的 Arc 版本，不能把它当成新 Arc DSE 候选的验证结果。

### 3. RTL 实现与验证

RTL 必须按 Func Model 的接口、寄存器、descriptor、数据格式、行为语义实现。RTL 允许有不同 micro-architecture，但不能改变软件可见行为。

RTL 阶段必须验证：

- 输出与 Func Model golden reference bit-exact 或满足明确 tolerance
- RTL AXI/DMA/MMIO trace 与 Func Model trace 对齐
- RTL cycle 与 Func timing model 的差异在预算内
- stall/bubble reason 可解释
- 若 RTL 暴露 Func Model 未建模行为，先记录差异，再判断是 RTL bug、Func bug，还是 Contract 需要升级

## 校准回路

Func Model 是 Spec，但不是天然正确。发现差异时按以下顺序处理：

1. **RTL 输出错误，Func 有独立 oracle 支撑**：修 RTL。
2. **Func 与 NumPy/PyTorch/GGUF/ONNX oracle 不一致**：修 Func，再重生 golden。
3. **RTL 性能慢于 Func，但行为正确**：先记录实现差距；若 RTL trace 证明 Func timing 假设过乐观，校准 Func timing 参数。
4. **Contract 假设不可实现或代价过高**：回退 Arc Model，更新约束后重新 DSE。

## 示例

| 场景 | 错误做法 | 正确做法 |
|------|----------|----------|
| Arc v2 选出 FSA | 直接宣布当前 Func/RTL 已验证 FSA | 标注 FSA 仍是 Architecture Candidate，先生成 FSA Contract，再做 Func v2 |
| Func v1 是 Block/bootstrap | 用它否定 FSA 的 Arc DSE 结果 | 说明二者版本不对齐，Func v1 只验证 Block/bootstrap 链路 |
| RTL 不支持 batch 权重复用 | 修改 Func Model 加 M-tiling 去匹配 RTL | Func Model 保持权重共享 Spec，RTL 加多累加器支持 |
| RTL cycle 数多于 Func Model | 调高 Func Model 的 cycle 估算 | 把 RTL cycle 差异记录为实现差距，排入优化计划 |
| Func Model 的 DDR 模型与 RTL AXI trace 不符 | 改 Func Model 去拟合 trace | 用 trace 校准模型参数，但不改变 Spec 行为 |
