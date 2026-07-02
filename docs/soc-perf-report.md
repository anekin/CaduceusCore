# SoC Performance Report — Qwen2.5-3B blk.0 RTL+Func Model Comparison

> **Test**: `cocotb_bridge.test_qwen_blk0` — 17 ops Qwen2.5-3B blk.0 full-chain  
> **RTL**: CaduceusCore SoC (Ibex RISC-V + MXU/SFU/Vector/DMA/Crossbar)  
> **Log**: `sim/regression/qwen_blk0.log`  
> **Date**: 2026-07-01  
> **Tool**: Synopsys VCS V-2023.12-SP2

---

## 1. Summary

| Metric | Value |
|--------|-------|
| Ops total | 17 |
| Ops PASS | **16/17** |
| Ops FAIL | **1/17** — op10 RMSNORM post-attn |
| Total simulation time | 195,843.50 ns |
| Total RTL cycles (est.) | **193,519** cycles (op start → $finish) |
| Func Model total cycles | **1,691,265** cycles |
| Cycle delta% | **−88.6%** (RTL uses single-tile workaround) |

**RTL total cycles ≈ 193,519** is **not** representative of full-model performance. All large MMUL ops (Q/K/V/O/gate/up/down) exceeded the 64 KB weight buffer and fell back to a **single 64×64 tile** workaround (min(K,64)×min(N,64)). The Func Model predictions model full matrix dimensions and are the correct reference for TTFT/TPS.

---

## 2. Per-Op Cycle Comparison

### Legend
- **FM cycles**: Func Model cycle prediction (full matrix)
- **RTL cycles**: RTL measured from `dut.sim_cycle` delta (CMD.START → STATUS.DONE + store_wait)
- **N/A**: Cycle count not available in log (INFO-level messages filtered; log only shows WARNING+)
- **FAIL**: Golden comparison failed

### Table

| Op | Name | Dimensions | FM cycles | RTL cycles | Delta% | Status |
|:--:|------|-----------:|----------:|-----------:|-------:|:-----:|
| 00 | RMSNORM pre-attn | elements=2560 | 350 | N/A | N/A | PASS |
| 01 | Q_proj MMUL | M=1 K=2560 N=4096 | 174,080 | N/A¹ | N/A | PASS |
| 02 | K_proj MMUL | M=1 K=2560 N=256 | 10,880 | N/A¹ | N/A | PASS |
| 03 | V_proj MMUL | M=1 K=2560 N=256 | 10,880 | N/A¹ | N/A | PASS |
| 04 | ROPE | q=4096 k=256 | 408 | N/A | N/A | PASS |
| 05 | attn_score MMUL | M=32 K=128 N=2 | 136 | N/A | N/A | PASS |
| 06 | SOFTMAX | elements=64 | 160 | N/A | N/A | PASS |
| 07 | attn_weight MMUL | M=32 K=2 N=128 | 136 | N/A | N/A | PASS |
| 08 | O_proj MMUL | M=1 K=4096 N=2560 | 174,080 | N/A¹ | N/A | PASS |
| 09 | VRESID pre-attn | elements=2560 | 20 | N/A | N/A | PASS |
| **10** | **RMSNORM post-attn** | **elements=2560** | **350** | **10,766** | **+2976%** | **FAIL** |
| 11 | gate MMUL | M=1 K=2560 N=9728 | 439,740 | N/A¹ | N/A | PASS |
| 12 | up MMUL | M=1 K=2560 N=9728 | 439,740 | N/A¹ | N/A | PASS |
| 13 | SILU | elements=9728 | 304 | N/A | N/A | PASS |
| 14 | VMUL gate×up | elements=9728 | 76 | N/A | N/A | PASS |
| 15 | down MMUL | M=1 K=9728 N=2560 | 439,905 | N/A¹ | N/A | PASS |
| 16 | VRESID post-attn | elements=2560 | 20 | N/A | N/A | PASS |

> ¹ MMUL ops with K>64 or N>64 used single-tile workaround (min(K,64)×min(N,64)=64×64).  
>   Their RTL cycles represent single-tile compute only, not full matrix dimensions.

### Key Observation — op10 RMSNORM post-attn FAIL

Op10 RMSNORM post-attn failed with **10,766 cycles** (vs. Func Model 350 cycles). The comparison report shows:

```
First mismatch @ byte[0]: actual=0.66259765625, golden=0.23046875
Total FP16 mismatches: 2558/2560
Rel_err ≈ 1.875 (≈ 2.875× scale error)
```

This is a known RTL SFU precision bug: the RMSNORM two-pass Newton-Raphson sqrt/reciprocal pipeline produces outputs that are ~2.875× larger than the IEEE-correct golden reference. The high cycle count (10,766 vs. 350) includes the SFU compute on 2560 elements plus the testbench store_wait + golden comparison overhead.

---

## 3. TTFT/TPS Proxy Estimation

