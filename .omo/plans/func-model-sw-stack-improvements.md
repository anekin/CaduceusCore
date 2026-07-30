# func-model-sw-stack-improvements — Work Plan
## TL;DR (For humans)
> Summary: Close the remaining Func Model software stack gaps identified during close-e2e-command-gap. 14 todos across 3 waves: Wave 1 fixes Spike E2E correctness and multi-connection device_server, Wave 2 hardens ABI / boundary / error paths, Wave 3 improves quality / observability / CI hygiene.
> Phase constraint: Func Model only. RTL real transport, FPGA real transport, performance signoff, ExecuTorch expansion, and multi-model support are deferred to the next phase.
> Deliverables:
> - Wave 1: Spike firmware executes real MXU/SFU compute through full blk.0 graph; device_server supports multi-connection lifecycle; C Runtime routes `fm://spike` correctly; single-copy ggml-npu build.
> - Wave 2: `cad_execution_stats_t` gains `struct_size`; buffer overflow hardened; VADD / RMSNorm verified end-to-end through Host Runtime; transport error path unified.
> - Wave 3: ASan leak cleanup; flatc version pinned; signoff evidence enriched; stale evidence regenerated; structured logging across layers.
> Effort: M — 14 todos across 3 waves. Each wave is independently composable.
> Risk: Low — Wave 1 tasks 2 (I-008) and 3 (I-009) carry the most debugging risk; the remainder are well-characterized fixes.

## Scope

### In-Scope
1. Fix device_server `_last_request_id` per-connection (I-007 / I-016).
2. Debug Spike firmware full-model blk.0 dispatch to exercise real MXU/SFU compute (I-008).
3. Fix C Runtime `fm://spike` URI routing confusion (I-009).
4. Unify ggml-npu canonical/build sources via symlink or cmake copy (I-012).
5. Add `struct_size` to `cad_execution_stats_t` (I-017).
6. Harden `cadBufferRead/Write` against integer overflow (I-011).
7. Verify `CAD_OP_VADD` and `CAD_OP_SFU_RMSNORM` end-to-end through Host Runtime signoff (no proxy).
8. Unify transport error propagation through runtime.
9. Fix ASan mock transport test leaks (add missing `cadDeviceClose`).
10. Pin flatc / pip flatbuffers versions and document.
11. Enrich Qwen3B signoff evidence with explicit `npu_ops_executed` and `cpu_fallback_ops`.
12. Regenerate or explicitly BLOCK 10 stale evidence files.
13. Add structured logging (TRACE/DEBUG/INFO/WARN/ERROR) across Runtime / Transport / Server.
14. Extend Qwen blk.0 evidence to auto-detect NPU/CPU op split.

### Must NOT Do
- Do NOT modify RTL datapath (`rtl/mxu/`, `rtl/sfu/`, `rtl/vector/`, `rtl/soc/`).
- Do NOT implement real FPGA / RTL transport — Func Model (`fm://` path) only.
- Do NOT claim performance signoff (TTFT/TPS/TOPS).
- Do NOT add new framework model support (new LLM or CV models beyond Qwen2.5-3B).
- Do NOT expand ExecuTorch beyond its current scaffold.
- Do NOT change the existing `cadCommandListAppendExecuteBlob` / `cadBufferGetDeviceAddress` / `cadFenceGetExecutionStats` public ABI semantics — only add `struct_size`, do not break callers.

## Verification Strategy
- **TDD throughout**: write failing test first, then implement, then verify green.
- **Unit layer**:
  - CTest (`software/` build), Python pytest (`sim/tests/`), firmware `make -C firmware` zero warnings.
  - ASan/UBSan clean for all new/modified C/C++ code.
- **Integration layer**:
  - Wave 1 task 2: Spike blk.0 full-graph test with `mmul > 0` and `sfu > 0`.
  - Wave 1 task 1: multi-connection round-trip test.
- **CI layer**:
  - GitHub Actions `caduceus-core-ci.yml` must pass from clean checkout.
  - Aggregator `--strict` must exit 0 after Wave 3 task 12.
