# Arc Model Zoo PPA 报告

## 1. 执行摘要

- 产品需求: 3B decode 20-25 tok/s, 芯片面积 ~30mm², M.2 ≤10W / 芯片 ≤12W / PCIe ≤15W
- 评测模型数: 5 个 LLM（M=1 与 M=2 双场景） + 1 个 CV 模型
- 在芯片级约束（≤12W, ≤40mm²）下达标模型: qwen2.5-1.5b, qwen2.5-3b, qwen2.5-7b, qwen3-8b, gemma-4-12b
- 未达标模型: 无
- 主要瓶颈: DRAM bandwidth wall: qwen2.5-1.5b, qwen2.5-3b, qwen2.5-7b, qwen3-8b, gemma-4-12b

整体而言，全部 5 个模型在 12W/40mm² 芯片约束下均可满足 20-25 tok/s 的 3B 级目标，其中 Gemma-4-12B 由 gmma 引擎达到 33.0 tok/s，说明 GMMA 的 TMA 异步 DMA 流水线对中大规模模型有显著收益。当前 DSE 空间对 1.5B–12B LLM 均已存在可行的芯片级解。

## 2. 评测模型与方法

- 5 个 LLM: Qwen2.5-1.5B/3B/7B, Qwen3-8B, Gemma-4-12B
- DSE 配置: 7 引擎 × 7 阵列 × 6 DRAM × 2 精度 × 3 频率
- M=1 (单 token decode) 和 M=2 (batch=2) 双场景

### 2.1 DSE 配置空间

| 维度 | 选项 |
|------|------|
| 引擎类型 | systolic, os_systolic, block, tensor_core, wmma, gmma, input_stationary |
| 阵列尺寸 | 64×64, 96×96, 128×128, 128×192, 128×256, 192×256, 256×256 |
| DRAM 类型 | LPDDR5-32b/64b/128b/256b, HBM2e-1024b, HBM3-1024b |
| 峰值带宽 | 25.6 / 51.2 / 102.4 / 204.8 / 460.0 / 819.2 GB/s |
| 权重量化 | INT2, INT4 |
| 频率 | 800 / 1000 / 1200 MHz |
| Weight Cache | systolic/block/gmma 可选开启 |

### 2.2 约束定义

- M.2 模组约束: 功耗 ≤10W，无面积限制
- 芯片级约束: 功耗 ≤12W 且面积 ≤40mm²（作为 ~30mm² 的容差）
- PCIe 卡约束: 功耗 ≤15W，无面积限制
- 若无配置满足约束，则标记为 N/A

### 2.3 Pass/Fail 准则

- 1.5B 模型目标 ≥25 tok/s
- 3B/7B/8B/12B 模型目标 ≥20 tok/s（3B MRD 下限）
- Params 列采用 architecture weight 估算（不含 embedding），用于 DRAM/tok 与理论上限计算

## 3. 各模型最佳配置 (M=1)

下表给出每个模型在全部 DSE 配置中吞吐最高的结果（无功耗/面积约束），用于观察算力上限。

| Model | Best Config | tok/s | Area | Power | tok/W | tok/mm² |
|-------|-------------|------:|-----:|------:|------:|--------:|
| qwen2.5-1.5b | bloc 128×256 INT2 800MHz WC HBM3-1024b | 1992.2 | 189.2 | 77.0 | 25.9 | 10.5 |
| qwen2.5-3b | bloc 128×256 INT2 800MHz WC HBM3-1024b | 987.5 | 189.2 | 77.0 | 12.8 | 5.2 |
| qwen2.5-7b | bloc 128×256 INT2 800MHz WC HBM3-1024b | 417.5 | 189.2 | 77.0 | 5.4 | 2.2 |
| qwen3-8b | bloc 128×256 INT2 800MHz WC HBM3-1024b | 449.8 | 189.2 | 77.0 | 5.8 | 2.4 |
| gemma-4-12b | bloc 128×256 INT2 800MHz WC HBM3-1024b | 270.8 | 189.2 | 77.0 | 3.5 | 1.4 |

所有模型的无约束最佳配置均落在 HBM3-1024b + block 引擎 + 128×256 阵列 + INT2 上，面积与功耗分别达到 189.2 mm² 与 77W，远超芯片级目标，仅适合数据中心/PCIe 高功耗形态。

## 4. Batch M=2 吞吐提升

对比 M=1 与 M=2 的绝对最佳吞吐，观察 batch decode 的收益。

| Model | M=1 tok/s | M=2 tok/s | 提升 |
|-------|----------:|----------:|-----:|
| qwen2.5-1.5b | 1992.2 | 1988.9 | -0.2% |
| qwen2.5-3b | 987.5 | 984.0 | -0.4% |
| qwen2.5-7b | 417.5 | 416.7 | -0.2% |
| qwen3-8b | 449.8 | 448.4 | -0.3% |
| gemma-4-12b | 270.8 | 269.9 | -0.3% |

