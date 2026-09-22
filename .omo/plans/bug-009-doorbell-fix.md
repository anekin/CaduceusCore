# bug-009-doorbell-fix - Work Plan

## TL;DR (For humans)

**What you'll get:** 门铃（doorbell）声明的"状态窗口"在硬件里真正落地：固件每次写命令状态、主机读最后状态，从此落在真实寄存器上而不是掉进无人应答的死窗口；缺陷台账翻成"已修复"（13 Fixed / 2 Open）。

**Why this approach:** 除 RTL 外所有层（固件 20+ 处状态写、Func Model、device server、3 个测试文件）都已按 6 寄存器窗口工作——RTL 是唯一落后方。所以把 RTL 补齐到 ABI，而不是把 ABI 改小：单 RTL 文件 + 两个 TB 翻转，零固件/零 FM/零 spec 结构改动。

**What it will NOT do:** 不改固件（状态写本来就正确，只是此前落在死窗口）；不动 Func Model（已实现该窗口）；不改 ABI 寄存器偏移/宽度/访问标注（wo/ro vs RW 良性漂移维持已记录现状，出范围）；不动 7 个并行会话 dirty 文件；不自动 push。

**Effort:** Medium
**Risk:** Low-Medium - 单 RTL 文件 + 两 TB + spec 注记；doorbell_irq/指针寄存器路径零触碰；最大风险是回归暴露对旧死窗口行为的隐性依赖，FM-SOC 33 + W4 + pytest 全覆盖。
**Decisions to sanity-check:** (1) 扩 RTL 窗口（方案 A）；(2) conformance 行恰好 20 槽（4 指针 + 0x10 + 数组[0..14]，第 16 项 @0x50 与 0x54 边界由 tb_doorbell 覆盖）；(3) W4 batch 重写其 tracked 证据文件后按纪律恢复至 HEAD；(4) 访问标注漂移出范围。

Your next move: 计划已就绪，对我说"开始"或 `/start-work bug-009-doorbell-fix`。

---

> TL;DR (machine): Medium | Low-Medium | doorbell.v +LAST_STATUS+COMPLETION_STATUS[16]（0x00-0x50 21 字窗口，irq/pslverr/bkdoor 零触碰）+ tb_doorbell Test12 翻锚/Test13-14 + run_doorbell_tb target + conformance 20 槽（REG_CNT 6→20，显式 0x50/0x54）+ xverif pre/post + 重建 simv_soc_ibex + apb_smoke/FM-SOC 33/W4/pytest 回归 + spec 注记 resolution + gen regen + 台账 13-fixed-2-open；不 push。

## Scope
### Must have
0. **P0 基线**：前置门（main 含 `a326ef9`）+ 分支 `bug-009-doorbell-fix` + provenance（含 4 个触碰文件基线哈希）+ 7-dirty 终拍断言
1. **H1 harness + X1-pre 表征**：tb_doorbell 加 FSDB dump 守卫块、Makefile 新增 `run_doorbell_tb`、陈旧 simv 守卫、pre-fix `0x14` 探针两行、pre-fix W4 `cycles` 控制值；xverif 死窗口表征
2. **R1**：doorbell.v 扩窗（LAST_STATUS + COMPLETION_STATUS[16]，写/读改键 `word_idx`）+ tb_doorbell Test 12 翻锚 + Test 13/14 + 失败模式矩阵
3. **T1**：conformance TB 翻行（20 槽、`REG_CNT[5]`→20、两处 `ACC_DOCDIVR`→`ACC_RW`、`REG_DOCDIV[5]` 清零、报告字符串、头注释、循环外显式 0x50/0x54 检查）
4. **X1-post**：xverif 活窗口验证（同接口 config，裸偏移域）
5. **回归**：强制重建 full-SoC simv → apb_smoke → FM-SOC 33 → W4 → 全量 pytest
6. **spec resolution + gen regen**：KNOWN_DISCREPANCY → RESOLVED、COMPLETION_STATUS/LAST_STATUS description、pinned decision resolution；生成器注记字面量改写；`--generate` + `--check` + 有界结构字段门 + 定向 pytest
7. **台账**：BUG-009 → Fixed（R1 commit 引证 + 七条 Residual）+ 统计 13 Fixed / 2 Open + README 快照
8. F1-F4 终审

### Must NOT have (guardrails, anti-slop, scope boundaries)
- **firmware/ 零改动**；**rtl/soc/ 其他文件零改动**（apb_decoder / caduceus_soc_top / soc.flist——paddr[11:0] 已路由，doorbell.v 自解码）；rtl/mxu//sfu//vector//intc/、vendored 零改动
- **FM 域零改动**（`sim/models/apb_peripheral.py` 等）；`sim/regmap.py` 零改动（offset 常量本就正确）
- **spec 结构字段零改动**：`offset`/`width`/`access`/`reset`/`array_size` 一律不动——只改 `notes`/`description` 文本与 pinned decision 的 resolution 记录（wo/ro vs RW 良性漂移维持现状，出范围）
- **gen/ 只走 regen**（`scripts/gen_npu_abi.py --generate`），禁止手编
- doorbell.v 内：**指针寄存器（0x00-0x0C）行为、`doorbell_irq`、`pready`/`pslverr`、`bkdoor_*` 路径零触碰**；tb_doorbell Test 1-11 不动；conformance TB 的 pcie/dma DOC-DIV 行与 `check_docdiv` task 本体（`:898-922`）不动
- **7 个并行会话 dirty 文件不动不提交**：`.omo/evidence/task-0-signoff-v3-runner.txt`、`.omo/evidence/task-20-uncertainty-kpis.json`、`.omo/evidence/task-23-perf-spec-ci.txt`、`.omo/notepads/fm-e2e-qwen-cv-software-stack/learnings.md`、`.omo/notepads/phase6-rtl-verification/learnings.md`、`build/evidence/fm-cv-chain.txt`、`build/evidence/w3-4-mobilenetv3-fm.txt`；**禁止 `git add .`/`-A`/`commit -a`**（W4 证据文件在 HEAD 是干净的，只在 todo 5 运行时被重写并按纪律恢复）
- VCS 仅 sz0001 经 `sim/regression/soc-verification-run.sh`，**todo 1→5 串行**；不 push；no_silent_skip（任一回归 FAIL → 签名落档 STOP）

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **tests-after**（先改 RTL，后以 tb_doorbell 翻锚 + conformance 翻行 + xverif post-fix 验收；tb_doorbell Test 12 即 RED anchor）
- 框架：VCS 自检 TB（`tb_doorbell` / `apb_conformance_real_tb`）+ xverif MCP（`apb.query` / `apb.transfer_window`；worker 先加载 `xverif-mcp` skill 取语法）+ pytest（sz0001 fmpytest venv + readline shim + **15 项** ignore，见 AGENTS.md:82-96）
- Evidence: `.omo/evidence/task-{0..7}-bug-009-doorbell-fix.txt`（随对应 todo commit 入库）；每份 sz0001 evidence 必含 **provenance 块**（git HEAD / simv 标识 + VCS 版本 / 完整命令 / FSDB+daidir 路径）
- **grep-able 判定行**：`P0-SNAPSHOT:` / `H1-HARNESS:` / `XVERIF-PREFIX:` / `R1-WINDOW:` / `T2-DOORBELL-TB:` / `T3-CONFORMANCE:` / `XVERIF-POSTFIX:` / `REG-APBSMOKE:` / `REG-FMSOC:` / `REG-W4:` / `PYTEST:` / `SPEC-NOTE:` / `GEN-REGEN:` / `ABI-GATES:` / `LEDGER-STATUS:` / `STATS:`

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave。本计划多为单 todo 波：sz0001 VCS 必须串行 + 同文件依赖，故按下方顺序执行。

