# fm-hardening-phase10 - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->

**What you'll get:** Func Model 验证加固——把 Phase 10 在真实 RTL 段跑（每次 7.5 小时）才暴露的 6 类 bug，全部变成在纯 Python 的 Func Model 阶段几秒内必然触发的断言和回归用例：(1) 内存区域重叠与环语义分歧（layout/ring）；(2) scale/accumulate golden 空洞（裸 INT32 golden 放过 stub）；(3) 双 activation packer 分歧；(4) 跨语言常量漂移；(5) 段边界 SRAM 残留；(6) 回归套件未接线的自动门禁。

**Why this approach:** Phase 10 的复盘证明 Func Model 数值上已 bit-exact，缺的不是"算得更准"而是"布局/契约层的守卫"——地址空间重叠、环语义、常量漂移这些缺陷对纯数值模型天然不可见。本计划给 Func Model 补上这一层契约，并把 W4-PERF 这类"只在人工 F3 才跑"的套件接入自动门禁，让 RTL 改动导致的回归在提交后立即暴露。

**What it will NOT do:** 不修 RTL bug（含 BUG-RTL-SOC-007 的 RTL 根因）；不改 Arc Model；不加新 RTL 功能/新算子；不改 firmware 控制流（仅把手写常量换成生成的 ABI 头，且 8MB 回归窗口常量保留手写，见 todo 9）；不改 `quantize.py` / `ggml-npu/`；ISSUE-13A 的基础设施修复（bulk preload、ssh keepalive）已在 Phase 10 落地，不在本计划内。

**Effort:** Medium
**Risk:** Low — 唯一跨语言风险点是 firmware 常量源切换（todo 9），用 `make -C firmware` + Spike smoke 复验兜底；W4-PERF 门禁跑在 sz0001 上，不消耗本地资源。
**Decisions to sanity-check:** (1) 14 个 todo 分 3 波，全在 sim/ 与 firmware 常量层面；(2) FM-SOC runner 布局的差异化处置：P0/P1/P2P3 保留 `DESC_BASE=0x80001000`+32 条目环（per-runner 契约），P4 采用 verify-and-annotate（其 descriptor 为 block 相对地址 0x80048000，已天然无碰撞，只加守卫与注释，不迁移）；(3) `sim/device_server.py` 的 16 条目环（host-device 协议路径）明确排除在统一范围外；(4) 测试策略为 tests-after（断言以当前正确状态为准，不回溯验证旧值会失败）。

Your next move: approve, or run a high-accuracy review first. Full execution detail follows below.

---

> TL;DR (machine): Medium effort, low risk; 14 todos across 3 waves hardening Func Model verification against the 6 Phase 10 bug classes (layout/ring, scale-golden vacuity, packer divergence, constant drift, segment-boundary state, unwired regression gates).

## Scope
### Must have
- **C1 地址空间契约**：新建 `sim/address_space.py` 拥有 DRAM 区域表（命令环、完成环、descriptor 池、activation、weight、output）与区域重叠/8MB 窗口检查；`spike_host`/`rtl_soc_segment_run`/`rtl_soc_runner` 调度期断言 descriptor 区域与环区域不相交（todo 1/2）。
- **C2 命令环语义统一**：新建 `sim/command_ring.py` 作为环配置唯一事实源（RING_BASE/RING_ENTRIES=1024/CMD_ENTRY_SIZE=32/COMPLETION_RING_ADDR/DESC_STRIDE）；迁移 `spike_host.py:176` 的 `% 64` 轮询、`rtl_soc_segment_run.py:69`、`cocotb_bridge.py:2390`、`rtl_soc_runner.py` P4/P0；P0 的 32 条目环保留为显式 per-runner 配置并加命令数上限断言；环回绕 stress 与长序列 FM 场景（todo 3/4/5）。
- **C3 scale/accumulate golden 加固**：FM 层新增 SCALE_ADDR!=0 非平凡 FP32 scale 回归（对齐 `matmul_int4_per_block`）与 CTRL[2] accumulate 回归，补上 `test_soc_fm.py` 现有 SCALE_ADDR=0 镜像的盲区（todo 6/7）。
- **C4 双 packer 等价 + 跨语言常量契约**：`spike_host._pack_act_tile_major_contig` 与 `cocotb_bridge.pack_int8_activation_tile_major` 逐字节等价测试；ring/desc 常量改由 `spec/npu_abi.json` 单一来源，`firmware/npu_firmware.c` 引用生成的 ABI 头，Python 侧数值与 schema 比对（todo 8/9）。
- **C5 段边界协议门禁**：`segment_preload` 增加显式 `clear_sram` 边界契约（force_full 边界模式必须带全零 SRAM，否则断言失败）；双段 FM 场景回归 + `test_dram_bulk` 补 sram 路径（todo 10）。
- **C6 回归接线**：反向依赖自动门禁脚本（RTL/firmware/桥接文件变更 → 自动重跑 pytest + W4-PERF 6 批次 + scale/accumulate 回归）；FM 侧 attn_weight 覆盖场景；F 波门禁脚本；验证方法论文档同步（todo 11/12/13/14）。

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不修改任何 RTL 功能逻辑（`rtl/` 下零改动）；BUG-RTL-SOC-007 的 RTL 根因调试明确排除。
- 不改 firmware 控制流/调度语义；仅允许把 `firmware/npu_firmware.c` 手写常量改为引用 `gen/npu_abi_firmware.h`（已生成的 ABI 头，`firmware/npu-regmap.h:17` 已包含）。
- 不改 Arc Model（`sim/arc_model.py`、`sim/design_space_explorer.py` 冻结）、`sim/quantize.py`、`ggml-npu/`、`requirements.txt`。
- 不加新算子/新 MMIO 寄存器/新 RTL 特性；不在非 sz0001 机器跑 VCS。
- `sim/device_server.py:95` 的 `RING_SIZE=16`（host-device 协议路径）**明确排除**在环统一之外，只加注释说明（该路径与 Phase 10 段跑无关，改动无收益且引入回归风险）。
- 不删除任何现有探针/证据文件；契约断言默认开启（它们是门禁本身），违反时抛出带明确信息的异常。

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after + pytest 框架（`sim/tests/`）；新增断言按当前正确状态编写，QA 场景含 happy + failure（failure 用模拟注入验证门禁真的会抓）。
- Evidence: `build/evidence/task-<N>-fm-hardening-phase10.txt`（每 todo 一份：时间戳、commit、精确命令、PASS/FAIL、关键断言输出）。
- 全量回归基线：pytest ≥ 现有基线（task-3 基线：164 failed / 1901 passed / 45 errors 为已知遗留，新增 0 失败 0 错误）；FM-SOC 33/33 不因本计划改动而退化（todo 9 固件改动后用 Spike smoke + 至少 FM-SOC-001 复验）。
- 每波门禁：该波全部 acceptance 命令通过 + 无新增 pytest 失败。
- 三段式只用于涉及行为变化的 todo（todo 9 firmware、todo 3 迁移）：先快照行为（现有测试跑通）→ 改动 → 因果 gate（测试通过且 Spike smoke PASS）。

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- **Wave 1（地基，5 todos）**：todo 1 `sim/address_space.py`（区域表+重叠/窗口检查+单测）→ todo 2 调度期断言接线 + 修复 `scripts/p10_36layer_preflight.sh:471-472` 陈旧 DESC_BASE → todo 3 `sim/command_ring.py` 统一 + 全消费者迁移清单 → todo 4 ring-stress 回绕场景（140 命令跨 entry 128 + 回绕）→ todo 5 长序列持久偏移 FM 门禁（≥200 命令、9 层等效）。todo 1/3 无依赖可并行；2 依赖 1；4/5 依赖 2+3。
- **Wave 2（契约加固，5 todos）**：todo 6 scale golden 回归、todo 7 accumulate 回归、todo 8 双 packer 等价测试（三者无依赖，可并行）→ todo 9 ABI 常量单一来源（依赖 W1 的 1/3）+ firmware 头引用 + 重编译 + Spike smoke → todo 10 段边界 SRAM 清零契约（无依赖，可并行）。
- **Wave 3（接线与文档，4 todos）**：todo 11 反向依赖门禁脚本（依赖 3/6/7/9）、todo 12 FM attn_weight 覆盖（无依赖）二者并行；todo 13 F 波门禁脚本（依赖 1-12）；todo 14 文档同步（依赖 1-13）收尾。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 (address_space) | — | 2, 9 | 3 |
| 2 (调度期断言) | 1 | 4, 5 | 6, 7, 8, 10, 12 |
| 3 (command_ring 统一) | — | 4, 5, 9, 11 | 1 |
| 4 (ring-stress 场景) | 2, 3 | — | 5 |
| 5 (长序列 FM 门禁) | 2, 3 | — | 4 |
| 6 (scale golden) | — | 11 | 2, 7, 8, 10, 12 |
| 7 (accumulate golden) | — | 11 | 2, 6, 8, 10, 12 |
| 8 (packer 等价) | — | — | 2, 6, 7, 10, 12 |
| 9 (ABI 常量单一来源) | 1, 3 | 11 | 10 |
| 10 (段边界协议) | — | — | 2, 6, 7, 8, 9, 12 |
| 11 (反向依赖门禁) | 3, 6, 7, 9 | 13 | 12 |
| 12 (attn_weight 覆盖) | — | 13 | 2, 6, 7, 8, 10, 11 |
| 13 (F 波门禁脚本) | 1-12 | 14 | — |
| 14 (文档同步) | 1-13 | — | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

