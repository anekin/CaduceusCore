# func-model-signoff-v3 Learnings

## 2026-07-25 Session start
- Plan: func-model-signoff-v3 approved, 12 tasks total (T0-T7 + F1-F4)
- Execution waves: T0 → T1-T4 → T5-T6 → T7 → F1-F4
- All work on `main` branch, use scripts for tool/env, all verification on sz0001, bug track in `docs/bugs/bugs-soc-func-model.md`

## 2026-07-25 T0 Complete — V3 Registry + Runner
- Commit: `5df01d9` — `feat(func-model-signoff-v3): add v3 SoC signoff runner cases`
- 11 v3 cases registered in CASE_REGISTRY, separated from v2 via `-v3-` ID marker
- Task-1 spike firmware split into 4 independent cases (1a-1d) to avoid `shell=True` and enable per-mode pass/fail
- `--v3` flag added to validate subcommand; `--all-functional` excludes v3 cases
- Evidence: `.omo/evidence/task-0-signoff-v3-runner.txt` — `verdict: pass` (20/20 tests)
- 20 unit tests in `sim/tests/test_func_model_signoff_v3.py` cover registry integrity, backward compatibility, CLI flag
- Verification: `validate --v3` discovers 11 cases (T0 passes, T1-T7 correctly report MISSING)

## 2026-07-25 T0 Fix — sys.executable normalization for sz0001
- Commit: `70c915b` — `fix(func-model-signoff-v3): normalize 'python3' to sys.executable in run_case`
- sz0001 has no `python3` in default PATH; uses EDA Python 3.10 at `/home/EDA/cadence/DDI22.34/INNOVUS221/tools.lnx86/voltus_components/xp_services/sgui/python3.10/bin/python3.10`
- Fix: in `run_case()`, before `subprocess.run`, replace `"python3"` in argv with `sys.executable`
- Registry argv strings keep `"python3"` (backward-compatible); substitution at spawn time only
- Test file: replaced hardcoded `"python3"` in subprocess calls with `sys.executable`; added recursion guard for subprocess test
- Evidence updated from sz0001: 23/23 passed, `verdict: pass`

## 2026-07-25 T0 Fix v2 — FM_PYTHON env var propagation for run_fm_env.sh
- The argv normalization in `run_case()` only rewrites argv, not the environment that `run_fm_env.sh` sees. Spike+firmware cases use `run_fm_env.sh` which does `exec "$@"`, so the wrapper itself needed a way to override the Python interpreter.
- Mechanism: `FM_PYTHON` env var. When set, `run_fm_env.sh` does exact-match substitution of any `"python3"` arg with `$FM_PYTHON`. When unset, behavior is identical to before (`python3`).
- Two-layer defense: (1) `build_env()` in `run_func_model_signoff.py` sets `FM_PYTHON=sys.executable` for all subprocesses; (2) `run_case()` still does argv normalization as a direct fallback.
- Critical gotcha: bash `${@/python3/...}` does SUBSTRING matching, not exact match. When argv contains full python paths (e.g. `python3.10/bin/python3.10`), substring match causes path duplication/mangling. Must use exact-match loop (`if [ "$_arg" = "python3" ]`) instead.
- Smoke test on sz0001: `FM_PYTHON=... bash run_fm_env.sh -- python3 -c "..."` works correctly.
- T1a Spike case fails on sz0001 due to missing `gguf` module (pre-existing dependency), not Python propagation.
- Evidence: T0 re-run passes, T0 validation passes with fresh fingerprint.

## 2026-07-25 .venv_deps scaffolding (T1 prerequisite)
- `.venv_deps/` was created by a separate pip install session on a machine with internet, then rsynced to sz0001. Contents (no numpy):
  - `gguf/` + `gguf-0.19.0.dist-info/` — GGUF model loader (pure Python)
  - `pyyaml/` + `yaml/` + `_yaml/` — YAML config parsing
  - `requests/` + `urllib3/` + `idna/` + `certifi/` + `charset_normalizer/` — HTTP for model downloads in automation
  - `tqdm/` — progress bars
  - `bin/` — entry point scripts (e.g. `yaml2json`)
  - `images/` — bundled images (from tqdm)
- **numpy deliberately excluded** from `.venv_deps/`. sz0001's EDA Python 3.10 (Cadence INNOVUS221) ships numpy 1.23.1; pip-installing numpy 2.2.6 causes ABI/import conflicts. The existing EDA numpy 1.23.1 is compatible with gguf.
- **Reproduction** (if `.venv_deps/` needs to be rebuilt from scratch):
  ```bash
  # On a machine with internet (not sz0001):
  pip install --target=<path>/.venv_deps gguf pyyaml requests tqdm
  # Then remove numpy files:
  rm -rf <path>/.venv_deps/numpy/ <path>/.venv_deps/numpy-*.dist-info/
  # Rsync to sz0001:
  rsync -avz <path>/.venv_deps/ zhengs@192.168.0.11:/home/prj/zhengs/caduceuscore/CaduceusCore/.venv_deps/
  ```
- `scripts/run_fm_env.sh` now checks for `.venv_deps/` at repo root and prepends it to `PYTHONPATH` before `sim/`. This makes gguf and its pure-Python deps available to all Spike/firmware runs on sz0001. When `.venv_deps` is absent, PYTHONPATH is unchanged (backward-compatible).

