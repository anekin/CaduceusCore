---
slug: phase9-firmware-rtl-fix
status: awaiting-approval
intent: clear
pending-action: dual review passed (Momus + Oracle both APPROVE on Pass 9 Final Wave); awaiting user explicit okay for /start-work
approval-gate: passed
approach: "Fail-first diagnose M=1 multi-tile divergence in firmware doorbell path (direct wrapper preload cs=1.0 vs firmware cs<0.999), decide firmware-fix vs RTL-fix by evidence, then add per-K-tile weight streaming, Q8_0 download, full PERF re-run, Phase 6 6b closure. Spike+plugin debug path enabled."
---

# Draft: phase9-firmware-rtl-fix

## Components (topology ledger)
<!-- Lock the SHAPE before depth. One row per top-level component that can succeed or fail independently. -->
<!-- id | outcome (one line) | status: active|deferred | evidence path -->

| id | outcome | status | evidence |
|---|---|---|---|
| C1 | Spike ABI re-verified + read-only diagnostic harness dumps firmware MMIO/wrapper probe sequence | active | `build/evidence/ph9-spike-abi.txt`, `build/evidence/ph9-divergence-report.txt` |
| C2 | M=1 multi-tile divergence root-cause isolated to firmware MMIO redundancy OR RTL wrapper broadcast/store-out count | active | `build/evidence/ph9-divergence-report.txt` |
| C3 | RTL/firmware fix applied (branch A or B per diagnostic) | active | `firmware/npu_firmware.c` or `rtl/wrapper/mxu_soc_wrapper.v` diff |
| C4 | PERF-01/04/05/06/11/13/17 all cos_sim>=0.999 on sz0001 | active | `build/evidence/w4-perf-p{0,1,2,3,4}.txt` re-run |
| C5 | Fullchain multi-tile 5-op pipeline cos_sim>=0.999 | active | `build/evidence/ph9-fullchain-multitile.txt` |
| C6 | Per-K-tile weight streaming for K=2560 Q_proj, 36-layer L0/L10/L20/L35 checkpoint cos_sim>=0.999 | active | `build/evidence/ph9-36layer-checkpoint.txt` |
| C7 | Q8_0 GGUF downloaded + Q_proj precision control experiment + Phase 6 6b checkbox finalized | active | `build/evidence/ph9-q8_0-precision.txt` |

## Open assumptions (announced defaults)
<!-- assumption | adopted default | rationale | reversible? -->

