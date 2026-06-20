# NPU 验证方案

## 验证形态

| 形态 | 全称 | 入口 | 覆盖范围 |
|------|------|------|---------|
| **Arc Model** | 架构验证 | `sim/arc_model.py` | 量化方案精度 + 性能 |
| **FM验证** | Func Model 验证 | `sim/func_model.py` | MMIO → DMA → MXU → 固件调度 |
| **E2E验证** | 端到端验证 | `sim/e2e_llamacpp.py` | Host CPU(hex) → DDR → 固件 → NPU → 输出 |

## Model Zoo

### Transformer / LLM 类

| 模型 | 参数量 | Arc Model | FM 验证 | E2E 验证 | 备注 |
|------|:------:|:---------:|:------:|:-------:|------|
| Qwen2.5-1.5B | 1.5B | ✅ 0.990 | ✅ | ✅ 6/6 | 主力验证模型 |
| Qwen2.5-3B | 3B | 已配置 | — | — | arc_model.py 参数就绪 |
| Qwen2.5-7B | 7B | 已配置 | — | — | GGUF 已下载 |
| Qwen3-8B | 8B | 已配置 | — | — | GGUF 已下载 |
| Gemma-4-12B | 12B | 已配置 | — | — | GGUF 已下载 |

### CV 类（架构设计中，待落地验证）

来自 `docs/NPU软件架构方案v0.1.md` 明确的工作负载目标：`3B LLM / YOLOv8 / ResNet`。

| 模型 | 参数量 | 输入 | 关键算子 | 验证状态 | MXU 映射方式 |
|------|:------:|------|------|:------:|------|
| YOLOv8n | 3.2M | 640×640 | Conv2D + SiLU + Concat | 已规划 | im2col → MatMul |
| ResNet-18 | 11.7M | 224×224 | Conv2D + BN + ReLU + Residual | 已规划 | im2col → MatMul |
| ViT-Base | 86M | 224×224 | MatMul + Softmax + LayerNorm | 已规划 | 原生 MatMul（架构同 LLM） |
| MobileNetV3-S | 2.5M | 224×224 | Depthwise Conv + SE Block | 已规划 | im2col + Element-wise |
| EfficientNet-B0 | 5.3M | 224×224 | MBConv (DW + SE + PW) | 已规划 | im2col + MatMul |

**CV 验证待办**（与 LLM 验证的差异）：
1. **数据路径**：CV 模型不走 llama.cpp hex 协议，需要独立的 ONNX → IREE/自研 → NPU ISA 流程
2. **量化**：per-block INT4 已验证可行（LLM），CV Conv2D 需验证 im2col→matmul 后相同量化路径的精度
3. **FM 验证**：硬件链路（MXU/DMA/MIMO）复用现有验证，需新增 Conv2D golden reference
4. **Arc Model**：需扩展精度评估维度（mAP/Accuracy，不仅是 cos_sim）

**验证覆盖度说明**：
- **Arc Model**：`arc_model.py` 内置 5 LLM 架构参数，CV 模型需补充 ONNX 解析 + Conv2D 性能模型
- **FM 验证**：独立于模型，使用 Python 合成数据验证硬件链路，所有模型共享
- **E2E 验证**：LLM 通过 GGUF 路径，CV 需独立 ONNX→ISA 路径（IREE HAL 后端计划中）

**扩展计划**：LLM 新模型只需 `--model` 参数；CV 模型需 ONNX 模型文件 + 预处理脚本（待开发）。

本地 GGUF 可用列表（17 个，`~/models/`）：
```
qwen2.5-1.5b-instruct-q4_k_m.gguf    Qwen2.5-7B-Instruct-Q4_K_M.gguf
Qwen3-8B-Q4_K_M.gguf                  Qwen3-14B-Q4_K_M.gguf
Qwen3-30B-A3B-Instruct-2507-Q4_K_M    gemma-4-12B-it-Q4_K_M.gguf
qwen2.5-coder-7b-instruct-q4_k_m      ... (+ 10 more)
```

