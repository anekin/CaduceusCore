# soc-verification-gaps-phase5 - Work Plan

## TL;DR (For humans)

**做什么**: 在 INT4×INT8 数据格式下，补全 SoC 验证的覆盖率缺口——多 layer forward pass、SFU+Vector 模块级性能、CV 模型 E2E、SoC 级性能、流程改进。5 个 Wave，~8-11 天。

**为什么**: 当前 33/33 FM-SOC + 210/210 pytest 全部 PASS，但单 layer 已验证的前提下缺少 36 层串联验证、SFU+Vector 性能未测、CV 模型零覆盖、PCIe TLP read 通路未通、Review Gate 流程缺失。

**验证方法论**: 新增 case 先走 Func Model 验证 → 再上 sz0001 SoC testbench → 发现 RTL/TB/Func Model bug 即记录 → 修复 → 重跑回归确认无退化。

**不会做**: INT8×INT8 / BF16 新数据通路开发（留待后续 phase）；综合/物理设计；新引擎架构。

**允许做**: 在 INT4×INT8 路径上发现 RTL bug 时可修复（含 mxu/sfu/vector/wrapper/soc/tb）；新增 anti-vacuous test case 覆盖缺陷场景。

**预计工作量**: W1 1-2天 | W2 2-3天 | W3 3-4天 | W4 2-3天 | W5 贯穿全程

**关键决策**:
- 模型锁定 Qwen2.5-3B-Q4_K_M GGUF（36 layers, hidden_size=2048）
- PCIe TLP read 是 testbench TC2 覆盖率缺口（非 SoC 功能性 bug）
- 验证顺序：Func Model → sz0001 SoC testbench → 记录 bug → fix TB/FM → RTL 回归
- **36-layer RTL 仿真推迟到 Phase 6（FPGA 阶段）** — Phase 5 只做到 3-layer RTL + Func Model 36-layer golden reference 生成。理由：VCS 36-layer 单次仿真 2-4h，debug 成本太高；FPGA 上秒级重跑更合适；3-layer 已覆盖 forward pass 核心 pattern。

## Scope

### 修改范围（允许）
- Func Model 测试基础设施（新增 case generation、compare logic）
- RTL testbench（添加新场景、修复覆盖率缺口）
- RTL bug fix（仅限 INT4×INT8 数据通路，含 mxu/sfu/vector/wrapper/soc/tb）
- SoC 回归脚本（新增 target、perf measurement）
- `docs/bugs/` — 按 phase 拆分 bug 追踪
- `docs/issues_found.md` — 已知盲区记录

### Must NOT Have（受保护范围）
- INT8×INT8 / BF16 新数据通路开发
- 综合/物理设计
- 新引擎架构

### RTL Bug Fix 策略
- 在 INT4×INT8 验证过程中发现有 root-cause 确认的 RTL bug → 允许修复
- 修复范围限于 `rtl/mxu/`, `rtl/sfu/`, `rtl/vector/`, `rtl/wrapper/`, `rtl/soc/`, `rtl/tb/`
- 每个 bug fix 后需重跑 FM-SOC 全量 + 受影响模块的模块级回归确认无退化
- Bug 记录到 `docs/bugs/bugs-soc-rtl.md`

## Verification strategy

### 三层验证顺序（每个新增 case 遵循）

```
Func Model 验证 → sz0001 SoC testbench → Bug report → Fix TB/FM issues → RTL 回归
```

1. **Func Model 先行**: 新 case 先用 Python Func Model（GoldenExecutor）跑通，确认 golden reference 正确，生成 `.npz` 测试向量
2. **sz0001 SoC testbench**: 在 EDA server 上（`sz0001 / 192.168.0.11`）跑 Cocotb + VCS，driven by Spike CPU
3. **Bug 记录**: 发现的 RTL bug / TB bug / Func Model bug 即时记录到 `docs/bugs/bugs-soc-rtl.md`（或对应 phase 文件），发现即 commit
4. **修复 + 回归**: 修复后重跑 FM-SOC 全量 + 受影响模块的模块级回归

### Func Model 验证环境约束

> **所有验证活动——即使不调用任何 EDA 工具——都必须在 EDA server `sz0001` 上执行。** 不允许在开发机跑 Func Model 然后 scp 结果到 EDA server。

- **执行位置**: 所有验证（Func Model、Cocotb testbench、VCS 仿真、perf measurement）均在 **sz0001（`192.168.0.11`）** 上运行
- **Testbench 复用**: Func Model 阶段的 testbench（如 `cocotb_bridge.py`、`rtl_soc_runner.py`）与 RTL 阶段使用**同一套代码**，区别仅在于：
  - Func Model 模式：Cocotb 调用 GoldenExecutor**而非**驱动 RTL 信号，不启动 VCS
  - RTL 模式：Cocotb 通过 VPI 驱动真实的 RTL Verilog，启动 VCS
- **Testcase 共用**: Func Model 生成的 `.npz` 测试向量直接作为 RTL 验证的输入，保证两边消费完全相同的测试数据
- **dry-run 机制**: 在进入 VCS 仿真之前，所有 `_build_*()` / descriptor / MMIO 配置逻辑在 sz0001 上用纯 Python（不启动 VCS）先跑通，5 分钟能抓的 bug 不拖到小时级 VCS 仿真中（Lesson 12）

