# wrp-defects-and-tooling-fixes - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** 上一阶段暴露的两个"真缺陷"被修掉——MXU 完成信号报得太早（数据还没写完就让固件去读）和预载数量算错（大 K 模型只加载一半数据，读到未初始化内存），外加验证工具链的可信度修复（假通过判定、漏清理旧二进制与旧编译库、跑不通的目标、每次跑都弄脏仓库的产物）。收尾后 MXU 测试套件从 4/6 回到全绿，台账补齐两个新条目的完整根因与证据（并如实标注各自的适用边界）。

**Why this approach:** 根因已探明且都在 RTL 侧写错/写漏，不是测试写错——所以按文档契约修硬件（完成信号 = 数据真的可读；预载数量用专门的寄存器），测试只补它漏编程的那一步；工具链按最小风险修（用前先删旧产物 + 让旧编译库失效，不动 Makefile 依赖图，避免连锁影响）。

**What it will NOT do:** 不碰 MXU 引擎内部（缺陷都在包装层）；不改固件（固件等的是另一个位，它的等待本来就是必需的，只记录不删）；不行使中断路径的激进改动（改为显式记录边界）；不修那条需要改 vendored IP 的历史字段缺口；不推送远端。

**Effort:** Medium
**Risk:** Low-Medium - 两处 RTL 行为改动都有明确契约 + 6 例套件/33 例 SoC 回归兜底；工具链改动全部朝"失败要响"方向。

**Decisions to sanity-check:** (1) 完成信号改为"引擎完成 ∧ 缓冲区排空"的锁存组合，引擎完成的来源必须用包装层可见的粘滞信号； (2) 中断路径**本轮不改**，改为显式记录边界（脉冲语义 + 无测试覆盖）； (3) 预载寄存器的修复**只覆盖 K ≤ 128**（缓冲只有 2 个 K-tile），K > 128 明确标注不支持； (4) 两个缺陷以正式条目一次落账为 Fixed（台账 4F/0O → 6F/0O）； (5) 工具链只修核心项，VCS 缺失回退保持原样（记为残留）。

Your next move: 计划已生成（dual 高精度复核 Round 1 的 2 BLOCKER + 4 HIGH + 7 MEDIUM 已全折入）；执行需你 `/start-work wrp-defects-and-tooling-fixes` 或明示开始。Full execution detail follows below.

---

> TL;DR (machine): Medium | Low-Medium | WRP-1 = STATUS.DONE 读回改为 latched(可见粘滞完成信号 ∧ so_drain_done)（读路径包装，引擎完成源用 dbg_state==S_DONE 或 rdata[1] 锁存；IRQ 本波不改、记 residual）；WRP-2 = preload 改用 `wrp_k_tiles` + TB 先编程并读回（仅 K≤128 精确）；工具链 = Makefile cd ×3 + 三脚本判定解析（共享 helper + self-consistency）+ `$REPO_ROOT/csrc` 与 rm-before-rebuild + untrack 6 产物（+ .gitignore）；F2 小修（conformance TB 部分转 residual）；回归 = wrapper 6/6 + conformance + e2e + FM-SOC（基线相对）；台账 6F/0O；不 push。

## Scope
> 注：本区为摘要；行号与细节**以 Todos 为准**（Scope 中未随修正同步的旧行号不构成执行依据）。
### Must have
0. **P0 基线**：前置门（`git log --oneline main -3` 含 `f1d9652`）+ 分支 `wrp-defects-and-tooling-fixes` + provenance（HEAD / 触碰文件基线哈希 / **内联** porcelain 快照 / 固件 hex sha）+ 原子提交。
1. **工具链可信度（先修——后续所有验证依赖它）**：
   (a) **坏 target**：`sim/regression/Makefile` 的 `run_wrapper_sfu`/`run_wrapper_vector`/`run_wrapper_mxu` 三个 recipe 加 `cd $(REPO_ROOT) &&` 前缀（对齐 `:98`/`:403`/`:1022`；三条 recipe 只调 `bash scripts/wv_run_*.sh`，无 cwd 依赖副作用）；修后经 `soc-verification-run.sh` 调用不再 Error 127。
   (b) **假通过判定 + 退出码（含 fail-open 收口）**：三个 `scripts/wv_run_*.sh` 的判定改为调用**共享 helper**（如 `scripts/parse_cocotb_verdict.sh <log>`，或各脚本 `--log <file>` 干跑模式——**vector 的解析目前嵌在 SSH heredoc 内，必须抽出**）。判定规则：**汇总行必须存在且自洽**——`TESTS=<n> PASS=<n> FAIL=0 SKIP=0` 且 `n>0 && PASS==TESTS`（真实格式 `** TESTS=1 PASS=1 FAIL=0 SKIP=0 … **`）；逐用例行 `** <module>.<test>  PASS|FAIL  **` 仅作**交叉核对**；两者不一致或汇总行缺失 → **exit 1**（截断日志不得靠一条游离 PASS 行通过）。`wv_run_mxu.sh:96` 的 `grep -qE 'TEST.*PASS'` 确实匹配失败汇总行（`TESTS=1 PASS=0 FAIL=1`）——已证。`wv_run_vector.sh:76-84`（"无标记的 exit 0 = PASS"）与 `wv_run_sfu.sh:88-95`（无条件 `exit 0`）必须重写；任一脚本在失败路径 **不得有裸 `exit 0`**。提交 fail/pass fixture 供构造性验证（真实 sfu 跑 7 个用例、汇总为 `TESTS=7`；mxu 为 1；**解析器不得写死任何 n**）。
   (c) **清理列表（stale-binary + stale-elab 假 PASS）**：`soc-verification-run.sh` 的 CLEAN 块增补 `simv_apb_conformance_real*` **与 `$REPO_ROOT/csrc`**（**注意：实际 VCS 中间目录在仓库根 `csrc`，不在 `sim/regression`**——现有清理项 `sim/regression/csrc` 根本不存在；`simv_soc*` 在 `sim/regression` 下无匹配，属无效项，不加）；`run_ibex_full_rtl.sh:65-66` 的"存在即复用"改为：**先 `rm -rf "$SIMV" "$SIMV.daidir"` 再编译，并在编译后断言 `[ -x "$SIMV" ]` 否则中止**（否则 VCS 失败时旧二进制会继续被 case 循环用成假 PASS）；`wv_run_sfu.sh:33-39`/`wv_run_vector.sh:29-42` 改为仿 `wv_run_mxu.sh:39` 的 always-rm+rebuild（**不要走 `scripts/wv_compile.sh`**——它 `set +e`、记录 RC 但从不失败、也不删旧 simv；内联 rm+compile）。**在本 todo 内跑一次"强制重建后的 FM-SOC 新基线"**，记录实际 `pass/skip/fail/total` 作为 todo 5 基准（固件 hex 为 hash-bound，不强制重建；基线 evidence 记 hex sha；若基线现新失败 → 逐 case 归因，不得改期望值）。
   (d) **untrack 再生成产物**：grep **全部 runner 脚本**（`wv_run_mxu/sfu/vector.sh`、`wv_regression.sh`）确认被每次重写的 tracked 文件——实测为 **6 个**：`wrap-mxu-regression.txt`、`wrap-sfu-regression.txt`、`wrap-sfu-debug.txt`、`wrap-vec-regression.txt`、`wrap-vec-status.txt`、`wrap-regression-summary.txt`（`wrap-regression-summary.txt` 由 `wv_regression.sh` 写），另有 5 个 `wv_vector_logs/*.dbg` 每次重写 → 一并 `git rm --cached`（内容快照进 `.omo/evidence/`）；给该集合加 **`.gitignore` 规则**（逐项列名 `build/evidence/wv_vector_logs/` + enumerated `wrap-*`——**不得用通配 `*.dbg`**，历史 `.dbg` 必须保持 tracked）；**明确保护历史静态 `.dbg`**（`build/evidence/wv-bug007-*.log.dbg`、`build/evidence/wv_bug005_logs/*.dbg`）与 `wrap-bug005/007-result.txt`（保持 tracked）。注：`build/evidence/` **并未被 gitignore**；`docs/bugs/bugs-soc-rtl.md` 中引用这些产物的行号在 fresh clone 会悬空——属可再生成物，记一笔。
