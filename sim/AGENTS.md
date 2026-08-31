# sim/ — Python verification & simulation harness

## OVERVIEW
Func Model (golden reference) + perf timing pipeline + cocotb RTL control + pytest suites. Entry points: `npu_sim.py` (perf sim), `timing/benchmark.py` (CLI), `golden_executor.py` (golden gen), `cocotb_bridge.py`/`rtl_soc_runner.py` (RTL drivers).

## STRUCTURE
```
sim/
├── timing/       # perf spec/oracle/baseline/sweep gates + benchmark + dashboard (29 test files)
├── engine/       # 8 MAC engines (block=canonical) + timeline.py + multicore
├── models/       # sfu/vector/dma/noc/kv_cache/dram/sw_overhead perf models — frozen latencies
├── tests/        # golden + SoC-FM + spike + CV + firmware pytest (conftest.py fixtures)
├── cv/           # CV simulator + traces (yolov8n/resnet50/mobilenetv3/vit)
├── signoff/      # Qwen3B signoff gates (per-layer compare, full forward)
├── regression/   # Makefile (~90 targets) + run_*.sh — EDA-server entry
├── config/       # YAML hardware configs (npu_config.yaml is THE spec)
├── perf_tests.py # cocotb W4-PERF batches (PR class :136)
└── cocotb_bridge.py  # CocotbBridge (:354), run_step (:1650), _run_tiled_mmul (:1828)
```

## WHERE TO LOOK
| Task | Location |
|------|----------|
| Perf estimate formula | `engine/block_engine.py` (per-tile compute = H+2+2=68) |
| Perf spec gate | `timing/providers.py` Block64Provider :274; `timing/qwen_spec_gates.py` |
| Golden reference | `golden_executor.py` — subcommands smoke/sfu-verify/run-isa/gen-test |
| Drive RTL | `cocotb_bridge.py` CocotbBridge.run_step (:1650); `perf_tests.py` PR.mmul (:150) |
| FM-SOC cases | `rtl_soc_runner.py` builders (:1365+) — TESTCASE=test_soc_ibex_full |
| Regression run | `regression/Makefile` + `regression/run_*.sh` |
| Uncertainty bands | `timing/metrics.py:207-271` (0.7/1.3 cycles; inverse throughput; RSS sum-of-stages) |

## CONVENTIONS
- `PYTHONPATH=sim`; conftest.py:10-12 also injects sys.path; timing tests use `sys.path.insert(0, parents[3])`.
- Typing split: `@dataclass` for timing metrics (`timing/types.py`); pydantic-v2 `BaseModel` + `extra="forbid"` for contract schemas (`timing/perf_contract.py`).
- Frozen spec: `config/func_model_perf_spec_v1.json` — basis=architecture_assumption, estimated_cycles only, math.ceil rounding, seed 42.
- Cross-engine overhead: `SAME_ENGINE_GAP_TOTAL=4` (crossbar 2 + sram 1 + vcov 1) at `engine/timeline.py:141-146`; cross-engine gaps were deferred — do not assume 4.
- Evidence: `_save()` JSON-lines with `{case_id, simulator, status, cycles, commit, timestamp, cos_sim}` (`perf_tests.py:59-73`); task files under `build/evidence/`, `.omo/evidence/`.
- Cocotb invocation pattern: `MODULE=sim.cocotb_bridge TESTCASE=<t> TOPLEVEL=tb_soc ... +BOOTROM_HEX=firmware/build/npu_firmware.hex`, run from repo parent.

## ANTI-PATTERNS
- No `measured_cycles` field — PerfEstimate `extra="forbid"` rejects it (`perf_contract.py:217,370`).
- No `rtl_calibrated` state — only `uncalibrated`+architectural_formula is verdict-eligible (`providers.py:470-476`).
- Oracle independence: verifier/reducer must NOT import sim.models / sim.engine / sim.timing.providers / sim.timing.timing_engine / sim.npu_sim (`config/func_model_perf_oracle_v1.json:13`).
- No `skip=true` in any matrix case (`check_func_model_perf_spec.py:251`).
- Qwen canonical op counts frozen (17 ops/layer: 9 MXU / 5 SFU / 3 Vector) — `qwen_spec_gates.py:230`.
- Don't edit frozen CV manifests (`workloads.py:3` content hashes frozen).

## NOTES
- Firmware ring opcodes (current): SFU via `0x01` + `desc.sfu_op`, ROPE via `0x05`, Vector `0x0F-0x14` (see firmware/npu_firmware.c:558-603). `perf_tests.py` fullchain example uses STALE 0x06/0x17 — copy its dispatch structure, not its opcode values.
- `_pack_sfu_desc` hardcodes sfu_op=0 (word 10) — extend it to dispatch non-softmax SFU ops.
- Two test styles coexist: pytest-class (`test_perf_contract.py`) and unittest (`test_perf_baseline.py`) — don't "fix" the split.
- pytest baselines: README 210 (150 sim + 60 timing); F2 perf audit 802; final regression summary 700 — see docs/issues_found.md:368.