- **Evidence policy**:
  - Every todo has happy and failure QA scenarios, each with an agent-executable command and evidence path under `.omo/evidence/`.

## Guardrail Traceability
> Maps open issues from close-e2e-command-gap to the plan item that addresses them.

| Issue | Description | Addressed By |
|-------|-------------|-------------|
| I-007 | `_last_request_id` global, breaks multi-connection | Todo 1 |
| I-008 | Spike full-model no MXU/SFU compute | Todo 2 |
| I-009 | `fm_parse_uri()` rejects `fm://spike` | Todo 3 |
| I-012 | Canonical vs build copy of ggml-npu.cpp | Todo 4 |
| I-017 | `cad_execution_stats_t` lacks `struct_size` | Todo 5 |
| I-011 | `UINT64_MAX + 1 == 0` overflow | Todo 6 |
| CMD-IR gap | VADD / RMSNorm not verified end-to-end through Host Runtime | Todo 7 |
| Error path | Transport error not unified | Todo 8 |
| ASan leak | Mock transport tests leak 24KB | Todo 9 |
| flatc mismatch | flatc vs pip version incompatibility | Todo 10 |
| Evidence gap | `cpu_fallback_ops` not explicit in signoff JSON | Todo 11 |
| Stale evidence | 10 files rejected by strict aggregator | Todo 12 |
| Observability | No structured logging | Todo 13 |
| NPU/CPU auto-split | Blk.0 evidence doesn't auto-detect NPU vs CPU | Todo 14 |

## Dependency Matrix

| Todo | Depends On | Blocks | Can Parallelize With |
|------|-----------|--------|---------------------|
| 1 (per-connection I-007) | None | 2 | 3, 4 |
| 2 (Spike I-008) | 1 | None | — |
| 3 (URI routing I-009) | None | None | 1, 4 |
| 4 (ggml-npu single copy I-012) | None | None | 1, 3 |
| 5 (struct_size I-017) | None | None | 6, 7, 8 |
| 6 (buffer overflow I-011) | None | None | 5, 7, 8 |
| 7 (VADD/RMSNorm signoff verify) | None | None | 5, 6, 8 |
| 8 (transport error path) | None | None | 5, 6, 7 |
| 9 (ASan leaks) | None | None | 10 |
| 10 (flatc version pin) | None | None | 9 |
| 11 (signoff evidence enrichment) | 2 | None | 12, 13, 14 |
| 12 (stale evidence regen) | None | None | 11, 13, 14 |
| 13 (structured logging) | None | None | 11, 12, 14 |
| 14 (NPU/CPU auto-split) | 2 | None | 11, 12, 13 |

### Critical Path
```
W1(1→2) ──→ W3(11, 14)
W1(3, 4) parallel
W2(5, 6, 7, 8) parallel
W3(9, 10, 12, 13) parallel
```

## Todos

> Implementation + Test = ONE todo. Never separate. Target 3–6 per wave.

### Wave 1 — Critical Fixes

- [x] 1. Fix device_server per-connection `_last_request_id` (I-007 / I-016)
  - **What to do**:
    - Introduce per-connection request ID tracking in the device server — add a `dict[int, int]` mapping connection identifier → last request ID, and assign a connection ID (`_next_conn_id`) per new socket client.
    - `_next_request_id_ok(conn_id, req_id)` checks against the per-connection counter instead of a global.
    - `cadDeviceReset` and `cadDeviceClose → cadDeviceOpen` cycles must work without "request out of order" rejections.
  - **Must NOT do**:
    - Do not break the existing request-id monotonicity guarantee per connection.
  - **References**: `sim/device_server.py` `_next_request_id_ok`, `_last_request_id`
  - **Acceptance criteria**:
    - A test that opens `fm://...`, submits a command, closes, opens again, submits another command → both succeed.
    - `cadDeviceReset()` after submission returns `CAD_SUCCESS`.
  - **QA scenarios**:
    - happy: `PYTHONPATH=sim:gen python3 sim/device_server.py --sock /tmp/caduceus_w1t1.sock & sleep 1; ./build/software/test_fm_e2e_mmul fm://unix?path=/tmp/caduceus_w1t1.sock && ./build/software/test_fm_e2e_mmul fm://unix?path=/tmp/caduceus_w1t1.sock 2>&1 | tee .omo/evidence/task-w1t1-happy.log; kill %1`
    - failure: two connections race on request IDs → second connection succeeds (not rejected).
  - **Commit**: Y | `fix(device_server): track _last_request_id per-connection (I-007, I-016)`

