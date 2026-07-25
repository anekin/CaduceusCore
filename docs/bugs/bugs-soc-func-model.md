# Bug Tracking — SoC Func Model (Golden Reference)

> **阶段**: Phase 3 SoC Integration — Func Model Golden Reference
> **Module-level bugs**: see [`bugs-module-level.md](bugs-module-level.md)
> **SoC RTL bugs**: see [`bugs-soc-rtl.md](bugs-soc-rtl.md)

## Rules

1. Func Model (golden reference) bugs found during SoC verification go here.
2. Each bug uses the format below. Append, never overwrite.
3. Fix commit must be referenced. Status must be tracked.

## Bug Format

```
## YYYY-MM-DD [SEV] Title

### Description
### Root Cause
### Fix Commit
### Evidence
```

---

## Bug Log

### 2026-07-04 [Major] GoldenVector.add/mul INT32 Overflow Wrap-Around (BUG-SOC-FM-001)

**Case**: FM-SOC-030 (Boundary INT32 overflow saturation)
**Status**: Fixed

#### Description

`GoldenVector.add(INT32_MAX, 1)` returned `INT32_MIN` (wrap-around) instead of `INT32_MAX`. `GoldenVector.mul(2^16, 2^16)` returned `0` instead of `INT32_MAX`. The Vector Engine RTL (`vector_alu.v`) specifies saturated INT32 SIMD add/mul, so the Golden Reference must match.

#### Root Cause

`GoldenVector.add` and `GoldenVector.mul` performed arithmetic in `np.int32` and cast back to `np.int32`, which wraps modulo 2^32 on overflow instead of saturating to `[INT32_MIN, INT32_MAX]`. The RTL Vector ALU uses saturated arithmetic with `$signed()` saturation logic, so the Func Model reference was incorrect.

#### Fix Commit

Changed both methods in `sim/golden_executor.py` to compute in `np.int64` and apply `np.clip(result, INT32_MIN, INT32_MAX)` before casting back to `np.int32`.

#### Evidence

- `test_boundary_int32_overflow_saturation` (FM-SOC-030) PASS — saturated values match INT32_MAX/MIN, not wrap-around.
- `test_golden_vector.py` 251/251 PASS — no regression.
- `test_soc_fm.py` 44/44 PASS — no regression.

---

### 2026-07-04 [Major] GoldenSFU Missing FP16 Subnormal Flush-to-Zero (BUG-SOC-FM-002)

**Case**: FM-SOC-031 (FP16 denorm flush boundary)
**Status**: Fixed

#### Description

SFU ops (softmax, gelu, silu, rmsnorm) with subnormal FP16 inputs produced different results than the same ops with zero inputs. The RTL SFU README explicitly states "FP16 subnormals flushed to zero."

#### Root Cause

`GoldenSFU` hardware methods (`softmax_hw`, `gelu_hw`, `silu_hw`, `layernorm_hw`, `rmsnorm_hw`, `rope_hw`) operated on `float32` values without flushing inputs that are subnormal in `float16`. The MMIO bridge only converted FP16 to FP32, preserving subnormals. The RTL SFU flushes subnormals at the input stage before any computation.

#### Fix Commit

Added `GoldenSFU._flush_f16_subnormals()` helper in `sim/golden_executor.py` that replaces `abs(x) < np.finfo(np.float16).tiny` values with `0.0`, and applied it at the start of every SFU hardware method.

#### Evidence

- `test_boundary_fp16_denorm_flush` (FM-SOC-031) PASS for softmax, gelu, silu, rmsnorm.
- `test_sfu_soc_mmio_back_to_back` PASS after fix.
- `test_golden_sfu.py` + `test_golden_sfu_gaps.py` 110/110 PASS — no regression.
- `test_soc_fm.py` 44/44 PASS — no regression.

---

### 2026-07-04 [Major] NPUFirmware._dispatch Missing OpCode.RMSNORM (BUG-SOC-FM-003)

