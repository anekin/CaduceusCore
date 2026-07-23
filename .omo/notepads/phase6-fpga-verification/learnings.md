# Metis pre-execution review 2026-07-10

> Scope: `.omo/plans/phase6-fpga-verification.md` (VCS-based RTL verification, no FPGA)
> Context: Phase 5 deferred W3-RTL, W4-PERF and 36-layer RTL to "Phase 6 (FPGA stage)". This review treats the plan as read-only and surfaces issues for Prometheus to resolve before execution.

---

## 1. Hidden Intentions

1. **Strategic pivot from FPGA to VCS is not treated as a decision**. Phase 5 explicitly deferred the RTL work to "Phase 6 (FPGA stage)" because VCS full-chain runtime was "数十分钟级" vs FPGA seconds (`soc-verification-gaps-phase5.md:10-11`, `:24-26`, `:593-602`). Phase 6 now says "不使用 FPGA" (`phase6-fpga-verification.md:5`) and "不会做：...FPGA 相关任何工作" (`:9`). The plan never explains *why* the FPGA assumption was dropped or what changed. This is a phase-transition risk that should be a documented go/no-go decision, not an implicit assumption.
2. **Func Model is being asked to validate itself via RTL**. FM-1 (`:66-76`) and FM-3 (`:87-94`) acceptance criteria defer cross-engine/cross-model validation to the very RTL measurements they are supposed to predict. The hidden loop is: build model → use RTL data to validate model → use model to accept RTL. There is no independent golden for the performance pipeline.
3. **36-layer "checkpoint" approach narrows the deferred scope without calling it a reduction**. Phase 5 deferred "36-layer RTL SoC 全量 forward pass" (`soc-verification-gaps-phase5.md:250`, `:591-592`). Phase 6 changes this to four checkpoints (`phase6-fpga-verification.md:186-193`). The checkpoint rationale (VCS runtime) is sound, but the deliverable is different and should be flagged as a negotiated scope change.
4. **CV validation is deliberately a single-op demo, not model-level coverage**. FM-2 and W3-RTL task 19 only verify one Conv2D layer (`:78-85`, `:123-130`), while Phase 5 MobileNetV3 FM E2E covered 40/52 layers (`soc-verification-gaps-phase5.md:403`). The remaining CV layers are silently excluded.
5. **The plan is written as if Phase 5 W2 PERF was fully closed**, but Phase 5 left W2.5–W2.8 as "PENDING" (`soc-verification-gaps-phase5.md:309-362`). FM-1 depends on `build/evidence/sfv-P2-back-to-back-summary.json` (`phase6-fpga-verification.md:67`), whose production was itself pending.

---

## 2. Ambiguity Hotspots