2. **BUG-MXU-WRP-001（WRP-1，RTL）：DONE 早报** — `rtl/wrapper/mxu_soc_wrapper.v`：
   (1) **引擎完成源的取法（必须先核实、不得引用不存在的网）**：`status_done` 是 **mxu_top 内部 wire（`mxu_top.v:112`），wrapper 无此端口**——禁止在 wrapper 直接引用。可用替代（**二选一，worker 核实后写明**）：① `dbg_state == S_DONE`（wrapper 已连 `.state(dbg_state)`；**须在 `controller.v` 核实 S_DONE 保持到下次 `cmd_start`**）；② 在 APB 读时锁存 `apb_mmio_rdata[1]`（**注意首读时序**：锁存必须使当次读也能置位/反映，否则死锁）。
   (2) `mxu_done_seen`（粘滞，下次启动清除）∧ `so_drain_done = so_fifo_empty && (so_state==SO_IDLE)`；读路径：在 `:297` mux **之前**对 `paddr==12'h008` 包装 `apb_mmio_prdata`（bit1 来自 mxu_top），不改 mux 三元项本身。
   (3) **语义写明**："数据可见" = W 通道已接受（B fire-and-forget，`:778-779/:879`）；**并写明两条边界假设**：(i) 若 store-out 期间发生 WDT trip（`:714` 强制回 SO_IDLE 且 FIFO 非空），**DONE 保持 deassert、`WRP_STATUS[1]` 为权威**（WDT 用例只覆盖 preload 路径——写明）；(ii) `so_fifo_empty` 是指针相等（`:652`，深 64 = `MAX_TILE`），隐含假设"每行 drain 延迟 < 64 cycles"——写进头注释与 evidence。
   (4) **IRQ：本轮不改，但必须显式记录边界（禁止天真与门）**：`mxu_irq` 是**单周期脉冲**（`controller.v:145` 每周期默认清零 vs `:319` 仅 S_DONE 置位），用 `(mxu_irq && …drain_done)` 直接与门会**把中断永久吞掉**（且现有套件无覆盖：`_preload_and_run` 写 `IRQ_EN=0`、WDT 用例的 `irq==1` 由 `wrp_wdt_timeout` 满足）。默认：不改 IRQ，在注释 + todo 6 台账 + evidence 记为 **accepted residual**，并加**可 grep 标记 `WRP1-IRQ-RESIDUAL:`**（写明脉冲语义、门控需先锁存 `mxu_irq_seen`、当前无测试覆盖）；若实现 `mxu_irq_seen` 锁存，则**必须**附 IRQ_EN=1 的 TB 用例（断言 irq 在 DONE 之后升起）。
   (5) 头注释注明新契约。Must NOT：不改 `rtl/mxu/**`；不改 TB 既有语义；不动 WRP_STATUS 的 load_done/wdt 位。
