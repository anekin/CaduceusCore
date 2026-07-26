# func-model-bug-fix - Work Plan

## TL;DR (For humans)

**What you'll get:** 修复 `func-model-signoff-v3` 发现的 4 个遗留 Func Model bug，消除 RTL 验证的隐患。按优先级 P0→P3 执行：
- **P0**: BUG-SOC-FM-007 — Python Host 与 C Firmware 的 opcode 协议定义不一致（Python 0/1/2/3 vs 固件 0/0x01/0x0F），导致 chain mode 中 vector 命令被分发到错误的 SFU 分支
- **P1**: BUG-SOC-FM-005 — Bridge path 与 GoldenMXU 的 INT4 精度 gap（固件 tile=64×64 vs Python group_size=128 + row-major 布局），导致 Spike 路径 MMUL 结果错误
- **P2**: BUG-SOC-FM-004 — SFU descriptor SRAM 字段被固件硬编码忽略，多 token / 并发场景下会成为功能 bug
- **P3**: BUG-SOC-FM-006 — EDA 服务器 sz0001 缺少 `tokenizers` 模块，forward pass 无法运行

**每条 bug 修复包含三步**：Root Cause 确认 → 代码修复 → 证据更新 + bug track 更新。

---

## Known constraints
1. All work on `main` branch.
2. Bug track lives in `docs/bugs/bugs-soc-func-model.md` — must be updated after each fix.
3. EDA server sz0001 (192.168.0.11) required for Spike-related fixes.
4. Firmware changes require `make -C firmware` rebuild.
5. Use `scripts/run_fm_env.sh` for sz0001 Python environment.
6. Firmware and `spike_src/` are REFERENCED but NOT modified in func-model signoff scope — bug fixes may touch firmware, which is outside the signoff scope constraint.

---

## Task decomposition

### T0. Infrastructure — OpCode enum unification ✅
- **Why**: BUG-SOC-FM-007 的根因是 Python host 和 C firmware 使用不同 opcode 编号。先统一定义，再修两边的 dispatch。
- **Plan**:
  1. Create `sim/opcode.py` with a shared `EngineOp` enum mirroring the firmware's opcode table:
     ```python
     class EngineOp(IntEnum):
         MMUL     = 0x00
         SFU      = 0x01
         ROPE     = 0x05
         VECTOR   = 0x0F   # VADD = 0x0F, VMUL = 0x10, ...
         PCIE_DMA = 0x07
         DMA_COPY = 0x09
     ```
  2. Export the enum values to a C header snippet for `npu_firmware.c` reference in comments.
  3. Add `test_opcode_consistency` that asserts enum values match firmware expectations.
- **Acceptance**: `python3 -m pytest sim/tests/test_opcode_consistency.py -v` → PASS
- **Evidence**: `.omo/evidence/bug-fix-t0-opcode-enum.txt`
- **Commit**: Y — `refactor(func-model): define shared EngineOp enum for host/firmware opcode consistency`