### Wave 1 — 地基（地址空间 + 环语义 + 场景）

- [x] 1. 新建 `sim/address_space.py`：DRAM 区域表 + 重叠/8MB 窗口检查 + 单元测试
  What to do / Must NOT do: 新模块 `sim/address_space.py` 拥有 DRAM 区域布局的 Python 事实源：REGIONS 字典包含命令环（base 0x80000000, size 1024*32）、完成环（0x80008000, 1024*32）、descriptor 池（0x80010000 起，stride 64）、activation 区（P10_ACT_BASE..P10_ACT_END = 0x80020000..0x801E0000）、weight 区（P10_WGT_BASE..P10_WGT_END = 0x801E0000..0x80800000）；提供 `regions_overlap(a, b)`、`addr_in_window(addr, size)`（8MB 窗口 [0x80000000, 0x80800000)）、`contract_check(ring_entries=1024, desc_base=None, desc_count=0, act_base=None)`——**参数化**：默认取 spike_host 常量，但允许调用方传入 per-runner 配置（供 todo 3 的 P0/P4 差异化布局使用）；断言两条：(a) `desc_base >= 完成环结束地址`（完成环结束 = RING_BASE + ring_entries*CMD_ENTRY_SIZE + ring_entries*32，即命令环与完成环各占 ring_entries 条目；对 1024 条目环即 0x80010000）；(b) `desc_base + desc_count*DESC_STRIDE <= act_base`（默认 P10_ACT_BASE=0x80020000）；违反时抛 `OverlapError`；所有区域落 8MB 窗口外抛 `WindowError`；`act_base=None` 表示跳过断言 (b)（只做重叠/窗口检查）——per-runner 布局（P0/P4）必须显式传 `act_base` 或改用 scoped 检查（见 todo 3）。常量值必须与 `sim/spike_host.py:44,66,67,347-352` 及 `spec/npu_abi.json:1435,1579-1582` 一致。Must NOT 在本 todo 修改 spike_host 常量或任何调用点（接线是 todo 2/3）；Must NOT 改 RTL。
  Parallelization: Wave 1 | Blocked by: — | Blocks: 2, 9
  References (executor has NO interview context - be exhaustive): `sim/spike_host.py:44`（FIRMWARE_RING_BASE）、`:66`（DESC_BASE=0x80010000）、`:67`（DESC_STRIDE=64）、`:347-352`（FP_DRAM_BASE/P10_ACT_BASE/P10_ACT_END/P10_WGT_BASE/P10_WGT_END）; `spec/npu_abi.json:19`（address_regions）、`:1435,1579-1582`（rings: ring_buffer_addr/ring_entries=1024/completion_ring_addr=0x80008000）; `firmware/npu_firmware.c:15-17,28-31`（DRAM_BASE/DRAM_SIZE=0x00800000/RING_ENTRIES=1024/CMD_DESC_SIZE=32/COMPLETION_RING_ADDR）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_address_space.py -v` exit 0，≥8 个测试，含命名用例 `test_desc_region_disjoint_from_ring`、`test_desc_in_window`、`test_region_table_matches_spike_host_constants`
  QA scenarios (name the exact tool + invocation): happy — `PYTHONPATH=sim python -c "from sim import address_space as a; a.contract_check(desc_base=0x80010000, desc_count=20); print('CONTRACT OK')"` 打印 CONTRACT OK；failure — `PYTHONPATH=sim python -c "from sim import address_space as a; a.contract_check(desc_base=0x80001000, desc_count=20)"` 抛 `OverlapError`（0x80001000 低于完成环结束 0x80010000）。Evidence `build/evidence/task-1-fm-hardening-phase10.txt`
  Commit: Y | feat(sim): add DRAM address-space contract module

- [x] 2. 调度期断言接线 + 修复 `scripts/p10_36layer_preflight.sh` 陈旧 DESC_BASE
  What to do / Must NOT do: `sim/spike_host.py` 的 `schedule_chain()`（:149）与 `write_cmd_entry()`（:139）接入 `address_space.contract_check(desc_base=DESC_BASE, desc_count=len(ops))`（默认参数即 spike_host 常量：断言 `DESC_BASE >= 0x80010000`（完成环结束地址）且 `DESC_BASE + desc_count*DESC_STRIDE <= P10_ACT_BASE`（0x80020000））；`sim/rtl_soc_segment_run.py` 启动路径调用一次 `contract_check()`。`scripts/p10_36layer_preflight.sh` 的陈旧常量改为从 `sim/spike_host.py` 导入/引用（`PYTHONPATH=sim python -c "from sim import spike_host as sh; print(hex(sh.DESC_BASE))"`），禁止硬编码。**该文件共 5 处陈旧硬编码，全部处置**（grep 验收覆盖）：`:471` 的 `DESC_BASE = 0x80001000` 与 `:473` 的 `FP_DRAM_BASE = 0x81000000`（现实际为 0x80020000，见 `spike_host.py:347`）改为导入；`:446` 注释、`:496` 的 CHECK 5c OUT 分支日志、`:696` 的 PRECONDITION 文本（"FP_DRAM_SIZE (currently 0x81000000, out of window)"）改为反映修复后 in-window 状态（FP_DRAM_BASE=0x80020000 已在窗口内，5c 的 OUT 分支为死代码，其日志/前置条件文本须更新或删除，不得保留误导性输出）。新增 `sim/tests/test_spike_host_overlap.py`。Must NOT 改动任何区域常量值本身；Must NOT 改环回绕算术（todo 3）；Must NOT 在 todo 2 中改 `sim/rtl_soc_runner.py`（其 per-runner 处置是 todo 3 明确项，避免两 todo 抢改同一文件）。
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 4, 5
  References: `sim/spike_host.py:139-170`（write_cmd_entry/schedule_chain/poll_completion）; `sim/rtl_soc_segment_run.py:59-61,131,495-497`; `sim/rtl_soc_runner.py:1095-1099`（P0 DESC_BASE/RING_SIZE）; `scripts/p10_36layer_preflight.sh:455-479`（CHECK 5、陈旧 DESC_BASE 行 :471-472）; `sim/address_space.py`（todo 1 产物）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_spike_host_overlap.py -v` exit 0；`grep -n "0x80001000\|0x81000000" scripts/p10_36layer_preflight.sh` 无输出；`bash -n scripts/p10_36layer_preflight.sh` exit 0
  QA scenarios: happy — `PYTHONPATH=sim python -c "from sim import spike_host as sh; sh.schedule_chain([]); print('SCHEDULE OK')"` 通过；failure — `PYTHONPATH=sim python -c "from sim import spike_host as sh; sh.DESC_BASE=0x80001000; sh.schedule_chain([0]*20)"` 抛 OverlapError（0x80001000 < 完成环结束 0x80010000，20 个 descriptor 也覆盖不了该前提）。Evidence `build/evidence/task-2-fm-hardening-phase10.txt`
  Commit: Y | fix(sim): wire ring-overlap assertions and fix stale preflight DESC_BASE

