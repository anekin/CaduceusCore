# bug-012-fm-audit - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** 在动硬件之前，先把软件的"标准答案"（Func Model）自己审一遍——全量回归跑通 + 用上次漏网 bug 的教训（列数不是 64 倍数、不是 2 的幂）主动攻击它 + 把"列数必须是真实值、输出必须紧凑排列"这两条隐性约定钉成显式断言。审干净了，才放行后面的硬件修复。

**Why this approach:** 上次的漏网 bug 暴露了一个真相：标准答案那一层只按惯例写对了，没有任何东西在防止它悄悄变错。所以先给标准答案补上防变错的断言和攻击测试，再让它去当硬件修复的裁判，顺序不能反。

**What it will NOT do:** 不碰任何硬件/固件代码、不上仿真服务器、全程本地；不修改任何现有测试来"凑通过"；只有真发现问题时才修标准答案本身。

**Effort:** Quick
**Risk:** Low - 全本地运行，纯加法式新增测试；唯一分支是"真发现了标准答案的 bug"，有明确处置路由
**Decisions to sanity-check:** (1) 攻击矩阵：列数 {2,10,12,20,33,40,64} × 行数 {1,4,32,65} × 内维 {1,64,128,129}，与独立直算逐位比对；(2) 契约测试把"列数=真实值 + 输出紧凑"从惯例升级为断言；(3) 审完并全绿后，硬件修复计划才解锁启动

Your next move: 直接 `/start-work bug-012-fm-audit` 开始执行，或先跑一轮高精度评审。Full execution detail follows below.

---

> TL;DR (machine): Quick | Low | FM 先行审计 — 基线 210 + 窄 N 攻击矩阵 + pack/tile/scale 攻击 + ABI 契约测试 + 条件修复；全本地；解锁 RTL bug-012-fix 计划。

## Scope
### Must have
1. **A1 FM 基线**：`PYTHONPATH=sim python -m pytest sim/tests/ sim/timing/tests/ -q`（预期 `210 passed`，README Quick Start 口径）+ `PYTHONPATH=sim python3 scripts/verify_ops_func_model.py`（op05/op07/op10 全 PASS）；判定行 `FM-BASELINE: <计数>-passed` + `OP05/OP07/OP10: PASS`
2. **A2-1 窄 N 攻击矩阵**：新文件 `sim/tests/test_fm_audit_narrow_n.py`（≤250 LOC，`np.random.default_rng(42)`，INT4 权重 INT8 激活 INT32 输出）：N∈{2,10,12,20,33,40,64} × M∈{1,4,32,65} × K∈{1,64,128,129} 全组合，GoldenMXU 输出 vs **独立 numpy 直算 oracle**（本文件内手写 int4×int8→int32 累加，**不 import sim.models/sim.engine/sim.timing**——oracle 独立性反模式）逐元素 bit-exact；判定行 `FM-HUNT-NARROW-N: clean|<first-fail-signature>`
3. **A2-2 pack/tile/scale 攻击**：新文件 `sim/tests/test_fm_audit_pack_edges.py`（≤200 LOC）：(a) `pack_int4_tile_major`（`sim/cocotb_bridge.py:208-239`）与 `pack_int8_activation_tile_major`（`:184-205`）对 N∈{2,10,33}、**M∈{4,64}、K∈{64,129}** 的零填充断言（列 ≥N 恒零、行 ≥M 恒零、字节数 == 64-tile 几何；**M=65 超出 pack 单 tile 契约走 (b)**——Oracle round-1 折入：Scope 原写 M∈{4,65} 是 stale 的）；(b) tile 调度 ceil 边界（N=33→1 n-tile、N=65→2、K=129→3 k-tiles、M=65→2 m-tiles）；(c) `_read_scale_hex`（`cocotb_bridge.py:173-181`）FP16→FP32 reshape 断言（num_blocks×N，缺块补零）+ accumulate 跨两命令（命令2 累加进命令1）等价于单命令双倍 K 直算（**INT32 域 bit-exact、无容差**——Oracle round-1 折入：原写 1e-6 是 stale 的）。判定行 `FM-HUNT-PACK: clean` / `FM-HUNT-TILE: clean` / `FM-HUNT-SCALE: clean`
4. **A3 ABI 契约**：新文件 `sim/tests/test_fm_abi_contract.py`（≤150 LOC）：(a) FM SoC bridge 写 MXU DIM1（0x10）真实 N 后读回 == N（N∈{2,33,64}）；(b) GoldenMXU 输出 dense 行主序契约（M×N 连续、元素序 [r*N+c] 逐字可索引）；(c) **FM 域零 padding 审计**：grep 证明 `sim/` 内 DIM1-padding（`engine_n = ((...dim_n + 63) // 64)` 模式）只存在于 RTL-driver 文件（`sim/cocotb_bridge.py`/`sim/diagnose_data_layout.py`），FM 域（mmio_bridge/models/tests）零命中——契约测试以注释+断言记录该边界。判定行 `FM-ABI-CONTRACT: pass`
5. **A4 修复处置（条件 todo）**：仅当 A2/A3 发现 FM bug → 在 FM 域修复（golden_executor/models/mmio_bridge 等）→ `docs/bugs/bugs-soc-func-model.md` 台账新条目（含 Root Cause + Fix Commit + Evidence）→ 重跑 A1 全绿；判定行 `FM-FIX: none-needed | <BUG-ID>-fixed`；**no_silent_skip：修复面大（>2 文件或 >200 LOC）→ STOP 上报，不自行扩大**
6. 分支 `bug-012-fm-audit`（当前目录、不建 worktree）；一 todo 一原子 commit（Commit: 预声明）；F1-F4 全 APPROVE + 用户 explicit okay 后 `--no-ff` merge 回 main；不自动 push；**merge 后解锁 Phase B（bug-012-fix 计划的前置门）**