### T1. [P0] Fix BUG-SOC-FM-007 — Spike chain mode opcode mismatch ✅
- **Why**: Python Host 的 `schedule_chain()` 使用 opcode 0/1/2/3，而 C Firmware 的 `dispatch_cmd()` 定义了不同的 opcode 语义（0x01=SFU, 0x02=SFU LAYERNORM, 0x0F=Vector VADD）。opcode 2 被固件 SFU 分支捕获，Vector 命令从未真正执行。Chain mode 本身作为全链路验证方法是正确的，问题纯粹是两个代码模块之间的接口协议不一致。T1 原始执行的 PASS 是误报（通过标准为"non-zero output, no crash"，太宽松）。
- **Root cause recap**: `spike_host.py` `schedule_chain()` 使用 opcode 0/1/2/3；`npu_firmware.c` `dispatch_cmd()` 期望 0/0x01/0x0F/0x09。
- **Plan**:
  1. In `sim/spike_host.py` `schedule_chain()`: replace hardcoded opcodes with `EngineOp` enum values (`EngineOp.MMUL`, `EngineOp.SFU`, `EngineOp.VECTOR`, `EngineOp.DMA_COPY`).
  2. In `sim/spike_host.py` `write_sfu_descriptor()`: write the SFU sub-operation (`SFU_OP_GELU` etc.) to **`src[10]`** in the 15-word descriptor. (Note: `src[9]` is reserved for `pos`, see T4.)
  3. In `firmware/npu_firmware.c` `dispatch_cmd()`:
     - Change SFU branch to match only `op == EngineOp.SFU` (0x01), not the multi-opcode union.
     - In `read_sfu_desc()`: add `desc->sfu_op = src[10]` and `desc->pos = src[9]`.
     - In the SFU dispatch: pass `desc->sfu_op` to `sfu_start()` instead of `sfu_hw_op(cmd->opcode)`.
  4. Update `firmware/npu_firmware.c` to handle Vector dispatch via the correct opcode (`>= 0x0F`), matching `EngineOp.VECTOR`.
  5. Tighten chain mode acceptance: replace "non-zero output, no crash" with per-op completion status check + output shape verification.
  6. Rebuild firmware: `make -C firmware`.
  7. Re-run `task-1b-v3-spike-chain` on sz0001 via `run_func_model_signoff.py`.
- **Acceptance**: `spike_host.py --mode chain` → all 3 ops complete, NPU_HEAD=3, exit 0. Evidence shows PASS.
- **Evidence**: `.omo/evidence/bug-fix-t1-fm007.txt`
- **Commit**: Y — `fix(bug-fm-007): unify opcode numbering between spike_host and firmware`

### T2. [P1] Fix BUG-SOC-FM-005 — Bridge/Golden INT4 precision gap
- **Why**: Spike 路径的 MMUL 结果与 Golden 参考偏差 77-858，根源是固件的 tile 偏移公式与 Python 的 row-major 布局不兼容。修复后 chain mode 的 MMUL 比对就能 PASS。
- **Root cause recap (3 mismatches)**:
  - M1: Python 写 row-major packed INT4 (字节偏移 = `(row*N+col)/2`)，固件按 tile offset `(n*num_blocks+k)*2048` 读
  - M2: Python quantize group_size=128，固件 TILE_H=64 → scale 分块粒度不一致
  - M3: 固件 DMA 写错数据到 SRAM，Bridge 读取后调用同一 `matmul_int4_per_block()` → garbage out
- **Plan (方案 A: Python 侧预 tile，推荐)**:
  1. In `sim/spike_host.py`: add `_reorder_weights_to_firmware_tiles()` that converts row-major packed INT4 to firmware's tiled layout (TILE_H=64, TILE_W=64):
     ```python
     def _reorder_weights_to_firmware_tiles(wgt_packed, scales, K, N, TILE_H=64, TILE_W=64):
         W = unpack_int4(wgt_packed).reshape(K, N)
         # scales shape = (K/128, N). 2 K-tiles (64 each) share 1 scale block (128).
         num_blocks = (K + TILE_H - 1) // TILE_H
         num_tiles  = (N + TILE_W - 1) // TILE_W
         tiled_w, tiled_s = bytearray(), bytearray()
         for n in range(num_tiles):
             for k in range(num_blocks):
                 k_end = min((k + 1) * TILE_H, K)
                 n_end = min((n + 1) * TILE_W, N)
                 tile = W[k * TILE_H : k_end, n * TILE_W : n_end]
                 # Zero-pad partial tiles to full TILE_H×TILE_W before pack_int4
                 if tile.shape != (TILE_H, TILE_W):
                     padded = np.zeros((TILE_H, TILE_W), dtype=tile.dtype)
                     padded[:tile.shape[0], :tile.shape[1]] = tile
                     tile = padded
                 tiled_w += pack_int4(tile)
                 # scale index: k // 2 because 2 TILE_H=64 blocks share 1 group_size=128 block
                 tiled_s += scales[k // 2, n * TILE_W : n_end].tobytes()
         return bytes(tiled_w), bytes(tiled_s)
     ```
  2. In `sim/spike_host.py` `run_one_op()` (line 188) and `_add_mmul_op()` (line 435): both paths quantize weights and write row-major data to DRAM. Insert `_reorder_weights_to_firmware_tiles()` before the respective `host_write_data()` calls.
  3. Re-run `task-1a-v3-spike-mmul-smoke` on sz0001 — expect golden comparison PASS or significantly reduced max_diff.
  4. Update `sim/mmio_bridge.py` `_run_mxu_compute()`: document the tiled data layout assumption.
  5. If MMUL smoke still fails: trace exact byte layout with a small synthetic test (K=128, N=128).
