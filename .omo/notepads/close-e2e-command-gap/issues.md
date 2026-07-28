# close-e2e-command-gap Issues

## W1-T4: Evidence migration blockers

- **Task 15 ggml lifecycle BLOCKED**: `build/llama/bin/test-backend-ops` with `fm://python` fails because the Func Model device server cannot start (`ModuleNotFoundError: No module named 'caduceus_device_protocol'`). Evidence annotated with `VERDICT: BLOCKED` and `blocked_reason: prerequisite unavailable`.
- **Task 20 FPGA NO-GO remains BLOCKED**: no FPGA platform available; unchanged from baseline.
- **Strict aggregator regression risk**: evidence files created before W1-T1 that lack explicit verdict fields will now fail. All known historical evidence under `.omo/evidence/` has been migrated, but any newly added evidence must include an explicit `verdict` field (or `verdict`/`status`/`result`/`pass` column for CSV).

## W1-T1: Fix aggregator assume-pass fallbacks (2026-07-28)

**Status**: Fixed and verified (30/30 tests passing).

### Changes made to `scripts/aggregate_software_signoff.py`

1. **Line 206** — Non-standard record verdict: `verdicts.append("pass")` → `verdicts.append("fail")`
2. **Line 235** — Catch-all JSON fallback: `return "pass"` → `return "fail"`
3. **Line 295** — Log >20 bytes fallback: `return "pass"` → `return "partial"`
4. **Line 327** — CSV without verdict column: `return "pass"` → `return "fail"`
5. **Line 345** — Unknown extension fallback: `return "pass" if st_size > 0` → `return "missing"`
6. **CLI** — Replaced `--no-stale-check` with `--allow-stale`; added `--strict` flag
7. **Exit logic** — `--strict` exits non-zero on any non-PASS status

### Changes made to `sim/tests/test_software_signoff_aggregator.py`

- Updated 3 CLI test invocations: `--no-stale-check` → `--allow-stale`
- Added 5 new negative tests:
  - `test_empty_json_object_returns_fail`
  - `test_log_over_20_bytes_no_verdict_returns_partial`
  - `test_unknown_verdict_in_record_returns_fail`
  - `test_allow_stale_cli_accepts_stale_evidence`
  - `test_strict_exits_nonzero_on_partial`

### Edge cases considered
- Existing evidence files (`.json`, `.csv`, `.log`, `.txt`) unaffected — all primary evidence patterns are correctly recognized before hitting fallbacks.
- `.diff` and `.md` files in evidence dir are not discovered by `discover_evidence_files()`, so the unknown-extension fallback is defense-in-depth only.

## W1-T3: Stale `software/build/libcaduceus_runtime.so` symlink fix

- **Issue**: Tracked symlink at `software/build/libcaduceus_runtime.so` pointed to `../../build/software/libcaduceus_runtime.so` which doesn't exist on a fresh checkout. Clean clone would have a broken symlink tracked in git.
- **Fix**: 
  1. `git rm --cached software/build/libcaduceus_runtime.so` — removed from git tracking.
  2. Added `software/build/` to `.gitignore` — prevents accidental re-tracking.
  3. Added `add_custom_command(TARGET caduceus_runtime_shared POST_BUILD ...)` in `software/CMakeLists.txt` — creates the symlink at cmake build time (`cmake -E make_directory` + `cmake -E create_symlink`).
- **Verification**:
  - `cmake -S software -B build/software -DCADUCEUS_BUILD_TESTS=ON` → configures OK.
  - `cmake --build build/software` → builds all targets, creates symlink as POST_BUILD step.
  - Python smoke test (`PYTHONPATH=software/python:build/software`) → `device_name: b'CaduceusCore NPU'` — binding works.
  - `git status` shows `D software/build/libcaduceus_runtime.so` (staged deletion), no tracked content under `software/build/`.
- **Evidence**: `.omo/evidence/task-w1t3.log`

## 2026-07-28 — W1-T2 CI Trustworthiness

### I-001: `--no-stale-check` in release aggregator bypasses staleness detection
- **File**: `.github/workflows/caduceus-core-ci.yml`, line 181 (old)
- **Root cause**: `--no-stale-check` flag passed to `aggregate_software_signoff.py` skips all staleness checks
- **Fix**: Replaced with `--allow-stale` which detects staleness but continues

### I-002: `continue-on-error: true` on L3 Spike steps masked failures
- **File**: `.github/workflows/caduceus-core-ci.yml`, lines 94, 97 (old)
- **Root cause**: Two L3 Spike steps had `continue-on-error: true`, allowing toolchain build/manifest failures to pass silently
- **Fix**: Removed both `continue-on-error: true` — Spike tier now fails the pipeline on failure