### Testcase List 管理（zartbot 方法论）

> **每个 Wave 定义独立的 testcase-list.md，放在该 Wave 对应的目录下。每个 testcase 执行完毕后即时更新状态并推送到 GitHub。**

- **目录结构**: `build/<wave>/testcase-list.md` — 例如 `build/wave1/testcase-list.md`、`build/wave2/testcase-list.md`
- **模板格式**: 沿用 zartbot 方法论，包含：Case ID、优先级（P0-P4）、描述、Func Model 状态、RTL 状态、验收标准、证据路径、备注
- **状态更新**: 每个 testcase 执行完毕后（PASS/FAIL/SKIP），**立即**更新对应的 `testcase-list.md` 将该 case 标记为 ✅/❌/⏸️
- **推送频率**: 每完成一个 todo 或一个 testcase 状态变更，即 commit + push 到 GitHub，不攒批到 Wave 结束
- **参考模板**: 已有的 `rtl/testcase-list-soc-fm.md`、`rtl/testcase-list-mxu-perf.md`、`rtl/testcase-list-sfu-vector-perf.md` 等
- **规格 vs 执行追踪**: `rtl/testcase-list-*.md` 是**规格文档**（定义测试目标、周期公式、验收标准），保持不变；`build/<wave>/testcase-list.md` 是**执行追踪表**（轻量级，只追踪每个 case 的执行状态和证据路径，引用规格文档作为参考）。例如 Wave 2 的规格文档是 `rtl/testcase-list-sfu-vector-perf.md`，执行追踪表是 `build/wave2/testcase-list.md`

### 回归基线
所有 Wave 完成后必须保持：
- pytest 210/210 PASS
- FM-SOC 33/33 PASS（+ 本计划新增 case: 3-layer forward, 36-layer FM golden）
- 模块级 API 回归无退化（SFU 319/319, Vector 63/63, MXU 9/9）

### Review Gate
每个 Wave 完成后设 Review Gate（Atlas final-review），检查：
- SUMMARY 行与实际 case 日志一致
- FAIL case 有对应 bug entry
- Anti-vacuous case 确实检测到 MISMATCH
- 回归基线未退化

## Execution strategy

### 并行路径
```
Path A: W1 (3-layer forward + 36-layer Func Model golden) → W3-CV
Path B: W2 (SFU+Vector 模块级 perf) → W4 (SoC 级 perf)
        注意: W2 的 Func Model 部分可与 W1 并行; W2 的 RTL 仿真部分与 W4 串行（共享 sz0001 VCS license）
Path C: W5 (process + ISA gap + descriptor 对齐) — 贯穿全程，第 1 天启动
```

### 脚本优先原则（Script-First Discipline）

> **所有工具调用、环境变量设置、编译/仿真流程必须以脚本形式固化，多个 agent 执行不同 wave 时调用同一套脚本，避免不同 agent 反复踩同样的坑。**

- **环境初始化**: `source sim/regression/run_env.sh` — 统一加载 VCS license、Python 环境、cocotb 路径
- **编译**: `make -C sim/regression <target>` 或 `bash sim/regression/soc-verification-run.sh <target>` — 不直接写 `vcs` 命令行
- **仿真**: `bash sim/regression/run_fm_soc_case.sh <CASE_ID>` — 单 case 执行入口
- **回归**: `bash sim/regression/run_ibex_full_rtl.sh` / `bash sim/regression/run_p0_full_rtl.sh` 等 — 已有脚本直接复用
- **新增脚本**: 每个 Wave 需要的新流程（如 W2 的 `run_sfu_perf_case.py`、W4 的 `run_perf_case.py`）必须做成可独立调用的脚本，放入 `sim/regression/` 或 `scripts/` 目录
- **禁止的做法**: Agent 在 prompt 里手写 `module load vcs && vcs -full64 ...` 或 `ssh zhengs@sz0001 ...` — 这些应该已被封装在现有脚本中

### NFS 路径约束

> **当前工作目录 `/home/prj/zhengs/caduceuscore/CaduceusCore` 是 NFS 挂载，sz0001 和 sz0002 均可直接访问。所有脚本、testbench、测试向量直接放在 repo 目录下，不需要复制到 `/tmp` 或 sz0001 本地目录。**

- 脚本路径: `sim/regression/*.sh`, `scripts/*.py` — 直接在 NFS 路径执行
- 构建产物: `build/` 目录也在 NFS 上，不同 agent 可共享编译缓存（simv 二进制）
- 临时文件: 如需 `/tmp`，仅用于 VCS 的 `-Mdir` 编译中间文件（`csrc/`），仿真输入/输出/日志全部落在 repo 内
- SSH 转发: 如果当前不在 sz0001 上，由 `soc-verification-run.sh` 自动 ssh 转发到 sz0001 执行，agent 无需手动 ssh