### Must NOT have (guardrails, anti-slop, scope boundaries)
- **rtl/、firmware/、gen/、config/、vendored、scripts/ 产品代码零改动**（A 阶段纯 sim/ 测试域 + .omo/ 工件）
- **不跑 sz0001/VCS**——全本地（pytest + python3 脚本）
- **不改既有测试以图通过**；不并案（BUG-012 RTL 修复仍在 bug-012-fix 计划，本计划只做 FM 审计）
- 新测试**纯加法**（3 个新文件），不 import sim.models/sim.engine/sim.timing.providers/sim.npu_sim（oracle 独立性反模式；GoldenMXU 经 golden_executor 导入属允许——它是被测对象）
- **7 个并行会话 dirty 文件全程不动不提交**：`.omo/evidence/task-0-signoff-v3-runner.txt`、`.omo/evidence/task-20-uncertainty-kpis.json`、`.omo/evidence/task-23-perf-spec-ci.txt`、`.omo/notepads/fm-e2e-qwen-cv-software-stack/learnings.md`、`.omo/notepads/phase6-rtl-verification/learnings.md`、`build/evidence/fm-cv-chain.txt`、`build/evidence/w3-4-mobilenetv3-fm.txt`；**绝不 `git add .`/`-A`/`commit -a`**
- 不 push（用户明示后才 push）
- no_silent_skip：任一 hunt 失败 → 签名落档（first-fail 组合 + actual vs expected 前 10 元素），按 A4 路由

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **tests-after**（新增攻击/契约测试本身就是交付物；基线先行）——框架：pytest（本地）+ 独立 numpy oracle + grep 审计
- Evidence: 统一 `.omo/evidence/task-{0..5}-bug-012-fm-audit.txt`（随对应 todo commit 入库）
- 每份 evidence 必含 **provenance 块**（git HEAD / python 版本 / `git status --porcelain` 7 行快照口径）+ **grep-able 判定行**：`FM-BASELINE:` / `OP05/OP07/OP10:` / `FM-HUNT-NARROW-N:` / `FM-HUNT-PACK:` / `FM-HUNT-TILE:` / `FM-HUNT-SCALE:` / `FM-ABI-CONTRACT:` / `FM-FIX:`

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- **Wave 1**：todo 0（P0 基线，阻塞全部——单 todo 波显式豁免：P0 必须先行）
- **Wave 2**：todo 1（基线复跑，只写 evidence）∥ todo 2（新测试文件 A）∥ todo 3（新测试文件 B）——**执行序约束（Metis MAJOR 折入）**：todo 1 的 pytest 全量命令必须**先于** 2/3 的文件创建完成（2/3 可先做全部非文件工作）；否则全量 pytest 会收集到半成品新文件 → 基线计数污染 / collection error。todo 4 不在本波（依赖 2/3 的判定结果与 1 的基线）
- **Wave 3**：todo 4（契约测试，需 1/2/3 落定后写——契约断言以 hunt 结果为准绳）
- **Wave 4**：todo 5（条件修复处置，仅 A2/A3 有发现时）
- **终审波**：F1-F4 并行评审

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 0 | — | 1,2,3,4,5 | — |
| 1 | 0 | 4,5 | 2,3（异文件） |
| 2 | 0 | 4,5 | 1,3（异文件） |
| 3 | 0 | 4,5 | 1,2（异文件） |
| 4 | 1,2,3（失败路径 +5） | 5 | — |
| 5 | 4 | F1-F4 | —（条件触发；none-needed 分支无条件执行——证据分支而非跳过） |
| F1-F4 | 5 | merge gate | 彼此并行 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 0. P0 基线：分支 + provenance + pathspec 提交 + 终拍 7 行快照断言
  What to do / Must NOT do: (1) `git checkout -b bug-012-fm-audit main`（当前目录、**禁止 worktree**；确认 `git branch --show-current`）。(2) provenance 块：git HEAD sha + branch；`python3 --version`；**不跑任何测试**（基线跑在 todo 1）。(3) 落档 `.omo/evidence/task-0-bug-012-fm-audit.txt`。(4) pathspec 提交 plan + draft（`add -f`）+ 本 evidence；`.omo/notepads/bug-012-fm-audit/` 若存在一并显式路径提交。(5) **提交完成后拍 `git status --porcelain` 终拍快照（最后一步）**：必须**恰好等于**以下 8 行（任何额外行 → STOP 上报）：` M .omo/evidence/task-0-signoff-v3-runner.txt`、` M .omo/evidence/task-20-uncertainty-kpis.json`、` M .omo/evidence/task-23-perf-spec-ci.txt`、` M .omo/notepads/fm-e2e-qwen-cv-software-stack/learnings.md`、` M .omo/notepads/phase6-rtl-verification/learnings.md`、` M build/evidence/fm-cv-chain.txt`、` M build/evidence/w3-4-mobilenetv3-fm.txt`、`?? .omo/plans/bug-012-fix.md`（**Metis BLOCKER 折入**：Phase B 计划文件现存 untracked——保持 untracked、归 bug-012-fix 计划所有，本 todo 不得提交/触碰它）。Must NOT：不动/不提交 7 个 dirty 文件与 bug-012-fix.md；不用 `git add .`/`-A`/`commit -a`。
  Parallelization: Wave 1 | Blocked by: none | Blocks: 1,2,3,4,5
  References (executor has NO interview context - be exhaustive): 7 个 dirty 文件清单见 Scope Must-NOT（与 bug-012-root-cause todo 0 同一清单）；P0 先例 `.omo/evidence/task-0-bug-012-root-cause.txt`
  Acceptance criteria (agent-executable): `git branch --show-current` == `bug-012-fm-audit`；evidence 含 HEAD 行 + python 版本行；提交后 `git status --porcelain` 恰好 8 行（7 M + 1 `?? bug-012-fix.md`）且逐行 ⊆ Must-NOT 清单。
  QA scenarios: happy=provenance 齐全 + 8 行快照 PASS；failure=快照出现额外行（并行会话新产物？）→ STOP 记录根因。Evidence `.omo/evidence/task-0-bug-012-fm-audit.txt`
  Commit: Y | chore(omo): P0 baseline — branch + provenance snapshot (bug-012-fm-audit)

