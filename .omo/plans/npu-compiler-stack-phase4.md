# npu-compiler-stack — Phase 4 Plan

## TL;DR (For humans)

**What you'll get:** CaduceusCore NPU 的 LLVM 编译栈——一个能接受 gguf Q4_K_M 权重 + ONNX 模型描述、生成 MXU/SFU/Vector 指令序列、并通过 Func Model 验证正确性的编译器。完成后，64×64 NPU 从「只能通过 Python MMIO 手工调寄存器」升级为「可以被 ggml 直接调用的一等 citizen ML 后端」。

**Why this approach:** CaduceusCore 当前没有任何编译栈——Func Model v2/v3 验证了数学正确性，但模型推理是通过 Python 直接写 MMIO 寄存器驱动的。没有编译器 = 没有算子融合 = DDR 读写冗余 3-5× = 利用率锁定在 18-42%。LLVM 是唯一工业级开源编译器底座（NVIDIA/AMD/华为/平头哥全部基于 LLVM），按「后端 Target → AI Pass → 前端 → 二进制」四阶段推进。

**What it will NOT do:** 不改变 MXU/SFU/Vector RTL，不改变 SoC 架构，不改变固件 functional spec，不做性能签收（那是后续 Phase 的事）。只做编译栈——从高级模型描述 → NPU 机器码的整条链路。

**Effort:** XL — 约 10-15 天，分 4 个 Milestone
**Risk:** Medium — LLVM 后端 Target 开发学习曲线陡峭但文档齐全；算子融合是编译优化的「常规操作」而非 open research。
**Decisions to sanity-check:** (1) 用 LLVM 原生 TableGen 而不是手写 C++ 后端；(2) 在本地 macOS 上开发、sz0001 上部署验证；(3) Phase 4 只做编译栈，不做物理设计（那是 Phase 5+）。

Your next move: approve，然后开始 M0（LLVM 环境搭建 + MXU 指令集建模）。

---

## Scope

### Must have

- **M0: LLVM 环境 + 指令集建模 (2d)**
  - 安装 LLVM 19+ 开发环境（源码编译或 brew）
  - 用 TableGen 定义 MXU 指令集（`MXU_MMUL`、`MXU_LOAD_W`、`MXU_LOAD_A`、`MXU_STORE`）
  - 同样为 SFU（`SFU_SOFTMAX` 等 7 条）和 Vector（`VEC_ADD` 等 6 条）定义指令
  - 完成 Target 注册——`-march=caduceus` 可正常识别

- **M1: 后端 Target + 代码生成 (3d)**
  - SelectionDAG 模式匹配：将 LLVM IR 的 `mul`+`add` 序列匹配为 `MXU_MMUL`
  - 寄存器分配：定义 64×64 虚拟寄存器文件（映射到 MAC array PE 阵列）
  - 指令编码：`opcode(8) + flags(8) + addr(16)` 格式，对齐 firmware dispatch 表
  - 产出：clang 编译一个简单 matmul kernel → 生成 MXU 机器码

- **M2: AI 优化 Pass (3d)**
  - 算子融合 Pass（最高优先级）：RMSNorm+MatMul, gate+up+SiLU+VMUL, Conv+BN+ReLU
  - Tiling Pass：将 M×K×N 大矩阵映射到 64×64×64 tiles
  - 存储调度 Pass：标注哪些张量放在 SRAM（高频访问）、哪些在 DRAM

- **M3: 前端对接 + E2E 验证 (2-3d)**
  - ggml → LLVM IR 桥接层：读取 Q4_K_M 权重 → 生成 LLVM IR load + MXU compute
  - Func Model 集成验证：编译器产出的指令序列 → Func Model 执行 → cos_sim ≥ 0.99
  - Qwen2.5-3B blk.0 end-to-end：模型 → 编译器 → 指令 → Func Model → 结果验证

### Must NOT have

