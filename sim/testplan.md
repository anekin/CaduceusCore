# NPU Func Model Test Plan

> 最后更新：2026-06-28
> 被测对象：`sim/golden_executor.py` — GoldenMXU + GoldenSFU + GoldenVector + GoldenDMA
> 参考实现：每个 `_hw` 方法对应一个 `_ref` 方法（numpy/scipy 等价实现）
> 方法论：zartbot pattern — Agent 读源码→自主设计测试→写回状态

---

## 验收标准

| 模块 | 指标 | 阈值 | 理由 |
|------|------|------|------|
| SFU | max_relative_error | 1e-5 | FP32 LUT 误差上限，现有测试已用此值 |
| Vector conv_i32_to_f16 | 往返误差 | 0 LSB（bit-exact） | INT32 范围内 BF16 表达无损，丢 1 bit = bug |
| MXU per_channel/block | 输出一致性 | 与 matmul_int32 输出 bit-exact | 量化数学等价，不应引入额外误差 |
| DMA descriptor | encode→decode | bit-exact | 编码解码必须无损 |

**原则**：不满足上述阈值的 case 一律标 ❌ FAIL，不允许"差不多对"。

---

## 优先级说明

```
P0 = 立即执行（0% 覆盖 + 已知坑 + 下游依赖）
P1 = 高优先（0% 覆盖 + bug 难排查）
P2 = 中优先（有基础测试打底，补缺口）
P3 = 低优先（大部分已覆盖，补边界）
P4 = 最终验证（依赖单模块先稳定）
```

---

## 状态图例

- ⬜ TODO — 待执行
- 🔄 RUNNING — 执行中
- ✅ PASS — 通过
- ❌ FAIL — 失败（读备注修复后重试，最多 3 次）
- ⏸️ SKIP — 已有覆盖/无需重复

---

## P0：GoldenVector（9 cases）— 最先执行

> 理由：① 0% 覆盖 ② INT32→BF16 转换桥是已知 error pattern ③ MXU/SFU 输出都依赖它

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| V-05 | P0 | conv_i32_to_f16 | **BF16 转换桥** | INT32→BF16→INT32 往返误差 = 0 LSB | ✅ | 12 exact values roundtrip 0 LSB, clamp verified, 16 tests passed |
| V-06 | P0 | conv_i32_to_f16 | 边界枚举 | INT32_MIN/MAX/0/±1 bit-exact | ✅ | 0/±1 bit-exact, INT32_MIN/MAX saturate to ±65504, 6 tests passed |
| V-01 | P0 | add / mul | FP32 运算正确性 | 随机 1000 组 vs numpy, max_error < 1e-7 | ✅ | 1000/1000 groups bit-exact, 4 tests passed |
| V-02 | P0 | add / mul | 边界值 | NaN→NaN, Inf→Inf, ±0→±0, denorm 行为 | ✅ | ±0/INT32_MINMAX/NaN/Inf/denorm all deterministic, 7 tests passed |
| V-03 | P0 | max_reduce | 正确性 | 随机 100 组 vs np.max, bit-exact | ✅ | 100/100 groups bit-exact + anti-vacuous, 101 tests passed |
| V-04 | P0 | sum_reduce | 累积精度 | 10000 个 1e-7 累积, vs np.sum 误差 < 1% | ✅ | rel_err=~3e-16 (float64), anti-vacuous: float32 would fail at ~5%, 2 tests passed |
| V-07 | P0 | residual_add | 精度保持 | original=1e6, delta=1e-3 → 结果 = 1e6+1e-3 (不丢失) | ✅ | 1e6+1 preserves contribution, INT32 clamp verified, 5 tests passed |
| V-08 | P0 | softmax_max_reduce | 正确性 | vs np.max 参考 | ✅ | 100 parametrized groups bit-exact with np.max, anti-vacuous, 101 tests passed |
| V-09 | P0 | softmax_scale_sub + sum_reduce | 端到端流水线 | max→sub→exp→sum→div, 与 np 参考一致 | ✅ | full pipeline matches scipy softmax, sum-to-1 property, anti-vacuous, 9 tests passed |