- [x] 2. Debug Spike firmware full-model blk.0 dispatch (I-008)
  - **What to do**:
    - Trace `_execute_flat()` → Spike MMIO bridge → firmware dispatch for a single MMUL op (which W3-T2 already passes individually).
    - Extend to a 2-op chain (MMUL + DMA_COPY) through Spike → if `mmul > 0`, scale to 4-op chain, then to blk.0.
    - Identify why `cadBufferAllocate` fails in the second graph submission (buffer address collision or sizing bug).
    - Goal: a test where Spike firmware executes `mmul >= 1` AND `sfu >= 1` for blk.0.
  - **Must NOT do**:
    - Do not change the C firmware (`firmware/npu_firmware.c`) — only debug the Python-side dispatch/bridge logic (unless a firmware-side fix is strictly required).
  - **References**: `sim/device_server.py` `_execute_flat`, `sim/mmio_bridge.py`, `sim/func_model.py`, `firmware/npu_firmware.c` dispatch_cmd
  - **Acceptance criteria**:
    - Spike blk.0 single-token decode gate reports `mmul > 0` AND `sfu > 0`.
    - Fence status COMPLETED, no internal "buffer allocation failed" errors.
    - Output text matches CPU reference.
  - **QA scenarios**:
    - happy: `PYTHONPATH=sim:gen python3 sim/device_server.py --spike --sock /tmp/caduceus_w1t2.sock & sleep 2; PYTHONPATH=sim:gen python3 scripts/run_qwen3b_software_signoff.py positive --device fm://spike --gate single_decode_token --evidence .omo/evidence/task-w1t2-happy.json 2>&1 | tee .omo/evidence/task-w1t2-happy.log; kill %1`
    - failure: remove firmware ELF prerequisite → gate fails with BLOCKED not PASS.
  - **Commit**: Y | `fix(spike): debug full-model blk.0 dispatch to exercise MXU/SFU (I-008)`

- [x] 3. Fix C Runtime `fm://spike` URI routing (I-009)
  - **What to do**:
    - Currently `fm_parse_uri()` (`transport_fm.cpp`) only accepts `fm://`, `fm://python`, and `fm://unix?path=...` — `fm://spike` is rejected with `CAD_TR_ERR_UNSUP`. Extend `fm_parse_uri()` to accept `fm://spike` and map it to the FM transport (like `fm://python`).
  - **Must NOT do**:
    - Do not break the `fpga://` → UNSUPPORTED behavior added in W2-T8.
  - **References**: `software/src/runtime_core.c` `find_transport()`, fpga UNSUPPORTED check
  - **Acceptance criteria**:
    - C test: `cadDeviceOpen("fm://spike")` returns `CAD_SUCCESS` (not UNSUPPORTED).
    - Transport name reported is "FuncModel" (not "FPGA").
  - **QA scenarios**:
    - happy: `ctest --test-dir build/software -R unsupported_uri` still passes; new test on `fm://spike` → FM transport opened.
    - failure: `cadDeviceOpen("fpga://anything")` still returns `CAD_ERROR_UNSUPPORTED`.
  - **Commit**: Y | `fix(runtime): URI prefix match for fm:// to avoid fpga false match (I-009)`