3. **BUG-MXU-WRP-002（WRP-2，RTL+TB）：preload K-tile 数取错寄存器** — RTL：preload FSM 的 tile 数由 DIM0 派生改为 **`wrp_k_tiles`(0x44)**（`:238-239` 派生线退役；`:483`/`:509` 改引用）；`wrp_k_tiles==0 → 1`；**在 FSM 注释与台账写明适用边界：修复仅对 K ≤ 128（2 K-tile 缓冲：`W_BUF_DEPTH=64`/`A_BUF_DEPTH=128`，`:354-355`；`:477`/`:497` 以 `pl_k_tile_cnt` 索引越界即丢失；`:595` 的 `act_buf_idx` 在 `burst_cnt≥2` 越界）精确；K > 128 每命令仍不支持（X），不改缓冲深度（超范围）**；头注释 `:43-50` 使用流程改正（K_TILES 先写、DIM 后写——与 `firmware/npu_firmware.c:247-272`、`cocotb_bridge.py:2267` 一致）。TB：`_preload_and_run` 在基址写完之后、**TRIG_LOAD（`:199`）之前**写 `OFF_WRP_K_TILES = (K+63)//64` 并**读回断言**；修正 accumulate 测试过时注释。RED 证据：现有失败日志 + 单 burst 对照。Must NOT：不改 `rtl/mxu/**`；不用 `COCOTB_RESOLVE_X`；不改其它测试语义。
4. **F2 评审意见收口** — `mxu_soc_wrapper.v`：(1) 看门狗"无误触发"注释改正（per-phase 预算：PL 整段预载 / 每行 store-out 各一，由 PL_READY/SO_IDLE/SO_TRANSFORM 清零；大 K + 慢从机暴露；10× 余量论证仍成立）；(2) sticky 清除条件加 `penable`（对齐 `wrp_trigger:210`）；(3) 恢复分支加"本拍无握手"门（`arvalid&&arready`/`awvalid&&awready` 不触发 trip）为**默认**——无法判定低风险时改记 residual；**必须在 evidence 落明确选择 + 理由**；(4) `test_mxu_wrapper.py` 的 `wdt_cnt` 探测 `except Exception` 收窄为 `(AttributeError, ValueError)`；(5) `docs/bugs/bugs-soc-rtl.md` 的混版行号引用改 HEAD 版或删行号；**F2-2 的 conformance TB 部分（`rtl/tb/apb_conformance_real_tb.sv:24/:680`）本轮冻结不改 → 在 evidence + todo 6 台账记为显式 residual**（守卫"不动 conformance TB"优先）。Must NOT：不改看门狗阈值/位定义；不改 INTC；不扩大为重构。
5. **回归（sz0001 串行、强制清编译）**：(a) **经修复后的 Makefile target** `bash sim/regression/soc-verification-run.sh run_wrapper_mxu` → **6/6 PASS**（若仍 Error 127 → STOP，P1a 失败）；(b) `run_apb_conformance_real` → GREEN 263/`doc_div_cnt==0`；(c) `run_e2e_mxu_single`+`run_e2e_mxu_multi` → PASS；(d) `run_fm_soc_all.sh` → **以 todo-1 的强制重建新基线为比较基准**（目标 0-fail-0-timeout/33；pass/skip 与基线一致或更好；8 SKIP ID 核对）。每项 evidence 含 provenance + simv sha256 + compile CPU time；**实测记录，不硬编码**。
6. **台账（正式立案）** — `docs/bugs/bugs-module-level.md` 新增两条目（Fixed）：`BUG-MXU-WRP-001`（DONE 早报；根因=完成信号与 drain 无连接；修复=锁存组合门控；commit + evidence `task-2-*`；**措辞校正：修复的是 STATUS.DONE/APB 契约；BUSY-based 等待者（firmware `npu-regmap.h:269` 读 bit0=BUSY、cocotb `_poll_done`）不受影响、仍各自需要 drain 等待——`npu_firmware.c:275-281` 的 256-nop 保持 load-bearing，不得删；IRQ 未改、记 residual**）；`BUG-MXU-WRP-002`（preload 取错寄存器；根因=DIM0 派生 vs TRIG_LOAD 后写；修复=`wrp_k_tiles` + TB 编程；commit + evidence `task-3-*`；注：`ctrl_acc_mode` 死输入、`COCOTB_RESOLVE_X` 不可用、**K > 128 仍不支持**）；统计 `Open 0 → 0`、`Fixed 4 → 6`（总数同步 4→6）；**改写既有 Note**（把 WRP-1/2 从 "NOT filed … stats stay at 4F/0O" 移除、保留 PROCESS-1/2/3、统计口径改 6F/0O）；`README.md:26`/`:39` → `6 = 6 Fixed / 0 Open`。Must NOT：不写没有 evidence 的 Fixed；不动 WDT 条目 5 条 residual；不加新的 Open；**不写没有 commit/evidence 引用的条目**。
7. F1-F4 终审。

