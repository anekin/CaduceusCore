# 端侧 NPU 架构方案建议书

> 约束基线：**LPDDR5-6400（51.2 GB/s，64-bit）+ INT4 量化**  
> 注：原 Arc Model Zoo v2 DSE 以 INT2 为默认精度，结果乐观；本文按可量产精度 INT4 重新推导。

---

## 1. 产品需求基线（修正后）

| 维度 | 目标要求 | 说明 |
|------|---------|------|
| 主力模型 | Qwen2.5-3B | 3B decode 20-25 tok/s 为 MRD 下限 |
| 兼容模型 | Qwen2.5-1.5B/3B/7B | 7B 目标 ≥20 tok/s |
| 内存接口 | **LPDDR5-6400，64-bit** | 51.2 GB/s 峰值，~43.5 GB/s 有效带宽（85% 效率） |
| 功耗形态 | 芯片 ≤12W / M.2 ≤10W | 端侧封装 |
| 面积约束 | ~30 mm²（≤40 mm² 容差） | 作为 30 mm² 目标的工程余量 |
| 量化精度 | **INT4** | 当前可量产最低精度；INT2 仅作为未来待验证选项 |

**关键约束变更理由**：
1. **INT2 尚未被端侧 LLM 量产验证**。Qwen/llama.cpp 生态主流为 INT4（Q4_K_M / AWQ / GPTQ-Int4）。INT2 在学术研究中可行（UPQ、Bi-LLM），但普遍存在明显精度退化，不满足通用产品交付要求。
2. **64-bit LPDDR5 是端侧芯片物理极限**。128-bit LPDDR5 仅见于 Apple M 系列、Jetson Orin 等笔记本/开发板级芯片，~30 mm² 手机/IoT SoC 无法承受引脚数和封装面积。

---

## 2. 引擎选型：四类引擎直接淘汰

在 **INT4 + LPDDR5-6400** 约束下，用 `npu_sim.py` 对 64×64 阵列实测：

| 引擎 | 面积 (64×64 INT4) | 3B decode tok/s | 结论 |
|------|:-----------------:|:---------------:|:----|
| **WMMA** | ~57 mm² | 0.054 | 16×16 碎片导致 DMA 启动开销爆炸，单 die NPU 不可用。**一票否决**。 |
| **Input-Stationary** | ~44 mm² | 6.4 | M=1 decode 激活复用率极低，未达 20 tok/s 目标。**一票否决**。 |
| **Tensor Core** | ~52 mm² | 27.7 | 64×16×16 子 tile 碎片，性能略低于 Block 但面积更大。**不推荐**。 |
| **Systolic** | **22.2 mm²** | **11.2** | 面积最小，但 pipeline fill/drain 开销在 M=1 decode 下无法摊销，**未达 20 tok/s 目标**。 |
| **OS-Systolic** | 28.2 mm² | **29.6** | 零 pipeline 开销，DRAM-bound，性能 = Block。 |
| **Block** | 28.2 mm² | **29.6** | 全并行广播，DRAM-bound，与 OS-Systolic 等价。 |
| **GMMA** | 30.2 mm² | **30.0** | TMA 可隐藏 DMA latency，但受 DRAM 带宽上限约束，**性能与 Block 相同**。 |

**候选引擎集**：{Block, OS-Systolic}。Systolic 因性能不达标被排除；GMMA 因面积/功耗更大但无性能收益，不推荐。

---

## 3. 阵列尺寸验证：为什么 64×64 是端侧最优

在 LPDDR5-6400 带宽受限下，扩大阵列不会提升 decode 吞吐：

| 引擎 | 阵列 | 3B tok/s (INT4, LPDDR5-6400) | 面积 |
|------|:----:|:----------------------------:|:----:|
| Block | 64×64 | 29.6 | 28.2 mm² |
| Block | 96×96 | 29.0 | ~38 mm² |
| Block | 128×128 | 30.0 | ~52 mm² |
| GMMA | 64×64 | 30.0 | 30.2 mm² |
| GMMA | 96×96 | 29.0 | ~43 mm² |
| GMMA | 128×128 | 30.0 | ~60 mm² |