- [x] 1. A1 FM 基线复跑（本地；只写 evidence，零代码改动）
  What to do / Must NOT do: (1) `PYTHONPATH=sim python -m pytest sim/tests/ sim/timing/tests/ -q` → 预期 `210 passed`（README Quick Start 口径；AGENTS.md NOTES 警告 210/802/700 口径差异——**cite 实际执行的命令与末行数字**）；(2) `PYTHONPATH=sim python3 scripts/verify_ops_func_model.py` → 预期 `op05 (attn_score MMUL): PASS`、`op07 (attn_weight MMUL): PASS`、`MMUL VERDICT: ALL PASS`、`op10 (RMSNORM post-attn): PASS`、`FINAL VERDICT: ALL PASS`（**5 条判定行**，:233/:234/:235/:263/:264——Metis MINOR 折入：计划原写 4 条漏了 MMUL VERDICT）；(3) 两段全量输出落档 evidence + 判定行 `FM-BASELINE: <实际计数>-passed` / `OP05/OP07/OP10: PASS`。Must NOT：改任何代码；跳过失败项；旧 evidence 冒充新跑。
  Parallelization: Wave 2 | Blocked by: 0 | Blocks: 4,5 | 可与 2/3 并行（异文件、只写 evidence）
  References: `README.md` Quick Start（`PYTHONPATH=sim python -m pytest sim/tests/ sim/timing/tests/ -q`，预期 210）；`scripts/verify_ops_func_model.py:199-264`（op05/op07/op10 判定行原文）；`AGENTS.md` NOTES（计数口径警告）
  Acceptance criteria (agent-executable): evidence 含完整命令 + pytest 末行 + verify_ops 的 5 条判定行原文；`FM-BASELINE:` 判定行存在；零改动 `git diff HEAD^ HEAD --name-only` == 仅 evidence 文件。
  QA scenarios: happy=210 passed + op05/07/10 PASS；failure=非 210 或任一 op FAIL → 逐项 triage 落档（区分并行会话引入 vs FM 真实回归，后者触发 A4 路由）。Evidence `.omo/evidence/task-1-bug-012-fm-audit.txt`
  Commit: Y | test(fm): FM baseline re-run evidence — pytest + verify_ops (no code change)

