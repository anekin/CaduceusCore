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

本地 GGUF 可用列表（17 个，`$HOME/models/`）：
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

**B1 qkv 维度修复（Task 11）**：3B 及以上模型 qkv 维度计算错误已修复，qkv = num_heads × head_dim 现已正确。修复后 3B/7B/8B/12B 模型均可加载测试（证据：`$HOME/npu/.omo/evidence/task-11-green-qkv.txt`，3/3 测试通过）。

**运行**：
```bash
cd sim && PYTHONPATH=. python3 arc_model.py --model $HOME/models/qwen2.5-1.5b-instruct-q4_k_m.gguf --scheme both
```

## FM 验证

**目标**：验证硬件链路 bit-exact 正确性。

**数据来源**：Python 手工构造（无需 GGUF/llama.cpp）。

**覆盖**：
- MMIO Bridge 寄存器读写
- **SFU 处理器**（F1, Task 13）：`_handle_sfu()` 已实现，支持 softmax、gelu、silu、layernorm、rope 五类算子，float16 I/O 通过 SRAM
- **Vector 处理器**（F1, Task 13）：`_handle_vector()` 已实现，支持 ADD、MUL、RED_MAX、RED_SUM、CONV、RESID 六类算子
- DMA DRAM ↔ SRAM 搬运（双通道 CH0/CH1）
- MXU per-block INT4 矩阵乘（含 ACCUMULATE 模式）
- **固件 opcode 分发**（F2, Task 14）：`NPUFirmware._dispatch()` 现已覆盖 SFU、VECTOR、DMA 全部 opcode（此前仅实现 MMUL tile 路径）
- 固件 tile 级双缓冲调度（tile_scheduler.py）
- AXI Trace 事务顺序验证

**当前状态**：✅ PASS（F1/F2 已完成）
- 256×256 矩阵，2 K-blocks × 2 N-tiles
- 512KB SRAM
- 91 AXI 事务（DMA 55 + MXU 36）
- SFU/Vector handler 回归通过（test_mmio_bridge.py 2/2）
- 全 opcode 分发通过（test_firmware.py 4/4）
- 已知问题：func_model.py trace 导出依赖硬编码 `/Users/zheng/` 路径，核心验证逻辑无影响（见 Task 19 证据）

**运行**：
```bash
cd CaduceusCore && PYTHONPATH=sim python3 sim/func_model.py
```

### 契约加固（fm-hardening-phase10）

Phase 10 复盘结论：Func Model 数值上已 bit-exact，缺的是布局/契约层守卫。fm-hardening-phase10 为 FM 验证补齐四类契约，让 6 类曾需 7.5h RTL 段跑才暴露的 bug 在纯 Python 秒级触发。

**内存布局契约**（`sim/address_space.py` + `sim/command_ring.py`）
- `address_space.py` 拥有 DRAM 区域表（命令环/完成环/descriptor 池/activation/weight）与重叠、8MB 窗口检查：`regions_overlap()`、`addr_in_window()`、`contract_check()`；调度期断言 descriptor 区与环区不相交，违反抛 `OverlapError`/`WindowError`。
- `command_ring.py` 是环配置唯一事实源（RING_BASE、RING_ENTRIES=1024、CMD_ENTRY_SIZE=32、COMPLETION_RING_ADDR、DESC_STRIDE），提供 `ring_entry_addr()`、`advance_head()`、`expected_head()`。
- BUG-RTL-SOC-008（DESC_BASE 与命令环重叠）现可在 FM 秒级复现：注入 `DESC_BASE=0x80001000` 会使 `schedule_chain()` 抛 `OverlapError`（`sim/tests/test_spike_host_overlap.py`）。

**scale/accumulate golden 要求**（对齐 `matmul_int4_per_block`）
- scale：SCALE_ADDR!=0 + 非平凡 FP32 scale（随机 [0.5,1.5]），scale 缓冲按桥接读取器布局 `[ceil(K/128)][N]` fp32 写；输出与 `matmul_int4_per_block(group_size=128)` 对齐（fp32_tol rtol=atol=1e-5），K=256 覆盖多 scale-block 路径（`test_soc_fm.py::test_mmul_scale_nonzero`）。
- accumulate：CTRL[2] 两命令链（K=256 拆 2×128 同输出地址），结果 == 第一段 partial + 第二段 fresh partial，与 `matmul_int4_per_block` 分块组合 golden 一致（`test_soc_fm.py::test_mmul_accumulate`）。

**段边界 SRAM 清零协议**（`clear_sram` 契约）
- `segment_preload(force_full=True, clear_sram=True)` 要求 `sram == b"\x00" * SRAM_SIZE`，否则抛 `SegmentBoundaryError`（`sim/cocotb_bridge.py`）；段跑调用点传 `clear_sram=True`，单段/probe 调用保持默认 `clear_sram=False`（`sim/tests/test_segment_boundary.py`）。