| assumption | default | rationale | reversible? |
|---|---|---|---|
| Script-first principle (user addendum) | 任何工具调用、环境变量设置、SSH/VCS/cocotb 调用必须封装为 `scripts/p9_*.sh` 脚本并复用；不允许在 todo 里直接 inline shell 命令字面量；agent 只 `bash <script>` 一个入口 | 用户要求：不同 agent 复用同一脚本，避免重复犯错 | yes |
| All-on-sz0001 (user addendum) | 所有验证（包括不调 EDA 的 pytest、固件 build、Q8_0 对比）都在 `ssh zhengs@192.168.0.11` 下执行；保持环境一致 | 用户要求：环境一致性 | yes |
| Bug-tracking 强制 (user addendum) | 任何 bug（固件/RTL/集成）发现即追加到 `docs/bugs/bugs-soc-rtl.md`（沿用 BUG-RTL-SOC-NNN 模板）；疑似 RTL bug 额外产独立 `docs/bugs/BUG-MXU-<SLUG>-NNN.md` 单文件 bug report 含 Symptom/Root Cause/Hypothesis/Evidence/Repro/Proposed Fix/Verdict 块；修后 Status→Fixed 不删除 | 用户要求：bug 可追溯，RTL bug 单独根因分析 | irreversible (历史) |
| Wave 1 diagnostic case sweep | 3 cases M=1 (K,N) ∈ {(128,64),(256,128),(512,128)} | Oracle issue 7: single K=128,N=64 insufficient because Phase 8 showed different divergence magnitudes across K/N; sweep localizes | yes |
| Wave 2 fix target | branch by diagnostic conclusion; if (A) firmware edit `npu_firmware.c:198-205` remove I_ADDR/W_ADDR/O_ADDR writes; if (B) RTL edit `mxu_soc_wrapper.v` broadcast/store-out counter | Oracle 1&2: must cite exact file:line per branch | yes (rebuild/recompile) |
| Wave 3 weight layout | firmware owns per-K-tile DMA segment chain; `sim/perf_tests.py` writes contiguous flat weight buffer via existing `pack_int4_tile_major`; NO `sim/cocotb_bridge.py` change | Oracle 6: resolves F4 contradiction — bridge stays read-only import | yes |
| Regression gates after each fix wave | MXU 9/9, SFU 319/319, Vector 63/63, FM-SOC 33/33, pytest sim/tests+timing enumerated with commands | Oracle 5: full regression granularity, not only pytest+FM-SOC | yes |
| Firmware rebuild gate | `make -C firmware clean && make -C firmware` after ANY firmware edit, verify `firmware/build/npu_firmware.bin` newer than source | Oracle 5: avoid testing stale binary | yes |
| Wave 1 read-only guarantee | `git diff -- rtl/ firmware/` empty after diagnostic; probes via FSDB/VCD signal access only, no RTL `$display` injection | Oracle 4: prevent timing perturbation | yes |
| Phase 6 6b checkbox location | `.omo/plans/phase6-rtl-verification.md:107` `6b. [x] L35 drift root-cause: Q8_0/FP16 control experiment` | exploration grep; test condition = `grep -c 'cos_sim' build/evidence/w1-6b-q8o.txt` → 36 | yes |
| Phase 6 6b decision rule | Q8_0 Q_proj cos_sim>=0.999 → keep [x] PASS and close; 0.990<=cs<0.999 → mark CONDITIONAL + per-layer delta file `ph9-q8_0-precision.txt`; cs<0.990 → revert to [ ] FAIL + root-cause hypothesis | Oracle 9: decision threshold explicit | yes |
| Q8_0 download failure fallback | retry ≤3 × 60s; on failure file `build/evidence/ph9-q8_0-download-FAILED.txt`, mark 6b BLOCKED-NETWORK, do NOT block waves 0-4/Final | Oracle 10: external network risk | yes |
| SRAM budget pre-check before Wave 3 | assert `total_weight + activation_buffer + output_buffer <= 4MB`; Q_proj 2560×4096×0.5B = 5.24MB > 4MB → must use per-K-tile streaming so peak SRAM = 2 tiles × 2048B + act tile + out tile << 4MB | Oracle 11: prevent runtime overflow | yes |
| Evidence naming | `ph9-*` prefix; pre-check `ls build/evidence/ph9-* 2>/dev/null` at Wave 0; if conflict, archive as `ph9-v0-*` | Oracle 12 | yes |
| Causality gate for PERF-11 | after Wave 2, run PERF-11 with K<=64 (no streaming) AND K=512 (streaming); if K<=64 also cs>=0.999 → causal proof of doorbell fix; document `build/evidence/ph9-causality.txt` | Oracle 14: G8/G11 causal vs coincidental | yes |

## Findings (cited - path:lines)