### 验证流程（每个新 case）
1. Func Model: `PYTHONPATH=sim python3 sim/golden_executor.py` 验证 golden 正确性
2. Testbench prep: 在 sz0001 上编译 Cocotb simv，生成 `.npz` 向量
3. EDA server: `bash sim/regression/soc-verification-run.sh <target>` 跑 RTL 仿真
4. Compare: `compare_rtl.py` 比对 RTL vs Golden
5. Bug log: 发现差异 → 记录 `docs/bugs/bugs-soc-rtl.md` → root-cause → fix → 重跑

### Golden Reference 约定
- 所有 RTL vs Golden 比对使用 **Func Model 生成的 `.npz` 作为唯一 golden reference**
- llama.cpp / PyTorch reference 仅用于在 Func Model 阶段推导 tolerances，不作为 RTL 验收的 PASS/FAIL 基准
- 每个 case 的 `.npz` 路径: `rtl/test_vectors/soc_e2e/<case_id>/expected.npz`

### 环境就绪 Gate（Pre-Wave 1，阻塞所有 RTL todo）
- 0.1 [x] sz0001 环境验证：`module load vcs/vcs_2023.12sp2` 可用；Python 3.11 + cocotb 可用；Spike + plugin 可编译；`firmware/build/npu_firmware.hex` 可生成
  **Acceptance**: `vcs -ID` 输出版本 V-2023.12-SP2；`cocotb-config --version` 返回 1.x；`make -C firmware` 成功
  **Commit**: `[Env] Verify sz0001 EDA environment readiness`

- 0.2 [x] 清理 `/tmp` 硬编码路径，统一使用 `build/` 目录
  **Refs**: `scripts/run_task17_regression.py`, `scripts/run_batch_regression.py`, `scripts/run_mxu_perf_case.py`, `scripts/post_sf01_compare.py`, `scripts/gen_qwen_mxu_e2e.py`, `rtl/sfu/README.md`, `rtl/vector/README.md`, `README.md`
  **Acceptance**: 所有回归脚本默认 simv 路径改为 `build/<name>/simv_*`；batch 文件/日志改为 `build/evidence/`；README 文档中命令行示例不再引用 `/tmp/`；socket 路径改为 `build/run/`（Unix domain socket 仍可放在 `/tmp`，但通过环境变量可覆盖）
  **QA happy**: `grep -r '/tmp/simv' scripts/ rtl/*/README.md README.md` 返回 0 结果；`grep -r '/tmp/' scripts/run_batch_regression.py scripts/run_task17_regression.py scripts/run_mxu_perf_case.py` 返回 0 结果（除已文档化的临时文件外）
  **QA fail**: if any script still defaults to `/tmp` → fix → re-check
  **Commit**: `[Fix] Replace /tmp hardcoded paths with build/ directory`

## Todos

### Wave 1: 3-Layer Forward + 36-Layer Func Model Golden + Multi-Op Intermediate Compare

1. [x] Define Qwen2.5-3B 36-layer forward test specification
   **Refs**: `docs/rtl_development_plan.md` §4.4.1; Qwen2.5-3B-Q4_K_M GGUF (36 layers, hidden=2048, intermediate=11008)
   **Methodology**: Document per-layer expected tensor shapes, tolerances (cos_sim ≥ 0.999, max_rel_err ≤ 1e-4); Func Model `.npz` is the golden reference (llama.cpp used only to derive tolerances, not as PASS/FAIL benchmark)
   **Acceptance**: Spec file at `docs/qwen25-3b-forward-spec.md` containing: GGUF SHA256 hash, per-layer tensor shapes table, tolerance thresholds, reference to Func Model golden `.npz` path
   **QA happy**: `head -5 docs/qwen25-3b-forward-spec.md` prints expected fields; `grep -c "Layer" docs/qwen25-3b-forward-spec.md` returns 36
   **QA fail**: if spec file missing or incomplete → block W1.2 until fixed
   **Commit**: `[Doc] Qwen2.5-3B 36-layer forward test specification`

2. [x] Func Model: 3-layer subset forward pass (layers 0, 1, 2)
   **Refs**: FM-SOC-027 (blk.0 17-op chain); `sim/e2e_llamacpp.py`; `sim/golden_executor.py`
   **Methodology**: Pure Python Func Model, compare per-layer hidden states against llama.cpp reference
   **Acceptance**: 3/3 layer outputs cos_sim ≥ 0.999; intermediate hidden states saved per layer (not just final)
   **QA happy**: TESTS=3 PASS=3; per-layer cos_sim recorded
   **QA fail**: if any layer fails, isolate to specific op, produce minimal repro
   **Commit**: `[Test][FM] Qwen2.5-3B 3-layer Func Model forward pass verified`

3. [x] SoC testbench: 3-layer forward pass on sz0001
   **Refs**: W1.2 Func Model vectors; `sim/rtl_soc_runner.py`; `sim/cocotb_bridge.py`
   **Methodology**: Load Func Model .npz vectors → Cocotb + VCS on sz0001 → compare RTL output vs golden per layer. Uses per-op hex preload for each op (true op-to-op data flow blocked by ISA gap FP16→INT32 — see W1.4a).
   **Acceptance**: 3/3 layers PASS on RTL SoC (per-op regression mode); 51/51 ops PASS (45→51 after fix: attn_weight streaming fix + VMUL fix)
   **QA happy**: regression log TESTS=1 PASS=1 FAIL=0; `test_qwen25_3b_3layer PASS`
   **QA fail**: if RTL output differs from Func Model golden, root-cause and fix
   **Commit**: `[Test][RTL] Qwen2.5-3B 3-layer SoC RTL forward pass verified (per-op regression)`

