# SoC Performance Report — Qwen2.5-3B blk.0 RTL Simulation Proxy

> **Test**: `cocotb_bridge.test_qwen_blk0` — 17 ops Qwen2.5-3B blk.0 full-chain
> **RTL**: CaduceusCore SoC (Ibex RISC-V + MXU/SFU/Vector/DMA/Crossbar)
> **Log**: `sim/regression/qwen_blk0.log`
> **Cycle JSON**: `func_model_cycles.json`
> **Date**: 2026-07-02
> **Tool**: Synopsys VCS V-2023.12-SP2

---

## 1. Summary

| Metric | Value |
|--------|-------|
| Ops total | 17 |
| Ops PASS | **17/17** |
| Simulation end time | **195,843.50 ns** |
| Measured per-op total_cycles | **99,565 cycles** |

The per-op total (`99,565 cycles`) is the sum of each op's measured RTL cycle count from `bridge.run_step()`. It excludes the SoC warm-up / firmware boot phase visible in the full simulation time.

> **Important**: These numbers are a **simulation proxy**, not real silicon performance. Large MMUL ops use a single-tile workaround because the current 64 KB weight buffer cannot hold full Qwen weights, so their cycle counts are far lower than a full-matrix implementation.

---

## 2. Per-Op Cycle Counts

Cycle counts are measured from CMD.START to STATUS.DONE plus a `store_wait` drain window.

| Op | Name | Dimensions | RTL cycles |
|:--:|------|-----------:|-----------:|
| 00 | RMSNORM pre-attn | elements=2560 | 10,766 |
| 01 | Q_proj MMUL | M=1 K=2560 N=4096 | 284 |
| 02 | K_proj MMUL | M=1 K=2560 N=256 | 284 |
| 03 | V_proj MMUL | M=1 K=2560 N=256 | 284 |
| 04 | ROPE | q=4096 k=256 | 11,404 |
| 05 | attn_score MMUL | M=32 K=128 N=2 | 631 |
| 06 | attn_softmax SOFTMAX | elements=64 | 860 |
| 07 | attn_weight MMUL | M=32 K=2 N=128 | 492 |
| 08 | O_proj MMUL | M=1 K=4096 N=2560 | 284 |
| 09 | VRESID | elements=2560 | 5,928 |
| 10 | RMSNORM post-attn | elements=2560 | 10,766 |
| 11 | gate MMUL | M=1 K=2560 N=9728 | 284 |
| 12 | up MMUL | M=1 K=2560 N=9728 | 284 |
| 13 | SILU | elements=9728 | 29,698 |
| 14 | VMUL gate*up | elements=9728 | 21,104 |
| 15 | down MMUL | M=1 K=9728 N=2560 | 284 |
| 16 | VRESID | elements=2560 | 5,928 |
| **Total** | | | **99,565** |

### Single-Tile Workaround Note

MMUL ops 01/02/03/08/11/12/15 have weight files larger than the 64 KB weight buffer. The test falls back to a **single 64×64 tile** (`min(K,64) × min(N,64)`), so their RTL cycles represent only one tile of compute. Consequently, the per-op total is much smaller than a full-model prediction.

---

## 3. TTFT/TPS Simulation Proxy

All metrics below are derived from the measured per-op cycle total and scaled with simple assumptions. They are labeled **proxy** to avoid confusing them with silicon projections.

### Formulas Applied

```
blk0_latency_ms   = total_cycles / 1_000_000        # 1 GHz clock
decode_latency_ms = blk0_latency_ms * 28            # 28 blocks, sequential
TPS_proxy         = 1000 / decode_latency_ms        # tok/s
ttft_proxy_ms     = blk0_latency_ms * 28 * 128      # seq_len=128 prefill proxy
mem_bw_MBps       = 140 / (blk0_latency_ms / 1000)  # blk.0 weights only
```

### Computed Values

| Metric | Value | Unit |
|--------|------:|------|
| total_cycles | **99,565** | cycles |
| blk0_latency_ms | **0.100** | ms |
| decode_latency_ms (28 blocks) | **2.788** | ms |
| **TPS_proxy** | **358.7** | tok/s |
| **TTFT_proxy** (seq=128) | **356.8** | ms |
| mem_bw_MBps | **1,406,118** | MB/s |

### Sanity Check

The raw `mem_bw_MBps` far exceeds the LPDDR5-6400 64-bit ceiling of ~51,200 MB/s. This is expected: the single-tile workaround removes most of the DRAM-weight traffic, so the proxy throughput is artificially high. The proxy numbers are useful only for relative RTL regression tracking, **not** for architectural performance claims.

---

## 4. Explicit Assumptions

The following six assumptions apply to the TTFT/TPS proxy calculation:

1. **Clock frequency is 1 GHz (simulation timing, not silicon).**
   All cycle-to-ms conversions use a 1 ns cycle time.

2. **All 28 transformer blocks have the same workload as blk.0.**
   Full-model latency is blk.0 latency multiplied by 28.

3. **No inter-block pipelining or parallelism.**
   Blocks are assumed to execute sequentially, giving a conservative upper bound.

4. **DRAM behavior is idealized in the simulation.**
   The RTL DRAM model does not model real LPDDR5-6400 timing or contention.

5. **Weights are pre-loaded into SRAM.**
   The measured cycles do not include DRAM-to-SRAM weight fetch time.

6. **seq_len = 128 is used for the prefill/TTFT proxy.**
   TTFT is approximated as 128 × single-block latency × 28 blocks.

---

## 5. Methodology Notes

- `cocotb_bridge.py` was modified to write `qwen_blk0_cycles.json` directly from `test_qwen_blk0`, independent of the cocotb logger level. This captures per-op cycles even when the console log is filtered to WARNING+.
- The simulation was re-run with `.omo/scripts/soc-verification-run.sh run_e2e_blk0 0` (CLEAN=0) so the existing compiled `simv_soc_cocotb` was reused and only the Python test code changed.
- No RTL source files were modified.

---

*Report generated from `qwen_blk0.log` and `func_model_cycles.json`.*