- `rtl/mxu/mxu_top.v` — grep for `I_ADDR|W_ADDR|araddr` → no hits → MXU consumes only `weight_bus_i/activation_bus_i` broadcast buses; does not self-read SRAM from I_ADDR/W_ADDR registers.
- `rtl/wrapper/mxu_soc_wrapper.v:165-169` — `wrp_weight_base/wrp_act_base/wrp_out_base/wrp_k_tiles/wrp_n` APB registers; preload sequencer FSM states at lines 282-387; broadcast driver lines 402-467; store-out FIFO + AXI4 W FSM lines 477-631.
- `firmware/npu_firmware.c:179-208` — `mxu_wrapper_preload()` writes WRP_* registers; `mxu_start()` ALSO writes `mxu->I_ADDR/W_ADDR/O_ADDR/SCALE_ADDR/CTRL/DIM0/DIM1` then `CMD.START=1`. The I_ADDR/W_ADDR/O_ADDR writes (lines 199-201) are the suspected redundant/conflicting writes.
- `firmware/npu_firmware.c:395-456` — `dispatch_cmd()` MMUL handler: outer N-tile loop, inner K-block loop, ping-pong weight DMA, accumulate_ctrl bit 2 for k_block>0.
- `rtl/ip/dma_wrapper.v:206-237,334-338` — CH1 descriptor latch direction correct (SRC→read_addr, DST→write_addr).
- `firmware/npu_firmware.c:136-138` — CH1 SRC=SRAM, DST=DRAM.
- `build/evidence/ph8-perf-11-before-after.txt` — Phase 8 P09 evidence: DRAM matches SRAM nonzero → DMA CH1 readback works post-tile-major-fix; the standalone "DMA readback zeros" issue no longer exists.
- `build/evidence/ph8-diagnostic.txt` — direct wrapper preload cs=1.0 for multi-tile M=1; firmware doorbell cs<0.999; divergence is in firmware→MMIO→wrapper→MXU path.
- `build/evidence/w4-perf-p3.txt` — Phase 8 P3: M=32 cases cs=1.0; M=1 multi-tile cases cs 0.386-0.796; single-tile M=1 cs=1.0.
- `.omo/plans/phase6-rtl-verification.md:107` — 6b checkbox `[x]` but actually NOT RESOLVED (depends on Q8_0).
- `build/evidence/w1-6b-q8o.txt` — 6b evidence file exists but is stale/placeholder (Q8_0 was missing).

## Decisions (with rationale)

1. Scope = P0 + P2 + P4 (user chose). Rationale: one phase covers M=1 divergence + per-K-tile streaming + Q8_0 closure; maximizes blocker resolution.
2. Strategy = diagnose-then-fix (user chose). Rationale: root cause not 100% isolated; avoid premature RTL change.
3. Debug path = Spike+plugin (user chose). Rationale: ABI fixed in Phase 7; faster MMIO sequence inspection than RTL waveform.
4. Wave 1 sweep of 3 cases (Oracle 7). Rationale: single case insufficient given divergent magnitudes.
5. Wave 2 dual-branch fix with exact file:line per branch (Oracle 1&2). Rationale: worker zero-judgment.
6. Wave 3 firmware-owned DMA segment chain, cocotb_bridge.py stays read-only (Oracle 6). Rationale: resolves F4 contradiction.
7. Regression gates enumerated with commands after each fix wave + firmware rebuild gate (Oracle 5).
8. Read-only diagnostic gate with git-diff-empty check (Oracle 4).
9. Phase 6 6b decision rule explicit thresholds (Oracle 9).
10. Q8_0 download fallback NOT-RESOLVED pattern (Oracle 10).
11. SRAM budget pre-check before Wave 3 commit (Oracle 11).
12. Causality gate: PERF-11 K<=64 vs K=512 after Wave 2 (Oracle 14).

## Scope IN

- Firmware `firmware/npu_firmware.c` `mxu_start()` / `dispatch_cmd()` MMUL path
- RTL `rtl/wrapper/mxu_soc_wrapper.v` broadcast driver / store-out sequencer (only if diagnostic branch B)
- Spike plugin re-verify and diagnostic harness `sim/diagnose_mmu_path.py` (read-only)
- PERF tests `sim/perf_tests.py` run only (no source change unless Wave 3 needs contig weight layout; bridge stays read-only)
- Q8_0 GGUF download + `sim/e2e_llamacpp.py` Q_proj comparison
- Phase 6 plan 6b checkbox finalization
- Documentation: `rtl/testcase-list-perf.md`, `docs/issues_found.md`, `build/evidence/ph9-*`

## Scope OUT (Must NOT have)

- Arc Model / DSE changes
- New engine types or Q4_K downgrade or BF16 support
- `sim/cocotb_bridge.py` source modification (read-only import only)
- Phase 8 plan 6b checkbox touched (different phase)
- RTL changes in `rtl/mxu/`, `rtl/sfu/`, `rtl/vector/`, `rtl/soc/`, `rtl/ip/` (only `rtl/wrapper/mxu_soc_wrapper.v` may change, only if branch B)
- Q8_0 used outside Phase 6 6b experiment
- Spike plugin rebuilt (Phase 7 already fixed ABI; Wave 0 re-verifies only; if ABI broken again, HALT and file as Phase 7 defect)

