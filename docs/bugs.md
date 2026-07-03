# Bug Tracking — MXU 模块级性能验证

> 更新: 2026-07-02
> 被测对象: `rtl/mxu/` — 64×64 Broadcast MAC Array (8 RTL files, 1,304 lines)
> 测试用例: `rtl/testcase-list-mxu-perf.md` — 18 cases (MX-P01..MX-P18)
> 前序记录: [`docs/issues_found.md`](issues_found.md) (Func Model 开发阶段问题)

## 使用规则

1. 性能验证中发现的 **每个 bug** 都必须记录在此文件
2. 新 bug 始终 **追加** (append) 到末尾, 不覆盖已有条目
3. RTL bug 必须包含「详细根因分析」(Detailed Root Cause Analysis) 子章节
4. 修复后更新 Fix / Verification / Status 字段, 不删除原条目
5. 每个 bug 可附带独立分析文件 `docs/bugs/BUG-XXX.md`, 但 bugs.md 必须保留摘要

## 严重级别 (Severity)

| 标签 | 定义 |
|------|------|
| **Critical** | 功能错误或周期偏差 > 25% — 阻塞后续 P0-P4 case 执行 |
| **Major** | 周期偏差 1~25% 或非功能性设计缺陷 |
| **Minor** | 周期偏差 ≤ 1 cycle 但测试可复现, 可能需要设计确认 |
| **Trivial** | 文档错误、信号命名不一致、日志格式问题等 |

## 类型标签 (Type)

| 标签 | 定义 |
|------|------|
| **RTL** | Verilog 逻辑错误 — FSM、datapath、pipeline |
| **Testbench** | `tb_mxu_perf.v` 测量逻辑错误 |
| **Script** | Python 脚本 (analyze_perf.py, gen_mxu_vectors.py 等) |
| **Environment** | VCS 编译、EDA server、module 加载等问题 |
| **Formula** | 预期 cycle 公式推导错误 |
| **Tooling** | 分析工具、diff 脚本、CI 等问题 |
| **Func Model** | Python golden reference 行为模型错误 — 与 RTL 规范不一致 |

## Bug 条目模板

每个 bug 记录遵循以下结构。RTL 类型必须包含「详细根因分析 (Detailed Root Cause Analysis)」子章节。

```
### BUG-MX-PERF-001

| 字段 | 内容 |
|------|------|
| **Date** | YYYY-MM-DD |
| **Case** | MX-PXX (关联测试 case ID) |
| **Severity** | Critical / Major / Minor / Trivial |
| **Type** | RTL / Testbench / Script / Environment / Formula / Tooling |
| **Status** | Open / Fixed / Won't Fix / Duplicate |
| **Found by** | Agent / Human |

#### Symptom (症状)

简要描述观察到的失败现象或周期偏差。

#### Root Cause (根因)

描述根本原因。RTL bug 必须包含「详细根因分析 (Detailed Root Cause Analysis)」。

#### Detailed Root Cause Analysis (详细根因分析)

> 仅 RTL 类型需要此章节。以下为必需内容。

1. **涉及模块**: 受影响 RTL 文件及行号范围
2. **触发条件**: 什么配置或数据序列触发该 bug
3. **机制分析**: 从 RTL 源码层面描述信号/状态错误传播路径
4. **影响范围**: 哪些 case、哪些配置受影响; 是否影响功能正确性
5. **为什么未被前期功能测试发现**: 分析功能测试 (MX-01..MX-16) 覆盖率缺口

#### Fix (修复)

描述修复方式, 涉及的文件和修改概要。Status=Fixed 时必填。

#### Verification (验证)

修复后如何验证。Status=Fixed 时必填。

#### References (参考)

相关 commit hash、issue 链接、波形文件路径等。
```

---

## Bug 日志

<!-- 每发现一个 bug, 在下方按模板追加一条新记录。不要覆盖已有条目。 -->

### BUG-MX-PERF-000 (占位示例 — 非真实 Bug)

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-02 |
| **Case** | MX-PXX |
| **Severity** | Minor |
| **Type** | Testbench |
| **Status** | Fixed |
| **Found by** | Agent |

#### Symptom (症状)