### Must NOT have (guardrails, anti-slop, scope boundaries)
- **不改 `rtl/mxu/**`**（两个缺陷都在 wrapper 层修）、不改 `firmware/`、`spec/`、`gen/`、vendored；**不改 `rtl/tb/apb_conformance_real_tb.sv`**（F2-2 的该部分转 residual）；**不动 conformance TB 与 010/011/WDT 已验收内容**。
- **不改 Makefile 依赖图**；**不动 `Makefile:72-78` 的 VCS-missing 回退**（记 residual：缺 VCS 时编译 recipe 静默"成功"，已被 rm-f 路线大幅抵消）。
- **禁止天真的 IRQ 与门**（见 todo 2(4)）；**禁用 `COCOTB_RESOLVE_X`** 或任何把 X 归零的手段。
- 不删历史静态证据（`wrap-bug005/007-result.txt`、`wv-bug007-*.log.dbg`、`wv_bug005_logs/*.dbg`）；除 `git rm --cached` 的 enumerated 集外不做删除。
- **7 个受保护 dirty 文件不动不提交**：`.omo/evidence/task-0-signoff-v3-runner.txt`、`.omo/evidence/task-20-uncertainty-kpis.json`、`.omo/evidence/task-23-perf-spec-ci.txt`、`.omo/notepads/fm-e2e-qwen-cv-software-stack/learnings.md`、`.omo/notepads/phase6-rtl-verification/learnings.md`、`build/evidence/fm-cv-chain.txt`、`build/evidence/w3-4-mobilenetv3-fm.txt`。
- 不 push；不 worktree；禁 `git add .`/`-A`/`commit -a`。

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **tests-after** + 既有框架（cocotb wrapper 套件 / conformance VCS TB / e2e mxu / FM-SOC 33）；RED 证据 = 现有失败签名（`.omo/evidence/task-3-rtl-open-bugs-cleanup.txt`、`task-4`、`build/evidence/t4-2-wv-mxu-test_mxu_accumulate_mode.log`）。
- 每个 RTL 改动先复跑确认 RED、修后 GREEN；RED/GREEN 双日志入证；判 PASS 必须解析逐用例/逐 case 行（或经修复后的 helper）。
- Evidence: `.omo/evidence/task-{0..6}-wrp-defects-and-tooling-fixes.txt`；判定行 `P0-SNAPSHOT:` / `TOOLING:` / `WRP1-FIX:` / `WRP1-GATE:` / `WRP2-FIX:` / `WRP2-GATE:` / `NITS:` / `REGR:` / `LEDGER:`
- sz0001 串行；强制清编译 + 双重新鲜度证明（compile CPU time + 源推导不变量）。

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.
- **Wave 1**：todo 0（P0，阻塞全部）
- **Wave 2**：todo 1（工具链——后续验证的可信度前提）
- **Wave 3**：todo 2（WRP-1）→ todo 3（WRP-2）**串行**（同一文件 `mxu_soc_wrapper.v`）
- **Wave 4**：todo 4（F2 小修，同文件，串行在 3 后）
- **Wave 5**：todo 5（全量回归，sz0001 串行）
- **Wave 6**：todo 6（台账）
- **终审波**：F1-F4 并行

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 0 | —（前置门 f1d9652） | 1-6 | — |
| 1 | 0 | 2,3,4,5（验证可信度） | — |
| 2 | 1 | 3（同文件） | — |
| 3 | 2 | 4,5 | — |
| 4 | 3 | 5 | — |
| 5 | 4 | 6 | —（sz0001 串行） |
| 6 | 5 | F1-F4 | — |
| F1-F4 | 6 | merge gate | 彼此并行 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 0. P0 基线：前置门（含 f1d9652）+ 分支 + provenance + 快照断言
  What to do / Must NOT do: (1) 前置门：`git log --oneline main -3` 必须含 `f1d9652`；不含 → STOP。(2) `git checkout -b wrp-defects-and-tooling-fixes main`（原地，禁 worktree）。(3) provenance：HEAD sha + branch；`sha256sum` 触碰文件基线：`rtl/wrapper/mxu_soc_wrapper.v`、`sim/tests/wrapper/test_mxu_wrapper.py`、`sim/regression/Makefile`、`sim/regression/soc-verification-run.sh`、`sim/regression/run_ibex_full_rtl.sh`、`scripts/wv_run_mxu.sh`、`scripts/wv_run_sfu.sh`、`scripts/wv_run_vector.sh`、`scripts/wv_regression.sh`、`docs/bugs/bugs-module-level.md`、`README.md`；固件 hex sha。(4) 落档 `.omo/evidence/task-0-…txt`，**内联** `git status --porcelain` 快照。(5) pathspec 提交 plan + draft（`git add -f`）+ evidence。(6) 终拍：`git status --porcelain | grep -cE '^ ?M'` == **7**（`??` 不计）。Must NOT：不动 7 dirty；不 push；不重建固件。
  Parallelization: Wave 1 | Blocked by: none（前置门 f1d9652） | Blocks: 1-6
  References: 同形先例 `.omo/plans/rtl-open-bugs-cleanup.md` todo 0 + `.omo/evidence/task-0-rtl-open-bugs-cleanup.txt`
  Acceptance criteria (agent-executable): `git branch --show-current` == `wrp-defects-and-tooling-fixes`；evidence 含 HEAD + 11 文件哈希 + 固件 hex sha + 内联快照；`grep -E '^ ?M'` 行数 == 7；判定行 `P0-SNAPSHOT: pass`。
  QA scenarios: happy=provenance + 快照 PASS；failure=main 无 f1d9652 或 M 行非 7 → STOP 记录。Evidence `.omo/evidence/task-0-wrp-defects-and-tooling-fixes.txt`
  Commit: Y | chore(omo): P0 baseline — branch + provenance snapshot (wrp-defects-and-tooling-fixes)