1. **"Representative configuration" is never defined**. Lines `:19`, `:139`, `:152`, `:159` claim PERF cases will use "代表性配置" (e.g. K=128,N=64), but there is no selection matrix, no rationale, and no mapping from PERF-01..P20 to concrete dimensions. This makes "20/20 measured" (`:267`) unverifiable.
2. **Tolerances for FM-1 are internally inconsistent**. Same-engine gap is ±10% (`:71`) but QA-fail triggers only at >50% (`:74`). Cross-engine gap is ±50% (`:167`). It is unclear whether the 10% is a *goal* and 50% a *hard gate*, or whether they apply to different metrics.
3. **"Consistent with 0.998278" for L35 is unquantified**. Line `:189` says L35 cos_sim should be "consistent with Phase 5 FM result (0.998278)". Consistent could mean ≥0.998, within ±0.001, or simply "not lower". A hard threshold is needed for agent execution.
4. **Anti-vacuous corruption method is unspecified**. Task 17b says "Corrupt PCIe routing to verify anti-vacuous detection" (`:116`) but gives no signal, layer, or injection point. Task 19's "im2col tile schedule exceeds SRAM" (`:128`) is similarly undefined.
5. **"Measured" vs "PASS" for PERF cases**. Line `:267` says success is "20/20 cases measured", but PERF-21 acceptance (`:144`) says "All 4 cases PASS". Some PERF cases are infrastructure/measurement tasks (P03, P17) where PASS is ambiguous.
6. **Q_proj K dimension mismatch**. FM-3 / W4-PERF-09..P12 references "Q_proj, K=2048" (`:159`), but the SoC perf spec and model specs use Qwen2.5-3B Q_proj as K=2560, N=4096 (`rtl/testcase-list-perf.md:16`, `:89-90`). This will cause wrong test vectors or wrong model predictions.
7. **FM-1 "validation deferred" vs Path D dependency**. FM-1 cross-engine validation is deferred to W4 PERF-13..P16 (`:72`), but Path D "部分依赖 FM-1" (`:244`). Which sub-tasks depend on which FM-1 outputs is not specified.
8. **Pre-existing failures ≤10 is not enumerated**. Final regression F2 (`:210`) allows up to 10 pre-existing failures but does not list them. An agent cannot verify this criterion without a known-fail list.
9. **"Realistic same-engine gaps" in FM-4 Review Gate (`:98`)** has no operational definition.
10. **Per-tile cycle JSON format is unspecified**. P03 (`:144`) requires JSON but fields, schema, and path are not defined.

---

## 3. Missing Success Criteria

1. **No VCS environment readiness gate**. Phase 5 had Pre-Wave 0.1/0.2 gates (`soc-verification-gaps-phase5.md:154-164`). Phase 6 jumps straight to execution without re-verifying `module load vcs`, cocotb, license, or `simv_soc_cocotb` state.
2. **No compile-success criterion for `simv_soc_cocotb`**. The plan says "复用 Phase 5 的 `simv_soc_cocotb`" (`:16`). If the binary is missing, stale, or compile fails, there is no explicit acceptance/failure path.
3. **No license/resource availability criterion**. Path C/D/E require VCS and should be serialized to avoid license conflict (`:248`), but there is no check that licenses are free or a max-parallelism rule.
4. **No runtime contingency**. Estimated 8-12h for W4 (`:139`) and 2-4h for 36-layer (`:188`). No criterion for what happens if runtime exceeds estimate (e.g. timeout, abort, split case).
5. **No firmware-toolchain dependency check**. Phase 5 noted "sz0001 lacks riscv-gcc — firmware build on sz0002, workaround documented" (`soc-verification-gaps-phase5.md:583`). Phase 6 SoC tests still need firmware but do not mention this dependency.
6. **No evidence schema**. Many tasks say "evidence: build/evidence/xxx.txt" but specify only a grep command. Minimum required fields (case_id, status, cycles, cos_sim, timestamp, commit) are absent.
7. **No criterion for what to do if a checkpoint fails**. 36-1 says "isolate to specific layer and debug" (`:188`), but there is no procedure for isolation (e.g. rerun with per-layer dumps, bisect layers).
8. **No success criterion for FM-3 beyond field presence**. FM-3 acceptance is just `weight_streaming_overlap_ratio` present (`:90-91`), with validation deferred. This means the model can be trivially accepted without correctness validation.
9. **No disk-space / FSDB-size criterion**. Long VCS runs with waveforms can exhaust `/tmp` or `build/`. Phase 5 F4 had `/tmp` hygiene checks; Phase 6 has none.
10. **No explicit contingency if VCS proves too slow or fails**. Phase 5 had an FPGA fallback; Phase 6 has no fallback after abandoning FPGA.

---

## 4. Dependency Gaps

