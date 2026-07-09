# Func Model Golden Reference Tolerance Spec

**Purpose:** Define how RTL output is compared against the Func Model golden
reference for every NPU ISA opcode. RTL verification engineers can implement
the scoreboard / comparator directly from this document without reading the
Python golden model.

**Source files defining this spec:**
- `sim/engine/isa.py` — opcode definitions
- `rtl/sfu/README.md` — SFU tolerances and precision notes
- `rtl/vector/README.md` — Vector Engine tolerances and comparison modes
- `sim/golden_executor.py` — Func Model op implementations and dtype transitions
- `rtl/mxu/README.md` — MXU bit-exact verification results
- `scripts/compare_sfu.py` — inline comparator tolerances

---

## 1. Comparison Primitives

### 1.1 Bit-Exact

Two outputs match if every bit is identical. Used for:
- Integer data paths with no approximation (INT32 MXU, INT32 Vector ops, DMA).
- Non-overflow cases of INT32 saturating arithmetic.

### 1.2 Tolerance

Two FP16 outputs `a` (RTL) and `b` (golden) match if either:

```
|a - b| ≤ abs_tol
```

or

```
|a - b| / (|b| + eps) ≤ rel_tol      with eps = 1e-8
```

The default SFU/Vector FP16 comparator is `compare_sfu.py` with
`abs_tol = 2e-3`, `rel_tol = 1e-2`.

### 1.3 Bit-Exact with Saturation Exception

Applies to INT32 Vector `ADD`, `MUL`, `RESID`, and `MAX`/`SUM` reductions.
Non-overflow values must be bit-exact. If an input combination would overflow
INT32 and the RTL saturates correctly, the comparison is reported as a
"saturation exception" rather than a failure.

---

## 2. Per-Opcode Tolerance Table

| Opcode | Name | Output Type | Comparison | abs_tol | rel_tol | Notes |
|-------:|------|:-----------:|:----------:|:-------:|:-------:|:------|
| `0x00` | MMUL | INT32 | bit-exact | 0 | 0 | 64×64 broadcast MAC; saturation at INT32 boundary |
| `0x01` | SOFTMAX | FP16 | tolerance | 2e-3 | 1e-2 | LUT-based exp + iterative division |
| `0x02` | LAYERNORM | FP16 | tolerance | 2e-3 | 1e-2 | Fixed-point mean/var; subnormals flushed |
| `0x03` | GELU | FP16 | tolerance | 2e-3 | 1e-2 | 64-entry LUT; ~1.2% rel error at boundaries |
| `0x04` | RELU | FP16 | bit-exact | 0 | 0 | `max(0, x)`; no approximation in datapath |
| `0x05` | ROPE | FP16 | tolerance | 2e-3 | 1e-2 | 16-stage CORDIC Q18.14 fixed-point |
| `0x06` | SILU | FP16 | tolerance | 2e-3 | 1e-2 | Reuses exp LUT + Newton-Raphson reciprocal |
| `0x07` | MAXPOOL | FP16 | tolerance | 2e-3 | 1e-2 | 2×2 window max |
| `0x08` | AVGPOOL | FP16 | tolerance | 2e-3 | 1e-2 | 2×2 window mean |
| `0x09` | DMA_LD | bytes | bit-exact | 0 | 0 | DRAM → SRAM byte copy |
| `0x0A` | DMA_ST | bytes | bit-exact | 0 | 0 | SRAM → DRAM byte copy |
| `0x0B` | KV_LOAD | bytes | bit-exact | 0 | 0 | KV cache load |
| `0x0C` | KV_STORE | bytes | bit-exact | 0 | 0 | KV cache store |
| `0x0D` | BARRIER | — | N/A | — | — | No data output |
| `0x0E` | NOP | — | N/A | — | — | No data output |
| `0x0F` | VADD | INT32 | bit-exact* | 0 | 0 | *Saturating; non-overflow exact |
| `0x10` | VMUL | INT32 | bit-exact* | 0 | 0 | *Saturating; non-overflow exact |
| `0x11` | VRED_MAX | INT32 | bit-exact* | 0 | 0 | *Saturating; non-overflow exact |
| `0x12` | VRED_SUM | INT32 | bit-exact* | 0 | 0 | *INT64 intermediate; final INT32 saturation |
| `0x13` | VCONV | FP16 | tolerance | 2e-3 | 1e-2 | INT32 → FP16; saturates to ±65504 |
| `0x14` | VRESID | INT32 | bit-exact* | 0 | 0 | *Saturating; non-overflow exact |
| `0x15` | DMA_LDD | bytes | bit-exact | 0 | 0 | Linked-list DMA load |
| `0x16` | DMA_STD | bytes | bit-exact | 0 | 0 | Linked-list DMA store |
| `0x17` | RMSNORM | FP16 | tolerance | 2e-3 | 1e-2 | Two-pass FPU; eps=1e-5 |
| `0x18` | VCONV_F16_I32 | INT32 | bit-exact† | 0 | 0 | †Finite values truncate; ±Inf/NaN saturate |

