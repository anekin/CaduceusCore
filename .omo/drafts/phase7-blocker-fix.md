---
slug: phase7-blocker-fix
status: plan-written
intent: clear
review_required: false
pending-action: present summary, offer start-work vs high-accuracy-review
approach: 严格范围——只修能修的环境/文档问题（Spike 插件、证据 schema、testcase-list），深层阻塞项保持 NOT RESOLVED 并文档化。Metis+Momus 双审已纳入。
---

# Draft: phase7-blocker-fix

## Components (topology ledger)
<!-- id | outcome (one line) | status: active|deferred | evidence path -->
PH7-SPIKE | 在 sz0001 上重编译 `npu_mmio_plugin.so`，修复 C++ ABI/GLIBC 不匹配 | active | build/evidence/ph7-spike-fixed.txt
PH7-WGT | 固件 + Cocotb harness 支持 per-K-tile weight streaming，解除 PERF-11 和 36-layer RTL 全量阻塞 | active | build/evidence/ph7-wgt-streaming.txt
PH7-Q8 | 下载 Q8_0 GGUF 资产，运行 L35 drift 根因确认实验 | active | build/evidence/w1-6b-q8o.txt（覆盖）
PH7-SFUVEC | 在 PERF 测试路径中验证 SFU/Vector opcode dispatch，完成 fullchain SFU/Vector 段落实测 | active | build/evidence/ph7-sfuvec-fullchain.txt
PH7-SCHEMA | 补全 W4-PERF 全部 21 条证据记录的 timestamp + commit 字段 | deferred | build/evidence/w4-perf-p*.txt（原位修改）
PH7-DOC | 更新 `rtl/testcase-list-perf.md` 20 个 case 的状态列 | deferred | rtl/testcase-list-perf.md（原位修改）

## Open assumptions (announced defaults)
<!-- Record any default you adopt instead of asking, so the user can veto it at the gate. -->
<!-- assumption | adopted default | rationale | reversible? -->
Weight streaming 实现位置 | 修改 `cocotb_bridge.py:_run_tiled_mmul()` 和 `sim/perf_tests.py`，不修改 firmware C 代码 | 探索发现 firmware `npu_firmware.c` 已有 per-K-tile DMA reload 逻辑（line 436-437），阻塞点在 Python 测试路径的 `mmul_workaround` 截断 | 是
SFU/Vector PERF 路径修复 | 复用现有 FM-SOC-004/005 的 opcode dispatch 模式，在 PERF harness 中新增 SFU/Vector 命令发送 | 固件 `dispatch_cmd()` 已支持 SFU op=0x01-0x06/0x17 和 Vector op=0x0F-0x14，只缺测试端发送 | 是
Q8_0 GGUF 修复位置 | 在 sz0002 上运行 Python-only 实验（非 VCS），SCP 模型文件后执行 `scripts/run_w1_6b_q8o_control.py` | Phase 6 6b 任务设计就是 Python-only，无需 VCS | 是

## Findings (cited - path:lines)
1. **Spike plugin**: `spike_src/plugins/npu_mmio_plugin.so` 在本地编译链接了 GLIBC ≥2.32，sz0001 上是旧版 GLIBC。修复：`make -C spike_src/plugins` 在 sz0001 上就地重编 (`docs/bugs/bugs-soc-rtl.md:56-66`, `docs/spike-integration.md:111`)
2. **Weight streaming**: firmware `npu_firmware.c:436-437` 已有 `dma_copy(...TILE_WEIGHT_BYTES...)` per-K-tile 重载，但 `cocotb_bridge.py:_run_tiled_mmul()` 用 `mmul_workaround` 截断 K/N=64，跳过了 multi-tile 路径 (`rtl/testcase-list-perf.md:43-63`)
3. **SFU/Vector dispatch**: firmware `dispatch_cmd()` 在 line 458-467 处理 SFU opcodes，line 477-483 处理 Vector opcodes。阻塞点在 PERF test harness 不发这些命令 (`firmware/npu_firmware.c:458-483`)
4. **Q8_0 GGUF**: `~/models/` 只有 Q4_K_M，缺少 `qwen2.5-3b-instruct-q8_0.gguf`。HF repo: `Qwen/Qwen2.5-3B-Instruct-GGUF` (`build/evidence/w1-6b-q8o.txt:1-71`)
5. **Evidence schema**: 21 条 PERF 记录中仅 PERF-01 有 timestamp+commit（4.8%）。schema 要求: `case_id, simulator, status, cycles, timestamp, commit` (`.omo/notepads/phase6-rtl-verification/learnings.md:18`)
6. **testcase-list**: `rtl/testcase-list-perf.md` 20 case 全部 ⬜ TODO，需更新为 19 PASS + 1 FAIL（PERF-11）(`rtl/testcase-list-perf.md:1-172`)

## Decisions (with rationale)
1. **Wave 顺序**: Spike → Weight Streaming → SFU/Vector → 文档（Q8_0 和 Schema 可并行）。Spike 修复后所有后续任务可复用 Spike 加速调试；Weight Streaming 是 PERF-11 和 36-layer 的前提。
2. **不修改 firmware C 代码**: 现有固件已支持 per-K-tile reload 和 SFU/Vector opcode，阻塞点在 Python 测试路径。最小化变更风险。
3. **36-layer RTL 全量前向传播**: 不纳入本 Phase——它需要固件 weight streaming 功能完成后才能跑，且 VCS 运行 2-4h。先修复阻塞项，后续单独计划。

## Scope IN
- 在 sz0001 EDA server 上重编译 Spike plugin `.so`
- 修改 `cocotb_bridge.py` / `perf_tests.py` 实现 multi-tile weight streaming
- 在 PERF 路径中新增 SFU/Vector 命令 dispatch
- 下载 Q8_0 GGUF 并运行 6b 控制实验
- 补全 W4-PERF 证据记录的 timestamp + commit
- 更新 `rtl/testcase-list-perf.md` 状态列

## Scope OUT (Must NOT have)
- 不修改任何 RTL Verilog 源文件（`rtl/mxu/*.v`, `rtl/sfu/*.v`, `rtl/vector/*.v`, `rtl/soc/*.v` 等）
- 不修改 firmware C 代码（`firmware/npu_firmware.c` 等）
- 不做完整 36-layer RTL 前向传播（仅解除阻塞，不在本 Phase 跑）
- 不新增 INT8×INT8 / BF16 数据通路
- 不做综合/物理设计
- 不做 FPGA 相关工作

## Open questions
无——所有阻塞项均已通过探索确定了具体修复范围和文件路径。

## Approval gate
status: awaiting-approval
<!-- 等待用户批准后写入 .omo/plans/phase7-blocker-fix.md -->