- [x] 1. 工具链可信度：坏 target + 判定解析 + 清理（simv + csrc）+ untrack 产物
  What to do / Must NOT do: (a) `Makefile` 三个 `run_wrapper_*` recipe 加 `cd $(REPO_ROOT) &&`。   (b) **判定解析（含两个已证的静默漏点）**：抽共享 helper（`scripts/parse_cocotb_verdict.sh <log>` 或 `--log` 干跑；vector 的 heredoc 内解析必须抽出）；规则 = 汇总行存在且自洽（`TESTS=<n> PASS=<n> FAIL=0 SKIP=0`，`n>0 && PASS==TESTS`）+ 逐用例行交叉核对，**不一致/缺失 → exit 1**；失败路径无裸 `exit 0`；fixtures（fail/pass；**真实 sfu=7、mxu=1，不得写死 n**）。**两个静默漏点必须收口**：(i) `wv_run_vector.sh:51-57` 的 `TESTS=()` 只列 5 个、模块实有 6 个（漏 `test_bug005_vector_nonaligned_wstrb`）→ **默认加入列表**（residual 仅为文档化例外、且必须具名），并删除硬编码 "5"/"7" 字样；(ii) `scripts/wv_regression.sh`（写 `wrap-regression-summary.txt`）自身硬编码 + fail-open（`:103-104/:121/:141`）且仍被 `wv_f1_audit.sh:87-91` 当审计证据 → 接入 helper 或记显式 residual 并从审计路径移除该产物。   (c) **清理**：`soc-verification-run.sh` CLEAN 块增补 `simv_apb_conformance_real*` + **`$REPO_ROOT/csrc`**（`sim/regression/csrc` 不存在；`simv_soc_cocotb*` 已被现有 CLEAN 覆盖，不重复加）；`run_ibex_full_rtl.sh`（复用分支 `:49`、echo `:67`）：先 `rm -rf "$SIMV" "$SIMV.daidir"` **并 `rm -rf "$BUILD_DIR/csrc"`**（该脚本用 `-Mdir="$BUILD_DIR/csrc"`，`:53`——与仓库根 `csrc` 是两处，都要清）再编译 + 编译后断言 `[ -x ]` 否则中止；`wv_run_sfu/vector.sh` 内联 rm+rebuild（**不走 `wv_compile.sh`**）；**跑一次强制重建后的 FM-SOC 新基线**（记录 pass/skip/fail/total；固件 hex hash-bound，记 sha；新失败 → 逐 case 归因，不改期望）。(d) **untrack**：grep 全部 runner 脚本确认重写集合（实测 6 个 `wrap-*` + 5 个 `wv_vector_logs/*.dbg`）→ `git rm --cached` + 快照进 `.omo/evidence/` + 加 `.gitignore` 规则；**保护** `wrap-bug005/007-result.txt`、`wv-bug007-*.log.dbg`、`wv_bug005_logs/*.dbg`。Must NOT：不动 Makefile:72-78；不改依赖图；不删历史证据。
  Parallelization: Wave 2 | Blocked by: 0 | Blocks: 2-5
  References: `sim/regression/Makefile:1305-1317`、`:98/:403/:1022`（house style）；`scripts/wv_run_mxu.sh:39,96-120`；`scripts/wv_run_sfu.sh:33-39,54,77-95`；`scripts/wv_run_vector.sh:29-42,70,75-106`；`scripts/wv_regression.sh`（写 summary）；`scripts/wv_compile.sh:17-32`（不采用）；`sim/regression/soc-verification-run.sh:40-46`；`sim/regression/run_ibex_full_rtl.sh:28-29,49,53,67,88,115`；`build/evidence/`（未 gitignore，`git check-ignore` exit 1）
  Acceptance criteria (agent-executable): (a) `bash sim/regression/soc-verification-run.sh run_wrapper_mxu` 不再 Error 127；(b) `bash scripts/parse_cocotb_verdict.sh <fixture-fail.log>` → **exit 1**、pass fixture → exit 0、**`TESTS=7`** 样本正确；`grep` 断言三脚本（+`wv_regression.sh` 或具名 residual）均调用 helper 且失败路径无裸 `exit 0`；(c) CLEAN 含 `simv_apb_conformance_real` 与**字面 `REPO_ROOT/csrc`**（`grep -cE 'REPO_ROOT/csrc'` ≥1——裸 `csrc` 已在旧文件命中，**不算**）、`run_ibex_full_rtl.sh` 含 `rm -rf "$SIMV"` **与 `$BUILD_DIR/csrc`** 且无 "Reusing existing simv"、**FM-SOC 新基线已记录（csrc mtime before/after 为主判据 + hex sha）**；(d) `git ls-files build/evidence | grep -cE 'wrap-(mxu|sfu|vec)-regression|wrap-sfu-debug|wrap-vec-status|wrap-regression-summary'` == 0 **且 `git ls-files build/evidence/wv_vector_logs | wc -l` == 0**，同时 `wv-bug007-*.log.dbg`/`wv_bug005_logs/*.dbg` **仍在 tracked**；evidence 记 "SFU/vector 在新判定下可能首次报 FAIL——这是修复生效、不是回归"（`run_wrapper_all` 可能首次变红）；判定行 `TOOLING: fixed`；
  QA scenarios: happy=fixture fail→exit 1 / pass→exit 0；全绿样本 exit 0；failure=任一脚本仍 fail-open → 保留输出 STOP。Evidence `.omo/evidence/task-1-wrp-defects-and-tooling-fixes.txt`
  Commit: Y | chore(sim): fix wrapper targets + verdict parsing + simv/csrc cleaning + untrack regenerated artifacts

- [x] 2. BUG-MXU-WRP-001：STATUS.DONE 门控到 store-out drain 完成
  What to do / Must NOT do: `rtl/wrapper/mxu_soc_wrapper.v`：(1) **引擎完成源（禁止引用不存在网）**：`status_done` 是 mxu_top 内部 wire，wrapper 无端口——**单一路径（已核实）**：`mxu_done_seen` 是**锁存器**，由 `dbg_state == S_DONE` 置位（`S_DONE` 仅**一周期**：`controller.v:76`/`:316-328` 下一拍回 `S_IDLE`——**禁止组合使用**），清除 = CMD.START 写（**必须带 `mmio_cs` 限定**：`mmio_cs && mmio_we && mmio_addr==12'h04 && mmio_wdata[0]`——`mmio_we`/`mmio_addr`/`mmio_wdata` 是未门控裸信号（`apb_to_mmio.v:59-62`），少 `mmio_cs`(=psel∧penable) 会在总线空闲时把锁存逐拍清零；与 todo 4(2) 的 penable 惯例一致；镜像引擎 `controller.v:156/:323`）；**同拍优先级：CMD.START 写（清除）胜出**（否则新命令继承旧 DONE=1；6/6 门看不出来——单命令无重合、bug007 只查 DONE 已置位且数据错降级为 warning）。备用②（仅当①不可行）：APB 读时锁存 `apb_mmio_rdata[1]`——**禁止锁存被门控后的值**（自引用死锁），并写明读历史耦合的代价。(2) `mxu_done_seen`（粘滞，下次启动清）∧ `so_drain_done = so_fifo_empty && (so_state==SO_IDLE)`；读路径在 `:297` mux **之前**对 `paddr==12'h008` 包装 `apb_mmio_prdata`。(3) **语义与边界写明**（头注释 + evidence）："数据可见"=W 已接受（B fire-and-forget）；(i) store-out 期间 WDT trip → **DONE 保持 deassert、`WRP_STATUS[1]` 权威**（WDT 用例仅覆盖 preload 路径）；(ii) `so_fifo_empty` 为指针相等（深 64=MAX_TILE），假设"每行 drain 延迟 < 64 cycles"（实测 ≈6 cyc/行 wrapper、≈11-15 FM-SOC）；**本波不做、记为未来项**：`so_overflow` 跳线（`wr_ptr==rd_ptr` 且 `so_capture_en` 时置位）。(4) **IRQ：不改，显式 residual**；**禁止** `(mxu_irq && …)` 天真与门（单周期脉冲会被永久吞掉；无测试覆盖——`IRQ_EN=0`、WDT 用例由 wdt 位满足）；若实现 `mxu_irq_seen` 锁存则必须附 IRQ_EN=1 TB 用例。(5) 头注释注明新契约。Must NOT：不改 `rtl/mxu/**`；不改 TB 既有语义；不动 WRP_STATUS 的 load_done/wdt 位。
  Parallelization: Wave 3 | Blocked by: 1 | Blocks: 3（同文件）
  References: `mxu_soc_wrapper.v:297`（读 mux）、`:320-345`（mxu_top 实例，仅 `.irq`/`.state` 可见）、`:670-788`（SO FSM）、`:652`（empty=指针相等）、`:714`（WDT 强制回 IDLE）、`:778-779/:879`（W 接受/B fire-and-forget）、`:880`、`:50`（文档契约）；`rtl/mxu/mxu_top.v:112`（status_done 内部）、`controller.v:140-146`（irq 默认清零）、`:311/:316-328`（status_done 粘滞、S_DONE）、`:15` vs `:311`（注释矛盾，以代码为准）；RED 证据 `.omo/evidence/task-3-rtl-open-bugs-cleanup.txt:278-291`
  Acceptance criteria (agent-executable): 修前复跑 `test_mxu_single_tile_compute` RED（签名 byte-identical）→ 修后 GREEN，**其余 4 个绿测**（apb_regmap_rw / preload_single_tile / store_out_burst / watchdog）不回归（套件共 6 例、现 4 绿 2 红；tracked `wrap-mxu-regression.txt` 的 "5 PASS" 是 2026-07 陈旧件，勿采信）；**IRQ 决策（含 `WRP1-IRQ-RESIDUAL:` 标记）+ WDT-trip 语义 + drain-latency 假设均已落文字**；判定行 `WRP1-FIX: done` + `WRP1-GATE: green`；diff ⊆ {mxu_soc_wrapper.v, evidence}。
  QA scenarios: happy=RED→GREEN 双日志 + 决策落档；failure=DONE 恒 0（源选错/首读时序）或某测回归 → 保留日志 STOP。Evidence `.omo/evidence/task-2-wrp-defects-and-tooling-fixes.txt`
  Commit: Y | fix(rtl/wrapper): gate MXU DONE on store-out drain (BUG-MXU-WRP-001)