## Arc Model 验证

**目标**：架构决策前验证量化方案精度 + 性能。

**参数**：`--scheme per-channel|per-block|both`

**精度维度**：cos_sim gate（≥0.96 进入性能评估）

**性能维度**：decode tok/s, MXU utilization, DRAM stall

**Qwen2.5-1.5B 结论**：per-block (g=128) 胜出
- per-channel: mean_cos=0.9763, min=0.9001
- **per-block: mean_cos=0.9903, min=0.9707** ✅
- 性能: 43.3 tok/s, MXU 94.5% util

**运行**：
```bash
cd ~/npu/sim && PYTHONPATH=. /usr/bin/python3 arc_model.py --model ~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf --scheme both
```

## FM 验证

**目标**：验证硬件链路 bit-exact 正确性。

**数据来源**：Python 手工构造（无需 GGUF/llama.cpp）。

**覆盖**：
- MMIO Bridge 寄存器读写
- DMA DRAM ↔ SRAM 搬运（双通道 CH0/CH1）
- MXU per-block INT4 矩阵乘（含 ACCUMULATE 模式）
- 固件 tile 级双缓冲调度（tile_scheduler.py）
- AXI Trace 事务顺序验证

**当前状态**：✅ PASS
- 256×256 矩阵，2 K-blocks × 2 N-tiles
- 512KB SRAM
- 91 AXI 事务（DMA 55 + MXU 36）

**运行**：
```bash
cd ~/npu && PYTHONPATH=. python3 sim/func_model.py
```

## E2E 验证

**目标**：验证全栈数据流正确性（llama.cpp 视角）。

**数据来源**：GGUF 模型权重 → per-block INT4 量化 → tile-major 布局。

**流程**：
1. 加载 GGUF → 反量化 float32 → per-block INT4 量化
2. 打包为 tile-major 布局（匹配硬件 tile 级调度）
3. 模拟 llama.cpp 写 hex → DDR
4. Func Model 固件 tile 级调度执行
5. 输出对比 per-block golden matmul

**当前状态**：✅ PASS（2 层 × 3 ops = 6/6）

**踩坑记录**：
- DRAM 地址碰撞：weight/scale/output 区域必须分离，大矩阵 weight 可超 1MB
- DMA 双通道触发：CH0 和 CH1 共用一个 CMD，完成后必须清 SIZE 防误触发
- Descriptor 字段顺序：writer 和 reader 必须对齐（15 uint32）

**运行**：
```bash
cd ~/npu/sim && PYTHONPATH=. /usr/bin/python3 e2e_llamacpp.py --model ~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf --layers 2
```

## 验证门禁

新功能合入前必须通过的验证：

| 门禁 | 验证形态 | 要求 |
|------|---------|------|
| Spike 编译 | 固件构建 | `make -C firmware` + patch apply 通过 |
| 量化方案精度 | Arc Model | cos_sim ≥ 0.96（全层） |
| 硬件链路正确 | FM 验证 | smoke test PASS |
| 全栈数据流 | E2E 验证 | 前 2 层 attention ops PASS |

## 依赖构建

### Spike RISC-V 模拟器（patch 方式）

Spike 上游 `riscv-software-src/riscv-isa-sim` 通过 patch 集成 NPU 设备，不维护 fork。

```bash
# 初始构建（仅一次）
cd spike_src
bash ../patches/apply_spike_patches.sh .
mkdir build && cd build
../configure --prefix=$HOME/.local
make -j$(sysctl -n hw.ncpu)
make install

# 后续重新构建
cd spike_src/build && make -j$(sysctl -n hw.ncpu)
```

Patch 内容（`patches/` 目录）：
- `spike_npu.patch` — `sim.cc`（npu_factory 注册）+ `riscv.mk.in`（编译 npu_device.cc）+ `spike_main.mk.in`（注释修正）
- `npu_device.cc` — NPU MMIO 设备实现（RISC-V 端门铃寄存器）
