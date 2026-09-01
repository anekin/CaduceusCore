# CaduceusCore 项目执行审查报告

- 审查日期：2026-08-28
- 被审查项目：`/home/prj/zhengs/caduceuscore/CaduceusCore`
- 审查方式：只读审查
- 审查范围：当前分支状态、SoC RTL signoff 计划与执行证据、相关回归脚本、测试平台、固件边界、项目级 signoff 状态
- 审查约束：未在被审查项目目录内创建、修改或删除任何文件；未运行会生成缓存、日志、波形或构建产物的测试

## 1. 执行摘要

### 1.1 总体结论

**审查不通过，不建议批准当前完整 SoC RTL signoff 或项目级 signoff。**

项目已经取得大量可确认的功能进展，部分 SoC RTL 场景具有较强的真实 VCS 运行证据。但现有 signoff 同时存在无效测试、回归统计假阳性、失败状态被转换为成功、证据来源不完整、未签署 waiver 被提前关闭、项目级 blocker 尚未解除等问题。

当前最准确的状态表述是：

> 限定范围内的部分 SoC RTL 功能回归已经跑通；完整 SoC RTL signoff 和 CaduceusCore 项目级 signoff 尚未完成。

### 1.2 五路审查结果

| 审查方向 | 结论 | 核心原因 |
|---|---|---|
| 目标与约束符合性 | FAIL | 两项关键 RTL gap test 未覆盖目标行为，waiver 与开放问题未闭环 |
| 运行证据 | FAIL | “33/33 PASS”包含未执行/N/A case，部分证据缺少可信 provenance |
| 代码质量 | FAIL | timeout、退出码、Make pipeline 和 evidence gate 存在可重复假阳性路径 |
| 安全与证据完整性 | FAIL | timeout 可绕过、checkpoint 可被不受信 NPZ 恢复、固件地址检查过宽 |
| 历史与项目上下文 | FAIL | 性能、FPGA、ggml、全连续 36 层和用户签署仍未完成 |

## 2. 项目与版本状态

审查时的工作分支为 `fix/fm-soc-10x-sfu-desc`，HEAD 为 `e678f302`。该分支与同名远端分支同步；相关实现已合入 `main`。当前分支比 `origin/main` 少 5 个以 daily-sync 文档和日志为主的提交，不影响本轮核心 SoC RTL 代码判断。

工作区不是干净交付状态：存在 13 个已跟踪修改和多项未跟踪文件/目录，包括 performance evidence、Spike FAIL 日志、firmware ELF/O/MAP、未跟踪 feature-status CSV、performance calibration 计划以及 Ibex 构建产物。

这意味着当前目录同时混合了已提交 signoff 记录、后续重跑结果和未提交运行产物，不能直接作为可复现的 release/signoff snapshot。

## 3. 关键阻塞问题

### 3.1 Critical：Crossbar fairness 测试未验证并发仲裁

文件：`/home/prj/zhengs/caduceuscore/CaduceusCore/rtl/tb/axi_crossbar_fairness_tb.sv:10`

测试平台明确禁止两个 master 同时竞争，并按 `0→1→...→6` 的固定顺序逐笔发送请求。最终 grant 计数均衡和严格交替主要由 stimulus 顺序保证，即使把 crossbar 换成固定优先级仲裁器，该测试也可能继续通过。

源码注释还记录：如果多个 master 在同一 slave-free cycle 同时断言 VALID，可能出现多个请求被 accept、只有一个获得 grant、其余请求永久等待的 phantom-accept deadlock。当前测试通过主动规避竞争隐藏了该问题。

影响：

- `FAIRNESS: PASS` 不能证明真实竞争下的 round-robin 公平性。
- SoC interconnect 不能据此宣称 100% closure。
- 并发 master 场景可能存在永久死锁风险。

必须补充：修复 accept/grant 协议后，让 6/7 个 master 同周期持续竞争同一个 slave，验证无死锁、所有事务完成、grant 差不超过 1，并加入固定优先级 mutation 必须失败的反证测试。

### 3.2 Critical：APB conformance 没有连接真实外设 RTL

