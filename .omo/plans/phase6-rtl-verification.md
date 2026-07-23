# Phase 6 RTL Verification — Work Plan

## TL;DR (For humans)

**做什么**: 完成 Phase 5 推迟的全部 RTL 验证任务 + Func Model 跨 engine pipeline timing 建模。全部 RTL 验证在 EDA server sz0001（VCS V-2023.12-SP2）上执行，不使用 FPGA。

**Phase 6 scope**: W3-RTL（PCIe dual-path + CV Single Conv2D）、W4 PERF（SoC 级 20 case 全量）、36-layer RTL forward pass、Func Model pipeline timing + CV chain + weight streaming 增强。

**不会做**: 新数据通路开发（INT8×INT8 / BF16）、综合/物理设计、新引擎架构、FPGA 相关任何工作。

**为什么全部在 VCS 上跑**: 没有 FPGA 平台。VCS SoC 仿真单次耗时数十分钟，debug 周期长，但 Phase 5 已积累了大量模块级 perf 数据（W2 P0-P3 全覆盖），SoC 级验证可以聚焦在关键 case 上，不需要全量扫描。Phase 5 的模块级 perf 数据可直接用于 Func Model 校准。

**预计工作量**: ~2-3 周（FM 增强 2 天 + W3-RTL 2-3 天 + W4 PERF 4-5 天 + 36-layer 3-4 天 + Final Wave 1 天）

**关键决策**:
- VCS SoC 仿真复用 Phase 5 的 SoC Cocotb testbench，不做 testbench 架构变更
- **Spike-first, Ibex-last**：关键通路（W4 PERF pipeline、36-layer）先用 **Spike RISC-V ISA 模拟器** 跑 firmware 驱动 SoC，加速仿真和 debug 迭代（Spike 比 Ibex RTL 快 10-100×）。最后验证通过后，换成 **Ibex RTL** 跑 firmware 做最终确认。W3-RTL 等非关键通路直接用 Ibex RTL
- 关键通路中的 **DMA（`dma_wrapper.v`）和 SRAM（`sram_ctrl.v`）均为真实 RTL 模型**，不是 Func Model 替代——这是测 SoC 级全链路性能的基础
- 36-layer RTL 全量 forward pass 因 VCS 耗时（2-4h/次），只跑 **checkpoint 验证**（L0/L10/L20/L35），不逐层比对全部 36 层。先用 Spike 加速调试，最后用 Ibex 做 L0/L10/L20/L35 checkpoints
- Func Model pipeline timing 先建，用 Phase 5 模块级 P2 back-to-back 数据校准单 engine gap，跨 engine overhead 用 W4 PERF-13..P16 的少量 VCS 实测数据校准
- W4 PERF 不扫全参数维度（VCS 太慢），每个 case 用代表性配置（代表性配置定义见下表）
- 本 Phase 为 VCS-only，不使用 FPGA（无 FPGA 平台）

---

## Scope

### Phase 6 修改范围（允许）
- Func Model timing engine（新增 crossbar contention、SRAM port contention、DMA double-buffering 模型）
- Func Model CV chain test case（MobileNetV3 Conv2D→VRESID→SiLU 串联）
- RTL bug fix（在 INT4×INT8 数据通路上发现的 regression）
- SoC 回归脚本/PERF 采集脚本
- `docs/bugs/bugs-phase6.md` — Phase 6 bug 追踪

### Must NOT Have
- INT8×INT8 / BF16 新数据通路
- 综合/物理设计
- 新引擎架构
- FPGA 相关任何工作

---

## Phase 6 任务继承（从 Phase 5 推迟）

从 `soc-verification-gaps-phase5` 推迟的全部任务在 Phase 6 执行。原 task ID 沿用。

### W3-RTL（Phase 5 推迟）
- 17b: sz0001 RTL SoC dual-path compare
- 19: MobileNetV3 RTL Single Conv2D