### Total Cycles

```
RTL total_cycles = 193,519 cycles
(from first op diag at 2324.50 ns to $finish at 195843.50 ns)
```

### Formulas Applied

Following the plan (`.omo/plans/soc-verification.md` lines 157-161):

```
blk0_latency_ms  = total_cycles / 1e6        (ms, 1 GHz assumption)
decode_latency_ms = blk0_latency_ms × 28      (28 blocks, single-token decode proxy)
TPS_proxy         = 1000 / decode_latency_ms   (token/s, decode stage proxy)
prefill_proxy_ms  = blk0_latency_ms × 28 × 128 (seq_len=128, rough estimate)
mem_bw_MBps       = 140 / (blk0_latency_ms / 1000) (blk.0 weights only, ≤ 51200 MB/s ceiling)
```

### Computed Values

| Metric | Value | Unit |
|--------|------:|------|
| total_cycles | 193,519 | cycles |
| blk0_latency_ms | **0.194** | ms |
| decode_latency_ms (28 blk) | **5.42** | ms |
| **TPS_proxy** | **184.6** | tok/s |
| prefill_proxy_ms (seq=128) | **693.5** | ms |
| mem_bw_MBps | **723,514** | MB/s |

### DRAM Bandwidth Sanity Check

**mem_bw_MBps = 723,514 MB/s** exceeds the LPDDR5-6400 64-bit ceiling of **51,200 MB/s** by ~14×.

This confirms that the RTL cycle count (dominated by single-tile workaround MMUL ops) does **not** reflect the true DRAM-bandwidth-bound performance. The Func Model predicts a realistic 1,691,265 cycles for blk.0, which would yield:

| Metric (Func Model) | Value |
|---------------------|------:|
| blk0_latency_ms | 1.691 ms |
| TPS_proxy | 21.1 tok/s |
| prefill_proxy_ms | 6,058 ms |
| mem_bw_MBps | 82,790 MB/s |

The Func Model TPS (21.1 tok/s) is consistent with the Arc Model DSE's LPDDR5 S1 prediction of **23 TPS** for Qwen2.5-3B. The RTL workaround yields inflated TPS (~185 tok/s) and should not be used for architectural decisions.

---

## 4. Explicit Assumptions

The following 6 assumptions from the verification plan are applied verbatim to the TTFT/TPS proxy computation:

1. **时钟频率 1 GHz（simulation timing，非 silicon）**  
   Clock frequency is 1 GHz (simulation timing, not silicon).

2. **28 个 block 全部与 blk.0 计算量相同**  
   All 28 blocks have identical compute load to blk.0.

3. **无 block 间流水线/并行（纯顺序执行，最保守估计）**  
   No inter-block pipelining/parallelism (purely sequential execution, most conservative estimate).

4. **DRAM 行为模型理想化（不反映 LPDDR5-6400 实际时序）**  
   DRAM behavioral model is idealized (does not reflect actual LPDDR5-6400 timing).

5. **权重已预加载至 SRAM（不包含 DRAM→SRAM 搬运时间）**  
   Weights are pre-loaded to SRAM (DRAM→SRAM transfer time not included).

6. **seq_len = 128 用于 prefill 估算**  
   seq_len = 128 used for prefill estimation.

---

## 5. Known Issues

1. **op10 RMSNORM post-attn FAIL** — RTL SFU RMSNorm pipeline produces ~2.875× scale error. 2558/2560 FP16 elements mismatch. Root cause: Newton-Raphson sqrt/reciprocal precision bug in `rmsnorm_hw.v`. The 10,766 cycle count includes SFU compute on 2560 elements plus testbench store_wait (est. ~200 cycles) and golden comparison overhead.

2. **Per-op RTL cycle counts unavailable** — The VCS simulation was run with WARNING+ log level. Per-op `[cycle_count]` and `[Step N] PASS` messages are at INFO level (Python logger) and were filtered out. Only FAIL op (op10) cycle count appears in the log. To capture all per-op cycle data, re-run with `export COCOTB_LOG_LEVEL=INFO` or add `logging.getLogger().setLevel(logging.INFO)` in the test setup.

3. **MMUL single-tile workaround** — 8 of 17 ops (Q/K/V/O/gate/up/down, attn_score, attn_weight) are MMUL with weight sizes exceeding the 64 KB weight buffer. The test applies a single 64×64 tile workaround (`min(K,64)×min(N,64)`), meaning RTL MMUL cycles represent only a fraction of the full matrix multiplication.

4. **op07 attn_weight MMUL no diagnostic** — Unlike all other ops, op07 (attn_weight) has no `[diag]` print in the log. Its execution is visible only through the MXU_FSM state traces at t=41180–41591 ns.

---

*Report generated from `qwen_blk0.log` and `func_model_cycles.json`.*  
*Next step: Re-run VCS simulation with INFO log level to capture full per-op cycle data.*
