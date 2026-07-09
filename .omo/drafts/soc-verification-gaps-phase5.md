# Draft: soc-verification-gaps-phase5
intent: clear
review_required: true
status: adjusted-plan-in-review
round: 5
decisions:
  - INT8 mode deferred to future phase (not in current plan)
  - Golden reference: Func Model `.npz` only; llama.cpp for tolerance derivation
  - RTL bug fixes allowed in INT4×INT8 datapath scope
  - Spike + real firmware for descriptor alignment (not miniv.py)
  - Wave 3 RTL tasks (dual-path RTL, MobileNetV3 RTL) deferred to Phase 6 (FPGA)
  - Wave 4 PERF-01..P20 SoC-level performance entirely deferred to Phase 6 (FPGA)
  - Phase 5 retains: W1, W2 (P0-P3 complete SFU+Vector perf), W3-FM, W5, Phase 5.5 FPGA readiness, Final verification (Phase 5 scope only)
  - L35 drift: W1.6 marked conditional PASS (L0/L10/L20 ≥ 0.999, L35=0.998278 documented); Q8_0 control experiment deferred to Phase 6 (todo 6b)
round5_fixes:
  - W1.3: clarified per-op regression vs true E2E acceptance, added full-chain cos_sim criteria
  - W1.6: split into numerical gate (conditional PASS) + Q8_0 control experiment (deferred 6b)
  - Duplicate #7 resolved: first renamed to 7 (36-layer golden), second to 8 (intermediate compare), review gate to 9
  - W2 todos completely restructured: 2.1-2.4 filled with evidence/acceptance/QA/commit/evidence paths; 2.5-2.8 given full executable structure; 2.9 review gate with evidence path
  - W2 numbering changed to W2.N to avoid collision with W1
  - W3.4: acceptance updated to actual verified scope (40/52 layers; top-1 deferred to Phase 6)
  - F2: locked exact FM-SOC case IDs and count (33+5=38 items); removed "~6" approximation
  - F4: expanded to include file-system checks + wave-lead declaration
  - Added F0 Phase 5.5 FPGA readiness inventory + go/no-go gate
  - Success criteria table: added ⚠️ for conditional items, ⏭️ for deferred, added L35 root cause and FPGA readiness rows
  - Anti-vacuous spec: strengthened W1.8 intermediate compare corruption to 32-byte 0x80 overwrite
  - Cross-server toolchain constraint documented in Pre-Wave 0.1 notes
  - VCONV_F16_I32 prerequisite dry-run added to W2.5
known_remaining:
  - Wave 2: 2.5-2.9 pending (~3-4 days)
  - Wave 3: Review Gate 20 pending (~0.5 days)
  - Phase 5.5: F0 pending (FPGA readiness, estimates TBD)
  - Final: F1-F4 pending (~1 day)
metis_round1: 12 issues found, all HIGH addressed
momus_round1: REJECT, 3 blockers resolved
metis_round2: REJECT, 3 issues: duplicate numbering, llama.cpp, miniv.py — ALL FIXED
momus_round2: REJECT, 3 issues: T8 QA fail, Wave 2 perf QA fails, W4+F2 QA fails — ALL FIXED
metis_round3: CRITICAL: W1.6 conditional PASS, W3.4 top-1 unverified, W2 underspecified, Phase 6 no FPGA plan — ALL FIXED
momus_round3: REJECT: W2 incomplete, duplicate numbering, regression baseline inconsistency — ALL FIXED
round5_review_target: Metis + Momus re-review after fixes applied
round5_review_result: Metis CONDITIONAL APPROVE, Momus CONDITIONAL — both found W2.8/W2.9 case count inconsistency and Momus found W2.2 wrong model paths
round5_1_fixes:
  - W2.2: fixed model paths from `sim/timing/models/sfu_model.py` → `sim/models/sfu.py` and `sim/timing/models/vector_model.py` → `sim/models/vector.py`; fixed QA command to use existing `scripts/verify_ops_func_model.py`
  - W2.9: fixed case count from 34/34 (5 P3) → 35/35 (6 P3 = SFV-P29..P34) to align with W2.8 spec
  - Success criteria table: updated SFU+Vector entry to show "35/35 SFV cases"
