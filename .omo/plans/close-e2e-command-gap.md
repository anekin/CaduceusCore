# close-e2e-command-gap - Work Plan
## TL;DR (For humans)
> Summary: Close the end-to-end command execution gap in the CaduceusCore NPU software stack, as identified by the 2026-07-28 review report. The Host Runtime currently only submits NOP commands and the FM transport discards command data — this plan fixes both and proves the full chain works with a single MMUL hard gate and llama.cpp real offload.
> Phase constraint: RTL real transport and FPGA real transport are deferred. This plan closes the gap from Host Runtime through Func Model (Python and Spike firmware), not through RTL/FPGA hardware.
> Deliverables:
> - Trusted CI and evidence aggregator (no assume-pass, no stale evidence, no masked failures);
> - `cadCommandListAppendExecuteBlob()` API in the stable C Host Runtime ABI;
> - FM transport that forwards real command payload via FlatBuffers `SubmitRequest.cmdBlob`;
> - Single non-NOP MMUL hard gate through `fm://python` and `fm://spike` with independent CPU oracle;
> - Real-firmware signoff driven through Host Runtime, not direct Python ring/doorbell;
> - llama.cpp backend real offload — supported partition submits to `fm://`, with observable NPU execution counts and hard fail on silent CPU fallback;
> - SoC golden observable contract freeze and differential/fault anti-vacuity fix.
> Effort: XL — 26 todos across 5 waves (Phase 0–3). Wave 1 establishes trust; Wave 2 builds the Runtime API; Wave 3 proves the single-op gate; Wave 4 extends to chain + SoC golden; Wave 5 wires llama.cpp real offload.
> Risk: Medium — the wire-format reconciliation between `cad_command_blob_t` header format and `device_server.py` flat layout is the primary risk; buffer address model must be locked; ASan/UBSan will expose memory-safety issues in new serialization paths.
## Scope

### In-scope
1. Fix evidence aggregator "assume pass" fallback paths and CI trustworthiness (stale detection, continue-on-error, mock-only signoff).
2. Extend Host Runtime C ABI with a non-NOP command append API (`cadCommandListAppendExecuteBlob`) and implement command data serialization in `runtime_core.c`.
3. Fix `transport_fm.cpp` `fm_submit()` to stop discarding `cmd_data` and populate the FlatBuffers `SubmitRequest.cmdBlob` payload.
4. Mark `fpga://` transport as `UNSUPPORTED` rather than silently mapping to mock.
5. Wire the Func Model device server's existing `_execute_on_model()` through the full Host Runtime → FM transport path.
6. Implement a single non-NOP MMUL end-to-end hard gate: a C/C++ program using ONLY the public Host Runtime API (`fm://`), submitting a real Qwen-shape MMUL command whose output is verified against an independent CPU oracle.
7. Extend the real-firmware signoff to be Host-Runtime-driven (no direct Python ring/doorbell construction bypassing the Runtime).
8. Wire llama.cpp backend to submit real NPU commands (not NOP) for supported partitions, with observable NPU execution counts and hard fail on silent CPU fallback.
9. Freeze a SoC golden observable contract from the ABI schema, descriptor layout, ring buffer, doorbell, and INTC definitions.
10. Fix the differential/fault-injection checker to require actual fault detection (anti-vacuity) — not just prove mutation was executed.
11. Fix the ring entry ABI mismatch in `device_server.py:325` where `desc_addr` is read as 8 bytes (`<IQI`) but firmware reads it as 4 bytes (`uint32_t`).
12. Clean up stale `software/build/libcaduceus_runtime.so` symlink so a clean checkout can bootstrap.

### Must NOT do
- Do NOT modify RTL datapath (`rtl/mxu/`, `rtl/sfu/`, `rtl/vector/`, `rtl/soc/`).
- Do NOT implement real FPGA transport — only mark `fpga://` as `UNSUPPORTED`.
- Do NOT claim performance signoff or timing guarantees.
- Do NOT add new framework model support (new models, new ops beyond what `supports_op` already covers).
- Do NOT expand ExecuTorch beyond its current scaffold (Phase 4 is deferred).
- Do NOT introduce a second command serialization format — reuse the existing `cad_command_blob_t` encode/decode and the FlatBuffers `SubmitRequest.cmdBlob` wire format.
## Verification strategy
> Zero human intervention — all verification is agent-executed.

- **Test decision**: TDD throughout — write failing tests first, then implement, then verify green.
- **Unit layer**: CTest (`software/` build), Python pytest (`PYTHONPATH=sim` and `PYTHONPATH=sim:gen` for FlatBuffers tests), firmware `make -C firmware` build with zero warnings.
- **Integration layer**: the single MMUL non-NOP gate (todo ~P1-5) is the hard integration gate — a C program using ONLY public `cad*` APIs, driving `fm://python` and `fm://spike`, with independent CPU oracle comparison.
- **CI layer**: GitHub Actions `caduceus-core-ci.yml` must run from a clean checkout, produce all build artifacts, and reject stale evidence. No `continue-on-error` on critical gates.
- **Evidence policy**: every todo has happy and failure QA scenarios, each with an agent-executable command and evidence path under `.omo/evidence/`. Failure scenarios that inherently require a code-level mutation (e.g., removing a detector, disabling validation) are verified by the TDD red-green cycle — the test first passes (proving the detection works), then the protection is removed and the test fails (proving the detection was real). Evidence is captured as the pytest/CTest output log. Aggregator reads evidence with `--no-stale-check` REMOVED (opt-in, not opt-out).
- **Firmware policy**: Python `NPUFirmware` is a fast oracle for development; real Spike firmware (compiled `npu_firmware_spike.elf` from the same C source) is the mandatory signoff gate for every gate that exercises the engine path.
- **Anti-vacuity policy**: fault-injection tests must prove the checker actually detected the fault (NOT just that the mutation was applied). Every corruption scenario includes an `expected_detector` field, and passing requires the detector to actually fire.

## Guardrail traceability
> Maps every SW-01..SW-10 and GR-01..GR-08 from the review report to the plan item that addresses or explicitly defers it.