`tb_mxu_perf.v` 中 `perf_cycle` 计数器在 `$display` 输出中显示 `READ_DIMS=0`, 导致首 tile 缺少 1 cycle。

#### Root Cause (根因)

计数器使用 `if (perf_counting)` 控制累加, 而 `perf_counting` 在 `READ_DIMS` 状态后一个 cycle 才拉高, 导致 `READ_DIMS` 期间未被计入。

#### Detailed Root Cause Analysis (详细根因分析)

> 注: 此章节为 RTL bug 专用。此处为 Testbench bug, 仅用于格式示例。

N/A — Testbench 类型不需要此章节。

#### Fix (修复)

将累加条件从 `if (perf_counting)` 改为 `if (state != S_IDLE && state != S_DONE)`, 确保 FSM 进入 READ_DIMS 即开始计数。

#### Verification (验证)

重新运行 MX-P01 (shape=64,64,64): `total=134`, `cnt_read_dims=1`, 与公式预期一致。P0 三个 case 全部 PASS。

#### References (参考)

- Commit: `a1b2c3d4`
- 见 learnings.md 2026-07-02 Phase 0b 条目

---

### BUG-SOC-FM-001 — GoldenVector.add/mul INT32 overflow wrap-around

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-04 |
| **Case** | FM-SOC-030 (Boundary INT32 overflow saturation), Task 17 |
| **Severity** | Major |
| **Type** | Func Model reference bug |
| **Status** | Fixed |
| **Found by** | Agent |

#### Symptom (症状)

`GoldenVector.add(INT32_MAX, 1)` returned `INT32_MIN` (wrap-around) instead of `INT32_MAX`. `GoldenVector.mul(2^16, 2^16)` returned `0` instead of `INT32_MAX`. The Vector Engine RTL (`vector_alu.v`) specifies saturated INT32 SIMD add/mul, so the Golden Reference must match.

#### Root Cause (根因)

`GoldenVector.add` and `GoldenVector.mul` performed arithmetic in `np.int32` and cast back to `np.int32`, which wraps modulo 2^32 on overflow instead of saturating to `[INT32_MIN, INT32_MAX]`. The RTL Vector ALU uses saturated arithmetic with `$signed()` saturation logic, so the Func Model reference was incorrect.

#### Fix (修复)

Changed both methods in `sim/golden_executor.py` to compute in `np.int64` and apply `np.clip(result, INT32_MIN, INT32_MAX)` before casting back to `np.int32`.

#### Verification (验证)

- `test_boundary_int32_overflow_saturation` (FM-SOC-030) PASS — saturated values match INT32_MAX/MIN, not wrap-around.
- `test_golden_vector.py` 251/251 PASS — no regression (V-01 random range [-10000,10000] avoids overflow).
- `test_soc_fm.py` 44/44 PASS — no regression.

#### RTL Impact / Phase 2 Note

RTL `vector_alu.v` already implements saturated INT32 add/mul correctly (lines ~98-112). This bug was in the Func Model reference only. During RTL Phase 2 cross-validation, confirm that `vector_alu.v` saturation bounds match `np.clip(INT32_MIN, INT32_MAX)` exactly for all corner cases (INT32_MAX+1, INT32_MIN-1, 0+0, INT32_MAX+INT32_MAX).

#### References (参考)

- Fix applied in Task 17 (Boundary and Corner Cases, 2026-07-04)
- See learnings.md 2026-07-04 Task 17

---

### BUG-SOC-FM-002 — GoldenSFU missing FP16 subnormal flush-to-zero

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-04 |
| **Case** | FM-SOC-031 (FP16 denorm flush boundary), Task 17 |
| **Severity** | Major |
| **Type** | Func Model reference bug |
| **Status** | Fixed |
| **Found by** | Agent |

#### Symptom (症状)

SFU ops (softmax, gelu, silu, rmsnorm) with subnormal FP16 inputs produced different results than the same ops with zero inputs. The RTL SFU README explicitly states "FP16 subnormals flushed to zero."

#### Root Cause (根因)

`GoldenSFU` hardware methods (`softmax_hw`, `gelu_hw`, `silu_hw`, `layernorm_hw`, `rmsnorm_hw`, `rope_hw`) operated on `float32` values without flushing inputs that are subnormal in `float16`. The MMIO bridge only converted FP16 to FP32, preserving subnormals. The RTL SFU flushes subnormals at the input stage before any computation.