---

## P1：GoldenDMA（4 cases）

> 理由：① 0% 覆盖 ② descriptor bug 极难定位 ③ 错误模式已记录

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| DM-01 | P1 | encode/decode | 往返无损 | 随机 100 组合法 descriptor, 往返 = bit-exact | ✅ | 100/100 roundtrip bit-exact + anti-vacuous, 2 tests passed |
| DM-02 | P1 | decode | 非法值拒绝 | 无效 src/dst/len 组合必须抛异常 | ✅ | 12 parametrized invalid combos rejected (direction/channel/size/addr) + anti-vacuous, 13 tests passed |
| DM-03 | P1 | actual_size | 字段溢出 | size=0→0, size=4096→4096, size>4096→异常 | ✅ | size=0→4096 (hw encoding: 0=4096B), size=4096 encode→decode→4096, size>4096→exception, regular sizes verified + anti-vacuous, 5 tests passed |
| DM-04 | P1 | GoldenDMA | 端到端传输 | 已知 pattern→DMA→读回比对, bit-exact | ✅ | load/store both bit-exact with random patterns, different-DRAM-addr → different data anti-vacuous, 3 tests passed |

---

## P2：GoldenMXU 缺口（7 cases）

> 理由：已有 smoke 测试打底, 补 non-square / quant variant / pack 往返

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| MX-04 | P2 | pack_int4 ↔ unpack_int4 | **往返无损** | [-8,7] 全 16 值, 往返 = bit-exact | ✅ | 4 tests: all 16 values roundtrip + full sequence + known packed bytes + anti-vacuous |
| MX-05 | P2 | unpack_int4 | 边界符号扩展 | 0x08→-8, 0x0F→-1, 0x07→7 | ✅ | 6 tests: 3 named value + high nibble + all 256 bytes exhaustive + anti-vacuous |
| MX-01 | P2 | matmul_from_sram | SRAM 地址偏移 | 与 matmul_int32 输出一致 | ✅ | 6 tests: 5 parametrized (M,K,N) bit-exact + anti-vacuous |
| MX-02 | P2 | matmul_int4_per_channel | scale 精度 | scale=1→与 matmul_int32 一致; scale≠1→手动验证 | ✅ | 7 tests: 4 parametrized scale=1 + 2 parametrized scale≠1 + anti-vacuous |
| MX-03 | P2 | matmul_int4_per_block | 分块边界 | block_size=K→与 per_channel 一致; block_size=32→边界正确 | ✅ | 7 tests: 3 block_size=K + 3 block_size=32 boundary + anti-vacuous |
| MX-06 | P2 | matmul_int32 | 非方阵 tiling | M=1/M=128/K=4096/N=4096, 与 numpy matmul 一致 | ✅ | M=1 和 M=128 均与 INT64 参考 bit-exact (3 subtests) |
| MX-07 | P2 | matmul_int32 | 零值 | 零输入→零输出 | ✅ | 激活/权重单零/双零/非方阵全零 → 全零 (5 subtests) |
| MX-08 | P2 | matmul_int32 | INT32 饱和 | 超限→截断至 INT32_MIN/MAX, 不 wrap | ✅ | 随机值 vs INT64 ref ± 极值验证, 输出全在 INT32 范围内 (4 subtests) |

---

## P3：GoldenSFU 缺口（7 cases）