- [x] 3. 新建 `sim/command_ring.py` 环配置唯一事实源 + 全消费者迁移 + FM-SOC runner 布局差异化处置
  What to do / Must NOT do: 新模块 `sim/command_ring.py`：`RING_BASE=0x80000000, RING_ENTRIES=1024, CMD_ENTRY_SIZE=32, COMPLETION_RING_ADDR=0x80008000, DESC_STRIDE=64`，帮助函数 `ring_entry_addr(i)`、`advance_head(cur, n)`、`expected_head(total_cmds)`（全部以 RING_ENTRIES 取模）。迁移消费者（逐文件，grep 验收）：(a) `sim/spike_host.py` 中 **全部 8 处** `% 64` 期望头计算——行 :176, :256, :933, :1209, :1666, :1709, :1716, :1762——改为 `command_ring.expected_head()`；(b) `sim/rtl_soc_segment_run.py:69` `RING_SIZE` 改为导入；(c) `sim/cocotb_bridge.py:2390,2595` `SEGMENT_RING_SIZE` 改为导入。**FM-SOC runner 差异化处置（关键决策）**：(d1) `sim/rtl_soc_runner.py` P0（:1095 `DESC_BASE=0x80001000`）/P1（:1635 继承）/P2P3（:2003 继承）保留 `DESC_BASE=0x80001000` + `RING_SIZE=32`（:1099），**如实处置**：0x80001000 实际落在 1024 条目固件环区域内（entry 128-191，32 命令 × 64B stride = 2048B = 64 个条目），其安全性完全依赖"每次调度 ≤32 条命令、永不写到 entry 128+"这一不变量；完成环固定于 0x80008000（`firmware/npu_firmware.c:31`、`spec/npu_abi.json:1582`），与 32 命令下的完成写入区 [0x80008000, 0x80008400) 及 desc 区 [0x80001000, 0x80001800) 均不相交。守卫：(i) 新增调度期断言 `len(ops) <= RING_SIZE`（否则抛 `RingOverflowError`——防 `% 32` 别名误报完成）；(ii) 新增 scoped 检查 `assert_desc_clear_of_used_regions(ring_usage_end=RING_BASE+RING_SIZE*CMD_ENTRY_SIZE, completion_usage_end=COMPLETION_RING_ADDR+RING_SIZE*32)`（断言 desc 区与"本 runner 实际会写到的环/完成环区间"不相交，不使用通用 contract_check 的 1024 条目完成环语义）；(iii) 在 `DESC_BASE` 旁加注释引用 BUG-RTL-SOC-008，说明该布局仅在不变量 (i) 下安全；(d2) **P4 实际不使用继承的 `DESC_BASE`**：其 descriptor 是 block 相对地址（`rtl_soc_runner.py:2584` `_P4_DESC_BASE_REL=0x00038000`，`:2697` `desc_base = block_base + _P4_DESC_BASE_REL`，block 0 即 0x80048000，落在环区 0x80000000-0x80008000 与完成环 0x80008000-0x80010000 之外），FM-SOC-032 链为 28 block × 23 命令 = 644 命令（`:3046-3048` 注释 "all 28*23 commands fit in one ring"），单 1024 条目环容纳无回绕。处置为**verify-and-annotate（不迁移、不改布局）**：在 `_P4_DESC_BASE_REL` 旁加注释（引用 BUG-RTL-SOC-008，声明该布局已核验与环/完成环不相交），并在 P4 builder 启动处加一条显式守卫 `contract_check(ring_entries=1024, desc_base=block_base+_P4_DESC_BASE_REL, desc_count=23, act_base=0x80800000)`（**必须显式传 act_base=DRAM 窗口末端 0x80800000**：P4 desc 0x80048000 高于默认 P10_ACT_BASE=0x80020000，不传会误抛；上界用 8MB 窗口末端是 P4 的真实边界）；(e) `sim/device_server.py:95` `RING_SIZE=16` **明确排除**（host-device 协议路径），仅加注释指向 command_ring 并说明排除理由；(f) `sim/miniv.py:457` ring_size=16（已废弃模块）加注释指向 command_ring，不改行为。新增 `sim/tests/test_command_ring.py` 单测（回绕算术、expected_head、P0/P4 布局契约）。Must NOT 在本 todo 改 firmware C（todo 9）；Must NOT 改 device_server 行为；Must NOT 改 `spike_host.DESC_BASE`（0x80010000 已在正确位置）。
  Parallelization: Wave 1 | Blocked by: — | Blocks: 4, 5, 9, 11
  References: `sim/spike_host.py:176`; `sim/rtl_soc_segment_run.py:69`; `sim/cocotb_bridge.py:2390,2595`; `sim/rtl_soc_runner.py:1099`（P0 RING_SIZE=32）、`:1290`（P0 轮询）、`:2574`（P4 RING_SIZE=1024）、`:3334`（P4 轮询）; `sim/device_server.py:95,918,982`; `sim/miniv.py:457`; `firmware/npu_firmware.c:29,665-669`（真值参照）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_command_ring.py -v` exit 0（含命名用例 `test_p0_scoped_layout_guard`（32 命令上限 + desc 0x80001000 与 used-regions 不相交 + 33 命令抛 RingOverflowError）与 `test_p4_layout_contract`（1024 条目环 + desc base 0x80048000 + act_base=0x80800000 通过））；`grep -n "% 64" sim/spike_host.py` 无输出（8 处全迁移）；`grep -n "BUG-RTL-SOC-008" sim/rtl_soc_runner.py` 有输出（P0/P4 verify-and-annotate 注释落地）；`grep -n "contract_check\|assert_desc_clear_of_used_regions" sim/rtl_soc_runner.py | head` 有输出（P0/P4 守卫调用落地）；`PYTHONPATH=sim python -m pytest sim/tests/test_command_ring.py sim/tests/test_spike_host_overlap.py -q` 无失败（P0/P4 变更的实际回归面——注意 `test_soc_fm.py` 不 import rtl_soc_runner，FM-SOC 全量回归由 F3 在 sz0001 抽查 FM-SOC-001/003（P0）与 FM-SOC-032（P4））
  QA scenarios: happy — `PYTHONPATH=sim python -c "from sim import command_ring as cr; assert cr.expected_head(1300)==1300%1024; print('RING OK')"`；failure — P0 runner 调度 33 命令抛 `RingOverflowError`（新断言单测覆盖）。Evidence `build/evidence/task-3-fm-hardening-phase10.txt`
  Commit: Y | refactor(sim): unify command-ring config into sim/command_ring.py

- [x] 4. ring-stress 回绕场景（BUG-RTL-SOC-008 类在 FM 速度下的复现守卫）
  What to do / Must NOT do: 新增 `sim/tests/test_command_ring_stress.py`：纯 Python Func Model 场景——140 条命令、起始环偏移 120（跨 entry 128）、`expected_head` 从 1023 回绕到 0；descriptor 在 DESC_BASE 依次分配；断言 (a) `address_space.contract_check()` 通过且 descriptor 区与环不相交、(b) 每条命令完成记录 cmd_id 与状态正确、(c) 每条命令输出与 golden 匹配。命名测试 `test_ring_wrap_at_entry_128`。**环大小前置**：FuncModel 固件模拟默认 `ring_size=16`（`sim/miniv.py:457`，`sim/func_model.py:139` 直接读取），无法容纳 140 命令/偏移 120——本 todo 需把 `ring_size` 提为 `NPUFirmware` 构造参数（默认仍 16，行为不变），测试内以 `ring_size=1024` 构造（与 `command_ring.RING_ENTRIES` 一致），或等价地在测试 fixture 中覆盖。Must NOT 触碰 RTL/VCS；运行时间 < 30s。
  Parallelization: Wave 1 | Blocked by: 2, 3 | Blocks: —
  References: `sim/tests/test_soc_fm.py:1511-1580`（doorbell ring wrap/corrupt/overflow 现有用例模式）; `sim/func_model.py:131-149`（host_write_command ring-full 逻辑）; `sim/command_ring.py`、`sim/address_space.py`（todo 1/3 产物）; `.omo/notepads/phase10-rtl-verification/func-model-verification-gap-report.md:64-65`（S2 提案）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_command_ring_stress.py::test_ring_wrap_at_entry_128 -v` exit 0
  QA scenarios: happy — 140 命令链完成、完成环 cmd_id 全对、输出全匹配；failure — 测试内 `monkeypatch` DESC_BASE=0x80001000 时 `contract_check()` 抛 OverlapError 导致测试失败（证明门禁有效）。Evidence `build/evidence/task-4-fm-hardening-phase10.txt`
  Commit: Y | test(sim): ring-stress wrap scenario crossing entry 128