- **Alternative (方案 B: 固件 align to group_size=128)**: Change `TILE_H=128` in firmware. Simpler conceptually but affects SRAM budget and DMA burst — requires full chain regression.
- **Acceptance**: `task-1a-v3-spike-mmul-smoke` → max_diff < 1e-3 or documented explained gap. Evidence shows significant improvement.
- **Evidence**: `.omo/evidence/bug-fix-t2-fm005.txt`
- **Commit**: Y — `fix(bug-fm-005): pre-tile weights for firmware-compatible DRAM layout`

### T3. Update BUG-SOC-FM-005 evidence with detailed mismatch analysis
- **Why**: The current bug report says "different quantization/dequantization flows" but doesn't explain the three specific mismatches. The deep analysis from code exploration must be recorded.
- **Plan**:
  1. Edit `docs/bugs/bugs-soc-func-model.md` BUG-SOC-FM-005 section:
     - Replace placeholder root cause with the three-mismatch analysis (Weight Tile Layout, Scale Blocking, Bridge Data Corruption)
     - Add the code references: `sim/spike_host.py` `_quantize_weight_for_mmul`, `firmware/npu_firmware.c` `dispatch_cmd` tile loop, `sim/mmio_bridge.py` `_run_mxu_compute`
     - Update Status from "Open (documented, no fix needed)" to "Open (fix plan: T2 above)"
     - Add fix strategy section referencing方案 A (pre-tile) or 方案 B (firmware tile size)
  2. Same for BUG-SOC-FM-007: replace "Under investigation" with opcode mismatch analysis.
  3. Same for BUG-SOC-FM-004: add the full data flow diagram showing the two hardcoding layers.
  4. Same for BUG-SOC-FM-006: add the offline wheel install procedure.
  5. Update the Stats section: keep total=7 (BUG-001 counted in module-level), Open=4, but add "Has fix plan: 4/4".
- **Acceptance**: `docs/bugs/bugs-soc-func-model.md` has detailed root cause + fix strategy for all 4 open bugs.
- **Evidence**: Git diff of `docs/bugs/bugs-soc-func-model.md`
- **Commit**: Y — `docs(bugs): add detailed root cause analysis and fix strategies for FM-004/005/006/007`

### T4. [P2] Fix BUG-SOC-FM-004 — SFU descriptor SRAM field respect
- **Why**: 当前硬编码不影响 pos=0 forward pass，但多 token 生成和并发调度时需要 host 动态指定 SRAM buffer。修复成本低，主要是 descriptor 字段映射。
- **Root cause recap**: `read_sfu_desc()` hardcodes input_sram=0x00000000, output_sram=0x00018000; `sfu_start()` uses different hardcoded macros SFU_SCRATCH_IN=0x20080000, SFU_SCRATCH_OUT=0x20080400. Both ignore descriptor.
- **Descriptor schema (defined by T1)**: The 15-word SFU descriptor layout is:
  ```
  [0]=input_addr  [1]=0  [2]=output_addr  [3]=0
  [4]=input_sram  [5]=output_sram  [6]=0  [7]=0
  [8]=dim  [9]=pos  [10]=sfu_op  [11]=0
  [12]=1  [13]=dim  [14]=1
  ```
  T1 handles `src[10]=sfu_op` and `src[9]=pos`. T4 adds `src[4]=input_sram` and `src[5]=output_sram`.