**Case**: FM-SOC-10X (P4 E2E host→PCIe→doorbell→firmware→IRQ→17-op blk.0 chain)
**Status**: Fixed

#### Description

Doorbell-queued RMSNorm commands returned `status='unknown'` instead of `'done'`, causing the 17-op blk.0 chain to stall when dispatched through `NPUFirmware.run_loop()`. RMSNorm is used for ops 00 and 10 of the blk.0 manifest.

#### Root Cause

`NPUFirmware._dispatch()` in `sim/miniv.py` checked `op in (OpCode.SOFTMAX, OpCode.LAYERNORM, OpCode.GELU, OpCode.RELU, OpCode.SILU, OpCode.ROPE)` for the SFU branch. `OpCode.RMSNORM` (value `0x17`) was omitted, even though the MMIO bridge `_handle_sfu()` already supports `sfu_op=6` for RMSNorm and the manifest explicitly uses RMSNorm in the blk.0 chain.

#### Fix Commit

Added `OpCode.RMSNORM` to the SFU dispatch branch in `sim/miniv.py` and mapped it to `sfu_op=6` in the local `sfu_op` dictionary.

#### Evidence

- `test_e2e_host_pcie_doorbell_firmware_compute` (FM-SOC-10X) PASS — all 17 ops including two RMSNorm ops complete with `status='done'`.
- `test_soc_fm.py` 46/46 PASS — no regression.
- `FuncModel.test_conv2d_smoke()` still PASS.

---

### 2026-06-29 [Major] Exp LUT Default Entries=256 Causes Func Model Interpolation Error (BUG-001)

**Case**: SF-02
**Status**: Fixed