## Open questions

None — all forks resolved by user or best-practice default recorded above.

## Approval gate
status: in-review-dual-pass
<!-- Dual high-accuracy review rerunning against revised plan after fixing BLOCKERs/MAJORs from pass 1. Pass 2 must return OKAY from BOTH reviewers before setting awaiting-approval and presenting brief. -->

## Review history (high-accuracy dual review)

### Pass 1 (2026-07-20)
- **Momus** (ses_0851b0b86ffeInw5EK8wKFxvH9): VERDICT=REVISE; 3 BLOCKER, 18 MAJOR, 8 MINOR. Receipt: this draft.
- **Oracle** (ses_08515a6afffeZpz4ihUQXuZn6V): VERDICT=REJECT; 4 BLOCKER, 8 MAJOR, 5 MINOR. Receipt: this draft. Cross-checked repo: `scripts/run_w1_6b_q8o_control.py` has no CLI; `scripts/run_36layer_checkpoint.py` 没有 `--out`; RTL line numbers in T2 wrong (`pl_state`/`pl_beat_cnt` swapped); `test_w4_perf_fullchain_multitile` does not exist; F1-F4 missing structure.

### Fixes applied between pass 1 and pass 2 (2026-07-20)
1. T1: removed duplicate step 1/2 lines (stale inline `ls`/`make` blocks); added step 0 with explicit `chmod +x` script creation; added `p9_spike_chain.sh`; fixed dependency matrix (T1 Blocks 2,3,9).
2. T2: corrected RTL line refs — `pl_state` at `:289` not `:290`; `pl_beat_cnt` at `:290` not `:289`; `so_capture_row` at `:501`; `so_state` declared at `:525`; `m_axi_*` assignments at `:392/:394/:398/:591/:592/:617`.
3. T3: added explicit `p9_log_bug.sh --rtl-report` step when conclusion is (B) or (C); AC `grep -qE '^CONCLUSION: \((A|B|C)\): '` matches the prose format; bug-report existence AC.
4. T4 branch A: removed duplicate causality step 4; all commands wrapped in `bash scripts/p9_fix_branch_a.sh`; added `p9_log_bug.sh --id BUG-RTL-SOC-P9-00A` step; AC adds `grep -q '^async def test_w4_perf_p9_directed_sweep' sim/perf_tests.py`.
5. T4 branch B: all commands wrapped in `bash scripts/p9_fix_branch_b.sh`; added `p9_log_bug.sh --rtl-report BUG-MXU-P9-00B-broadcast-multitile`; AC uses single unambiguous VCS exit 0 check.
6. T5: all commands wrapped in `bash scripts/p9_regression.sh`; AC `<pytest log>` replaced with concrete `build/evidence/ph9-pytest.log`, `build/evidence/ph9-mxu-reg.log`, `build/evidence/ph9-sfu-vector.log`.
7. T6: removed inline `python3 - <<'PY'`; wrapped in `bash scripts/p9_sram_budget.sh` + `bash scripts/p9_weight_streaming.sh`; AC uses `build/evidence/ph9-t6-no-new-rtl.txt` to check T6 introduces no NEW RTL changes (avoids false-negative when T4-B already edited wrapper).
8. T7: removed unsupported `--out` flag call; `bash scripts/p9_36layer.sh 0 10 20 35` wraps the actual CLI `--layers 0 10 20 35 --no-amend` and `cp` of `36layer-checkpoint.txt` to `ph9-36layer-checkpoint.txt`; L35 threshold explicitly 0.997.
9. T8: removed duplicate step 3; wrapped in `bash scripts/p9_perfect_batch.sh`; AC adds `grep -q '^async def test_w4_perf_fullchain_multitile' sim/perf_tests.py`; AC adds stale-state guard `head -1 build/evidence/w4-perf-p*.txt | grep -q '^# Phase 9 re-run'`.
10. T9: removed unsupported `--model`/`--out` flags; wrapped in three `bash scripts/p9_q8o_*.sh`; `run_w1_6b_q8o_control.py` invoked with NO CLI args (per actual script); output `w1-6b-q8o.txt` `cp`-ied; AC adds `grep -qE '^ba/judge=(PASS|CONDITIONAL|FAIL|BLOCKED-NETWORK)'`.
11. F1-F4: each filled with What-to-do / Acceptance / happy+failure QA / Commit: N — full todo structure per template.
12. Wave/dependency matrix: T9 Blocks 中包含 short-circuit rule when BLOCKED-NETWORK; F1-F4 explicitly declared as parallel post-T8/T9.
13. Success criteria: replaced with version noting L35 0.997 threshold, bug-report landing, stale-state guard, judge field, script-skeleton check.
14. Added "Phase 9 脚本清单" section listing every `scripts/p9_*.sh` and its exact responsibility, plus `p9_sz0001.sh` `p9_ssh()` semantics.