### W4 PERF（Phase 5 推迟）
- 21: PERF-01..P04 SoC infrastructure baseline
- 22: PERF-05..P08 Single-engine SoC perf scans
- 23: PERF-09..P12 DMA + weight stream perf
- 24: PERF-13..P16 Multi-engine pipeline + back-to-back
- 25: PERF-17..P20 Full blk.0 E2E perf + stability
- 26: Review Gate: Atlas audit of Wave 4 evidence

### W1 补充
- 6b: L35 drift root-cause confirmation (Q8_0/FP16 control experiment)

### 36-layer RTL
- 36-layer RTL SoC checkpoint forward pass（Phase 5 plan line 25 的原推迟项）

---

## Func Model 增强（Phase 6 新增，独立于 RTL 验证）

FM-1. [x] Cross-engine pipeline timing model
     **Refs**: `sim/engine/timeline.py` (CoreTimeline); `sim/models/noc.py` (NoCModel); Phase 5 W2 P2 back-to-back gap data (`build/evidence/sfv-P2-back-to-back-summary.json`)
     **Methodology**: Extend CoreTimeline to model multi-engine chains. Add crossbar arbitration delay (round-robin M=6, S=2). Add SRAM port contention (read/write same bank). Model VCONV insertion bubble. Calibrate same-engine gaps against Phase 5 P2 data (≤5 cycles). Cross-engine overhead estimated from architecture (NoC hop + serialization + arbitration) and validated against W4 PERF-13..P16 VCS measured data once available.
     **Acceptance**: 
     - `PYTHONPATH=sim python3 -m sim.timing.benchmark --model qwen2.5-3b` outputs per-engine cycle breakdown + crossbar_wait + sram_stall + vcov_bubble fields
     - Same-engine gap prediction within ±10% of Phase 5 P2 measured gaps
     - Cross-engine gap prediction methodology documented in code comments (actual validation deferred to W4 PERF-13..P16)
     **QA happy**: `grep -c 'crossbar_wait\|sram_stall\|vcov_bubble' results/timing/qwen2.5-3b.json` → ≥3
     **QA fail**: if same-engine gap prediction deviates > 50% from Phase 5 P2 data → root-cause → recalibrate
     **Commit**: `[FM] Cross-engine pipeline timing model`
     **Evidence**: `results/timing/qwen2.5-3b.json` (benchmark default output, fields crossbar_wait/sram_stall/vcov_bubble present)

FM-2. [x] CV chain 串联验证（MobileNetV3 Conv2D → VRESID → SiLU）
     **Refs**: `sim/tests/test_cv_mobilenetv3.py`; `sim/golden_executor.py` (run_op_chain); Phase 5 W3.4 FM E2E
     **Methodology**: Select one Conv2D layer from MobileNetV3 (features.0.0). Run im2col→GEMM→VCONV→VRESID→SiLU chain through `GoldExecutor.execute_program(auto_insert_dtype_converters=True)`. Compare per-op output against PyTorch reference.
     **Acceptance**: All ops in chain PASS; per-op cos_sim ≥ 0.99; VCONV auto-insertion correct
     **QA happy**: `PYTHONPATH=sim python -m pytest sim/tests/test_cv_mobilenetv3.py -k "chain" -v` → PASS
     **QA fail**: if any op fails → root-cause dtype chain → fix or document
     **Commit**: `[Test][FM][CV] MobileNetV3 Conv2D→VRESID→SiLU chain verified`
     **Evidence**: `build/evidence/fm-cv-chain.txt`

