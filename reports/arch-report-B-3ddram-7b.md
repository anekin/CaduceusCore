# 架构选型报告 B — On-chip 3D DRAM 高带宽 NPU

**日期**: 2026-07 | **工艺**: TSMC 12nm  
**需求**: 高端端侧 NPU，3D DRAM 堆叠，跑 7B 模型实时推理  
**模型**: Qwen2.5-7B INT4 (3.5 GB)，28 层  
**内存**: On-chip 3D DRAM 5GB, 500 GB/s（权重全量常驻，TSV 互联）  
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

## 1. H×W 维度扫描

On-chip 权重常驻 → 无 K-tiling。H 处理 batch 维度（M），W 处理输出维度（N）。

| H×W | TOPS INT8 | TPS | TTFT | 面积 | 达标 |
|:---:|:---:|:---:|:---:|:---:|:---:|
| **4×1536** | **6.1** | **148** | **160ms** | **69.1mm²** | ✓ |
| 8×1536 | 12.3 | 148 | 80ms | 73.9mm² | ✓ |
| 16×1536 | 24.6 | 148 | 40ms | 83.6mm² | ✓ |
| 32×1024 | 32.8 | 142 | 28ms | 90.1mm² | ✓ |
| 32×1536 | 49.2 | 148 | 20ms | 103.0mm² | ✓ |
| 64×1024 | 65.5 | 142 | 14ms | 115.9mm² | ✓ |
| 128×512 | 65.5 | **78** | 13ms | 115.9mm² | ✗ TPS |

**核心洞察**：

1. **Decode TPS 恒定 ~148 tok/s**——on-chip 500GB/s 带宽天花板（500/3.5≈143，加 KV cache overhead~148）。7B 模型下算力严重过剩。

2. **TTFT 随 H 减小而增大**（prefill 需 M_tiles=128/H），但 H=4 时 TTFT=160ms 仍 < 200ms ✓

3. **H=4 用 6.1 TOPS 达成 148 TPS，而 H=128 需 65.5 TOPS 仅 78 TPS**——高 H 的 per-tile 固定开销（H+4=132 cycles）远大于矮 H（4+4=8 cycles）。→ **矮宽阵列 ≈ 24× TOPS 效率提升**

4. **128×512 不及格**：H 过高导致 per-tile overhead 致命，W 过小导致 N_tiling 8 次。矮宽（H≤16, W≥1024）是正确范式。

---

## 2. 面积分解 (block 4×1536 @12nm)

| 组件 | 面积 | 占比 |
|------|:---:|:---:|
| PE 阵列 (6,144 MACs) | 4.4mm² | 6.4% |
| SRAM L1+L2 (8MB) | 36.1mm² | **52.3%** |
| PCIe Gen4 ×4 | 5.9mm² | 8.5% |
| SFU + RISC-V + DMA + Crossbar | 16.3mm² | 23.6% |
| TSV overhead (10%) | 6.3mm² | 9.1% |
| DRAM PHY | 0mm² | — (on-chip 免 DDR) |

> **8MB SRAM 占 52%，TSV 占 9%，PCIe 占 9%。** SRAM 是最大成本项。  
> 面积 = (PE + SRAM + PCIe + 固定) × (1 + 10% TSV) = 62.8 × 1.10 = 69.1mm²

---

## 3. 与 RK1828 对标

| | RK1828 | Arc Model (H=4, W=1536) |
|---|---|---|
| TOPS INT8 | 20 | 6.1 |
| 工艺 | 22nm | 12nm |
| 面积 | ~100mm² | **~69mm²** |
| 7B TPS | 59-180 | **148** |
| mm²/TOPS | 5.0 | 11.3 |
| mm²/TPS | 0.56-1.69 | **0.47** |

**分析**：
- Arc mm²/TPS=0.47 优于 RK1828 区间中点（~0.84），有成本竞争力
- mm²/TOPS=11.3 看起来差，但因为 on-chip BW 瓶颈下算力过剩，TOPS 不是有效指标
- Arc 面积（69mm²）比 RK1828 折算 12nm ≈ 30mm² 差。差距主要来自：SRAM 预算（Arc 8MB vs RK 未知但可能更小）+ TSV 10%

---

## 4. 推荐配置

| 参数 | 值 |
|------|------|
| 引擎 | **block 4×1536** (broadcast output stationary) |
| SRAM | 8 MB (L2)，优化空间大 |
| 精度 | INT4 权重 / INT8 激活 |
| 内存 | On-chip 3D DRAM 5GB, 500 GB/s, TSV 互联 |
| PCIe | Gen4 ×4（主机通信） |
| 面积 @ 12nm | **~69 mm²** |
| TOPS INT8 | **6.1** |
| 功耗 | ~14W（估） |

### 预期性能

| 负载 | 性能 |
|------|:---:|
| Qwen2.5-7B decode | **148 tok/s** |
| TTFT (128 tok) | **160 ms** ✓ |
| Qwen2.5-3B decode | ~600 tok/s |

### 面积优化路径

| 优化 | 面积 | TPS | 备注 |
|------|:---:|:---:|------|
| 当前基线 | 69mm² | 148 | 8MB SRAM + PCIe + TSV 10% |
| SRAM 4MB | ~46mm² | 148¹ | 节省 22mm² |
| 7nm 工艺 | ~24mm² | 148 | (12/7)²=2.94× 缩放 |
| 7nm + SRAM 4MB | ~16mm² | 148 | 极致成本 |

> ¹ 待验证 KV cache 在 4MB SRAM 下的竞争影响

---

## 5. 两场景对比

| | 场景 A (LPDDR5) | 场景 B (On-chip 3D DRAM) |
|---|---|---|
| 模型 | 3B | 7B |
| 引擎 | FSA 128×128 | block 4×1536 |
| TOPS INT8 | 16.4 | **6.1** |
| 面积 @ 12nm | 62mm² | 69mm² |
| Decode TPS | 22.5 | 148 |
| TTFT | 106ms | 160ms |
| 瓶颈 | LPDDR5 BW (51.2 GB/s) | On-chip BW (500 GB/s) |
| PCIe | ✓ | ✓ |
| DRAM PHY | ✓ (14.7mm²) | ✗ (TSV 6.3mm² 替) |

**架构哲学**：没有万能引擎。带宽充裕时用矮宽 block（H=4），带宽稀缺时引擎差异退化为次要矛盾。TSV 替代 DDR PHY 在 12nm 下净省 ~8.4mm²。

---

*DSE 工具: Arc Model v4 — 统面积模型（TPUv1 ISCA 2017 校准）*  
*面积数据来源: [references/area_sources.md](../references/area_sources.md)*  
*工艺: TSMC 12nm | 模型: Qwen2.5 系列 INT4*  
*3D 堆叠: TSV overhead 10% (keep-out zone + SerDes + 冗余)，PCIe 保留（主机通信）*