**结论**：DRAM 墙下，128×128 相对 64×64 几乎没有性能收益，但面积和功耗大幅增加。端侧应坚持 **64×64 小阵列**。

---

## 4. 两级产品架构方案

### 方案 A：成本优先 — Block 64×64 INT4 + LPDDR5-6400

| 参数 | 值 |
|------|-----|
| 引擎 | **Block Engine + Weight Cache** |
| 阵列 | 64×64 |
| DRAM | **LPDDR5-6400（51.2 GB/s）** |
| 量化 | **INT4** |
| 频率 | 800 MHz |
| 面积 | **28.2 mm²** |
| 功耗 | **~9.6 W** |
| 3B tok/s | **29.6** |
| 7B tok/s | **~13**（估算，INT4 下约为 INT2 DSE 值的一半） |
| 12B tok/s | **~8**（不达标） |

**推荐理由**：
1. **面积/功耗最低的可行解**：28.2 mm² / 9.6W，满足 3B 20-25 tok/s 目标。
2. Block 与 OS-Systolic 在此配置下性能相同；Block 实现更简单、面积模型更成熟，优先选 Block。
3. 适合仅覆盖 **1.5B-3B** 的低成本产品。

**限制**：
- 7B 仅 ~13 tok/s，不满足 ≥20 tok/s 目标。
- 12B 完全不可用。

**适用场景**：超低成本 IoT、穿戴设备、仅跑 1.5B-3B 模型的端侧芯片。

---

### 方案 B：GMMA 64×64 INT4 + LPDDR5-6400 — 不推荐

| 参数 | 值 |
|------|-----|
| 引擎 | GMMA + Weight Cache |
| 阵列 | 64×64 |
| DRAM | LPDDR5-6400（51.2 GB/s） |
| 量化 | INT4 |
| 频率 | 800 MHz |
| 面积 | 30.2 mm² |
| 功耗 | ~10.4 W |
| 3B tok/s | 30.0 |
| 7B tok/s | ~13（估算） |
| 12B tok/s | ~8（不达标） |

**不推荐理由**：
1. **TMA 无法突破 DRAM 带宽上限**：GMMA 的 TMA 只能把 DMA latency 重叠到 compute 后面，不能减少需要从 DRAM 读取的总字节数。在 LPDDR5-6400 下，Qwen2.5-3B INT4 的 weight-bound 上限约 **41 tok/s**，实际可达约 30 tok/s，与 Block 相同。
2. **面积/功耗更大但无收益**：相比 Block 64×64 增加 2 mm² TMA 单元 + 4 MB Shared Memory，性能没有提升。
3. **7B 仍不达标**：与 Block 一样约 13 tok/s，无法覆盖 7B 模型。

**适用场景**：仅当未来升级到 HBM 或显著更高带宽，且 TMA 的 latency-hiding 能带来 compute-bound 场景的收益时，才值得重新评估。

---

## 5. 模型覆盖能力总结（INT4 + LPDDR5-6400）

| 产品定位 | 引擎 | 3B | 7B | 12B | 备注 |
|---------|:----:|:--:|:--:|:---:|:----|
| **推荐** | **Block 64×64** | ✅ **29.6** | ❌ ~13 | ❌ ~8 | 仅 1.5B-3B |
| 不推荐的备选 | GMMA 64×64 | ✅ 30.0 | ❌ ~13 | ❌ ~8 | 面积/功耗更大，无性能收益 |

> 12B 在 INT4 + LPDDR5-6400 约束下无法达到 20 tok/s，这不是引擎选择问题，而是 **DRAM 带宽 / 权重大小** 的物理限制。GMMA 的 TMA 不能突破这一上限。

---