- **Wave 1**：todo 0（P0，阻塞全部）
- **Wave 2**：todo 1（harness + X1-pre，**须先于 todo 2**）
- **Wave 3**：todo 2（R1 RTL + tb_doorbell）
- **Wave 4**：todo 3（conformance 翻行，sz0001 串行）
- **Wave 5**：todo 4（X1-post，sz0001 串行）
- **Wave 6**：todo 5（回归，sz0001 串行）
- **Wave 7**：todo 6（spec/gen；**须待 todo 5 完成后**）→ todo 7（台账）
- **终审波**：F1-F4 并行

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 0 | —（前置门 a326ef9） | 1,2,3,4,5,6,7 | — |
| 1 | 0 | 2（提供 harness 与 target） | —（sz0001） |
| 2 | 1 | 3,4,5,6 | —（sz0001） |
| 3 | 2 | 4,5,7 | —（sz0001 串行） |
| 4 | 3 | 7 | —（sz0001 串行） |
| 5 | 2 | 7 | —（sz0001 串行） |
| 6 | 5 | 7 | —（须待 5 完成，见并发说明） |
| 7 | 3,4,5,6 | F1-F4 | — |
| F1-F4 | 7 | merge gate | 彼此并行 |

> **并发说明**：todo 6 含 sz0001 定向 pytest 且 `--generate` 会重写 `gen/` 五个产物，与 todo 5 的 pytest 在同一 NFS 工作树竞争，故**必须待 todo 5 完成后串行**。

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [ ] 0. P0 基线：前置门 + 分支 + provenance + 7 行终拍断言
  What to do / Must NOT do: (1) **前置门**：`git log --oneline main -5` 必须含 `a326ef9`——不含则 STOP。(2) `git checkout -b bug-009-doorbell-fix main`（当前目录，禁止 worktree；确认 `git branch --show-current`）。(3) provenance：git HEAD sha + branch；`sha256sum firmware/build/npu_firmware.hex`；`sha256sum rtl/soc/doorbell.v rtl/tb/tb_doorbell.v rtl/tb/apb_conformance_real_tb.sv spec/npu_abi.json`。(4) 全部落档 `.omo/evidence/task-0-bug-009-doorbell-fix.txt`，**含完整 `git status --porcelain` 快照内联**（不允许"见下文"式悬空引用）。(5) pathspec 提交 plan + draft（`git add -f`）+ evidence。(6) 提交后终拍：`git status --porcelain` 的 M 行恰好 7 行（清单见 Scope Must-NOT）。Must NOT：不动 7 dirty；不用 `git add .`/`-A`/`commit -a`；不重建固件。
  Parallelization: Wave 1 | Blocked by: none（前置门 a326ef9） | Blocks: 1,2,3,4,5,6,7
  References: 7 dirty 清单见 Scope Must-NOT；`git log --oneline main -3`（预期顶部为 a326ef9）
  Acceptance criteria (agent-executable): `git branch --show-current` == `bug-009-doorbell-fix`；evidence 含 HEAD + firmware hex sha256 + 4 文件基线哈希 + 内联 porcelain 快照；M 行恰好 7 行且 ⊆ Must-NOT 清单；判定行 `P0-SNAPSHOT: pass`。
  QA scenarios: happy=provenance 与快照齐 PASS；failure=main 无 a326ef9 或快照出现额外 M 行 → STOP 记录。Evidence `.omo/evidence/task-0-bug-009-doorbell-fix.txt`
  Commit: Y | chore(omo): P0 baseline — branch + provenance snapshot (bug-009-doorbell-fix)

