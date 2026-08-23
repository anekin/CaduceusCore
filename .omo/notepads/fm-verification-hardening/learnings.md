# fm-verification-hardening learnings

fm-hardening-phase10 的关键决策记录，供后续验证工作参考，避免重复推演。来源：`.omo/plans/fm-hardening-phase10.md` 及 `fm-hardening-phase10/learnings.md` 各 todo 的落地记录。

## 6 类 bug → 守卫映射表

Phase 10 复盘把曾需 7.5h RTL 段跑才暴露的缺陷归为 6 类，每类对应一组纯 Python FM 守卫：

| # | Bug 类 | 暴露场景 | FM 守卫 | 落地 todo |
|---|--------|----------|---------|-----------|
| 1 | 内存区域重叠 / 环语义分歧（layout/ring） | BUG-RTL-SOC-008：DESC_BASE 落入命令环区，L19 段跑才暴露 | `address_space.contract_check()` 调度期断言 + `command_ring` 环配置唯一事实源 + ring-stress / 长序列持久偏移场景 | 1/2/3/4/5 |
| 2 | scale/accumulate golden 空洞（裸 INT32 golden 放过 stub） | F3 发现 PERF 回归：FP16-in-FP32 scale 坍缩、忽略 CTRL[2] | SCALE_ADDR!=0 + 非平凡 FP32 scale 回归、CTRL[2] 两命令链 accumulate 回归，均对齐 `matmul_int4_per_block` | 6/7 |
| 3 | 双 activation packer 分歧 | ISSUE-13B：spike_host 与 cocotb_bridge 布局漂移 | 双 packer 逐字节等价测试（6 网格点 + 行主序失败注入） | 8 |
| 4 | 跨语言常量漂移 | 手写常量分处 Python/C | `spec/npu_abi.json` 单一来源 → 生成头 → firmware 引用 + 跨语言数值比对测试 | 9 |
| 5 | 段边界 SRAM 残留 | ISSUE-13C：段间残留数据污染下段 | `segment_preload` 显式 `clear_sram` 契约 + 双段 FM 场景 | 10 |
| 6 | 回归套件未接线的自动门禁 | RTL 改动未自动触发相关回归 | 反向依赖门禁脚本 + FM attn_weight 覆盖 + F 波脚本 | 11/12/13 |

共同原则：每个守卫都带失败注入用例证明门禁真实，不是空断言（如 DESC_BASE=0x80001000 注入必须抛 `OverlapError`）。

## FM-SOC runner 差异化布局决策（P0/P1/P2P3 vs P4）

- **P0/P1/P2P3** 保留 `DESC_BASE=0x80001000` + `RING_SIZE=32`：该地址实际落在 1024 条目固件环区域内（entry 128-191，32 命令 × 64B stride），其安全性完全依赖"每次调度 ≤32 条命令、永不写到 entry 128+"这一不变量。处置为**如实标注 + 守卫**（`len(ops) <= RING_SIZE` 上限断言 + scoped `assert_desc_clear_of_used_regions`），并加 BUG-RTL-SOC-008 注释，不迁移布局。
- **P4** 使用 block 相对 descriptor 地址（`_P4_DESC_BASE_REL=0x00038000`，block 0 即 0x80048000），天然位于环区与完成环区之外；644 命令（28 block × 23）单 1024 条目环容纳无回绕。处置为 **verify-and-annotate（不迁移）**，在 builder 启动处加 `contract_check(ring_entries=1024, desc_base=block_base+..., desc_count=23, act_base=0x80800000)` 显式守卫。
- 关键点：两种布局都不适配通用 `contract_check` 的 1024 条目完成环语义（P0 的 0x80001000 会被通用检查判 OverlapError，但 per-runner 不变量下是安全的），因此各自使用 scoped 检查 + 显式参数，而非统一迁移。

## `sim/device_server.py` RING_SIZE=16 排除理由

- `device_server.py:95` 的 16 条目环属于 **host-device 协议路径**，与 Phase 10 段跑（固件命令环，1024 条目）无关。
- 统一该常量无收益（不共享任何代码路径），且改动引入回归风险。处置：保留原值，仅加注释说明排除理由。

## tests-after 选择

- 本计划所有新增断言按"当前正确状态"编写，不回溯验证旧值会失败。
- 理由：6 类 bug 在 FM 层要么从未存在、要么已修复，tests-after 锁定的是加固后的契约；每个守卫都带失败注入用例（monkeypatch 构造 pre-fix 形态）证明门禁确实能抓错，避免测试自身为空转。
