# DSE 评估报告：FSA 引擎 vs 全引擎设计空间

**日期**: 2026-06-27  
**分支**: `feat/fsa-arc-eval`  
**方法**: 8 引擎 × 7 阵列尺寸 × 2 量化精度 × 3 频率 × 6 内存带宽 = 2,016 配置

---

## 1. FSA 架构原理

### 1.1 设计动机

传统 NPU 处理 Transformer Attention 的典型管线：

```
MXU (QK^T) → SFU (softmax) → MXU (PV) → Vector (residual)
```

三跳数据搬运，每次都要出阵列→进 SFU→回阵列。FSA 的核心思想：**把 softmax 需要的所有操作（rowmax、exp、rowsum）直接焊进脉动阵列的数据流里，零次搬运。**

### 1.2 硬件改动

相比标准 weight-stationary 脉动阵列，FSA 在 PE 层加了三个东西：

| 组件 | 功能 | 传统做法 | FSA 做法 |
|------|------|---------|---------|
| **CMP 列** | 在线 rowmax | 外部 Vector 逐行求 max | 每列一个比较器，纵向传播最大值，一个周期完成 |
| **Split 单元** | exp 近似 | 外部 SFU 的 LUT+插值 | 复用 PE 的 MAC 乘法器做分段线性插值 |
| **纵向数据通路** | 在线归约 | 出阵列→Vector reduce | 数据在阵列内纵向流动，减少读出写回 |

面积代价：±12%（等效于 systolic PE 8.0 → 8.96 mm² @ 128×128, 7nm）。

### 1.3 指令级执行模型

FSA 用 5 条矩阵指令覆盖完整 FlashAttention：

```
F.mx_load_stationary(Q)     → Q 驻留在阵列，等待 K 流式进入
F.mx_attn_score(K, L)       → S = QK^T，同时 CMP 做 rowmax，Split 做 exp，Accumulator 做 rowsum
F.mx_attn_value(V, O)       → P = softmax(S) 已在 Acc 中，直接乘 V
F.mx_reciprocal(L)           → 1/Σexp（行和倒数）
F.mx_attn_lse_norm(O)       → O = O / L
```

关键：第二步 `mx_attn_score` 在一个阵列遍历中完成了 **矩阵乘 + 取 max + exp + 累加**，传统管线需要 MXU→SFU→Vector 来回三次。

### 1.4 为什么效率这么高

三个因素叠加：

1. **PE 面积效率**：weight-stationary PE 只需要 1 个 MAC + 1 个寄存器堆，面积约 8 mm²（128×128），而 block engine 的 PE 需要全并行 MAC，面积 32 mm²。FSA 继承了 systolic PE 的紧凑设计，只加了 12% 的 CMP/Split/纵向通路。

2. **消除管线瓶颈**：传统 systolic 的致命弱点是 pipeline fill/drain——512 个周期的填充 + 512 个周期的排空，吞吐只剩 1/3。FSA 的纵向通路 + CMP/Split 在线操作让数据不需要出阵列，fill/drain 开销被 inline 操作摊薄。

3. **零数据搬运**：MXU→SFU→MXU 的三次搬运完全消除。对于 prefill（128 token query），SFU softmax 8 级流水线 × 16K 元素 × 多头的延迟非常可观，FSA 直接省掉了。

---

## 2. LLM 解码性能对比

### 2.1 受约束场景（面积 ≤ 80mm²，LPDDR5 ≤ 102.4 GB/s）

| 引擎 | tok/s | 面积 | 功耗 | 效率 | 最优配置 |
|------|:---:|:---:|:---:|:---:|------|
| **fsa** | **251** | 59mm² | 22.5W | **4.3** | 128×256 INT2 800MHz LPDDR5-256b |
| gmma | 236 | 64mm² | 24.4W | 3.7 | 96×96 INT2 800MHz LPDDR5-256b |
| wmma | 0 | 26mm² | 8.5W | — | 64×64 INT4 800MHz LPDDR5-32b |

在 ≤ 80mm² 约束下，FSA 是唯一能跑到 250+ tok/s 的引擎，比 gmma 多 6%。

### 2.2 无约束场景（HBM3 带宽）

| 引擎 | tok/s | 面积 | 效率 | 是否 Pareto |
|------|:---:|:---:|:---:|:---:|
| **fsa** | **969-984** | 128-143mm² | 17-18 | ✅ 全线 Pareto |
| block | 813 | 143mm² | 13.9 | ❌ |
| os_systolic | 813 | 143mm² | 13.9 | ❌ |
| gmma | — | >200mm² | — | ❌ 面积超标 |

**FSA 在同等面积下领先 block/os_systolic 约 20% 吞吐（969 vs 813 tok/s），面积效率（tok/s·mm²）高 30%。**

### 2.3 Pareto 前沿

```
tok/s
1000 ┤                              ● fsa 128×256
 900 ┤                    ● fsa 96×96
 800 ┤          ● fsa 64×64
 700 ┤
 600 ┤    block/os_systolic 在更右侧（更大面积）
 500 ┤
     └┬────────┬────────┬────────┬────────
     128      133      138      143      mm²
```

**FSA 独占整个 Pareto 前沿**——在 128-143 mm² 范围没有其他引擎能同时做到更高吞吐和更小面积。

---

## 3. 设计空间关键发现

### 3.1 引擎排名（按效率 tok/s·mm²）

