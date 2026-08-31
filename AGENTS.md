# PROJECT KNOWLEDGE BASE

**Generated:** 2026-08-28T09:11:10Z
**Commit:** e678f30
**Branch:** fix/fm-soc-10x-sfu-desc

## OVERVIEW
CaduceusCore — NPU coprocessor for CV + LLM inference. Three-layer stack: Arc Model (DSE sandbox) → Func Model (golden reference) → RTL (hardware). 64×64 INT4×INT8 broadcast MAC + SFU (7 FP16 ops) + Vector engine; Ibex RV32IMC + AXI crossbar + APB + doorbell ring + C firmware.

## STRUCTURE
```
CaduceusCore/
├── sim/        # Python: func model + perf timing + cocotb control + pytest  (own AGENTS.md)
├── rtl/        # Verilog engines + SoC integration + TBs + test_vectors      (own AGENTS.md)
├── scripts/    # gen→vcs→compare pipeline + perf signoff framework           (own AGENTS.md)
├── software/   # runtime/compiler/executorch software-stack release          (own AGENTS.md)
├── firmware/   # npu_firmware.c (doorbell dispatch loop) + generated npu-regmap.h
├── spec/       # npu_abi.json — single source of truth for the ABI
├── gen/        # generated ABI stubs (sv/c/h/py) — DO NOT EDIT
├── config/     # frozen perf spec/oracle/matrix/workloads JSON
├── docs/       # design docs, bugs ledger, waivers, dated research
├── reports/    # DSE / architecture reports
├── .omo/       # agent workflow: plans/, drafts/, evidence/, notepads/
└── vendored (NEVER edit): rtl/cpu/ibex/ rtl/ip/verilog-*/, llama_ref/, spike_src/, software/executorch/
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| RTL regression | `sim/regression/Makefile` + `run_*.sh` | ~90 targets; EDA server only |
| Func Model perf estimate | `sim/timing/`, `sim/npu_sim.py` | benchmark CLI: `python -m sim.timing.benchmark` |
| Perf spec gates | `config/func_model_perf_*.json` + `sim/timing/providers.py` | frozen; Path A vs independent oracle |
| Golden vector generation | `sim/golden_executor.py gen-test`, `scripts/gen_*_vectors.py` | writes `rtl/test_vectors/` |
| Firmware | `firmware/npu_firmware.c` | `firmware_main()` :648, doorbell ring loop |
| ABI truth | `spec/npu_abi.json` → `scripts/gen_npu_abi.py` → `gen/` | regenerate, never hand-edit gen/ |
| Plans / evidence | `.omo/plans/`, `.omo/evidence/`, `build/evidence/` | task-{N}-{plan}.txt per todo |
| Bugs / waivers | `docs/bugs/`, `docs/waivers/` | WVR-SOC-RTL-002 active (8MB DRAM window) |

## CODE MAP
Centrality unmeasured (no LSP/codegraph installed; grep-based only).
| Symbol | Type | Location | Role |
|--------|------|----------|------|
| NPUSimulator | class | sim/npu_sim.py:81 | cycle-accurate perf simulator (GEMM path) |
| TimingEngine | class | sim/timing/timing_engine.py:133 | TokenTiming + module breakdown |
| BlockEngine | class | sim/engine/block_engine.py:40 | canonical MXU estimator (per-tile H+2+2) |
| MetricsCollector | class | sim/timing/metrics.py | TTFT/TPS/TPOT/ITL + uncertainty bands |
| Block64Provider | class | sim/timing/providers.py:274 | spec-driven perf provider |
| CocotbBridge | class | sim/cocotb_bridge.py:354 | RTL drive/readback layer |
| PR (PerfRunner) | class | sim/perf_tests.py:136 | firmware-doorbell cocotb perf tests |
| caduceus_soc_top | module | rtl/soc/caduceus_soc_top.v:41 | full-chip top, CROSSBAR_MASTERS=7 |
| firmware_main | fn | firmware/npu_firmware.c:648 | firmware entry (via startup.S, not C main) |
| run_func_model_perf_signoff | CLI | scripts/run_func_model_perf_signoff.py | 6-subcommand perf signoff framework |

## CONVENTIONS
- **Commits**: `type(scope): summary` (types feat/fix/test/docs/chore/refactor); one atomic commit per plan todo with message pre-declared in the todo's `Commit:` line; evidence added with `git add -f` (build/evidence/ is gitignored).
- **Python**: `PYTHONPATH=sim` everywhere; dataclasses for metrics, pydantic-v2 `BaseModel` for contract schemas; frozen-spec data in JSON, hardware config in YAML.
- **RTL**: flist-only compilation (no globs); `$readmemh` + plusarg vectors; MMIO register tables in module header comments.
- **Firmware**: `__attribute__((packed, aligned(4)))` descriptor structs; `_Static_assert` ABI pins; ABI generated from `spec/npu_abi.json`.
- **Evidence**: JSON-lines perf entries; `task-{N}-{plan}.txt` must contain timestamp + commit + exact command + PASS/FAIL.

## ANTI-PATTERNS (THIS PROJECT)
- NEVER edit frozen perf spec/matrix (`config/func_model_perf_spec_v1.json`, `func_model_perf_matrix_v1.json`) — overlay, don't revise.
- NEVER emit `measured_cycles`; only `estimated_cycles` + `basis=architecture_assumption` + `calibration_state=uncalibrated`; max claim is `rtl_calibrated_atoms`, never `rtl_calibrated`.
- NEVER skip a case (`no_silent_skip`), never auto-approve waivers, never accept zero-test as PASS, never infer PASS from terminal text.
- NEVER edit vendored IP, generated ABI (`gen/`, `npu-regmap.h`), or engine internals from wrappers.
- NEVER run VCS off sz0001; use `sim/regression/soc-verification-run.sh` (auto-SSH).
- Oracle/verifier/reducer files must NOT import `sim.models` / `sim.engine` / `sim.timing.providers` / `sim.npu_sim`.

## UNIQUE STYLES
- Plan-driven workflow: `.omo/plans/<slug>.md` todos carry References / Acceptance (grep-able commands) / QA (happy+failure) / `Commit:`; final wave F1-F4 must all APPROVE.
- Dual-path perf verification: Path A (provider formulas) vs Path B (independent hand-derived oracle), ≤10%/≤20% gates + 26-fault adversarial matrix.
- Mixed-mode regression: `USE_RTL_<MODULE>` defines swap one RTL module into the Func Model SoC.
- Code comments cite issues as `ISSUE-13B (cf6736b)`.

## COMMANDS
```bash
# Local (no EDA): pytest 210 baseline
PYTHONPATH=sim python -m pytest sim/tests/ sim/timing/tests/ -q
# Local: perf benchmark
PYTHONPATH=sim python -m sim.timing.benchmark --model qwen2.5-3b --output results/timing
# EDA server: full 33-case FM-SOC regression
bash sim/regression/run_fm_soc_all.sh
# EDA server: single make target (auto-SSH from any host)
bash sim/regression/soc-verification-run.sh run_e2e_blk0
# Firmware
make -C firmware
```

## NOTES
- EDA server sz0001 / 192.168.0.11, NFS-shared repo; simv must run from the repo PARENT (`$readmemh` relative paths).
- CI (`.github/workflows/caduceus-core-ci.yml`) is software-stack only (mock:// device); never runs VCS/RTL.
- Open items: BUG-RTL-SOC-007 (attn_weight, chain-level not reproduced); WVR-SOC-RTL-002 (8MB DRAM window) active; E2E-07 perf calibration deferred to FPGA.
- Test-count discrepancy: README "210 passed" vs F2 audit "802/802" vs final summary "700" (see docs/issues_found.md:368) — cite the run you actually executed.
- Firmware SFU/Vector ring opcodes changed (SFU via 0x01+desc.sfu_op, ROPE 0x05, Vector 0x0F-0x14); older examples in `sim/perf_tests.py` fullchain use stale 0x06/0x17.