- [x] 3. BUG-MXU-WRP-002：preload 改用 WRP_K_TILES + TB 在 TRIG_LOAD 前编程
  What to do / Must NOT do: (1) RTL：preload tile 数改用 `wrp_k_tiles`(0x44)（`:483`/`:509`；`:238-239` 派生线退役）；`==0 → 1`；**写明适用边界：仅 K ≤ 128（2 K-tile 缓冲：params `:67`/`:70`、数组 `:363`/`:364`；索引 `:480`/`:506` 越界即丢失；`:595` `act_buf_idx` 在 `burst_cnt≥2` 越界）；K > 128 仍不支持（X），不改缓冲深度**；头注释流程改正。(2) TB：`_preload_and_run` 在基址写完后、**TRIG_LOAD（`:199`）之前**写 `OFF_WRP_K_TILES=(K+63)//64` 并**读回断言**；修正 accumulate 测试注释。(3) RED：现有失败日志 + 单 burst 对照。(4) 多 tile：e2e multi + FM-SOC 覆盖；疑点 → residual。Must NOT：不改 `rtl/mxu/**`；不用 `COCOTB_RESOLVE_X`；不改其它测试语义。
  Parallelization: Wave 3 | Blocked by: 2（同文件） | Blocks: 4,5
  References: `mxu_soc_wrapper.v:67,70,238-239,363-364,480,483,506,509,593-604,43-50`；`test_mxu_wrapper.py:194-199,208-215,~493-526`；`firmware/npu_firmware.c:247-272`；`cocotb_bridge.py:2267`；RED `build/evidence/t4-2-wv-mxu-test_mxu_accumulate_mode.log:129,147,181-198,276-298`
  Acceptance criteria (agent-executable): 修前 RED（ValueError 'x' + 单 burst）；修后 GREEN、无 X、结果匹配；**`wrp_k_tiles` 读回 == ceil(K/64)**；**burst 计数用机械断言**（sim log 中 preload AR burst：len32×≥2 + len64×≥2，或明确以读回为主断言）；判定行 `WRP2-FIX: done` + `WRP2-GATE: green`；diff ⊆ {mxu_soc_wrapper.v, test_mxu_wrapper.py, evidence}。
  QA scenarios: happy=RED→GREEN + 读回/计数断言；failure=仍 X 或结果错 → 保留日志 STOP。Evidence `.omo/evidence/task-3-wrp-defects-and-tooling-fixes.txt`
  Commit: Y | fix(rtl/wrapper): preload honors WRP_K_TILES + TB programs K-tiles before TRIG_LOAD (BUG-MXU-WRP-002)

- [x] 4. F2 评审意见收口（conformance TB 部分转 residual）
  What to do / Must NOT do: `mxu_soc_wrapper.v`：(1) 看门狗"无误触发"注释改正（per-phase 预算；大 K + 慢从机暴露；10× 余量仍成立）；(2) sticky 清除加 `penable`；(3) 恢复门加"本拍无握手"为**默认**——无法判定低风险则记 residual，**必须在 evidence 落明确选择 + 理由**；(4) `test_mxu_wrapper.py` 探测 `except` 收窄 `(AttributeError, ValueError)`；(5) `docs/bugs/bugs-soc-rtl.md` 行号引用修正（按内容定位）；**F2-2 的 conformance TB 部分冻结 → evidence + todo 6 显式 residual**。Must NOT：不改看门狗阈值/位定义；不改 INTC；不动 `rtl/tb/apb_conformance_real_tb.sv`。
  Parallelization: Wave 4 | Blocked by: 3 | Blocks: 5
  References: `.omo/evidence/f2-rtl-open-bugs-cleanup.txt`；`mxu_soc_wrapper.v:31,401-402,439,455,710`；`test_mxu_wrapper.py:792`；`docs/bugs/bugs-soc-rtl.md`（DMA 段）
  Acceptance criteria (agent-executable): 改后 `run_wrapper_mxu` 6/6 仍绿；`penable` 在清除条件行；探测 `except` 宽捕获消除；**恢复门选择 + conformance-TB residual 落文字**；判定行 `NITS: done`。
  QA scenarios: happy=6/6 保持 + grep 断言；failure=任一回退 → 保留日志 STOP。Evidence `.omo/evidence/task-4-wrp-defects-and-tooling-fixes.txt`
  Commit: Y | chore(rtl/wrapper): address F2 review nits — comment wording, penable, recovery gate, probe scope

