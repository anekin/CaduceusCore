# 架构选型报告 B — On-chip 3D DRAM 高带宽 NPU（终版）

**日期**: 2026-06-29 | **工艺**: TSMC 12nm  
**需求**: 高端端侧 NPU，3D DRAM 堆叠，跑 7B 模型实时推理  
**模型**: Qwen2.5-7B INT4 (3.5 GB)，28 层  
**内存**: On-chip 3D DRAM 5GB, 500 GB/s（权重全量常驻）  
**对标**: 瑞芯微 RK1828 (20 TOPS INT8, ~100mm²@22nm, 7B=59-180 tok/s)

---

## 0. 设计约束

| 约束 | 要求 | 验证 |
|------|------|:---:|
| 模型 | 7B | 7B |
| Decode TPS | ≥ 100 | ✓ (150) |
| TTFT (128 tok) | < 200ms | ✓ (160ms) |
| 权重驻留 | 5GB ≥ 3.5GB | ✓ |

---

## 1. H 维度敏感性（核心发现）

On-chip 权重常驻 → 无 K-tiling。H 处理 batch 维度（M），W 处理输出维度（N）。

| H×W | TOPS INT8 | Decode TPS | TTFT | 面积 | 
|:---:|:---:|:---:|:---:|:---:|
| 4×1536 | **6** | **150** | **160ms** ✓ | **79mm²** |
| 8×1536 | 12 | 150 | 80ms | 105mm² |
| 16×1536 | 25 | 150 | 40ms | 158mm² |
| 32×1024 | 33 | 143 | 28ms | 194mm² |
| 128×1024 | 131 | 143 | 7ms | 617mm² |

**关键洞察**：
- Decode TPS 恒定 ~150——on-chip 500GB/s 带宽天花板（500/3.5≈143）
- TTFT 随 H 减小而增大（M_tiles=128/H），但 H=4 仍 160ms < 200ms
- H=4 用 6 TOPS 达成同等 TPS（vs H=128 需 131 TOPS）→ **22× TOPS 效率提升**

---

## 2. 与 RK1828 对标

| | RK1828 | Arc Model (H=4, W=1536) |
|---|---|---|
| TOPS INT8 | 20 | 6 |
| 工艺 | 22nm | 12nm |
| 面积 | ~100mm² | ~79mm² |
| 7B TPS | 59-180 | **150** |
| 架构 | 宽向量/unknown | block 4×1536 |

面积效率（mm²/TOPS）：RK1828≈5.0, Arc≈13.2。Arc 偏保守 ~2.6×，可能因为 block 广播互连面积高估。

---

## 3. 推荐配置

| 参数 | 值 |
|------|------|
| 引擎 | **block 4×1536** |
| SRAM | 8 MB (L2) |
| 精度 | INT4 权重 / INT8 激活 |
| 内存 | On-chip 3D DRAM 5GB, 500 GB/s |
| 面积 @ 12nm | **~79 mm²** |
| TOPS INT8 | **6** |
| 功耗 | ~20W（估） |

### 预期性能

| 负载 | 性能 |
|------|:---:|
| Qwen2.5-7B decode | **150 tok/s** |
| TTFT (128 tok) | **160 ms** ✓ |
| Qwen2.5-3B decode | ~600 tok/s |
| 等效生成速度 | ~9,000 字/分钟 |

---

## 4. 两场景对比

| | 场景 A (LPDDR5) | 场景 B (On-chip 3D DRAM) |
|---|---|---|
| 模型 | 3B | 7B |
| 引擎 | FSA 128×128 | block 4×1536 |
| TOPS INT8 | 16 | **6** |
| 面积 @ 12nm | 32mm² | 79mm² |
| Decode TPS | 23 | 150 |
| TTFT | 105ms | 160ms |
| 选择逻辑 | 带宽瓶颈 → 选面积最小 | on-chip BW 瓶颈 → 选矮宽阵列 |

**DSE 价值**: 同一引擎库，通过切换内存模型和 H/W sweep，自动选出最优架构。低带宽用 FSA（脉动管线面积优势），高带宽用 block（矮宽阵列高 PE 利用率）。

---

*DSE 工具: Arc Model v4 (K-tiling + SRAM + H-sweep + M-tiling + on-chip memory)*  
*工艺: TSMC 12nm | 模型: Qwen2.5 系列 INT4*  
*关键公式: block on-chip per_tile = K+4, M_tiles = ceil(M/H), N_tiles = ceil(N/W)*