## 6. 关于 INT2 的补充说明

如果后续 INT2 量化精度得到验证（例如通过 QAT + 误差校正），在相同 LPDDR5-6400 下性能可接近翻倍（受 DRAM 带宽上限约束，INT2 理论上限约 82 tok/s）：

| 引擎 | INT4 + LPDDR5-6400 | INT2 + LPDDR5-6400（修正后） |
|------|:------------------:|:----------------------------:|
| Block 64×64 | 29.6 | ~58 |
| GMMA 64×64 | 30.0 | ~58 |

届时：
- **Block 64×64 INT2** 可覆盖 1.5B-7B（3B ~58，7B ~24，12B ~16，12B 仍略低于 20 tok/s）
- **GMMA 64×64 INT2** 性能与 Block 相同，但面积/功耗更大，仍不推荐

但**在 INT2 被验证之前，架构决策应以 INT4 为准**。

---

## 7. 模型假设修正：TMA_OVERLAP 与 DRAM 带宽上限

### 7.1 原始模型的问题

在此前版本中，`sim/engine/gmma_engine.py` 使用 `TMA_OVERLAP = 0.5` 将 effective per-tile DMA 计算为：

```
effective_per_tile_dma = per_tile_dma × (1 - 0.5) = 0.5 × per_tile_dma
bottleneck = max(per_tile_compute, effective_per_tile_dma)
```

这相当于假设 TMA 把 steady-state 的 DRAM 时间减半，即**把有效带宽翻倍**。但 TMA 只能隐藏 latency（让 compute 不必等待 DMA），不能减少必须从 DRAM 读取的总字节数，因此**不能突破物理 DRAM 带宽上限**。

### 7.2 修正后的模型

已修复 `gmma_engine.py`，steady-state bottleneck 现在受物理 `per_tile_dma` 下限约束：

```python
bottleneck = max(per_tile_compute, per_tile_dma)
```

`TMA_OVERLAP` 仍保留，但仅用于描述 compute-bound 场景下可被隐藏的 exposed DMA latency，不再产生额外带宽。

### 7.3 DRAM 带宽上限估算

Qwen2.5-3B 总参数量约 2.5 B。INT4 decode 每 token 需要把全部权重读一遍：

```
每 token 读取权重 ≈ 2.5 B params × 0.5 byte/param = 1.25 GB
LPDDR5-6400 64-bit 有效带宽 ≈ 51.2 GB/s × 85% = 43.5 GB/s
理论 tok/s 上限 ≈ 43.5 / 1.25 ≈ 35 tok/s
```

实际仿真约 30 tok/s（含 activation、KV cache、SFU、cold-start 等开销），与该上限一致。任何引擎在此约束下都不可能显著超过 35 tok/s。

### 7.4 修正后的影响

| 配置 | 修正前 tok/s | 修正后 tok/s | 原因 |
|------|:-----------:|:-----------:|:----|
| GMMA 64×64 INT4 | 59.1 | 30.0 | 原模型把带宽翻倍，违反物理上限 |
| Block 64×64 INT4 | 29.6 | 29.6 | 原模型已符合带宽上限，无变化 |
| GMMA 64×64 INT2 | 122.1 | ~58 | 同上 |
| Block 64×64 INT2 | 61.2 | ~58 | 微调后仍在带宽上限内 |

**结论**：在 LPDDR5-6400 下，GMMA 与 Block 性能等价；GMMA 的 TMA 单元和 Shared Memory 成为纯面积/功耗开销，因此不再推荐。

---

## 8. 推荐架构矩阵