- [x] 5. 回归（sz0001 串行、强制清编译）：wrapper 6/6 + conformance + e2e mxu + FM-SOC 33
  What to do / Must NOT do: 前置：强制清编译（`simv_apb_conformance_real`、`simv_soc_ibex*`、`simv_soc_cocotb*`、wrapper simv + `$REPO_ROOT/csrc`）+ 双重新鲜度证明。串行：(a) **经修复后的 Makefile target** `run_wrapper_mxu` → **6/6**（仍 Error 127 → STOP）；(b) `run_apb_conformance_real` → GREEN 263/`doc_div==0`；(c) `run_e2e_mxu_single`+`run_e2e_mxu_multi` → PASS；(d) `run_fm_soc_all.sh` → **对比 todo-1 新基线**（目标 0-fail-0-timeout/33；pass/skip 与基线一致或更好；8 SKIP ID 核对）。**实测记录，不硬编码**。Must NOT：并发 sz0001；跳 case；读汇总文案判 PASS。
  Parallelization: Wave 5 | Blocked by: 4 | Blocks: 6 | sz0001 串行
  References: `.omo/evidence/task-4-rtl-open-bugs-cleanup.txt`（上一轮口径）；`Makefile` conformance `:206-228`、wrapper `:1313-1315`、e2e `:571-596`
  Acceptance criteria (agent-executable): `REGR:` 四行 —— `REG-WRAPPER: 6-pass` / `REG-CONF: GREEN-docdiv-0` / `REG-E2EMXU: single-pass, multi-pass` / `REG-FMSOC: 0-fail-0-timeout-total-33 且 pass/skip 与 todo-1 基线一致（或更好）`；每项 simv sha256 + compile CPU time；8 SKIP ID 逐一列出。
  QA scenarios: happy=四组全绿（wrapper 首次 6/6）；failure=任一不达 → 日志签名 STOP 回 todo 2/3/4 归因。Evidence `.omo/evidence/task-5-wrp-defects-and-tooling-fixes.txt`
  Commit: Y | test(wrp-fixes): wrapper 6/6 + conformance + e2e mxu + FM-SOC 33 regression evidence

- [x] 6. 台账：BUG-MXU-WRP-001/002 立案为 Fixed（4F/0O → 6F/0O）+ README + residual 收口
  What to do / Must NOT do: (1) 新增两条目（Fixed）——`BUG-MXU-WRP-001`（根因/修复/**措辞校正：修复的是 STATUS.DONE/APB 契约；BUSY-based 等待者（firmware `npu-regmap.h:268-270` 读 bit0=BUSY、cocotb `_poll_done`）不受影响、**256-nop 保持 load-bearing 不得删**；IRQ 未改记 residual（含 `WRP1-IRQ-RESIDUAL:` 标记）；WDT-trip/FIFO-latency 边界**）/commit/evidence；`BUG-MXU-WRP-002`（根因/修复/**仅 K ≤ 128 精确**/`ctrl_acc_mode` 死输入/`COCOTB_RESOLVE_X` 不可用）/commit/evidence。(2) **改写既有 Note**（WRP-1/2 从 "NOT filed … 4F/0O" 移除；保留 PROCESS-1/2/3；口径改 6F/0O）。(3) `README.md:26`/`:39` → `6 = 6 Fixed / 0 Open`。(4) 记 conformance-TB residual（F2-2）。(5) 判定行 `LEDGER:`。Must NOT：不写无 evidence 的 Fixed；不动 WDT 5 条 residual；不加新的 Open；不写无引用的条目。
  Parallelization: Wave 6 | Blocked by: 5 | Blocks: F1-F4
  References: `docs/bugs/bugs-module-level.md`（格式模板/统计表/WRP Note——**按内容定位**）；`README.md:26,39`；todo 2/3 的 Commit 行与 evidence
  Acceptance criteria (agent-executable): 两条目各 ≥2 处命中；统计 `| Fixed | 6 |` 与 `| Open | 0 |`；README 含 `6 = 6 Fixed / 0 Open`；`grep -n '4 = 4 Fixed\|statistics stay at 4 Fixed' README.md docs/bugs/bugs-module-level.md` 无命中；diff ⊆ {bugs-module-level.md, bugs-soc-rtl.md（仅行号）, README.md}。
  QA scenarios: happy=五要素 + 计数一致 + residual 收口；failure=计数/引用缺失 → 补齐复跑。Evidence `.omo/evidence/task-6-wrp-defects-and-tooling-fixes.txt`
  Commit: Y | docs(bugs): file BUG-MXU-WRP-001/002 as Fixed — wrapper drain/derivation defects (module ledger 6F/0O)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