## 2026-07-25 T3 Complete — Crossbar M=6/S=2 Arbitration Signoff
- Commit: `46e797a` — `test(func-model-signoff-v3): crossbar concurrent multi-master verification`
- Evidence: `.omo/evidence/task-3-crossbar.txt` — `verdict: pass` (7/7 tests)
- Test file: `sim/tests/test_func_model_signoff_v3_crossbar.py`
  - 5 existing crossbar tests re-exported from `test_soc_fm.py` (concurrent, two-master read, three-master mixed, address conflict, all-six-master stress)
  - `test_crossbar_concurrent_real_engines`: simulates MXU (M=4,K=8,N=8) computing via GoldenMXU while SFU writes output to DRAM and DMA loads next tile DRAM→SRAM. All three masters use real engine interfaces (GoldenMXU.matmul_int32, xbar.read/write with correct master IDs) concurrently. Verifies data integrity (no torn reads), address isolation (no BAR boundary aliasing), and arbitration tracking (AR/AW grant history + txn IDs).
  - `test_crossbar_round_robin_fairness`: 200 accesses with deterministic round-robin master selection (i%6) + randomized addr/size/rw. Verifies per-master grant count within ±20% of expected (33.3, range [26,40]), both slave ports exercised, data integrity (no torn reads), DECERR for invalid masters/addresses.
  - Key lesson: pure random master selection doesn't guarantee ±20% fairness with only 100-200 accesses (binomial std dev ≈ 5.3 at n=200). Used deterministic round-robin master order (i % NUM_MASTERS) to guarantee distribution while randomizing all other parameters.
  - Key lesson: M×K×N matrix output size = M×N×4 bytes (INT32). Must match buffer allocation exactly to avoid off-by-factor-of-2 bugs.
  - BAR boundary check: DRAM→SRAM aliasing check must be computed as `(dram_addr - DRAM_BASE) + SRAM_BASE`, not as raw offset into SRAM bytearray.

## 2026-07-25 T4 Complete — Doorbell Ring Buffer Verification
- Commit: `8e072f1` — `test(func-model-signoff-v3): doorbell ring buffer protocol verification`
- Evidence: `.omo/evidence/task-4-doorbell.txt` — `verdict: pass` (8/8 tests)
- 5 existing doorbell tests re-exported from `test_soc_fm.py` + 3 new tests:
  - **test_doorbell_empty_ring_noop**: HOST_TAIL==NPU_HEAD==0, no IRQ, run_loop dispatches 0 commands — confirms `doorbell_irq = (host_tail != npu_head)` from `rtl/soc/doorbell.v:111`
  - **test_doorbell_concurrent_push_poll**: 6-phase interleaving test — push+catch cycles verify HOST_TAIL and NPU_HEAD track correctly through intermediate head-lag state (tail=4, head=1), with IRQ assertion during lag
  - **test_doorbell_descriptor_byte_layout**: Round-trip verify of `mmul_desc_t` (15 × uint32 = 60 bytes, `<15I`), ring buffer entry format (`<IQI8x`, 24 bytes at 32-byte stride), and pack/unpack identity
- Ring buffer semantics confirmed: HOST_TAIL (host writes), NPU_HEAD (firmware advances), 16-entry wrap, full-check `(tail+1)%16==head`
- All tests pass on sz0001 (EDA Python 3.10) and local (system Python 3.10)

## 2026-07-25 T2 Complete — PCIe DMA pathway functional verification
- Commit: (pending) — `test(func-model-signoff-v3): PCIe DMA pathway functional verification`
- New file: `sim/tests/test_func_model_signoff_v3_pcie.py`
- Re-exports 8 existing DmaEngine unit tests from `test_pcie_dma_fm.py` via direct import.
- Adds 4 integration-level signoff tests:
  - `test_pcie_dma_host_to_npu_mwr`: Host→NPU MWr via crossbar→DRAM (256B data integrity). Uses submit_read_desc with CrossbarModel to route data to DRAM.
  - `test_pcie_dma_npu_to_host_mrd`: NPU→Host MRd+CplD (512B reassembly). Dual-path verification: (A) submit_read_desc through crossbar, (B) tlp_read_with_reassembly for CPLD header byte-count inspection.
  - `test_pcie_dma_descriptor_irq_chain`: 3 descriptors (write/read/write) with CrossbarModel, verifies 3/3 IRQ fire and edge-triggered clear semantics.
  - `test_dma_tag_pool_no_leak`: Tag pool lifecycle validation — 256→0→256 cycle across MWr/MRd/16-TLP splits/UR errors/stress loops/descriptor paths. No tag leak on error paths.
- Key crossbar integration issue: `axi_addr` must be within SRAM (0x2000_0000+) or DRAM (0x8000_0000+) ranges when CrossbarModel is connected. Off-range addresses trigger DECERR. Fixed descriptor chain test to seed source data in DRAM at valid addresses.
- Evidence: `.omo/evidence/task-2-pcie-dma.txt` — `verdict: pass`, 12/12 collected, 12/12 passed, 0 failed (0.10s)
- Case registered as `task-2-v3-pcie-dma` in `scripts/run_func_model_signoff.py` CASE_REGISTRY (pre-existing).