- [x] 5. 长序列持久偏移 FM 门禁（多段链在 Func Model 速度跑通）
  What to do / Must NOT do: 新增 `sim/tests/test_soc_fm_long_sequence.py::test_multi_layer_persistent_offset`：复用 28-block scaled chain fixture 模式（`test_soc_fm.py:2453-2764`），以**持久环偏移**（不按层重置）调度 ≥200 条命令（9 层等效），断言累计偏移正确回绕、每层输出与 golden 匹配、末层 cos ≥ 0.999。纯 Python，运行时间 < 2 分钟。Must NOT 用 VCS；Must NOT 改调度算法。
  Parallelization: Wave 1 | Blocked by: 2, 3 | Blocks: —
  References: `sim/tests/test_soc_fm.py:2453-2764`（28-block scaled chain 模式）; `sim/func_model.py:131-149`; `.omo/notepads/phase10-rtl-verification/func-model-verification-gap-report.md:63`（S1 提案）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_soc_fm_long_sequence.py::test_multi_layer_persistent_offset -v` exit 0；测试体内对末层 cos 做**数值断言**（`assert final_cos >= 0.999`，打印 `final_cos=...` 仅供人读，gate 不依赖字符串匹配）
  QA scenarios: happy — ≥200 命令持久偏移跑通、末层 cos≥0.999；failure — 测试内把某条链中段 descriptor 地址错写（模拟 ISSUE-13D 类错误）→ 对应层输出失配、断言失败。Evidence `build/evidence/task-5-fm-hardening-phase10.txt`
  Commit: Y | test(sim): long-sequence persistent-offset FM gate

### Wave 2 — 契约加固（scale/accumulate/packer/常量/段边界）

- [x] 6. scale 路径 golden 加固（SCALE_ADDR!=0 + 非平凡 FP32 scale 的 FM 回归）
  What to do / Must NOT do: 新增 `sim/tests/test_soc_fm.py::test_mmul_scale_nonzero`：经 FM doorbell/桥接路径下发 MMUL descriptor（scale_addr != 0，scale 区域写入非平凡 FP32 值如随机 [0.5,1.5]），断言输出与 `GoldenMXU.matmul_int4_per_block(group_size=128)` 在 fp32_tol（rtol=1e-5, atol=1e-5）内一致。**scale 缓冲必须按 FM 桥接读取器布局写 `[ceil(K/128)][N]` fp32**（`sim/mmio_bridge.py:249-252` 按 `num_blocks=(K+127)//128` 读 `num_blocks*N*4` 字节——固件 per-64 K-block 布局 `[n_tile][ceil(K/64)][64]` 与桥接读取器不同，本测试走 FM 路径必须匹配桥接布局，否则读数错位）；scale 值用非平凡随机 FP32，使 FP16-padding 坍缩类错误（F3 发现的 PERF 回归第一子因）必然失配。**K 用 256（M=1, N=128）**：桥接读取器 `num_blocks=ceil(256/128)=2`，覆盖多 scale-block 路径（K≤64 时 num_blocks=1 退化为单块，正是现有覆盖盲区）。现有覆盖注记：`test_soc_fm.py:2018-2030` 已有 SCALE_ADDR!=0 用例（scale 全 1），本测试补的是非平凡 scale 值盲区，不是新开路径。Must NOT 测 accumulate（todo 7）；Must NOT 改 RTL；Must NOT 在本测试用固件 per-64 布局（per-128 vs per-64 分歧的守卫归属 todo 9(e) 的显式分歧断言）。
  Parallelization: Wave 2 | Blocked by: — | Blocks: 11
  References: `sim/mmio_bridge.py:247-260`（raw_s>0 → matmul_int4_per_block 分支）、`:249-252`（桥接 scale 读取布局）; `sim/golden_executor.py:191-259`（matmul_int4_per_block）; `sim/tests/test_soc_fm.py:2018-2030`（现有 SCALE_ADDR!=0 用例，scale 全 1）; `sim/rtl_soc_runner.py:1479,1492-1509`（FM-SOC-003 scale descriptor 模式）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_soc_fm.py::test_mmul_scale_nonzero -v` exit 0
  QA scenarios: happy — 随机 scale 下输出与 golden ≤1 ulp；failure — scale 区域按 FP16 字节写（模拟 F3 发现的 FP16-in-FP32 格式错误，`perf_tests.py` 修复前形态）→ 输出坍缩、断言失败。Evidence `build/evidence/task-6-fm-hardening-phase10.txt`
  Commit: Y | test(sim): FM regression for scale-carrying MMUL path

- [x] 7. accumulate 路径 golden 加固（CTRL[2] 两命令链 FM 回归）
  What to do / Must NOT do: 新增 `sim/tests/test_soc_fm.py::test_mmul_accumulate`：同一输出地址两命令链，第二条 CTRL[2]=1，断言结果 == 第一段 partial + 第二段 fresh partial（`mmio_bridge.py:268-278` 语义），且与用 `matmul_int4_per_block` 分块组合的 golden 一致。Must NOT 与 todo 6 合并（scale 与 accumulate 正交，需独立归因）。
  Parallelization: Wave 2 | Blocked by: — | Blocks: 11
  References: `sim/mmio_bridge.py:139`（accumulate = CTRL bit2）、`:268-278`（accumulate 累加实现）; `firmware/npu_firmware.c:541`（accumulate_ctrl = (k_block>0)?4:0）; `sim/tests/test_soc_fm.py:2557-2835`（多块 scaled chain 模式）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_soc_fm.py::test_mmul_accumulate -v` exit 0
  QA scenarios: happy — 两命令 accumulate 结果与组合 golden 一致；failure — 测试内模拟忽略 CTRL[2]（monkeypatch accumulate=False）→ 结果仅剩第二段、断言失败。Evidence `build/evidence/task-7-fm-hardening-phase10.txt`
  Commit: Y | test(sim): FM regression for MMUL accumulate mode