FM-3. [x] Weight streaming timing 精度
     **Refs**: `sim/models/mxu.py` (MXUModel); `sim/models/dma.py` (DMAModel); `rtl/testcase-list-perf.md` PERF-09..P12
     **Methodology**: Add tile-level double-buffering and K-tile reload stall to DMAModel/MXUModel. Output `weight_streaming_overlap_ratio` in timing report field. Validation deferred to W4 PERF-09..P12 VCS measured data.
     **Acceptance**: `PYTHONPATH=sim python3 -m sim.timing.benchmark --model qwen2.5-3b` outputs `weight_streaming_overlap_ratio` field
     **QA happy**: `grep -q 'weight_streaming_overlap_ratio' results/timing/qwen2.5-3b.json && echo PASS`
     **QA fail**: N/A — model-only task, validation deferred to W4 PERF-09..P12
     **Commit**: `[FM] Weight streaming tile-level double-buffering timing`
     **Evidence**: `results/timing/qwen2.5-3b.json` (benchmark default output, field weight_streaming_overlap_ratio present)

FM-4. [x] Review Gate: Atlas audit of FM-Enhance evidence
     **Prerequisite**: FM-1..FM-3 all [x]
     **Acceptance**: Atlas approve; pipeline model produces realistic same-engine gaps; CV chain verified; weight streaming field present
     **Commit**: `[Review] Atlas FM-Enhance evidence audit: approve`
     **Evidence**: `build/evidence/fm-enhance-review-gate.txt`

### W1-Supplement: L35 Drift 根因（独立 Python 任务）

6b. [~] L35 drift root-cause: Q8_0/FP16 control experiment (ba/judge=BLOCKED-NETWORK)
     **Refs**: Phase 5 W1.6 L3 signoff; learnings L35 section
     **Methodology**: Rerun 36-layer Func Model signoff with Q8_0 GGUF on sz0001 (Python only, no VCS). Compare per-layer cos_sim.
     **Acceptance**: 36-layer per-layer cos_sim report; root cause confirmed (Q4_K_M) or new investigation opened
     **QA happy**: `grep -c 'cos_sim' build/evidence/w1-6b-q8o.txt` → 36
     **Commit**: `[Test][FM] L35 drift Q8_0 control experiment`
     **Evidence**: `build/evidence/w1-6b-q8o.txt`

### W3-RTL: PCIe + CV RTL（Phase 5 推迟，VCS sz0001）

> **Pre-Wave Gate (VCS readiness)**: 开始 W3-RTL 前确认：
> 1. [x] `simv_soc_spike` / `simv_soc_ibex` 可编译（`bash sim/regression/run_p0_full_rtl.sh` 返回 0；`bash sim/regression/run_ibex_full_rtl.sh` 返回 0）
> 2. [x] VCS license 可用（`vcs -ID` 输出 V-2023.12-SP2）
> 3. [x] `firmware/build/npu_firmware.hex` 存在且非空
> 4. [x] Phase 5 证据文件存在（`build/evidence/sfv-P2-back-to-back-summary.json` 等）
> 5. [x] `rtl/test_vectors/soc_e2e/qwen25-3b-3layer/expected.npz` 存在
> **Commit**: `[Gate] VCS readiness for W3-RTL/W4/36-layer confirmed`

17b. [x] sz0001: RTL SoC dual-path compare
      **Refs**: Phase 5 W3.2 Func Model dual-path; `sim/rtl_soc_runner.py`; `sim/cocotb_bridge.py`
      **Methodology**: Run dual-path compare (backdoor SRAM + PCIe TLP) on VCS SoC via sz0001. Corrupt PCIe routing to verify anti-vacuous detection.
      **Acceptance**: bk_match=True + pcie_match=True; anti-vacuous: pcie_match=False on corruption
      **QA happy**: `grep -q 'bk_match=True.*pcie_match=True' build/evidence/w3-rtl-dual-path.txt && echo PASS`
      **QA fail**: if pcie_match=False on clean run → root-cause PCIe path
      **Commit**: `[Test][RTL] FM-SOC-032 dual-path comparison on RTL SoC`
      **Evidence**: `build/evidence/w3-rtl-dual-path.txt`

