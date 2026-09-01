# REMEDIATION-RTL-EXCEPTION-2026-08-28 — soc-rtl-review-remediation 计划 RTL/固件修改例外

| 字段 | 内容 |
|------|------|
| **Exception ID** | REMEDIATION-RTL-EXCEPTION-2026-08-28 |
| **Plan** | `.omo/plans/soc-rtl-review-remediation.md`（Must NOT #9 / Metis M1 前置动作） |
| **Date** | 2026-08-28（计划签署日）；本文件创建于 2026-08-31 执行 todo 7 之前 |
| **Type** | 外部评审整改的 RTL/firmware 产品代码修改例外 |
| **Status** | 生效（active） |

## 批准记录（Approval Record）

- 计划 `.omo/plans/soc-rtl-review-remediation.md` 经 **2026-08-31 用户批准**执行，
  批准前经过 **round-6 双审查**（Oracle + Metis 轮次审查，计划 Must NOT 列表与
  TL;DR 均引用该审查结论）。
- 计划 Must have #1 与 #2 明确授权修改本文件所列两项产品代码；
  Must NOT #9 要求在执行修改前先创建本例外文件——本文件即该前置动作。
- 用户"同意执行"决策点（TL;DR Decisions to sanity-check）已通过：修复方案
  定为 accept/grant 耦合（option (a) 强制、option (b) 禁用）。

## 允许修改的文件（Scope）

| # | 文件 | 修改内容 | 授权依据 |
|---|------|----------|----------|
| 1 | `rtl/soc/axi_crossbar.v` | AR/AW accept 与 grant 耦合，消除 phantom-accept deadlock；新增 `FIXED_PRIORITY` mutation 测试参数（默认 0 = 原行为） | 计划 todo 7；评审报告 3.1 节（Critical） |
| 2 | `firmware/npu_firmware.c` | 地址 allowlist + 实际 size 校验 + completion-status 越界修复 | 计划 todo 8；评审报告 3.7（High）、3.9（High）节 |

F4 范围核对口径：除上表两文件外，不得有其他 `rtl/` / `firmware/` 产品代码改动
（TB、runner、evidence、docs 不在此限，属计划明示的测试基础设施改动）。

## 理由（Reason）

外部评审报告 `reports/CaduceusCore-review-report-2026-08-28.md`：

- **3.1（Critical）**：crossbar 在 slave-free cycle 多 master 同时 VALID 时，
  可能 accept 全部但只 grant 一个，其余永久等待（phantom-accept deadlock）——
  必须修复 accept/grant 协议并以真实并发竞争 + 固定优先级 mutation 反证。
- **3.7（High）**：completion status 索引越过 ABI 定义区域写入相邻 INTC MMIO，
  可能清除 INTC.ENABLE/THRESHOLD——必须钳制 completion index。
- **3.9（High）**：`dram_range_ok()` 对 ROM/空洞/MMIO 低地址放行；descriptor
  声明 size 与实际访问量脱钩——必须 allowlist + checked arithmetic + 实际
  字节数校验。

## 撤销条件（Revocation / Rollback Gate）

本例外在以下任一条件触发时自动撤销，对应修改回滚并 file blocker：

1. **全量回归回滚门（Metis m4）**：todo 7/8 修复后，全量 33-case FM-SOC 回归中
   任何修复前 PASS 的 case 变为 FAIL → 自动回滚该 commit 并 file blocker。
2. `run_crossbar_stress` 不回退门：修复后 stress 必须仍 PASS（1,260 txns
   0 errors）；若回退（新死锁/饥饿）→ 回滚。
3. 计划 F1-F4 最终验收未通过（任一 F 波非 APPROVE 且无用户豁免）。
4. 用户随时可口头/书面撤销本例外。

## 临时 / 永久

**临时例外。** 关闭条件：计划 soc-rtl-review-remediation 完成（F1-F4 全 APPROVE
+ 用户确认，分支 merge 到 main）后，本例外归档关闭；届时两文件的修改已成
main 基线，不再需要例外身份。
