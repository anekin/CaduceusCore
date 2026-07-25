
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
