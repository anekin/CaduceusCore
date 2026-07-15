# Phase 6 RTL Verification — Execution Log

## 2026-07-15 Session start

- Plan selected: `.omo/plans/phase6-rtl-verification.md`
- Work ID: `phase6-rtl-verification-ceb68124`
- Session: `opencode:ses_0b8aee0b1ffeX5EJesZTZa2xWz`
- Prior review verdicts: Momus YES-WITH-NOTES; Metis READY-WITH-NOTES

### Inherited constraints

1. **VCS-only, no FPGA**. All RTL work on sz0001 via VCS V-2023.12-SP2.
2. **Spike-first, Ibex-last**. Spike runs are for debug only; review gates require Ibex evidence.
3. **Pre-Wave Gate fixed**. Use `bash sim/regression/run_p0_full_rtl.sh` / `bash sim/regression/run_ibex_full_rtl.sh`, not Makefile targets.
4. **FM-2 chain test missing**. `pytest -k "chain"` currently has no match; must add chain test as part of FM-2 scope.
5. **FM-1 / W4 circular dep unresolved**. FM-1 same-engine calibration (±10% vs Phase 5 P2) can proceed; cross-engine validation deferred to W4-PERF-13..P16.
6. **F2 baseline contradiction**. Regression baseline section says pytest 210+, FM-SOC 33/33, MXU 9/9, SFU 319/319, Vector 63/63; success criteria table says pytest >=700, SFU 526/537, Vector 64/64. Must reconcile before Final Wave.
7. **Evidence schema**. Long VCS evidence should include `simulator: spike|ibex`, `case_id`, `status`, `cycles`, `cos_sim`, `timestamp`, `commit`.

### First parallel batch (Python-only, independent)

- FM-1: cross-engine pipeline timing model
- FM-2: CV chain test (add `-k "chain"` test)
- FM-3: weight streaming timing field
- 6b: L35 drift Q8_0 control experiment

## 2026-07-15 6b: Q8_0 control experiment — blocked by missing asset

- Task: rerun W1.6 36-layer Func Model signoff with Qwen2.5-3B Q8_0 GGUF.
- Checked local (`~/models`) and sz0001 (`zhengs@192.168.0.11:~/models`); only
  `qwen2.5-3b-instruct-q4_k_m.gguf` exists.
- No HuggingFace network access from this environment (`Network is unreachable`),
  so the official Q8_0 GGUF cannot be downloaded.
- Re-quantizing the existing Q4_K_M file to Q8_0 with
  `llama-quantize --allow-requantize` is technically possible, but it would be a
  re-quantization of an already-quantized model; it would not isolate the Q4_K_M
  quantization error and therefore is not a valid control for the L35 drift.
- Evidence file: `build/evidence/w1-6b-q8o.txt` documents the missing asset with
  36 placeholder per-layer entries and the Phase 5 L35 baseline
  (`0.998278 ± 0.001`).
- Conclusion: Q8_0 root-cause confirmation is blocked pending the official Q8_0
  GGUF. Once the asset is available, run
  `python3 scripts/run_w1_6b_q8o_control.py` to compare L35 cosine similarity
  against the Phase 5 baseline.

### 2026-07-15 FM-3: Weight streaming tile-level double-buffering timing

- **Status**: IMPLEMENTED
- **Files**: `sim/models/dma.py`, `sim/timing/dashboard.py`, `sim/timing/benchmark.py`, `sim/timing/tests/test_dashboard.py`, `sim/timing/tests/test_tile_double_buffer.py`
- **Method**: Added `DMAModel.estimate_tile_double_buffer_overlap()` that models per-K-tile cold start + N-tile double-buffering overlap + K-tile reload stall.
- **Formula**: For each GEMM (M,K,N) with tile size (H,W):
  - K_tiles = ceil(K/H), N_tiles = ceil(N/W)
  - Per K-tile: first N-tile cold (DMA+compute), remaining N_tiles-1 overlap via max(DMA, compute)
  - K-tile reload stall: DMA(act + first weight tile of next K-tile) - remaining_compute of last N-tile
  - overlap_ratio = 1 - DMA_on_critical_path / total_DMA
- **Output field**: `weight_streaming_overlap_ratio` in benchmark JSON (float in [0,1]).
- **Qwen2.5-3B result**: 0.98 (DMA almost fully hidden by compute at 64x64 tile granularity with 51.2 GB/s BW).
- **Validation deferred**: Cross-validation against W4 PERF-09..P12 VCS data is deferred.
- **Test coverage**: 9 unit tests covering Q_proj PERF-09 config, all 7 transformer matmuls, edge cases (zero dims, single tile, infinite BW), monotonicity, and prefill vs decode comparison.