- [x] 4. Unify ggml-npu canonical/build source copy (I-012)
  - **What to do**:
    - Add a cmake `configure_file(ggml-npu/ggml-npu.cpp third_party/llama.cpp/ggml/src/ggml-npu/ggml-npu.cpp COPYONLY)` or create a symlink.
    - Ensure the build always picks up the canonical source without manual copy.
  - **Must NOT do**:
    - Do not change the `fetch_llama_cpp.py` semantics for fetching upstream llama.cpp — only ensure the npu backend file is synced.
  - **References**: `ggml-npu/CMakeLists.txt`, `third_party/llama.cpp/ggml/src/ggml-npu/ggml-npu.cpp`
  - **Acceptance criteria**:
    - Edit `ggml-npu/ggml-npu.cpp` (e.g., add a comment), run `cmake --build build/llama --target ggml-npu` → build picks up the change.
    - `diff ggml-npu/ggml-npu.cpp third_party/llama.cpp/ggml/src/ggml-npu/ggml-npu.cpp` is identical after cmake configure.
  - **QA scenarios**:
    - happy: modify ggml-npu.cpp → build → new code is compiled.
    - failure: revert ggml-npu.cpp to git state → build succeeds.
  - **Commit**: Y | `fix(build): symlink ggml-npu.cpp into third_party build tree (I-012)`

### Wave 2 — Hardening

- [x] 5. Add `struct_size` to `cad_execution_stats_t` (I-017)
  - **What to do**:
    - Add `uint32_t struct_size;` as first field of `cad_execution_stats_t`.
    - Define `#define CAD_EXECUTION_STATS_STRUCT_SIZE sizeof(cad_execution_stats_t)`.
    - Update all callers to set `stats.struct_size = CAD_EXECUTION_STATS_STRUCT_SIZE` before calling `cadFenceGetExecutionStats`.
    - `cadFenceGetExecutionStats` validates `struct_size >= sizeof(cad_execution_stats_t)` and handles smaller callers gracefully.
  - **Must NOT do**:
    - Do not change the existing field layout — `struct_size` is prepended.
  - **References**: `software/include/caduceus/runtime.h`, `software/src/runtime_core.c`
  - **Acceptance criteria**:
    - Existing `test_execution_stats` tests pass with `struct_size` set.
    - A caller compiled against an older struct (without `struct_size`) gets `CAD_ERROR_INVALID_ARGUMENT` (or graceful partial fill).
  - **QA scenarios**:
    - happy: `ctest --test-dir build/software -R execution_stats --output-on-failure` → PASS.
    - failure: set `struct_size = 0` → `cadFenceGetExecutionStats` returns error.
  - **Commit**: Y | `fix(abi): add struct_size to cad_execution_stats_t (I-017)`

- [x] 6. Harden `cadBufferRead/Write` integer overflow (I-011)
  - **What to do**:
    - Before `offset + size`, check `offset > NULL - size` (or `offset > SIZE_MAX - size`).
    - If overflow detected, return `CAD_ERROR_INVALID_ARGUMENT` with a clear error string.
  - **Must NOT do**:
    - Do not break the existing exact-boundary valid reads/writes.
  - **References**: `software/src/runtime_core.c` `cadBufferRead`, `cadBufferWrite`
  - **Acceptance criteria**:
    - `cadBufferRead(buf, UINT64_MAX, 1, data)` returns `CAD_ERROR_INVALID_ARGUMENT`.
    - `cadBufferRead(buf, 255, 1, data)` on a 256B buffer still succeeds.
  - **QA scenarios**:
    - happy: `ctest --test-dir build/software -R buffer_edge_cases --output-on-failure` → all 4 pass (including fixed overflow test).
    - failure: `cadBufferRead(buf, 100, UINT64_MAX - 50, data)` also returns `INVALID_ARGUMENT`.
  - **Commit**: Y | `fix(runtime): integer overflow check in cadBufferRead/Write (I-011)`