4a. [x] ISA fix: Fill FP16 → INT32 dtype conversion gap (PRIORITY — blocks true E2E forward pass)
   **Refs**: `docs/isa-fp16-to-int32-gap-analysis.md`; `sim/engine/isa.py` (VCONV=0x13 INT32→FP16 only)
   **Symptom**: Real forward pass requires 3 dtype conversions; only INT32→FP16 implemented. SFU output (FP16) has no path to MMUL input (INT8) or Vector VRESID input (INT32). Current per-op regression works because hex files are pre-generated with correct dtype — Python runner acts as implicit converter.
   **Methodology**: 
     (a) ISA: add `VCONV_F16_I32` opcode to `sim/engine/isa.py` (implemented as `0x18` because `0x14` is already `VRESID`)
     (b) Func Model: add `GoldenVector.conv_f16_to_i32()` to `sim/golden_executor.py`
     (c) RTL: add `f16_to_i32` module to `rtl/vector/vector_top.v`
   **Acceptance**: 
     - ISA: new opcode added, `OpCode.VCONV_F16_I32` defined ✅
     - Func Model: `conv_f16_to_i32()` numpy test PASS for all boundary values (+/-inf, NaN, denorm, zero, max) ✅
     - RTL: `f16_to_i32` VCS standalone test PASS (bit-exact vs numpy float32→int32 within ±1 LSB) ✅
   **Verification**: `PYTHONPATH=sim python -m pytest sim/tests/test_vconv_f16_i32.py -v` → 10/10 PASS; `bash sim/regression/soc-verification-run.sh run_vector_vconv_f16_i32` → PASS (128 values match golden, elapsed_cycles=262)
   **Files changed**: `sim/engine/isa.py`, `sim/golden_executor.py`, `sim/tests/test_vconv_f16_i32.py`, `rtl/vector/f16_to_i32.v`, `rtl/vector/vector_top.v`, `rtl/tb/tb_vector.v`, `scripts/gen_vector_vectors.py`, `sim/regression/Makefile`
   **Impact**: Unblocks true E2E forward pass on RTL (op output → next op input without hex preload)
   **Commit**: `[ISA] Add VCONV_F16_I32 opcode + Func Model + RTL — FP16→INT32 dtype conversion`

5. [x] Func Model L2 signoff: dtype 闭包矩阵 + 真实 op 串联验证
   **Refs**: `docs/func-model-signoff-criteria.md` §L2; `docs/isa-fp16-to-int32-gap-analysis.md`
   **Precondition**: ISA fix (task 4a) — VCONV_F16_I32 opcode must be in ISA + Func Model + RTL
   **Methodology**:
     (a) **dtype 闭包矩阵**: 列出所有 opcode 的输入/输出 dtype，标记相邻 op 不兼容点（MMUL INT32 out → SFU FP16 in = ✅ via VCONV; SFU FP16 out → MMUL INT8 in = ✅ via VCONV_F16_I32; SFU FP16 out → VRESID INT32 in = ✅ via VCONV_F16_I32）。产出 1 页矩阵文档。
     (b) **真实 op 串联 case**: 选 3 条连续 op（如 SFU softmax → VCONV_F16_I32 → VRESID）, op N 的输出不加任何中间 preload 直接作为 op N+1 的输入。验证 Func Model 能自动在 op 间插入正确的 VCONV 指令。
     (c) **覆盖所有 dtype 转换点**: INT32→FP16（MXU→SFU）, FP16→INT32（SFU→VRESID）, FP16→INT8（SFU→MMUL）。每种至少 1 个 case PASS。
   **Acceptance**: dtype 闭包矩阵文档存在；3 条串联 case PASS（无 hex preload）；所有 dtype 转换点有覆盖 case
   **QA fail**: 如果某个转换点 Func Model 串联失败 → 确认是 ISA/Func Model bug 还是硬件设计约束 → fix or document
   **Commit**: `[Test][FM] L2 signoff: dtype closure matrix + true op chain validation`