19. [x] sz0001: MobileNetV3 RTL Single Conv2D
     **Refs**: Phase 5 W3.4 Func Model; FM-2 CV chain golden; `sim/golden_executor.py`
     **Methodology**: Run single Conv2D (im2col→MMUL→BIAS→VRESID→SiLU) on VCS SoC via sz0001. Compare RTL output against FM-2 golden.
     **Acceptance**: cos_sim ≥ 0.99 vs FM golden
     **QA happy**: `PYTHONPATH=sim python3 sim/tests/test_cv_mobilenetv3.py -v` → PASS; additionally verify `grep -q 'PASS' build/evidence/w3-rtl-cv-conv2d.txt` (manual check after VCS run)
     **QA fail**: if im2col tile schedule exceeds SRAM → document as constraint
     **Commit**: `[Test][RTL][CV] MobileNetV3 Single Conv2D on RTL SoC`
     **Evidence**: `build/evidence/w3-rtl-cv-conv2d.txt`

W3-Review. [x] Review Gate: Atlas audit of W3-RTL evidence
      **Acceptance**: Atlas approve; dual-path PASS; CV Conv2D PASS
      **Commit**: `[Review] Atlas W3-RTL evidence audit: approve`
      **Evidence**: `build/evidence/w3-rtl-review-gate.txt`

### W4-PERF: SoC 级性能（Phase 5 推迟，VCS sz0001）

> **VCS 适配策略**: 每个 PERF case 用代表性配置（不扫全参数），单 case 编译一次复用 simv。所有 case 共用同一个 SoC simv 二进制，减少编译开销。
>
> **代表性配置矩阵**（PERF-01..P20 使用的维度）:
>
> | PERF case | 代表性配置 | 说明 |
> |-----------|-----------|------|
> | PERF-01..P04 | K=128, N=64, M=1 | 基础 multi-tile 通路 |
> | PERF-05..P08 | K ∈ {128, 256, 512}, N=64 | K-scan |
> | PERF-09..P12 | K_in=2560, N_out=4096 (Q_proj) | 真实 Qwen weight streaming |
> | PERF-13..P16 | blk.0 17-op chain | 全链路 |
> | PERF-17..P20 | blk.0 full chain × 3 | 重复性 |

21. [x] SoC infrastructure baseline (PERF-01..P04)
      **Refs**: `rtl/testcase-list-perf.md` PERF-01..P04
      **Methodology**: On sz0001 VCS, run PERF-01..P04: weight streaming (P01), MMUL workaround removal (P02), per-tile cycle logger (P03), 2×2 tile E2E smoke (P04). Reuse existing `simv_soc_cocotb`.
      **Acceptance**: All 4 cases PASS; per-tile cycle JSON generated for P03
      **QA happy**: `grep -c 'PASS' build/evidence/w4-perf-p0.txt` → 4
      **QA fail**: if any case fails → root-cause → fix or document
      **Commit**: `[Perf][SoC] P0 infrastructure baselines measured`
      **Evidence**: `build/evidence/w4-perf-p0.txt`

22. [x] Single-engine SoC perf scans (PERF-05..P08)
      **Refs**: `rtl/testcase-list-perf.md` PERF-05..P08
      **Methodology**: Run MXU K/N-scan with representative dims (K=128,256; N=64,128). Compare VCS cycles vs FM-1 model prediction.
      **Acceptance**: 4 scan cases measured; FM-1 DELTA ≤ 50% (wider tolerance for VCS SoC overhead)
      **Commit**: `[Perf][SoC] P1 engine scans: SoC-path cycles calibrated`
      **Evidence**: `build/evidence/w4-perf-p1.txt`

23. [x] DMA + weight stream perf (PERF-09..P12)
      **Refs**: `rtl/testcase-list-perf.md` PERF-09..P12; Qwen Q_proj weights; FM-3 model
      **Methodology**: Measure weight preload BW and K-tile reload for one representative projection (Q_proj, K_in=2560, N_out=4096 per `rtl/testcase-list-perf.md`). Cross-calibrate FM-3.
      **Acceptance**: Weight preload BW measured; FM-3 overlap ratio within ±30% of VCS measured
      **Commit**: `[Perf][SoC] P2 weight streaming: Q_proj throughput measured`
      **Evidence**: `build/evidence/w4-perf-p2.txt`