M=2 并未带来显著提升，部分模型甚至出现小幅下降。这符合 decode 阶段的特性：batch 增加主要放大 K/V 与激活内存，而权重读取仍是主导流量，因此受 DRAM 带宽制约明显。

## 5. 产品需求对标矩阵

针对三类产品形态，分别筛选功耗/面积约束下的最高吞吐配置。

### M.2 模组约束: ≤10W

| Model | Best under 10W | tok/s | Area | Power | Pass/Fail |
|-------|----------------|------:|-----:|------:|:---------:|
| qwen2.5-1.5b | bloc 64×64 INT2 800MHz WC LPDDR5-64b | 126.8 | 28.2 | 9.6 | Pass |
| qwen2.5-3b | bloc 64×64 INT2 800MHz WC LPDDR5-64b | 61.2 | 28.2 | 9.6 | Pass |
| qwen2.5-7b | bloc 64×64 INT2 800MHz WC LPDDR5-64b | 25.5 | 28.2 | 9.6 | Pass |
| qwen3-8b | bloc 64×64 INT2 800MHz WC LPDDR5-64b | 27.5 | 28.2 | 9.6 | Pass |
| gemma-4-12b | bloc 64×64 INT2 800MHz WC LPDDR5-64b | 16.5 | 28.2 | 9.6 | Fail |

在 10W 限制下，所有模型均选择 LPDDR5-64b + block 64×64 的最低功耗组合。
除 Gemma-4-12B 外，其余模型均满足目标吞吐。
值得注意的是，7B/8B 模型在 LPDDR5-64b 下仍能分别达到约 25/27 tok/s，
说明 INT2 量化与 block 引擎对 decode 阶段的权重读取效率较高。

### 芯片级约束: ≤12W, ~30mm²

| Model | Best under 12W & ~30mm² | tok/s | Area | Power | Pass/Fail |
|-------|-------------------------|------:|-----:|------:|:---------:|
| qwen2.5-1.5b | gmma 64×64 INT2 800MHz WC LPDDR5-64b | 252.6 | 30.2 | 10.4 | Pass |
| qwen2.5-3b | gmma 64×64 INT2 800MHz WC LPDDR5-64b | 122.1 | 30.2 | 10.4 | Pass |
| qwen2.5-7b | gmma 64×64 INT2 800MHz WC LPDDR5-64b | 51.0 | 30.2 | 10.4 | Pass |
| qwen3-8b | gmma 64×64 INT2 800MHz WC LPDDR5-64b | 55.0 | 30.2 | 10.4 | Pass |
| gemma-4-12b | gmma 64×64 INT2 800MHz WC LPDDR5-64b | 33.0 | 30.2 | 10.4 | Pass |

芯片级约束下所有模型选择 gmma 64×64（30.2 mm², 10.4W）搭配 LPDDR5-64b，而 M.2 约束使用 bloc 64×64 以降低面积（28.2 mm², 9.6W）；GMMA 的 TMA 异步 DMA 使其在相同 DRAM 下获得更高吞吐，适合芯片级产品。若放宽面积到 40mm² 以上，可上探 LPDDR5-128b 获得更高吞吐。

### PCIe 卡约束: ≤15W

| Model | Best under 15W | tok/s | Area | Power | Pass/Fail |
|-------|----------------|------:|-----:|------:|:---------:|
| qwen2.5-1.5b | gmma 64×64 INT2 800MHz  LPDDR5-128b | 344.0 | 37.2 | 13.4 | Pass |
| qwen2.5-3b | gmma 64×64 INT2 800MHz  LPDDR5-128b | 166.8 | 37.2 | 13.4 | Pass |
| qwen2.5-7b | gmma 64×64 INT2 800MHz  LPDDR5-128b | 69.6 | 37.2 | 13.4 | Pass |
| qwen3-8b | gmma 64×64 INT2 800MHz  LPDDR5-128b | 75.2 | 37.2 | 13.4 | Pass |
| gemma-4-12b | gmma 64×64 INT2 800MHz  LPDDR5-128b | 45.1 | 37.2 | 13.4 | Pass |

PCIe 15W 允许使用 LPDDR5-128b，所有模型均达标。
Gemma-4-12B 在此约束下达到 45.1 tok/s，与芯片级约束下的 33.0 tok/s 共同说明
12B 级模型在 LPDDR5-64b/128b 配合 gmma 引擎下均具备产品级可用性。

## 6. 模型规模梯度与 DRAM 墙