文件：`/home/prj/zhengs/caduceuscore/CaduceusCore/rtl/tb/apb_register_conformance_tb.sv:124`

该 testbench 只实例化了真实 `apb_decoder`。MXU、SFU、Vector、DMA、PCIe、Doorbell、INTC 七个下游全部是测试代码自建的 `apb_conformance_slave` 寄存器模型，其行为来自同一份期望表。

因此当前 168/168 PASS 只能证明：

- decoder 能把地址路由到测试模型；
- 测试模型符合构造它的同一张期望表。

它不能证明七个真实外设的 reset、RW、RO、WO、W1C 和 hostile-write 行为符合 regmap。

必须补充：连接真实七个外设或完整 SoC top，以独立 regmap oracle 检查寄存器语义；否则应把当前目标降级为 decoder routing test，不能称为 seven-peripheral conformance。

### 3.3 Critical：24 小时 timeout 会把未完成运行转换成成功

文件：`/home/prj/zhengs/caduceuscore/CaduceusCore/sim/regression/run_ibex_segment_run.sh:59`

主要问题：

- GNU `timeout` 返回 124 时，脚本无条件 `exit 0`。
- checkpoint 仍为 PENDING、evidence 不存在或 evidence 含旧 PASS 时，也可能被上层视为成功。
- `SEG_TIMEOUT_S` 未做正整数/合法 duration 校验，类似 `--help` 的值可能让 GNU timeout 自身返回 0，而仿真完全未启动。
- TERM 被忽略、`--kill-after` 最终发送 KILL 时可能返回 137，当前脚本又不会写 timeout evidence。
- timeout 只向固定路径的既存 evidence 追加文本，没有验证 commit、run ID、新鲜度或本轮完成情况。

必须补充：timeout 必须返回与 PASS 可区分的非成功状态；严格解析 timeout 参数；覆盖正常成功、测试失败、TERM timeout、KILL escalation、无 evidence、旧 PASS evidence、PENDING evidence 等负面场景。

### 3.4 Critical：全量回归“33/33 PASS”存在确定性统计误报

文件：`/home/prj/zhengs/caduceuscore/CaduceusCore/sim/regression/run_ibex_full_rtl.sh:85`

driver 使用 `... || true` 丢弃每个 simulator 的真实退出码，随后只通过 grep 日志内容决定 PASS/SKIP/FAIL。

实际 33 个 case 中：

- 25 个走了实际 Ibex RTL 执行路径；
- 6 个属于 superseded；
- 2 个属于 Ibex RTL 模式 N/A。

由于 skip 文本和脚本匹配规则不一致，8 个未执行/N/A case 最终被 cocotb 空执行 summary 计为 PASS，汇总成 `33 PASS / 0 SKIP`。

证据：`/home/prj/zhengs/caduceuscore/CaduceusCore/build/evidence/task-16-soc-rtl-verification-signoff.txt:26`

正确口径应至少改为：

> 25 个实际执行 case 获得 cocotb PASS，6 个 superseded，2 个 N/A。

如果 signoff 标准要求 33 个 case 全部真实运行，则当前条件未满足。

### 3.5 High：Final F3 并非 Real Manual QA

文件：`/home/prj/zhengs/caduceuscore/CaduceusCore/build/evidence/task-F3-soc-rtl-verification-signoff.txt:1`

F3 被标记为 `Real manual QA: PASS`，但记录显示：

- pytest：20 failed、15 errors；
- Spike smoke：DRY-RUN/DEFERRED；
- sz0001 spot checks：DRY-RUN/DEFERRED；
- 最终仍输出 F3 overall PASS。

当前工作区里的 Spike 日志还明确记录：

- L0 Q_proj FAIL；
- `max_diff=7.64e+02`；
- `0 PASS, 1 FAIL`。

文件：`/home/prj/zhengs/caduceuscore/CaduceusCore/build/evidence/task-F3-spike-smoke.log:5`

因此 F1-F4 全 APPROVE 的结论不可接受。F3 应在规定环境真实执行，或将无法执行的项目明确标记为 INCONCLUSIVE/BLOCKED，而不是 PASS。

