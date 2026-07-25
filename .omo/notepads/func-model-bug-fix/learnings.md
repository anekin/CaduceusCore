
## T0 — EngineOp opcode enum (infrastructure)

Confirmed firmware opcode dispatch table from `firmware/npu_firmware.c` `dispatch_cmd()` (lines 406–519):

| Opcode(s) | Engine | Notes |
|-----------|--------|-------|
| `0x00` | MMUL | Matrix multiply |
| `0x01..0x04`, `0x06`, `0x17` | SFU | Routed via `sfu_hw_op()` remap (0x01→softmax, 0x02→layernorm, 0x03→gelu, 0x04→relu, 0x06→silu, 0x17→rmsnorm) |
| `0x05` | ROPE | Dispatched to SFU engine with separate handler (head_dim/elements packing) |
| `0x07` | PCIe_DMA | Host↔NPU PCIe DMA |
| `0x09`, `0x0A`, `0x15`, `0x16` | DMA_COPY | `0x09`=load, `0x0A`=store, `0x15`=ldd, `0x16`=std |
| `0x0F..0x14` | Vector | `0x0F`=VADD, `0x10`=VMUL, `0x11`=VRED_MAX, `0x12`=VRED_SUM, `0x13`=VCONV, `0x14`=VRESID |

Created `sim/opcodes.py` with `EngineOp(IntEnum)` — single source of truth; verified 24/24 pytest PASS.
# Func Model Bug Fix — Learnings

## 2026-07-25 — T5 BUG-SOC-FM-006: `--token-ids` fallback for offline tokenization

- **Fallback implemented**: Added `--token-ids` to `sim/spike_host.py`. When supplied, `run_forward_pass()` skips `sim.tokenizer.tokenize()` (and therefore the HuggingFace `tokenizers` import) and uses the caller-provided integer IDs directly. `embedding_lookup()` is still invoked to read `token_embd.weight` from the GGUF file, so the forward pass data path remains intact.
- **Validation**: argparse validates that `--token-ids` is a comma-separated list of integers and emits a clear error for malformed input (e.g., `1,foo,3`).
- **Behavior preservation**: When `--token-ids` is omitted, the existing `--prompt` tokenizer path is unchanged.
- **Documentation**: Updated `docs/bugs/bugs-soc-func-model.md` BUG-SOC-FM-006 with both the offline wheel install procedure and the new `--token-ids` fallback.
- **Environment observations**: Local verification shows the module imports cleanly and argparse accepts `--token-ids 1,2,3`. A full forward run requires a valid GGUF model and reference `.npz`, which are not available in the local workspace; the change only touches the Python host adapter, so the Spike/firmware side is unaffected.

## 2026-07-25 — T1 BUG-SOC-FM-007: Unify opcode numbering between spike_host and firmware

- **Root cause**: `sim/spike_host.py` `schedule_chain()` used hardcoded opcodes 0/1/2/3 for MMUL/SFU/Vector/DMA_COPY, while `firmware/npu_firmware.c` expected `EngineOp` values (MMUL=0x00, SFU=0x01, Vector=0x0F, DMA_COPY=0x09). The SFU dispatch also relied on multi-opcode matching (0x01..0x04, 0x06, 0x17) rather than a single unified opcode with the sub-op in the descriptor.
- **Fix — `sim/spike_host.py`**:
  - Added `from opcodes import EngineOp` import.
  - `schedule_chain()` now uses `int(EngineOp.MMUL)`=0x00, `int(EngineOp.SFU)`=0x01, `int(EngineOp.VECTOR)`=0x0F, `int(EngineOp.DMA_COPY)`=0x09.
  - `write_sfu_descriptor()` writes SFU sub-op to `src[10]` (was unused), `pos` to `src[9]` (was 0).
  - `run_chain_smoke()` tightened: verifies NPU_HEAD==len(ops), treats zero-output as FAIL, requires all ops pass.
  - Chain mode `main()`: `chain_ok = completed and failed == 0` (was `completed` only).
- **Fix — `firmware/npu_firmware.c`**:
  - `sfu_desc_t`: added `sfu_op` field (word after `pos`), reduced `_pad[4]` to `_pad[3]`.
  - `read_sfu_desc()`: reads `desc->pos = src[9]` (was 0), `desc->sfu_op = src[10]` (new).
  - SFU dispatch branch: matches only `op == 0x01` (EngineOp.SFU) instead of multi-opcode union; passes `desc.sfu_op` to `sfu_start()` instead of `sfu_hw_op(op)`.
  - Removed `sfu_hw_op()` function entirely; ROPE branch (op==0x05) now passes hardcoded HW op 5.
- **Verification**:
  - `make -C firmware`: 0 errors, 0 warnings.
  - `python3 scripts/run_func_model_signoff.py run --case task-1b-v3-spike-chain`: PASS (exit 0, NPU_HEAD=3, mmul/sfu/vector all PASS).
  - 4-op chain (mmul+sfu+vector+dma_copy): NPU_HEAD=4, all 4 ops PASS.
   - Plugin ABI fix: rebuilt `npu_mmio_plugin.so` with `-D_GLIBCXX_USE_CXX11_ABI=0` to match Spike binary's old C++ ABI.