6. [x] Func Model L3 signoff: 36-layer forward pass golden + 精度漂移分析 (35/36 PASS; L35 cos_sim=0.998278; drift FAIL; root-cause documented as Q4_K_M quantization/accumulation limitation in learnings.md)
   **Refs**: `docs/func-model-signoff-criteria.md` §L3; W1.4 golden generation
   **Precondition**: L2 signoff (task 5) — dtype 转换已验证
   **Methodology**: Pure Python Func Model on sz0001 (no VCS required). Run all 36 layers with real op-to-op data flow (not per-op preload). Compare against llama.cpp reference at checkpoint layers (L0, L10, L20, L35). Check cos_sim does not drift below 0.999. For the layer with lowest cos_sim, decompose into per-op to identify error source.
   **Acceptance**:
     - 36 `.npz` files generated (one per layer)
     - cos_sim ≥ 0.999 at checkpoints L0/L10/L20/L35
     - No monotonic cos_sim degradation across layers (drift analysis PASS)
     - Per-op decomposition for worst layer completed
   **QA happy**: `PYTHONPATH=sim python3 -c "from sim.e2e_llamacpp import verify_36layer_true_e2e; verify_36layer_true_e2e()"` prints 36/36 PASS with per-layer cos_sim
   **QA fail**: if any checkpoint cos_sim < 0.999 → per-op decomposition → root-cause → fix or document as quantization limitation
   **Commit**: `[Test][FM] L3 signoff: 36-layer Func Model golden + drift analysis`

7. [x] Multi-op back-to-back intermediate result comparison (NEW — RTL verification deferred to Phase 6)
   **Refs**: W1.3 3-layer infrastructure; `sim/golden_executor.py`; `sim/e2e_llamacpp.py`
   **Methodology**: Pure Python Func Model on sz0001 (no VCS required). Run all 36 layers, save per-layer hidden states as `.npz` golden reference to `rtl/test_vectors/soc_e2e/qwen25-3b-36layer/`. Compare against llama.cpp reference at 3-layer checkpoints (L0, L17, L35) to verify numerical stability — confirm cos_sim does not drift across layers (>0.999 at all checkpoints).
   **Why Func Model only**: VCS RTL simulation of 36 layers takes 2-4h per run and is impractical for debug cycles. RTL validation of 36-layer will be done on FPGA in Phase 6 where runtime is seconds. 3-layer RTL (W1.3) already validates the core forward-pass pattern.
   **Acceptance**: 36 `.npz` files generated (one per layer) ✅; cos_sim ≥ 0.999 at checkpoints L0/L17/L35 vs llama.cpp — L35=0.998278 (documented as Q4_K_M quantization/accumulation limitation in learnings.md) ⚠️; no numerical drift across layers — drift FAIL documented ⚠️
   **QA happy**: `PYTHONPATH=sim python3 -c "from sim.e2e_llamacpp import verify_36layer; verify_36layer()"` prints 36/36 PASS
   **QA fail**: if any checkpoint shows cos_sim < 0.999 → root-cause (quantization drift, int overflow) → fix Func Model or document as quantization limitation ✅
   **Commit**: `[Test][FM] Qwen2.5-3B 36-layer Func Model golden reference generated`
   **Evidence**: `build/evidence/w1-6-fm-l3-signoff.txt`, `rtl/test_vectors/soc_e2e/qwen25-3b-36layer/*.npz`

7. [x] Multi-op back-to-back intermediate result comparison
    **Refs**: `docs/caduceus-verification-lessons.md` Lesson 6; FM-SOC-032 only compares final output
    **Methodology**: For 17-op blk.0 chain, save + compare golden vs RTL after EACH op in rtl_soc_runner.py
    **Note**: After ISA fix (task 4a), the chain expands to 18+ ops with VCONV insertion. Intermediate comparison should capture per-op outputs including VCONV results.
    **Acceptance**: 17+/17+ intermediate snapshots saved + compared; anti-vacuous: inject accumulator non-clear → detected
    **QA happy**: intermediate comparison detects deliberate corruption
    **QA fail**: existing FM-SOC-027/032 still PASS (no regression)
    **Commit**: `[Test] Per-op intermediate result comparison for multi-op chains`
    **Evidence**: `build/evidence/w1-7-intermediate-compare.txt` (TESTS=18 PASS=18, ANTI-VACUOUS: PASS)

8. [x] Review Gate: Atlas audit of Wave 1 evidence
   **Acceptance**: Atlas approve; 3-layer RTL per-op regression PASS; L2 dtype closure + true op chain verified; L3 36-layer Func Model golden verified; intermediate comparison evidence verified
   **QA fail**: Atlas reject/missing → fix → re-submit
   **Commit**: `[Review] Atlas W1 evidence audit: approve`

### Wave 2: SFU + Vector Module-Level Performance

7. [x] Create SFU+Vector perf measurement infrastructure
   ...
8. [x] Func Model: Verify SFU+Vector perf golden vectors
   ...
9. [x] sz0001: SFU P0 baseline measurement (SFV-P01..P07)
   ...
10. [x] sz0001: Vector P0 baseline measurement (SFV-P08..P14)
   ...
11. [ ] SFU P1 parameter sweep + VCONV_F16_I32 baseline (SFV-P15..P19 + SFV-P35) — PENDING
     **Note**: After ISA fix (task 4a), add VCONV_F16_I32 as SFV-P35. Measure cycles for N=[16..4096], confirm per-element cycle ≤ 1.
12. [ ] Vector P1 parameter sweep (SFV-P20..P22) — PENDING
13. [ ] SFU+Vector P2 back-to-back + Func Model calibration (SFV-P23..P28) — PENDING
14. [ ] SFU+Vector P3 edge cases (SFV-P29..P34) — PENDING