> **F1-F4 状态（2026-09-25）：四项评审全部 APPROVE** —— F1 合规（7 commit 与计划 `Commit:` 行逐字一致、RED→GREEN 双日志签名一致、工具链"失败样本 exit 1"构造验证、`WRP1-IRQ-RESIDUAL:` 与三条边界在案）；F2 代码质量（Round-1 REJECT 的 1 个真错误"BUSY 持续到 drain"已改正措辞 → 复审 APPROVE）；F3 真实手工 QA 重跑（wrapper 6/6、conformance GREEN 263/0、e2e 287/923 cycles、FM-SOC 25/8/0/0/33 新二进制 `d516a216`）；F4 范围保真（40 文件 ⊆ 白名单、0 越界、0/7 受保护文件入库、未 push、单 worktree）。证据 `.omo/evidence/f{1..4}-wrp-defects-and-tooling-fixes.txt` + `.omo/evidence/f2fix-wrp-defects-and-tooling-fixes.txt`。
> **勾选与合并被 Final Wave 门阻塞于用户 explicit okay（用户-only 决策）→ 按 boulder continuation 规则标记 `- [~]`，不视为完成。** 待用户拍板：批准合并（勾选 F1-F4 → 提交 `.omo` 记账 = 计划 + F 证据 + notepads → `--no-ff` 合并回 main，**不 push**）；以及未推送清单 A/B/C/D（`main` 领先 origin 8 个提交 + 本分支 8 个）。
> **2026-09-25 用户回复「批准合并」** → F1-F4 勾选为 `- [x]`；合并由 orchestrator 以 `--no-ff` 执行（**不 push**）。**未推送清单 A/B/C/D 仍未决。**
> **非阻塞遗留（已记录，不挡合并）**：F3 LOW（`run_fm_soc_all.sh` 缺 host 转发，裸跑立即报错不假 PASS）；I-1（sz0001 自环 SSH key）；I-13（`results.xml` 被 cocotb 碰脏、每次已还原）；I-20（FM-SOC `nohup` 丢 rc，建议加 `[RUNNER-EXIT]` trailer）；R1（conformance TB 行号冻结）；K>128 不支持（有界声明）；IRQ 未改（标记在案）；SFU 6/7 与 Vector 5/6 是修复生效后**首次可见的真实结论**，非回归。
- [x] F1. Plan compliance audit - todos 0-6 逐条：evidence 存在、acceptance 复跑、判定行齐全、无 silent skip；**关键**：WRP-1/2 的 RED→GREEN 双日志在案且 RED 签名与记录一致；工具链"失败样本 exit 1"构造性验证在案；IRQ/WDT-trip/K≤128 三条边界声明在案（**`WRP1-IRQ-RESIDUAL:` 标记 grep 命中**）
- [x] F2. Code quality review - `mxu_soc_wrapper.v` 恰为设计内改动；**未引用不存在的网**（status_done 事件已用可见代理，且 S_DONE 为**锁存**非组合、**set/clear 同拍优先级已核实（清除胜出）**）；**无天真 IRQ 与门**且 `WRP1-IRQ-RESIDUAL:` 标记在案；`so_drain_done` 语义与两条边界写明；`wrp_k_tiles==0→1` 明确；`rtl/mxu/**`/`firmware/`/`spec/`/`gen/`/conformance TB 零改动；工具链 fail-closed 无依赖图改动（vector 漏测与 `wv_regression.sh` 的处置落文字）；`COCOTB_RESOLVE_X` 零使用；台账不 overclaim
- [x] F3. Real manual QA - fresh 独立复跑（sz0001，强制清编译）：经 Makefile target 的 wrapper 6/6、conformance GREEN/doc_div==0、e2e mxu multi PASS；对照 task-5 evidence；sz0001 不可达 ⇒ BLOCK，不降级
- [x] F4. Scope fidelity - `git diff $(git merge-base main HEAD) HEAD --name-only` ⊆ {rtl/wrapper/mxu_soc_wrapper.v, sim/tests/wrapper/test_mxu_wrapper.py, sim/regression/Makefile, sim/regression/soc-verification-run.sh, sim/regression/run_ibex_full_rtl.sh, scripts/wv_run_mxu.sh, scripts/wv_run_sfu.sh, scripts/wv_run_vector.sh, scripts/wv_regression.sh（若接入 helper；否则 grep-only 仅读）, scripts/wv_f1_audit.sh（仅当走"从审计路径移除该产物"分支）, scripts/parse_cocotb_verdict.sh（新 helper）, sim/regression/fixtures/*, .gitignore, docs/bugs/bugs-module-level.md, docs/bugs/bugs-soc-rtl.md（仅行号）, README.md, build/evidence/*（仅 enumerated untrack 集）, .omo/*}；`rtl/mxu/`/`rtl/soc/`/`firmware/`/`spec/`/`gen/`/vendored/`rtl/tb/apb_conformance_real_tb.sv` 零命中；7 dirty 不入任何提交；未 push；单 worktree；**untrack 集显式列出**

## Commit strategy
- 一个 todo 一个原子 commit；message 预声明于各 todo `Commit:` 行；evidence 随 todo 入库（`git add -f`）。
- staging 纪律：逐 todo 显式 pathspec；**禁 `git add .`/`-A`/`commit -a`**；7 个受保护 dirty 文件永不入库。
- sz0001 VCS 串行；F1-F4 全 APPROVE + 用户 explicit okay 后 `--no-ff` merge；**不自动 push**。

## Success criteria
1. `WRP1-FIX: done` + `WRP1-GATE: green` —— `test_mxu_single_tile_compute` RED→GREEN；引擎完成源用可见粘滞代理；IRQ/WDT-trip/FIFO-latency 三条边界落文字；其余 4 绿测（apb_regmap_rw / preload_single_tile / store_out_burst / watchdog）不回归
2. `WRP2-FIX: done` + `WRP2-GATE: green` —— `test_mxu_accumulate_mode` 无 X、读回断言通过、结果匹配；K ≤ 128 边界写明
3. `TOOLING: fixed` —— target 可经 runner 调用；解析器 fail-closed（fixture 构造验证）；`simv_apb_conformance_real`+`$REPO_ROOT/csrc` 清理生效；rm-before-rebuild 生效；6 个再生成产物 + `wv_vector_logs/*.dbg` untrack 且加 `.gitignore`；历史静态件保护
4. `NITS: done` —— F2 MEDIUM 注释改正 + penable + 恢复门（或 residual）+ 探测收窄 + conformance-TB residual 落账
5. `REGR:` 四行全绿 —— wrapper **6/6**（经 Makefile target）、conformance GREEN doc_div 0、e2e mxu PASS、FM-SOC **0-fail-0-timeout/33**（与 todo-1 基线一致或更好）
6. `LEDGER:` —— 模块级 **6 Fixed / 0 Open**，README 同步，两条目含边界声明；F1-F4 全 APPROVE + 用户 okay → `--no-ff` merge；不 push
