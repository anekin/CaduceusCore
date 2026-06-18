# CaduceusCore — 端侧 LLM 推理 NPU 协处理器

PCIe 协处理器形态，目标跑通 3B 参数大模型 @ 27+ tok/s（单核，INT4）。

## 架构概览

- **128×256 Weight-Stationary Systolic Array** + Weight Cache（参考 TPUv1/OpenTPU）
- **INT4 权重 + BF16 激活** 混合精度
- **五引擎设计空间搜索**：WS-Systolic / OS-Systolic (Gemmini) / Block (TPUv4) / TensorCore / Input-Stationary
- **RISC-V RV64 + 专用 NPU ISA** 主控
- **LPDDR5-6400** 64-bit + **PCIe Gen4 x4**
- **TSMC 12nm** 目标工艺

## 设计空间探索结论（v0.4）

| 引擎 | tok/s | 面积 | DRAM 利用率 |
|------|:---:|:---:|:---:|
| WS-Systolic 128×128 | 16 | 28mm² | 54% |
| WS-Systolic +WeightCache | 21 | 28mm² | 72% |
| **WS-Systolic 128×256 +WC** ✅ | **27** | **36mm²** | **92%** |
| OS-Systolic (Gemmini) | 29 | 52mm² | 100% |
| Block Engine (TPUv4) | 29 | 52mm² | 100% |
| TensorCore 64×16×16 | 28 | 52mm² | 98% |
| Block +DMA×2 (128b DRAM) | 58 | 53mm² | 100% |

**核心结论**：DRAM 带宽（50GB/s）是真正天花板，不是 NPU 引擎。Systolic 128×256 + WeightCache 在 36mm² 达到 92% DRAM 利用率——花 16mm² 换 OS/Block 的 2 tok/s 不值得。详见 `docs/NPU硬件详细架构设计v0.1.md` 第九章。

## 快速开始

```bash
cd ~/npu/sim
python3 npu_sim.py                    # 单点模拟
python3 design_space_explorer.py      # 五引擎设计空间搜索
```

## 项目结构

```
~/npu/
├── sim/                    # Python 性能模拟器
│   ├── npu_sim.py          # 主入口
│   ├── design_space_explorer.py  # 多引擎设计空间搜索
│   ├── hw_levels.py        # L0/L1/L2 三级对比
│   ├── engine/             # 五引擎 + PPA 模型
│   ├── models/             # MXU, SFU, DMA, KV Cache, DRAM 模型
│   └── config/             # NPU 架构配置文件
├── docs/                   # 设计文档
│   ├── NPU硬件详细架构设计v0.1.md  # 主架构文档（含设计空间探索）
│   ├── NPU软件架构方案v0.1.md
│   └── NPU系统级模拟器方案v0.1.md
└── rtl/                    # RTL (待开发)
```

## 设计理念

**模型即 Spec**：Python 性能模拟器是唯一事实来源。RTL 照着 simulator 的接口写，simulator 的 functional mode 做 golden reference 验证。

## License

MIT
