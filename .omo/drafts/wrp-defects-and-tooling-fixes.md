---
slug: wrp-defects-and-tooling-fixes
status: awaiting-user-decision
intent: clear
review_required: false
pending-action: deliver plan summary; wait for user start (`/start-work wrp-defects-and-tooling-fixes`) or dual high-accuracy review request
approach: A1（RTL 主修的两个缺陷）+ B1（正式立案、修完一起 Fixed）+ tests-after；工具链按最小风险修（rm -f 路线，不动依赖图）
---

# Draft: wrp-defects-and-tooling-fixes

## Components (topology ledger)
| id | outcome (one line) | status | evidence path |
| --- | --- | --- | --- |
| 0-p0-baseline | branch wrp-defects-and-tooling-fixes @ f1d9652 + provenance | active | .omo/evidence/task-0-wrp-defects-and-tooling-fixes.txt |
| 1-tooling | 坏 target/假通过判定/清理列表/untrack 产物 修复 | active | task-1 |
| 2-wrp1 | DONE 门控到 drain 完成（BUG-MXU-WRP-001） | active | task-2 |
| 3-wrp2 | preload 用 WRP_K_TILES + TB 先编程（BUG-MXU-WRP-002） | active | task-3 |
| 4-nits | F2 四小修（注释/penable/恢复门/探测收窄） | active | task-4 |
| 5-regression | wrapper 6/6 + conformance + e2e + FM-SOC 33 | active | task-5 |
| 6-ledger | 两条目 Fixed；模块级 6F/0O；README | active | task-6 |
| F1-F4 | final wave all APPROVE | active | (plan file) |

## Open assumptions (announced defaults)
| assumption | adopted default | rationale | reversible? |
| --- | --- | --- | --- |
| DONE 门控形态 | latched（engine_done 置位、下次启动清）+ `so_fifo_empty && so_state==SO_IDLE` | 若 status_done 是脉冲则直接合并会永不置位；锁存对固件是"更晚但更真" | yes |
| wrp_k_tiles==0 语义 | 视为 1 tile | 防未编程时 0 次 fetch；与派生时代的默认（1）一致 | yes |
| 工具链范围 | 只 4 项（cd/判定/清理/untrack 5 产物）；VCS-missing 回退、Makefile 依赖图不动 | 最小风险；缺 VCS 的假 PASS 已被 rm-f 路线大幅抵消 | yes |
| F2 恢复门 | 加"本拍无握手"门 或 显式 residual（worker 二选一） | 一拍窗口、仅故障路径；显式化即可 | yes |
| 立案方式 | 条目直接以 Fixed 落账（含根因/修复/证据） | 修复在立案前已验证（todo 5） | yes |