- [x] 7. Verify VADD and RMSNorm in W3-T2 signoff through Host Runtime without proxy
  - **What to do**:
    - `CAD_OP_VADD` (0x0F) and `CAD_OP_SFU_RMSNORM` (0x17) are already lowered by `lower.c` (via `write_vector_desc` / `write_sfu_desc`) and covered in `test_command_lowering.cpp`. Verify they actually work end-to-end through the Host Runtime API (`fm://python` or `fm://spike`).
    - In the W3-T2 signoff script (`scripts/run_runtime_spike_signoff.py`), replace the SFU SiLU proxy for the RMSNorm scenario with a real `CAD_OP_SFU_RMSNORM` command blob, and add a dedicated Vector VADD scenario (or replace the existing SFU SiLU proxy for VADD).
    - Confirm the fence completes and the output matches the CPU oracle.
  - **Must NOT do**:
    - Do not modify `lower.c` if it already handles VADD/RMSNorm correctly.
    - Do not add new opcodes beyond what is already defined.
  - **References**: `software/compiler/lower.c` `supported()` and `write_vector_desc`/`write_sfu_desc`, `software/tests/test_command_lowering.cpp`, `scripts/run_runtime_spike_signoff.py`
  - **Acceptance criteria**:
    - RMSNorm scenario in the signoff uses `CAD_OP_SFU_RMSNORM` (not SiLU proxy).
    - VADD scenario in the signoff uses `CAD_OP_VADD` (not SFU SiLU proxy).
    - Both scenarios pass (fence COMPLETED, output matches oracle).
  - **QA scenarios**:
    - happy: `ctest --test-dir build/software -R command_lowering --output-on-failure` → PASS. Then run signoff with updated scenarios.
    - failure: attempt RMSNorm with invalid shape → lowerer returns `CAD_LOWER_INVALID_SHAPE`.
  - **Commit**: Y | `test(signoff): verify VADD and RMSNorm end-to-end through Host Runtime`

- [x] 8. Unify transport error propagation through runtime
  - **What to do**:
    - Add `transportErrorToString(cad_error_t, char *buf, size_t len)` to transport vtable (default: `cadErrorString`).
    - FM transport overrides to include FM-specific context (e.g., "FM transport: socket write failed").
    - runtime `cadQueueSubmit` and `cadFenceGetExecutionStats` use this for user-visible errors.
  - **Must NOT do**:
    - Do not remove existing `cadErrorString` — keep it as the generic fallback.
  - **References**: `software/src/transport_fm.cpp`, `software/include/caduceus/cad_transport.h`
  - **Acceptance criteria**:
    - When FM transport fails a submit due to broken socket, `cadErrorString` returns a message containing "FM transport".
    - RTL transport failure includes "RTL transport" context.
  - **QA scenarios**:
    - happy: `./build/software/test_fm_e2e_submit fm://unix?path=/nonexistent` → `cadErrorString` mentions socket/transport.
    - failure: NULL transport pointer → generic error, no crash.
  - **Commit**: Y | `refactor(transport): unified transport-specific error context in cadErrorString`

### Wave 3 — Quality / Observability

- [x] 9. Fix ASan mock transport test leaks
  - **What to do**:
    - Audit all C/C++ test files using `open_mock_device()` — ensure `cadDeviceClose()` is called before exit.
    - Add `atexit(cleanup)` or test harness teardown where appropriate.
    - Run full ASan build: no leaks from close-e2e-command-gap code.
  - **Must NOT do**:
    - Do not modify production code paths for test-only cleanup.
  - **References**: `software/tests/test_*.c`, `software/tests/test_*.cpp`
  - **Acceptance criteria**:
    - `cmake -S software -B build/software-asan ... -DCADUCEUS_BUILD_TESTS=ON && cmake --build build/software-asan && ctest --test-dir build/software-asan` → all tests leak-free.
  - **QA scenarios**:
    - happy: ASan build ctest reports 0 leaks.
    - failure: intentionally omit one `cadDeviceClose` → ASan reports the leak.
  - **Commit**: Y | `test(runtime): add cadDeviceClose to all mock transport tests (ASan)`