### Fixes applied after Momus Pass 2 (still before Oracle Pass 2)
15. Added `scripts/p9_bootstrap_scaffold.sh` as the sole bootstrap script (T1 step 0 invokes it); it creates shared lib `p9_sz0001.sh`, all shared scripts, and writes `build/evidence/ph9-base-commit.txt`.
16. Added full "Bootstrap script content" code block in the plan so the executor knows exactly what to write.
17. T1 step 0 now explicitly: write bootstrap script → `chmod +x` → `bash scripts/p9_bootstrap_scaffold.sh`.
18. T1 AC updated to check bootstrap script + all shared final-wave scripts exist and executable, plus `ph9-base-commit.txt`.
19. T2 step 1 now a single `bash scripts/p9_diag_harness.sh`; signal list moved to a "Diagnostic harness signals" subsection, not inline python heredoc.
20. T6 AC fixed: `ph9-t6-no-new-rtl.txt` marker uses `test -f` + `grep 'T6_NO_NEW_RTL=1'` (script writes content, not empty); layout-change marker `ph9-t6-perf-tests-layout.txt` removes conditional AC branch.
21. T8 residual failure path now explicitly calls `bash scripts/p9_log_bug.sh --id BUG-RTL-SOC-P9-00D`.
22. F1-F4 What-to-do simplified to single script invocations (`p9_f1_audit.sh`, `p9_f2_code_quality.sh`, `p9_f3_manual_qa.sh`, `p9_f4_scope_gate.sh`); removed inline `for...grep`, `git diff`, `git log`, `xxd` commands.
23. F2/F4 no longer use `<phase9-base-commit>` placeholder; they read `build/evidence/ph9-base-commit.txt`.
24. F4 acceptance criteria no longer conditional; `p9_f4_scope_gate.sh` writes `f4-gate.txt` with `RTL_SCOPE_OK=1`, `Q8O_JUDGE_OK=1`, `SPIKE_PLUGIN_UNCHANGED=1`.
25. Script manifest descriptions for f1-f4 updated to match marker-file outputs.
26. Verified no inline command literals remain in any todo What-to-do section.

### Fixes applied after Oracle Pass 2 (before Pass 3)
27. Extended `p9_bootstrap_scaffold.sh` to create ALL 20 Phase 9 scripts as stubs (8 shared + 12 per-todo), resolving the script-creation gap.
28. T4 branch B: removed inline VCS command from What-to-do; now references script-internal VCS elaboration without repeating command literal.
29. T4 branch B: fixed forward-reference wording — T4-B creates `test_w4_perf_p9_directed_sweep` independently; T8 later reuses it (not the reverse).
30. T8: fixed `fullchain-pipeline.txt` → `build/evidence/fullchain-pipeline.txt` in What-to-do and stale-state description.
31. Script manifest: `p9_weight_streaming.sh` description now explicitly mentions required marker files (`ph9-t6-no-new-rtl.txt`, `ph9-t6-perf-tests-layout.txt`).
32. T4 branch A: fixed line range from `firmware/npu_firmware.c:198-205` to `:199-201`.
33. Success criteria: fixed malformed row `7.T7 证据路径` → `T7 证据路径`.
34. T6 AC: fixed indentation anomaly on `ph9-t6-no-new-rtl.txt` bullet.