- [x] 8. 双 packer 逐字节等价测试（ISSUE-13B activation 布局类守卫）
  What to do / Must NOT do: 新增 `sim/tests/test_packer_equivalence.py`：对 (M,K) 网格 {(1,64),(1,128),(64,128),(32,256),(1,2048),(64,2048)} 用确定性随机 INT8 激活，断言 `sim/spike_host._pack_act_tile_major_contig` 与 `sim/cocotb_bridge.pack_int8_activation_tile_major` 输出逐字节相等，并在测试 docstring 中记录两者必须同为列主序 broadcast 布局（ISSUE-13B 根因）。Must NOT 修改任一 packer 实现。
  Parallelization: Wave 2 | Blocked by: — | Blocks: —
  References: `sim/spike_host.py:586-601`（_pack_act_tile_major_contig）; `sim/cocotb_bridge.py:166-187`（pack_int8_activation_tile_major）; `.omo/notepads/phase10-rtl-verification/issues.md:124-134`（ISSUE-13B 布局根因）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_packer_equivalence.py -v` exit 0，6 个网格点全等
  QA scenarios: happy — 网格逐字节相等；failure — 测试内把 `_pack_act_tile_major_contig` 替换为行主序版本（复刻 pre-fix 形态）→ 不相等、断言失败。Evidence `build/evidence/task-8-fm-hardening-phase10.txt`
  Commit: Y | test(sim): packer equivalence guard between spike_host and cocotb_bridge

- [x] 9. ABI 常量单一来源：schema → 生成头 → firmware 引用 + 跨语言数值比对
  What to do / Must NOT do: (a) 检查并补充 `spec/npu_abi.json` rings/address_regions 段，确保含 DRAM_BASE/RING_ENTRIES/CMD_DESC_SIZE/COMPLETION_RING_ADDR/TILE_SCALE_BYTES 全部数值（现已有 :1579-1582 等，缺则补）；(b) `python3 scripts/gen_npu_abi.py` 重新生成 `gen/npu_abi_firmware.h`；(c) 最小 C 改动：`firmware/npu_firmware.c` 中 `RING_ENTRIES/CMD_DESC_SIZE/COMPLETION_RING_ADDR`（:29-31）及 `TILE_SCALE_BYTES`（:490，值 256 与 schema 一致）改为引用生成头宏（`firmware/npu-regmap.h:17` 已 include 生成头）；**`DRAM_SIZE=0x00800000`（:16，8MB RTL 回归窗口）必须保留手写并加注释**——生成头的 `NPU_ABI_DRAM_SIZE=0x80000000`（2GB）是芯片级窗口，与 BUG-RTL-SOC-002 的 8MB 回归约束（`dram_range_ok` :455-460）语义不同，不得替换；`DRAM_BASE`（:15）可引用生成头（schema 值 0x80000000 与现有一致）；**不改任何控制流/调度语义**；(d) 新增 `sim/tests/test_npu_abi_constants.py`：解析 `spec/npu_abi.json` 并与 `sim/address_space.py`/`sim/command_ring.py` 数值比对；(e) `sim/tile_scheduler.py:15` 的 TILE_W=128（:17 处 TILE_SCALE_BYTES=512B，per-128 分组）加注释声明与 firmware per-64（256B）的**有意分歧**，并加一个断言该分歧常量的测试（使未来变更必须显式）。Must NOT 用 pytest 正则解析 C 常量（用 schema 机制替代）；Must NOT 改 firmware 控制流。
  Parallelization: Wave 2 | Blocked by: 1, 3 | Blocks: 11
  References: `spec/npu_abi.json:19,1435,1579-1582`; `scripts/gen_npu_abi.py`（生成器入口）; `firmware/npu-regmap.h:2-17`（include 生成头）; `firmware/Makefile:34`（ABI_HEADER=../gen/npu_abi_firmware.h）; `firmware/npu_firmware.c:15-17,28-31,487-490`; `sim/tile_scheduler.py:15-17`; `scripts/contract_check.py:640-673`（既有 schema 校验，可复用）
  Acceptance criteria (agent-executable): `python3 scripts/gen_npu_abi.py --check` exit 0（或等价校验命令）；`make -C firmware` exit 0；`PYTHONPATH=sim python3 sim/spike_host.py --mode mmul_smoke --model $HOME/models/qwen2.5-1.5b-instruct-q4_k_m.gguf --layers 1 --ops Q_proj` exit 0 且输出含 `Spike Host Summary: 1 PASS, 0 FAIL`（spike_host.py:1899 的实际成功输出格式）；`PYTHONPATH=sim python -m pytest sim/tests/test_npu_abi_constants.py -v` exit 0
  QA scenarios: happy — 上述 4 命令全过；failure — 手改 `firmware/npu_firmware.c` 某常量回错误值后 `make -C firmware` 或 ABI 断言/数值比对测试失败（按所选机制）。Evidence `build/evidence/task-9-fm-hardening-phase10.txt`
  Commit: Y | refactor(firmware): source ring constants from generated ABI header

- [x] 10. 段边界 SRAM 清零契约（ISSUE-13C 类守卫）
  What to do / Must NOT do: `sim/cocotb_bridge.py` `segment_preload()`（:2488-2543）新增 `clear_sram: bool = False` 参数：当 `force_full=True and clear_sram=True` 时断言 `sram == b"\x00" * SRAM_SIZE`（否则抛 `SegmentBoundaryError`）；`sim/rtl_soc_segment_run.py:495-497` 调用点传 `clear_sram=True`。新增 `sim/tests/test_segment_boundary.py::test_two_segment_sram_clear`：FuncModel 层两段连续场景（段间边界清零语义断言）+ `sim/test_dram_bulk.py` 补 sram 传参变体覆盖写路径。Must NOT 改 RTL；Must NOT 强制单段调用方（probe 类）传 sram（它们 clear_sram=False）。
  Parallelization: Wave 2 | Blocked by: — | Blocks: —
  References: `sim/cocotb_bridge.py:2488-2543`（segment_preload、SRAM 仅在 sram 非空时写）; `sim/rtl_soc_segment_run.py:495-497`（6091ec9 的 sram 清零调用点）; `sim/test_dram_bulk.py:33,48`（现有无 sram 调用）; `.omo/notepads/phase10-rtl-verification/issues.md:222-247`（ISSUE-13C 根因）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_segment_boundary.py::test_two_segment_sram_clear -v` exit 0；`PYTHONPATH=sim python -m pytest sim/test_dram_bulk.py -v` exit 0（既有 + 新变体）
  QA scenarios: happy — 双段场景通过、dram_bulk 全过；failure — `segment_preload(force_full=True, clear_sram=True, sram=b"")` 抛 `SegmentBoundaryError`（单测覆盖）。Evidence `build/evidence/task-10-fm-hardening-phase10.txt`
  Commit: Y | feat(sim): segment-boundary SRAM-clear contract + regression

