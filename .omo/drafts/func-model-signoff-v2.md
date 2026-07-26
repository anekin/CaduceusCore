---
slug: func-model-signoff-v2
status: awaiting-approval
intent: clear
review_required: false
pending-action: write .omo/plans/func-model-signoff-v2.md
approach: Build on existing 984-line plan draft, adding standard template structure, fixing 3 verified gaps (missing func-model-signoff-checklist.md creation, branch→main, commit→per-task), and compressing into agent-executable task batches with TDD QA scenarios.
---

# Draft: func-model-signoff-v2

## Components (topology ledger)
| id | outcome (one line) | status | evidence path |
|----|---------------------|--------|---------------|
| C1 | FP16/SFU tolerance comparator fix (F-FM-03) | active | sim/golden_executor.py:650 |
| C2 | Signoff runner framework (Task 0A) | active | scripts/run_func_model_signoff.py (new) |
| C3 | Synthetic + real-GGUF preflight (Task 0B) | active | sim/signoff/test_qwen_blk0_synthetic_stress.py (new) |
| C4 | Scaled test reclassification (Task 3) | active | sim/tests/test_soc_fm.py |
| C5 | Synthetic direct-MMIO + tiled stress gates | active | sim/signoff/ |
| C6 | Real-GGUF direct/tiled/connected projection gates | active | sim/signoff/test_qwen25_3b_real_blk0.py (new) |
| C7 | Robustness coverage | active | sim/signoff/ + sim/tests/ |
| C8 | Documentation + checklist consistency | active | docs/func-model-signoff-checklist.md (new) |
| C9 | Full functional sweep + signoff | active | .omo/evidence/ |

## Open assumptions (announced defaults)
| assumption | adopted default | rationale | reversible? |
|-------------|-----------------|-----------|-------------|
| Branch | main (not feat_func_model) | User confirmed all work on main | Yes |
| Commit policy | Per-task commit to main | User confirmed | Yes |
| Test directory | sim/signoff/ (new) | User confirmed | Yes |
| Signoff runner | Retain full independent runner with case registry | User confirmed | Yes |

## Findings (cited - path:lines)
- sim/golden_executor.py:661 — uses old `np.all(abs) or np.all(rel)` (F-FM-03 bug)
- scripts/verify_w2_2_fm_golden_vectors.py:225 — same duplicated semantics
- sim/func_model.py:27 — FuncModel(dram_mb=64, sram_kb=512) defaults
- sim/tests/test_soc_fm.py:1613 — _blk0_run_mmul has min(M,64),min(K,64),min(N,64) caps
- sim/tests/test_soc_fm.py:1812 — test_blk0_full_chain_single_tile (needs rename)
- sim/tests/test_soc_fm.py:2541 — test_28block_chain (needs rename)
- sim/tests/test_soc_fm.py:2812 — test_e2e_host_pcie_doorbell_firmware_compute (needs rename)
- sim/qwen25_forward.py:201 — forward_with_intermediates() exists and captures intermediates
- ggml-npu/q4_dequant.py:171 — load_weights_from_gguf() exists
- scripts/gen_qwen25_3b_rtl_vectors.py:241 — _get_quant_weight() with INT4 group-128 quantization
- /home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf — exists, 2104932768 bytes (matches plan)
- rtl/test_vectors/qwen_blk0/blk0_manifest.json — 17 ops, 46 files, synthetic (2560/9728 dims)
- docs/func-model-signoff-checklist.md — DOES NOT EXIST (must create from scratch)
- scripts/gen_blk0_golden.py — exists

## Decisions (with rationale)
1. Retain full independent signoff runner — user confirmed; provides audit trail, stale-state detection, anti-vacuous guards
2. Per-task commit to main — user confirmed; consistent with rtl-bug-fix-wv workflow
3. Tests in sim/signoff/ new directory — user confirmed; separates signoff from fast regression
4. Create docs/func-model-signoff-checklist.md from scratch — explorer verified it doesn't exist
5. Use main branch not feat_func_model — user confirmed all work on main

## Scope IN
- sim/golden_executor.py: FP16 tolerance comparator fix
- scripts/verify_w2_2_fm_golden_vectors.py: same comparator fix
- scripts/run_func_model_signoff.py: new signoff evidence runner
- sim/signoff/: new signoff test files (synthetic + real-GGUF)
- sim/tests/test_soc_fm.py: renamed test functions + reclassification
- sim/tile_scheduler.py: only if tiled gate exposes real defect
- ggml-npu/q4_dequant.py: selective GGUF loading extension
- sim/qwen25_forward.py: additive intermediate capture extension
- sim/qwen25_func_model.py: new real-model func model runner
- sim/qwen25_signoff_oracle.py: new independent quantized oracle
- docs/func-model-signoff-checklist.md: new, created from scratch
- scripts/check_func_model_signoff_docs.py: new doc consistency checker
- rtl/testcase-list-soc-fm.md: scope wording only

## Scope OUT (Must NOT have)
- No RTL implementation changes (rtl/wrapper/*, rtl/sfu/*, rtl/mxu/* etc.)
- No RTL testbench repair
- No SFU RTL batch 526/537 closure
- No performance signoff
- No full multi-layer or 36-layer Qwen 3B signoff (blk.0 only)
- No FM-SOC RTL VCS rerun
- No changes to sim/tests/test_engines.py (known perf model failures excluded)

## Open questions
None remaining — all 3 owner-decisions answered by user.

## Approval gate
status: awaiting-approval
<!-- User approved the approach via question answers; ready to write plan -->