### 2.1 Notes on Specific OpCodes

- **MMUL (`0x00`):** INT32 accumulation must be bit-exact for all non-saturated
  values. The `overflow` scenario in `rtl/mxu/README.md` verifies INT32
  saturation clamping.

- **SOFTMAX (`0x01`):** The functional model uses a 4096-entry exp LUT for
  internal accuracy, but the RTL uses a 256-entry ROM. The verification
  tolerance (`abs_tol=2e-3`, `rel_tol=1e-2`) is the binding requirement.

- **ROPE (`0x05`):** 16-stage CORDIC with Q18.14 fixed-point cannot meet the
  default `compare_rtl.py` `abs_tol=1e-3`; use `compare_sfu.py` tolerance.

- **VCONV (`0x13`):** Hardware saturates to `±0x7BFF` (±65504) while numpy
  float16 overflows to `±Inf`. The tolerance absorbs this intentional
  behavioral difference.

- **VCONV_F16_I32 (`0x18`):** Finite FP16 values convert with round-toward-zero
  truncation and must be bit-exact versus the Func Model. Subnormals are
  flushed to zero before conversion. `±Inf` and `NaN` saturate sign-aware to
  `INT32_MAX` / `INT32_MIN`.

- **VRED_MAX / VRED_SUM (`0x11` / `0x12`):** The Vector README declares these as
  INT32 reductions. The current Func Model `golden_executor.py` reads FP16 for
  these opcodes; this spec treats the intended data type as INT32 to align with
  the Vector README. If the Func Model is run with FP16 inputs, the comparison
  must first convert the golden to INT32 using the `VCONV_F16_I32` rules.

---

## 3. Anti-Vacuous Verification Rules

A comparator must not pass simply because all values are near zero or because
errors are hidden by a loose tolerance.

1. **Bit-exact ops:** Any single-bit flip in any output element must cause a
   failure. The `single_tile` MXU test (4096 INT32 values) and Vector INT32
   tests enforce this.

2. **Tolerance ops:** Inject a deterministic corruption (e.g., flip one mantissa
   bit or add 1% relative error to one element) and verify the comparator
   reports FAIL. This must be run as a smoke test for every FP16 op.

3. **Saturation detection:** For INT32 saturating ops, an input vector that
   produces `INT32_MAX` or `INT32_MIN` in any lane must be included. A design
   that wraps instead of saturating must fail.

4. **All-zero / all-constant inputs:** Every op must be verified with at least
   one non-trivial input distribution (random, ramp, or real model weights).
   All-zero inputs are allowed only as an additional edge case, not the sole
   test.

5. **Output shape / length:** The comparator must verify that RTL produces the
   expected number of output elements. A truncated or zero-length output must
   fail even if the produced values are within tolerance.

---

## 4. Multi-Op Accumulation Tolerance

### 4.1 Chained INT32 Ops

For chains such as `VADD → VMUL → VRESID`, errors do not accumulate if each op
saturates correctly. Each op is verified bit-exact independently; the chain is
verified end-to-end with bit-exact comparison against the Func Model's chained
execution.

### 4.2 Chained FP16 SFU Ops

Many transformer ops are decomposed into multiple SFU/Vector steps. Examples:

| Chain | End-to-End Requirement |
|-------|------------------------|
| Softmax | `Vector(max_reduce) → SFU(exp) → Vector(sum_reduce) → SFU(div)` | `abs_tol ≤ 2e-3`, `rel_tol ≤ 1e-2` |
| LayerNorm | `SFU(layernorm)` (single op) | `abs_tol ≤ 2e-3`, `rel_tol ≤ 1e-2` |
| RMSNorm | `SFU(rmsnorm)` (single op) | `abs_tol ≤ 2e-3`, `rel_tol ≤ 1e-2` |
| GELU/SiLU | `SFU(gelu)` / `SFU(silu)` (single op) | `abs_tol ≤ 2e-3`, `rel_tol ≤ 1e-2` |

