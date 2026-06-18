# NPU 架构设计空间 — 多引擎对比参考

> 自研 Python simulator 建模 7 种矩阵乘法引擎 × 6 级 DRAM 带宽。
> 目标：3B 参数 LLM decode 推理，找到 tok/s vs mm² 的最优 trade-off。

## 快速使用

```bash
cd ~/npu/sim

# 列出支持的引擎
python3 npu_sim.py --list-engines

# 列出 DRAM 带宽预设
python3 npu_sim.py --list-dram

# 单点模拟（CLI 覆盖配置）
python3 npu_sim.py --engine gmma --dram 100 --array 128x256

# 设计空间搜索（多引擎多配置扫参数）
python3 design_space_explorer.py --quick    # 快速：36 配置
python3 design_space_explorer.py --top 30   # 完整：~2400 配置

# 三级对比（L0基线 L1优化 L2换引擎）
python3 hw_levels.py
```

## 七种引擎

| 引擎 | CLI 值 | 数据流 | 参考 | 特征 |
|------|--------|--------|------|------|
| WS-Systolic | `systolic` | Weight-Stationary | TPUv1 | pipeline fill/drain ~385c/tile |
| OS-Systolic | `os_systolic` | Output-Stationary | Gemmini | 零 pipeline overhead |
| Block Engine | `block` | 全并行 MAC | TPUv4 VMU | 1 cycle/tile，无流水线 |
| Tensor Core | `tensor_core` | 64×16×16 块 | A100 | 小块并行，DMA 碎片化 |
| WMMA | `wmma` | 16×16 warp MMA | Volta/Ampere | **M=1 灾难**，仅 GPU warp 隐藏 |
| GMMA | `gmma` | 128×128×32 + TMA | Hopper H100 | 异步 DMA 重叠 |
| Input-Stationary | `input_stationary` | Input-Stationary | Eyeriss | 权重广播，适合大 batch |

## DRAM 配置

| 预设 | 带宽 | 位宽 | 典型内存 |
|------|------|------|----------|
| `25` | 25.6 GB/s | 32-bit | LPDDR5 低端 |
| `50` | 51.2 GB/s | 64-bit | LPDDR5-6400（当前基线）|
| `100` | 102.4 GB/s | 128-bit | LPDDR5 双通道 |
| `200` | 204.8 GB/s | 256-bit | LPDDR5 四通道 |
| `460` | 460 GB/s | 1024-bit | HBM2e |
| `819` | 819 GB/s | 1024-bit | HBM3 |

## 核心结论

### 不改 DRAM（50 GB/s）

```
Architecture              tok/s   面积     tok/mm²  说明
──────────────────────────────────────────────────────────
Systolic 128×128           16     28mm²   0.57    baseline
Systolic 128×128 +WC       21     28mm²   0.75    +PE dual register
Systolic 128×256 +WC       27     36mm²   0.75    ✅ 推荐
OS-Systolic                31     52mm²   0.60    大引擎
Block Engine               32     52mm²   0.62    大引擎
GMMA                       32     60mm²   0.53    TMA 无助于 DRAM 墙
Tensor Core                28     52mm²   0.54    小块 DMA 碎片
Input-Stationary           31     48mm²   0.65    大 batch 友好
WMMA                        7     57mm²   0.12    ☠️ 碎片杀
──────────────────────────────────────────────────────────
```

**结论：** LPDDR5 实际效率 75-80%（含刷新/行冲突/bank 竞争），有效带宽 38.4-41.0 GB/s。在此约束下，128×128+WC 是唯一诚实的配置——21 tok/s @ 28mm²，DRAM 利用率 74%，留有余量。128×256+WC 的名义 27 tok/s 在实际效率下掉到 ~23 tok/s（95% 利用率），多花的 8mm² 边际收益近乎为零。Block/OS/GMMA/IS 的 31-32 tok/s 在 75% 效率下需要 42.4 GB/s，超过物理上限，实际会被 DRAM 卡在 ~28 tok/s，多花的 20-32mm² 全部浪费。WMMA 的 16×16 小块在 M=1 decode 下产生灾难性 DMA 碎片——这是 GPU warp 级并行才能掩盖的开销，单 die NPU 无法复制。

### 扩 DRAM（100 GB/s+）

```
Architecture           DRAM       tok/s   面积    tok/mm²
──────────────────────────────────────────────────────────
Block 128×128         LPDDR5-128b    63     59mm²   1.07
GMMA 128×128          LPDDR5-128b    63     67mm²   0.94   ← TMA 未体现优势
GMMA 128×128          LPDDR5-256b   240     74mm²   3.24   ← TMA 生效
Block 128×128         HBM3          942    133mm²   7.08   ← 天花板换到 compute
```

**结论：** DRAM >100GB/s 后 GMMA 的 TMA 异步重叠价值体现。DRAM >200GB/s 后瓶颈从带宽转到 compute fabric（阵列 MAC 数不够了）。

### WMMA 为什么不行

```
M=1 decode, Q_proj (M=1,K=2560,N=4096), 50GB/s DRAM:

Block:     40×64 = 2,560 tiles  →  32 tok/s
TensorCore: 64× more waves       →  28 tok/s
WMMA:       16× more DMA starts  →   7 tok/s  ☠️

根因：16×16 tile × 10c DMA startup × 100K+ invocations
     = 百万级 cycles 的启动开销
```

**NVIDIA 的做法：** 数千个 warp 同时跑 → DMA 启动开销被 warp 切换隐藏。单 die NPU 只有 1 个指令流 → 启动开销等于纯等。

## 模型即 Spec

Python 性能模拟器是唯一事实来源。RTL 开发按 simulator 接口写，simulator functional mode 做 golden reference。

## License

MIT