## Findings (cited - path:lines)
- **WRP-1**（store-out drain 早于 DONE，RTL 缺陷）：DONE 源自 `rtl/mxu/controller.v:316-318`（STORE_OUT loop 后置位）→ `rtl/mxu/mmio_if.v:140` → `rtl/mxu/mxu_top.v:112-114/146-148` → wrapper 读 mux `rtl/wrapper/mxu_soc_wrapper.v:153,297`；与 SO drain FSM `:670-788` **零连接**。controller 1/cycle 灌 64 行入 64 深 FIFO（`:637,661-666`），SO 逐行 drain ~6cyc/行（`:717-783`）。时序铁证：rows0-10 至 2840ns、DONE@2880ns、错从 row11（`.omo/evidence/task-3-rtl-open-bugs-cleanup.txt:278-291`）。**固件可见**：`firmware/npu_firmware.c:275-281` 256-nop 绕行。失败的测是 `test_mxu_single_tile_compute`（`store_out_burst` 只查 row0，不暴露）。
- **WRP-2**（preload K-tile 数取错，RTL 缺陷 + TB 缺口）：preload 用 `wrp_k_tiles_derived`（源自 DIM0，`mxu_soc_wrapper.v:238-239` → `:483/:509`），而 **`wrp_k_tiles`(0x44) 被 FSM 完全忽略**；固件与 TB 都在 TRIG_LOAD **之后**才写 DIM0（`firmware/npu_firmware.c:247-272`、`test_mxu_wrapper.py:208-215`）→ K=128 只预载 1 tile（日志单 burst：`build/evidence/t4-2-wv-mxu-test_mxu_accumulate_mode.log:129,147`）→ 第二 tile 读无 init 的 `weight_buf[32:63]`/`activation_buf[64:127]`（`:363-364`）→ X → `m_axi_wdata`（`:856`）→ cocotbext `axi_slave.py:154` ValueError。副发现：`ctrl_acc_mode` 在 `controller.v:37` 从不被读（跨 K-tile 累加恒开，`cf6736b` 后语义）；`COCOTB_RESOLVE_X` 会把 X 归零→静默错算，不可用。
- **工具链**（比上一轮记录更广）：`Makefile:1305-1315` 三 target 缺 `cd $(REPO_ROOT) &&`（soc-verification-run.sh:40 cd 到 sim/regression → 127）；`wv_run_mxu.sh:96-105` 的 `grep -qE 'TEST.*PASS'` 匹配失败汇总行 `TESTS=1 PASS=0 FAIL=1` → 假 PASS + **exit 0**；`wv_run_sfu.sh:77-95`/`wv_run_vector.sh:75-106` 同病（后者还把"无标记 exit 0"记 PASS）；清理列表只有 `simv_soc_cocotb*`（`soc-verification-run.sh:45`）；`run_ibex_full_rtl.sh:50,58` 复用旧 `simv_soc_ibex`（33 例主假 PASS 通道）；Makefile flist-only 规则 `:96/:220/:399/:1116`；5 个 `wrap-*` 产物 tracked 且每次重写（`git ls-files` 证实）。

## Decisions (with rationale)
1. A1：两个缺陷都 RTL 主修（恢复文档契约：DONE⇒数据可见；tile 数⇒专用寄存器），TB 只补漏编程。**用户已批准（2026-09-24 "同意"）**。
2. B1：WRP-1/2 立为 `BUG-MXU-WRP-001/002`，随本计划 Fixed（模块级 4F/0O → 6F/0O）。**已批准**。
3. tests-after；回归面 = wrapper 套件 + conformance + e2e mxu + FM-SOC 33。**已批准**。
4. 工具链最小风险：rm -f 路线；不动依赖图与 VCS-missing 回退。

## Scope IN
P0；工具链 4 项；WRP-1 RTL；WRP-2 RTL+TB；F2 四小修；全量回归；台账两条目 + README。