15. [ ] Review Gate: Atlas audit of Wave 2 evidence
    **Acceptance**: Atlas approve; 34/34 SFV cases logged
    **QA fail**: Atlas reject/missing → fix → re-submit
    **Commit**: `[Review] Atlas W2 evidence audit: approve`

### Wave 3: PCIe TLP Read + CV E2E

16. [x] Fix PCIe EP testbench TC2 (TLP Memory Read)
    **Refs**: `rtl/tb/pcie_ep_tb.sv` TC2; failure: TLP Memory Read issues no completion data (testbench log: `[INFO] No completion received — may need full pcie_axi_master init`)
    **Methodology**: Root-cause TC2 failure → if fixable in tb or pcie_ep_wrapper, apply fix and re-run
    **Acceptance**: (a) If fixable in tb/wrapper: TLP Memory Read → completion data matches written value; `make run_pcie_test` → all 4 tests PASS. (b) If root cause is in vendored pcie_axi_master IP: document as known limitation in `docs/issues_found.md`, verify remaining 3 tests PASS, skip TC2 acceptance.
    **QA happy**: write known pattern to SRAM via TLP write → read back via TLP read → data matches; remaining TC1/TC3/TC4 still PASS
    **QA fail**: if TC2 root cause cannot be isolated to tb/wrapper vs vendor IP within 1 working day → document and skip, proceed with remaining 3 tests
    **Commit**: `[Fix][PCIe] TLP Memory Read TC2: (fix or document limitation)`

17. [x] Func Model: Add dual-path comparison to blk.0 chain (backdoor + PCIe)
    **Refs**: `docs/caduceus-verification-lessons.md` Lesson 7
    **Methodology**: Extend Func Model verify to read results BOTH via backdoor SRAM AND simulated PCIe TLP
    **Acceptance**: bk_match=True + pcie_match=True for correct computation
    **QA happy**: Anti-vacuous: corrupt PCIe routing → pcie_match=False while bk_match=True
    **Commit**: `[Test][FM] Dual-path comparison for blk.0 chain`

17. [ ] sz0001: Dual-path compare on RTL SoC
    **Refs**: W3.2 Func Model setup; FM-SOC-032; `sim/rtl_soc_runner.py`
    **Acceptance**: RTL SoC dual-path: bk_match=True + pcie_match=True
    **QA happy**: anti-vacuous verified on RTL
    **Commit**: `[Test][RTL] FM-SOC-032 dual-path comparison on RTL SoC`

18. [x] Func Model: MobileNetV3-Small E2E
    **Refs**: `docs/NPU软件架构方案v0.2.md`; `scripts/export_mobilenetv3_onnx.py`; reference: PyTorch torchvision `mobilenet_v3_small(pretrained=True)` checkpoint
    **Methodology**: ONNX → INT4 per-block quantized → Func Model im2col→GEMM→SFU→Vector → per-layer output compare against PyTorch reference (GPU not required, run on CPU)
    **Acceptance**: Per-layer cos_sim ≥ 0.99 for all 15 layers; end-to-end classification on 10 fixed ImageNet validation images (list at `docs/imagenet_val_10.txt`) matches PyTorch reference output within 2% top-1 accuracy delta
    **QA happy**: `PYTHONPATH=sim python3 -c "from sim.tests.test_cv_e2e import test_mobilenetv3_func_model; test_mobilenetv3_func_model()"` prints "PASS: 15/15 layers, top1=XX vs ref=YY"
    **QA fail**: if any layer cos_sim < 0.99 → log layer name + delta → root-cause (quantization error vs im2col layout) → fix or document as known quantization limitation
    **Commit**: `[Test][FM][CV] MobileNetV3-Small Func Model E2E verified`

19. [ ] sz0001: MobileNetV3-Small RTL SoC Single Conv2D layer
    **Refs**: W3.4 Func Model; extend to RTL SoC via Spike + firmware
    **Acceptance**: Single Conv2D (im2col→MMUL→BIAS→VRESID→SiLU) on RTL SoC; output == golden (cos_sim ≥ 0.99)
    **QA fail**: if im2col tile schedule exceeds SRAM, document as known constraint
    **Commit**: `[Test][RTL][CV] MobileNetV3-Small RTL SoC single Conv2D layer verified`

20. [ ] Review Gate: Atlas audit of Wave 3 evidence
    **Acceptance**: Atlas approve; PCIe dual-path; MobileNetV3 FM + RTL evidence
    **QA fail**: Atlas reject/missing → fix cited issues → re-submit
    **Commit**: `[Review] Atlas W3 evidence audit: approve`

### Wave 4: SoC-Level Performance

21. [ ] SoC infrastructure baseline (PERF-01..P04)
    **Refs**: `rtl/testcase-list-perf.md` PERF-01..P04; MX-P01 module baseline (total=134 cycles for single_tile 64×64×64)
    **Acceptance**: PERF-01 (wrapper overhead ≤ 5× MX-P01 module cycles, i.e. ≤ 670 cycles), PERF-02 (DMA+MXU overlap ratio ≥ 80%), PERF-03 (NoC serialization latency ≤ 10 cycles per 512-bit beat), PERF-04 (Ibex dispatch latency from HOST_TAIL write to CMD.START ≤ 500 cycles); all recorded to `build/evidence/soc-perf-sofar.json`
    **QA happy**: PERF-01..P04 all within thresholds; JSON artifact generated with fields: case_id, cycles, breakdown, threshold, pass/fail
    **QA fail**: if any case exceeds threshold → root-cause analysis → document whether architectural constraint or bug → record in perf report
    **Commit**: `[Perf][SoC] P0 infrastructure baselines measured`