### I-003: No symlink guard before release aggregator
- **File**: `.github/workflows/caduceus-core-ci.yml`, release_aggregator job
- **Root cause**: `software/build/libcaduceus_runtime.so` is a checked-in symlink to a build artifact. In CI (fresh checkout), the target doesn't exist yet — the aggregator could consume stale/dangling artifacts without noticing
- **Fix**: Added `"Verify release artifact is a real file (not symlink)"` step that exits with `::error::` if the file is a symlink or missing

## W1-T5: Clean-checkout bootstrap (2026-07-29)

### No blockers — scripts verified and passing

Both `ci_bootstrap.sh` and `ci_bootstrap_firmware.sh` complete with exit 0 on this machine. Evidence captured to `.omo/evidence/task-w1t5-bootstrap.log` and `.omo/evidence/task-w1t5-firmware.log`.

- **Software baseline**: cmake configure + build, CTest 15/15, release build with all smoke tests (C, C++, Python mock, Python fm://) PASS.
- **Firmware baseline**: RISC-V toolchain available → firmware built successfully with 3 artifacts verified.
- **Known limitation**: `build_software_release.py --clean` in the bootstrap duplicates the cmake build work (the release script does its own clean+build). This is intentional — the outer cmake build step proves standalone build works; the release step proves the full packaging pipeline. Redundant but harmless for a one-shot baseline.

## W2-T6: ExecuteBlob Host Runtime C ABI (2026-07-29)

### No blockers — implementation and tests pass

`cadCommandListAppendExecuteBlob()` added to the Host Runtime C ABI with full conformance test coverage.

- **Implementation**: 4 files modified (`runtime.h`, `runtime_core.h`, `runtime_core.c`, `CMakeLists.txt`), 1 new test file (`test_execute_blob_conformance.c`).
- **Verification**: Build clean, 4/4 new tests PASS, full suite 16/16 PASS (no regressions).
- **Design note**: LSP diagnostics (clangd) not installed on this machine; the cmake build with `-Wall -Wextra` serves as the diagnostic gate instead. No new warnings from changed files.


## W2-T8: fpga:// unsupported implementation (2026-07-29)

**Status**: Implemented and verified.

### Changes made
- `software/src/runtime_core.c`: removed `fpga://` fallback-to-mock in `find_transport`; added explicit `CAD_ERROR_UNSUPPORTED` return in `cadDeviceOpen`; updated `cadErrorString(CAD_ERROR_UNSUPPORTED)` to mention "fpga".
- `software/include/caduceus/runtime.h`: documented `fpga://` as reserved and not yet available.
- `software/tests/test_unsupported_uri.c`: new test verifying `cadDeviceOpen("fpga://...")` returns `CAD_ERROR_UNSUPPORTED` and the error string mentions "fpga".
- `software/CMakeLists.txt`: registered new `unsupported_uri` CTest.

### Verification
- `ctest --test-dir build/software -R unsupported_uri --output-on-failure`: 1/1 passed.
- `ctest --test-dir build/software -R 'runtime_|abi_|unsupported_uri'`: 8/8 passed.

### Note
- `lsp_diagnostics` could not be run because `clangd` is not installed and LSP installation was previously declined. Compiler diagnostics (`-Wall -Wextra`) from the cmake build are clean for the modified and new files.

## W2-T10: rtl_submit() blob-forwarding (2026-07-29)

**Status**: Implemented and verified.

### Changes made
- `software/src/transport_rtl.cpp`: Modified `rtl_submit()` to populate `SubmitRequestT.cmd_blob` from the serialized command payload (same raw-byte-forwarding semantics as FM transport W2-T9). Added capture-mode infrastructure (`g_rtl_capture_mode`, `cad_rtl_set_capture_mode()`, `cad_rtl_get_last_submit_blob()`) for testing without a running mock server.
- `software/include/caduceus/transport_rtl.h`: Declared `cad_rtl_set_capture_mode()` and `cad_rtl_get_last_submit_blob()`.
- `software/tests/test_rtl_transport.cpp`: Added `submit_populates_cmd_blob` test using capture mode.

### Verification
- `cmake --build build/software` — 0 warnings with -Wall -Wextra.
- `ctest --test-dir build/software -R rtl_transport --output-on-failure` — 2/2 PASSED.
- Evidence: `.omo/evidence/task-w2t5.log`.

### Known issues
- W2-T9's `test_fm_transport_blob` cannot build because `cd::GetSubmitRequest` is not a generated FlatBuffers function (should be `flatbuffers::GetRoot<cd::SubmitRequest>(...)`). This is a W2-T9 task issue and does not block W2-T10. The full test suite excluding that target is 19/20 PASSED.
