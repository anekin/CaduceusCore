# Bug Tracking — SoC RTL Substitution

> **阶段**: Phase 4 SoC RTL 逐步替换
> **关联 plan**: `.omo/plans/soc-rtl-substitution.md`

## 规则

1. 发现 bug 立即追加，不攒批
2. 修复后更新 Status → Fixed，不删除
3. 每个 bug 一次 git commit：`fix(rtl): BUG-RTL-SOC-NNN — <module> <root cause>`

---

## Bug 条目模板

```
### BUG-RTL-SOC-NNN

| 字段 | 内容 |
|------|------|
| **Date** | YYYY-MM-DD |
| **Block** | T# (发现于哪个 todo) |
| **Case** | FM-SOC-NNN |
| **Severity** | Critical / Major / Minor |
| **Type** | Engine RTL / Wrapper / Integration / Firmware / Environment |
| **Status** | Open / Fixed |

#### Symptom

#### Root Cause

#### Fix

#### Verification
```

---

## Bug 日志

### BUG-RTL-SOC-001 — Spike plugin GLIBC 不兼容

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-04 |
| **Block** | T7 P1 |
| **Case** | FM-SOC-009~026 |
| **Severity** | Critical |
| **Type** | Environment |
| **Status** | Fixed |

#### Symptom

`npu_mmio_plugin.so` 加载失败，Spike 进程提前退出：

```
ERROR [SPIKE] process exited early
Unable to load extlib 'npu_mmio_plugin.so':
  /lib64/libc.so.6: version `GLIBC_2.32' not found
```

#### Root Cause

`npu_mmio_plugin.so` 在本地机器上编译，链接了本地新版 GLIBC（2.32）。VCS 仿真时 Spike 跑在 EDA server（sz0001, 192.168.0.11）上，该服务器的 GLIBC 是旧版本。T6 时 agent 在 sz0001 上重编译了 `spike` 本体，但忘了 plugin .so 也需要重编译。

#### Fix

在 EDA server (sz0001) 上重新 `make -C spike_src/plugins`。或者修改 `run_env.sh` 让每次跑之前自动在目标机器上编译 plugin。

#### Verification

T7 P1 全 7 cases 重跑，Spike plugin 加载成功，firmware 正常启动。

---

### BUG-RTL-SOC-002 — DRAM 地址窗口越界

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-04 |
| **Block** | T7 P1 |
| **Case** | FM-SOC-010 |
| **Severity** | Major |
| **Type** | Environment (DRAM model) |
| **Status** | Open |

#### Symptom

Spike firmware 访问 DRAM 地址时报错：

```
ValueError: Address 0x81FFFFC0 outside simulated DRAM window
```

`0x81FFFFC0` = DRAM base `0x80000000` + ~32 MB，但 `dram_model.v` 只模拟了 8 MB 窗口。

#### Root Cause

`dram_model.v` 的行为级模型使用 sparse 8 MB 存储（`reg [7:0] mem [0:8388607]`），但 firmware 使用的数据地址落在 >8 MB 区域。Func Model 的 Python DRAM 模型没有此限制（使用 `bytearray`），所以 Func Model 阶段未暴露。

#### Fix

扩大 `dram_model.v` 的存储窗口，或确认 firmware 数据地址分配在 8 MB 以内。

#### Verification

FM-SOC-010 DRAM preload 无越界错误。

---

### BUG-RTL-SOC-003 — `caduceus_soc_spike_top.v` 缺失 SFU/Vector/DMA 的 `cb_m_arvalid` 驱动

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-04 |
| **Block** | T8 |
| **Case** | FM-SOC-003/004/005/006/011/026 |
| **Severity** | Critical |
| **Type** | RTL (SoC integration) |
| **Status** | Fixed |

#### Symptom

引擎（SFU/Vector/DMA）的 AXI 读地址 valid 信号 `cb_m_arvalid[2/3/4]` 未驱动，导致总线上出现 X。引擎无法从 SRAM/DRAM 读取数据，表现为：
- SFU/Vector 输出全 0。
- MXU 输出为垃圾值。

#### Root Cause

`rtl/soc/caduceus_soc_spike_top.v` 中为每个 AXI master 分配了 `cb_m_araddr/len/size/burst`，但遗漏了 `cb_m_arvalid[2]` (SFU)、`cb_m_arvalid[3]` (Vector)、`cb_m_arvalid[4]` (DMA) 的 assign 语句。

#### Fix

补全三条 assign：

```verilog
assign cb_m_arvalid[2] = sfu_arvalid;
assign cb_m_arvalid[3] = vec_arvalid;
assign cb_m_arvalid[4] = dma_arvalid;
```

#### Verification

补全后 P0 8/8 PASS、P1 6/7 PASS（FM-SOC-026 的 chain SFU 零输出为独立问题，见 BUG-RTL-SOC-004 / I24）。

---

### BUG-RTL-SOC-004 — SFU wrapper 在 rd_state 忙时丢弃 I_ADDR prefetch

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-04 |
| **Block** | T9 |
| **Case** | FM-SOC-027 |
| **Severity** | Major |
| **Type** | RTL (SFU wrapper) |
| **Status** | Fixed |

#### Symptom

FM-SOC-027（blk.0 17-op chain）执行到第 2 个 Vector→SFU 依赖操作时，SFU 输出全 0，仿真超时被判定为 FAIL。波形显示 SFU wrapper 在 `rd_state` 忙时收到新的 `I_ADDR` prefetch 请求，但直接丢弃，导致后续 SFU 运算读取到 stale/零数据。

#### Root Cause

`rtl/wrapper/sfu_soc_wrapper.v` 的 prefetch 握手逻辑仅在 `prefetch_pending==0` 时锁存 `I_ADDR`，未考虑 `rd_state` 正在处理前一次读请求的情况。当后一条指令的 `I_ADDR` 提前到达时，地址被丢弃，`sfu_top` 读到的仍是旧 SRAM 数据。

#### Fix

增加一个 `prefetch_pending` 锁存：若 `rd_state` 忙，将新 `I_ADDR` 暂存，等当前读完成后再把锁存地址送入读通道。

Commit: `722c6a8 fix(rtl): BUG-RTL-SOC-004 — SFU wrapper drops I_ADDR prefetch when rd_state busy`

#### Verification

FM-SOC-027 在 P2+P3 回归中 PASS；P2+P3 全部 10 个 active cases PASS（10/10）。
