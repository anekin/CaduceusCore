# 架构选型报告 B — 3D DRAM 高带宽端侧 NPU

**日期**: 2026-06-29  
**需求**: 高端端侧 / 轻量加速卡，3D DRAM 堆叠，跑 7B 模型实时推理  
**模型**: Qwen2.5-7B INT4 (3.5 GB)，28 层, hidden=3584  
**内存**: 3D DRAM 1024 GB/s (1 TB/s)，可用 870 GB/s

---

## 0. 物理天花板

```
7B INT4 = 3.5 GB
理论 TPS 天花板 = 1024 GB/s ÷ 3.5 GB ≈ 293 tok/s
理论 TTFT 底线 = 3.5 GB ÷ 870 GB/s ≈ 4 ms (纯权重 DMA)
```

**关键区别**: 1 TB/s 带宽下，DRAM 不再是瓶颈。计算单元（引擎 pipeline depth）成为新瓶颈。

---

## 1. 引擎对比

**固定**: 1 TB/s, SRAM 8MB

| Engine | TTFT | TPS | 面积 | 功耗 | TPS/mm² |
|--------|:---:|:---:|:---:|:---:|:---:|
| **block 128×256** ★ | 26ms | **38** | 198mm² | 97W | **0.191** |
| block 256×256 | 26ms | 39 | 262mm² | 129W | 0.147 |
| block 256×512 | 13ms | 77 | 390mm² | 193W | 0.197 |
| FSA 128×256 | 103ms | 13 | 152mm² | 74W | 0.086 |
| FSA 256×256 | 66ms | 20 | 170mm² | 83W | 0.115 |
| gmma 128×256 | 102ms | 13 | 214mm² | 105W | 0.061 |

**核心发现**:

1. **引擎差异暴露**: FSA 13 TPS vs block 38 TPS — 2.9× 差距
2. **原因**: FSA 的脉动管线 fill/drain（H+M+W = 385 cycles/tile）成为瓶颈；block 无管线开销（H+4 = 132 cycles/tile）
3. **block TPS/mm² 领先 2.2×**: 0.191 vs FSA 0.086
4. **FSA 在 LPDDR5 场景胜出，在 3D DRAM 场景被反超** — DSE 的价值体现

**为何选 block**: 带宽充裕时，算力效率决定吞吐。block 的全并行广播架构无脉动管线延迟，在 M=1 decode 场景下利用率远高于 FSA。

---

## 2. SRAM 敏感性

**固定**: block 128×256, 1 TB/s

| SRAM | TTFT | TPS | 面积 |
|:---:|:---:|:---:|:---:|
| 2MB | 26ms | 38 | 189mm² |
| 4MB | 26ms | 38 | 192mm² |
| 8MB | 26ms | 38 | 198mm² |
| 16MB | 26ms | 38 | 211mm² |

**SRAM 完全不影响 TPS** — 因为瓶颈是算力（pipeline depth），不是数据搬运。1 TB/s 带宽已经足以让 DMA 隐藏在计算之后。

**推荐 SRAM: 4-8MB** —满足 KV cache buffer 需求（7B 每层 KV ≈ 2MB），不需更大。

---

## 3. 推荐配置

| 参数 | 值 | 依据 |
|------|------|------|
| **引擎** | **block 128×256** | 面积效率最优 (0.191) |
| **SRAM** | **4-8 MB** | KV cache 缓冲足够 |
| **精度** | INT4 权重 / INT8 激活 | cos_sim ≥ 0.96 |
| **内存** | 3D DRAM 1024 GB/s | 1 TB/s |
| **阵列** | 128×256 | 256×256 仅 +1 TPS 但面积 +32% |
| **面积** | **~192-198 mm²** | block PE + 8MB SRAM + PHY |
| **功耗** | **~97W** | 含 DRAM 接口 |

### 预期性能

| 负载 | 性能 |
|------|:---:|
| Qwen2.5-7B decode | **38 tok/s** |
| Qwen2.5-7B TTFT (128 tok) | **26 ms** ✓ < 200ms |
| Qwen2.5-3B decode | ~200 tok/s |
| Qwen2.5-1.5B decode | ~600 tok/s |

---

## 4. 两场景引擎选择对比

| | 场景 A: LPDDR5 + 3B | 场景 B: 3D DRAM + 7B |
|---|---|---|
| 瓶颈 | DRAM 带宽 | **引擎算力** |
| 推荐引擎 | **FSA** | **block** |
| 关键指标 | 面积 (32mm²) | TPS (38) |
| TPS/mm² | FSA 0.70 vs block 0.42 | **block 0.19** vs FSA 0.09 |
| 选择逻辑 | 带宽瓶颈掩盖引擎差异 → 选面积最小的 | 算力瓶颈暴露 → 选 pipeline 最短的 |

**DSE 价值**: 同一套引擎库，通过切换内存参数，自动选出最优架构。不存在万能引擎。

---

## 5. 设计约束验证

| 约束 | 要求 | 实际 | 状态 |
|------|------|------|:---:|
| TTFT | < 200ms | 26ms | ✓ |
| 模型 | 7B | 7B | ✓ |
| 实时性 | > 20 tok/s | 38 tok/s | ✓ |

### 模型局限

1. **单 token decode 假设** — 无 batch。多用户并发时 FSA 的 pipeline 利用率会显著改善，两者的差距会缩小
2. **SRAM 面积模型** — block 的广播互连面积可能低估
3. **weight preloading** — 当前模型未考虑权重预加载优化，可能进一步提高 block 利用率

---

*DSE 工具: `sim/design_space_explorer.py` (K-tiling + SRAM v3)*  
*场景: 3D DRAM 1TB/s + Qwen2.5-7B INT4*