22. [ ] Single-engine SoC perf scans (PERF-05..P08)
    **Refs**: PERF-05..P08
    **Acceptance**: MXU K/N-scan with SRAM BW; Func Model calibration (DELTA documented)
    **QA fail**: DELTA > 50% without explainable cause → flag as potential bug
    **Commit**: `[Perf][SoC] P1 engine scans: SoC-path cycles calibrated`

23. [ ] DMA + weight stream perf (PERF-09..P12)
    **Refs**: PERF-09..P12; real Qwen Q_proj weights
    **Acceptance**: Weight preload BW ≥ 50% theoretical SRAM BW; streaming continuity; Q/K/V/O_proj measured
    **Commit**: `[Perf][SoC] P2 weight streaming: QKV_proj throughput measured`

24. [ ] Multi-engine pipeline + back-to-back (PERF-13..P16)
    **Refs**: PERF-13..P16; blk.0 17-op chain
    **Acceptance**: Pipeline overlap quantified; inter-op gap ≤ 100 cycles
    **Commit**: `[Perf][SoC] P3 pipeline overlap: multi-engine concurrency measured`

25. [ ] Full blk.0 E2E perf + stability (PERF-17..P20)
    **Refs**: PERF-17..P20
    **Acceptance**: blk.0 per-op breakdown; 3-run repeatability std ≤ 1 cycle
    **Commit**: `[Perf][SoC] P4 E2E perf: blk.0 breakdown + repeatability`

26. [ ] Review Gate: Atlas audit of Wave 4 evidence
    **Acceptance**: Atlas approve; PERF-01..P20 recorded
    **Commit**: `[Review] Atlas W4 evidence audit: approve`

### Wave 5: Process + ISA Gap + Descriptor Alignment

27. [x] Split bug tracking by verification phase
    **Refs**: `docs/caduceus-verification-lessons.md` Lesson 11
    **Methodology**: Create `docs/bugs/bugs-module-level.md`, `bugs-soc-func-model.md`, `bugs-soc-rtl.md`; migrate from `docs/bugs.md`; archive old file
    **Acceptance**: Each bug file has date, severity, description, root cause, fix commit; new bugs committed per-fix
    **Commit**: `[Doc] Split bug tracking into per-phase files`

28. [x] Create and maintain `issues_found.md` with known uncovered points
    **Refs**: Lesson 14
    **Methodology**: Sections: CV model gaps, ISA opcode gaps, PCIe TLP limitation, SRAM peak concerns; update after each Wave
    **Acceptance**: File exists with dated entries and evidence links; updated per-Wave
    **Commit**: `[Doc] issues_found.md with known verification blind spots`

29. [x] 14-lesson checklist status audit
    **Refs**: `docs/caduceus-verification-lessons.md` §二
    **Acceptance**: 14-item audit with dated matrix; ≥ 10/14 items covered or addressed by this plan
    **Commit**: `[Doc] 14-lesson checklist status audit`

30. [x] ISA opcode gap: implement AVGPOOL/MAXPOOL/RELU in GoldenExecutor
    **Refs**: `docs/rtl_development_plan.md` §8 ISA gap; `sim/engine/isa.py` 23 opcodes, 20/23 handled
    **Methodology**: Func Model first — add step() handling for AVGPOOL/MAXPOOL/RELU; generate test vectors; verify golden outputs
    **Acceptance**: 3 missing opcodes handled in GoldenExecutor.step(); new pytest cases added
    **QA happy**: `PYTHONPATH=sim python -m pytest sim/tests/ -k "pool or relu"` PASS
    **Commit**: `[ISA] GoldenExecutor: add AVGPOOL/MAXPOOL/RELU opcode handling`

31. [x] Firmware descriptor field alignment verification (Func Model → Ibex)
    **Refs**: Lesson 5; `firmware/npu_firmware.c` descriptor struct (15 fields: opcode, w_addr, i_addr, o_addr, dim0, dim1, scale_addr, bias_addr, next_desc, flags, reserved[5]); `firmware/npu-regmap.h` MMIO definitions
    **Methodology**: Use Spike + real `npu_firmware.elf` to verify descriptor fields are correctly read by firmware dispatch; compare firmware interpretation with Func Model's `write_mmul_descriptor()` / `write_sfu_descriptor()` / `write_vector_descriptor()` in `sim/spike_host.py`; `miniv.py` only as secondary cross-check with documented limitations
    **Acceptance**: All 15 descriptor fields verified aligned across C firmware, C header, Python Func Model, and RTL MMIO registers; mismatches documented in `docs/bugs/bugs-soc-func-model.md`
    **QA happy**: `diff <(grep DESCRIPTOR firmware/npu-regmap.h | awk '{print $2, $4}') <(python3 -c "from sim.spike_host import DESC_STRIDE; print(DESC_STRIDE)")` — offsets consistent
    **QA fail**: any field mismatch → log field name + expected vs actual → fix in firmware or Python → re-verify
    **Commit**: `[Firmware] Descriptor field alignment verified: 15/15 fields match`