### 3.6 High：Make 回归目标可能吞掉 simulator 失败

多个新增 Make target 将 simulator 输出管道传给 `tee`，但没有启用 `pipefail` 或检查 `PIPESTATUS`，随后只 grep PASS marker。

如果测试先打印 PASS marker、之后异常退出，Make target 仍可能返回成功。Cocotb XML 也没有作为最终结构化结果验证。

必须补充：保留 simulator 原始退出码；同时解析结构化 Cocotb/JUnit 结果；日志 marker 只能作为辅助信息。

### 3.7 High：Ring-wrap 通过不等于 1100 条命令全部正确完成

文件：`/home/prj/zhengs/caduceuscore/CaduceusCore/firmware/npu_firmware.c:668`

现有测试主要检查 head 前进到 1100，并复用最终输出 buffer；firmware 在 dispatch 出错后仍会推进 head。测试还接受 completion status 索引越过 ABI 定义区域，写入相邻 INTC MMIO，可能清除 INTC.ENABLE/THRESHOLD。

因此 `NPU_HEAD=1100` 不能证明 1100 条命令全部 `status=0`，而且测试自身可能产生寄存器越界副作用。

必须补充：逐条/逐波验证 completion status；限制 completion index；增加 wrap 过程中仍依赖 IRQ/WFI 的场景。

### 3.8 High：FM-SOC-10X 验收范围和报告不一致

SFU descriptor ABI 修复方向合理，已有后续日志显示 FM-SOC-004/027/10X 和 SFU batch 可以通过。但 `_verify_10X` 实际主要验证 op00/op01，后续 op 被跳过，最终却报告 `17-op chain PASS`。

文件：`/home/prj/zhengs/caduceuscore/CaduceusCore/sim/rtl_soc_runner.py:3611`

结论应限定为“前两个关键 op 和相关因果回归通过”，或补齐全部 17-op 的独立验证。

### 3.9 High：固件地址和尺寸检查不足

文件：`/home/prj/zhengs/caduceuscore/CaduceusCore/firmware/npu_firmware.c:458`

`dram_range_ok()` 对所有低于 `DRAM_BASE` 的地址直接返回成功，包括 ROM、地址空洞和 `0x4000_xxxx` MMIO。Host 控制的 descriptor 可能因此让 DMA/PCIe DMA 访问非预期低地址或设备寄存器。

另外，MMUL 只检查 descriptor 自报的 `input_size/weight_size/output_size`，实际访问量却由 M/K/N、tile 数量和固定 chunk 推导。声明 size 很小但维度很大时，实际访问可能越过已经验证的范围。

必须补充：

- 地址 allowlist：明确限定 SRAM 和 DRAM 合法窗口；
- checked arithmetic：防止加法/乘法溢出；
- 校验实际所需字节数不超过 descriptor size；
- 增加 MMIO/ROM、near-end、undersized buffer、最大维度和溢出负例。

### 3.10 High：Checkpoint 证据 provenance 不完整

Task 14 文本列出了 8 个 checkpoint cosine 值，但仍有以下问题：

- start/end 时间戳相同，却声称运行 47,241.5 秒；
- 缺少原始 simulator/driver log 和真实退出码；
- checkpoint NPZ 与原始日志被 `.gitignore` 排除；
- evidence 没有记录 NPZ、simv、firmware、golden、model 和所有输入源码的 hash；
- resume 使用 `allow_pickle=True` 加载本地 NPZ，存在不受信 pickle 和伪造 checkpoint 风险；
- evidence 对应的 commit 早于部分 resume/timeout 变更。

文件：`/home/prj/zhengs/caduceuscore/CaduceusCore/build/evidence/task-14-soc-rtl-verification-signoff.txt:3`

当前只能证明“存在一组通过 ladder 的数值产物”，不能独立证明同一个干净 commit 上的完整 RTL 流程成功结束。

## 4. 尚未闭环的项目级事项

### 4.1 Performance CI 当前失败

