# NPU 架构复盘 — 软件栈 / 模型 / 性能评估流程

> 项目：CaduceusCore (~/npu/) + llama.cpp NPU backend (~/llama.cpp/)
> 目标：3B-7B LLM decode，128×128 systolic @1GHz, LPDDR5-6400
> 更新：2026-06-19

---

## 1. 整体架构：三层栈

```
┌──────────────────────────────────────────────────┐
│  Layer 3: 应用层                                  │
│  llama.cpp → ggml-npu backend (C++)              │
│  识别 MUL_MAT → 写 hex → 等结果 → 回填 GGML buf   │
├──────────────────────────────────────────────────┤
│  Layer 2: 协议层                                  │
│  npu_server.py ←→ hex 文件                       │
│  反量化 Q4_K/Q6_K → numpy.matmul → 写 hex        │
│  verify_hex.py (离线验证)                         │
├──────────────────────────────────────────────────┤
│  Layer 1: 模拟器层                                │
│  npu_sim.py → systolic_engine.py → mxu.py        │
│  DRAM/DMA/SFU/Vector/KV Cache 全模块              │
│  eval_models.py (端到端 tok/s 评估)               │
└──────────────────────────────────────────────────┘
```

**数据流**：llama.cpp (C++) 写 `act_N.hex` → Python server 读取、反量化、算 MUL_MAT → 回写 `out_N.hex` → llama.cpp 读取回填。

**关键设计决策**：
- hex 协议实现 C++/Python/RTL 完全解耦（RTL 可用 `$readmemh` 直接读）
- 仅权重 MUL_MAT 走 NPU；KV cache（`cache_*`）和 contiguous 副本（`*_cont`）留 CPU
- Weight Stationary 数据流 + `weight_cache=ON`（PE 双 weight 寄存器，乒乓加载）
- **INT4 per-block 量化** (g=128)：预 Arc Model 对比 per-channel vs per-block，per-block 胜出（cos_sim +0.014）
- **Tile 级双缓冲 SRAM 调度**：固件按 K-block × N-tile 双循环，每次只 DMA 一个 tile (8KB weight + 512B scale) 到 SRAM

## 量化方案演进（2026-06）
| 阶段 | 方案 | 发现 | 决策 |
|------|------|------|------|
| v0.1 | INT4 全局 scale | Arc Model 验证：rel_err 10³-10⁴，不可用 | 废弃 |
| v0.2 | per-channel | cos_sim 0.976, 个别层跌至 0.90 | 备选 |
| v0.3 | **per-block (g=128)** | cos_sim 0.990, 最弱层 0.971 | ✅ 当前 |

## 验证体系
| 形态 | 入口 | 覆盖 |
|------|------|------|
| Arc Model | `sim/arc_model.py --scheme both` | 量化方案精度 + 性能 |
| FM 验证 | `sim/func_model.py` | 硬件链路 bit-exact |
| E2E 验证 | `sim/e2e_llamacpp.py` | llama.cpp → Func Model 全栈 |

---

## 2. NPU 硬件模型 (Layer 1: Simulator)

### 2.1 模块清单

| 模块 | 文件 | 职责 |
|------|------|------|
| **MXU** | `sim/engine/systolic_engine.py` | 核心 GEMM 引擎：tile 分解 + pipeline fill/drain 建模 |
| **MAC Engine 基类** | `sim/engine/mac_engine.py` | 统一接口 `estimate(M,K,N)`，计算 eff_bw, peak_macs |
| **DRAM** | `sim/models/dram.py` | LPDDR5-6400 时序：refresh 5.4%, row conflict, effective BW |
| **DMA** | `sim/models/dma.py` | 双通道 descriptor DMA：burst overhead, 重叠计算 |
| **SFU** | `sim/models/sfu.py` | 标量函数单元：softmax/layernorm/gelu/silu/rope 等 14 种 |
| **Vector** | `sim/models/vector.py` | SIMD 逐元素操作：add/mul/scale/bias/relu/mask |
| **KV Cache** | `sim/models/kv_cache.py` | SRAM 256KB + DRAM 96MB 区域 |
| **Compiler** | `sim/engine/compiler.py` | 将 GEMM (M,K,N) 映射为 tile 序列，调度 MXU+SFU+Vector |
| **Multicore** | `sim/engine/multicore.py` | 多核并行 |
| **PPA** | `sim/engine/ppa_model.py` | 面积/功耗模型（TSMC 12nm 基线） |