- [x] 10. Pin flatc / pip flatbuffers version and document
  - **What to do**:
    - Lock `pip install flatbuffers==25.2.10` in CI and document in `requirements.txt`.
    - Verify `flatc --version` is 25.2.10 in CI.
    - Add a script `scripts/check_flatc_version.sh` that exits non-zero on mismatch.
  - **Must NOT do**:
    - Do not regenerate all Python FlatBuffers files yet — only pin version and verify consistency.
  - **References**: `software/CMakeLists.txt`, `gen/caduceus_device_protocol/`, `requirements.txt`
  - **Acceptance criteria**:
    - CI bootstrap step verifies flatc version matches pip package.
    - `scripts/check_flatc_version.sh` exits 0 if versions match, non-zero otherwise.
  - **QA scenarios**:
    - happy: `bash scripts/check_flatc_version.sh` → exit 0.
    - failure: install mismatched flatc version → exit non-zero with clear message.
  - **Commit**: Y | `chore(build): pin flatc and pip flatbuffers to 25.2.10, add version check`

- [x] 11. Enrich signoff evidence with explicit NPU/CPU op breakdown
  - **What to do**:
    - Extend `qwen3b_signoff_runner.py` to emit `npu_ops: {MMUL:N, SFU:M, VECTOR:P}` and `cpu_fallback_ops: ["ROPE", ...]` in evidence JSON.
    - Update `config/qwen3b-signoff.json` schema to accept these fields.
  - **Must NOT do**:
    - Do not change existing gate pass/fail logic.
  - **References**: `sim/signoff/qwen3b_signoff_runner.py`, `config/qwen3b-signoff.json`
  - **Acceptance criteria**:
    - `PYTHONPATH=sim:gen python3 scripts/run_qwen3b_software_signoff.py positive --device fm://python --evidence .omo/evidence/task-w3t11.json` → evidence JSON contains `npu_ops` and `cpu_fallback_ops` fields. Verify with: `python3 -c "import json; d=json.load(open('.omo/evidence/task-w3t11.json')); assert 'npu_ops' in d; assert 'cpu_fallback_ops' in d"`
  - **QA scenarios**:
    - happy: evidence JSON parsable by aggregator `--strict`.
    - failure: run with mock:// → `npu_ops_executed = 0` documented correctly.
  - **Commit**: Y | `feat(signoff): emit npu_ops and cpu_fallback_ops in signoff evidence JSON`

- [x] 12. Regenerate or block 10 stale evidence files
  - **What to do**:
    - Re-run original verification commands for each stale evidence file. If prerequisites are missing (e.g., Spike binary, RTL environment), mark as `BLOCKED` with reason.
    - Re-run `aggregate_software_signoff.py --require l0,l1,l2,l3,l4,l5,framework --evidence .omo/evidence/task-22-release-signoff-rerun.json --strict` → exit 0.
  - **Must NOT do**:
    - Do not fabricate evidence — if it cannot be regenerated, mark BLOCKED.
  - **References**: `.omo/evidence/task-{3,4,8,9,12,13,14,18,19,20,21}*.{json,csv,log}`
  - **Acceptance criteria**:
    - Aggregator `--strict` exits 0 with all evidence PASS or BLOCKED (no FAIL).
  - **QA scenarios**:
    - happy: `PYTHONPATH=sim python3 scripts/aggregate_software_signoff.py --require l0,l1,l2,l3,l4,l5,framework --strict` → exit 0, all evidence PASS or BLOCKED (no FAIL).
    - failure: remove a fresh evidence file → aggregator reports FAIL, not PASS.
  - **Commit**: Y | `docs(evidence): regenerate 10 stale evidence files for strict aggregator`