文件：`/home/prj/zhengs/caduceuscore/CaduceusCore/.omo/evidence/task-23-perf-spec-ci.txt:1`

当前状态：

- `exit_code: 1`
- `verdict: fail`
- 峰值 RSS：约 17.4 GB
- 允许上限：4 GB

虽然 provider、Qwen/CV dual path、sweep、uncertainty 和 adversarial 子阶段通过，但总体资源门禁失败。

### 4.2 性能 calibration 尚未完成

Feature status 中 E2E-07 仍为“未覆盖”，`calibration_state=uncalibrated`。Func Model 公式/规格验证通过不能等价为 silicon/FPGA/RTL 实测 calibration 完成。

### 4.3 FPGA 与 ggml lifecycle 仍为 BLOCKED

文件：`/home/prj/zhengs/caduceuscore/CaduceusCore/docs/func-model-signoff-checklist.md:339`

- L5：FPGA platform 不可用，Task 20 NO-GO；
- Framework：`fm://python` device server prerequisite 不可用，ggml lifecycle BLOCKED；
- 聚合状态：Overall BLOCKED。

### 4.4 全连续 36 层 Ibex 未完成

当前完成的是分段 checkpoint subset：执行 15 个选定层、检查 8 个 checkpoint，各段首输入来自 Spike NPZ。它不是从 L0 连续运行到 L35 的完整 Ibex forward。

全连续 36 层仍 deferred 到 FPGA 阶段。

### 4.5 BUG-RTL-SOC-007 仍为 Open

17-op attn_weight chain 的特定运行显示 cycles>0、cosine 达标，说明该场景未复现原 cycles=0 问题；但这不等于根因已经找到或 bug 已修复。bug ledger 中“pending todo 15”的描述也已过期，应更新为“todo 15 已执行、未复现、根因仍未知”。

### 4.6 8 MB DRAM waiver 尚未签署

文件：`/home/prj/zhengs/caduceuscore/CaduceusCore/docs/waivers/WVR-SOC-RTL-002.md:11`

waiver 当前仍是 `pending sign-off`，签字栏为空，用户签署又是明确生效/关闭条件。但 bug ledger 已将 BUG-RTL-SOC-002 写为 formally Waived，存在状态漂移。

用户批准前应保持 Pending/Open，不应作为正式 closure 依据。

## 5. 已确认的有效进展

尽管总体 signoff 不通过，以下执行成果具有较强的实际运行迹象：

1. MobileNetV3 RTL chain：
   - 50/52 convolution cosine ≥ 0.99；
   - 2 个退化层 bit-exact；
   - 0 mismatch；
   - 657 条 ring command；
   - DRAM staging 保持在 8 MB 内。

2. ATTN-WEIGHT chain：
   - 17 ops、26 commands；
   - 关键 attn_weight op cycles=30755；
   - min cosine 约 0.999984；
   - INT32 op bit-exact。

3. IRQ mask/stall/drain：
   - ENABLE=0 时 cpu_irq 维持低；
   - NPU_HEAD 在指定窗口内停滞；
   - 恢复 ENABLE 后命令排空；
   - DMA 数据 bit-exact。

4. Ibex shared address：
   - Ibex→MXU 和 MXU→Ibex 双向一致；
   - DMEM/boot ROM 隔离检查通过。

5. PCIe TLP、INTC threshold、boot assertion、corrupted descriptor、部分 ring-wrap 场景均有非平凡 VCS 日志和 Cocotb PASS summary。

6. SFU descriptor ABI 根因定位有价值：此前 runner 使用 MMUL descriptor layout 写 SFU descriptor，导致 RMSNorm/SiLU 被误解释或跳过；改用 ABI-correct writer 后相关链路表现改善。

## 6. 文档与交付一致性问题