- [x] 2. A2-1 窄 N 攻击矩阵（新文件 sim/tests/test_fm_audit_narrow_n.py，≤250 LOC）
  What to do / Must NOT do: 新测试文件，**纯加法**：`rng = np.random.default_rng(42)`；N∈{2,10,12,20,33,40,64} × M∈{1,4,32,65} × K∈{1,64,128,129} 全组合（7×4×4=112 组合）。(1) 生成 INT4 权重（`rng.integers(-8,8,(K,N))`——含 +7 全域，Metis MINOR 折入）与 INT8 激活（`rng.integers(-128,128,(M,K))`——含 +127）。(2) **被测（Metis MAJOR 折入：入口与 packing 显式钉死）**：`GoldenMXU().matmul_int32(act, GoldenMXU.pack_int4(wgt), M, K, N)`——**权重必须先 pack**（golden_executor.py:90 入口、:103 `unpack_int4(weight_packed)`；**:96 docstring 说 "pre-unpacked" 与实现矛盾——直接传 (K,N) 数组会破坏负权重 → 112 全假失败**，执行者须以此为准）。(3) **独立 oracle（同文件内手写）**：`np.dot(act.astype(np.int32), wgt.astype(np.int32))` 直算——本文件**不 import sim.models/sim.engine/sim.timing/sim.npu_sim**（oracle 独立性反模式；golden_executor 导入允许）。(4) 逐元素 bit-exact 断言；失败 → 打印 first-fail (M,K,N) 组合 + 前 10 元素 actual vs expected + 判定行 `FM-HUNT-NARROW-N: clean|<signature>`。(5) 边界重点：K=1（零填充激活路径）、K=129（3 k-tiles）、M=65（2 m-tiles）、N=33/12/10（非 2 的幂 ceil）。Must NOT：改既有测试；import 被禁止模块；跳过组合（no_silent_skip——全部 112 组合必须显式跑）。
  Parallelization: Wave 2 | Blocked by: 0 | Blocks: 4,5 | 可与 1/3 并行（异文件）
  References: `sim/golden_executor.py`（GoldenMXU 类）；`sim/tests/test_golden_mxu_edges.py` / `test_golden_mxu_quant.py`（既有攻击面风格先例）；`.omo/evidence/task-2-bug-012-root-cause.txt:147-154`（controller ceil-tile 数学与 partial_tile_N 先例——FM 侧同族嫌疑）；`config/func_model_perf_oracle_v1.json:13`（oracle 独立性反模式原文）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_fm_audit_narrow_n.py -q` 全 PASS；`test "$(grep -c "import sim.models\|import sim.engine\|import sim.timing\|import sim.npu_sim" sim/tests/test_fm_audit_narrow_n.py || true)" -eq 0`（**Momus round-2 BLOCKER 折入：正确形式是 `|| true` 而非 `|| echo 0`**——`grep -c` 零命中时输出 "0" 且 exit 1，`|| true` 只兜 exit code、输出保持 "0"；`|| echo 0` 会输出 "0\n0" 导致 `test` 报 "integer expression expected"）；判定行落档 evidence；`git diff HEAD^ HEAD --name-only` ⊆ {新文件, evidence}。
  QA scenarios: happy=112 组合全 PASS + oracle 独立 grep 零命中；failure=任一组合 mismatch → first-fail 签名落档（A4 路由）。Evidence `.omo/evidence/task-2-bug-012-fm-audit.txt`
  Commit: Y | test(fm): narrow-N attack matrix — 112 combos vs independent numpy oracle

- [x] 3. A2-2 pack/tile/scale 攻击（新文件 sim/tests/test_fm_audit_pack_edges.py，≤200 LOC）
  What to do / Must NOT do: 新测试文件，**纯加法**：(a) **pack 零填充（Metis BLOCKER 折入：M=65 超出 pack 单 tile 契约）**：`pack_int8_activation_tile_major`（`sim/cocotb_bridge.py:184-205`）与 `pack_int4_tile_major`（`:208-239`）——**pack 是单 64 行/列 tile 原语、无 m-tiling**（`out=bytearray(k_tiles*4096)`、`dst=kt*4096+c*64+r`，M=65 在 r=64 处 IndexError）；(a) 限 **M∈{4,64}、K∈{64,129}**（K 值显式钉死，Metis 折入）、N∈{2,10,33}：断言解包后列 ≥N 恒零、行 ≥M 恒零、总字节数 == 64 宽 tile 几何（act: k_tiles×64 行×64 列 INT8；wgt: n_tiles×k_tiles×2048B INT4 打包）；**M=65 只经 (b) m_tiles ceil 与 GoldenMXU 路径验证**。(b) **tile ceil**：n_tiles = (N+63)//64 对 N∈{33,64,65} → {1,1,2}；k_tiles 对 K∈{64,128,129} → {1,2,3}；m_tiles 对 M∈{64,65} → {1,2}（按 `sim/cocotb_bridge.py:1839-1841` 同款公式）。(c) **scale/acc（Metis MAJOR 折入：accumulate harness 显式化）**：`_read_scale_hex`（`cocotb_bridge.py:173-181`）对 (K=256,N=2,group=128) → shape (2,2)、缺块补零 + 超长截断双向断言；**GoldenMXU 无 accumulate API**——accumulate 在 mmio_bridge（CTRL bit[2]，:139/:268-278/:317-321）且单命令路径约束 K≤64（:202-203 docstring）：构造 MMIOBridge（mxu+sram modules，xbar=None 回退），激活经 `pack_int8_activation_tile_major` 预铺 4096B K-tile 布局，两命令各 K=64（命令2 置 CTRL bit[2]）→ SRAM 输出 vs `matmul_int32(K=128)` 单命令 **bit-exact**（INT32 域**去掉** 1e-6 容差表述；如需 scaled FP32 路径另行显式定容差）。**harness 钉死（Oracle round-1 MINOR 折入；Oracle round-2 措辞精化）**：`modules['sram']` 显式预分配 `bytearray(0x400000)`（**真实机制**：mmio_bridge.py:292-293 对空 sram 走 `if not sram: return` **静默跳过**——不预分配则计算静默 no-op、输出全零假象）；**两命令共用同一 O_ADDR**；命令2 的激活与权重必须指向**第二 K 半段**——I_ADDR2 = i_off + 4096、W_ADDR2 = w_off + 32*N（wgt 为 dense packed bytes，`GoldenMXU.pack_int4` 连续 nibble 打包，64·N 恒偶 → K 半段边界字节对齐，奇数 N 亦然）。判定行 `FM-HUNT-PACK: clean` / `FM-HUNT-TILE: clean` / `FM-HUNT-SCALE: clean`。Must NOT：改 pack/scale/accumulate 实现（发现 bug 属 A4）；import 被禁止模块；跳过任一分项。
  Parallelization: Wave 2 | Blocked by: 0 | Blocks: 4,5 | 可与 1/2 并行（异文件）
  References: `sim/cocotb_bridge.py:184-205`（pack_int8——被测对象）、`:208-239`（pack_int4——被测对象）、`:173-181`（_read_scale_hex）、`:1839-1841`（tile 公式）；`sim/mmio_bridge.py:139/:268-278/:317-321`（accumulate）+ `:195-196`（read 路径）；`sim/golden_executor.py:90-135`（matmul_int32）；`sim/tests/test_packer_equivalence.py`（既有 pack 测试——复核其覆盖、不重复造轮子）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_fm_audit_pack_edges.py -q` 全 PASS；三判定行落档；oracle 独立性 grep 零命中（**与 todo 2 完全同款命令**：`test "$(grep -c "import sim.models\|import sim.engine\|import sim.timing\|import sim.npu_sim" sim/tests/test_fm_audit_pack_edges.py || true)" -eq 0`——Momus round-2 折入：`|| true` 为正确形式）；`git diff HEAD^ HEAD --name-only` ⊆ {新文件, evidence}。
  QA scenarios: happy=三分项全 PASS；failure=任一分项 mismatch → 签名落档（A4 路由）。Evidence `.omo/evidence/task-3-bug-012-fm-audit.txt`
  Commit: Y | test(fm): pack zero-fill / tile-ceil / scale-accumulate attack tests