**反向依赖门禁**
- `scripts/fm_reverse_dependency_gate.sh`：RTL/firmware/桥接文件变更 → 自动重跑全量 pytest + W4-PERF 6 批次 + scale/accumulate 回归，状态持久化于 `.omo/last_fm_gate.json`。
- `--dry-run` 只打印将执行项、不执行任何动作：干净状态 exit 0，有敏感文件 diff 时 exit 1。

```bash
cd CaduceusCore && ./scripts/fm_reverse_dependency_gate.sh --dry-run
```

## FM → RTL 交付门禁（Pre-RTL Signoff Gate）

fm-hardening-phase10 的复盘结论是：Func Model 数值本身已 bit-exact，但布局/契约层缺失系统化的前置守卫，导致 6 类问题必须等到 7.5h 的 RTL 段跑才暴露。为避免下一个 SoC 在 RTL 验证阶段再回头补 FM，所有 Func Model 在交付 RTL 之前必须通过下表门禁。

| # | 检查项 | 守卫目标 | 门禁命令 |
|---|--------|----------|----------|
| 1 | 地址空间与命令环布局契约 | descriptor/activation/weight 不重叠；环回绕语义正确 | `PYTHONPATH=sim python -m pytest sim/tests/test_address_space.py sim/tests/test_command_ring.py sim/tests/test_spike_host_overlap.py -q` |
| 2 | 环回绕与长序列压力场景 | BUG-RTL-SOC-008 类在 FM 秒级复现 | `PYTHONPATH=sim python -m pytest sim/tests/test_command_ring_stress.py sim/tests/test_soc_fm_long_sequence.py -q` |
| 3 | scale/accumulate golden 加固 | 非平凡 FP32 scale、CTRL[2] 累加与 `matmul_int4_per_block` 对齐 | `PYTHONPATH=sim python -m pytest sim/tests/test_soc_fm.py::test_mmul_scale_nonzero sim/tests/test_soc_fm.py::test_mmul_accumulate -v` |
| 4 | 双 packer 等价与 ABI 常量一致性 | spike_host/cocotb_bridge 布局不漂移；Python/C 常量同源 | `PYTHONPATH=sim python -m pytest sim/tests/test_packer_equivalence.py sim/tests/test_npu_abi_constants.py -q` |
| 5 | 段边界 SRAM 清零协议 | ISSUE-13C 类残留数据不泄漏到下一段 | `PYTHONPATH=sim python -m pytest sim/tests/test_segment_boundary.py -q` |
| 6 | RTL/firmware 变更反向依赖门禁 | RTL/firmware/桥接改动自动触发 FM + W4-PERF 回归 | `./scripts/fm_reverse_dependency_gate.sh --dry-run` |
| 7 | PCIe TLP 完整链守卫（SOC-13） | tlp_write→BAR 路由→crossbar→SRAM/DRAM 写入→tlp_read bit-exact；4KB 载荷按 MPS=256B 分裂；BAR 路由隔离；载荷篡改→readback 失配 | `PYTHONPATH=sim python -m pytest sim/tests/test_pcie_tlp_chain.py -v` |
| 8 | INTC ENABLE/THRESHOLD 门控（SOC-17 / FW-10） | `popcount(PENDING & ENABLE) >= THRESHOLD` 才置 cpu_irq；ENABLE=0 屏蔽、THRESHOLD 门控、ACK 清除、WFI 唤醒 | `PYTHONPATH=sim python -m pytest sim/tests/test_intc_gating.py -v` |
| 9 | AXI 仲裁公平性（SOC-14） | 多 master 并发 round-robin 交替公平；DECERR 地址拒绝；AXI ID 路由 master_id<<8\|txn_id | `PYTHONPATH=sim python -m pytest sim/tests/test_crossbar_arbitration.py -v` |
| 10 | APB 寄存器 conformance（SOC-15） | 8 个 peripheral write→readback 序列语义（rw/r/w/w1c）与 `regmap.py` 一致；写只读寄存器值不变 | `PYTHONPATH=sim python -m pytest sim/tests/test_apb_register_conformance.py -v` |
| 11 | Ibex 共享地址空间跨引擎（SOC-16） | Ibex 经 crossbar 写 SRAM→MXU 读一致（双向）；DMEM/boot ROM 隔离 | `PYTHONPATH=sim python -m pytest sim/tests/test_ibex_shared_address_space.py -v` |
| 12 | IRQ 驱动 firmware 调度（FW-10） | op 完成由 IRQ（非 STATUS 轮询）驱动调度下一命令；抑制 IRQ→firmware 停滞 | `PYTHONPATH=sim python -m pytest sim/tests/test_irq_driven_dispatch.py -v` |
| 13 | 固件 boot 序列（SOC-18） | PC=0→step() 执行真实 firmware hex 进 main→doorbell poll→首命令完成；boot ROM 隔离 | `PYTHONPATH=sim python -m pytest sim/tests/test_firmware_boot_sequence.py -v` |
| 14 | Spike↔Ibex ring 管理对齐（FW-08） | 208 命令两路径 NPU_HEAD/HOST_HEAD/COMPLETION_STATUS 一致；ring_size 分歧注入→wrap 行为分歧 | `PYTHONPATH=sim python -m pytest sim/tests/test_spike_ibex_ring_alignment.py -v` |
| 15 | firmware 内存契约（FW-09） | FM 生成 JSON 与 address_space/command_ring/spec 三源一致；篡改 RING_ENTRIES→比对失败 | `python3 scripts/gen_firmware_memory_contract.py --check && PYTHONPATH=sim python -m pytest sim/tests/test_memory_contract.py -v` |
| 16 | 28 层 Qwen full-model FM gate（E2E-04） | 531 命令持久偏移跑通；末层 cos ≥ 0.999；ring wrap ≥ 33；layer 5 op14 篡改→失配 | `PYTHONPATH=sim python -m pytest sim/tests/test_soc_fm_long_sequence.py::test_multi_layer_persistent_offset -v` |
| 17 | MobileNetV3 CV chain FM gate（E2E-05） | 全图 doorbell ring 调度；GEMM 层与 golden cos_sim ≥ 0.99；权重地址篡改→单层失配 | `PYTHONPATH=sim python -m pytest sim/tests/test_mobilenetv3_fm_chain.py -v` |
| 18 | Spike forward tolerance 回归（E2E-06） | 2 层 max_abs < 1e-1；36 层 cos_sim 逐层 ≥ P10_LADDER（0.999/0.998/0.997）；阈值收紧→ok=False | `PYTHONPATH=sim python -m pytest sim/tests/test_spike_forward_tolerance.py -v` |
| 19 | ABORT/MXU idle 既有覆盖（E2E-08） | attn_weight shape 分发守卫 + 无 idle gap 连续分发回归（fm-hardening-phase10 遗留，已 ✅） | `PYTHONPATH=sim python -m pytest sim/tests/test_soc_fm.py::test_mmul_attn_weight_shape sim/tests/test_soc_fm.py::test_mmul_attn_weight_shape_not_dispatched -v` |

