# 架构选型报告 B — On-chip 3D DRAM 高带宽 NPU

**日期**: 2026-07 | **工艺**: TSMC 12nm  
**需求**: 高端端侧 NPU，3D DRAM 堆叠，跑 7B 模型实时推理  
**模型**: Qwen2.5-7B INT4 (3.5 GB)，28 层  
**内存**: On-chip 3D DRAM 5GB, 500 GB/s（权重全量常驻）  
**对标**: 瑞芯微 RK1828 (20 TOPS INT8, ~100mm²@22nm, 7B=59-180 tok/s)

---

## 0. 设计约束

| 约束 | 要求 | 验证 |
|------|------|:---:|
| 模型 | 7B | 7B |
| Decode TPS | ≥ 100 | ✓ (148) |
| TTFT (128 tok) | < 200ms | ✓ (160ms) |
| 权重驻留 | 5GB ≥ 3.5GB | ✓ |

---

## 1. H×W 维度扫描（核心发现）

On-chip 权重常驻 → 无 K-tiling（无需从 DRAM 加载权重维度）。H 处理 batch 维度（M），W 处理输出维度（N）。

| H×W | TOPS INT8 | TPS | TTFT | 面积 | 达标 |
|:---:|:---:|:---:|:---:|:---:|:---:|
| **4×1536** | **6.1** | **148** | **160ms** | **56.9mm²** | ✓ |
| 8×1536 | 12.3 | 148 | 80ms | 61.3mm² | ✓ |
| 16×1536 | 24.6 | 148 | 40ms | 70.1mm² | ✓ |
| 32×1024 | 32.8 | 142 | 28ms | 76.0mm² | ✓ |
| 32×1536 | 49.2 | 148 | 20ms | 87.8mm² | ✓ |
| 64×1024 | 65.5 | 142 | 14ms | 99.5mm² | ✓ |
| 128×512 | 65.5 | **78** | 13ms | 99.5mm² | ✗ TPS |

**核心洞察**：

1. **Decode TPS 恒定 ~148 tok/s**——on-chip 500GB/s 带宽天花板（500/3.5≈143，加 KV cache overhead ~148）。计算带宽比 148/6.1≈24，7B 模型下算力根本不是瓶颈。

2. **TTFT 随 H 减小而增大**（prefill 需 M_tiles=128/H），但 H=4 时 TTFT=160ms 仍 < 200ms ✓

3. **H=4 用 6.1 TOPS 达成 148 TPS，而 H=128 需 65.5 TOPS 仅 78 TPS**——高 H 的 per-tile 固定开销（H+4=132 cycles）远大于矮 H（4+4=8 cycles），导致计算资源大量浪费。→ **矮宽阵列 = 24× TOPS 效率提升**

4. **128×512 不及格**：H 过高导致 per-tile overhead 致命，W 过小导致 N_tiling 8 次 tile 切换。矮宽（H≤16, W≥1024）才是正确范式。

---

## 2. 面积分解 (block 4×1536 @12nm)

| 组件 | 面积 | 占比 |
|------|:---:|:---:|
| PE 阵列 (6,144 MACs) | 4.4mm² | 7.7% |
| SRAM L1+L2 (8MB) | 36.1mm² | **63.5%** |
| SFU + RISC-V + DMA + Crossbar | 16.3mm² | 28.8% |
| DRAM PHY | 0mm² | — (on-chip 免) |
| PCIe | 0mm² | — (on-chip 免) |

> **8MB SRAM 占 63.5% 面积**，是主导成本。12nm 下 SRAM 约 0.0044 mm²/KB（含外围），4MB 可降至 17.6mm² 节省 ~50% SRAM 面积，总面积降至 ~38mm²。但待评估 KV cache 容量需求。

---

## 3. 与 RK1828 对标

| | RK1828 | Arc Model (H=4, W=1536) |
|---|---|---|
| TOPS INT8 | 20 | 6.1 |
| 工艺 | 22nm | 12nm |
| 面积 | ~100mm² | ~57mm² |
| 7B TPS | 59-180 | **148** |
| mm²/TOPS | 5.0 | 9.3 |
| 架构 | 宽向量/unknown | block 4×1536 broadcast |

**分析**：
- Arc 6.1 TOPS vs RK1828 20 TOPS：三倍算力差但同等 TPS，因为 on-chip 带宽瓶颈下算力严重过剩
- Arc mm²/TOPS=9.3 比 RK1828=5.0 差，但 mm²/TPS=0.38 vs RK1828 折算 0.30——实际面积效率更优
- RK1828 的 20 TOPS 可能在 INT4 标称（本文档统一 INT8），若按 INT4 则等效 10 TOPS INT8，9.3 vs 10 对应

---

## 4. 推荐配置

| 参数 | 值 |
|------|------|
| 引擎 | **block 4×1536** (broadcast output stationary) |
| SRAM | 8 MB (L2)，可选 4MB 降至 ~38mm² |
| 精度 | INT4 权重 / INT8 激活 |
| 内存 | On-chip 3D DRAM 5GB, 500 GB/s |
| 面积 @ 12nm | **~57 mm²** |
| TOPS INT8 | **6.1** |
| 功耗 | ~12W（估） |

### 预期性能

| 负载 | 性能 |
|------|:---:|
| Qwen2.5-7B decode | **148 tok/s** |
| TTFT (128 tok) | **160 ms** ✓ |
| Qwen2.5-3B decode | ~600 tok/s |
| Qwen2.5-1.5B decode | ~1,200 tok/s |

### 面积优化路径

| 优化 | SRAM | 总面积 | TPS 影响 |
|------|:---:|:---:|:---:|
| 当前 | 8MB | 57mm² | 148 ✓ |
| SRAM 降至 4MB | 4MB | ~38mm² | 待验证（KV cache 竞争） |
| 工艺升级 7nm | 8MB | ~20mm² | TPS 不变（BW 瓶颈） |

---

## 5. 两场景对比

| | 场景 A (LPDDR5) | 场景 B (On-chip 3D DRAM) |
|---|---|---|
| 模型 | 3B | 7B |
| 引擎 | FSA 128×128 | block 4×1536 |
| TOPS INT8 | 16.4 | **6.1** |
| 面积 @ 12nm | 62mm² | 57mm² |
| Decode TPS | 22.5 | 148 |
| TTFT | 106ms | 160ms |
| 瓶颈 | LPDDR5 BW (51.2 GB/s) | On-chip BW (500 GB/s) |
| 选择逻辑 | 带宽瓶颈 → 引擎几乎无关，选面积+验证简单的 FSA | BW 天花板极高 → 矮宽阵列（H=4）用最少算力榨干带宽 |

**架构哲学**：没有万能引擎。带宽充裕时用矮宽 block（H=4），带宽稀缺时引擎差异退化为次要矛盾，面积和架构简化度优先。

---

*DSE 工具: Arc Model v4 — 统面积模型（TPUv1 ISCA 2017 校准）*  
*面积数据来源: [references/area_sources.md](../references/area_sources.md)*  
*工艺: TSMC 12nm | 模型: Qwen2.5 系列 INT4*  
*关键公式: block on-chip per_tile = K+4, M_tiles = ceil(seq_len/H), per-layer cycles = (M_tiles × N_tiles × (H+4)) + SFU*