- **Plan**:
  1. In `firmware/npu_firmware.c` `read_sfu_desc()`: read `src[4]` → `desc->input_sram`, `src[5]` → `desc->output_sram` (instead of hardcoding). `src[9]`→pos and `src[10]`→sfu_op already added by T1.
  2. In `firmware/npu_firmware.c` `sfu_start()`:
     - Add `uint32_t i_sram, uint32_t o_sram` parameters.
     - When `i_sram != 0`, use it as DMA dest and MMIO I_ADDR; otherwise fall back to SFU_SCRATCH_IN. Same for o_sram / SFU_SCRATCH_OUT.
  3. In `firmware/npu_firmware.c` dispatch: pass `desc->input_sram` and `desc->output_sram` to `sfu_start()`.
  4. In `sim/spike_host.py` `write_sfu_descriptor()`: the `input_sram` and `output_sram` params already write to `src[4]`/`src[5]` — no change needed. (T1 already added `sfu_op` at `src[10]`.)
  5. **All new descriptor fields must use optional parameters with defaults** to avoid breaking existing callers (`schedule_chain()` via `**desc`). For Vector: `write_vector_descriptor(..., a_sram=0, b_sram=0, o_sram=0)`.
  6. Same treatment for Vector: add `a_sram, b_sram, o_sram` to `vector_desc_t` (at `src[4]`–`src[6]`), update `read_vector_desc()` and `vector_start()` with fallback to `VEC_SCRATCH_A/B/O` macros.
  7. Update `scripts/verify_descriptor_alignment.py` to remove the "design inconsistency" note and assert new layout.
  8. Rebuild firmware and re-run W5.5 descriptor alignment check.
- **Acceptance**: `scripts/verify_descriptor_alignment.py` → SFU descriptor SRAM fields match between host and firmware. No "design inconsistency" warnings.
- **Evidence**: `.omo/evidence/bug-fix-t4-fm004.txt`
- **Commit**: Y — `fix(bug-fm-004): firmware reads SFU/Vector SRAM addresses from descriptor`

### T5. [P3] Fix BUG-SOC-FM-006 — Resolve tokenizers dependency for forward pass ✅
- **Why**: Forward pass 是最高级别的集成测试（完整 Qwen 模型推理链路），需要能用但不用作为日常门禁。
- **Root cause**: EDA server sz0001 无外网，无法 `pip install tokenizers`。
- **Plan (方案: 离线 wheel + --token-ids fallback)**:
  1. On a machine with internet: `pip download tokenizers --platform manylinux2014_x86_64 --python-version 3.10 --only-binary=:all: -d /tmp/tokenizers_whl`
  2. scp the `.whl` to sz0001 and install: `FM_PYTHON -m pip install --no-index --find-links=/tmp/tokenizers_whl tokenizers`
  3. Add `--token-ids` CLI argument to `sim/spike_host.py` as a fallback: if provided, skip tokenizer and use raw IDs.
  4. Document the offline install procedure in the bug report.
- **Acceptance**: `spike_host.py --mode forward --layers 1` → no `ModuleNotFoundError`. Or `--token-ids` fallback works.
- **Evidence**: `.omo/evidence/bug-fix-t5-fm006.txt`
- **Commit**: Y — `fix(bug-fm-006): add --token-ids fallback for offline tokenization`

### T6. Full regression — Re-run all Spike+firmware tasks on sz0001
- **Why**: After T1-T5 fixes, all three Spike tasks (1a, 1b, 1c) must be re-verified.
- **Plan**:
  1. `python3 scripts/run_func_model_signoff.py run --case task-1a-v3-spike-mmul-smoke`
  2. `python3 scripts/run_func_model_signoff.py run --case task-1b-v3-spike-chain`
  3. `python3 scripts/run_func_model_signoff.py run --case task-1c-v3-spike-forward` (or --token-ids variant)
  4. `python3 scripts/run_func_model_signoff.py validate --v3` → expect 0 STALE, 0 MISSING, improved pass rate.