```
                    模型覆盖范围增大 →

  超低成本           1.5B-3B           1.5B-7B
  ┌──────────┐      ┌──────────┐      ┌─────────────────────┐
  │ Block    │      │ Block    │      │ 需要更高带宽        │
  │ 64×64    │ ───→ │ 64×64    │ ───→ │ （LPDDR5X-8533 /    │
  │ INT4     │      │ INT4     │      │  LPDDR5T-9600）     │
  │ LPDDR5-64│      │ LPDDR5-64│      │ 或 INT2 验证        │
  │ 28.2mm²  │      │ 28.2mm²  │      │                     │
  │ ~9.6W    │      │ ~9.6W    │      │                     │
  │          │      │          │      │                     │
  │ 3B: 30 ✅│      │ 3B: 30 ✅│      │ 7B: ~25 tok/s       │
  │ 7B: 13 ❌│      │ 7B: 13 ❌│      │ 12B: ~16 tok/s      │
  │ 12B: 8 ❌│      │ 12B: 8 ❌│      │                     │
  └──────────┘      └──────────┘      └─────────────────────┘
```

---

## 9. 关键设计原则

1. **INT4 是当前端侧可量产最低精度**  
   在 INT2 未被产品级验证前，所有架构决策以 INT4 为基线。

2. **LPDDR5-6400 64-bit 是 ~30mm² 芯片的合理上限**  
   128-bit LPDDR5 对端侧芯片封装面积不可接受；LPDDR5X-8533/LPDDR5T-9600 可提升带宽但仍保持 64-bit。

3. **小阵列（64×64）匹配低带宽**  
   DRAM 墙下，128×128 相对 64×64 几乎无性能收益，但面积/功耗显著增加。

4. **TMA 不能突破 DRAM 带宽上限**  
   GMMA 的 TMA 只能隐藏 DMA latency，不能减少总读取字节数。在 LPDDR5-6400 下，GMMA 与 Block 性能相同，但面积/功耗更大，因此不推荐。

5. **不要选 Systolic / WMMA / Tensor Core / Input-Stationary 作为端侧 decode 主引擎**  
   Systolic pipeline 开销在 M=1 下致命；WMMA/Tensor Core 受碎片/调度限制；Input-Stationary 激活复用率极低。

---

## 10. 结论

在 **INT4 + LPDDR5-6400 + ~30 mm²** 的真实端侧约束下，推荐架构为：

> **Block 64×64 + Weight Cache + INT4 + LPDDR5-6400**  
> 面积 28.2 mm²，功耗 ~9.6 W

该方案可覆盖：
- Qwen2.5-3B：**29.6 tok/s**（满足 20-25 tok/s 目标）
- Qwen2.5-7B：**~13 tok/s**（不达标）

**7B/12B 模型在 INT4 + LPDDR5-6400 约束下无法达到 20 tok/s**。若产品必须支持 7B，需满足以下至少一项：
- 升级到 LPDDR5X-8533（68 GB/s）或 LPDDR5T-9600（76.8 GB/s），仍保持 64-bit
- 采用 INT2 并验证其精度（Block 64×64 INT2 可达 ~58 tok/s，7B ~24 tok/s）
- 放宽面积/功耗约束，允许更大阵列或 HBM

**GMMA 不推荐**：在 LPDDR5-6400 下，GMMA 64×64 与 Block 64×64 性能相同（~30 tok/s），但面积增加 2 mm²、功耗增加 ~0.8 W，没有架构收益。TMA 的价值只在 compute-bound 且带宽充裕的场景（如 HBM）才能体现。

---

*本文基于 `github.com/anekin/CaduceusCore` 自研 Python NPU simulator。LPDDR5-6400 为 51.2 GB/s，npu_sim.py CLI 以 `--dram 50`（50 GB/s）为最接近预设，误差 <2.5%，不影响架构结论。所有 INT4 + LPDDR5-6400 数据由 `npu_sim.py --precision 4 --dram 50 --array 64x64 --weight-cache` 实测得到，7B/12B 数据由 DSE 结果按 INT4 折半估算。GMMA TMA 模型已修正：`gmma_engine.py` 的 steady-state bottleneck 不再低于物理 `per_tile_dma`，确保结果不违反 DRAM 带宽上限。*