1. `docs/func-model-signoff-checklist.md` 顶部写 Performance signoff PASS，但文件其他位置仍写 FAIL/PARTIAL，实际状态又是 `calibration_state=uncalibrated`。
2. `docs/soc-rtl-verification-feature-status.csv` 将 64/66 标为 PASS，但该文件未被 Git 跟踪，不能作为正式交付物。
3. BUG-RTL-SOC-007 的 ledger 文本仍写 todo 15 pending，而 todo 15 已执行。
4. WVR-SOC-RTL-002 尚未签署，但 ledger 已称 formally Waived。
5. plan 要求 F1-F4 全 APPROVE 后等待用户 explicit okay，现有 Git 中没有用户签收记录。
6. F1 对大量 EDA acceptance 使用 SKIP-ENV 后仍计 PASS；F3 对 DRY-RUN/DEFERRED 项也计 PASS。

## 7. 整改优先级

### P0：先消除假阳性和真实 RTL 风险

1. 修复 crossbar 并发 accept/grant deadlock，并增加真实 contention fairness test。
2. 将 APB conformance 连接到真实七个 peripheral RTL。
3. 所有 regression runner fail-closed：
   - 不吞 simulator 退出码；
   - timeout 不得返回 PASS；
   - 禁止旧 evidence 满足当前运行；
   - 使用结构化结果区分 PASS/SKIP/FAIL/BLOCKED/TIMEOUT。
4. 修复固件地址 allowlist、实际 size 校验和 completion-status 越界。

### P1：重建可信 signoff 证据

1. 在干净、固定 commit 上 fresh build。
2. evidence 绑定以下 hash：
   - Git HEAD 和 dirty state；
   - simv；
   - RTL/filelist；
   - Python/Cocotb driver；
   - firmware ELF/HEX；
   - golden/model/checkpoint；
   - 工具版本。
3. 将回归口径更正为真实执行数、SKIP 和 N/A，不再用 33/33 掩盖未执行项。
4. 真实执行 F1-F4，不允许 DRY-RUN/DEFERRED 自动转 PASS。
5. 对 checkpoint resume、timeout、旧 evidence、损坏 NPZ 和错误 commit 做负面测试。

### P2：关闭项目级 blocker

1. 完成 RTL performance decomposition/calibration。
2. 处理 performance CI 17.4 GB RSS 超限。
3. 完成或明确重新定界全连续 36 层 Ibex forward。
4. 解除 FPGA L5 和 ggml lifecycle blocker。
5. 继续定位 BUG-RTL-SOC-007 根因。
6. 用户评审并签署 WVR-SOC-RTL-002；签署前保持 Pending。

### P3：整理正式交付状态

1. 清理并分类当前 dirty worktree，保留用户有效产物，不覆盖现有修改。
2. 将正式 feature-status、计划、签核报告和必要 evidence 纳入版本控制。
3. 统一 checklist、bug ledger、waiver、vplan 和 evidence 的状态口径。
4. 形成一个可重放的 signoff manifest 和最终签收记录。

## 8. 建议的阶段性状态表述

在上述问题关闭之前，建议对外统一使用以下表述：

> 截至 2026-08-28，CaduceusCore 已完成多项 SoC RTL 定向功能回归，MobileNetV3、ATTN-WEIGHT、IRQ、共享地址及若干异常场景有实际 VCS PASS 证据。全量 runner 中实际执行的 25 个 Ibex case 获得 cocotb PASS，另有 6 个 superseded 和 2 个 N/A；当前不应表述为 33 个独立 case 全部实跑。36 层仅完成分段 8-checkpoint subset。Crossbar 并发公平性、真实 APB peripheral conformance、RTL performance calibration、FPGA、ggml、开放 bug、未签 waiver 和可信 evidence provenance 尚未闭环，因此完整 SoC RTL/project signoff 未完成。

## 9. 最终意见

本轮工作不是“没有完成”，而是“功能开发推进较快，但 signoff 方法和证据门禁没有达到与项目复杂度匹配的可信度”。部分真实功能已经跑通，应予保留；但在关键测试有效性、退出码传播、证据来源和项目级 blocker 关闭前，不应批准完整 signoff。

建议先完成 P0/P1，再重新执行一次干净环境下的独立 review。新的 review 应以真实竞争、真实 peripheral、结构化退出状态和可验证 provenance 为批准前提。