- [x] 13. Structured logging across Runtime / Transport / Server
  - **What to do**:
    - Define `CAD_LOG_TRACE=0, CAD_LOG_DEBUG=1, CAD_LOG_INFO=2, CAD_LOG_WARN=3, CAD_LOG_ERROR=4` in runtime.h.
    - Runtime and transport log via `CAD_LOG(level, fmt, ...)` which respects `CADUCEUS_LOG_LEVEL` env var.
    - Python device server: add `LOGGER = logging.getLogger("caduceus.device")` with `CADUCEUS_LOG_LEVEL` mapping.
    - Replace existing `printf`/`fprintf`/`print` debugging output with leveled logs.
    - Production default: `CADUCEUS_LOG_LEVEL=WARN` (only warnings and errors).
  - **Must NOT do**:
    - Do not make logging performance-significant on the hot path (TRACE/DEBUG are compiled out in release builds).
  - **References**: `software/src/runtime_core.c`, `software/src/transport_fm.cpp`, `sim/device_server.py`
  - **Acceptance criteria**:
    - `CADUCEUS_LOG_LEVEL=DEBUG ./build/software/test_fm_e2e_mmul fm://...` outputs structured log lines.
    - `CADUCEUS_LOG_LEVEL=WARN` (default) suppresses DEBUG/INFO/TRACE.
    - No raw `printf` left in runtime/transport source.
  - **QA scenarios**:
    - happy: run with `CADUCEUS_LOG_LEVEL=TRACE` → see detailed submit/fence/buffer lifecycle.
    - failure: run with `CADUCEUS_LOG_LEVEL=WARN` on a broken socket → only error logs, no debug spam.
  - **Commit**: Y | `feat(logging): structured leveled logging across Runtime/Transport/Server`

- [x] 14. Auto-detect NPU/CPU op split in Qwen blk.0 evidence
  - **What to do**:
    - Extend `qwen3b_signoff_runner.py` or `ggml-npu/ggml-npu.cpp` to track per-op execution location (NPU vs CPU).
    - Emit `npu_ops_executed` and `cpu_fallback_ops` as structured evidence.
    - Verify that the split is deterministic across repeated runs.
  - **Must NOT do**:
    - Do not change `supports_op()` semantics.
  - **References**: `sim/signoff/qwen3b_signoff_runner.py`, `ggml-npu/ggml-npu.cpp`
  - **Acceptance criteria**:
    - `PYTHONPATH=sim:gen python3 scripts/run_qwen3b_software_signoff.py positive --device fm://python` → evidence JSON records `npu_ops_executed >= 1` and `cpu_fallback_ops` list explicitly (e.g., `["ROPE", "MUL_MAT(unsupported_layout)"]`).
    - Repeated runs produce identical op split.
  - **QA scenarios**:
    - happy: signoff runner outputs structured evidence → F4 aggregator can check `npu_ops_executed > 0`.
    - failure: `CADUCEUS_NPU_STRICT=1` with unsupported op → evidence marks it as `cpu_fallback_ops`, not silent.
  - **Commit**: Y | `feat(signoff): auto-detect NPU/CPU op split in Qwen blk.0 evidence`

## Commit Strategy
- One atomic commit per todo using the commit subjects listed above.
- Implementation and its tests land in the same commit.
- Do NOT commit: build directories (`build/`), virtual environments (`.venv_*`), firmware build artifacts, spike plugin binaries, evidence files (`.omo/evidence/`).
- Preserve unrelated dirty paths from ongoing work.

## Success Criteria
1. **Spike E2E correctness**: blk.0 decode gate exercises real MXU/SFU compute through Spike firmware (`mmul > 0, sfu > 0`).
2. **Multi-connection device_server**: `cadDeviceClose → cadDeviceOpen` cycles work without server restart.
3. **ABI hardened**: `cad_execution_stats_t` has `struct_size`; buffer overflows are caught.
4. **CMD-IR coverage**: VADD and RMSNorm pass end-to-end through the Host Runtime signoff (no proxy ops).
5. **ASan clean**: All mock transport tests leak-free.
6. **Aggregator green**: `aggregator --strict` exits 0.
7. **Observable**: structured logging across all three layers.
8. **Evidence complete**: signoff evidence enumerates NPU vs CPU ops explicitly.
