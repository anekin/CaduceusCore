## T5 Regression Failures

**Date:** 2026-07-22
**Script:** `scripts/p9_regression.sh`

### Failures

1. **pytest not available on sz0001**
   - Evidence: `build/evidence/ph9-pytest.log`
   - Error: `/NAS/Tools/anaconda3/envs/py3.11/bin/python: No module named pytest`
   - Impact: pytest regression reports 0 passed; AC requires >=210 passed.

2. **FM-SOC-003 MXU output mismatch**
   - Evidence: `build/evidence/ph9-fm-soc-33.log`, `build/ibex_full_rtl/evidence/FM-SOC-003.log`
   - Error: `mismatch out: addr=0x80030000 expected=... actual=...`
   - Hypothesis: `sim/rtl_soc_runner.py::_build_003_mxu` still writes broadcasted activations (64x repeat per activation byte) into SRAM for the old broadcast-MAC path, but the T4 firmware dispatches through `mxu_wrapper_preload` which expects a non-broadcasted SRAM copy and lets the wrapper broadcast.
   - Impact: FM-SOC regression 32/33 PASS, 1 FAIL; AC requires 33/0.

3. **SFU batch regression 0/537 passed**
   - Evidence: `build/evidence/ph9-sfu-vector.log`, `build/run/sfu_batch.log`
   - Errors:
     - Stale `params.txt` vs `input.hex` mismatch (e.g., `gelu_smoke` DIM=42 but input has 35 elements).
     - Missing/obsolete `manifest.json` in several scenario directories.
     - All inline comparisons return status=256.
   - Hypothesis: SFU test-vector directories accumulated stale/inconsistent artifacts that do not match the current `tb_sfu.v` + `compare_sfu.py` expectations.
   - Impact: SFU regression reports 0/537; AC expects 319/319.

4. **Vector/SFU scenario count mismatch**
   - Evidence: `build/evidence/ph9-sfu-vector.log`
   - Observation: `run_batch_regression.py` discovered 537 SFU and 93 Vector scenarios, while the task AC expects 319 SFU and 63 Vector.
   - Hypothesis: Extra directories (smoke, e2e, `sf03_*`, `sf04_*`, etc.) were left in `rtl/test_vectors/sfu` and `rtl/test_vectors/vector` from earlier generation runs and are being picked up by the recursive manifest discovery.
   - Impact: AC grep for `319/319` and `63/63` fails even if the intended scenarios were to pass.

### Disposition

T5 halted without marking the plan checkbox. Failures recorded in `build/evidence/ph9-regression-fail.txt`.