### Fixes applied after Momus Pass 3 (final pass before dual-OKAY)
35. Removed ALL remaining inline command literals from todo bodies and script manifest: T3, T4-A, T5, T7, T8, T9, p9_spike_chain, p9_fix_branch_a/b, p9_weight_streaming, p9_36layer, p9_perfect_batch, p9_q8o_download, p9_q8o_precision.
36. T3: replaced placeholder `BUG-MXU-P9-NNN-doorbell-divergence` with fixed ID `BUG-MXU-P9-001-doorbell-divergence` in todo body, AC, and script manifest.
37. Bootstrap: added `chmod +x scripts/p9_lib/p9_sz0001.sh` and `mkdir -p "$ROOT/build/evidence"` before writing base commit.
38. T1 step 0: compressed bootstrap description to a single script invocation line.
39. T9 AC: fixed regex to match actual `phase6-rtl-verification.md` format (`^6b\. \[(x|~| )\]` and `ba/judge=...` anywhere on the line).
40. F2: expanded whitelist to include `sim/diagnose_mmu_path.py`, `scripts/p9_lib/*.sh`, `docs/bugs/*.md`, `docs/issues_found.md`, `rtl/testcase-list-perf.md`, `.omo/plans/phase6-rtl-verification.md`.
41. F2/F3: rephrased What-to-do to avoid naming `git diff` / `python3 -c` / `grep` / `xxd` commands.

### Fixes applied after Momus Pass 3 REVISE feedback (before re-run Pass 3)
42. F2 whitelist: added `.omo/notepads/phase9-firmware-rtl-fix/*.md` to cover T1 step 1 notepad writes (verified not gitignored).
43. T7 AC: changed regex from `status=PASS` / `layer=N ` to match actual `scripts/run_36layer_checkpoint.py` output format `[PASS] L{layer_idx}: cos_sim=...`.
44. Script manifest: removed remaining inline command literals from `p9_env_check.sh` and `p9_fw_rebuild.sh` descriptions.
45. T2 AC: added `test -n "$(ls build/evidence/ph9-probe-*.jsonl 2>/dev/null)"` to verify probe JSONL products.
46. T6: clarified line range description as K-block for-loop from header line 425 to closing brace line 451.

### Fixes applied after Pass 3 dual review (Momus + Oracle both REVISE) before Pass 4
47. Bootstrap `p9_log_bug.sh`: replaced placeholder echo with a `--help` handler that prints `--rtl-report`, satisfying T1 AC.
48. T7 AC: fixed regex to match actual evidence file format `layer=N simulator=ibex status=PASS cos_sim=...` and enforced exact thresholds (`0.999[0-9]|1.0` for L0/L10/L20, `0.99[7-9]|1.0` for L35).
49. T9 download: added `timeout 600` and failure-file content validation (`BLOCKED-NETWORK|exit_code|huggingface-cli`).
50. T8 AC: reduced `| ✅ PASS |` threshold from ≥25 to ≥20 based on actual 21-row `rtl/testcase-list-perf.md`.
51. T3: explicitly defined the 3 divergence sweep cases (K=128,N=64; K=512,N=128; K=2048,N=256).
52. T5 AC: replaced loose `grep -c 'PASS'` / `grep -c 'FAIL'` with structured `[PASS] FM-SOC-` / `[FAIL] FM-SOC-` counts plus summary-line checks.
53. T8/T4-A/T4-B PERF/fullchain cos_sim regexes: changed all loose `0.99[0-9]` to strict `0.999[0-9]|1.0` to enforce ≥0.999.
54. T4-A/T4-B bug report ACs: scoped verdict check to `verdict=resolved` within the specific bug entry/file.
55. T4-B AC: clarified VCS success check via `VCS_EXIT_CODE=0` marker instead of ambiguous "exit-code 0" wording.
56. T6: replaced `--symptom "..."` placeholder with concrete symptom text.
57. Success criteria: replaced undefined "5 gap" with explicit "5 op chain (MMUL→SFU→Vector→DMA→Residual)".
58. T5 pytest AC: made awk threshold check more explicit/robust (`v=int($1); exit (v<210)`).