## 2026-07-25 — T4 BUG-SOC-FM-004: SFU/Vector descriptor SRAM field respect

- **Root cause**: `read_sfu_desc()` hardcoded `input_sram=0x00000000, output_sram=0x00018000` and `sfu_start()` hardcoded `SFU_SCRATCH_IN`/`SFU_SCRATCH_OUT` for DMA/MMIO addresses, making the descriptor SRAM fields inoperative. Similarly, `vector_desc_t`, `read_vector_desc()`, and `vector_start()` had no SRAM fields at all.
- **Fix — `firmware/npu_firmware.c`**:
  - `read_sfu_desc()`: changed from hardcoded to `src[4]`→`input_sram`, `src[5]`→`output_sram`.
  - `sfu_start()`: added `i_sram, o_sram` parameters; when non-zero use them as DMA dest/MMIO I_ADDR, else fallback to `SFU_SCRATCH_IN`/`SFU_SCRATCH_OUT`.
  - SFU dispatch (op==0x01 and op==0x05): pass `desc->input_sram`/`desc->output_sram`.
  - `vector_desc_t`: added `a_sram, b_sram, o_sram` fields.
  - `read_vector_desc()`: reads `src[4]`→`a_sram`, `src[5]`→`b_sram`, `src[6]`→`o_sram`.
  - `vector_start()`: added SRAM params with `VEC_SCRATCH_A/B/O` fallback.
  - Vector dispatch: passes SRAM fields.
- **Fix — `sim/spike_host.py`**:
  - `write_vector_descriptor()`: added optional `a_sram=0, b_sram=0, o_sram=0` params written to `src[4]`–`src[6]`.
- **Fix — `scripts/verify_descriptor_alignment.py`**:
  - Removed 2 "design inconsistency" notes about SFU SRAM/pos hardcoding (pos was already fixed by T1).
  - Added alignment checks for SFU `src[4]`=input_sram, `src[5]`=output_sram, `src[9]`=pos, `src[10]`=sfu_op.
  - Added alignment checks for Vector `src[4]`=a_sram, `src[5]`=b_sram, `src[6]`=o_sram.
- **Verification**:
  - `make -C firmware`: 0 errors, 0 warnings (clean rebuild).
  - `python3 scripts/verify_descriptor_alignment.py`: PASS, 0 warnings, 15/15 fields aligned.
- **Design choices**: All new SRAM fields default to 0 to preserve backward compatibility with existing callers. When SRAM fields are 0, `sfu_start()` and `vector_start()` fall back to the original hardcoded `SFU_SCRATCH_*`/`VEC_SCRATCH_*` macros, so existing behavior is unchanged.

## 2026-07-25 — T2 BUG-SOC-FM-005: Weight pre-tiling for firmware-compatible DRAM layout

- **Root cause**: Firmware expects weights in tiled order (for each N-tile, for each K-tile, TILE_WEIGHT_BYTES of packed INT4 + TILE_SCALE_BYTES of float32), but `spike_host.py` wrote row-major packed weights to DRAM. The firmware's tile DMA offsets `(n_tile * num_blocks + k_block) * TILE_WEIGHT_BYTES` read wrong data from row-major layout.
- **Fix — `sim/spike_host.py`**: Added `_reorder_weights_to_firmware_tiles()` that converts row-major packed INT4 weights to firmware's tiled DRAM layout (TILE_H=64, TILE_W=64). Scale blocking: 2 K-tiles share 1 group_size=128 scale block. Partial tiles zero-padded to full tile size. Integrated into `_quantize_weight_for_mmul()`, `_quantize_weight_tile()`, and `run_one_op()`.
- **Fix — `sim/mmio_bridge.py`**: Added docstring to `_run_mxu_compute()` documenting the tiled data layout assumption and the host's responsibility to write weights in firmware-compatible tiled order.
- **Verification**:
  - Direct simulation (bypassing Spike): tile-by-tile computation with reordered data matches golden within 9.2e-5 for K=1536,N=1536. Reordering unit test: roundtrip extraction and tile-by-tile golden comparison pass.
  - Spike smoke test (mmul_smoke, L0 Q_proj): nonzero output entries increased from 768/1536 (50%, baseline row-major) to 1536/1536 (0%, reordered), confirming tiling eliminates wrong-tile reads.
  - Spike smoke test max_diff: still 1.07e+03 → caused by pre-existing bridge accumulation bug. Bridge trace shows per-tile accumulation stops updating after k_block≥2; all k_blocks 2-23 produce identical output.
- **Remaining issue**: Bridge `_run_mxu_compute()` accumulation across K-tiles is broken for k_block≥2. The crossbar MXU master's read of `o_abs` appears to return stale data rather than the updated accumulated output. Root cause TBD but is independent of the weight tiling fix. Direct GoldenMXU tile-by-tile simulation proves the reordering + per-tile computation logic is correct.