### 2.2 引擎矩阵

7 种 MAC 引擎可插拔：

| 引擎 | 类型 | Pipeline 开销 | 面积 | 适用场景 |
|------|------|:--:|------|------|
| **systolic** (当前) | Weight-stationary | fill+drain 385c/tile | 8mm² | M=1 decode |
| block | 全并行 MAC | 无 | 32mm² | M≥4 预填充 |
| gmma | NVIDIA 风格 Tensor Core | 中 | 中 | 混合精度 |
| wmma | AMD 风格 | 中 | 中 | 混合精度 |
| is_systolic | Input-stationary | 中 | ~8mm² | 激活常驻 |
| os_systolic | Output-stationary | 中 | ~8mm² | 部分和积累 |
| tensor_core | 通用 TC | 中 | 中 | 通用 |

### 2.3 配置参数

```yaml
# sim/config/npu_config.yaml (核心)
mxu:
  array_height: 128
  array_width: 128
  frequency_mhz: 1000
  weight_precision_bits: 4      # INT4
  dataflow: weight_stationary
  weight_cache: true              # PE 双 weight 寄存器

sram:
  l1_per_core_kb: 512
  l2_shared_kb: 2048             # 总计 2.5 MB

memory:
  type: LPDDR5-6400
  bandwidth_gbps: 51.2
  dram_efficiency: 0.85          # 有效 43.5 GB/s
```

---

## 3. 软件栈 (Layer 2+3: Hex 协议 + llama.cpp)

### 3.1 ggml-npu backend (llama.cpp)

**位置**：`~/llama.cpp/ggml/src/ggml-npu/`

```
ggml-npu.cpp     # 后端主逻辑：识别 MUL_MAT → 写 hex → 等结果
ggml-npu.h       # 接口声明
npu_server.py    # Python 服务：反量化 + matmul + 验证
verify_hex.py    # 离线验证脚本
q4_dequant.py    # Q4_K/Q6_K 向量化反量化实现
```

**流程**：
1. `ggml_npu_compute_forward()` 遍历计算图
2. 对每个 MUL_MAT：跳过 `cache_*` 和 `*_cont` 张量 → CPU 自处理
3. 写权重 hex（一次性）+ 激活 hex（每 batch）
4. 等待 Python server 处理完毕（`DONE` 信号文件）
5. 读 `out_N.hex` 回填 GGML 输出 buffer

### 3.2 Hex 协议

```
每 batch 目录:
  batch_N/
  ├── manifest.json    # 操作描述（M,K,N, weights_path）
  ├── act_N.hex        # 激活数据（INT8 hex）
  ├── out_N.hex        # 输出数据（FP32 hex，server 写入）
  ├── READY            # C++ 写完信号
  └── DONE             # Python 处理完信号
```

**验证**：
- `npu_server.py` 内置 `_verify_result()` → 每次 MUL_MAT 做 hex round-trip 对照
- `verify_hex.py` 离线：独立加载 hex → 重新 dequant → np.dot 对照 → 全部 PASS（max diff <1e-5）

---

## 4. 性能评估流程

### 4.1 快速评估

```bash
cd ~/npu/sim
python3 eval_models.py    # 对 1.5B/3B/7B 跑端到端 tok/s 评估
```

### 4.2 设计空间探索

```bash
python3 design_space_explorer.py --quick    # 36 配置
python3 design_space_explorer.py --top 30   # ~2400 配置
python3 hw_levels.py                        # 三级对比
```

### 4.3 专项瓶颈分析

```bash
python3 bottleneck_analysis.py      # 单 tile 开销分解
python3 weight_cache_eval.py        # weight_cache 收益
python3 dma_improvement_eval.py     # DMA 改进评估
python3 sw_overhead_eval.py         # 软件开销
python3 param_sweep_v2.py           # 参数扫描
```

### 4.4 自动化闭环

```bash
cron: NPU Overnight Auto-Fix Loop (每 2 小时)
  1. 跑 overnight_loop.py
  2. 检查 issues_found / issues_fixed
  3. 更新 results/morning_summary.md
  4. 已稳定运行 26 轮，零残留 issue
```

---

## 5. 当前性能基线 & 瓶颈

### 5.1 端到端 tok/s (Arc Model Zoo DSE 实测)