The per-op tolerance is intentionally the same as the end-to-end tolerance
because the Func Model golden reference is already computed through the same
hardware-equivalent approximations.

### 4.3 MXU → VCONV → SFU Chain

```
MMUL (INT32) → VCONV (FP16) → SFU (FP16)
```

- MMUL output: bit-exact INT32.
- VCONV output: FP16 within `abs_tol=2e-3`, `rel_tol=1e-2` versus INT32→FP16
  round-to-nearest-even.
- SFU output: same tolerance versus the Func Model hardware-equivalent path.

**Important:** The `VCONV` hardware saturates to `±65504` while numpy float16
overflows to `±Inf`. The Func Model mirrors the hardware saturation, so the
tolerance comparison is performed against the saturated golden reference, not
raw numpy.

### 4.4 Error Budget for Long Pipelines

For an end-to-end layer composed of many FP16 ops, the cumulative relative
error should remain below **5e-2** when compared against a float64 reference.
The Func Model hardware-equivalent path is the primary golden; the float64
reference is used only as a sanity bound during model development.

---

## 5. Special Values Handling

### 5.1 ±Infinity

| Scenario | Requirement |
|----------|-------------|
| FP16 output from SFU | Hardware must not produce `±Inf` for finite inputs. Overflow must saturate to `±0x7BFF` (±65504). |
| VCONV (INT32→FP16) | Saturate `|x| > 65504` to `±0x7BFF`; Func Model golden uses the same saturation. |
| VCONV_F16_I32 (FP16→INT32) | `±Inf` saturates sign-aware to `INT32_MAX` / `INT32_MIN`. |
| Comparator | Any `±Inf` in RTL output where the golden is finite is a FAIL. |

### 5.2 NaN

- No NPU op should produce `NaN` for valid finite inputs.
- `NaN` in RTL output is a FAIL unless the Func Model golden is also `NaN`
  (which should not happen for normal inference).
- Comparator must treat any NaN mismatch as a failure regardless of tolerance.

### 5.3 Denormals (Subnormals)

- **SFU input path:** FP16 subnormals are flushed to zero before any SFU
  computation.
- **VCONV output:** INT32→FP16 never produces subnormals for normal INT32
  values; defensive `±0x0001` is permitted.
- **VCONV_F16_I32 input:** FP16 subnormals are flushed to zero before INT32
  conversion.
- Comparator may ignore sign-of-zero differences for FP16 tolerance ops; all
  other denormal behavior must match.

### 5.4 Zero

- **INT32:** `+0` and `-0` are the same 32-bit value. No special handling.
- **FP16:** `+0` (`0x0000`) and `-0` (`0x8000`) compare equal numerically.
  The comparator must accept either sign for tolerance-based ops, but the
  preferred output is `+0`.

### 5.5 INT32 Saturation

- Valid range: `[-2^31, 2^31 − 1]`.
- MXU accumulator, Vector `ADD`/`MUL`/`RESID`, and `reduce_tree` final output
  must saturate.
- A result that wraps (e.g., `INT32_MAX + 1 → INT32_MIN`) is a FAIL.
- Overflow vectors must be explicitly included in the regression suite.

---

## 6. Comparator Implementation Checklist

A verification comparator must implement the following:

- [ ] Use bit-exact compare for opcodes marked `bit-exact` in Section 2.
- [ ] Use `abs_tol=2e-3`, `rel_tol=1e-2` for FP16 tolerance ops.
- [ ] Treat NaN mismatches as unconditional failures.
- [ ] Detect ±Inf in RTL output when golden is finite.
- [ ] Allow INT32 saturation exceptions only when the Func Model also reports
      saturation for the same input.
- [ ] Verify output length / shape before value comparison.
- [ ] Include corruption-injection smoke tests for every tolerance op.
- [ ] Run anti-vacuous tests with non-zero, non-constant inputs.

---

## 7. References

- ISA opcode definitions: `sim/engine/isa.py`
- SFU tolerances and precision notes: `rtl/sfu/README.md` (Section "Inline
  Comparison Tolerance")
- Vector tolerances and comparison modes: `rtl/vector/README.md` (Section
  "Comparison Modes")
- Func Model op implementations: `sim/golden_executor.py` (`GoldenExecutor.step`)
- MXU bit-exact verification: `rtl/mxu/README.md`
- Inline comparator: `scripts/compare_sfu.py`