### Wave 3 — 接线与文档

- [ ] 11. 反向依赖自动门禁脚本（RTL/firmware 变更 → 自动重跑 pytest + W4-PERF + scale 回归）
  What to do / Must NOT do: 新建 `scripts/fm_reverse_dependency_gate.sh`：(a) 状态文件 `.omo/last_fm_gate.json` 记录上次门禁通过时的 git HEAD + 每个敏感文件的内容哈希（`git hash-object`）；(b) 敏感文件清单（**覆盖 Phase 10 实际变更过的全部 RTL 面**）：`rtl/mxu/*.v`、`rtl/soc/*.v`、`rtl/sfu/*.v`、`rtl/vector/*.v`、`rtl/wrapper/*.v`（含 mxu/sfu/vector_soc_wrapper，95ef1c8 的 vector_soc_wrapper 变更必须在列）、`rtl/ip/*.v`、`firmware/npu_firmware.c`、`firmware/npu-regmap.h`、`gen/npu_abi_firmware.h`、`sim/golden_executor.py`、`sim/mmio_bridge.py`、`sim/perf_tests.py`、`sim/cocotb_bridge.py`、`sim/tile_scheduler.py`、`sim/func_model.py`；(c) 有变更时执行：`PYTHONPATH=sim python -m pytest sim/tests/ -q`（新增 0 失败）+ sz0001 上 W4-PERF 6 批次（复用 `sim/regression/run_w4_perf_batch.sh`，经 `p10_ssh`）+ todo 6/7 用例；(d) 全部通过则写状态文件 exit 0，任一失败 exit 1；`--dry-run` 只打印将执行项（干净时 exit 0，有 diff 时 exit 1）。Must NOT 重复实现批次逻辑；Must NOT 替代 F1-F4 最终波。
  Parallelization: Wave 3 | Blocked by: 3, 6, 7, 9 | Blocks: 13
  References: `sim/regression/run_w4_perf_batch.sh`（6 批次现成入口）; `scripts/p10_f3_manual_qa.sh:199-254`（Phase 3 批次调用模式）; `scripts/p10_lib/p10_sz0001.sh`（p10_ssh）; `sim/perf_tests.py:222-224`（PERF-06 位置）; `.omo/notepads/phase10-rtl-verification/issues.md:559-588`（F3 发现 PERF 回归的教训）、`:678-706`（Phase 10 实际变更文件清单，敏感清单以此为准）
  Acceptance criteria (agent-executable): `./scripts/fm_reverse_dependency_gate.sh --dry-run` 在干净状态下 exit 0；**对敏感文件做一次真实内容变更**（`echo "// gate-trigger-test" >> rtl/mxu/controller.v`，随后 `git checkout -- rtl/mxu/controller.v` 恢复）后 `--dry-run` exit 1 且列出将执行的批次；完整运行（含 sz0001 批次）exit 0 且 `.omo/last_fm_gate.json` 更新
  QA scenarios: happy — 干净状态 dry-run exit 0；failure — 模拟 PERF 失败（临时改 `sim/perf_tests.py` 期望值后运行门禁）→ exit 1 且输出指明失败批次，随后恢复。Evidence `build/evidence/task-11-fm-hardening-phase10.txt`
  Commit: Y | feat(scripts): reverse-dependency regression gate for RTL/firmware changes

