# soc-rtl-verification-signoff — Issues

## [2026-08-27] Todo 11 — FM-SOC-10X op00 RMSNorm failure

**Status:** root-cause identified and fixed on `fix/fm-soc-10x-sfu-desc`.

### Symptom
`FM-SOC-10X` (Ibex-firmware chain through `test_soc_ibex_full`) failed at op00
`RMSNORM pre-attn` with `max_abs=2.95e+00`; the output buffer was never written.

### Root cause
`P4SpikeRunner._build_block()` in `sim/rtl_soc_runner.py` built SFU sub-op
descriptors with `model.host_write_descriptor()`, which packs the **MMUL**
layout (15-word descriptor with `input_size`/`output_size`/`M`/`K`/`N`).
For an SFU descriptor, firmware expects:

- word 8 (`dim`) = element count (or `(head_dim << 16) | elements` for ROPE)
- word 9 (`pos`) = RoPE position
- word 10 (`sfu_op`) = SFU sub-op (`SOFTMAX=0`, `SILU=4`, `ROPE=5`, `RMSNORM=6`)

Because `host_write_descriptor()` wrote the element count into word 10, the
firmware saw `sfu_op = elements & 0xF` (usually `0` = SOFTMAX) instead of the
intended sub-op.  In addition, `_build_block` emitted command opcodes
`0x17` for `RMSNORM` and `0x06` for `SILU`; the firmware command dispatcher in
`firmware/npu_firmware.c` does **not** handle those opcodes, so the RMSNorm
command was silently skipped and the output buffer remained zero.

### Fix
In `sim/rtl_soc_runner.py` `_build_block()`:
- import `SFU_OP_SILU` and `SFU_OP_ROPE`
- write all SFU sub-op descriptors with `write_sfu_descriptor()` from
  `spike_host.py` so the ABI layout is correct
- route `SOFTMAX`, `RMSNORM`, and `SILU` through the generic SFU command
  opcode `0x01` (firmware reads the actual sub-op from the descriptor)
- keep `ROPE` on its special command opcode `0x05` so the firmware's
  head_dim/pos handling is used

No RTL or firmware changes were required.

### Evidence
- Pre-fix failing FSDB: `build/evidence/task-11-fm10x-pre-fix.fsdb`
- Pre-fix failing log: `build/evidence/task-11-fm10x-pre-fix.log`
- Post-fix Ibex RTL logs:
  - `build/ibex_full_rtl/evidence/FM-SOC-10X.log` — `TESTS=1 PASS=1 FAIL=0 SKIP=0`
  - `build/ibex_full_rtl/evidence/FM-SOC-004.log` — `TESTS=1 PASS=1 FAIL=0 SKIP=0`
  - `build/ibex_full_rtl/evidence/FM-SOC-027.log` — `TESTS=1 PASS=1 FAIL=0 SKIP=0`
- SFU module batch regression: `.omo/evidence/task-17-rerun.txt` —
  `SFU: 319/319 passed, Vector: 63/63 passed`

### Verification command
```bash
bash sim/regression/run_ibex_full_rtl.sh 'FM-SOC-004 FM-SOC-027 FM-SOC-10X'
source sim/regression/run_env.sh && python3 scripts/run_batch_regression.py
```
