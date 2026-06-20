# NPU 验证方案

## 验证形态

| 形态 | 全称 | 入口 | 覆盖范围 |
|------|------|------|---------|
| **Arc Model** | 架构验证 | `sim/arc_model.py` | 量化方案精度 + 性能 |
| **FM验证** | Func Model 验证 | `sim/func_model.py` | MMIO → DMA → MXU → 固件调度 |
| **E2E验证** | 端到端验证 | `sim/e2e_llamacpp.py` | Host CPU(hex) → DDR → 固件 → NPU → 输出 |

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
cd ~/npu && PYTHONPATH=. python3 sim/arc_model.py --model ~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf --scheme both
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
cd ~/npu && PYTHONPATH=. python3 sim/e2e_llamacpp.py --model ~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf --layers 2
```

## 验证门禁

新功能合入前必须通过的验证：

| 门禁 | 验证形态 | 要求 |
|------|---------|------|
| 量化方案精度 | Arc Model | cos_sim ≥ 0.96（全层） |
| 硬件链路正确 | FM 验证 | smoke test PASS |
| 全栈数据流 | E2E 验证 | 前 2 层 attention ops PASS |
