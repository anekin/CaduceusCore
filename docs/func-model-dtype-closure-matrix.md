# Func Model L2 Signoff — Dtype Closure Matrix

> One-page reference for opcode input/output dtypes and adjacent-op compatibility.
> Acceptance: every adjacent pair in a chain is closed by an explicit `VCONV` / `VCONV_F16_I32`
> instruction or by matching native dtypes.

## 1. Per-Opcode Dtype

| Opcode | Mnemonic | Primary Input Dtype | Output Dtype | Notes |
|--------|----------|---------------------|--------------|-------|
| 0x00 | MMUL | INT8 activation, INT4 weight | INT32 | Accumulator output, saturated to INT32 |
| 0x01 | SOFTMAX | FP16 | FP16 | LUT-based exp + normalize |
| 0x02 | LAYERNORM | FP16 | FP16 | Fixed-point mean/variance |
| 0x03 | GELU | FP16 | FP16 | 64-entry piecewise LUT |
| 0x04 | RELU | FP16 | FP16 | Element-wise max(x, 0) |
| 0x05 | ROPE | FP16 | FP16 | CORDIC rotation of Q/K pairs |
| 0x06 | SILU | FP16 | FP16 | Reuses exp LUT |
| 0x07 | MAXPOOL | FP16 | FP16 | 2x2 max pooling |
| 0x08 | AVGPOOL | FP16 | FP16 | 2x2 average pooling |
| 0x09 | DMA_LD | bytes (DRAM) | bytes (SRAM) | Not a compute node |
| 0x0A | DMA_ST | bytes (SRAM) | bytes (DRAM) | Not a compute node |
| 0x0B | KV_LOAD | token_id | bytes (SRAM) | Not a compute node |
| 0x0C | KV_STORE | bytes (SRAM) | token_id | Not a compute node |
| 0x0D | BARRIER | — | — | Synchronization only |
| 0x0E | NOP | — | — | No operation |
| 0x0F | VADD | INT32 (A), INT32 (B) | INT32 | Saturated element-wise add |
| 0x10 | VMUL | INT32 (A), INT32 (B) | INT32 | Saturated element-wise mul |
| 0x11 | VRED_MAX | INT32 | FP16 scalar | Tree reduction, scalar output |
| 0x12 | VRED_SUM | INT32 | FP16 scalar | Tree reduction, scalar output |
| 0x13 | VCONV | INT32 | FP16 | INT32 → FP16 (MXU→SFU bridge) |
| 0x14 | VRESID | FP16 (residual `sa`), INT32 (delta `sb`) | INT32 | Saturated residual add |
| 0x15 | DMA_LDD | bytes (DRAM descriptor) | bytes (SRAM) | Not a compute node |
| 0x16 | DMA_STD | bytes (SRAM descriptor) | bytes (DRAM) | Not a compute node |
| 0x17 | RMSNORM | FP16 | FP16 | Two-pass FPU normalization |
| 0x18 | VCONV_F16_I32 | FP16 | INT32 | FP16 → INT32 (SFU→Vector/MXU bridge) |

## 2. Adjacency Closure Matrix

Rows = previous op output dtype; columns = next op primary input dtype.
Cell value = how the dtype boundary is closed.

| Previous \ Next | INT8 (MMUL act) | INT32 (Vector/MXU) | FP16 (SFU) |
|-----------------|-----------------|--------------------|------------|
| **INT32**       | clip + pack INT8* | direct | `VCONV` (0x13) |
| **FP16**        | `VCONV_F16_I32` (0x18) → clip INT8* | `VCONV_F16_I32` (0x18) | direct |
| **INT8**        | direct | `sign_extend` (implicit in read) | — |

\* The hardware INT8 datapath consumes raw INT8 bytes. When the upstream producer
is INT32/FP16, Func Model clips the converted INT32 value to [-128, 127] and
rewrites it as INT8 bytes in SRAM before MMUL consumes it. This is not a dedicated
ISA opcode; it is the software-visible data-type handshake.

## 3. Critical Chain Examples

```text
INT32→FP16:   MMUL (INT32 out) → VCONV (0x13) → SOFTMAX/LAYERNORM/GELU/... (FP16 in)
FP16→INT32:  SOFTMAX (FP16 out) → VCONV_F16_I32 (0x18) → VRESID/VADD/VMUL (INT32 in)
FP16→INT8:   GELU/SILU/RELU (FP16 out) → VCONV_F16_I32 (0x18) → clip INT32→INT8 → MMUL (INT8 in)
```

## 4. Incompatible Pairs Resolved by This Matrix

| Adjacent Pair | Status | Conversion Mechanism |
|---------------|--------|----------------------|
| MMUL INT32 out → SFU FP16 in | ✅ closed | `VCONV` (0x13) |
| SFU FP16 out → VRESID INT32 in (`sb`) | ✅ closed | `VCONV_F16_I32` (0x18) |
| SFU FP16 out → MMUL INT8 in | ✅ closed | `VCONV_F16_I32` (0x18) + INT8 clip |
| SFU FP16 out → VADD/VMUL INT32 in | ✅ closed | `VCONV_F16_I32` (0x18) |
| Vector INT32 out → SFU FP16 in | ✅ closed | `VCONV` (0x13) |

## 5. Func Model L2 Acceptance

- [x] Dtype closure matrix documented for all 23 opcodes.
- [x] Three true op chains pass with no hex preload:
  1. INT32→FP16: MMUL → VCONV → SOFTMAX
  2. FP16→INT32: SOFTMAX → VCONV_F16_I32 → VRESID
  3. FP16→INT8: GELU → VCONV_F16_I32 → MMUL (with INT8 clip)
- [x] All dtype conversion points have ≥1 passing case.
- [x] Evidence file: `build/evidence/w1-5-fm-l2-signoff.txt`.