1. **Circular dependency: FM-1 ↔ W4-PERF**. FM-1 cross-engine gap validation depends on W4 PERF-13..P16 (`:72`). Path D "部分依赖 FM-1" (`:244`). If both start in parallel, neither can validate the other; if sequential, order matters and is unspecified.
2. **FM-2 golden dependency for W3-RTL task 19**. Task 19 compares RTL output against "FM-2 golden" (`:125`), but FM-2 (`:78-85`) is on Path A and could finish after Path C starts. The plan does not state that FM-2 must be a hard gate for task 19.
3. **W4-PERF P0 is a hidden hard gate**. PERF-01..P04 infrastructure (`:141-148`) must work before P1-P4 can run, but the dependency graph (`:239-246`) shows Path D as a single linear chain without a P0 gate.
4. **36-layer depends on Phase 5 `.npz` golden files**. Task 36-1 references `rtl/test_vectors/soc_e2e/qwen25-3b-36layer/` (`:187`) and Phase 5 `.npz` golden (`:188`), but there is no existence/integrity check.
5. **FM-1 depends on Phase 5 P2 back-to-back data**. Line `:67` references `build/evidence/sfv-P2-back-to-back-summary.json`, but Phase 5 W2.7 was pending; if it did not produce the artifact, FM-1 cannot meet its ±10% same-engine acceptance.
6. **W4-PERF tasks assume `rtl/testcase-list-perf.md` thresholds**. PERF-21 says "All 4 cases PASS" but the spec defines specific thresholds (e.g. PERF-01 wrapper overhead ≤5× module cycles) that are not copied into the plan.
7. **No dependency on Phase 5 F0 FPGA-readiness gate closure**. Phase 6 should acknowledge the Phase 5 F0 deferred decision (`soc-verification-gaps-phase5.md:509-515`) and either confirm it was closed or superseded.

---

## 5. VCS-Specific Risks

1. **Stale `simv_soc_cocotb` binary**. Reusing Phase 5's binary (`:16`) is efficient but dangerous if RTL or testbench files changed. Phase 5 had explicit stale-binary cross-cut checks (`soc-verification-gaps-phase5.md:319`, `:333`, `:346`, `:537`). Phase 6 has none.
2. **Long debug cycles**. 36-layer single run is 2-4h (`:188`); W4 is 8-12h (`:139`). A single bug requiring 3-5 iterations can consume days. The checkpoint strategy helps but does not eliminate the risk.
3. **Full-chip compile fragility**. `simv_soc_cocotb` is a full SoC compile. If it fails at Phase 6 start, all RTL paths block. No compile-smoke task is defined.
4. **License contention and server availability**. Three VCS paths (C/D/E) are supposed to be serialized (`:248`), but there is no enforcement or monitoring mechanism. A long 36-layer run could starve others.
5. **Non-deterministic repeatability**. Task 25 requires 3-run std ≤5% of mean (`:175`). VCS + Cocotb repeatability can be affected by host load, initialization races, or DRAM model timing; no repeatability protocol (same seed, same server load) is specified.
6. **Checkpoint failure debuggability**. If L10 fails in 36-1, isolating the layer requires intermediate dumps or rerunning from L0; the plan does not define this procedure.
7. **FSDB/waveform disk exhaustion**. Long runs with `-debug_access+all` and FSDB dumps can fill disk. No cleanup or size-limit policy is stated.
8. **Cocotb/VPI hangs**. Long SoC simulations may hang silently. No timeout/heartbeat/watchdog is specified for any VCS task.
9. **RTL bug fixes require recompile**. Line `:28` allows RTL bug fixes, but line `:16` says reuse the same simv. Any RTL fix invalidates the reused binary; the plan does not include a recompile trigger.
10. **VCS-only fallback after FPGA pivot**. Phase 5 explicitly chose FPGA to avoid VCS inefficiency. Phase 6 is now VCS-only without a new fallback, so if VCS becomes the bottleneck there is no escape route.

---

## 6. Cross-Plan Consistency with Phase 5 Deferrals

