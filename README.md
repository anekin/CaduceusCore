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

**模型即 Spec**：Python 性能模拟器是唯一事实来源。RTL 照着 simulator 的接口写，simulator 的 functional mode 做 golden reference 验证。

## License

MIT