### Fixes applied after Pass 4 dual review (Momus + Oracle both REVISE) before Pass 5
59. Success criteria `testcase-list-perf.md`: corrected remaining stale `≥25` to `≥20` to match T8 AC.
60. T6 K=512 AC: tightened `cos_sim.*0\.99[0-9]` to `cos_sim=(0\.999[0-9]|1\.0)` to enforce ≥0.999.
61. T8 PERF evidence files: split PERF-13 check to `w4-perf-p3.txt` and PERF-17 check to `w4-perf-p4.txt`.
62. Bootstrap `p9_fw_rebuild.sh`: fixed timestamp comparison bug (`-nt` on numeric strings → `-gt` integer comparison).
63. T5 MXU AC: tightened `MXU.*9.*PASS` to `MXU.*9/9.*PASS|MXU.*all.*9.*PASS`.
64. T8 fullchain: added DMA/AXI non-zero traffic AC (`DMA_(wr|rd)_bytes|axi_.*_bytes|nonzero_traffic=1`).
65. T3/T9 `grep -c 'cos_sim'` ACs: changed to numeric regex `cos_sim=[0-9]\.[0-9]+` to ensure actual values.
66. T1 md5 AC: changed from `grep 'md5'` to match real `md5sum` output format (`^[a-f0-9]{32}  firmware/build/npu_firmware\.elf`).
67. T3 Evidence: standardized bug report filename from `BUG-MXU-P9-NNN-...` to `BUG-MXU-P9-001-...`.
68. Bootstrap: added `mkdir -p` for `.omo/notepads/phase9-firmware-rtl-fix/` and removed duplicate `build/evidence` mkdir.
69. Success criteria: clarified "T4 fix 后 PERF-13" → "T4 fix 后 directed sweep（对应 PERF-13 场景）"; fixed bug report filename.
70. T9 judge AC: required `ba/judge=...` to be on the same line as the 6b checkbox.

After Pass 5 completes, the phase9 plan awaits dual-OKAY, then status → awaiting-approval and brief to user.

### Fixes applied after Pass 5 dual review (Momus + Oracle both REVISE) before Pass 6

1. T8 AC regexes changed from `cos_sim=` to JSON key syntax `"cos_sim":`, `nonzero_traffic=1` to `"nonzero_traffic": 1`.
2. T4A What-to-do added `// P9-A` marker annotation to branch A step (a).
3. T4B What-to-do added `ph9-t4b-elapsed.txt` marker creation with `VCS_EXIT_CODE=0`.
4. T9 `ba/judge=` prefix clarified as judge field bound to 6b checkbox same line.
5. T5 branch B added explicit FM-SOC simv path `build/p9_simv_soc_top` and log instruction.
6. F2 whitelist expanded to include `build/evidence/ph9-*`, `w4-perf-p*.txt`, `fullchain-pipeline.txt`, `f{1,2,3,4}-*`, `36layer-checkpoint.txt`.
7. T4A AC regex decoupled from variable names: split into removal count check and `// P9-A` addition count check.
8. T9 download steps clarified with `--local-dir ~/models --local-dir-use-symlinks False` and target path.
9. T2 probe JSONL AC moved to T3 (probes triggered during divergence sweep, not harness).
10. T6 evidence path `w4-perf-p2.txt` renamed to `ph9-t6-p2-k512.txt` to avoid T8 conflict.
11. T7 stale `--out` reference to `scripts/run_36layer_checkpoint.py` removed from Must NOT do.

### Fixes applied after Momus Pass 6 (before Pass 7)

1. **BLOCKER** — bootstrap `write_script()` heredoc: `<<EOF` → `<<'EOF'` to prevent command substitution expansion during bootstrap; removed `\$` backslash escapes from `source "$(dirname $0)/p9_lib/p9_sz0001.sh"`.
2. **BLOCKER** — T9 What-to-do inline `huggingface-cli` literal removed, moved into `scripts/p9_q8o_download.sh` script description only.
3. **MAJOR** — T6 AC regex changed from `cos_sim=` to `"cos_sim":` for JSON-line evidence file `ph9-t6-p2-k512.txt`.