#### Fix (修复)

Added `GoldenSFU._flush_f16_subnormals()` helper in `sim/golden_executor.py` that replaces `abs(x) < np.finfo(np.float16).tiny` values with `0.0`, and applied it at the start of every SFU hardware method.

#### Verification (验证)

- `test_boundary_fp16_denorm_flush` (FM-SOC-031) PASS for softmax, gelu, silu, rmsnorm.
- `test_sfu_soc_mmio_back_to_back` PASS after fix.
- `test_golden_sfu.py` + `test_golden_sfu_gaps.py` 110/110 PASS — no regression.
- `test_soc_fm.py` 44/44 PASS — no regression.

#### RTL Impact / Phase 2 Note

The RTL SFU already flushes FP16 subnormals at the input boundary (verified in `rtl/sfu/README.md`). This bug was in the Func Model reference only. During RTL Phase 2 cross-validation, verify the flush threshold (`abs(x) < 2^-24` for FP16) matches between RTL and Func Model, and confirm no SFU pipeline stage operates on subnormal values before the flush.

#### References (参考)

- Fix applied in Task 17 (Boundary and Corner Cases, 2026-07-04)
- See learnings.md 2026-07-04 Task 17

---

### BUG-SOC-FM-003 — NPUFirmware._dispatch missing OpCode.RMSNORM

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-04 |
| **Case** | FM-SOC-10X (P4 E2E host→PCIe→doorbell→firmware→IRQ→17-op blk.0 chain), Task 19 |
| **Severity** | Major |
| **Type** | Func Model reference bug |
| **Status** | Fixed |
| **Found by** | Agent |

#### Symptom (症状)

Doorbell-queued RMSNorm commands returned `status='unknown'` instead of `'done'`, causing the 17-op blk.0 chain to stall when dispatched through `NPUFirmware.run_loop()`. RMSNorm is used for ops 00 and 10 of the blk.0 manifest.

#### Root Cause (根因)

`NPUFirmware._dispatch()` in `sim/miniv.py` checked `op in (OpCode.SOFTMAX, OpCode.LAYERNORM, OpCode.GELU, OpCode.RELU, OpCode.SILU, OpCode.ROPE)` for the SFU branch. `OpCode.RMSNORM` (value `0x17`) was omitted, even though the MMIO bridge `_handle_sfu()` already supports `sfu_op=6` for RMSNorm and the manifest explicitly uses RMSNorm in the blk.0 chain.

#### Fix (修复)

Added `OpCode.RMSNORM` to the SFU dispatch branch in `sim/miniv.py` and mapped it to `sfu_op=6` in the local `sfu_op` dictionary.

#### Verification (验证)

- `test_e2e_host_pcie_doorbell_firmware_compute` (FM-SOC-10X) PASS — all 17 ops including two RMSNorm ops complete with `status='done'`.
- `test_soc_fm.py` 46/46 PASS — no regression.
- `FuncModel.test_conv2d_smoke()` still PASS.

#### RTL Impact / Phase 2 Note

The firmware opcode decode table in the RTL firmware (`firmware/npu_firmware.c` or equivalent) must include `RMSNORM = 0x17` in its dispatch logic. While the MMIO bridge covers the direct-write path, any firmware-driven dispatch that uses an opcode-to-SFU-op mapping table must handle RMSNORM. Verify that the RTL firmware's main dispatch loop covers all 7 SFU opcodes (softmax=1, layernorm=2, gelu=3, silu=4, rope=5, rmsnorm=6) without omission.

#### References (参考)

- Fix applied in Task 19 (P4 E2E, 2026-07-04)
- See learnings.md 2026-07-04 Task 19

---

## 统计

| 指标 | 值 |
|------|:---:|
| Bug 总数 | 3 |
| Open | 0 |
| Fixed | 3 |
| Won't Fix | 0 |
| Duplicate | 0 |
| Critical | 0 |
| Major | 3 |
| Minor | 0 |
| Trivial | 0 |
| RTL | 0 |
| Testbench | 0 |
| Script | 0 |
| Environment | 0 |
| Formula | 0 |
| Tooling | 0 |
| Func Model | 3 |