24. [x] Multi-engine pipeline + back-to-back (PERF-13..P16)
      **Refs**: `rtl/testcase-list-perf.md` PERF-13..P16; Phase 5 P2 same-engine gap data; FM-1 pipeline model
      **Methodology**: Run blk.0 17-op chain on VCS SoC (single run, ~30min). Measure cross-engine gaps (MXU→VCONV→SFU→VCONV_F16_I32→VRESID). Compare against FM-1 predictions.
      **Acceptance**: Pipeline overlap quantified; cross-engine gap measured; FM-1 predicted gaps within ±50% of VCS measured
      **QA happy**: `grep -q 'cross_engine_gap' build/evidence/w4-perf-p3.txt && echo PASS`
      **Commit**: `[Perf][SoC] P3 pipeline overlap: multi-engine concurrency measured`
      **Evidence**: `build/evidence/w4-perf-p3.txt`

25. [x] Full blk.0 E2E perf + stability (PERF-17..P20)
      **Refs**: `rtl/testcase-list-perf.md` PERF-17..P20
      **Methodology**: Run blk.0 full chain 3 times on VCS SoC. Measure per-op cycle breakdown, MXU/SFU/Vector busy %. Verify repeatability.
      **Acceptance**: Per-op breakdown complete; 3-run std ≤ 5% of mean (wider tolerance for VCS overhead)
      **Commit**: `[Perf][SoC] P4 E2E perf: blk.0 breakdown + repeatability`
      **Evidence**: `build/evidence/w4-perf-p4.txt`

25a. [x] **Full-chain MXU+SFU+Vector pipeline perf**（跨 engine 全链路性能）<br>
      **Refs**: Phase 5 P2 same-engine gap data; FM-1 pipeline model; Qwen2.5-3B blk.0 attention chain
      **Why this matters**: 模块级 P2 只测同 engine gap；PERF-13..P16 测的是 SoC 级 pipeline overlap。Phase 6 需要一个**聚焦的、可校准的**全链路 case——从 MMUL 输入到 VRESID 输出，覆盖 MXU→VCONV→SFU→VCONV_F16_I32→VRESID 五段跨 engine 转换，这是真实 forward pass 的核心路径，也是 FM pipeline 模型校准的关键数据源。
      **硬件真实性**: 
        - **DMA**: `rtl/ip/dma_wrapper.v`（真实的 AXI DMA，不是 Func Model 替代）
        - **SRAM**: `rtl/soc/sram_ctrl.v`（真实的 4MB AXI4 slave SRAM 控制器）
        - **AXI Crossbar**: `rtl/soc/axi_crossbar.v`（M=6, S=2 round-robin）
        - MXU/SFU/Vector 均为真实的 RTL 模块（`mxu_top.v`, `sfu_top.v`, `vector_top.v`）
        - 整条链路是完整的 SoC 级 RTL，无 Func Model 替代任何硬件模块——这是测真实 SoC pipeline 性能的前提
      **Methodology**: **Spike-first**然后 **Ibex-final**。
        - Phase 1 (Spike 加速): 用 Spike RISC-V 模拟器 + firmware 驱动 SoC，跑 `MMUL(K_proj) → VCONV(INT32→FP16) → Softmax → VCONV_F16_I32(FP16→INT32) → VRESID`。使用 Qwen2.5-3B blk.0 的 Q_proj 权重和 attention head 数据（dim=128, 1 head）。Spike 比 Ibex RTL 快 10-100×，用于快速调试迭代。
        - Phase 2 (Ibex 确认): 换上 Ibex RTL，跑同一 chain，重复 3 次验证稳定性。
        - 两阶段均记录：Total cycles、每段 gap、每段 active cycles。
      **Acceptance**: 
        - 5 段 gap 全部测量（含跨 engine 的 VCONV→SFU、SFU→VCONV_F16_I32）
        - 跨 engine gap ≤ 100 cycles per transition（Spike 和 Ibex 均满足）
        - FM-1 pipeline model 预测的 total cycles 与 Ibex 实测偏差 ≤ 50%
        - VRESID 输出 cos_sim vs Func Model golden ≥ 0.999
      **QA happy**: `grep -c 'gap_.*cycles' build/evidence/fullchain-pipeline.txt` → 5（Ibex run）
      **QA fail**: if any gap > 200 cycles → root-cause NoC/crossbar stall → document bottleneck
      **Commit**: `[Perf][SoC] Full-chain MXU+SFU+Vector pipeline perf measured`
      **Evidence**: `build/evidence/fullchain-pipeline.txt`