- NO RTL changes（`rtl/mxu/*`、`rtl/sfu/*`、`rtl/soc/*`）
- NO firmware changes（只生成兼容现有 dispatch 表的指令）
- NO performance optimization beyond operator fusion（那是后续 Phase）
- NO physical design（no OpenROAD/Yosys synthesis）
- NO new bugs in existing RTL or Func Model

---

## Execution Plan

### Milestone 0: LLVM 环境 + 指令集建模

| Task | Description | Duration |
|---|---|---|
| T0.1 | Install LLVM 19+ dev env (brew or source) | 0.5d |
| T0.2 | Study existing RISC-V backend as reference | 0.5d |
| T0.3 | TableGen: define MXU instructions | 0.5d |
| T0.4 | TableGen: define SFU + Vector instructions | 0.5d |
| T0.5 | Register `caduceus` target, verify `-march=caduceus` | — |

**Deliverable:** LLVM 能识别 `-march=caduceus`，指令集定义完整但尚未生成代码。

### Milestone 1: 后端 Target + 代码生成

| Task | Description | Duration |
|---|---|---|
| T1.1 | Implement SelectionDAG patterns for MXU | 1d |
| T1.2 | Register allocator: virtual register file mapping | 0.5d |
| T1.3 | Instruction encoding: opcode + flags + addr | 0.5d |
| T1.4 | Compile simple matmul kernel → machine code | 0.5d |
| T1.5 | Verify machine code against manual encoding | 0.5d |

**Deliverable:** `clang -march=caduceus matmul.c` → 正确 MXU 指令序列。

### Milestone 2: AI 优化 Pass

| Task | Description | Duration |
|---|---|---|
| T2.1 | Operator fusion pass: RMSNorm+MatMul | 0.5d |
| T2.2 | Operator fusion pass: gate+up+SiLU+VMUL | 0.5d |
| T2.3 | Tiling pass: 64×64 tile decomposition | 1d |
| T2.4 | Memory scheduling pass: SRAM vs DRAM annotation | 0.5d |
| T2.5 | Verify fusion eliminates redundant DDR accesses | 0.5d |

**Deliverable:** 算子融合后 DDR 访问减少 ≥ 70%（vs 分段执行）。

### Milestone 3: 前端对接 + E2E

| Task | Description | Duration |
|---|---|---|
| T3.1 | ggml Q4_K_M weight reader → LLVM IR | 0.5d |
| T3.2 | ONNX-like graph → LLVM IR lowering | 0.5d |
| T3.3 | Generate Qwen2.5-3B blk.0 instruction stream | 0.5d |
| T3.4 | Func Model execution + cos_sim validation | 0.5d |
| T3.5 | Document full compilation flow | 0.5d |

**Deliverable:** Qwen2.5-3B blk.0 → Compiler → Func Model → cos_sim ≥ 0.99。

---

## Verification Strategy

- **Per-task verification:** 每个 milestone 产出可执行的编译命令 + 输出指令序列
- **Golden comparison:** 编译器输出的指令序列 vs 手工 MMIO 驱动序列 → bit-exact match
- **Func Model integration:** 编译器输出 → Func Model → cos_sim 验证
- **DDR access trace:** 编译前后 DDR 读写次数对比（融合前 40-50 vs 融合后 6-8）

---

## Risk Mitigation

| Risk | Mitigation |
|---|---|
| LLVM 学习曲线陡峭 | 先研究 RISC-V 后端（最干净的开源参考），再定制 |
| TableGen 语法复杂 | 从已有的 GPU/NPU 后端抄模板（AMDGPU/NVPTX） |
| 算子融合破坏正确性 | 每次融合后立即 Func Model 验证，cos_sim gate |
| M3 E2E 复杂度高 | 先做单算子验证，再做 blk.0，最后全模型 |

---

## Key Success Metrics

| Metric | Target |
|---|---|
| Matmul kernel compiles to MXU instructions | ✅ |
| Operator fusion reduces DDR accesses | ≥ 70% |
| Qwen2.5-3B blk.0 cos_sim vs golden | ≥ 0.99 |
| Compilation time (blk.0) | < 30s |
| Incremental hardware knowledge base | Learnings per milestone |