All 3 issues eliminated (2 BLOCKER, 1 MAJOR). Zero residual.

### Fixes applied after Pass 7 dual review (Momus + Oracle both REVISE) before Pass 7.1

1. **BLOCKER** — Bootstrap `write_script()` heredoc: `<<'EOF'` → `<<EOF` (unquoted) so `${content}` expands at bootstrap time; `$(dirname $0)` escaped to `\$(dirname \$0)` for literal preservation in generated scripts.
2. **BLOCKER** — `p9_ssh()` missing Python/cocotb env: added `source sim/regression/run_env.sh` after `cd`.
3. **BLOCKER** — T4 branch B VCS compile used wrong top and missing VPI: changed to `-top tb_soc rtl/tb/tb_soc.v` + `+define+COCOTB_SIM=1 +vpi -P sim/regression/pli.tab -load $(cocotb-config --lib-name-path vpi vcs) -o build/p9_simv_soc_top -l build/p9_soc_elaborate.log`.
4. **BLOCKER** — `--rtl-report` filename: `docs/bugs/BUG-MXU-P9-NNN-<slug>.md` → `docs/bugs/<slug>.md`; caller provides complete ID.
5. **MAJOR** — T6 sub-step order: swapped (c) firmware rebuild before (d) K=512 test.
6. **MAJOR** — T5 branch B stale `simv_soc_ibex`: added delete instruction before FM-SOC regression + recompile check in AC.
7. **MAJOR** — T8 Success criteria: "所有 FAIL 必须改为 PASS" relaxed to "最多 1 行保留 SKIP/NOT RESOLVED 等例外状态".
8. **MINOR** — T4 branch B elaborate log: `-l build/p9_soc_elaborate.log` added to VCS command; `test -s` AC added.

All 8 issues eliminated (4 BLOCKER, 3 MAJOR, 1 MINOR). Zero residual.

### Fixes applied after Pass 7.1 residual review before Pass 8

1. **RESIDUAL** — Line 58 `--rtl-report BUG-MXU-P9-NNN-<slug>` → `--rtl-report <slug>` to match AC filename convention (`docs/bugs/<slug>.md`).
2. **RESIDUAL** — Line 109 `BUG-MXU-P9-00B` bare ID → `BUG-MXU-P9-00B-broadcast-multitile` to match AC filename `docs/bugs/BUG-MXU-P9-00B-broadcast-multitile.md`.

Both residuals eliminated. Zero residual.

### Fixes applied after Momus Pass 8 (before Final Wave)

1. **MAJOR** — T4B What-to-do step b inline VCS command literal removed (violated SCRIPT-FIRST); replaced with script-only instruction `bash scripts/p9_fix_branch_b.sh`; full VCS command details (filelist paths, cocotb-config VPI flags, `-o build/p9_simv_soc_top`, `-l build/p9_soc_elaborate.log`, exit code check, elapsed marker) moved into `p9_fix_branch_b.sh` inventory entry.

Zero BLOCKER/MAJOR residual.

### Final Verification Wave (Pass 9 / Final)

**Date:** 2026-07-20

- **Momus** verdict: **APPROVE** — no BLOCKER/MAJOR remaining; 5 previously noted items reduced to observations.
- **Oracle** verdict: **APPROVE** — with 3 minor observations:
  1. T1 bootstrap script creates files in `scripts/p9_lib/` which is not currently tracked by `.gitignore` or git; ensure executor commits or explicitly ignores.
  2. T4-B VCS elaboration command lives inside `scripts/p9_fix_branch_b.sh`; verify on sz0001 that `tb_soc` top, VPI flags, and `-f rtl/soc/soc.flist` produce `simv` exit 0 before Wave 2 execution.
  3. F4 scope gate relies on `build/evidence/ph9-base-commit.txt`; ensure bootstrap writes this file before any final-wave script runs.

Plan approved by both Momus and Oracle. Awaiting user explicit okay to proceed with `/start-work phase9-firmware-rtl-fix`.