- [ ] 1. H1 harness 准备 + X1-pre 死窗口表征（sz0001，须先于 todo 2）
  What to do / Must NOT do: **(a) harness（无 RTL 行为改动）**：(1) `rtl/tb/tb_doorbell.v` 加 FSDB 守卫块（既有 initial 之外、独立 initial）：`` `ifdef FSDB `` · `initial begin $fsdbDumpfile("tb_doorbell.fsdb"); $fsdbDumpvars(0, tb_doorbell); end` · `` `endif ``。(2) `sim/regression/Makefile` 新增 `run_doorbell_tb`：先确认 `tb_doorbell` 在 Makefile 中零命中（`:174` 的注释只是把 doorbell 列为 conformance 覆盖的外设之一），仿 `run_apb_smoke`（`:126-136`）编译 `rtl/soc/doorbell.v rtl/tb/tb_doorbell.v -top tb_doorbell`，运行后门 `grep -q "RESULT: ALL TESTS PASSED"`。(2b) **pre-fix `0x14` 探针**：在 Test 12 内追加两行——`apb_read(12'h14, rd); check32(rd, 32'd0, "pre-fix: read 0x14 returns 0");` 与 `apb_write(12'h14, 32'hDEADBEEF);`（写被丢弃）。这两行使 "0x014 读 0/写丢弃" 证据在 pre-fix 阶段可产出；todo 2 翻锚 Test 12 时会一并重写。(2c) **陈旧 simv 守卫**：Makefile 规则只依赖源文件，覆盖 `VCS_OPTS` 加 `+define+FSDB` 不会触发重编，而 `soc-verification-run.sh` 的 CLEAN 只删 `simv_soc_cocotb*` → FSDB 编译前先 `rm -f sim/regression/simv_doorbell_tb sim/regression/simv_doorbell_tb.daidir`（或 `make -B`）。(3) 无 FSDB 先自证 harness 绿：`bash sim/regression/soc-verification-run.sh run_doorbell_tb` → `RESULT: ALL TESTS PASSED`（此时 RTL 未改，Test 12 仍断言死窗口 + 新增 0x14 探针通过）。(**b) X1-pre 表征**：(4) FSDB 编译运行：sz0001 上 `cd sim/regression && rm -f simv_doorbell_tb* && make VCS_OPTS="-full64 -sverilog -debug_access+all -kdb -timescale=1ns/1ps +define+FSDB -P $VERDI_HOME/share/PLI/VCS/LINUX64/novas.tab" run_doorbell_tb`（`module load vcs/vcs_2023.12sp2`）。(4b) **FSDB 卫生**：该目标 cwd = `sim/regression`，FSDB 落 `sim/regression/`（**以实际路径为准记录**）；`.gitignore` 不覆盖 `*.fsdb` → 运行后清理 FSDB 或把 `*.fsdb` 加入 `.gitignore`（二选一并写明），确保不许新 untracked 文件破坏 todo 0 的 git-status 纪律。(4c) **pre-fix W4 控制值**：在本 todo 的 pre-fix 树上跑一次 `bash sim/regression/run_w4_perf_batch.sh`，把各 JSONL 条目的 **`cycles`** 值（p0-p4 各 4 条 + fullchain 1 条 = 21 条）记入本 evidence，作为 todo 5 的漂移控制；跑后对 batch 重写的 tracked 证据文件 `git checkout --` 恢复（跑前/跑后 `git status --porcelain build/` 对照）。(5) xverif 会话（worker 先 `skill` 加载 `xverif-mcp` 取语法）：session_open（daidir + fsdb）→ `apb.config.load` 目标接口 = **模块级 `tb_doorbell.u_dut`**（DUT 端口直连 `psel/penable/paddr[11:0]/pwrite/pwdata/prdata`，无 decoder 歧义；`tb_doorbell.v:35` 即实例名）→ `apb.query` 过滤**裸偏移域 `0x010` / `0x014`**：预期读返回 0、写无寄存器效果 → `apb.transfer_window` 抽查一笔死窗口访问 → session_close。(6) evidence 含查询原文摘录 + 判定行。Must NOT：**不改 `rtl/soc/doorbell.v`**（todo 2 的事）；不改 conformance TB；不并发其他 sz0001 任务。
  Parallelization: Wave 2 | Blocked by: 0 | Blocks: 2
  References: `rtl/tb/tb_doorbell.v`（`:35` 实例名 `u_dut`、`:67-102` check32/check_bit、`:109-164` apb_write/apb_read/reg_write_read、`:447-460` Test 12、`:471-478` 汇总打印 `RESULT: ALL TESTS PASSED`）；`sim/regression/Makefile:126-136`（run_apb_smoke 模板）、`:174`（doorbell 仅作 conformance 外设被注释提及）；`sim/regression/soc-verification-run.sh`（auto-SSH + CLEAN 语义）；`.gitignore`（不覆盖 `*.fsdb`）；xverif-mcp skill
  Acceptance criteria (agent-executable): (a) `run_doorbell_tb` sz0001 日志含 `RESULT: ALL TESTS PASSED`（判定行 `H1-HARNESS: run_doorbell_tb-pass`）；(b) 删除陈旧 simv 后 FSDB 编译产出非零字节 FSDB，实际路径记入 evidence；(c) evidence 含 xverif 查询摘录（裸偏移 `0x010` 与 `0x014` 各自读 0、写丢弃）+ 判定行 `XVERIF-PREFIX: dead-window-confirmed` + provenance；(d) `git diff HEAD^ HEAD --name-only` ⊆ {rtl/tb/tb_doorbell.v, sim/regression/Makefile, .gitignore（仅当加 `*.fsdb`）} + evidence；(e) 21 条 pre-fix `cycles` 值记入 evidence，且跑后 tracked 证据文件已恢复（`git status --porcelain build/` 无新增 M 行）。
  QA scenarios: happy=harness 绿 + FSDB 产出 + 死窗口查询证据 + W4 控制值落档；failure=TB 挂 / FSDB 未产出 / xverif 连接失败 → 签名落档 STOP。Evidence `.omo/evidence/task-1-bug-009-doorbell-fix.txt`
  Commit: Y | test(bug009): doorbell harness (FSDB dump + run_doorbell_tb) + pre-fix dead-window characterization + W4 control

