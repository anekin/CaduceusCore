# CaduceusCore — 端侧 LLM 推理 NPU 协处理器

PCIe 协处理器形态，目标跑通 3B 参数大模型 @ 21+ tok/s（单核，INT4）。

## 架构概览

- **128×128 Weight-Stationary Systolic Array + WeightCache**（参考 TPUv1/OpenTPU）
- **INT4 权重 + BF16 激活** 混合精度
- **七引擎设计空间搜索**：WS-Systolic / OS-Systolic (Gemmini) / Block (TPUv4) / TensorCore / WMMA / GMMA (H100) / Input-Stationary
- **RISC-V RV64 + 专用 NPU ISA** 主控
- **LPDDR5-6400** 64-bit + **PCIe Gen4 x4**
- **TSMC 12nm** 目标工艺

## 设计空间探索结论（v0.5 — 75% LPDDR5 实际效率）

| 引擎 | tok/s | 面积 | DRAM利用率(75%) | 判定 |
|------|:---:|:---:|:---:|------|
| **WS-Systolic 128×128+WC** ✅ | **21** | **28mm²** | **74%** | 推荐 |
| WS-Systolic 128×128 | 16 | 28mm² | 57% | 保守 |
| WS-Systolic 128×256+WC | 23* | 36mm² | 95% | DRAM临界 |
| OS-Systolic / Block / GMMA / IS | 28* | 48-60mm² | 110%+ | DRAM不可达 |

> *标星号为 75% DRAM 效率下实际可达值，名义模型预测更高但受带宽限制。

**核心结论**：LPDDR5 实际效率 75-80%（含刷新/行冲突/bank竞争），有效带宽 38.4 GB/s。在此约束下，128×128+WC 是唯一诚实的配置——21 tok/s @ 28mm²，DRAM 余量充裕。大引擎（128×256+WC/Block/GMMA）的算力优势被 DRAM 带宽全部吃掉，多花的面积得不到回报。详见 `docs/NPU硬件详细架构设计v0.1.md`。

## 快速开始

```bash
cd ~/npu/sim
python3 npu_sim.py                    # 单点模拟
python3 design_space_explorer.py      # 七引擎设计空间搜索
```

## 项目结构

```
~/npu/
├── sim/                    # Python 性能模拟器
│   ├── npu_sim.py          # 主入口
│   ├── design_space_explorer.py  # 多引擎设计空间搜索
│   ├── hw_levels.py        # L0/L1/L2 三级对比
│   ├── sw_overhead_eval.py # 软件开销评估
│   ├── engine/             # 七引擎 + PPA 模型
│   ├── models/             # MXU, SFU, Vector, DMA, KV, DRAM, SW overhead
│   └── config/             # NPU 架构配置文件
├── docs/                   # 设计文档
│   ├── NPU硬件详细架构设计v0.1.md  # 主架构文档（含设计空间探索）
│   ├── NPU软件架构方案v0.2.md      # 两阶段方案：llama.cpp → ExecuTorch
│   ├── NPU系统级模拟器方案v0.1.md
│   └── NPU_Engines_Architecture_Guide.md  # 七引擎架构全景
└── rtl/                    # RTL (待开发)
```

## 软件栈方案（v0.2）

```
阶段 1 (现在, 4-8周): GGUF → llama.cpp → ggml NPU backend → Python Model
阶段 2 (RTL后, 6-12周): PyTorch → ExecuTorch NPU Delegate → NPU 硬件
```

详情见 `docs/NPU软件架构方案v0.2.md`。

## 设计理念

**模型即 Spec**：Python 性能模拟器是唯一事实来源。RTL 照着 simulator 的接口写，
simulator 的 functional mode 做 golden reference 验证。

**三级验证体系**：
- **Arc Model** (`sim/arc_model.py`)：量化方案精度 gate + 性能评估
- **FM 验证** (`sim/func_model.py`)：硬件链路 bit-exact（✅ PASS）
- **E2E 验证** (`sim/e2e_llamacpp.py`)：llama.cpp → Func Model 全栈

详见 `docs/verification_methodology.md`。

## 量化方案

**当前：INT4 per-block (g=128)**。经 Arc Model 对比：
- per-channel: mean_cos=0.9763, min=0.9001
- **per-block: mean_cos=0.9903, min=0.9707** ✅

硬件规格：128×128 systolic, weight-stationary, PE 双 weight 寄存器。
Tile 级调度：K-block × N-tile 双循环，512KB SRAM，8KB weight tile + 512B scale tile per DMA。

## Lessons Learned

### LL-001: 性能模型不等于精度验证（2026-06）

**问题**：INT4 全局 scale 方案在 Func Model 首次跑真实 GGUF 权重时发现完全不可用（rel_err 10³-10⁴）。

**根因**：
- Layer 1 时序模型（`mxu.py`/`systolic_engine.py`）只把 `weight_precision_bits=4` 当作字节数计算的除数。从未用真实 Transformer 权重做 INT4 量化→矩阵乘→对比验证。
- Layer 2 软件协议（`npu_server.py`）走 float32 反量化路线，绕过了硬件 INT4 通路。
- 时序假设被默认当成了「已验证的精度方案」。

**教训**：
> 在搭建任何时序模型之前，先建数值模型验证精度方案能不能跑通。时序模型只回答「多快」，不回答「对不对」。

**修复**：
1. Func Model (`golden_executor.py`) 承担数值模型角色——任何量化方案改动必须先在这里做 bit-exact 验证。
2. 新增验证门禁：新量化方案必须通过 `eval_models.py` 的 Qwen/Gemma 跨层对比（all-layer PASS）才能进入时序评估。
3. 后续 RTL 开发时，Func Model 输出直接作为 `$readmemh` golden reference。

## License

MIT