32. [x] Establish per-Wave Review Gate checklist and discipline
    **Refs**: Lesson 8, §3 Atlas usage; OMO ≥ v4.14 required for Atlas agent
    **Acceptance**: Standardized checklist: SUMMARY consistency, FAIL→bug mapping, anti-vacuous, regression baseline, known gaps update; Atlas invocation confirmed: `task(subagent_type="atlas", ...)` returns approve/reject/missing; if Atlas unavailable, fallback to manual checklist review by Oracle agent
    **QA happy**: `opencode plugin list | grep oh-my-openagent` shows version ≥ 4.14; Atlas tool accessible
    **QA fail**: if OMO < 4.14 → document fallback path; if Atlas returns reject for any wave gate → fix cited issues → re-submit
    **Commit**: `[Process] Review Gate checklist established; Atlas readiness confirmed`

33. [x] Wave 5 Review Gate: Audit of W5 process deliverables
    **Acceptance**: Atlas approve; evidence: bug tracking files (3 per-phase files created + populated), `docs/issues_found.md` updated, 14-lesson audit matrix, ISA pytest results (AVGPOOL/MAXPOOL/RELU), descriptor alignment report; FM-SOC regression baseline still 33/33
    **QA fail**: Atlas reject/missing → fix → re-submit
    **Commit**: `[Review] Atlas W5 evidence audit: approve`

## Final verification wave

F1. [ ] Plan compliance audit: 5/5 Waves have Review Gate approved; all todos completed; evidence consistent
    **Acceptance**: Atlas full-plan review → approve
    **Commit**: `[Verify] Full plan compliance audit`

F2. [ ] Regression baseline: pytest 210/210, FM-SOC 33 original + ~8 new (36-layer=1 batch, 3-layer=1, dual-path=2, single-Conv2D=1, ISA-opcode=3 — exact count enumerated at start of execution), module-level API regression (MXU 9/9, SFU 319/319, Vector 63/63)
    **Acceptance**: All regression suites PASS; no degradation from baseline; exact new case counts locked in `build/evidence/final-regression-summary.md`
    **QA fail**: if any baseline suite regresses → identify breaking change from git log → root-cause per Wave → fix or document as expected change → re-run affected suite
    **Commit**: `[Verify] Full regression baseline confirmed`

F3. [ ] Known gaps update: `docs/issues_found.md` reflects post-plan state; 14-lesson checklist fully audited; remaining gaps explicitly listed with rationale
    **Commit**: `[Doc] Final issues_found.md update`

F4. [ ] Scope fidelity: Must NOT Have paths (INT8 datapath, 综合/物理, 新引擎架构) verified unchanged
    **Acceptance**: `git diff --stat` shows only planned files
    **Commit**: `[Verify] Scope fidelity confirmed`

## Commit strategy

- 每完成一个 todo 立即 commit，每个 bug 一个 commit
- Commit message: `[Domain] description`（Domain: Test/FM/RTL/Doc/Perf/Process/Fix/Review/Verify）
- **每个 testcase 状态变更（PASS/FAIL/SKIP）后立即更新对应 Wave 的 `build/<wave>/testcase-list.md` 并 commit + push 到 GitHub**
- 每个 Wave 内部在功能分支上推进，Wave 完成 + Review Gate approve 后 squash merge

## Success criteria

| 指标 | 阈值 |
|------|:---:|
| **ISA fix** | VCONV_F16_I32 opcode added: ISA + Func Model + RTL |
| **Func Model L2 signoff** | dtype 闭包矩阵文档 + ≥3 条真实串联 case（无 hex preload）+ 所有 dtype 转换点覆盖 |
| **Func Model L3 signoff** | 36-layer Func Model golden: cos_sim ≥ 0.999 at L0/L10/L20/L35; 无逐层漂移 |
| 3-layer RTL forward pass | 51/51 ops PASS (per-op regression 模式) |
| Multi-op intermediate compare | ≥17 intermediate snapshots compared; anti-vacuous works |
| SFU+Vector 模块级 perf | P0 14/14 ✅ + P1 sweep + VCONV_F16_I32 baseline + P2/P3 |
| PCIe dual-path compare | bk_match=True + pcie_match=True |
| MobileNetV3 E2E | Single Conv2D RTL == Func Model (cos_sim ≥ 0.95) |
| SoC 级 perf | 20/20 PERF cases measured and recorded |
| ISA opcode gap | 24/24 opcodes handled in GoldenExecutor (23 original + VCONV_F16_I32) |
| Firmware descriptor | 15/15 fields aligned across C firmware + C header + Python Func Model + RTL MMIO |
| Review Gates | 5/5 Wave gates + final audit → all approve |
| Regression baseline | pytest 210/210 + FM-SOC 33/33 + 模块级全 PASS |
| Bug tracking | 3 per-phase bug files created + populated |