- [ ] 2. R1 doorbell.v 扩窗 + tb_doorbell 翻锚/新测（sz0001）
  What to do / Must NOT do: **RTL（rtl/soc/doorbell.v，单文件）**：(1) 新存储 `reg [31:0] last_status_reg;` + `reg [31:0] completion_status_reg [0:15];`，复位块一并清零。(2) 地址译码：新增 `wire [4:0] word_idx = paddr[6:2];`，`addr_valid` 由 `(paddr[11:4] == 8'h00)`（`:70`）改为 `(paddr[11:7] == 5'd0) && (word_idx <= 5'd20)`——有效窗口 0x00-0x50（21 字：4 指针 + LAST_STATUS@0x10 + 16 数组项 0x14-0x50）；0x54+ 维持 silent-0（读 0/写丢弃）。(3) **写路径改键 `case (word_idx)`**：`word_idx` 0-3 → 既有四个指针寄存器赋值（与现行 `reg_sel` 分支逐字一致，因 word 0-3 时 `paddr[3:2]==word_idx`）；`5'd4` → `last_status_reg <= pwdata`；`5'd5..5'd20` → `completion_status_reg[word_idx - 5'd5] <= pwdata`。**不得沿用 `case (reg_sel)`**——`reg_sel = paddr[3:2]` 在扩建后回绕（word 5→1=NPU_HEAD、6→2、7→3、8→0），会把 COMPLETION_STATUS 写别名到指针寄存器并破坏 `doorbell_irq`。(4) **读 mux 同样改键 `word_idx`**（替换现 `reg_sel` 版 `assign reg_rdata`）：**word 0-3 → 四个指针寄存器（HOST_TAIL/NPU_HEAD/HOST_HEAD/NPU_TAIL，顺序同写路径 (3)，取值与现行 `reg_sel` 分支逐字一致——遗漏此支会让指针读回 0、打断 Test 1-11 与固件轮询）**；word 4 → `last_status_reg`；word 5-20 → `completion_status_reg[word_idx-5]`；word 21-31 与越界 → 32'd0。`reg_sel` 声明保留但变为未使用（注释注明，勿删除以免无谓 diff）。(5) **零触碰清单**：`bkdoor_we`/`bkdoor_sel`/`bkdoor_rdata` 路径、`doorbell_irq`（`:127`）、`pready`/`pslverr`（`:121-122`）。(6) 头注释寄存器表（`:7-11`）补 `0x10 LAST_STATUS (RW)`、`0x14-0x50 COMPLETION_STATUS[16] (RW)`、`0x54+ unmapped: read 0 / write ignored`。**TB（rtl/tb/tb_doorbell.v）**：(7) **Test 12 翻锚**（`:447-460`）：改为 `write 0x5A5A_0000 to 0x10 → readback == written`（LAST_STATUS 活）；原"undefined addr 侧效应"语义移入 Test 14（改用 0x54）；Test 1-11 零改动。(8) **新增 Test 13**：**先快照** 4 个指针寄存器（0x00/0x04/0x08/0x0C）+ irq 状态；COMPLETION_STATUS 全 **16 项**（`i = 0..15`，**必须含 i=15@0x50**）写读 `apb_write(12'h14 + i*4, 32'hC0DE_0000 + i)` → 逐项 readback == written；**再断言** 4 指针与快照逐位相同、LAST_STATUS 未被扰动。(9) **新增 Test 14（非空洞 irq 断言）**：先造 `HOST_TAIL != NPU_HEAD`（如 0x00=1、0x04=0）使 `irq == 1`，快照 irq + 4 指针；写入全部状态寄存器（0x10 + 0x14..0x50）；断言 irq 仍为 1 且 4 指针逐位不变；再令两指针相等（irq=0）重复一遍；最后 0x54 unmapped（read 0 / 写丢弃 / 指针不受影响）。(10) 头注释测试清单（`:5-13`，现仅列 Test 1-7）改为完整列出 Test 1-14。(11) **失败模式→检查矩阵（记入 evidence）**：addr_valid 上界过宽→Test 14；下界过窄（拒 0x50）→Test 13 i=15；读 mux 索引错位→Test 13 唯一值；写对但读不可见→Test 12/13；部分实现→Test 12 与 Test 13 分别覆盖；irq 耦合破坏→Test 14；指针别名污染→Test 13/14 快照比对。(12) sz0001 运行 `bash sim/regression/soc-verification-run.sh run_doorbell_tb`。Must NOT：不动指针寄存器行为/irq/pslverr/bkdoor；不动 apb_decoder/caduceus_soc_top/soc.flist；Test 1-11 不动；不改 conformance TB（todo 3）；零 Makefile 改动（target 由 todo 1 建立）。
  Parallelization: Wave 3 | Blocked by: 1 | Blocks: 3,4,5,6
  References: `rtl/soc/doorbell.v` 全文（`:53-56` 存储、`:67-70` 译码、`:75-101` 写、`:109-116` 读、`:121-122` 握手、`:127` irq、`:132-136` bkdoor）；`rtl/tb/tb_doorbell.v`（`:169-172` 地址别名、`:447-460` Test 12）；`sim/regression/Makefile:126-136`；`firmware/npu_firmware.c:440-454`（clamp 镜像语义——RTL 数组即其落点）；`spec/npu_abi.json:599-613`（LAST_STATUS/COMPLETION_STATUS 定义）
  Acceptance criteria (agent-executable): sz0001 `run_doorbell_tb` 日志含 `RESULT: ALL TESTS PASSED`（Test 12 翻锚后 + Test 13/14 PASS 行摘录进 evidence）；判定行 `T2-DOORBELL-TB: pass` + `R1-WINDOW: 0x00-0x50`；`git diff HEAD^ HEAD --name-only` ⊆ {rtl/soc/doorbell.v, rtl/tb/tb_doorbell.v} + evidence；Test 13 覆盖 i=0..15（含 0x50）的 PASS 行在日志中；Test 14 的"irq 保持 1 + 指针逐位不变"断言 PASS 行在日志中。
  QA scenarios: happy=模块 TB 全绿（翻锚后 Test 12 + 新 13/14）；failure=任一 FAIL → dump + 签名 STOP（重点：word_idx 边界 20/21、数组索引 `word_idx-5` 差一）。Evidence `.omo/evidence/task-2-bug-009-doorbell-fix.txt`
  Commit: Y | fix(rtl/soc): implement doorbell ABI status window — LAST_STATUS + COMPLETION_STATUS[16]

- [ ] 3. T1 conformance TB 翻行 + run_apb_conformance_real GREEN（sz0001，串行）
  What to do / Must NOT do: **rtl/tb/apb_conformance_real_tb.sv**：(1) `REG_OFFS` doorbell 行（`:633-634`）改为 20 槽全用：`'{12'h00, 12'h04, 12'h08, 12'h0C, 12'h10, 12'h14, 12'h18, 12'h1C, 12'h20, 12'h24, 12'h28, 12'h2C, 12'h30, 12'h34, 12'h38, 12'h3C, 12'h40, 12'h44, 12'h48, 12'h4C}`（4 指针 + LAST_STATUS + COMPLETION_STATUS[0..14]；第 16 项 `[15]@0x50` 与 0x54 边界由 tb_doorbell Test 13/14 覆盖——20 槽恰好填满 `MAX_REGS=20`，表结构零改动）。(1b) **`REG_CNT` doorbell 6→`32'd20`**（`:619-620` 现为 `'{32'd18, 32'd8, 32'd14, 32'd15, 32'd10, 32'd6, 32'd3}`，索引 5=6；检查循环 `:998/:1023` 以 `REG_CNT` 为界，不改则数组项 0x18-0x4C 永不执行）。(2) `REG_ACC` doorbell 行（`:654-655`）：20 项全 `ACC_RW`（两处 `ACC_DOCDIVR` 移除）。(3) `REG_RST` doorbell 行（`:674-675`）：全 32'd0（值不变，语义从 6 项变 20 项）。(4) `REG_MSK` doorbell 行（`:689`）：已全 FFFF_FFFF——零改动。(5) `doc_bug` mux（`:758`）：移除 `5: doc_bug = "BUG-RTL-SOC-009"` 条目（pcie BUG-010 / dma BUG-011 条目保留）。(5b) `REG_DOCDIV[5][4]` / `[5]`（`:730` 现为 1）**清零**；final-report 字符串 `:1183-1184`（"each bug-filed: BUG-RTL-SOC-009/010/011"）与 `:1212`（GREEN 行点名 009）改为仅 `BUG-RTL-SOC-010/011`。(6) 头注释 `:25-28`、`:633`、`:654`、`:722-723` 更新为修复后状态；**(6b)** 在 TB 头显式声明：doorbell 20 槽统一按 `ACC_RW` 检查是**刻意**的（ABI 标 HOST_TAIL `wo`、HOST_HEAD/NPU_TAIL `ro`，RTL 实现为 RW 超集；该漂移属 documented-benign、出范围，见台账 residual (a)）。(7) **循环外显式检查 0x50 / 0x54**：`check()` 写/读回 `0x50`（活窗；即 firmware clamp 目标 `min(cmd_id,15)` 的落点、窗口最高地址）与 `0x54`（读 0/写丢弃 silent-0）。**这两次 `check()` 必须传 `slv = -1`**（全局，不计入 `slv_checks[5]`——`check()` 在 `slv >= 0` 时累加 `slv_checks[slv]`，`:874-882`；全局用法见 `:1130`）；若误传 `slv=5` 则 per-slave 计数变 62-64，与本行验收自相矛盾。(8) sz0001 运行 `bash sim/regression/soc-verification-run.sh run_apb_conformance_real`（binary 不存在，会全新编译）。Must NOT：pcie/dma 的 DOC-DIV 行与 `check_docdiv` task 本体（`:898-922`）零改动；`seed_intc_pending_real`（`:929-938`）零改动；不改 `MAX_REGS`。
  Parallelization: Wave 4 | Blocked by: 2 | Blocks: 4,5,7 | sz0001 与 4/5 串行
  References: `rtl/tb/apb_conformance_real_tb.sv:617-620`（MAX_REGS/REG_CNT）、`:628-691`（REG_OFFS/ACC/RST/MSK 四表 doorbell 行）、`:729-730`（REG_DOCDIV）、`:751-758`（doc_bug mux）、`:874-882`（check() 的 slv 计数）、`:898-922`（check_docdiv）、`:998/:1023`（检查循环）、`:1056`/`:1093`（docdiv 调用点）、`:1130`（全局 check 用法）、`:1183-1184`/`:1212`（final report）、`:25-28`/`:722-723`（头注释）；`sim/regression/Makefile:184-192`（target + GREEN 门）；`firmware/npu_firmware.c:453`、`firmware/npu-regmap.h:186`（clamp 目标）
  Acceptance criteria (agent-executable): run 日志含 `APB_CONFORMANCE_REAL: GREEN`；**`doc_div_cnt == 4`**（= DMA `ACC_WOS` 2 次（`:1080/:1085`）+ PCIE 2 次：`REG_DOCDIV[4]` 的 index 0/6 为 1（`:729`），对应 `REG_ACC[4]` 的 idx0 = `ACC_FIELD`（CTRL，`:653`，触发于 `:1093`）与 idx6 = `ACC_CONST`（BAR1_MASK 0x18，触发于 `:1056`）；doorbell 两条 DOC-DIVR 归零后 8−4=4）；**`slv_docdivs[5] == 0`**；**doorbell per-slave 检查计数 == 60**（20 个 `ACC_RW` 行 × (Phase1 1 + Phase2 2) = 60；基线 4×3 + 2×2 = 16）；**`REG_CNT[5] == 20` 由 F2 代码复核单独判定**；pcie/dma 的 DOC-DIV 行仍在（grep `BUG-RTL-SOC-010\|BUG-RTL-SOC-011` 命中）；判定行 `T3-CONFORMANCE: green-doorbell-docdiv-0`；`git diff HEAD^ HEAD --name-only` ⊆ {rtl/tb/apb_conformance_real_tb.sv} + evidence。
  QA scenarios: happy=GREEN + `doc_div_cnt=4` + `slv_docdivs[5]=0` + per-slave 60 + pcie/dma DOC-DIV 保留；failure=doorbell 任一 RW 读回不匹配 → 回 todo 2 复核 RTL 译码（no_silent_skip）。Evidence `.omo/evidence/task-3-bug-009-doorbell-fix.txt`
  Commit: Y | test(rtl/tb): doorbell conformance rows flip DOC-DIV→ABI conformance (BUG-009 closed at TB)

