# dse-funcmodel-prefill-closure — Learnings

## 2026-08-13 todo 1: prefill evidence/report refresh

- **Current prefill-2000 evidence** (task-20, post formula fix): `prefill_cycles=63,923,285,808`, `prefill_ms base=63,923.3ms`, `ttft_ms base=63,924.2ms`. The pre-fix values (`60,223,319,856` cycles / `60,223 ms`) are gone from the report.
- **prefill-128 Path A/B** (task-16) is self-consistent: A=108,640,230 vs B=108,639,758, diff 472 cycles, 0.0% error, both op_count=612 — well inside the 20% gate.
- **Where the stale numbers actually lived**: only section 8.3 (comparison block) and section 10 (conclusion) still carried `60,223 ms` / `TTFT ~60.2 s` after the canonical-formula fix commit (b4dbadd). Sections 3.2/7.2/8.1/8.2 were already refreshed by that commit.
- **Section 3.3 dual-path table is still stale** (`prefill-128` shows 4,091,517,432, and blk0/decode rows don't match task-16 evidence either). This is out of scope for todo 1 (report cleanup of 7.2/8.1/8.3/10 belongs to todo 4) — flag for todo 4 to reconcile section 3.3 against task-16 evidence.
- **Gate 3 in the spec** already describes prefill as compute-bound via `per_tile_compute = M*68` with BW x2 insensitivity — consistent with current numbers; no spec edit required by todo 1.
- **Verification**: signoff `run --reports uncertainty-kpis --cases qwen-prefill-2000` exits 0 with verdict pass; `grep "60,223"` on the report is empty.
# DSE ↔ Func Model Prefill/TTFT 闭环 — Learnings

## Todo 2: DSE TTFT 模型修复（2026-08-13）

- **根因**：`simulate_layer` 复用模块级 `_LLM_TRACE`（import 时以 batch_m=1 生成），
  且 CLI `--batch-m` 被 `choices=[1, 2]` 卡死 —— DSE 无法对 prefill（batch>1）建模 TTFT。
- **修复方式**：删除 `_LLM_TRACE` 初始化，改为 `_DEFAULT_LLM_SPEC = ("qwen2.5-3b", 1)` +
  `_MODEL_ALIAS`/`_PREFILL_BATCH_M` 模块变量；`simulate_layer(config, batch_m=None)` 按需
  `generate_trace_from_spec` 重新生成 trace（None → 1，decode 语义保持不变）；
  循环体抽成私有 `_simulate_ops()`，`simulate_prefill()`/`ttft_ms_from_prefill()` 复用，
  无重复 trace 逻辑、无新增全局可变状态（F2 审计项）。
- **单位陷阱**：`cycles × layers / freq_mhz` 是 µs（与 `tok_s_from_layer` 一致），
  转 ms 需再 `/1000`。`ttft_ms_from_prefill` 已按 ms 实现并单测锁定（1e6 cycles × 28 层 @1GHz = 28.0 ms）。
- **验收命令的隐含坑**：要求 `--quick --batch-m 128` 输出含 Block 64×64 的 ttft_ms，
  但原 quick dims 只有 128×128/128×256/256×256，根本没有 64×64。
  修复：quick dims 增加 `(64, 64)`（Block 64×64 是 RTL Phase 1 配置，也是本计划的目标配置）。
- **关键数值**（Block 64×64, INT4, 1GHz, LPDDR5-64b, WC）：
  - batch_m=1   → ttft_ms =   39.78, tok_s = 25.1
  - batch_m=128 → ttft_ms = 2649.49, tok_s = 25.1（tok_s 不随 batch 变化，decode 恒为 batch-1）
  - batch_m=2000 → ttft_ms = 41398.27；Func Model prefill-2000 TTFT = 63,924 ms
    → 比值 1.54×，落在 [0.5×, 2.0×] PASS 区间内（供 todo 3 建立 Gate 1b 使用）。
- **最小侵入**：`evaluate_config` 的 ttft_ms 通过 `PPA.config["ttft_ms"]` 携带、
  JSON 结果顶层再加 `ttft_ms`（非 CV；CV 置 0），避免改动 `engine/ppa_model.py` 的 PPA 结构，
  把改动面收敛在 `sim/design_space_explorer.py` + 测试两个文件。
- **兼容性**：HEAD 版 vs 新版默认 `--quick`（batch 1）逐 label 对比 —— 21 个公共配置
  tok_s/area_mm2/power_w 全部一致；pytest 6/6 通过；mutation（把 batch-m 改回 [1,2]）→ 128 被拒（exit 2）。

## Todo 3: DSE TTFT 目标 + Gate 1b 建立（2026-08-13）

- **DSE CLI 证据生成**：`--quick --batch-m 128/2000 --model-spec qwen2.5-3b` 均 exit 0；
  证据落在 `.omo/evidence/task-3-dse-ttft-m128.json` / `task-3-dse-ttft-m2000.json`。
  **坑**：从 `sim/` 目录运行时 `--output` 相对路径会写到 `sim/.omo/evidence/`，需移回仓库根 `.omo/evidence/`。
- **Block 64×64 @ 1GHz LPDDR5-64b (WC) TTFT 目标**（bloc 64×64 INT4 1000MHz WC LPDDR5-64b，tok_s=25.1 不变）：
  - M=128  → `ttft_ms = 2,649.49`
  - M=2000 → `ttft_ms = 41,398.27`
  - 与 todo 2 学习记录完全一致（2,649.49 / 41,398.27）。
- **Func Model 实测**：M=128 = task-16 `qwen25-3b-prefill-128` Path A total (108,640,230) × 36 / 1GHz / 1000
  = **3,911.05 ms**；M=2000 = task-20 `ttft_ms.base` = **63,924.19 ms**。
- **Gate 1b 判定**：M=128 比值 1.48×，M=2000 比值 1.54× —— 均落在 [0.5×, 2.0×] PASS 区间。
  差距来源：trace 结构（7-op layer vs 17-op DAG）+ 层内并行假设，量级一致可接受。
- **规格变更**（`.omo/notes/func-model-perf-verification-spec.md`）：
  - Gate 1 → Gate 1a (TPS) + Gate 1b (TTFT)；Gate 1a TPS 内容原样保留。
  - 判定汇总表新增 Gate 1b 行；section 5 修正 stale 记录
    （canonical TPS 10.99 → 30.75，比值 1.22×；"修复前 10.99" 仅作历史备注保留），
    新增 "DSE TTFT model fixed" 行（2026-08-13）。
  - 注意：section 4 S1 迁移表仍写 "TTFT (M=128) (未建模)" —— DSE TTFT 现已建模，留给 todo 4/后续刷新。
- **验证**：`grep "Gate 1b\|DSE TTFT"` 命中新增内容；DSE CLI 两条命令 exit 0。

## Todo 4: 验证报告更新（2026-08-13）

- **§3.3 双路径表已按 task-16 证据对齐**：blk0-decode=2,519,940/2,519,842；decode-c128-g1=2,536,628/2,536,628；prefill-16=15,053,832/15,053,690；prefill-128=108,640,230/108,639,758。修正前表格混用旧值（900,898 / 32,730,072 / 511,851,816 / 4,091,517,432），全部 0.0% PASS 不变。
- **新增 §3.5 "DSE TTFT 一致性 (Gate 1b)"**：Block 64×64 @ 1GHz LPDDR5-64b (WC) INT4；M=128: DSE 2,649.49 vs Func 3,911.05 = 1.48×；M=2000: DSE 41,398.27 vs Func 63,924.19 = 1.54×；均落在 [0.5×, 2.0×] PASS。来源：task-3-dse-ttft-m128/m2000.json + task-16 prefill-128 Path A + task-20 ttft_ms.base。
- **遗留陈旧数值清理**：§8.2 脚注 `1000/10.99≈91.0 ms` → `1000/30.75≈32.5 ms`（与表格 32.5 ms/token 一致）；§8.2 结论占比 82%~39% → 99%~66%，切换点 660 → ~1,967 tokens（63,924/32.5）；§8.3 `10.99 tok/s`/`33.7 ms` → `30.75 tok/s`/`32.5 ms`；§8.4 `128K cycles` → `136K cycles`；§10 `~11 tok/s` → `~31 tok/s`。
- **§9 限制第 4 条已重写**：从 "Prefill 分析基于 canonical formula 未验证" 改为 "已与 DSE BlockEngine 对齐"（canonical 公式修复 + DSE TTFT 模型修复 + Gate 1b 1.48×–1.54× PASS），残余差距仅来自 trace 结构差异。
- **§4 S1 迁移表说明**：报告的 §4 是 CV 结果，"S1 迁移表"（TTFT M=128 未建模）存在于 `.omo/notes/func-model-perf-verification-spec.md` §4（line 192），不在报告中。规格由 todo 3 拥有（本 todo 不改规格）；报告中已在 §3.5 明确 "DSE TTFT 现为可用验证目标，不再标记为未建模"。规格 §4 的 S1 行留待规格 owner 后续刷新（todo 3 学习记录已标注）。
- **验证**：`grep "60,223"` 空；signoff `run --reports uncertainty-kpis --cases qwen-prefill-2000` exit 0；§3.5 数值与规格 Gate 1b 逐字一致。

## Todo 5: 全量回归、signoff、DoneClaim、提交（2026-08-13）

- **回归结果**：targeted `pytest sim/tests/test_design_space_explorer.py -q` → 6/6 passed。
  全量 `pytest sim/tests/ sim/timing/tests/ -q` → 9 个模块 collection error
  （cocotb/caduceus_device_protocol 缺失，与 todo 2 记录一致，需 `--ignore`）；
  ignore 后 **2142 passed, 19 failed, 4 errors**。19+4 与 todo 2 记录的失败签名完全一致，
  本次用 `git stash`（DSE 两文件）+ HEAD 重跑失败子集复验：HEAD 下同样 19 failed + 4 errors，
  且失败模块均不 import design_space_explorer → **全部 pre-existing**，与本计划改动无关。
- **signoff `--all-spec`**：exit 0，verdict pass（8 阶段全过，uncertainty_kpis 含
  qwen-prefill-2000 等 5 cases）。DSE M=128/2000 证据重生成：
  ttft_ms = 2649.49 / 41398.27，sha256 与 task-3 证据逐字节一致（DSE 确定性）。
  老坑复现：从 `sim/` 跑 `--output .omo/evidence/...` 落到 `sim/.omo/evidence/`，需移回仓库根。
- **pytest 侧效应坑（重要）**：全量 pytest 里的 signoff 机制测试
  （`test_func_model_signoff_v3.py` 等）会**改写仓库内已提交证据/notepad**
  （task-0/task-20/task-23 证据、phase6/fm-e2e notepad、build/evidence/*.txt）。
  回归后必须 `git status --short` 审计并 `git restore` 这些 stray 文件 —— 本次恢复 7 个，
  其中 `task-20-uncertainty-kpis.json` 属上一计划，绝不能带入本计划 commit。
- **todo 5 mutation 目标修正**：计划声明的 mutation 目标 `simulate_layer()` 的 batch_m
  传递**不在 DSE TTFT 路径上**（`evaluate_config` 走 `simulate_prefill(cfg,
  _PREFILL_BATCH_M, _MODEL_ALIAS)`）；破坏 simulate_layer 后 6/6 仍然绿。
  有效目标是 `simulate_prefill()`：破坏后 `test_prefill_ttft` 失败
  （assert 1076464 > 1105117，cycles_128==cycles_1），证明测试确实守卫 batch_m 路径。
  DoneClaim 中如实记录两个目标的差异。
- **提交**：仅预期文件（DSE 2 文件 + spec + report + task-1/2/4/5 证据 + doneclaims +
  本 notepad + plan），单 commit；task-dse-*.json 与 task-3 JSON 被
  `.gitignore`（`.omo/evidence/*.json`）忽略，不进入提交（与既往证据策略一致）。
