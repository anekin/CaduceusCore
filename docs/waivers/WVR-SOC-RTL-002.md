# WVR-SOC-RTL-002 — 8MB DRAM 窗口约束（BUG-RTL-SOC-002）

| 字段 | 内容 |
|------|------|
| **Waiver ID** | WVR-SOC-RTL-002 |
| **Bug ID** | BUG-RTL-SOC-002 |
| **Date** | 2026-08-27 |
| **Type** | 环境（DRAM 行为模型窗口限制） |
| **Severity** | Major |
| **Temporary / Permanent** | 临时（temporary） |
| **Status** | 提交待签（pending sign-off） |
| **Sign-off** | （留空，待用户签署） |

## 约束描述（Constraint）

`firmware/npu_firmware.c` 中的 `dram_range_ok()` 拒绝任何越出 8MB 模拟窗口的
DRAM 地址区间（`firmware/npu_firmware.c:458` 函数定义，`:472-485` 处
`dispatch_cmd` 对描述符地址与 MMUL 数据地址的越窗检查）：

- `DRAM_SIZE = 0x00800000`（8MB），`DRAM_END = DRAM_BASE + DRAM_SIZE`（`npu_firmware.c:19-20`）。
- `rtl/ip/dram_model.v` 的行为级模型仅实现了 sparse 8MB 存储窗口
  （`reg [7:0] mem [0:8388607]`），与固件的 `DRAM_SIZE` 约束一致。
- 越窗访问区间会导致 DECERR AXI 事务或 backdoor 报错；固件策略为 REJECT——
  命令置错误状态（status=1），不发起事务。

## 影响（Impact）

- 单次加载的模型权重、激活或描述符数据必须落在 DRAM 基址 `0x80000000` 起的
  8MB 窗口内。
- 权重总量 >8MB 的模型必须**分段预载/分段下发**（split / pre-loaded in stages），
  或后续在 FPGA 阶段**扩展 `dram_model.v` 的存储窗口**后才能单次驻留。
- 当前 RTL 回归覆盖的 33 个 FM-SOC cases 全部在 8MB 窗口内 PASS，不影响
  phase 10 与本次 signoff 已声明的验证范围。

## 临时 / 永久

**临时 waiver。** 关闭条件：FPGA bring-up 阶段扩展 `dram_model.v`（或替换为
真实 DRAM 控制器）后，DRAM 窗口约束解除，本 waiver 关闭，BUG-RTL-SOC-002
转为 Fixed 或从台账移除。

## 证据（Evidence）

- **正式证据（待 todo 16 生成）**：todo 16 全量 RTL 回归，33 个 FM-SOC cases
  （FM-SOC-001..032 + FM-SOC-10X）全部在 8MB 窗口内 PASS，
  `build/evidence/task-16-soc-rtl-verification-signoff.txt`。
- **Transitional evidence**：`.omo/notepads/phase10-rtl-verification/issues.md`
  （ISSUE-13A 记录 8MB DRAM 镜像与 bulk preload 实测，逐字节 bit-exact）与
  `firmware/npu_firmware.c` 当前代码（`:19-20` DRAM_SIZE 定义、`:458,472-485`
  `dram_range_ok` 越窗拒绝）。
- **Bug 台账引用**：`docs/bugs/bugs-soc-rtl.md` BUG-RTL-SOC-002（Status Pending（waiver
  待用户签署），2026-08-31 更新——todo 19 soc-rtl-review-remediation 改回 Pending）。

## 关闭条件（Closure Criteria）

1. todo 16 全量 RTL 回归 33/33 FM-SOC PASS（无新增越窗）。
2. FPGA bring-up 阶段 DRAM 窗口扩展完成，越 8MB 的模型可单次驻留。
3. 用户签署本 waiver。

## Sign-off

> （留空，待用户签署）