| 模型 | M=1 tok/s | M=2 tok/s | 芯片约束 tok/s (≤12W, ≤40mm²) | 是否达标 |
|------|:---:|:---:|:---:|:---:|
| Qwen2.5-1.5B | 1986.2 | 1982.9 | 124.7 | ✅ PASS (≥25) |
| Qwen2.5-3B | 984.4 | 980.9 | 60.2 | ✅ PASS (≥20) |
| Qwen2.5-7B | 415.8 | 415.0 | 25.1 | ✅ PASS (≥20) |
| Qwen3-8B | 448.1 | 446.8 | 27.1 | ✅ PASS (≥20) |
| Gemma-4-12B | 269.8 | 268.9 | 16.3 | ❌ FAIL (<20) |

### 5.2 瓶颈金字塔

```
┌──────────────┐
│ 有用 MAC     │ 1 cycle/tile (0.3%)
│ Pipeline     │ 385c/tile (67%) ← 第一瓶颈
│ DMA          │ 191c/tile (33%) ← 距物理天花板仅 7%
└──────────────┘
```

**核心结论**：Pipeline fill+drain (256+129=385c) 占每 tile 67% 时间。Pipeline 和 DRAM 是**联合瓶颈** — 只优化一个，另一个立刻成新墙。

### 5.3 两条达标路径

| 方案 | 配置 | tok/s | 面积 | 代价 |
|------|------|:--:|------|------|
| A: 阵列加宽 | 128×256 | 25 | 42mm² | 面积 +3.5× |
| B: 连续批处理 | 128×128 M=2 | 31 | 27mm² | 延迟换吞吐 |

---

## 6. 文件索引

### 模拟器核心
```
~/npu/sim/
├── npu_sim.py                 # CLI 入口
├── eval_models.py              # 端到端模型评估
├── design_space_explorer.py    # 设计空间搜索
├── bottleneck_analysis.py      # 瓶颈分析
├── overnight_loop.py           # 自动化闭环
├── validate_e2e.py             # 端到端验证
├── engine/                     # 7 种引擎实现
├── models/                     # DRAM/DMA/SFU/Vector/MXU/KV Cache
├── config/                     # npu_config.yaml, design_space.yaml
└── reports/                    # bottleneck_analysis.md 等
```

### 软件协议层
```
~/npu/ggml-npu/
├── ggml-npu.cpp                # llama.cpp backend
├── npu_server.py               # Hex 协议 Python 端
├── verify_hex.py               # 离线验证
└── q4_dequant.py               # 量化反量化

~/llama.cpp/ggml/src/ggml-npu/  # (正式版)
```

### 设计文档
```
~/npu/
├── ENGINES.md                  # 七引擎全景
├── README.md
├── docs/
│   ├── NPU硬件详细架构设计v0.1.md
│   ├── NPU系统级模拟器方案v0.1.md
│   └── NPU软件架构方案v0.2.md
└── scale-sim-v3/               # Scale-Sim 参考实现
```

---

## 7. 已踩坑 & 经验

| 坑 | 教训 |
|----|------|
| weight_cache 默认关 | 误判 3B 性能为 14.8 tok/s，实际 19.9。现全部配置默认 ON |
| "Weight Stationary" ≠ 全模型驻留 | 2.5MB SRAM 装不下 1.7GB 权重，每 token 必须从 DDR 重搬 |
| DRAM "22% 余量"是错的 | 分母已打折，真实余量仅 7%，因为 DRAM 物理天花板 85% |
| Pipeline/DMA 是联合瓶颈 | 只优化一个会被另一个卡住，必须同时动 |
| 性能模型不做无证据推断 | 每个结论必须有 config + model trace 双验证 |
| 报告硬编码常数会导致 10-100× 偏差 | 所有资源估算必须从 config 和 trace 动态计算 |

---

## 8. Arc Model Zoo 评测结果

基于 `eval_model_zoo.py` 对 5 个 LLM 模型完成 7 引擎 × 7 阵列 × 6 DRAM × 2 精度 × 3 频率的完整 DSE，关键结论：

- **3B 目标 (20-25 tok/s)**: Qwen2.5-3B 在芯片约束（≤12W, ≤40mm²）下达到 60.2 tok/s (M=1)，无约束 M=2 最佳可达 980.9 tok/s。
- **CV 能力**: MobileNetV3-Small Arc Model DSE 最佳 systolic 497.6 tok/s-equivalent，tensor_core 64×64 HBM3 下达到 1243.3 tok/s。
- **完整报告**: `results/model_zoo/model_zoo_ppa_report.md`