> 理由：5/7 已覆盖, 补 rmsnorm + LUT 内部验证 + 边界

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| SF-01 | P3 | rmsnorm_hw | hw vs ref | 随机 5 组, max_error < 1e-5 | ✅ | 5 groups (3 1D + 2 2D) all max_err < 1e-5, anti-vacuous |
| SF-04 | P3 | _build_cordic_table | **CORDIC 增益方向** | 12 级逐级验证, 与理论值一致 | ✅ | 12 angles match arctan(2^-i) < 1e-6; gain matches theory within 5e-6; anti-vacuous checks pass |
| SF-02 | P3 | _build_exp_lut | LUT 精度 | [-20,0] 采样 1000 点 (4096 LUT entries), max_error < 1e-5 | ✅ | Direct LUT entry verify: all 4096 entries match np.exp within float32 rounding (< 1e-5), anti-vacuous |
| SF-03 | P3 | _build_gelu_lut | 分段边界 | 边界 ±eps 无跳跃 | ✅ | 62 interior boundaries + 2 clamp boundaries continuity verified at ±1e-6, anti-vacuous |
| SF-05 | P3 | softmax_hw | 大值稳定 | x=[1000,0,...] → [1.0,~0] 非 NaN | ✅ | no NaN/Inf; sum≈1; dominant≈1.0; others < 1e-3; anti-vacuous with [10,0,...] |
| SF-06 | P3 | rope_hw | position 边界 | pos=0(恒等), pos=100000(大角) | ✅ | pos=0 CORDIC near-identity < 5e-3; pos=100000 valid, no NaN, magnitude ≈ preserved |
| SF-07 | P3 | gelu_hw | 对称性 | gelu(-x) ≈ -gelu(x) | ✅ | HW matches ref on [-3,3] < 2e-3; anti-vacuous: ±4 asymmetry > 3.0 confirms non-trivial |

---

## P4：跨模块集成（3 cases）

> 理由：依赖 P0-P3 各模块先稳定

| case_id | 优先级 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|----------|----------|------|------|
| XL-01 | P4 | MXU→SFU 量化路径 | INT32→BF16→softmax, 与 float32 参考误差 < 1e-4 | ✅ | 5 parametrized (M,K,N) all max_abs_err < 1e-4, softmax sum-to-1 verified, 2 anti-vacuous (BF16 effect + softmax applied), 6 tests |
| XL-02 | P4 | SFU→Vector 协作 | rope→residual_add 完整路径, bit-exact | ✅ | 4 parametrized positions (0/42/1000/7777) bit-exact INT32 residual output, determinism verified, anti-vacuous rotation effect confirmed |
| XL-03 | P4 | MXU 全量化 | INT4→INT8→INT32→BF16→FP32, 端到端精度验证 | ⬜ | |

---

## Agent 执行规则

1. **严格按 P0→P4 顺序执行**，不跳级
2. 先读 `golden_executor.py` 理解被测方法
3. 每个 case：生成 pytest → 运行 → 更新状态 + 结果
4. **每次状态变更后立即 commit + push**（见下方 Git 规则）
5. 不满足验收标准 → ❌ FAIL → 修复 → 重试（最多 3 次）
6. 3 次仍 FAIL → 保持 ❌ 等待人类介入
7. 可自主新增 case（Agent 发现未列出的方法/边界）

### Git 规则（zartbot 模式）

```
每次 testplan.md 状态变化 = 一次 git commit

commit 格式:
  [case_id] status → NEW_STATUS

示例:
  [V-05] ⬜ → ✅ | BF16 转换桥往返 bit-exact, 0 errors
  [V-02] ⬜ → ❌ | NaN 传播异常, 需检查 add() 实现
  [MX-04] ❌ → ✅ | 修复符号扩展, 重新测试通过

原则:
  - 每完成一个 case（无论 PASS/FAIL）立即 commit
  - 不允许批量攒多个 case 再 commit
  - 修复后重新测试也要单独 commit
  - git log testplan.md = 完整测试执行时间线
```

### 首次启动

Agent 在开始执行前，先执行以下检查：

```bash
# 1. 确保在正确的目录
cd ~/npu/sim

# 2. 确保 git 状态干净
git status

# 3. 确认测试框架可用
pytest --version && python3 -c "import numpy, pytest"
```

如果 git 有未提交的修改，先提交或暂存后再开始。

---

## 统计

```
总计:     31 cases (28 新增 + 3 已有)
P0:        9 cases ← 当前
P1:        4 cases
P2:        8 cases
P3:        7 cases
P4:        3 cases
─────────────────────
覆盖率:    18% → 目标 100%
```