1. **Title/strategy mismatch**. Phase 5 deferred to "Phase 6 (FPGA stage)" (`soc-verification-gaps-phase5.md:10`, `:593`). Phase 6 file is named `phase6-fpga-verification.md` but states "不使用 FPGA" (`phase6-fpga-verification.md:5`). This is the most visible inconsistency and should be resolved (rename plan or document the VCS pivot).
2. **36-layer scope reduction**. Phase 5 deferred "36-layer RTL SoC 全量 forward pass" (`soc-verification-gaps-phase5.md:591-592`, `:250`). Phase 6 delivers checkpoint-only (`phase6-fpga-verification.md:186-193`). The reduction is reasonable but must be treated as a scope change, not a 1:1 fulfillment of the deferral.
3. **MobileNetV3 acceptance tightened for a subset**. Phase 5 success criteria expected MobileNetV3 RTL "cos_sim ≥ 0.95" (`soc-verification-gaps-phase5.md:589`). Phase 6 requires a single Conv2D "cos_sim ≥ 0.99" (`phase6-fpga-verification.md:126`). Higher bar, smaller scope.
4. **Regression baseline numbers disagree**. Phase 5 F2 locked "pytest 700/9, FM-SOC 33/33, MXU 9/9, Vector 64/64, SFU 526/537" (`soc-verification-gaps-phase5.md:529`). Phase 6 says "pytest ≥210, FM-SOC 33/33, MXU 9/9, SFU 319/319, Vector 63/63" (`:209`, `:228`). These cannot both be true; the authoritative baseline must be reconciled.
5. **Must-NOT-Have list is consistent**. Both plans exclude INT8×INT8/BF16, synthesis/physical design, new engine architecture, and FPGA work. Good.
6. **Q8_0 control experiment (6b) carries over correctly**. Phase 5 todo 6b (`soc-verification-gaps-phase5.md:240-244`) maps cleanly to Phase 6 6b (`phase6-fpga-verification.md:104-110`). Good.
7. **W3-RTL / W4 task IDs are preserved**. Task 17b, 19, 21-26, and 36-layer are reused from Phase 5 deferrals. Good.
8. **Review Gate discipline is consistent**. Both plans use Atlas/Oracle audit for wave gates. Good.

---

## 7. Top 5 Recommendations

1. **Add a Phase 6 VCS readiness go/no-go gate** before any RTL task starts. Verify: (a) `simv_soc_cocotb` compiles cleanly from current RTL, (b) VCS license/server available, (c) firmware `.hex` can be generated (or workaround documented), (d) Phase 5 evidence files (`sfv-P2-back-to-back-summary.json`, 36-layer `.npz`) exist and are non-empty. This replaces the missing Phase 5 F0 FPGA gate.
2. **Break the FM-1 / W4-PERF circular dependency**. Make FM-1 acceptance depend *only* on same-engine gap calibration (≤±10% vs Phase 5 P2 data). Move cross-engine gap validation to a new task **FM-1b** that runs *after* W4-PERF-13..P16 and validates FM-1 predictions against measured cross-engine gaps. Path D can then depend only on FM-1a.
3. **Publish a concrete PERF representative-config matrix**. Map PERF-01..P20 to exact (M,K,N) values and explain why each is representative. For example: PERF-05/06 = (1,128,128), PERF-09 = (1,256,64), PERF-11 = (1,2560,4096). Fix the Q_proj K=2048 vs K=2560 mismatch.
4. **Institute a compile/staleness check for every VCS task**. Each RTL task must start with: `git diff --name-only rtl/ sim/tb/ sim/regression/ | grep -q . && make -C sim/regression clean simv_soc_cocotb`. This prevents the biggest VCS-specific risk (stale or missing binary) from causing false PASS or wasted hours.
5. **Define an actionable checkpoint-failure procedure for 36-layer**. If any checkpoint fails, the procedure should be: (a) rerun from the previous passing checkpoint with per-layer `.npz` dump enabled, (b) compare each layer against Phase 5 golden, (c) report the first failing layer and its cos_sim, (d) only then enter debug. Add the required per-layer dump capability to the runner if absent.

---

*End of Metis pre-execution review.*