| Guardrail | Finding | Addressed by |
|-----------|---------|-------------|
| **SW-01** | Host Runtime only has NOP | Todo 6 (`AppendExecuteBlob`), 7 (serialization) |
| **SW-02** | FM transport discards payload | Todo 9 (`fm_submit` populates `cmdBlob`) |
| **SW-03** | llama.cpp falls back to CPU | Todos 22 (single MMUL via fm://), 25 (strict hard fail) |
| **SW-04** | ExecuTorch also NOP-only | Scope §Must NOT: "Do NOT expand ExecuTorch (Phase 4 deferred)" |
| **SW-05** | Firmware signoff bypasses Runtime | Todo 12 (signoff through Host Runtime API) |
| **SW-06** | Buffer/address/lifecycle untested | Todos 13 (`cadBufferGetDeviceAddress`), 15 (buffer edge cases) |
| **SW-07** | Qwen gates use mock:// | Todos 24 (blk.0 via fm://), 26 (decode via fm://spike) |
| **SW-08** | `fpga://` maps to mock | Todo 8 (`fpga://` → `UNSUPPORTED`) |
| **SW-09** | CI untrusted | Todos 1 (aggregator), 2 (CI fix), 5 (clean-checkout bootstrap) |
| **SW-10** | Error/recovery coverage gaps | Todos 14 (MMUL negative), 17 (ring/INTC boundary), 19 (anti-vacuity), 25 (fallback) |
| **GR-01** | Missing SoC observable contract | Todo 20 (golden contract freeze) |
| **GR-02** | Missing front-door hard gate | Todo 13 (single MMUL gate through fm://) |
| **GR-03** | Python vs real firmware unverified | Todo 18 (Python/Spike equivalence comparison) |
| **GR-04** | Differential checker weak | Todo 19 (fault-injection anti-vacuity) |
| **GR-05** | Boundary/concurrency gaps | Todo 17 (ring wrap, INTC, reset edge cases) |
| **GR-06** | Qwen must go through SoC path | Todo 24 (full-shape Qwen blk.0 via fm://) |
| **GR-07** | Evidence not reproducible | Todos 1 (strict aggregator), 4 (evidence migration), 5 (bootstrap) |
| **GR-08** | FM golden vs performance boundary | Scope §Must NOT + Todo 20: no timing/performance in contract |

## Execution strategy

### Waves overview

| Wave | Phase | Focus | Estimated todos | Dependencies |
|------|-------|-------|-----------------|--------------|
| W1 | Phase 0 | Trust repair — CI, aggregator, baseline | 5–7 | None (can start immediately) |
| W2 | Phase 1a | Runtime API + transport payload forwarding | 5–7 | W1 (CI must be trusted to validate) |
| W3 | Phase 1b | Firmware path + single MMUL hard gate | 6–8 | W2 (need runtime API + transport) |
| W4 | Phase 2 | Chain, SoC golden contract, anti-vacuity | 6–8 | W3 (need single-op path working) |
| W5 | Phase 3 | llama.cpp real offload | 5–7 | W4 (need SoC golden for correctness) |

Each wave: 5–8 todos. Intra-wave parallelism where dependency matrix permits.

### Critical path

```
W1(CI/aggregator) → W2(Runtime API) → W3(MMUL gate) → W4(SoC golden) → W5(llama.cpp)
```

### Parallelism within waves

Within each wave, independent tasks start together. The dependency matrix below defines which tasks block which. Tasks with no intra-wave dependencies can run in parallel.

### Dependency matrix

| Todo | Depends on | Blocks | Can parallelize with |
|------|------------|--------|---------------------|
| W1-T1 (aggregator fix) | None | W1-T5 | W1-T2, W1-T3, W1-T4 |
| W1-T2 (CI fix) | None | W1-T5 | W1-T1, W1-T3, W1-T4 |
| W1-T3 (symlink fix) | None | None directly | W1-T1, W1-T2, W1-T4 |
| W1-T4 (stale evidence migration) | None | None directly | W1-T1, W1-T2, W1-T3 |
| W1-T5 (clean-checkout baseline) | W1-T1, W1-T2 | W2-T1 | None |
| W2-T1 (runtime API: AppendExecuteBlob) | W1-T5 | W2-T2, W3-T1 | W2-T3 |
| W2-T2 (cmd serialization in runtime core) | W2-T1 | W2-T4, W3-T1 | None |
| W2-T3 (fpga:// → UNSUPPORTED) | W1-T5 | None directly | W2-T1 |
| W2-T4 (fm_submit populates cmd_blob) | W2-T2 | W3-T1 | W2-T5 |
| W2-T5 (rtl_submit populates cmd_blob) | W2-T2 | None directly | W2-T4 |
| W3-T1 (device_server wired through Host Runtime) | W2-T4 | W3-T3 | W3-T2 |
| W3-T2 (firmware signoff through Host Runtime) | W3-T1 | W3-T3 | None |
| W3-T3 (single MMUL hard gate, happy) | W3-T1, W3-T2 | W4-T1 | None |
| W3-T4 (single MMUL negative: corruption, timeout, reset) | W3-T3 | W4-T1 | W3-T5 |
| W3-T5 (buffer lifecycle edge cases) | W3-T1 | None directly | W3-T4 |
| W4-T1 (MXU/SFU/Vector/DMA chain through Host Runtime) | W3-T3 | W4-T4 | W4-T2 |
| W4-T2 (ring wrap / completion / INTC edge cases) | W3-T3 | W4-T4 | W4-T1 |
| W4-T3 (Python vs Spike firmware equivalence) | W3-T2 | W4-T4 | None |
| W4-T4 (differential/fault anti-vacuity fix) | W4-T1, W4-T2, W4-T3 | W5-T1 | W4-T5 |
| W4-T5 (SoC golden observable contract freeze) | None directly | W5-T1 | W4-T4 |
| W4-T6 (ring entry ABI mismatch fix) | W3-T1 | None | W4-T4, W4-T5 |
| W5-T1 (llama.cpp: single MMUL via fm://) | W4-T4 | W5-T3 | W5-T2 |
| W5-T2 (execution counter — FM returns real op/byte counts) | W3-T1 | W5-T3 | W5-T1 |
| W5-T3 (llama.cpp: full-shape blk.0 gate) | W5-T1, W5-T2 | W5-T4 | None |
| W5-T4 (silent CPU fallback → hard fail) | W5-T1 | None | W5-T3 |
| W5-T5 (single token decode gate via fm://spike) | W5-T3 | Final wave | None |
## Todos

> Implementation + Test = ONE todo. Never separate. Target 5–8 per wave.

### Wave 1 — Trust Repair (Phase 0)

- [x] 1. Fix aggregator "assume pass" fallback paths
  - What to do:
    - Replace the 5 "assume pass" paths in `scripts/aggregate_software_signoff.py` with explicit `FAIL` or `MISSING` verdicts for unrecognized evidence:
      - Line 235: catch-all JSON → `"fail"` with reason, not `"pass"`;
      - Line 206: non-standard verdict in records → `"fail"`, not `"pass"`;
      - Line 295: log file >20 bytes → `"partial"` (existence is NOT evidence of passing);
      - Line 327: CSV without verdict column → `"fail"`, not `"pass"`;
      - Line 345: unknown file extension → `"missing"`, not `"pass"`.
    - Make `--no-stale-check` the opt-in flag (add `--allow-stale`), and make staleness rejection the default.
    - Add a `--strict` mode that exits non-zero on PARTIAL or any non-PASS tier.
  - Must NOT do:
    - do not break existing evidence files that follow the documented format.
  - Parallelization: can start immediately | Wave 1 | Blocks W1-T5
  - References:
    - `scripts/aggregate_software_signoff.py`:235,206,295,327,345
    - `.omo/evidence/task-22-release-signoff.json` — current example
  - Acceptance criteria:
    - `PYTHONPATH=sim python3 -m pytest sim/tests/test_software_signoff_aggregator.py -q` → all tests pass including new negative tests;
    - an empty JSON evidence file returns `"fail"` (not `"pass"`);
    - a log file with >20 bytes of error output returns `"partial"` (not `"pass"`);
    - `--allow-stale` is required to skip staleness; without it, stale evidence is rejected.
  - QA scenarios:
    - happy: `PYTHONPATH=sim python3 scripts/aggregate_software_signoff.py --require l0,l1 --evidence .omo/evidence/task-22-w1t1-strict.json` → exit 0, all recognized evidence PASS, stale REJECTED.
    - failure: `PYTHONPATH=sim python3 -m pytest sim/tests/test_software_signoff_aggregator.py -q -k 'empty_json_fails or log_gt20_not_pass or unknown_verdict_fails' 2>&1 | tee .omo/evidence/task-w1t1-neg.log`.
  - Commit: Y | `fix(aggregator): reject "assume pass" — unrecognized evidence is FAIL, not PASS`

- [x] 2. Fix CI trustworthiness — stale detection, continue-on-error, mock-only
  - What to do:
    - Remove `--no-stale-check` from CI line 181; add `--allow-stale` only where intentionally needed.
    - Remove `continue-on-error: true` from L3 Spike steps (lines 94, 97); make Spike prereq failures a FAIL tier, not masked.
    - Keep L5 FPGA job `continue-on-error: true` but add an explicit annotation in the CI output: "L5 expected blocked."
    - Add a CI step that verifies `libcaduceus_runtime.so` is a real file (not broken symlink or missing) before the release aggregator runs.
    - Document which tiers use `mock://` vs real device in CI comments.
  - Must NOT do:
    - do not remove the `CADUCEUS_DEVICE: mock://` global env yet — real-device CI is a separate future step.
  - Parallelization: can start immediately | Wave 1 | Blocks W1-T5
  - References:
    - `.github/workflows/caduceus-core-ci.yml`:21,94,97,119,181
    - `software/build/libcaduceus_runtime.so` — current state
  - Acceptance criteria:
    - `git diff -- .github/workflows/caduceus-core-ci.yml` shows `--allow-stale` (not `--no-stale-check`);
    - L3 Spike pytest step no longer has `continue-on-error: true`;
    - new symlink check step exists and fails on a broken symlink.
  - QA scenarios:
    - happy: CI `release_aggregator` job passes with strict staleness on fresh evidence.
    - failure: `ls -la software/build/libcaduceus_runtime.so` → broken symlink → CI step fails with clear error.
  - Commit: Y | `ci(workflow): strict staleness, remove continue-on-error from L3, add symlink guard`

- [x] 3. Fix stale `software/build/libcaduceus_runtime.so` symlink
  - What to do:
    - Remove the stale symlink at `software/build/libcaduceus_runtime.so` (it points to `../../build/software/libcaduceus_runtime.so`).
    - Add `software/build/` to `.gitignore` so the symlink is never checked in again.
    - Update `software/CMakeLists.txt` or build scripts so the symlink is created at cmake configure time, not tracked in git.
  - Must NOT do:
    - do not break the Python ctypes binding at `software/python/caduceus_runtime.py` that resolves the library path.
  - Parallelization: can start immediately | Wave 1 | Blocks nothing
  - References:
    - `software/build/libcaduceus_runtime.so` — current symlink target
    - `software/python/caduceus_runtime.py` — library resolution logic
    - `.gitignore`:28 — already has `.omo/*.log` but nothing for `software/build/`
  - Acceptance criteria:
    - `git status` shows `software/build/libcaduceus_runtime.so` as deleted from tracking (and `.gitignore` covers `software/build/`);
    - `cmake -S software -B build/software -DCADUCEUS_BUILD_TESTS=ON && cmake --build build/software` creates the symlink at `build/software/libcaduceus_runtime.so` or `software/build/` is no longer used;
    - Python binding smoke test still resolves the library correctly.
  - QA scenarios:
    - happy: `rm -rf build/software && cmake -S software -B build/software -DCADUCEUS_BUILD_TESTS=ON && cmake --build build/software && PYTHONPATH=sim:build/software python3 -c "from caduceus_runtime import Device; d=Device('mock://'); print(d.caps.device_name)"` → prints device name.
    - failure: `ls software/build/libcaduceus_runtime.so 2>&1` → "No such file or directory" (symlink no longer tracked).
  - Commit: Y | `fix(build): remove stale tracked symlink, gitignore software/build/`

- [x] 4. Migrate existing task evidence to pass stricter aggregator
  - What to do:
    - Audit all evidence files under `.omo/evidence/` for compliance with the new strict aggregator rules (W1-T1).
    - For each evidence file that would fail under strict mode: either fix the format to include explicit verdict fields, or regenerate the evidence by re-running the original command.
    - Evidence files that cannot be regenerated (Spike binary missing, RTL environment unavailable) are annotated with `"verdict": "blocked"` and `"blocked_reason": "prerequisite unavailable"`.
    - Update the `docs/func-model-signoff-checklist.md` to reflect current strict-mode verdicts.
  - Must NOT do:
    - do not change historical evidence verdicts without re-running the original verification;
    - do not fabricate evidence — if it cannot be regenerated, mark it BLOCKED.
  - Parallelization: can start immediately | Wave 1 | Blocks nothing directly, but necessary for W1-T5 baseline
  - References:
    - `.omo/evidence/` — all current evidence files
    - `docs/func-model-signoff-checklist.md` — current checklist
  - Acceptance criteria:
    - `PYTHONPATH=sim python3 scripts/aggregate_software_signoff.py --require l0,l1,l2,l3,l4,l5,framework --evidence .omo/evidence/task-22-w1t4-strict.json` → exits 0, no evidence files fail with "unknown format" or empty JSON;
    - all BLOCKED evidence files have explicit `blocked_reason` fields;
    - all PASS evidence files have explicit `verdict` fields.
  - QA scenarios:
    - happy: aggregator rerun with strict mode → all tiers have clear PASS/PARTIAL/BLOCKED, no "unknown" or defaulted verdicts.
    - failure: `PYTHONPATH=sim python3 scripts/aggregate_software_signoff.py --require l3 --evidence .omo/evidence/task-w1t4-test.json` with a corrupted evidence file → fails with clear error, not silent pass.
  - Commit: Y | `docs(evidence): migrate evidence to strict aggregator format`

- [x] 5. Clean-checkout reproducibility baseline
  - What to do:
    - Create a script `scripts/ci_bootstrap.sh` that: (1) installs system deps (cmake, gcc, flatc, pip), (2) runs `cmake -S software -B build/software -DCADUCEUS_BUILD_TESTS=ON && cmake --build build/software`, (3) runs `PYTHONPATH=sim python3 scripts/build_software_release.py --clean --install-prefix build/install`, (4) exits with the aggregate exit code. The firmware build (`make -C firmware`) is moved to a separate `scripts/ci_bootstrap_firmware.sh` that documents the RISC-V toolchain prerequisite.
    - Document the clean-checkout bootstrap procedure in a new section of `docs/func-model-signoff-checklist.md`.
  - Must NOT do:
    - do not require Spike binary or RISC-V toolchain for the baseline bootstrap — `make -C firmware` is handled by `ci_bootstrap_firmware.sh` and documented as a separate prerequisite step;
    - do not fail the baseline if `ci_bootstrap_firmware.sh` prerequisites are missing — skip with a clear message.
  - Parallelization: depends on W1-T1, W1-T2 | Wave 1 | Blocks W2
  - References:
    - `scripts/build_software_release.py` — existing build script
    - `.github/workflows/caduceus-core-ci.yml` — CI reference
  - Acceptance criteria:
    - `bash scripts/ci_bootstrap.sh 2>&1 | tee .omo/evidence/task-w1t5-bootstrap.log` → exit 0;
    - all cmake, ctest, pip install, and smoke test steps succeed;
    - the script can be run on a fresh Ubuntu 22.04 machine with only git, cmake, gcc, python3, and pip installed.
  - QA scenarios:
    - happy: `bash scripts/ci_bootstrap.sh 2>&1 | tee .omo/evidence/task-w1t5-bootstrap.log`.
    - failure: intentionally break `software/CMakeLists.txt` → `bash scripts/ci_bootstrap.sh 2>&1 | tee .omo/evidence/task-w1t5-neg.log` exits non-zero with clear error.
  - Commit: Y | `ci(bootstrap): add clean-checkout reproducibility baseline script`

### Wave 2 — Runtime API + Transport Payload (Phase 1a)

- [x] 6. Add `cadCommandListAppendExecuteBlob` to Host Runtime C ABI
  - What to do:
    - Add `cadCommandListAppendExecuteBlob(cad_command_list_t cmd_list, cad_buffer_t blob_buffer, uint64_t blob_offset, uint64_t blob_size)` to `software/include/caduceus/runtime.h`.
    - The function records a reference to the encoded command blob buffer (a `cad_buffer_t` that the caller has already filled with encoded `cad_command_blob_t` data via `cadBufferWrite`).
    - Update `software/src/runtime_core.h` `cad_command_list_impl_t` to store an array of `{cad_buffer_t blob_buf; uint64_t offset; uint64_t size;}` entries (up to `max_entries`).
    - The command list's `entry_count` tracks both NOPs and ExecuteBlob entries.
  - Must NOT do:
    - do NOT change the existing `cadCommandListAppendNop` API;
    - do NOT embed command IR types into the runtime header — the runtime only sees opaque `cad_buffer_t` references;
    - do NOT let the runtime interpret or validate the blob contents (that is the transport/server's job).
  - Parallelization: depends on W1-T5 | Wave 2 | Blocks W2-T2, W3-T1
  - References:
    - `software/include/caduceus/runtime.h:220` — current `cadCommandListAppendNop`
    - `software/src/runtime_core.h:49-55` — current `cad_command_list_impl_t`
    - `software/compiler/command_ir.h` — blob encode/decode API
    - Vulkan `vkCmdExecuteCommands` / CUDA graph launch — pattern reference
  - Acceptance criteria:
    - `cmake --build build/software && ctest --test-dir build/software -R runtime_conformance --output-on-failure` includes new tests for `AppendExecuteBlob`;
    - calling `cadCommandListAppendExecuteBlob` with a NULL buffer returns `CAD_ERROR_INVALID_ARGUMENT`;
    - exceeding `max_entries` returns `CAD_ERROR_OUT_OF_MEMORY`;
    - the command list's `submitted` flag still works (cannot submit same list twice).
  - QA scenarios:
    - happy: `cmake -S software -B build/software -DCADUCEUS_BUILD_TESTS=ON && cmake --build build/software && ctest --test-dir build/software -R execute_blob_conformance --output-on-failure 2>&1 | tee .omo/evidence/task-w2t1-happy.log`.
    - failure: `ctest --test-dir build/software -R execute_blob_negative --output-on-failure 2>&1 | tee .omo/evidence/task-w2t1-neg.log`.
  - Commit: Y | `feat(runtime): add cadCommandListAppendExecuteBlob for non-NOP command submission`

- [x] 7. Implement command data serialization in `runtime_core.c` submit path
  - What to do:
    - In `cadQueueSubmit()` (`runtime_core.c:308`), replace the current `transport.submit(..., cmd_list, cmd_list->entry_count, ...)` with proper serialization.
    - Iterate the command list entries. For each NOP entry: emit nothing (increment a counter). For each ExecuteBlob entry: read the blob buffer's data via `cadBufferRead` (or pass the buffer handle directly).
    - Build a serialized payload: a header (uint32_t nop_count, uint32_t blob_count, uint32_t total_cmd_count) followed by the raw bytes of each blob, concatenated. The transport forwards this serialized buffer AS-IS to the device server. The device server's `_execute_on_model()` is extended to parse the serialized header, then for each blob: flatten from the headered `cad_command_blob_t` format to the flat `ring_entries (24B each) || descriptors (60B each)` layout it currently expects.
    - Pass this serialized buffer as `void* cmd_data` to the transport's submit.
    - After successful submit, free the serialized buffer.
  - Must NOT do:
    - do NOT interpret the blob contents — only concatenate raw bytes;
    - do NOT leak the serialized buffer on submit failure.
  - Parallelization: depends on W2-T1 | Wave 2 | Blocks W2-T4, W3-T1
  - References:
    - `software/src/runtime_core.c:319-325` — current submit call
    - `software/src/runtime_core.h:49-55` — `cad_command_list_impl_t` entry structure
  - Acceptance criteria:
    - CTest passes: mock transport receives serialized cmd_data with correct blob count and raw bytes;
    - a command list with 2 NOPs + 1 ExecuteBlob produces cmd_data with `nop_count=2, blob_count=1`;
    - an ExecuteBlob referencing a freed buffer returns `CAD_ERROR_INVALID_HANDLE` at submit time;
    - the serialized buffer is freed on submit success AND on submit failure.
  - QA scenarios:
    - happy: `ctest --test-dir build/software -R cmd_serialization --output-on-failure 2>&1 | tee .omo/evidence/task-w2t2-happy.log`.
    - failure: `ctest --test-dir build/software -R cmd_serialization_negative --output-on-failure 2>&1 | tee .omo/evidence/task-w2t2-neg.log`.
  - Commit: Y | `feat(runtime): serialize command list entries for transport submit`

- [x] 8. Mark `fpga://` transport as `UNSUPPORTED`
  - What to do:
    - In `software/src/runtime_core.c`, change the `fpga://` transport registry entry from mapping to mock to returning `CAD_ERROR_UNSUPPORTED` with a clear error string: "fpga:// transport not yet implemented — no FPGA platform available".
    - Remove the `fpga://` fallback-to-mock branch entirely.
    - Update `software/include/caduceus/runtime.h` documentation on URI schemes to note `fpga://` is reserved but not yet available.
  - Must NOT do:
    - do not remove the `cad_transport_fpga_ops` vtable or `transport_fpga.cpp` — the code stays for future implementation;
    - do not change `rtl://` or `mock://` behavior.
  - Parallelization: depends on W1-T5 | Wave 2 | Blocks nothing directly
  - References:
    - `software/src/runtime_core.c:37-41` — transport registry
    - `software/src/transport_fpga.cpp` — FPGA transport code
  - Acceptance criteria:
    - `cadDeviceOpen` with `uri = "fpga://..."` returns `CAD_ERROR_UNSUPPORTED`;
    - the error string from `cadErrorString(CAD_ERROR_UNSUPPORTED)` mentions "fpga";
    - `mock://`, `fm://`, and `rtl://` continue to work.
  - QA scenarios:
    - happy: C test opens `mock://` → success; same test with `fpga://` → `CAD_ERROR_UNSUPPORTED`.
    - failure: `ctest --test-dir build/software -R unsupported_uri --output-on-failure 2>&1 | tee .omo/evidence/task-w2t3.log`.
  - Commit: Y | `fix(transport): fpga:// returns UNSUPPORTED instead of silently mapping to mock`

- [x] 9. Fix `fm_submit()` to populate `SubmitRequest.cmdBlob`
  - What to do:
    - In `software/src/transport_fm.cpp`, replace `(void)cmd_data;` at line 542 with code that reads the serialized command data (from W2-T2 format: header + raw blobs).
    - Populate `cd::SubmitRequestT req.cmd_blob` with the serialized command payload bytes (the header + all blob bytes).
    - The FlatBuffers `SubmitRequest` already has `cmd_blob:[ubyte]` — only the population code is missing.
    - Verify that `cmd_blob` contains: a uint32 nop_count, a uint32 blob_count, and the concatenated raw blob bytes.
  - Must NOT do:
    - do NOT change the FlatBuffers schema (`device_protocol.fbs`);
    - do NOT interpret blob contents — just forward raw bytes;
    - do NOT break the existing CRC-32 validation (raw-wire checksum still computed correctly).
  - Parallelization: depends on W2-T2 | Wave 2 | Blocks W3-T1
  - References:
    - `software/src/transport_fm.cpp:539-552` — current `fm_submit()`
    - `software/src/transport_fm.cpp:164-230` — `fm_send_request()` message framing
    - `software/schema/device_protocol.fbs:78-82` — `SubmitRequest` schema
    - `sim/device_server.py:576-588` — server-side `_do_submit()` that reads `cmdBlob`
  - Acceptance criteria:
    - A C test that submits an ExecuteBlob through `fm://` transport verifies that `cmd_blob` is populated with non-zero bytes;
    - `fm_send_request` completes successfully (exit 0, no transport errors);
    - the device server's `_do_submit` receives the correct `cmdBlob` bytes (verified through a dedicated test or log).
  - QA scenarios:
    - happy: `PYTHONPATH=sim:gen python3 -m pytest sim/tests/test_device_protocol_cpp.py -q -k submit_with_blob 2>&1 | tee .omo/evidence/task-w2t4-happy.log`.
    - failure: submit with NULL cmd_data → transport returns error, not crash.
  - Commit: Y | `fix(transport): fm_submit populates SubmitRequest.cmdBlob with serialized command data`

- [x] 10. Fix `rtl_submit()` to forward command payload
  - What to do:
    - Same as W2-T4 but for `software/src/transport_rtl.cpp:646-649`.
    - The RTL transport uses the same `SubmitRequest` FlatBuffers schema via a mock RTL endpoint.
    - Populate `cmd_blob` identically to the FM transport.
  - Must NOT do:
    - do not require live cocotb/VCS to test — use the existing fake fixture.
  - Parallelization: depends on W2-T2 | Wave 2 | Blocks nothing directly
  - References:
    - `software/src/transport_rtl.cpp:646-649` — current `rtl_submit()`
    - `sim/rtl_protocol_endpoint.py` — RTL mock endpoint
  - Acceptance criteria:
    - RTL transport conformance test (CTest: `rtl_transport_conformance`) passes with blob payload forwarding;
    - RTL mock endpoint receives the blob bytes (verified via test assertion or op log).
  - QA scenarios:
    - happy: `ctest --test-dir build/software -R rtl_transport_conformance --output-on-failure 2>&1 | tee .omo/evidence/task-w2t5.log`.
    - failure: submit with corrupted blob header → mock endpoint returns error → test fails with clear error.
  - Commit: Y | `fix(transport): rtl_submit forwards command payload to mock endpoint`

### Wave 3 — Firmware Path + Single MMUL Hard Gate (Phase 1b)

- [x] 11. Wire device server `_execute_on_model` through Host Runtime path
  - What to do:
    - Create an end-to-end integration test (`software/tests/test_fm_e2e_submit.c`) that:
      1. Opens `fm://python` device via Host Runtime C API.
      2. Allocates a command blob buffer.
      3. Builds a single valid MMUL command blob (using `cad_command_blob_t` + encode).
      4. Writes the encoded blob to the buffer via `cadBufferWrite`.
      5. Creates a command list, appends `cadCommandListAppendExecuteBlob`.
      6. Submits via `cadQueueSubmit`, waits via `cadFenceWait`.
      7. Reads the output buffer via `cadBufferRead`.
    - Extend `sim/device_server.py` `_execute_on_model()` to handle the serialized format from W2-T2 (header: nop_count, blob_count, then raw blobs).
    - For each blob in the payload: **first, flatten** the `cad_command_blob_t` header format into the flat `ring_entries (24B each) || descriptors (60B each)` layout expected by `device_server.py` `_execute_on_model()`. The existing blob encoder produces a headered format (header + buffer table + 32B ring entries + descriptors); the flattening step strips the header and remaps 32B ring entries to 24B entries.
    - Then execute the flat payload as currently implemented in `_execute_on_model()`.
    - Start/stop the device server process around the test (`sim/device_server.py` for Python firmware, `sim/device_server.py --spike` for Spike firmware).
    - **Terminology**: throughout this plan, `fm://python` and `fm://spike` are documentation shorthands. The actual Host Runtime URI is `fm://unix?path=/tmp/caduceus_....sock`. The firmware type is selected by the device server's `--spike` flag, not by the URI scheme.
    - **FlatBuffers note**: W5-T2 (`SubmitResponse` schema extension) requires regenerating `gen/` code with `flatc --cpp --python`. Generated artifacts are committed alongside the schema change per commit strategy.
    - do not require Spike binary for the initial Python-firmware path — `fm://python` is sufficient;
    - do not bypass the public Host Runtime API — the test must use ONLY `cad*` functions.
  - Parallelization: depends on W2-T4 | Wave 3 | Blocks W3-T2, W3-T3
  - References:
    - `sim/device_server.py:304-347` — `_execute_on_model()` current implementation
    - `software/src/transport_fm.cpp` — FM transport (now with blob forwarding from W2-T4)
    - `sim/device_protocol.py` — FlatBuffers codec
    - `software/compiler/command_ir_codec.py` — Python blob encode/decode
  - Acceptance criteria:
    - The C integration test compiles, links, runs against a started `device_server `;
    - `cadFenceWait` returns `CAD_SUCCESS`, `cadFenceGetStatus` returns `CAD_FENCE_COMPLETED`;
    - the device server's `_worker_loop` processes the command without errors (log confirms `_execute_on_model` called);
    - output buffer contains non-zero data.
  - QA scenarios:
    - happy: `python3 sim/device_server.py  --socket /tmp/caduceus_w3t1.sock & sleep 1; cmake --build build/software && ./build/software/test_fm_e2e_submit fm://unix?path=/tmp/caduceus_w3t1.sock 2>&1 | tee .omo/evidence/task-w3t1-happy.log; kill %1`.
    - failure: submit with corrupted blob → device server returns error → `cadFenceWait` returns `CAD_ERROR_DEVICE_LOST` or fence status is ERROR.
  - Commit: Y | `feat(integration): wire device_server _execute_on_model through Host Runtime submit path`

- [x] 12. Real-firmware signoff through Host Runtime (no direct Python ring construction)
  - What to do:
    - Rewrite `scripts/run_runtime_spike_signoff.py` to use the Host Runtime C API (via ctypes Python binding at `software/python/caduceus_runtime.py`) instead of directly constructing `FuncModel(use_spike=True)` and writing ring/doorbell in Python.
    - The signoff script:
      1. Starts `sim/device_server.py --spike --socket /tmp/caduceus_spike_signoff.sock`.
      2. Opens `fm://unix?path=/tmp/caduceus_spike_signoff.sock` via Host Runtime.
      3. Runs the same 9 scenarios (mmul_smoke, sfu_rmsnorm, vector_vadd, dma_copy, chain, corrupted_descriptor, unknown_opcode, reset_recovery, timeout_behavior) through `cadBufferAllocate/Write`, `cadCommandListAppendExecuteBlob`, `cadQueueSubmit`, `cadFenceWait`, `cadBufferRead`.
      4. Compares results against the same golden oracles as before.
    - Keep the existing direct-Python signoff runner as a reference/development tool (rename: `scripts/run_runtime_spike_signoff_direct.py`).
  - Must NOT do:
    - do NOT lose the existing signoff coverage (9/9 scenarios);
    - do NOT require Spike binary on the test machine — precondition checks must fail cleanly.
  - Parallelization: depends on W3-T1 | Wave 3 | Blocks W3-T3
  - References:
    - `scripts/run_runtime_spike_signoff.py` — existing signoff runner (to be refactored)
    - `software/python/caduceus_runtime.py` — Python ctypes binding
    - `firmware/npu_firmware.c:441-556` — firmware dispatch_cmd
  - Acceptance criteria:
    - `PYTHONPATH=sim:gen python3 scripts/run_runtime_spike_signoff.py --require-prereqs 2>&1 | tee .omo/evidence/task-w3t2.log` → all 9 scenarios pass through Host Runtime;
    - the signoff script does NOT directly call `FuncModel(use_spike=True)`, `model.pcie.tlp_write()`, or `model.firmware.doorbell` — only public Runtime API and device server startup.
  - QA scenarios:
    - happy: `PYTHONPATH=sim:gen python3 scripts/run_runtime_spike_signoff.py --require-prereqs 2>&1 | tee .omo/evidence/task-w3t2-happy.log`.
    - failure: `PYTHONPATH=sim:gen python3 -m pytest sim/tests/test_runtime_real_firmware.py -q -k 'incompatible_abi or corrupted_descriptor or missing_prereq_fails' --require-spike 2>&1 | tee .omo/evidence/task-w3t2-neg.log`.
  - Commit: Y | `refactor(signoff): real-firmware signoff uses Host Runtime API, not direct Python ring construction`

- [x] 13. Single non-NOP MMUL end-to-end hard gate (happy path)
  - What to do:
    - **Buffer-address model**: `cad_buffer_t` handles allocated via `cadBufferAllocate(fm://)` map to device DRAM addresses in the device server's address space. The device server assigns a stable physical DRAM address to each allocated buffer (default: `0x8000_0000 + offset` within the DRAM window managed by `FmDeviceServer`). The command IR lowerer's `host_addr` fields for input/output buffers must be populated with these device physical addresses, obtained via a new `cadBufferGetDeviceAddress(cad_buffer_t, uint64_t *addr)` function added to the Runtime API. Returns `CAD_ERROR_INVALID_HANDLE` for NULL/invalid/mock:// buffers; `CAD_ERROR_UNSUPPORTED` for transports that don't support direct device addressing. Internal scratch buffers (no host data) have addresses auto-assigned by the lowerer.
    - Implement a C test program (`software/tests/test_fm_e2e_mmul.c`) that:
      1. Generates random weights, activations, scales (Qwen blk.0 shape: M=1, K=2048, N=2048, or smaller M=1, K=128, N=64 for fast CI).
      2. Independently computes the expected MMUL result on CPU (golden oracle).
      3. Opens `fm://python`, allocates buffers for weights/activations/scales/output.
      4. Builds a command IR blob (MMUL op), lowers, encodes.
      5. Writes blob + input data to device buffers.
      6. Submits, waits for fence, reads output.
      7. Compares output with CPU golden (bit-exact for INT32 accumulator, tolerance for FP16 conversion if applicable).
    - The test must be runnable with `fm://python` (no Spike required) for fast CI.
  - Must NOT do:
    - do NOT use mock:// for this gate — it MUST exercise the real `fm://` transport and device server;
    - do NOT hardcode descriptor addresses — use the command IR lowerer.
  - Parallelization: depends on W3-T1, W3-T2 | Wave 3 | Blocks W4-T1
  - References:
    - `software/compiler/command_ir.h` — `cad_op_mmul`, `cad_command_blob_lower`, `cad_command_blob_encode`
    - `sim/device_server.py:304-347` — `_execute_on_model`
    - `firmware/npu_firmware.c:446-517` — firmware MMUL dispatcher
    - `sim/func_model.py:121-146` — `host_write_command` for comparison
  - Acceptance criteria:
    - C test program exits 0, comparing output against independent CPU oracle;
    - MMUL result matches golden (bit-exact for INT32 or within tolerance for Q4_K_M quantized);
    - the test runs against a real `fm://python` device server (not mock);
    - evidence log records: device URI, blob size, command count, compute time, output match status.
  - QA scenarios:
    - happy: `python3 sim/device_server.py  --socket /tmp/caduceus_mmul.sock & sleep 1; cmake --build build/software && ./build/software/test_fm_e2e_mmul fm://unix?path=/tmp/caduceus_mmul.sock 2>&1 | tee .omo/evidence/task-w3t3-happy.log; kill %1`.
    - failure: `PYTHONPATH=sim:gen python3 -m pytest sim/tests/test_e2e_mmul.py -q -k 'corrupted_weight or zero_dim or missing_blob' 2>&1 | tee .omo/evidence/task-w3t3-neg.log`.
  - Commit: Y | `test(integration): single non-NOP MMUL end-to-end hard gate via fm:// transport`

- [x] 14. Single MMUL negative tests — corruption, timeout, reset recovery
  - What to do:
    - Extend the W3-T3 test program (`software/tests/test_fm_e2e_mmul.c`) with negative scenarios:
      1. Corrupted weight → output mismatch detected by oracle comparison.
      2. Zero-dimension MMUL (M=0) → device server returns error, fence signals ERROR.
      3. Device reset while command in-flight → fence signals ERROR, next command succeeds.
      4. Fence timeout → `cadFenceWait` with short timeout returns `CAD_ERROR_TIMEOUT`.
    - Each negative scenario must prove the error was actually detected (not just that the mutation was applied).
  - Must NOT do:
    - do not use mock transport for these tests — real `fm://` path must be exercised.
  - Parallelization: depends on W3-T3 | Wave 3 | Blocks W4-T1
  - References:
    - W3-T3 test program (`software/tests/test_fm_e2e_mmul.c`)
    - `software/src/runtime_core.c` — error propagation paths
    - `sim/device_server.py:358-405` — validation/rejection paths
  - Acceptance criteria:
    - All 4 negative scenarios produce the expected error code (not silent pass or wrong error);
    - corrupted weight scenario: oracle comparison fires, test exits non-zero with clear message;
    - zero-dim: `cadFenceGetStatus` returns `CAD_FENCE_ERROR`;
    - reset recovery: after reset, a new MMUL completes successfully;
    - timeout: `cadFenceWait` with 1ns timeout returns `CAD_ERROR_TIMEOUT`.
  - QA scenarios:
    - happy: `PYTHONPATH=sim:gen python3 -m pytest sim/tests/test_e2e_mmul_negative.py -q 2>&1 | tee .omo/evidence/task-w3t4.log`.
    - failure: remove the detection check from a corruption scenario → test must fail because the error is no longer caught.
  - Commit: Y | `test(integration): MMUL negative paths — corruption, zero-dim, reset, timeout`

- [x] 15. Buffer lifecycle edge cases via FM transport
  - What to do:
    - Add C tests covering:
      1. Buffer use-after-free: `cadBufferFree` then `cadBufferRead` → `CAD_ERROR_INVALID_HANDLE`.
      2. Buffer offset+size overflow: `cadBufferRead(buf, size-1, 2, ...)` → `CAD_ERROR_INVALID_ARGUMENT`.
      3. Double free: `cadBufferFree` twice → second call returns error.
      4. Submit with freed buffer: create blob in buffer, free buffer, then submit command list referencing it → submit fails.
    - All tests use `fm://` transport (not mock).
  - Must NOT do:
    - do not test mock-only edge cases — real FM transport must surface these errors.
  - Parallelization: depends on W3-T1 | Wave 3 | Blocks nothing directly
  - References:
    - `software/src/runtime_core.c:220-240` — buffer read/write validation
    - `software/src/transport_fm.cpp` — FM transport buffer ops
  - Acceptance criteria:
    - All 4 edge case tests pass with correct error codes;
    - use-after-free is caught before segfault (magic validation);
    - overflow test confirms the exact boundary check.
  - QA scenarios:
    - happy: `ctest --test-dir build/software -R buffer_edge_cases --output-on-failure 2>&1 | tee .omo/evidence/task-w3t5.log`.
    - failure: disable magic-number validation in the runtime → use-after-free must be caught before segfault.
  - Commit: Y | `test(runtime): buffer lifecycle edge cases via fm:// transport`

### Wave 4 — Chain, SoC Golden Contract, Anti-Vacuity (Phase 2)

- [x] 16. MXU/SFU/Vector/DMA chain through Host Runtime
  - What to do:
    - Extend the W3-T3 test to submit a multi-command chain: MMUL → SFU(RMSNorm) → Vector(ADD) → DMA_COPY (copy result to host-visible buffer).
    - Use `cadCommandListAppendExecuteBlob` multiple times (one blob per engine command, or one combined blob).
    - Verify that the final output matches the expected chain result (MMUL output → RMSNorm → VADD → readback).
    - Run against `fm://python` and, optionally, `fm://spike`.
  - Must NOT do:
    - do not hardcode descriptor addresses — use command IR;
    - do not use direct Python ring construction.
  - Parallelization: depends on W3-T3 | Wave 4 | Blocks W4-T4
  - References:
    - W3-T3 MMUL test program (`software/tests/test_fm_e2e_mmul.c`)
    - `firmware/npu_firmware.c:441-556` — dispatch_cmd chain handling
    - `scripts/run_runtime_spike_signoff.py:223-248` — existing chain scenario
  - Acceptance criteria:
    - Chain test passes: MMUL output flows into SFU, SFU output into Vector ADD, final result matches golden;
    - evidence records the command sequence (opcode order, descriptor addresses, blob sizes);
    - `fm://spike` path works (if Spike prerequisites are available).
  - QA scenarios:
    - happy: `./build/software/test_fm_e2e_chain fm://unix?path=/tmp/caduceus_chain.sock 2>&1 | tee .omo/evidence/task-w4t1-happy.log`.
    - failure: inject descriptor corruption in the chain → pipeline fails at the correct stage.
  - Commit: Y | `test(integration): MXU/SFU/Vector/DMA chain through Host Runtime`

- [x] 17. Ring wrap, completion, INTC, reset edge cases
  - What to do:
    - Add tests covering SoC boundary behaviors:
      1. Ring buffer wrap-around: submit enough commands to wrap the 16-entry ring, verify correct head/tail.
      2. Completion order: submit 3 commands, verify they complete in order (fence signals after last).
      3. INTC edge: ACK-before-PENDING (existing BUG-SOC-FM-008), multiple IRQs pending, mask/unmask.
      4. Reset with in-flight DMA: trigger device reset while a DMA is running, verify clean recovery.
      5. Malformed descriptor: unknown opcode, zero-size DMA, invalid address → firmware returns error status.
    - Use the Host Runtime path; Python ring construction allowed for INTC-only tests (they test firmware, not transport).
  - Must NOT do:
    - do not modify firmware behavior — only add test coverage.
  - Parallelization: depends on W3-T3 | Wave 4 | Blocks W4-T4
  - References:
    - `firmware/npu_firmware.c:571-603` — firmware_main() ring loop
    - `sim/mmio_bridge.py:580-586` — doorbell handling
    - `sim/mmio_bridge.py:587-603` — INTC handling
    - `docs/bugs/BUG-MXU-P9-001-doorbell-divergence.md` — known doorbell issues
  - Acceptance criteria:
    - All 5 edge case tests pass or are documented as known bugs with clear reproduction steps;
    - ring wrap test verifies head/tail arithmetic is correct;
    - reset recovery test confirms a subsequent command succeeds.
  - QA scenarios:
    - happy: `PYTHONPATH=sim:gen python3 -m pytest sim/tests/test_soc_boundary.py -q 2>&1 | tee .omo/evidence/task-w4t2.log`.
    - failure: submit a ring-wrap scenario with mismatched head/tail tracking → test must detect the off-by-one error.
  - Commit: Y | `test(soc): ring wrap, completion, INTC, reset boundary coverage`

- [x] 18. Python firmware vs Spike firmware equivalence comparison
  - What to do:
    - For each of the 9 signoff scenarios (W3-T2), run both Python `NPUFirmware` and real Spike firmware on the same inputs.
    - Compare: descriptor consumption order, MMIO write sequences, memory side effects, completion status, error codes, and IRQ state.
    - Produce a structured equivalence report: for each scenario, list (a) matching behaviors, (b) allowed differences (timing, Python-only debug logs), (c) unexplained differences (bugs to file).
  - Must NOT do:
    - do not require Spike binary for the Python-firmware path — Python path is always available;
    - do not claim equivalence where differences are unexplained.
  - Parallelization: depends on W3-T2 | Wave 4 | Blocks W4-T4
  - References:
    - `sim/miniv.py:434-516` — NPUFirmware._dispatch
    - `firmware/npu_firmware.c:441-556` — real firmware dispatch_cmd
    - `sim/func_model.py:78-99` — firmware selection logic
  - Acceptance criteria:
    - Equivalence report at `.omo/evidence/task-w4t3-equivalence.md` for all 9 scenarios;
    - at least 7/9 scenarios show full equivalence (descriptor, MMIO, memory, completion);
    - any unexplained differences are filed as bugs with bug IDs;
    - the report documents the ABI compatibility surface that must be maintained.
  - QA scenarios:
    - happy: `PYTHONPATH=sim:gen python3 scripts/compare_firmware_equivalence.py --scenarios all --report .omo/evidence/task-w4t3-equivalence.md 2>&1 | tee .omo/evidence/task-w4t3.log`.
    - failure: deliberately introduce a descriptor-field mismatch between Python and Spike firmware paths → equivalence report must flag it as `unexplained_difference`.
  - Commit: Y | `test(equivalence): compare Python firmware vs Spike firmware on all 9 signoff scenarios`

- [x] 19. Differential and fault-injection anti-vacuity fix
  - What to do:
    - Fix `sim/verification/differential.py` so that a fault-injection test does NOT pass simply because the injection was applied.
    - Require each `Action` in a fault scenario to declare an `expected_detector` field (the specific checker that must fire: e.g., `"mmul_output_mismatch"`, `"descriptor_corruption"`, `"ring_overflow"`).
    - After running the faulted scenario, verify that `expected_detector` actually fired (recorded in evidence as `detection_hit: true`).
    - If `expected_detector` did not fire, the scenario is `FAIL` with reason `"anti-vacuity: injection applied but no detector fired"`.
    - Add at least 3 new scenarios that test the anti-vacuity system itself: (a) a correct scenario with no fault → no false positive, (b) a corruption that is NOT detected by a specific checker → checker correctly reports no hit, (c) a corruption that IS detected → checker correctly reports hit.
  - Must NOT do:
    - do not break existing fault-injection scenarios — add `expected_detector` to each one.
  - Parallelization: depends on W4-T1, W4-T2, W4-T3 | Wave 4 | Blocks W5-T1
  - References:
    - `sim/verification/differential.py` — current fault detection logic
    - `sim/verification/fault_injector.py` — injection hooks
    - `sim/tests/test_verification_fault_injection.py` — existing tests
  - Acceptance criteria:
    - All existing fault scenarios updated with `expected_detector` fields;
    - anti-vacuity test (a): no fault → all checkers report no hit → PASS;
    - anti-vacuity test (b): corruption injected, wrong checker specified → FAIL;
    - anti-vacuity test (c): corruption injected, correct checker specified, checker fires → PASS.
  - QA scenarios:
    - happy: `PYTHONPATH=sim python3 -m pytest sim/tests/test_verification_fault_injection.py -q -k 'anti_vacuity' 2>&1 | tee .omo/evidence/task-w4t4.log`.
    - failure: remove the detector from a fault scenario → test fails with "no detector fired".
  - Commit: Y | `fix(verification): differential anti-vacuity — require expected_detector to actually fire`

- [x] 20. Freeze SoC golden observable contract
  - What to do:
    - Create `spec/soc_golden_contract.md` that freezes the set of architecture-observable behaviors Func Model must match for SoC RTL golden signoff:
      1. Register map (ABI schema: addresses, fields, access types, reset values).
      2. Descriptor layout (15-word MMUL/SFU/Vector/DMA/PICe_DMA descriptors).
      3. Ring buffer semantics (entry format, head/tail, wrap, empty/full).
      4. Doorbell semantics (HOST_TAIL, HOST_HEAD, NPU_HEAD, LAST_STATUS, COMPLETION_STATUS).
      5. INTC semantics (PENDING, ENABLE, THRESHOLD, ACK, IRQ sources 0-8).
      6. Crossbar address decode (SRAM 0x20000000, DRAM 0x80000000, MMIO 0x40000000+).
      7. PCIe/BAR TLP behavior (32-bit BAR, MMIO routing, DMA descriptor format).
      8. Reset behavior (all engine state cleared, doorbell zeroed, INTC masked).
      9. Error behavior (unknown opcode, invalid descriptor, DMA error, timeout).
    - Derive from `spec/npu_abi.json`, `firmware/npu-regmap.h`, and `gen/npu_abi_firmware.h`.
    - Add a `contract_check.py` script that verifies the golden contract against the generated ABI artifacts and reports drift.
  - Must NOT do:
    - do not define timing/performance requirements in the contract;
    - do not add implementation details not visible at the SoC interface.
  - Parallelization: depends on none directly (can read ABI schema anytime) | Wave 4 | Blocks W5-T1
  - References:
    - `spec/npu_abi.json` — authoritative ABI schema
    - `firmware/npu-regmap.h` — firmware register definitions
    - `gen/npu_abi.h`, `gen/npu_abi_pkg.sv` — generated artifacts
    - `docs/func-model-signoff-checklist.md` — current checklist
  - Acceptance criteria:
    - `spec/soc_golden_contract.md` exists with all 9 sections;
    - `PYTHONPATH=sim python3 scripts/contract_check.py --check` exits 0 (all ABI artifacts match contract);
    - a deliberate ABI mutation (e.g., change register offset in `npu_abi.json`) causes `contract_check.py --check` to fail.
  - QA scenarios:
    - happy: `python3 scripts/gen_npu_abi.py --check && PYTHONPATH=sim python3 scripts/contract_check.py --check 2>&1 | tee .omo/evidence/task-w4t5.log`.
    - failure: mutate a register offset → `contract_check.py --check` exits non-zero with drift report.
  - Commit: Y | `docs(soc): freeze SoC golden observable contract`

- [x] 21. Fix ring entry ABI mismatch in `device_server.py`
  - What to do:
    - In `sim/device_server.py:325`, change `struct.unpack_from("<IQI", ring_data, i * 24)` to `struct.unpack_from("<III", ring_data, i * 24)` to match the firmware's `cmd_entry_t` reading `desc_addr` as `uint32_t` (4 bytes), not `uint64_t` (8 bytes).
    - Adjust the ring entry packing at line 333 from `<IQI` + 8 bytes padding to `<III` + 12 bytes padding (or `<8I` for 32B alignment).
    - **IMPORTANT**: Also update the flattening code introduced in Todo 11 (W3-T1) to produce ring entries in the corrected `<III>` layout, so the entries produced by the flattening step match the reader's new format.
    - Verify that all existing device protocol tests still pass after the change.
    - Add a test that verifies the ring entry layout matches between Python and C firmware (golden vector test).
  - Must NOT do:
    - do not change the firmware `cmd_entry_t` — the firmware is correct (32-bit addresses are sufficient for the current model).
  - Parallelization: depends on W3-T1 | Wave 4 | Blocks nothing
  - References:
    - `sim/device_server.py:325` — current `<IQI` unpack
    - `firmware/npu_firmware.c:33-38` — `cmd_entry_t` with `uint32_t desc_addr`
    - `sim/tests/test_device_protocol.py` — existing protocol tests
  - Acceptance criteria:
    - All existing device protocol tests pass with the corrected layout;
    - a golden vector test packs a known ring entry in both Python and C, and the bytes are identical;
    - the `desc_addr` field correctly maps to `uint32_t` (values > 4GB are not used).
  - QA scenarios:
    - happy: `PYTHONPATH=sim:gen python3 -m pytest sim/tests/test_device_protocol.py sim/tests/test_device_protocol_cpp.py -q 2>&1 | tee .omo/evidence/task-w4t6.log`.
    - failure: create a ring entry with `desc_addr = 0xFFFFFFFF`, verify both Python and C read the same value.
  - Commit: Y | `fix(device_server): ring entry ABI — desc_addr is uint32, not uint64`

### Wave 5 — llama.cpp Real Offload (Phase 3)

- [x] 22. llama.cpp backend: submit a single supported MMUL node via `fm://`
  - What to do:
    - Modify `ggml-npu/ggml-npu.cpp` `npu_graph_compute()` to:
      1. Continue building the command IR blob via `npu_build_command_validation_blob()` (existing code, lines 402-439).
      2. Instead of submitting a NOP only (line 433), use the Host Runtime's new `cadCommandListAppendExecuteBlob` to submit the encoded blob to the NPU device.
      3. After the NPU submit completes (fence), verify the output tensor data by comparing with the CPU oracle result.
      4. Start with a SINGLE supported node (MUL_MAT with known Qwen shape).
    - The test scenario: a minimal ggml graph with one MUL_MAT node → NPU backend → FM transport → device server → firmware → engine → readback → CPU oracle comparison.
  - Must NOT do:
    - do NOT remove the CPU fallback path yet — it is still needed for unsupported nodes;
    - do NOT change the backend's `supports_op` logic.
  - Parallelization: depends on W4-T4 | Wave 5 | Blocks W5-T3
  - References:
    - `ggml-npu/ggml-npu.cpp:402-454` — `npu_graph_compute()`
    - `ggml-npu/ggml-npu.cpp:248-269` — MMUL op construction
    - `software/src/transport_fm.cpp` — FM transport (now with blob forwarding)
    - `sim/device_server.py` — device server
  - Acceptance criteria:
    - A ggml graph with one MUL_MAT node runs through the NPU backend;
    - backend calls `cadCommandListAppendExecuteBlob` (not `cadCommandListAppendNop`);
    - `cadFenceWait` returns success, output tensor matches CPU oracle (within Q4_K_M quantization tolerance);
    - the test can run against a started `fm://python` device server.
  - QA scenarios:
    - happy: `python3 sim/device_server.py  --socket /tmp/caduceus_llama.sock & sleep 1; CADUCEUS_DEVICE=fm://unix?path=/tmp/caduceus_llama.sock build/llama/bin/test-backend-ops test -b NPU -o MUL_MAT 2>&1 | tee .omo/evidence/task-w5t1-happy.log; kill %1`.
    - failure: `CADUCEUS_DEVICE=fm://unix?path=/tmp/caduceus_llama.sock build/llama/bin/test-backend-ops test -b NPU -o GELU 2>&1 | tee .omo/evidence/task-w5t1-neg.log` — GELU is unsupported, backend falls back to CPU cleanly.
  - Commit: Y | `feat(ggml): submit single supported MMUL node through fm:// transport`

- [x] 23. Execution counter — FM server returns real op/byte counts
  - What to do:
    - Extend `sim/device_server.py` `_execute_on_model()` to return execution statistics in the SubmitResponse or a separate query:
      - Number of ops actually executed (per engine: MMUL, SFU, Vector, DMA);
      - Number of bytes transferred (DMA read/write);
      - Firmware completion status word.
    - Extend the FlatBuffers `SubmitResponse` schema to include an optional `exec_stats` table with these fields.
    - Expose execution stats through the Host Runtime as a query function: `cadFenceGetExecutionStats(cad_fence_t, cad_execution_stats_t*)` that reads back the stats stored in the fence by the transport after a successful submit. (Design note: the original plan proposed `cadCommandListGetExecutionStats`, but command lists are semantically dead after submit — ownership transfers to the queue, and `validate_command_list()` rejects submitted handles. The fence outlives the submit and is the correct object for result retrieval.)
    - Error contract: returns `CAD_ERROR_NOT_READY` if fence has not yet signalled; `CAD_ERROR_INVALID_HANDLE` for NULL/invalid fence; `CAD_SUCCESS` with zero stats for NOP-only submission. Stats are stored on the fence by the transport during `fm_submit()` response processing.
    - Update the llama.cpp backend to call `cadFenceGetExecutionStats` after `cadFenceWait` and log the real execution counts.
  - Must NOT do:
    - do NOT allow stats to be populated by mock/config values — they must come from the actual device server response;
    - do NOT break backward compatibility — `SubmitResponse` without stats is still valid.
  - Parallelization: depends on W3-T1 | Wave 5 | Blocks W5-T3
  - References:
    - `sim/device_server.py:304-347` — `_execute_on_model()`
    - `software/schema/device_protocol.fbs:111` — `SubmitResponse` table
    - `ggml-npu/ggml-npu.cpp:419-439` — current submission path
  - Acceptance criteria:
    - After a successful MMUL submit, `cadFenceGetExecutionStats` returns `mmul_ops: 1, sfu_ops: 0, vector_ops: 0, dma_bytes: > 0`;
    - the stats match the actual command that was submitted (not a hardcoded value);
    - a NOP submit returns all-zero stats.
  - QA scenarios:
    - happy: `ctest --test-dir build/software -R execution_stats --output-on-failure 2>&1 | tee .omo/evidence/task-w5t2-happy.log`.
    - failure: submit with a fake/mock device that returns hardcoded stats → test detects stats provenance mismatch.
  - Commit: Y | `feat(runtime): execution stats query — FM server returns real op/byte counts`

- [x] 24. llama.cpp backend: full-shape Qwen blk.0 gate via `fm://`
  - What to do:
    - Extend the llama.cpp backend to handle the full Qwen2.5-3B block-0 graph:
      1. Partition all supported ops (MMUL, RMS_NORM, SOFT_MAX, ROPE, MUL, ADD) into the NPU backend.
      2. Lower the entire partition into a command IR blob.
      3. Submit via `cadCommandListAppendExecuteBlob` through `fm://` transport.
      4. Read back output hidden states and compare with CPU-only oracle (cosine similarity, max-abs-diff).
    - Re-run the Qwen 3B signoff runner (`scripts/run_qwen3b_software_signoff.py`) with `--device fm://python` (not `mock://`).
    - Update the signoff evidence to record: `device_uri: fm://python`, `npU_ops_executed: N`, `cpu_fallback_ops: M`, `execution_time_ms`.
  - Must NOT do:
    - do NOT use `mock://` for this gate — it must exercise the real `fm://` path;
    - do NOT claim PASS if ANY supported op was silently executed on CPU.
  - Parallelization: depends on W5-T1, W5-T2 | Wave 5 | Blocks W5-T4, W5-T5
  - References:
    - `ggml-npu/ggml-npu.cpp:192-400` — blob construction for all ops
    - `scripts/run_qwen3b_software_signoff.py` — existing signoff runner
    - `config/qwen3b-signoff.json` — signoff manifest
  - Acceptance criteria:
    - `PYTHONPATH=sim python3 scripts/run_qwen3b_software_signoff.py positive --device fm://python 2>&1 | tee .omo/evidence/task-w5t3-happy.log` → all 5 gates pass;
    - evidence records: NPU ops executed > 0, CPU fallback ops explicitly listed and justified (unsupported layouts, etc.);
    - full-shape blk.0 gate: cos_sim ≥ 0.99, max_abs_diff < 1e-3 (or appropriate Q4_K_M tolerance).
  - QA scenarios:
    - happy: `python3 sim/device_server.py  --socket /tmp/caduceus_qwen.sock & sleep 1; CADUCEUS_DEVICE=fm://unix?path=/tmp/caduceus_qwen.sock PYTHONPATH=sim python3 scripts/run_qwen3b_software_signoff.py positive --device fm://unix?path=/tmp/caduceus_qwen.sock 2>&1 | tee .omo/evidence/task-w5t3.log; kill %1`.
    - failure: `PYTHONPATH=sim python3 scripts/run_qwen3b_software_signoff.py negative --device fm://unix?path=/tmp/caduceus_qwen.sock 2>&1 | tee .omo/evidence/task-w5t3-neg.log` — corrupted weights detected.
  - Commit: Y | `feat(ggml): full-shape Qwen blk.0 gate through fm:// transport with real NPU execution`

- [x] 25. Silent CPU fallback → hard fail for NPU-expected ops
  - What to do:
    - In `ggml-npu/ggml-npu.cpp` `npu_graph_compute()`, after the NPU submit path, check which ops were executed on NPU vs CPU.
    - If any op was declared `supported` by `supports_op()` but was NOT executed on NPU (e.g., due to transport failure, blob lowering failure, or silent fallback), the gate must FAIL.
    - Add a backend flag: `CADUCEUS_NPU_STRICT=1` — when enabled, any unsupported-op-to-CPU fallback that was NOT explicitly declared unsupported is a hard error.
    - In strict mode, the Qwen gate must report: "NPU executed: X ops, CPU executed: Y ops (of which Z were expected fallbacks, W were UNEXPECTED)".
  - Must NOT do:
    - do NOT break the non-strict mode — development/debugging still needs flexible fallback.
  - Parallelization: depends on W5-T1 | Wave 5 | Blocks nothing directly
  - References:
    - `ggml-npu/ggml-npu.cpp:443-451` — CPU fallback path
    - `ggml-npu/ggml-npu.cpp:595-796` — `supports_op()`
  - Acceptance criteria:
    - With `CADUCEUS_NPU_STRICT=1`, a supported op that falls back to CPU causes the gate to FAIL;
    - the failure message names the op, node index, and reason for fallback;
    - non-strict mode (default) still allows fallback.
  - QA scenarios:
    - happy: `CADUCEUS_NPU_STRICT=1 CADUCEUS_DEVICE=fm://unix?path=/tmp/caduceus_strict.sock build/llama/bin/test-backend-ops test -b NPU 2>&1 | tee .omo/evidence/task-w5t4-happy.log`.
    - failure: `CADUCEUS_NPU_STRICT=1 CADUCEUS_DEVICE=fm://unix?path=/tmp/caduceus_strict.sock build/llama/bin/test-backend-ops test -b NPU -o UNSUPPORTED_OP 2>&1 | tee .omo/evidence/task-w5t4-neg.log`.
  - Commit: Y | `feat(ggml): strict mode — hard fail on silent CPU fallback for NPU-expected ops`

- [x] 26. Single token decode gate via `fm://spike`
  - What to do:
    - Implement a gate that runs a single Qwen2.5-3B decode token (prefill → first token) through the llama.cpp backend using `fm://spike`.
    - The gate: (1) loads the pinned GGUF model, (2) partitions the graph, (3) submits NPU partition through `fm://spike` with real Spike firmware, (4) CPU executes only unsupported nodes, (5) compares generated token with CPU-only reference.
    - This is the final proof that the full chain — framework → Runtime → transport → device server → Spike firmware → Func Model engine — works end-to-end for a real LLM workload.
  - Must NOT do:
    - do NOT use `mock://` or `fm://python` for this gate — it MUST use `fm://spike` with real firmware;
    - do NOT skip this gate if Spike prerequisites are missing — fail cleanly with a clear requirement.
  - Parallelization: depends on W5-T3 | Wave 5 | Blocks Final Wave
  - References:
    - W5-T3 Qwen gate (same backend code)
    - `scripts/run_qwen3b_software_signoff.py`
    - `sim/device_server.py --spike`
    - `spike_src/build/spike` + `firmware/build/npu_firmware_spike.elf`
  - Acceptance criteria:
    - `PYTHONPATH=sim python3 scripts/run_qwen3b_software_signoff.py positive --device fm://spike --gate single_decode_token 2>&1 | tee .omo/evidence/task-w5t5.log`;
    - generated token matches CPU-only reference;
    - evidence records: firmware ELF hash, plugin hash, Spike binary hash, NPU ops executed vs CPU fallback counts;
    - if Spike prerequisites are missing, the gate fails with `BLOCKED` (not PASS).
  - QA scenarios:
    - happy: `python3 sim/device_server.py --spike --socket /tmp/caduceus_token.sock & sleep 1; PYTHONPATH=sim python3 scripts/run_qwen3b_software_signoff.py positive --device fm://unix?path=/tmp/caduceus_token.sock --gate single_decode_token 2>&1 | tee .omo/evidence/task-w5t5-happy.log; kill %1`.
    - failure: `PYTHONPATH=sim python3 -m pytest sim/tests/test_qwen3b_software_signoff.py -q -k unsupported_device_uri 2>&1 | tee .omo/evidence/task-w5t5-neg.log`.
  - Commit: Y | `test(llama): single token decode gate via fm://spike with real firmware`
## Final verification wave
> Runs in parallel. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.

- [x] F1. Plan compliance audit
  - Verify every acceptance criterion against actual agent-executable evidence.
  - Confirm no RTL datapath changes, no FPGA real transport claims, no performance signoff claims.
  - Confirm `fm://python` and `fm://spike` paths produce distinct evidence, no mock substitution.
  - Confirm the single MMUL hard gate (task 13) uses real `fm://` transport, not `mock://`.
  - Evidence: `.omo/evidence/final-plan-compliance.md`.

- [x] F2. Code quality and ABI review
  - Review the new `cadCommandListAppendExecuteBlob` API for ABI forward-compatibility (`struct_size` convention).
  - Verify command serialization/deserialization is deterministic across C and Python.
  - Run ASan/UBSan on all C/C++ Runtime and transport builds.
  - Verify the ring-entry ABI fix (task 21) produces bit-identical ring entries in C and Python.
  - Verify FlatBuffers schema backward compatibility (new `exec_stats` field is optional).
  - Verify `fpga://` transport registration returns clean `UNSUPPORTED`, not mock fallback.
  - Evidence: `.omo/evidence/final-code-quality.md`.

- [x] F3. Real manual QA
  - Drive the actual installed software surfaces:
    - Single MMUL through `fm://python`: allocate buffers, build blob, submit, fence wait, readback, compare oracle.
    - Single MMUL through `fm://spike`: same test with real Spike firmware.
    - Chain (MMUL → SFU → Vector → DMA) through `fm://python`.
    - llama.cpp backend with one supported op via `fm://python` — verify NPU execution count > 0.
    - Qwen full-shape blk.0 through `fm://python` with observable op/byte counts.
    - CI bootstrap script on a clean checkout.
  - Verify observable outputs, failure behavior, and provenance.
  - Evidence: `.omo/evidence/final-manual-qa.md`.

- [x] F4. Scope fidelity and evidence audit
  - Confirm no RTL functional fix, performance signoff, or new framework model support entered scope.
  - Run aggregator in strict mode (no `--no-stale-check`) and verify all evidence passes or is explicitly BLOCKED with reason.
  - Verify that llama.cpp gate evidence records `npu_ops_executed` (not zero) and `cpu_fallback_ops` (explicitly documented).
  - Verify the SoC golden contract is self-consistent with generated ABI artifacts.
  - Evidence: `.omo/evidence/final-scope-fidelity.md`.
## Commit strategy

- One atomic commit per todo using the commit subjects listed above.
- Implementation and its tests land in the same commit.
- Generated artifacts (`gen/`) are committed alongside their schema/generator changes.
- Do NOT commit: build directories (`build/`), virtual environments (`.venv_*`), firmware build artifacts (`firmware/build/*.elf`, `*.hex`, `*.map`, `*.o`), Spike plugin binaries (`spike_src/plugins/*.so`), evidence files (`.omo/evidence/`), or temporary test sockets/logs.
- Preserve unrelated dirty paths: `.omo/notepads/phase6-rtl-verification/`, `.omo/notepads/func-model-gap-closure/`.
- Do NOT squash across ABI, Runtime, transport, or framework boundaries until all final reviewers approve.
## Success criteria

The end-to-end command gap is closed when:

1. **Trust baseline**: CI runs from a clean checkout, no `--no-stale-check`, no `continue-on-error` masking failures on critical gates, aggregator rejects unrecognized evidence as FAIL not PASS.
2. **Runtime API**: `cadCommandListAppendExecuteBlob()` exists, validates inputs, and is the production path for submitting non-NOP commands.
3. **Transport payload**: `fm_submit()` populates `SubmitRequest.cmdBlob` with flattened ring entries and descriptors — `(void)cmd_data;` is gone.
4. **Single MMUL hard gate**: A C/C++ program using ONLY public Host Runtime API (`fm://`) allocates buffers, builds an MMUL blob, submits, fences, reads back, and matches an independent CPU oracle.
5. **Real firmware via Host Runtime**: `scripts/run_runtime_spike_signoff.py` drives the 9 signoff scenarios through the Host Runtime API and `fm://spike` — no direct Python ring construction, no bypassing the transport.
6. **llama.cpp real offload**: The NPU backend submits at least one supported MMUL node through `fm://`, with NPU execution count > 0, hard fail on silent CPU fallback for expected-NPU ops.
7. **Full-shape Qwen blk.0 via `fm://`**: The Qwen gate runs through `fm://python`, records real NPU ops executed, and matches CPU oracle.
8. **SoC golden contract**: `spec/soc_golden_contract.md` exists with 9 sections, `contract_check.py --check` exits 0, and ABI drift is detected.
9. **Differential anti-vacuity**: Fault injection tests require the checker to actually fire — no pass from mutation-only.
10. **Evidence aggregator trust**: Strict-mode aggregator produces clear PASS/PARTIAL/BLOCKED/FAIL verdicts with no "assume pass" fallbacks; all evidence is regeneratable from committed source.

RTL real transport, FPGA real transport, performance signoff, multi-model support, and ExecuTorch full integration are NOT in scope for this plan.