- **Acceptance**: task-1b PASS (NPU_HEAD=3). task-1a max_diff reduced to acceptable level or documented. task-1c runs without ModuleNotFoundError.
- **Evidence**: `.omo/evidence/bug-fix-t6-regression.txt`
- **Commit**: N (evidence only)

### [x] T7. Plan compliance — Final validation
- **Why**: This is a separate bug-fix cycle from `func-model-signoff-v3`; need its own audit.
- **Plan**:
  1. Verify all T0-T6 acceptance criteria met via evidence files.
  2. Confirm `docs/bugs/bugs-soc-func-model.md` updated for all 4 bugs.
  3. Confirm stats: Open bugs remain 4 but all have fix plans.
  4. Run `python3 scripts/run_func_model_signoff.py validate --v3` one final time.
- **Acceptance**: All evidence files exist with `evidence.verdict: pass`. Bug track updated.
- **Evidence**: `.omo/evidence/bug-fix-t7-plan-compliance.txt`
- **Commit**: N (evidence only)

---

## Execution waves

| Wave | Tasks | Depends on |
|------|-------|------------|
| Wave 0 | T0 (infrastructure) | — |
| Wave 1 | T1 (P0: FM-007) | T0 |
| Wave 2 | T2 (P1: FM-005) + T3 (bug track update) | T0 (independent of T1) |
| Wave 3 | T4 (P2: FM-004) | T1 |
| Wave 4 | T5 (P3: FM-006) | — (independent) |
| Wave 5 | T6 (full regression) | T1, T2, T4, T5 |
| Wave 6 | T7 (plan compliance) | T3, T6 |

---

## Final verification wave
> Runs in parallel after ALL todos (T0-T6). ALL must APPROVE.

- [x] F1. Code quality review: compileall on all changed Python files; firmware compiles with `make -C firmware` zero errors; no forbidden imports; no RTL dependency.
  Acceptance: `.omo/evidence/bug-fix-final-code-quality.txt` has `evidence.verdict: pass`
  Evidence: `.omo/evidence/bug-fix-final-code-quality.txt`
  Commit: N

- [x] F2. Real manual QA on sz0001: (1) chain mode PASS, (2) mmul_smoke golden comparison improved, (3) forward pass runs, (4) descriptor alignment check passes.
  Acceptance: `.omo/evidence/bug-fix-final-real-qa.txt` has `evidence.verdict: pass`
  Evidence: `.omo/evidence/bug-fix-final-real-qa.txt`
  Commit: N

- [x] F3. Scope fidelity: changed files limited to `sim/`, `scripts/`, `firmware/`, `docs/bugs/`, `.omo/`. Reject RTL changes. Reject Spike plugin C++ changes.
  Acceptance: `.omo/evidence/bug-fix-final-scope-fidelity.txt` has `evidence.verdict: pass`
  Evidence: `.omo/evidence/bug-fix-final-scope-fidelity.txt`
  Commit: N

---

## Commit strategy
| Task | Commit | Message |
|------|--------|---------|
| T0 | Y | `refactor(func-model): define shared EngineOp enum for host/firmware opcode consistency` |
| T1 | Y | `fix(bug-fm-007): unify opcode numbering between spike_host and firmware` |
| T2 | Y | `fix(bug-fm-005): pre-tile weights for firmware-compatible DRAM layout` |
| T3 | Y | `docs(bugs): add detailed root cause analysis and fix strategies for FM-004/005/006/007` |
| T4 | Y | `fix(bug-fm-004): firmware reads SFU/Vector SRAM addresses from descriptor` |
| T5 | Y | `fix(bug-fm-006): add --token-ids fallback for offline tokenization` |
| T6 | N | Evidence only |
| T7 | N | Evidence only |