- [ ] 4. X1-post xverif 活窗口验证（sz0001，串行）
  What to do / Must NOT do: (1) 在 R1 修复后的代码上重跑 **todo 1 的同一 FSDB 命令**（模块级 `tb_doorbell`：`rm -f simv_doorbell_tb*` 后 `make VCS_OPTS="... +define+FSDB -P $VERDI_HOME/.../novas.tab" run_doorbell_tb`）→ 新 FSDB。(2) xverif 会话（同 todo 1 的接口 config，**裸偏移域**）：`apb.query` **0x010**——写→读回活（LAST_STATUS）；`apb.query` **0x014-0x050**——数组写活；`apb.transfer_window` 抽查一笔 COMPLETION_STATUS 写的波形窗口（PSEL/PENABLE/PADDR[11:0]/PWDATA/PRDATA 完整握手）；session_close。(3) 对照 todo 1 的 pre-fix 查询：**同偏移**行为从"读 0/写丢弃"翻为"写读回环"，evidence 并列摘录前后查询。Must NOT：不并发其他 sz0001；不改代码。
  Parallelization: Wave 5 | Blocked by: 3 | Blocks: 7 | sz0001 与 5 串行
  References: todo 1 的命令与接口 config（`.omo/evidence/task-1-bug-009-doorbell-fix.txt`）；`rtl/tb/tb_doorbell.v` Test 12/13（翻锚后即为活窗口访问源）；xverif-mcp skill
  Acceptance criteria (agent-executable): evidence 含 post-fix 查询摘录（裸偏移 0x010 / 0x014-0x050 写读回环 + 一笔 transfer_window 窗口描述）+ 判定行 `XVERIF-POSTFIX: window-live` + 前后对照（同偏移 pre/post 并列）+ provenance。
  QA scenarios: happy=活窗口证据 + 前后对照落档；failure=查询仍显示死窗口（simv 未含 R1）→ 签名 STOP。Evidence `.omo/evidence/task-4-bug-009-doorbell-fix.txt`
  Commit: Y | test(bug009): xverif post-fix live-window verification evidence