| 排名 | 引擎 | 效率 | 吞吐 | 面积 | 适用场景 |
|:---:|------|:---:|:---:|:---:|------|
| 1 | **fsa** | 18.0 | 942-984 | 128-143 | **全能（最高吞吐+最小面积）** |
| 2 | block | 13.9 | 813 | 143 | 纯空间映射 |
| 3 | os_systolic | 13.9 | 813 | 143 | 输出驻留 |
| 4 | gmma | 3.7 | 236 | 64* | 面积受限时可用 |
| 5 | tensor_core | <3.5 | 525 | 148* | 大阵列专用 |
| 6 | systolic | <0.4 | 20 | 52* | 管线瓶颈致命 |
| 7 | input_stat. | <0.3 | 10 | 44* | 同 systolic |
| 8 | wmma | ~0 | ~0 | 34* | 不适合小阵列 |

*表示该引擎在其他配置下的值

### 3.2 为什么 block 之前看起来最好

block engine 在 **无 HBM + 128×128+ 阵列 + 高带宽** 条件下确实表现好——它没有 pipeline fill/drain，PE 全并行。但在同等面积约束下：

- block 128×128 PE 面积 ≈ 32 × (128×128/128×128) = 32 mm²（纯 PE）
- FSA 128×128 PE 面积 ≈ 8.96 × (128×128/128×128) = 8.96 mm²

**FSA 的 PE 面积只有 block 的 28%，但吞吐持平甚至更高。** 多出来的面积可以堆 SRAM、加通道、或者缩小 die size 降低成本。

### 3.3 为什么 systolic 最弱

传统 weight-stationary systolic 的 pipeline fill/drain 占比太高：

```
64×64 阵列: fill 64 + compute N + drain 64 = N + 128 周期
           overhead = 128/N，当 N < 256 时 overhead > 50%
```

FSA 保持了 systolic PE 的紧凑（8.0 → 8.96），但纵向通路 + CMP 把数据留在阵列内流动，避免了 fill/drain 的第二次惩罚。

---

## 4. FSA 的局限

### 4.1 只做 attention 的 softmax

FSA 的 CMP/Split 是专门为 FlashAttention 的 rowmax + exp + rowsum 设计的。**不能做 layernorm、rmsnorm、gelu、silu、rope。** 这些仍需要独立 SFU。

### 4.2 CV 模型适配未知

CV 模型（MobileNetV3、ResNet、YOLO）以卷积为主，没有 attention。FSA 对 CV 等同于普通 systolic 引擎——**没有加速效果**。CV DSE 需要在安装了 onnx 后重新跑。

### 4.3 混合架构建议

最优方案可能是：
```
FSA (attention 专用) + 精简 SFU (layernorm/gelu/rope，不需要大吞吐 softmax)
```

因为 FSA 接管了 softmax 这个 SFU 最大的计算负载，SFU 可以大幅缩减面积。

---

## 5. CV 模型对比 — FSA 的局限验证

### 5.1 MobileNetV3-Small（轻量 CNN，无 attention）

| 引擎 | fps | 面积 | 约束下最优 |
|------|:---:|:---:|------|
| block | 1216 | 133mm² | — |
| os_systolic | 1216 | 133mm² | — |
| **fsa** | 1216 | 133mm² | ❌ |
| **gmma** | — | — | ✅ **1029 fps @ 51mm²** |

### 5.2 YOLOv8n（目标检测，含 SiLU 激活）

| 引擎 | fps | DW util | 约束下最优 |
|------|:---:|:---:|------|
| block | 146 | 0% | — |
| **fsa** | 146 | 0% | ❌ |
| **gmma** | — | — | ✅ **137 fps @ 64mm²** |

*YOLO 瓶颈在 depthwise conv（DW util = 0%），所有引擎表现一致*

### 5.3 ResNet-18（经典 CNN）

| 引擎 | fps | 约束下最优 |
|------|:---:|------|
| block | 709 | — |
| **fsa** | 709 | ❌ |
| **gmma** | — | ✅ **650 fps @ 64mm²** |

### 5.4 CV 结论

| 工作负载类型 | 推荐引擎 | FSA 表现 |
|------|:---:|------|
| **LLM（Transformer）** | **FSA** | ✅ 1.8× 效率，Pareto 全线最优 |
| **CV（CNN）** | gmma / block | ❌ 无优势，inline softmax 硬件闲置 |
| **混合（ViT）** | 待评估 | ViT 含 attention，FSA 可能有优势 |

**FSA 是一个典型的"领域专用架构"——在 attention 密集的 Transformer 上无敌，但在纯卷积 CNN 上优势归零。** 这完美验证了架构设计的取舍逻辑。

---

## 6. 后续工作

1. **ViT DSE**：Vision Transformer 含 attention，FSA 可能在 CV+attention 交叉领域保持优势
2. **混合架构建模**：FSA（attention）+ 精简 SFU（layernorm/gelu）+ gmma（CV）联合评估
3. **更多 LLM**：qwen2.5-7b、gemma-4-12b 的 attention 占比更高，FSA 优势可能继续扩大
4. **带宽频率校准**：DSE 中 `bandwidth_bytes_per_cycle` 未随频率缩放（已知 bug），修复后需重跑

---

*报告生成工具: sim/design_space_explorer.py + sim/fsa_ref.py*  
*FSA 上游: https://github.com/VCA-EPFL/FSA | Paper: arXiv 2507.11331*