- [x] 4. A3 ABI 契约测试（新文件 sim/tests/test_fm_abi_contract.py，≤150 LOC；待 1/2/3）
  What to do / Must NOT do: 新测试文件，**纯加法**：(a) **DIM1=真实 N 契约（Metis MAJOR 折入：读写回环不足——`_status.get` 只证寄存器存储，须加计算级断言）**：写 N 后读回 == N（N∈{2,33,64}，引 `spec/npu_abi.json:147-153` DIM1 定义入注释）**且**以 DIM1=N=33 驱动 MXU CMD（mmio_bridge `_run_mxu_compute` 路径，:199-323 是真实 N 被使用处），断言 SRAM 输出区为 dense M×N INT32 且 == `matmul_int32(M,K,33)`——钉死"FM 用真实 N 计算、无 padding"。**staging 钉死（Oracle round-1 MINOR 折入）**：激活必须经 `pack_int8_activation_tile_major` 预铺 4096B K-tile 广播布局、权重经 `GoldenMXU.pack_int4` 写 dense packed bytes（`_run_mxu_compute` mmio_bridge.py:285-296 按此读取；naive dense act staging 会产生假契约失败）。(b) **dense 输出契约**：GoldenMXU M=32/N=2/K=128 输出为 M×N 连续 buffer，元素 (r,c) 位于 [r*N+c]（逐字可索引断言）。(c) **FM 域零 padding 审计（Metis MAJOR 折入：正则必须命中两文件）**：`re.compile(r"\(\([^)]*(?:dim_n|\bN\b)\s*\+\s*63\)\s*//\s*64\)\s*\*\s*64")`——断言命中集**恰好等于** {`sim/cocotb_bridge.py`, `sim/diagnose_data_layout.py`}（正向两文件命中 + 其余零命中；旧正则 `.*dim_n.*` 会漏掉 diagnose_data_layout.py:151 的 `((N + 63) // 64) * 64`——**保留 `* 64` 锚点**排除 K 维 `* 4096` 行；Oracle round-1 已实测该正则恰好命中两处）；以 pytest 断言形式（os.walk + re.search）落为测试。判定行 `FM-ABI-CONTRACT: pass`。Must NOT：改任何 FM 实现（发现违约属 A4）；改既有测试；**例外（Oracle round-1 MINOR 折入）**：`sim/golden_executor.py:96` docstring（"pre-unpacked INT4" 与 :103 实现矛盾）做**注释-only 勘正**——本 todo 允许该单行注释修复（非条件项，勿等 A4 触发），F4 白名单随之外扩。
  Parallelization: Wave 3 | Blocked by: 1,2,3（**失败路径另加 5**：若 2/3 有发现，契约测试待 5 修复后写，断言限定与所修 bug 正交的形状 + 修复后必重跑——Metis MAJOR 折入） | Blocks: 5 | —
  References: `spec/npu_abi.json:147-153`（DIM1 定义）；`sim/tests/test_soc_fm.py:161/616/636`（FM 写真实 N 既有先例）；`sim/mmio_bridge.py:109-117`（WRP/MXU 偏移）；`.omo/evidence/task-1-bug-012-root-cause.txt`（G4 缺口来源——惯例而非契约）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_fm_abi_contract.py -q` 全 PASS；判定行落档；`git diff HEAD^ HEAD --name-only` ⊆ {新文件, evidence}。
  QA scenarios: happy=三断言全 PASS；failure=任一项违约 → 签名落档（A4 路由——契约被打破本身就是 FM bug）。Evidence `.omo/evidence/task-4-bug-012-fm-audit.txt`
  Commit: Y | test(fm): ABI contract — DIM1=actual-N + dense output + zero-padding audit

- [x] 5. A4 修复处置（条件 todo：仅当 2/3/4 任一失败时激活；否则证据记 FM-FIX: none-needed）
  What to do / Must NOT do: 若 2/3/4 全绿：evidence 记 `FM-FIX: none-needed`，本 todo 零代码改动、仅证据 + 计划标注。若发现 FM bug：(1) 在 FM 域修复（sim/golden_executor.py 或 sim/models 或 mmio_bridge——**修复面 ≤2 文件且 ≤200 LOC**，否则 STOP 上报不自行扩大）。(2) `docs/bugs/bugs-soc-func-model.md` 新增条目（Date/SEV/Title/Description/Root Cause/Fix Commit/Evidence 全字段，格式同 BUG-SOC-FM-001..003 先例）。(3) 重跑 A1 基线 + 相关 hunt 测试全绿。(4) 判定行 `FM-FIX: <BUG-ID>-fixed`。Must NOT：改 rtl//firmware//scripts/；跨计划修 RTL；无证据 claim Fixed。
  Parallelization: Wave 4 | Blocked by: 4 | Blocks: F1-F4 | —
  References: `docs/bugs/bugs-soc-func-model.md:26-84`（BUG-SOC-FM-001..003 条目格式先例）；todo 1 基线命令；`.omo/evidence/task-{2,3,4}-bug-012-fm-audit.txt`（失败签名）
  Acceptance criteria (agent-executable): `grep -n "FM-FIX:" .omo/evidence/task-5-bug-012-fm-audit.txt` ≥ 1（none-needed 或 BUG-ID-fixed）；若修复 → 台账新条目含 Fix Commit 且重跑基线全绿；`git diff main..HEAD --name-only` ⊆ {sim/, docs/bugs/bugs-soc-func-model.md, .omo/*}。
  QA scenarios: happy=none-needed 或 修复后重跑绿；failure=修复面超界 → STOP 上报（no_silent_skip）。Evidence `.omo/evidence/task-5-bug-012-fm-audit.txt`
  Commit: Y | docs(bugs)/fix(sim): FM audit disposition — <none-needed 或 bug 条目>

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit — 5 todos 逐条：evidence 存在、acceptance 断言复跑、`Commit:` 行与 `git log --oneline main..HEAD` 实际提交一一对应、判定行（FM-BASELINE/FM-HUNT-*/FM-ABI-CONTRACT/FM-FIX）齐全、无 silent skip（112 组合全部显式跑）。
- [x] F2. Code quality review — oracle 独立性复核（三个新测试文件零 import sim.models/sim.engine/sim.timing/sim.npu_sim）；seed 确定性（42）；纯加法 diff；无改既有测试；契约测试的 grep 审计不误报（padding 模式正则精确）。
- [x] F3. Real manual QA — fresh 独立复跑：`PYTHONPATH=sim python -m pytest sim/tests/ sim/timing/tests/ -q` 计数（含新测试后的实际总数，对照 todo 1 基线的 210 + 3 个新文件用例数）、`scripts/verify_ops_func_model.py` **五判定行**（op05/op07/MMUL VERDICT/op10/FINAL VERDICT——Oracle+Momus round-1 折入：原写四判定行漏了 MMUL VERDICT）、三个新测试文件各自单独跑；输出逐项与 task-1/2/3/4 evidence 对照。
- [x] F4. Scope fidelity — `git diff $(git merge-base main HEAD) HEAD --name-only` 变更集 ⊆ {`sim/tests/test_fm_audit_narrow_n.py`, `sim/tests/test_fm_audit_pack_edges.py`, `sim/tests/test_fm_abi_contract.py`, `sim/golden_executor.py`（**仅 :96 docstring 注释 1 行**——Oracle round-1 折入）, `docs/bugs/bugs-soc-func-model.md`（仅 A4 触发时）, `.omo/*`}；rtl/、firmware/、scripts/、gen/、config/、vendored 零命中；每提交 `git show --name-only` 均不含 7 个 dirty 文件；未 push；分支纪律（单一 worktree）。

## Commit strategy
- 一个 todo 一个原子 commit（type: chore/test/docs/fix），message 预声明于各 todo `Commit:` 行；evidence 随对应 todo 一并入库
- staging 纪律：只允许逐 todo 显式路径 `git add` / `git add -f`（plan/draft 在 .gitignore 内）；**禁止 `git add .`、`git add -A`、`git commit -a`**
- 并行波（Wave 2：todos 1/2/3）提交带 pathspec 且提交前断言 `git diff --cached --name-only` == 本 todo 文件清单（index.lock 冲突 2-5s 指数退避重试 ≤5 次）
- 全部 todo + F1-F4 APPROVE + 用户 explicit okay 后 `--no-ff` merge 回 main；**不自动 push**；merge 后 Phase B（bug-012-fix）前置门解除

## Success criteria
1. `FM-BASELINE: 210-passed` + `OP05/OP07/OP10: PASS` —— FM 基线全绿（cite 实际命令与数字；若 triage 出非 210 且属并行会话引入 → 判定行记录 triage verdict 并以实际数字为准绳，本成功标准随之更新——Metis MINOR 折入）
2. `FM-HUNT-NARROW-N: clean` —— 112 组合（N∈{2,10,12,20,33,40,64}×M∈{1,4,32,65}×K∈{1,64,128,129}）vs 独立 numpy oracle 全 bit-exact
3. `FM-HUNT-PACK/TILE/SCALE: clean` —— pack 零填充、tile ceil、scale/acc 全 PASS
4. `FM-ABI-CONTRACT: pass` —— DIM1=真实 N + dense 输出 + FM 域零 padding 审计三条契约钉死
5. `FM-FIX: none-needed`（或发现 bug → 修复 + 台账 + 重跑全绿）
6. F1-F4 全 APPROVE + 用户 okay；变更集 ⊆ 白名单；每提交无 7 个 dirty 文件；未 push；**Phase B（bug-012-fix）解锁——且解锁前置含"G3 挂载修订已折入 bug-012-fix todo 7(5)"（Oracle round-1 BLOCKER 折入：exec 死代码必须在 Phase B 启动前修好）**