- [ ] 5. 全量回归（sz0001 串行）：重建 simv + apb_smoke + FM-SOC 33 + W4 + 全量 pytest
  What to do / Must NOT do: **串行顺序**：(1) `bash sim/regression/soc-verification-run.sh run_apb_smoke` → `REG-APBSMOKE: pass`。(2) **强制重建 full-SoC simv**：`run_ibex_full_rtl.sh:49` 只在 `[ ! -x "$SIMV" ]` 时编译、否则打印 `Reusing existing simv`；`run_w4_perf_batch.sh:25-29` 仅在缺失时报错；而 `build/ibex_full_rtl/simv_soc_ibex` 现存且早于 R1 提交 → **必须先 `rm -rf build/ibex_full_rtl/simv_soc_ibex build/ibex_full_rtl/simv_soc_ibex.daidir build/ibex_full_rtl/csrc`**，否则 FM-SOC-33 与 W4 会跑改动前的旧 RTL（headline 回归变假证据、W4 零漂移门因二进制未变而必然通过）。(3) `bash sim/regression/run_fm_soc_all.sh` → `REG-FMSOC: 25-pass-8-skip-0-fail-0-timeout-total-33`（8 SKIP 必须是 FM-SOC-014/015/016/021/022/023（superseded）+ FM-SOC-017/019（not-applicable）这 8 个 case ID，不得只记"8 条理由"）。(4) `bash sim/regression/run_w4_perf_batch.sh` → `REG-W4: 6-pass-0-fail`；**逐 JSONL 条目（21 条）的 `cycles` 与 todo 1 (4c) 的 pre-fix 控制值严格相等（== 0 漂移）**，任何差异 → 签名 STOP 归因；跑前/跑后 `git status --porcelain build/` 对照，对 batch 重写的 tracked 证据文件逐项 `git checkout --` 恢复至 HEAD，fresh 数据保留在 gitignored run log + 本 evidence。(5) 全量 FM pytest（AGENTS.md COMMANDS 的 fmpytest 命令，`PYTHONPATH=sim:gen:/tmp/fmpytest-shim` + **15 项** ignore）→ `PYTEST: <实际计数>-passed`；**失败判定用 node-ID 集合差**：基线 = `build/evidence/bug-012-t3-pytest-run.log`（43 条 `^(FAILED|ERROR)` 行，末行 `32 failed, 2279 passed … 11 errors`）；执行 `comm -13 <(grep -E '^(FAILED|ERROR)' <base> | sed 's/ - .*//' | sort -u) <(grep -E '^(FAILED|ERROR)' <new> | sed 's/ - .*//' | sort -u)` 必须输出为空；**前置守卫**：基线文件存在 ∧ `grep -cE '^(FAILED|ERROR)'` == 43，不满足 → STOP（该文件在 gitignored `build/evidence/` 下，若缺失则先把归一化的 43 条 node-ID 清单写入本 evidence 再比对）；doorbell 相关 FM 测试（`sim/tests/test_firmware_boot_sequence.py`、`sim/tests/test_spike_ibex_ring_alignment.py`）必须全绿。每项 evidence 含 provenance + simv hash/mtime（须晚于 R1 提交）。Must NOT：并发 sz0001；改任何测试断言；跳过任何 case；旧证据冒充新跑。
  Parallelization: Wave 6 | Blocked by: 2（执行顺序在 4 之后串行） | Blocks: 7
  References: `sim/regression/Makefile:126-136`（apb_smoke 门）；`sim/regression/run_ibex_full_rtl.sh:49`（simv 存在则复用）；`sim/regression/run_w4_perf_batch.sh:25-29`（缺失才报错）、`:44-88`（batch + 判定）；`sim/regression/run_fm_soc_all.sh`；`sim/perf_tests.py:66`（evidence 键为 `cycles`）；`build/evidence/bug-012-t3-pytest-run.log`（pytest 基线）；`.omo/evidence/task-3-bug-012-driver-fix.txt`（FM-SOC 25/8/0/0/33 与 8 SKIP ID 基线）；AGENTS.md COMMANDS（fmpytest 完整命令）
  Acceptance criteria (agent-executable): evidence 含判定行 `REG-APBSMOKE:` / `REG-FMSOC:` / `REG-W4:` / `PYTEST:` 全 pass；`SIMV-REBUILT:` 记录重建后 simv hash/mtime 晚于 R1 提交；W4 21 条 `cycles` 与 todo 1 控制值逐一相等的对照表；`comm -13` 差集为空的原文；FM-SOC 8 个 SKIP case ID 逐一列出；tracked 证据文件已恢复（无新增 M 行）。
  QA scenarios: happy=四组全绿 + simv 确实重建 + 差集为空；failure=任一 FAIL / 差集非空 / cycles 漂移 → 日志签名 STOP 回 todo 2 归因。Evidence `.omo/evidence/task-5-bug-009-doorbell-fix.txt`
  Commit: Y | test(bug009): rebuilt-simv + apb-smoke + FM-SOC 33 + W4 + pytest regression evidence

- [ ] 6. spec resolution + gen regen（**须待 todo 5 完成后串行**）
  What to do / Must NOT do: (1) `spec/npu_abi.json`：`DOORBELL.notes.KNOWN_DISCREPANCY`（`:568`）改写为 RESOLVED 口径——说明本次修复后 HW 已实现 LAST_STATUS@0x10 与 COMPLETION_STATUS[16]@0x14-0x50（`rtl/soc/doorbell.v`），并保留 by-design 残留："固件按 `min(cmd_id,15)` 写 MMIO 镜像（索引 clamp），完整 1024 条记录仍在 DRAM completion ring——16 项 HW 数组是状态镜像，不是无损完成路径"；`COMPLETION_STATUS.description`（`:612`）同步改写（mirror + clamp 语义）；**`LAST_STATUS.description`（`:604`）同步改写**——现值 "0=done, non-zero=error" 在修复后可观察为假（固件实际写进度标记 `0x00005000|op`…`0x00007000|op`、`0x00002000|status`，成功 `0xAA` / 失败 `0xBB`，`firmware/npu_firmware.c:300-318/:554-576/:724/:750`），且该文本会渲染进 host 可见的 `gen/npu_abi.h:64`/`:141`；改写为真实编码语义 + 一句"host 不得在 HOST_HEAD 推进前解读 LAST_STATUS"约束。pinned decision `DOORBELL_COMPLETION_STATUS_SIZE`（`:1790-1792`）description 追加 resolution 记录（HW window 已扩到声明的 16 项；cmd_id>15 由固件 clamp + DRAM ring 处理）。**不要改 `KNOWN_DISCREPANCY` 键名**（`sim/tests/test_npu_abi_schema.py:159` 断言该键存在，只改取值文本）。(2) **生成器注记字面量改写**：`scripts/gen_npu_abi.py:474-480` 的 firmware-header "Known Discrepancy" 块是**硬编码字面量**（只插值 `rc['ring_entries']`），不读 spec notes → 只改 spec 不会更新 `gen/npu_abi_firmware.h:164-168`；**确定性做法：直接把 `:475-479` 五行的字面量改写为 RESOLVED 口径文本**（不做 schema-render 重构——那会牵动其它章节输出）。(3) regen：`python3 scripts/gen_npu_abi.py --generate`（无参只打印用法；`:13-14`、`:759-767`）；随后 `git diff -- gen/` 应显示 `gen/npu_abi_firmware.h` + `gen/npu_abi.md`/`gen/npu_abi.py`/`gen/npu_abi.h` 的注记同步。(4) **有界结构字段门**（只比 5 个结构字段，`notes`/`description` 由构造排除：天然可满足，且能堵住 `access`/`reset`/其他 `array_size` 改动经 regen 后绿灯通过的假绿通道——`--check` 只 filecmp spec↔gen、绑定测试只抓 offset、schema 测试只抓 width/枚举/冲突/COMPLETION array_size）：
