# NPU 性能瓶颈分析报告

> 基于 systolic_engine.py v2，配置 128×128 @1GHz, LPDDR5-6400, weight_cache=ON
> 评估模型：Qwen2.5 1.5B/3B/7B (Q4_K_M)，M=1 decode

---

## 1. 架构现实核查：Weight Stationary ≠ 全模型驻留

| 参数 | 值 |
|-----|----:|
| 片上 SRAM (L1+L2) | **2.5 MB** |
| 3B 模型量化后大小 | **~1.7 GB** |
| 单 tile 权重 (128×128 INT4) | **8 KB** |
| tile 数 (3B 模型) | **~217,000** |

**结论：** Weight Stationary 指 systolic array 内单 tile 计算期间权重不动，不是全模型常驻。每 token 推理时所有 ~1.7 GB 权重必须从 DDR 经 DMA 流式搬入 Weight Buffer，一次一个 8 KB tile。SRAM 仅作流水线缓冲（双缓冲，最多容纳 2 个 tile = 16 KB）。

---

## 2. 单 Tile 开销分解

以 128×128 tile, M=1, INT4 权重/INT8 激活为基准：

```
┌─────────────────────────────────────────────────────────┐
│  Cold Tile (首 tile, 无重叠):                           │
│  ┌──────┐┌──────────────┐┌──────────┐                  │
│  │ DMA  ││ Pipeline Fill││  Drain   │ = 576 cycles     │
│  │191c  ││    256c      ││  129c    │                  │
│  └──────┘└──────────────┘└──────────┘                  │
│                                                         │
│  Steady State (后续 tile, DMA 与上 tile drain 重叠):     │
│  ┌─────────────────────┬──────────────────┐             │
│  │ Pipeline Fill+Drain │  DMA (隐藏)      │ = 385c     │
│  │       385c          │    191c          │             │
│  └─────────────────────┴──────────────────┘             │
│                                                         │
│  有用 MAC: 1 cycle (128×128×2ops = 32768 MAC 同时在算)   │
│  利用率: 1/385 = 0.26%                                  │
└─────────────────────────────────────────────────────────┘
```

**DMA 计算：**
- 每 tile 搬 8320 B (8192 权重 + 128 激活)
- 有效带宽 43.52 B/cycle (51.2 × 0.85)
- DMA 时间 = 8320 / 43.52 = **191 cycles**

---

## 3. 瓶颈排序

| 排名 | 瓶颈 | 每 tile 开销 | 占比 | 说明 |
|:--:|---|:--:|:--:|---|
| **#1** | Pipeline fill+drain | 385c | 67% | 阵列流水线填充/排空，M=1 时 drain 占 1/3 |
| **#2** | DMA 权重搬运 | 191c | 33% | 稳态时与 pipeline 重叠，首 tile 为冷开销 |
| — | 有用 MAC 计算 | 1c | <0.3% | 128×128 PE 同时算，但只占 1 cycle |

### #1 Pipeline 为什么是主瓶颈

- Pipeline fill: H+W = 128+128 = **256 cycles**（权重沿对角线加载，激活逐行流过）
- Pipeline drain: M+H = 1+128 = **129 cycles**（最后激活穿过整个阵列）
- 合计 385 cycles，其中只有 1 cycle 是在做有用乘法
- M=1 decode 时尤为严重：drain 阶段 129 cycles 中只有首个 cycle 产出有效结果

### #2 DMA 为什么排第二

- 稳态时，tile[i] 的 DMA 可与 tile[i-1] 的 drain 重叠 → **DMA 被隐藏**
- 但首 tile 是冷启动：191c DMA + 385c Pipeline = 576c 一次性开销
- `weight_cache=ON` 的贡献：gate+up 共享一次 pipeline fill（省掉中间一次 drain+fill = 385c），但省不掉 DMA（仍需加载两份权重）

---

## 4. DRAM 带宽压力分析

### 4.1 当前利用率

| 模型 | tok/s | 权重数据量 | DRAM 需求 | 可用带宽 | 利用率 |
|------|:-----:|:-----:|:---:|:---:|:---:|
| 1.5B | 43.3 | 0.9 GB/tok | 39.0 GB/s | 43.5 GB/s | **90%** ⚠️ |
| 3B | 19.9 | 1.7 GB/tok | 33.8 GB/s | 43.5 GB/s | **78%** |
| 7B | 8.7 | 3.8 GB/tok | 33.1 GB/s | 43.5 GB/s | **76%** |

> 有效带宽 = 51.2 Gbps × 0.85 (refresh+row conflict) = 43.5 GB/s

### 4.2 DRAM 何时成瓶颈？

DMA 当前不是瓶颈（compute bound, bottleneck=385 > DMA=191），但逼近中：

| 场景 | DMA/tile | Pipeline/tile | 瓶颈 | 后果 |
|------|:---:|:---:|------|------|
| 当前 (64-bit DDR) | 191c | 385c | Compute | DMA 被隐藏 |
| 阵列扩到 256×256 | 764c | 641c | **DMA 反超** | Pipeline 等 DMA |
| 模型 > 13B (+30% tiles) | 191c | 385c | Compute | OK，但余量收窄 |
| 连续批处理 M=8 | 191c | 400c | Compute | 激活量加大但仍 compute bound |
| HBM3 替换 LPDDR5 | 43c | 385c | Compute | DMA 大幅宽松 |