**冻结面（范围门禁）**：任何进入 RTL 验证前的提交，不得改动 `rtl/`、`sim/arc_model.py`、`sim/design_space_explorer.py`、`sim/quantize.py`、`ggml-npu/`、`requirements.txt`。若确需改动，必须重跑 F4 范围门禁并通过：

```bash
bash scripts/fm_hardening_f4_scope_gate.sh
```

**决策记录**：P0/P1/P2P3 vs P4 布局差异化处置、`sim/device_server.py` RING_SIZE=16 排除理由、tests-after 策略等关键设计，详见 `.omo/notepads/fm-verification-hardening/learnings.md`。

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
cd sim && PYTHONPATH=. python3 e2e_llamacpp.py --model $HOME/models/qwen2.5-1.5b-instruct-q4_k_m.gguf --layers 2
```

## pytest 测试套件

**目录**：`sim/tests/`（10 个测试文件，109 项测试，全部通过，Task 21）

**命令**：
```bash
cd CaduceusCore && PYTHONPATH=sim python3 -m pytest sim/tests/ -v
```

**测试覆盖**：

| 测试文件 | 项数 | 覆盖范围 |
|---------|:----:|---------|
| `test_golden_smoke.py` | 60 | 10 种 tile/矩阵布局 × 6 维度验证（确定性/哈希/溢出/形状/输入/校验） |
| `test_golden_sfu.py` | 36 | softmax（15）/ layernorm（6）/ rope（6）/ gelu（5）/ silu（4）精度回归 |
| `test_arc_model.py` | 4 | qkv 维度（3）+ 跨平台路径验证 |
| `test_firmware.py` | 4 | MMUL / SFU / VECTOR / DMA 全 opcode 分发 |
| `test_mmio_bridge.py` | 2 | SFU / Vector handler 端到端计算 |
| `test_tile_scheduler.py` | 1 | 输入验证（非法 descriptor） |
| `test_arc_precision.py` | 1 | 精度报告 MSE 字段校验 |
| `test_golden_deprecation.py` | 1 | 旧版 models.golden 模块弃用确认 |

**证据目录**：
- `.omo/evidence/` — 各任务验证证据（task-11-green-qkv, task-13-green-mmio, task-14-green-dispatch, task-21-pytest-full 等）
- `logs/` — 脚本执行日志（verify_smoke.log, verify_sfu.log, verify_func_model.log 等）

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
make -j$(nproc)
make install

# 后续重新构建
cd spike_src/build && make -j$(nproc)
```

Patch 内容（`patches/` 目录）：
- `spike_npu.patch` — `sim.cc`（npu_factory 注册）+ `riscv.mk.in`（编译 npu_device.cc）+ `spike_main.mk.in`（注释修正）
- `npu_device.cc` — NPU MMIO 设备实现（RISC-V 端门铃寄存器）