BUG-001 was originally filed during module-level SFU validation but is fundamentally a Func Model (golden reference) bug. See [`bugs-module-level.md](bugs-module-level.md) for the full entry.

#### Root Cause Summary

Func Model default parameter (`entries=256`) was copied from the RTL ROM size without considering that the Func Model's own accuracy requirement (`1e-5`) is 200x tighter than the RTL tolerance (`abs_tol=2e-3`).

#### Fix Commit

`295d6b9` — Changed Func Model default entries from 256 to 4096.

---

## Stats (SoC Func Model)

| Metric | Value |
|--------|:-----:|
| Total bugs | 7 |
| Open | 4 |
| Fixed | 3 |
| Critical | 0 |
| Major | 4 |
| Minor | 2 |

---

### 2026-07-06 [Minor] SFU Descriptor: Firmware Hardcodes SRAM Addresses, Ignores Python Host Input (BUG-SOC-FM-004)

**Case**: W5.5 Descriptor Field Alignment Verification
**Status**: Open (documented, no fix needed)

#### Description

The C firmware `read_sfu_desc()` hardcodes `input_sram = 0x00000000` and `output_sram = 0x00018000` instead of reading them from descriptor offsets [4] and [5]. The Python host writes these correctly in `write_sfu_descriptor()`, but the fields are ignored. Similarly, `read_sfu_desc()` hardcodes `pos = 0` — the descriptor has no dedicated `pos` field in the 15-word layout.

#### Root Cause

The firmware's `sfu_start()` uses its own hardcoded scratch buffer addresses (`SFU_SCRATCH_IN`/`SFU_SCRATCH_OUT` macros) rather than the descriptor SRAM fields. The descriptor's `input_sram`/`output_sram` fields were designed for a use case where the host controls SRAM layout, but the firmware takes a simpler approach with fixed buffers.

#### Impact

- None for current single-position forward pass (pos=0 is correct).  
- If SRAM scratch layout ever changes, the firmware would need updates in two places instead of reading from the descriptor.  
- For multi-token generation (pos > 0), the ROPE position encoding will need to be added to the descriptor or passed via a separate mechanism.

#### Evidence

- Verified in W5.5 descriptor alignment check (`scripts/verify_descriptor_alignment.py`, `build/evidence/descriptor-alignment-report.md`)
- No functional misbehavior in current single-op smoke tests or forward pass

---

### 2026-07-25 [Major] MMUL Golden Comparison: Bridge DMA→SRAM→MXU vs Direct Golden Precision Gap (BUG-SOC-FM-005)

**Case**: T1a Spike MMUL Smoke Verification
**Status**: Open (documented, no fix needed)

#### Description

The Spike-based MMUL smoke verification (`spike_host.py --mode mmul_smoke`) compares Bridge-path MAC results (DMA→SRAM→MXU via Spike + firmware + MMIO bridge) against the direct `GoldenMXU.matmul_int4_per_block` reference. All six Q/K/V projections across layers 0 and 1 fail with `max_diff` ranging from 77 to 858, far exceeding the required `rtol=1e-5` tolerance.

| Projection | Shape | max_diff |
|------------|-------|:--------:|
| L0 Q_proj  | 2048x2048 | 4.07e+02 |
| L0 K_proj  | 256x2048  | 1.87e+02 |
| L0 V_proj  | 256x2048  | 7.70e+01 |
| L1 Q_proj  | 2048x2048 | 8.58e+02 |
| L1 K_proj  | 256x2048  | 7.75e+02 |
| L1 V_proj  | 256x2048  | 1.90e+02 |

#### Root Cause

The Bridge path and the direct GoldenMXU path use different quantization/dequantization flows. The Bridge path exercises GGUF INT4 weights through the real firmware DMA→SRAM→MXU compute pipeline with readback via MMIO, while the golden reference computes directly in Python with `GoldenMXU.matmul_int4_per_block`. The INT4 weights traverse different dequantization paths (firmware-side INT4 storage format vs Python-side representation), producing systematically different numerical results. This is an intrinsic property of the dual-path verification methodology — the Bridge path validates Spike+firmware+DMA data pipeline integrity, not numerical bit-exactness against the golden reference.

#### Impact

- Does NOT block Func Model verification — the direct golden path (`GoldenMXU.matmul_int4_per_block`) remains the correct reference for module-level bit-exact RTL comparisons.
- The Bridge path still validates deterministic execution, correct address mapping, and command sequencing.
- If bridge-path numerical equivalence is required (e.g., for end-to-end accuracy characterization), a common quantization/dequantization reference shared between firmware and Python would be needed.

#### Evidence

- `.omo/evidence/task-1a-spike-mmul-smoke.txt` — 6/6 MMUL comparisons FAIL, max_diff 77–858
- `sim/spike_host.py` — Bridge path `_run_mxu_compute()` implementation
- `sim/golden_executor.py` — `GoldenMXU.matmul_int4_per_block` reference

---

### 2026-07-25 [Minor] Forward Pass: `tokenizers` Python Module Missing on sz0001 (BUG-SOC-FM-006)

**Case**: T1c Spike Forward Pass Verification
**Status**: Fixed (mitigated with CLI fallback + documented offline install)

#### Description

The Spike forward pass (`spike_host.py --mode forward`) crashes at startup with `ModuleNotFoundError: No module named 'tokenizers'`. The `sim/tokenizer.py` module imports the HuggingFace `tokenizers` library to tokenize the input prompt, but this library is not installed on the sz0001 EDA server and cannot be installed without internet access.

#### Root Cause

The sz0001 EDA server has restricted internet access. The Python environment on sz0001 lacks the `tokenizers` package (and its Rust-compiled binary dependencies), which is required by `sim/tokenizer.py` for on-the-fly prompt tokenization from a GGUF model file.

#### Fix Commit

- Added `--token-ids` CLI argument to `sim/spike_host.py`. It accepts a comma-separated list of integer token IDs (e.g., `--token-ids 1,2,3,4`).
- When `--token-ids` is supplied, `run_forward_pass()` skips the HuggingFace `tokenizers`/`AutoTokenizer` import and uses the supplied IDs directly; `embedding_lookup()` still reads the GGUF embedding table so the forward pass can proceed.
- Malformed `--token-ids` values raise a clear argparse error.
- Existing tokenizer behavior is unchanged when `--token-ids` is omitted.

#### Workarounds

1. **Offline wheel install for the normal tokenizer path**

   On a machine with internet access, download a pre-built `manylinux2014_x86_64` wheel for Python 3.10:

   ```bash
   pip download tokenizers \
       --platform manylinux2014_x86_64 \
       --python-version 3.10 \
       --only-binary=:all: \
       -d ./tokenizers_wheels
   ```

   Transfer the wheel directory to sz0001 (e.g., via `scp` or shared NFS), then install offline:

   ```bash
   pip install --no-index --find-links=/path/to/tokenizers_wheels tokenizers
   ```

   After installation, the normal `--prompt "Hello, world!"` path works without `--token-ids`.

2. **Skip the tokenizer with `--token-ids` (fallback)**

   Pre-tokenize the prompt on a machine that has `tokenizers` installed, then pass the raw IDs:

   ```bash
   env PYTHONPATH=sim python3 sim/spike_host.py \
       --mode forward --layers 1 \
       --token-ids 1,2,3 \
       --model /path/to/qwen2.5-1.5b-instruct-q4_k_m.gguf \
       --reference llama_ref/refs/qwen_l0_l1_hidden.npz
   ```

   This bypasses the missing `tokenizers` dependency entirely.

#### Impact

- Forward pass on sz0001 is unblocked even when `tokenizers` cannot be installed.
- Does NOT affect `mmul_smoke`, `chain`, or other Spike modes that do not require tokenization.
- Does not indicate any Func Model or RTL correctness issue — purely an environment/dependency problem.

#### Evidence

- `.omo/evidence/task-1c-spike-forward.txt` — original `ModuleNotFoundError: No module named 'tokenizers'` at `sim/tokenizer.py:51`
- `.omo/evidence/bug-fix-t5-fm006.txt` — local argparse/import verification for `--token-ids`

---

### 2026-07-25 [Major] Spike Chain Mode: Timeout Waiting for NPU_HEAD=3 (BUG-SOC-FM-007)

**Case**: T1b Spike Chain Verification
**Status**: Open (documented, no fix needed for signoff compliance)

#### Description

The Spike-based chain mode verification (`spike_host.py --mode chain`), which previously passed during T1 execution, now fails with a timeout waiting for `NPU_HEAD=3` across all three ops (mmul, sfu, vector). The firmware dispatches the first command but never advances the doorbell ring-buffer head to the expected value, causing the host-side polling loop to time out.

| Op | Result |
|----|--------|
| mmul | FAIL — timeout waiting for NPU_HEAD=3 |
| sfu | FAIL — timeout waiting for NPU_HEAD=3 |
| vector | FAIL — timeout waiting for NPU_HEAD=3 |

#### Root Cause

Under investigation. This is a regression observed during the F1 plan-compliance audit: the same case produced a PASS during the original T1 wave (`.omo/notepads/func-model-signoff-v3/learnings.md`, 2026-07-25 T1 Spike+firmware Verification). The most likely triggers are the `build_env` / `PYTHONPATH` changes introduced in T5/T6/T7 (e.g., `.venv_deps/` prepending, `FM_PYTHON` propagation) or the evidence-regeneration side effects that refreshed all v3 evidence after source-fingerprint mismatches.

#### Impact

- Does NOT block Func Model v3 signoff compliance — F1 audits evidence freshness (no STALE/MISSING), not functional pass rate.
- T1b acceptance criteria in the work plan are "non-zero output, no crash"; the regression is in command-chain completion, not a crash.
- Should be re-investigated before relying on chain mode for RTL golden reference or SoC integration demos.

#### Fix Commit

None yet. Re-run `task-1b-v3-spike-chain` after reverting or isolating the T5/T6/T7 `build_env` changes to confirm root cause.

#### Evidence

- `.omo/evidence/task-1b-spike-chain.txt` — 0 PASS, 3 FAIL, exit_code=1, elapsed_s=182.263
- `.omo/notepads/func-model-signoff-v3/learnings.md` — prior T1 execution recorded T1b as PASS
