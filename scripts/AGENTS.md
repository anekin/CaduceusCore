# scripts/ — orchestration, golden generation, signoff gates

## OVERVIEW
gen→vcs→compare pipeline, the 6-subcommand perf signoff framework, and phase-scoped gates. 154 top-level files, loosely grouped by phase prefix: p9_/p10_ (phase 9/10 diagnostics), fm_ (func-model hardening gates), wv_ (wrapper verification), ci_ (CI), plus gen_*_vectors.py and *_signoff.py runners.

## WHERE TO LOOK
| Task | Location |
|------|----------|
| Perf signoff (THE entry) | `run_func_model_perf_signoff.py` — subcommands: run / validate / audit / negative / rerun / baseline (main :3844) |
| Spec validation | `check_func_model_perf_spec.py` (skip forbidden :251; basis=rtl_measurement rejected :628), `check_func_model_perf_docs.py` (forbidden phrases :44-50: cycle-accurate, rtl-calibrated, measured cycles) |
| Oracle reduction | `reduce_func_model_perf_oracle.py` — must NOT import Path-A modules (:11-34) |
| Golden generation | `gen_mxu_vectors.py` (10 scenarios), `gen_sfu_vectors.py` (319), `gen_vector_vectors.py` (63), `gen_sfu_luts.py`, `gen_*_golden.py` |
| Golden compare | `compare_rtl.py` (INT32 bit-exact; FP16 abs 1e-3/rel 1e-2), `compare_sfu.py` (abs 2e-3/rel 1e-2), `verify_results.py` |
| Batch regression | `run_batch_regression.py` (compile+run 319 SFU + 63 Vector), `run_task17_regression.py` |
| ABI generation | `gen_npu_abi.py` — spec/npu_abi.json → gen/ (DO NOT hand-edit gen/) |
| Scope gates | `fm_hardening_f{1..4}.sh` — f4 = machine-enforced frozen surface (rtl/ excl. rtl/tb/, arc_model, quantize, ggml-npu/, requirements.txt) |
| E2E signoff | `run_qwen3b_software_signoff.py`, `aggregate_software_signoff.py`, `run_fpga_software_signoff.py` |

## CONVENTIONS
- Gen scripts write per-scenario dirs under `rtl/test_vectors/<engine>/<scenario>/` with hex inputs + golden + params.txt + manifest.json.
- Evidence output: `build/evidence/task-{N}-{plan}.txt` — must include timestamp + commit + exact command + PASS/FAIL.
- Phase prefix in filename signals which plan produced it (p9_/p10_/fm_/wv_/ci_).

## ANTI-PATTERNS
- NEVER modify rtl/ or firmware/ from scripts — `p9_diag_harness.sh` declares itself a read-only observer (:6,49).
- NO `shell=True`, NO inferring PASS from terminal text, NO accepting zero-test as PASS (func-model-perf-signoff plan policy).
- ABI constants: edit `spec/npu_abi.json` and regenerate — never hand-edit `gen/` output or `firmware/npu-regmap.h` values.
- Perf signoff baseline cannot self-update in validate mode; updates need a changed spec version/rationale, never "accept current output".

## NOTES
- `run_func_model_perf_signoff.py` is a 6-subcommand framework, not a one-shot runner — read its --help before assuming behavior.
- CV model PPA gen: `generate_mobilenetv3_ppa.py`; firmware contract: `gen_firmware_memory_contract.py --check` (FW-09 static artifact).
- Deprecated dispatch carries a literal "DEPRECATED" marker (see sim/miniv.py:566).