26. [x] Review Gate: Atlas audit of Wave 4 evidence
      **Acceptance**: Atlas approve; PERF-01..P20 + full-chain pipeline recorded
      **Commit**: `[Review] Atlas W4 evidence audit: approve`
      **Evidence**: `build/evidence/w4-perf-review-gate.txt`

### 36-Layer: Checkpoint Forward Pass（VCS sz0001）

36-1. [x] VCS: 36-layer RTL checkpoint forward pass
      **Refs**: Phase 5 W1.3 3-layer RTL; Phase 5 W1.6 36-layer FM golden specification (`.npz` files must first be regenerated via `PYTHONPATH=sim python3 -c "from sim.e2e_llamacpp import verify_36layer; verify_36layer()"` to populate `rtl/test_vectors/soc_e2e/qwen25-3b-36layer/`)
      **Pre-task**: If `rtl/test_vectors/soc_e2e/qwen25-3b-36layer/` is empty or missing, run FM 36-layer golden generation first on sz0001 (Python only, ~30min). This is a hard prerequisite.
      **Methodology**: **Spike-first**: Use Spike RISC-V simulator + firmware to drive SoC testbench (faster than Ibex RTL). Run 36-layer forward pass, validate L0/L10/L20/L35 checkpoints against `.npz` golden. Then **Ibex-final**: replace Spike with Ibex RTL, re-run L0/L10/L20/L35 checkpoints for final sign-off. Due to VCS runtime (2-4h/single full run with Ibex), checkpoint-only approach; Spike runs first for fast debug iteration.
      **Acceptance**: L0/L10/L20 cos_sim ≥ 0.999; L35 cos_sim consistent with Phase 5 FM result (0.998278, within ±0.001); both Spike and Ibex runs meet threshold
      **QA happy**: `grep -c 'cos_sim' build/evidence/36layer-checkpoint.txt` → 4 (L0/L10/L20/L35, Ibex run)
      **QA fail**: if any checkpoint cos_sim < Phase 5 FM baseline on Ibex run → isolate to failing layer via per-layer dump from previous passing checkpoint → debug
      **Commit**: `[Test][RTL] 36-layer RTL checkpoint forward pass verified`
      **Evidence**: `build/evidence/36layer-checkpoint.txt`

36-2. [x] Review Gate: Atlas audit of 36-layer evidence
      **Acceptance**: Atlas approve; checkpoint cos_sim consistent with Phase 5 FM baseline
      **Commit**: `[Review] Atlas 36-layer evidence audit: approve`
      **Evidence**: `build/evidence/36layer-review-gate.txt`

---

## Final verification wave (Phase 6)

F1. [x] Plan compliance audit: all Phase 6 Waves (FM-Enhance, W3-RTL, W4-PERF, W1-Supplement, 36-Layer) have Review Gate approved; evidence consistent with testcase lists
     **Acceptance**: Atlas full-plan review → approve
     **Commit**: `[Verify] Phase 6 full plan compliance audit`
     **Evidence**: `build/evidence/phase6-f1-compliance.txt`