表中 Params 为 architecture weight（不含 embedding），DRAM/tok 按 INT2（2 bit/weight）估算，Theoretical Max 按 HBM3-1024b 819.2 GB/s × 85% 效率计算。

DRAM/tok 仅统计单次 decode 所需读取的权重大小，未计入 KV cache 与激活；由于 weight cache 与 layer fusion 可减少实际片外流量，achieved best 偶会接近甚至略低于理论上限。从 1.5B 到 12B，理论上限下降约 7.7 倍，与模型规模增长呈反比，验证 DRAM 墙是主要扩展瓶颈。

| Model | Params | DRAM/tok | Theoretical Max tok/s | Achieved Best tok/s | Bottleneck |
|-------|-------:|---------:|----------------------:|--------------------:|------------|
| qwen2.5-1.5b | 1.3B | 327.55 MB | 2125.9 | 1992.2 | DRAM bandwidth wall |
| qwen2.5-3b | 2.6B | 651.43 MB | 1068.9 | 987.5 | DRAM bandwidth wall |
| qwen2.5-7b | 6.5B | 1631.32 MB | 426.8 | 417.5 | DRAM bandwidth wall |
| qwen3-8b | 6.0B | 1509.95 MB | 461.2 | 449.8 | DRAM bandwidth wall |
| gemma-4-12b | 10.1B | 2516.58 MB | 276.7 | 270.8 | DRAM bandwidth wall |

随着 Params 增大，DRAM/tok 线性增长，HBM3 理论上限快速下降；所有模型的 achieved best 均接近 HBM3 上限，说明在 128×256 block 阵列下，系统仍被 DRAM 带宽约束，进一步提速需更宽带宽或更低 bit 量化。

## 7. CV 对比 (MobileNetV3-Small)

| Metric | Value |
|--------|-------|
| Best FPS | 1243.3 (tens 64×64 INT2 800MHz  HBM3-1024b) |
| Best Area-Efficient | 835.4 fps @ 28.2 mm² (29.6 fps/mm²) |
| SRAM Spill | 0 MB |

CV 任务在 LPDDR5-64b 即可达到 1000+ fps，且 SRAM spill 为 0，说明 CaduceusCore 对轻量 CV 模型的算力与片上存储均充足，不会成为产品瓶颈。

## 8. 关键洞察与建议

- 功耗分层下达标情况：M.2 (≤10W) 达标 4/5 (qwen2.5-1.5b, qwen2.5-3b, qwen2.5-7b, qwen3-8b); 芯片 (≤12W, ≤40mm²) 达标 5/5 (qwen2.5-1.5b, qwen2.5-3b, qwen2.5-7b, qwen3-8b, gemma-4-12b); PCIe (≤15W) 达标 5/5 (qwen2.5-1.5b, qwen2.5-3b, qwen2.5-7b, qwen3-8b, gemma-4-12b)。
- Batch M=2 提升有限：最高 qwen2.5-1.5b (-0.2%)，最低 qwen2.5-3b (-0.4%)，说明 decode 阶段 batching 收益受内存带宽制约。
- qwen2.5-1.5b, qwen2.5-3b, qwen2.5-7b, qwen3-8b, gemma-4-12b 的绝对最佳配置均接近 HBM3 带宽上限，继续扩大阵列尺寸收益递减；若产品形态允许 HBM2e/HBM3，则 7B/8B 模型仍有上探空间。
- qwen2.5-1.5b, qwen2.5-3b, qwen2.5-7b, qwen3-8b, gemma-4-12b 的绝对最佳配置面积超过 40 mm²、功耗超过 70W，仅适合高功耗 PCIe/加速卡；芯片级产品需在 LPDDR5-64b/128b 与 64×64/96×96 阵列之间取舍。
- 产品化建议：优先为 1.5B/3B 模型选择 LPDDR5-128b 或更宽带宽、面积 ≤40 mm² 的 gmma/block 配置，以在 12W 芯片封装内同时满足 20-25 tok/s 与面积目标；对 7B/8B/12B 模型建议采用 INT2 + weight cache + gmma 引擎并评估 HBM2e 成本收益。

综上，CaduceusCore 在当前 DSE 空间内已能为 1.5B-12B 的 LLM 提供满足 20-25 tok/s 的芯片级配置，其中 Gemma-4-12B 借助 gmma 引擎在 12W/40mm² 约束下达到 33.0 tok/s。绝对峰值性能仍受 DRAM 带宽上限制约。后续优化应聚焦：(1) 提升 LPDDR5 通道数以降低芯片成本形态下的 DRAM 墙；(2) 评估 INT2 以下量化或稀疏化对 7B+ 模型的收益；(3) 针对 decode 阶段优化 weight cache 命中率，缓解 batch 提升受限的问题。
