# CaduceusCore — 端侧 LLM 推理 NPU 协处理器

PCIe 协处理器形态，目标跑通 3B 参数大模型 @ 25+ tok/s。

## 架构概览

- **128×128 Weight-Stationary Systolic Array**（参考 TPUv1/OpenTPU）
- **INT4 权重 + BF16 激活** 混合精度
- **RISC-V RV64 + 专用 NPU ISA** 主控
- **LPDDR5-6400** 64-bit + **PCIe Gen4 x4**
- **TSMC 12nm** 目标工艺

## 项目结构

```
~/npu/
├── sim/                    # Python 性能模拟器 (Phase 1-2)
│   ├── npu_sim.py          # 主入口
│   ├── models/             # MXU, SFU, DMA, KV Cache, DRAM 模型
│   ├── engine/             # 事件驱动时间轴引擎
│   └── config/             # NPU 架构配置文件 (spec)
├── docs/                   # 设计文档
│   ├── NPU硬件详细架构设计v0.1.md
│   ├── NPU软件架构方案v0.1.md
│   └── NPU系统级模拟器方案v0.1.md
└── rtl/                    # RTL (待开发)
```

## 快速开始

```bash
cd ~/npu/sim
python3 npu_sim.py
```

输出示例：
```
Decode: 275.7 μs → 3,628 tok/s ✅
Prefill: 66.6 ms (128 tokens)
MXU 59.1% | KV Cache 22.1% | SFU 12.0% | DMA 6.7%
```

## 设计理念

**模型即 Spec**：Python 性能模拟器是唯一事实来源。RTL 照着 simulator 的接口写，simulator 的 functional mode 做 golden reference 验证。

## License

MIT