- [ ] 12. FM attn_weight 覆盖场景（BUG-RTL-SOC-007 的 FM 侧结构缺口）
  What to do / Must NOT do: 新增 `sim/tests/test_soc_fm.py::test_mmul_attn_weight_shape`：经 FM doorbell/桥接路径下发 attn_weight 形状 MMUL（M=32,K=32,N=64，对齐 `sim/perf_tests.py:255` PERF-13），断言命令实际执行（completion 状态 = 成功）且输出与 golden 匹配——补上 spike_host forward 路径从不发射 attn_weight op 导致的结构性盲区。Must NOT 追 RTL 根因（BUG-RTL-SOC-007 的 RTL 侧排除）。
  Parallelization: Wave 3 | Blocked by: — | Blocks: 13
  References: `sim/perf_tests.py:255`（PERF-13 attn_weight 参数）; `sim/spike_host.py:402-414`（_forward_attention host-side，说明为何 spike 路径无 attn_weight）; `docs/bugs/bugs-soc-rtl.md:324-359`（BUG-RTL-SOC-007，仅作背景）; `scripts/p10_36layer_preflight.sh:361`（attn_weight 派发检查）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_soc_fm.py::test_mmul_attn_weight_shape -v` exit 0
  QA scenarios: happy — 执行且与 golden 匹配；failure — 测试内让该命令不执行（completion 不写）→ 断言失败。Evidence `build/evidence/task-12-fm-hardening-phase10.txt`
  Commit: Y | test(sim): FM attn_weight op coverage

- [ ] 13. 创作 F 波门禁脚本（fm_hardening_f1..f4）并 dry-run 验证
  What to do / Must NOT do: 新建四个脚本（每个 ≤100 行，语义与下方 Final verification wave 的 What/Command 一致，不得自证——脚本只执行客观检查）：`scripts/fm_hardening_f1_audit.sh`（检查 `build/evidence/task-{1..14}-fm-hardening-phase10.txt` 存在且终态 PASS；重跑各 todo 的 pytest acceptance 命令并比对退出码）；`scripts/fm_hardening_f2_code_quality.sh`（grep TODO/FIXME/HACK 残留于新增文件；`bash -n` 所有改动 .sh；pytest 全量与 task-3 基线 diff failed=0/errors=0）；`scripts/fm_hardening_f3_manual_qa.sh`（全量 pytest + `make -C firmware` + Spike smoke + 反向依赖门禁 dry-run + sz0001 上 W4-PERF p0/p1 抽查 + **FM-SOC 001/003（P0）与 FM-SOC-032（P4）抽查**，即 F3 的 (a)-(e) 全部五项）；`scripts/fm_hardening_f4_scope_gate.sh`（git diff --name-only 白名单核对 + 冻结文件 diff 为空断言）。每个脚本完成后做一次 dry-run/语法验证。Must NOT 让任何脚本无条件 exit 0；Must NOT 在脚本中重复实现批次逻辑（复用现有入口）。
  Parallelization: Wave 3 | Blocked by: 1-12 | Blocks: 14
  References: `scripts/p10_f1_audit.sh`、`scripts/p10_f2_code_quality.sh`、`scripts/p10_f3_manual_qa.sh`、`scripts/p10_f4_scope_gate.sh`（Phase 10 既有 F 波脚本，模式参照）; `sim/regression/run_w4_perf_batch.sh`; `scripts/p10_lib/p10_sz0001.sh`
  Acceptance criteria (agent-executable): `bash -n scripts/fm_hardening_f{1,2,3,4}_*.sh` 全过；`bash scripts/fm_hardening_f1_audit.sh --help` 或直接运行能正确报告当前 14 个证据文件的缺失/存在状态（非无条件 exit 0）；`ls scripts/fm_hardening_f*.sh` 共 4 个文件
  QA scenarios: happy — 4 脚本存在、语法通过、F1 脚本如实报告当前证据状态；failure — 删除任一脚本后 `bash -n` 失败/文件缺失检查失败。Evidence `build/evidence/task-13-fm-hardening-phase10.txt`
  Commit: Y | feat(scripts): add FM hardening F-wave gate scripts

- [ ] 14. 验证方法论文档同步 + gap report 状态回填 + 新 notepad
  What to do / Must NOT do: (a) `docs/verification_methodology.md` FM 验证节新增小节：内存布局契约（address_space/command_ring）、scale/accumulate golden 要求、段边界协议、RTL/固件变更后反向依赖门禁（含 `scripts/fm_reverse_dependency_gate.sh` 用法）；(b) `.omo/notepads/phase10-rtl-verification/func-model-verification-gap-report.md` §5-6 提案**如实**回填：M1→todo 3、M2→todo 1、S1→todo 5、S2→todo 4、A1→todo 2、A2→todo 2、AL2→todo 9 标注"已落地为 fm-hardening-phase10 计划 todo N"；**T1/T2（firmware_memory_contract.json 双向比对）与 AL1（forward 路径 per-layer 环重置 vs 段跑累计偏移的对齐）明确标注"deferred，未纳入本计划"**，不得伪称已实现；(c) 新建 `.omo/notepads/fm-verification-hardening/learnings.md` 记录关键决策（6 类 bug→守卫映射表、FM-SOC runner 差异化布局决策、device_server 排除理由、tests-after 选择）。Must NOT 改其他文档；Must NOT 伪造"已实现"状态。
  Parallelization: Wave 3 | Blocked by: 1-13 | Blocks: —
  References: `docs/verification_methodology.md:78-106`（FM 验证节）; `.omo/notepads/phase10-rtl-verification/func-model-verification-gap-report.md:53-95`（§5-6 提案清单）
  Acceptance criteria (agent-executable): `git diff docs/verification_methodology.md | grep -c "反向依赖\|reverse-dependency"` ≥1；`ls .omo/notepads/fm-verification-hardening/learnings.md` 存在；gap report 中 M1/M2/S1/S2/A1/A2/AL2 条目旁出现 `fm-hardening-phase10` 字样、T1/T2/AL1 条目旁出现 `deferred` 字样（grep 验证）
  QA scenarios: happy — 三处文档检查全过；failure — 删除新增小节后 grep 失败。Evidence `build/evidence/task-14-fm-hardening-phase10.txt`
  Commit: Y | docs(verify): update FM verification methodology with Phase 10 hardening contracts

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
  What: 逐条核对 14 个 todo 的 evidence 文件存在、acceptance 命令可复跑通过、依赖矩阵一致、无跳过/缩水。
  Command: `bash scripts/fm_hardening_f1_audit.sh`（由 todo 13 创作；检查 `build/evidence/task-{1..14}-fm-hardening-phase10.txt` 存在且终态 PASS；重跑每个 todo 的 pytest acceptance 命令并比对退出码）
  Pass: 全部 evidence 存在、14/14 acceptance 复跑通过。

- [ ] F2. Code quality review
  What: 审查新增/修改的 Python 与 shell 代码：无 TODO/FIXME/HACK 残留、无硬编码魔数（区域常量只允许出现在 `sim/address_space.py`/`sim/command_ring.py`/schema）、无新增 pytest 失败、`bash -n` 通过。
  Command: `bash scripts/fm_hardening_f2_code_quality.sh`（由 todo 13 创作；grep 残留；`bash -n` 所有改动的 .sh；`PYTHONPATH=sim python -m pytest sim/tests/ -q` 与 task-3 基线 diff：failed=0, errors=0）
  Pass: 0 残留、0 新增失败、所有 shell 语法通过。

- [ ] F3. Real manual QA
  What: 独立复跑关键链路：(a) 全量 pytest（新增用例全过）；(b) `make -C firmware` + Spike smoke（todo 9 固件改动复验）；(c) `./scripts/fm_reverse_dependency_gate.sh` 干净状态 `--dry-run` exit 0；(d) 在 sz0001 上实际跑一次 W4-PERF 批次抽查（至少 p0/p1 两批）确认门禁真实可用；(e) FM-SOC 抽查 FM-SOC-001/003（P0 runner）与 **FM-SOC-032（P4 runner，验证 todo 3 的 P4 verify-and-annotate 守卫未破坏 P4 布局）**。
  Command: `bash scripts/fm_hardening_f3_manual_qa.sh`（由 todo 13 创作；逐项执行并落证据，覆盖 (a)-(e) 五项）
  Pass: 五项全过。

- [ ] F4. Scope fidelity
  What: 确认无 scope creep：`git diff` 只含 sim/ 新增与接线、firmware 常量引用、scripts/、docs/、spec/npu_abi.json；`rtl/` 零改动；`sim/arc_model.py`、`sim/design_space_explorer.py`、`requirements.txt`、`sim/quantize.py`、`ggml-npu/` 零改动。
  Command: `bash scripts/fm_hardening_f4_scope_gate.sh`（由 todo 13 创作；git diff --name-only 白名单核对 + 冻结文件 diff 为空断言）
  Pass: 无越界文件、冻结文件零改动。

## Commit strategy
- 每个 todo 一个原子 commit（类型按 todo 的 Commit 行）；证据文件随 todo 一并提交到 `build/evidence/`。
- 本计划不产生 RTL 逻辑改动，无需 feature branch；todo 9 的 firmware 常量改动为单一原子 commit，commit message 注明 Spike smoke 证据路径。
- Wave 3 结束后总结合并提交：`docs(fm-hardening): closure note for fm-hardening-phase10`。

## Success criteria
- C1: `sim/address_space.py` + 调度期断言就位；`DESC_BASE=0x80001000` 注入会使 `schedule_chain` 抛 OverlapError（BUG-RTL-SOC-008 类在 FM 秒级暴露）。证据 task-1/2。
- C2: 环配置单一事实源（`sim/command_ring.py`）；`spike_host` 无 `% 64` 残留；ring-stress（140 命令跨 entry 128 + 回绕）与长序列（≥200 命令）FM 场景通过。证据 task-3/4/5。
- C3: scale（SCALE_ADDR!=0、FP32 非平凡 scale）与 accumulate（CTRL[2]）FM 回归通过，均与 `matmul_int4_per_block` golden 对齐。证据 task-6/7。
- C4: 双 packer 逐字节等价测试通过；ring/desc 常量由 `spec/npu_abi.json` 单一来源，`npu_firmware.c` 引用生成头，Python 数值比对测试通过，`make -C firmware` + Spike smoke PASS。证据 task-8/9。
- C5: 段边界 `clear_sram` 契约断言就位（空 sram 在边界模式抛错），双段场景回归通过。证据 task-10。
- C6: `scripts/fm_reverse_dependency_gate.sh` 可用（dry-run 语义正确、状态文件持久化）；FM attn_weight 覆盖通过；F 波门禁脚本 4 件就位；验证方法论文档、gap report 回填（含 T1/T2/AL1 的 deferred 标注）、新 notepad 就位。证据 task-11/12/13/14。
- 全量 pytest 无新增失败（相对 task-3 基线）；FM-SOC 33/33 不退化（todo 3/9 迁移后抽查 FM-SOC-001/003 或全量）。
- F1-F4 全 APPROVE。