## Scope OUT (Must NOT have)
rtl/mxu/**、firmware/、spec/、gen/、vendored；Makefile 依赖图与 :72-78；conformance TB 与 010/011/WDT 已验收内容；`COCOTB_RESOLVE_X`；`wrap-bug005/007-result.txt` 删除；7 dirty 文件；push；worktree。

## Open questions
（无阻塞性；范围分叉已由用户拍板 A1+B1+tests-after）

## Review receipts
- Metis gap analysis (2026-09-24): 2 BLOCKER + 3 HIGH + 6 MEDIUM + 3 LOW — **all folded**:
  - BLOCKER: "失败样本 exit 1" 不可执行（解析内联/嵌在 SSH heredoc）→ todo 1(b) 抽出共享 helper `scripts/parse_cocotb_verdict.sh` + fail/pass fixtures，acceptance 变为可构造。
  - BLOCKER: 作用域矛盾（todo 4 改 `bugs-soc-rtl.md` 但 F4 白名单不含）→ 该文件加入 F4 白名单 + todo 4/(5) 与 todo 6 acceptance 更新。
  - HIGH: WRP-1 只门控 APB 读、`irq` 仍早到 → todo 2(4) 强制显式处理（同条件门控或 residual），F2 核查。
  - HIGH: firmware `WRP_K_TILES` 写入落地未证 → TB 加读回断言 + e2e/FM-SOC K>64 覆盖；`cocotb_bridge.py:2267` 先写后触发佐证。
  - HIGH: FM-SOC 新基线未立（stale simv 可能掩盖）→ todo 1(c) 先跑强制重建基线并归因新失败；todo 5 改基线相对判定（不硬编码 25/8）。
  - HIGH/MEDIUM: sfu 汇总为 `TESTS=5`（禁写死 1）；untrack 集以 grep 全脚本为准（含 `wrap-sfu-debug.txt`/`wv_vector_logs/*.dbg`）；wrapper simv 不在 runner 目录（去掉误加项）；`wrap-regression-summary.txt` 属 `wv_regression.sh`。
  - MEDIUM: 台账旧 Note "stats stay at 4F/0O" 与新条目自相矛盾 → todo 6(2) 强制改写；`so_drain_done` W/B 语义写明；多 n-tile 映射记 residual；计划内行号漂移 → 全部按内容定位。
  - LOW/INFO: `status_done` 已知是 sticky 电平（取消脉冲锁存过度设计）；读路径覆写精确到 `apb_mmio_prdata`；README 旧值 grep 限域。
- High-accuracy review: **user-requested 2026-09-24** — Round 1: **Momus NOT-OKAY** (1 BLOCKER: `status_done` 非 wrapper 可见网；1 HIGH: F2-2 conformance-TB 作用域冲突；4 MEDIUM: sfu=7 非 5、清理项矛盾/无效、untrack 覆盖不足、burst 计数不可验；2 LOW) + **Oracle NOT-OKAY** (2 BLOCKER: 天真 IRQ 与门会吞脉冲、`csrc` 真实位置在仓库根；3 HIGH: firmware 走 BUSY 措辞错、清理漏 `csrc`、always-rebuild 未先删；3 MEDIUM: K>128 越界、WDT-trip/FIFO-wrap 语义、解析器仍可 fail-open；2 LOW)。**全部折入（全量重写）**：可见粘滞完成代理（禁止引用 status_done）、IRQ 转显式 residual + 禁与门、BUSY 等待者措辞校正（256-nop 保持 load-bearing）、`$REPO_ROOT/csrc` + rm-before-rebuild、K≤128 边界、FIFO latency/WDT-trip 语义、解析器 self-consistency + 无裸 exit 0、untrack 6 产物 + `.gitignore` + 历史 `.dbg` 保护、conformance-TB 部分转 residual、F4 白名单同步。**Round 2 进行中（复用同两会话）。** → Round 2 结果：**Momus OKAY**（2 MEDIUM + 4 LOW）+ **Oracle NOT-OKAY**（N1 HIGH: S_DONE 单周期需锁存；N2-N5 MEDIUM: option② 需降级、`wv_regression.sh` 硬编码/仍在审计路径、vector 静默漏 1/6 用例、`csrc` 断言空洞；N6-N8 LOW）→ 全部折入（锁存单一路径 + 同拍清除优先、option② 降级、`wv_regression.sh` helper-or-residual、vector 默认补测、字面 `REPO_ROOT/csrc` + mtime 主判据、`$BUILD_DIR/csrc`、untrack 断言、4 绿测口径、IRQ 标记）。**Round 3：Momus OKAY + Oracle OKAY**（剩余仅文本级：latch-clear 需带 `mmio_cs`、同拍清除胜出、Success criterion 1 对齐 4 绿测、F4 补 `wv_f1_audit.sh`、`.gitignore` 列名不得通配、F1 检查 `WRP1-IRQ-RESIDUAL:` 标记）→ **全部落实**。**双评审通过（3 轮），计划交付、待用户 `/start-work`。**

## Approval gate
status: awaiting-user-decision
<!-- 用户已批准范围（A1+B1+tests-after）；计划已写入并过 Metis（findings 全折入）；等用户选择 start 或 dual review。 -->