```bash
python3 - <<'PY'
import json, subprocess
F = ("offset","width","access","reset","array_size")
def sig(d):
    return {f"{mn}.{rn}": tuple(r.get(f) for f in F)
            for mn, m in d.get("modules", {}).items()
            for rn, r in m.get("registers", {}).items()}
old = sig(json.loads(subprocess.check_output(["git","show","main:spec/npu_abi.json"]).decode()))
new = sig(json.load(open("spec/npu_abi.json")))
assert old == new, "STRUCT DRIFT: " + str([k for k in old if old.get(k) != new.get(k)])
print("STRUCT-FIELDS: ok")
PY
```
(5) `python3 scripts/gen_npu_abi.py --check` 必须 exit 0。(6) **定向 pytest（用 todo 5 相同的 fmpytest env——readline shim 是强制项，裸 `pytest` 不可接受）**：`env PATH=/home/zhengs/venvs/fmpytest/bin:/usr/bin:/bin PYTHONPATH=sim:gen:/tmp/fmpytest-shim /home/zhengs/venvs/fmpytest/bin/python -m pytest sim/tests/test_npu_abi_schema.py sim/tests/test_npu_abi_bindings.py -q` 全 PASS。Must NOT：不改 registers 结构字段（offset/width/access/reset/array_size）；不手编 gen/；不动 spec 其他模块；不改 `KNOWN_DISCREPANCY` 键名。
  Parallelization: Wave 7 | Blocked by: 5 | Blocks: 7
  References: `spec/npu_abi.json:564-614`（DOORBELL 块 + notes + 三处待改文本）、`:1790-1792`（pinned decision）；`gen/npu_abi_firmware.h:164-168`（待更新注记块——由生成器 `:474-480` 硬编码产出）；`gen/npu_abi.h:64`/`:141`（LAST_STATUS 文本渲染处）；`scripts/gen_npu_abi.py:13-14`（CLI）、`:474-480`（硬编码块）、`:759-767`（argparse）、`:778-786`（`--check` temp-regen + filecmp）；`sim/tests/test_npu_abi_schema.py:156-165`（含 `:159` KNOWN_DISCREPANCY 键断言）；`firmware/npu_firmware.c:445-453`（clamp 镜像）
  Acceptance criteria (agent-executable): `grep -n "RESOLVED" spec/npu_abi.json` ≥1；`gen/npu_abi_firmware.h` 注记块已同步（grep RESOLVED 命中）；`LAST_STATUS.description` 已改写（grep 不再是 "0=done, non-zero=error"）；有界门打印 `STRUCT-FIELDS: ok`；`--check` exit 0；定向 pytest 全 PASS；判定行 `SPEC-NOTE: resolved` + `GEN-REGEN: clean-regen` + `ABI-GATES: pass`；`git diff HEAD^ HEAD --name-only` ⊆ {spec/npu_abi.json, scripts/gen_npu_abi.py, gen/*} + evidence。
  QA scenarios: happy=全部门通过 + 三处文本已改；failure=有界门报 STRUCT DRIFT 或 pytest 失败 → STOP（结构化改动越界，签名上报）。Evidence `.omo/evidence/task-6-bug-009-doorbell-fix.txt`
  Commit: Y | docs(abi): resolve doorbell KNOWN_DISCREPANCY — generator note fix + regen (HW window implemented)

- [ ] 7. 台账 BUG-009 → Fixed + 统计 13/2 + README（本地，待 3/4/5/6 证据）
  What to do / Must NOT do: (1) `docs/bugs/bugs-soc-rtl.md` BUG-009 条目（`:678-727`）：**Status → Fixed**（Root Cause 段原文保留）；Fix 段：R1 commit 引证（`fix(rtl/soc): implement doorbell ABI status window — LAST_STATUS + COMPLETION_STATUS[16]`）+ 机制一句话（doorbell.v 实现 LAST_STATUS + COMPLETION_STATUS[16]@0x10-0x50，addr_valid 扩 21 字窗口；固件 20+ 处状态写与 clamp 镜像从此落在活寄存器）+ **Residual constraints 七条**：(a) 访问标注漂移（ABI `wo`/`ro` vs RTL RW 超集）维持 documented-benign——用户 2026-09-22 拍板出范围（RW 超集承重：固件轮询读 HOST_TAIL）；(b) COMPLETION_STATUS 镜像 cmd_id>15 由固件 clamp + 完整 1024 记录在 DRAM completion ring——by-design；(c) 地址别名与越界语义：`0x51-0x53` 因低两位不参与译码而别名到 slot 15（`0x50`，与既有 4 寄存器解码同风格），`0x54-0x7F` 维持 unmapped（读 0/写丢弃/`pslverr` 保持 0）；(d) `firmware/npu-regmap.h:317-322` 的 "Known Discrepancy" 注释仍称 RTL 未实现（固件本计划零改动）——留作已记录残留；(e) `sim/rtl_soc_runner.py:2688-2689` 注释仍称 "the RTL doorbell only implements the four head/tail pointer registers"——陈旧注释、行为不受影响（该路径读 DRAM），留作残留；(f) FM↔RTL 收敛仅限声明窗口 **0x00-0x50**：Func Model 的 doorbell 处理是无边界整页 store/readback（`sim/mmio_bridge.py:719-724`），`sim/models/apb_peripheral.py:350` 只声明一个 0x14 标量字段、不建模数组项 → **≥0x54 处 FM 会存并读回、RTL 恒返回 0，属刻意保留的语义分歧**，禁止无条件宣称"收敛"；(g) 陈旧行数/引用类文档漂移：`README.md:450`、`docs/rtl_development_plan.md:1504`、`rtl/ip/README.md:124`、`docs/soc-fm-gap-spec.md:1013`/`:1105` 的 "113 行" 与 `spec/soc_golden_contract.md:296` 的失效 LAST_STATUS 文本**不在本计划范围**，显式记录为既有漂移（本计划不新增 dated snapshot 行）。Verification 段补 task-1..5 证据路径 + 判定行。(2) By-Status 表：**:413** Fixed 行 12→13（+BUG-RTL-SOC-009）；**:417** Open 行 3→2（移除 BUG-RTL-SOC-009）；**⚠ `:415` 是 Pending（waiver）行、`:416` 是 Accepted 行——绝对不可动**；By-Severity Major 行是 **`:406`**——不动（BUG-009 仍 Major、计数 12 不变）。(3) Quality Metrics（`:435-444`）：`Bugs closed Fixed | 12 (70.6%)` → `13 (76.5%)`；`Open / under investigation | 3 (17.6%)` → `2 (11.8%)`。(4) `README.md` 状态快照：`17 = 12 Fixed / 1 Pending（waiver 待签）/ 1 Accepted（reconstruction-failure）/ 3 Open` → `17 = 13 Fixed / 1 Pending（waiver 待签）/ 1 Accepted（reconstruction-failure）/ 2 Open`。(5) 判定行 `LEDGER-STATUS: Fixed` / `STATS: 13-fixed-2-open`。Must NOT：无 3/4/5 证据不 claim Fixed；不动 BUG-002/007/010/011/012/WDT-001 字段；不动 `:415`/`:416`；不动 docs/ 其他文件（(g) 列出的漂移文件不修）。
  Parallelization: Wave 7 | Blocked by: 3,4,5,6 | Blocks: F1-F4
  References: `docs/bugs/bugs-soc-rtl.md:678-727`（BUG-009 条目）、`:405-407`（By-Severity）、`:411-418`（By-Status）、`:435-444`（Quality Metrics）；`README.md` 状态快照行（现值 12/1/1/3）；todo 1-6 的 Commit: 行与 evidence 路径
  Acceptance criteria (agent-executable): `grep -n "LEDGER-STATUS: Fixed" .omo/evidence/task-7-bug-009-doorbell-fix.txt` ≥1；台账含 Status Fixed + R1 commit 引证 + 七条 Residual (a)-(g) + Verification 证据路径；`grep -n "Bugs closed Fixed | 13" docs/bugs/bugs-soc-rtl.md` ≥1 且 `grep -n "Open / under investigation | 2" docs/bugs/bugs-soc-rtl.md` ≥1；README 行含 `13 Fixed` 与 `2 Open`；`git diff main..HEAD --name-only -- docs/ README.md` ⊆ {docs/bugs/bugs-soc-rtl.md, README.md}。
  QA scenarios: happy=判定行 + 五要素 + 计数一致；failure=计数遗漏（旧值 grep 仍命中）→ 补齐复跑。Evidence `.omo/evidence/task-7-bug-009-doorbell-fix.txt`
  Commit: Y | docs(bugs): BUG-RTL-SOC-009 Fixed — doorbell ABI status window implemented in RTL

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit — todos 0-7 逐条：evidence 存在（task-{0..7}-bug-009-doorbell-fix.txt）、acceptance 断言复跑（`git log --oneline main..HEAD` 按预声明 message 定位各 commit）、判定行齐全（P0-SNAPSHOT/H1-HARNESS/XVERIF-PREFIX/R1-WINDOW/T2-DOORBELL-TB/T3-CONFORMANCE/XVERIF-POSTFIX/REG-APBSMOKE/REG-FMSOC/REG-W4/PYTEST/SPEC-NOTE/GEN-REGEN/ABI-GATES/LEDGER-STATUS/STATS）、无 silent skip（FM-SOC 8 SKIP 按 case ID + pytest 差集原文）。
- [ ] F2. Code quality review — doorbell.v diff 恰好限定"存储新增 + addr_valid 译码 + `case (word_idx)` 写路径 + `word_idx` 版读 mux + 头注释"，且**不得残留 `reg_sel` 驱动的扩展译码**；指针寄存器行为/`doorbell_irq`/`pready`/`pslverr`/`bkdoor` 路径逐 hunk 确认零改动；word_idx 边界（20 有效/21 无效）与数组索引 `word_idx-5` 差一无误；tb_doorbell Test 12 翻锚语义正确 + Test 13 覆盖 `i=0..15`（含 0x50）+ Test 14 为非空洞快照断言 + Test 1-11 零改动 + 头注释列全 1-14；conformance 20 槽行算术正确（4+1+15）+ **`REG_CNT[5] == 20`** + 0x50/0x54 显式检查存在且传 `slv=-1` + REG_DOCDIV doorbell 两槽清零 + 报告字符串仅 010/011 + pcie/dma DOC-DIV 行与 `check_docdiv` 本体未动；生成器 `:475-479` 已改写且 `gen/npu_abi_firmware.h` 注记同步；spec 结构字段零改动（由 todo 6 的有界门 `STRUCT-FIELDS: ok` 证明）；台账不 overclaim（Fixed 有 commit + 证据 + 七条 Residual）。
- [ ] F3. Real manual QA — fresh 独立复跑（sz0001）：`run_doorbell_tb`（`RESULT: ALL TESTS PASSED` + Test 12/13/14 PASS 行）+ `run_apb_conformance_real`（`APB_CONFORMANCE_REAL: GREEN` + `doc_div_cnt == 4` + `slv_docdivs[5] == 0` + doorbell per-slave 检查计数 60）+ 定向 pytest（todo 6 的 fmpytest env）+ `gen_npu_abi.py --check`；输出逐项对照 task-1/2/3/6 evidence。**前置条件：sz0001 可达——不可达 ⇒ F3 判 BLOCK 并上报，不得降级 APPROVE。**
- [ ] F4. Scope fidelity — `git diff $(git merge-base main HEAD) HEAD --name-only` ⊆ {`rtl/soc/doorbell.v`, `rtl/tb/tb_doorbell.v`, `rtl/tb/apb_conformance_real_tb.sv`, `sim/regression/Makefile`, `.gitignore`（仅当加 `*.fsdb`）, `spec/npu_abi.json`, `scripts/gen_npu_abi.py`, `gen/*`, `docs/bugs/bugs-soc-rtl.md`, `README.md`, `.omo/*`}；**firmware/、sim/ 产品代码（models/regmap.py/device_server.py/rtl_soc_runner.py 等）、rtl/ 其他目录、vendored 零命中**；todo 0 的 4 文件基线哈希对照；每提交 `git show --name-only` 不含 7 dirty 文件；W4 tracked 文件恢复纪律复核（跑前/跑后对照无新增 M 行）；simv 重建证据（hash/mtime 晚于 R1 提交）；未 push；分支纪律（单 worktree）。

## Commit strategy
- 一个 todo 一个原子 commit（type: fix/test/docs/chore），message 预声明于各 todo `Commit:` 行；evidence 随对应 todo 一并入库（`.omo/evidence/task-N-bug-009-doorbell-fix.txt`，`git add -f`）
- staging 纪律：只允许逐 todo 显式路径 `git add`；**禁止 `git add .`/`-A`/`commit -a`**
- sz0001 VCS 串行（todo 1→2→3→4→5）；**todo 6 须待 5 完成后执行**（同一 NFS 工作树上 `--generate` 重写 gen/ 会与 todo 5 的 pytest 竞争）
- F1-F4 全 APPROVE + 用户 explicit okay 后 `--no-ff` merge 回 main；**不自动 push**

## Success criteria
1. `XVERIF-PREFIX: dead-window-confirmed` + `XVERIF-POSTFIX: window-live` —— xverif 波形级前后对照闭环（死窗口→活窗口）
2. `R1-WINDOW: 0x00-0x50` + `T2-DOORBELL-TB: pass` —— doorbell.v 实现 LAST_STATUS + COMPLETION_STATUS[16]；模块 TB 翻锚后全绿（Test 13 十六项零串扰 + Test 14 非空洞 irq 快照）
3. `T3-CONFORMANCE: green-doorbell-docdiv-0` —— conformance GREEN，`doc_div_cnt==4`、`slv_docdivs[5]==0`、doorbell per-slave 60、`REG_CNT[5]==20`、0x50/0x54 显式检查通过，pcie/dma DOC-DIV 保持
4. 回归全 PASS：`REG-APBSMOKE` / `REG-FMSOC: 25-pass-8-skip-0-fail-0-timeout-total-33`（**在强制重建 `simv_soc_ibex` 之后**）/ `REG-W4: 6-pass-0-fail`（21 条 `cycles` 与 pre-fix 控制值严格相等）/ `PYTEST`（`comm -13` node-ID 差集为空）
5. `SPEC-NOTE: resolved` + `GEN-REGEN: clean-regen` + `ABI-GATES: pass` —— spec 三处文本改写 + 生成器注记同步 + 有界结构字段门 `STRUCT-FIELDS: ok` + `--check` exit 0 + 定向 pytest 全 PASS
6. 台账 BUG-009 **Fixed**（R1 commit 引证 + 七条 Residual (a)-(g)）+ 统计 13 Fixed / 2 Open + README 同步；`LEDGER-STATUS: Fixed` / `STATS: 13-fixed-2-open` 落档
7. F1-F4 全 APPROVE + 用户 okay → `--no-ff` merge 回 main；变更集 ⊆ 白名单；固件/sim 产品代码零改动；每提交无 7 dirty 文件；未 push