### 4.3 1.5B 的特殊情况

1.5B 在 43.3 tok/s 下 DRAM 利用率已达 90%，距离饱和仅剩 10%。继续优化 pipeline → tok/s 提升 → DRAM 需求线性增长 → **DRAM 将成为 1.5B 的下一道墙**。

---

## 5. Weight Cache 的精确收益

`weight_cache=ON` 的核心机制：PE 有双 weight 寄存器，gate 和 up 两个 MUL_MAT 共享一次 pipeline fill。

| 指标 | 无 cache | 有 cache | 节省 |
|------|:---:|:---:|:---:|
| gate+up 每 K_tile 开销 | fill + 2×(dma+drain) + drain | fill + (2×dma + 2×drain) + drain | 省 1 次 fill (256c) |
| 3B 总周期 | ~98M | ~70M | **~40%** |
| 3B tok/s | 14.8 | 19.9 | **+34%** |

> 省的是 pipeline，不是 DMA。两份权重的 DMA 照做（2×191c），只是省掉了中间一次 fill (256c)。

---

## 6. 优化潜力与天花板

### 6.1 若 Pipeline 开销归零（理论上限）

| 模型 | 当前 tok/s | 纯 MAC 上限 | 提升倍数 |
|------|:---:|:---:|:---:|
| 1.5B | 43.3 | 19,600 | 452× |
| 3B | 19.9 | 9,800 | 492× |
| 7B | 8.7 | 4,200 | 483× |

> 纯 MAC 上限 = peak_macs / total_macs_per_token，仅考虑 1 cycle/tile 有用计算，忽略所有 overhead。不可达，但说明 pipeline 是压倒性瓶颈。

### 6.2 若 Pipeline 归零 + DRAM 带宽成新瓶颈

DMA 191 cycles/tile → 移除 pipeline 后每 tile 只需 191 cycles：

| 模型 | DMA 上限 tok/s | 当前 | 天花板 |
|------|:---:|:---:|:---:|
| 1.5B | ~87 | 43.3 | 2.0× |
| 3B | ~40 | 19.9 | 2.0× |
| 7B | ~17.5 | 8.7 | 2.0× |

**DMA 天花板 = 当前性能的 2.0×。** 即使 pipeline 完全消失，DRAM 也会在 ~2× 处卡住。

### 6.3 现实优化路径

| 优化 | 效果 | 代价 |
|------|------|------|
| **增加 M 维度利用** (连续批处理) | drain 占比下降，利用率 ↑ | 延迟换吞吐，decode 场景 M=1 天然受限 |
| **阵列做宽(W↑)** | tile 数减少，pipeline fill 摊薄 | 面积 ↑，W=256 时阵列面积 ×4 |
| **pipeline 重叠** (double-buffer tile) | fill[i+1] 与 drain[i] 重叠 | 需要额外 SRAM buffer |
| **DDR 扩到 128-bit** | DMA/tile 减半 → 首 tile 开销减 96c | 引脚 + PHY 面积 |
| **HBM** | DMA 降为 1/4，彻底消除 DMA 瓶颈 | 成本 + 封装复杂度 |

---

## 7. 纠正：DRAM 利用率做不到 100%

### 物理天花板

| 损耗来源 | 占比 | 性质 |
|---------|:---:|------|
| Refresh (tRFC/tREFI) | 5.4% | JEDEC 强制，不可消除 |
| Row conflict | ~4.5% | 权重 tile 跨行触发 precharge |
| Bus turnaround + Burst 碎片 | ~5% | DDR 半双工 + 非对齐开销 |
| **理论天花板** | **~85%** | config `dram_efficiency: 0.85` |

**报告中的"利用率"分母已经是打折后的 43.5 GB/s。** 所以 78% 意味着占天花板的 92%，真实余量只有 7%，不是 22%。

| 模型 | 有效利用率 | 占天花板% | 实际余量 |
|------|:---:|:---:|:---:|
| 1.5B | 90% | **106% → 已超** ⚠️ | 0% |
| 3B | 78% | 92% | **7%** |
| 7B | 76% | 89% | 9% |

---

## 8. 总结

```
性能瓶颈金字塔 (3B @ 19.9 tok/s):

        ┌──────────┐
        │ 有用 MAC │ ← 1 cycle/tile (0.3%)
        │  Pipeline │ ← 385 cycles/tile (67%, 主导瓶颈)
        │    DMA   │ ← 191 cycles/tile (33%, 几乎同步到墙)
        └──────────┘

当前墙:   Pipeline fill+drain (385c/tile)
同步墙:   DRAM 带宽 (78% 有效利用率 = 天花板 92%)
理论极限: DMA ceiling ≈ 40 tok/s (2× 当前)
```

**三句话结论：**

1. **Pipeline 是第一瓶颈** — 67% 的 tile 时间在等流水线填充/排空，M=1 decode 天然劣势
2. **DRAM 是同步瓶颈** — 距物理天花板仅 7% 余量；Pipeline 一优化就会撞 DRAM 墙
3. **必须联合优化** — 只修 Pipeline（DRAM 卡死）或只扩 DDR（Pipeline 继续浪费 67%），动一个等于没动