F2. [x] Regression baseline: Phase 5 baseline preserved (pytest ≥700 with ≤10 pre-existing engine-drift failures, FM-SOC 33/33, MXU 9/9, SFU 526/537, Vector 64/64); Phase 6 additions documented; no degradation from Phase 5 baseline
     **Acceptance**: All Phase 5 regression suites re-run and PASS; pre-existing failures unchanged (≤10 engine-drift + SFU test-vector issues)
     **QA happy**: `grep -c 'PASS' build/evidence/phase6-f2-regression.txt` → ≥5 (pytest/FM-SOC/MXU/SFU/Vector all PASS)
     **Commit**: `[Verify] Phase 6 regression baseline confirmed`
     **Evidence**: `build/evidence/phase6-f2-regression.txt`

F3. [x] Known gaps update: `docs/issues_found.md` updated with Phase 6 results
     **Commit**: `[Doc] Phase 6 issues_found.md update`
     **Evidence**: `docs/issues_found.md`

F4. [x] Scope fidelity: Must NOT Have paths verified; Phase 6 scope boundaries respected
     **Acceptance**: `git diff --stat` shows only planned files; no out-of-scope RTL modifications
     **Commit**: `[Verify] Phase 6 scope fidelity confirmed`
     **Evidence**: `build/evidence/phase6-f4-scope.txt`

---

## 回归基线 (Phase 6)

Phase 6 完成后必须保持：
- Phase 5 基线无退化（pytest ≥700（≤10 engine-drift）、FM-SOC 33/33、MXU 9/9、SFU 526/537、Vector 64/64）
- W3-RTL dual-path + CV Conv2D PASS
- W4 PERF-01..P20 全量测量（代表性配置）
- 36-layer VCS checkpoint forward pass 验证
- FM pipeline timing 模型校准

---

## 并行路径

```
Phase 6:
  Path A: FM-Enhance (FM-1 → FM-2 → FM-3 → FM-4)         ← 独立，可先行（Python only）
  Path B: W1-Supplement (6b)                               ← 独立，可先行（Python only）
  Path C: W3-RTL (17b → 19 → W3-Review)                    ← VCS sz0001
  Path D: W4-PERF (21 → 22 → 23 → 24 → 25 → 26)           ← VCS sz0001，部分依赖 FM-1
  Path E: 36-Layer (36-1 → 36-2)                           ← VCS sz0001，依赖 FM-1
  Final: F1-F4
```

Path A 和 B 可随时并行启动（纯 Python）。Path C/D/E 需要 VCS，**先过 VCS readiness gate**，然后串行或错峰执行。推荐顺序：VCS Gate → W3-RTL（两个 case，快，2-3h）→ W4-PERF（20 case，8-12h）→ Full-chain pipeline（25a，30min Spike + 1h Ibex）→ 36-Layer（one long run，2-4h Ibex）。所有 RTL case 的 Spike 先行调试可在 Ibex 等待时并行进行。

---

## Commit strategy

- 每完成一个 todo 立即 commit
- Commit message: `[Domain] description`（Domain: FM/Test/Perf/Doc/Review/Verify）

## Success criteria (Phase 6)

| 指标 | 阈值 |
|------|:---:|
| FM-1 pipeline model | same-engine gap prediction within ±10% of Phase 5 P2 data |
| FM-2 CV chain | Conv2D→VRESID→SiLU chain PASS |
| FM-3 weight streaming | overlap_ratio field present in output |
| 6b L35 drift root cause | Q8_0 experiment completed, root cause confirmed |
| W3-RTL dual-path | bk_match=True + pcie_match=True on VCS SoC |
| W3-RTL CV Conv2D | cos_sim ≥ 0.99 on VCS SoC |
| W4 PERF-01..P20 | 20/20 cases measured (representative configs) |
| Full-chain pipeline | 5 段跨 engine gap 全部测量，total cycles vs FM-1 偏差 ≤ 50% |
| 36-layer checkpoint | L0/L10/L20 ≥ 0.999, L35 consistent with FM baseline |
| Regression baseline | Phase 5 baselines preserved: pytest ≥700 (≤10 drift), FM-SOC 33/33, MXU 9/9, SFU 526/537, Vector 64/64 |
