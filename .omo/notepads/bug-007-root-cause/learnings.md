# learnings — bug-007-root-cause

Methodology takeaways from the BUG-RTL-SOC-007 root-cause investigation
(plan `.omo/plans/bug-007-root-cause.md`, todos 0-8; evidence in
`.omo/evidence/task-{0,1,2,6,7,8}-bug-007-root-cause.txt`).

## Mode archaeology under deleted evidence (2026-09-01/02)
- The original 2026-07-07 W1.3 failure artifact (45 PASS / 6 FAIL) was deleted from git, so
  "what mode did the failure run in" had to be recovered from sources that DID survive:
  the W1.3-era plan definition (`.omo/plans/soc-verification-gaps-phase5.md:186-190`), the
  first committed testcase (79654175:sim/cocotb_bridge.py), and the structurally-identical
  HEAD test (:5261-5480). Three independent sources converged on `MODE-ORIG: per-op-preload`.
- Lesson: when the failure artifact is gone, classify the MODE first from committed code +
  plan text, then re-run the reconstructed mode at HEAD; do not trust the ledger's
  symptom wording as evidence. The ledger said "firmware ring ... 32-entry ring overflow",
  but per-op mode has NO firmware ring dispatch at all — the ledger's H1 was a hypothesis,
  refuted twice over by code inspection (0 doorbell/ring hits in the test body +
  RING_ENTRIES=1024 in firmware).

## Differential / 证伪判据 design (2026-09-02)
- H0 was built as a differential: same manifest, same golden criteria, FuncModel replay
  (fm_adapter-python via `sim/verification/bug007_fm_replay.py`) vs RTL re-run, filling a
  2x2 matrix (FM-PASS/FAIL x REPRO-CLEAR/FAIL). The observed cell (FM-PASS x REPRO-CLEAR)
  routes to archaeology; each other cell had a pre-declared routing rule.
- Falsifiable invariants were named BEFORE running: H1 = descriptor bytes byte-identical to
  staged commands, H2 = no overlap / no out-of-window / no 512B-chunk pollution radius,
  H3 = START pulse consumed vs DONE pre-set, H4 = cycles>0 + cos gate on the un-clipped
  shape. Each hypothesis ended as confirmed / refuted / skipped-inapplicable with citation —
  no `no_silent_skip` was allowed.
- Lesson: 证伪判据 must be written as a testable invariant with an explicit value, not as a
  narrative; the verdict line (`H4-N128-BLK0-SINGLE: PASS ... cycles=31291 cos_sim=1.0`)
  is grep-able and machine-checkable in evidence.

## Reconstruction uncertainty when the testcase is uncommitted (2026-09-02)
- The 07-07 testcase was never committed (first commit 79654175 on 07-08, ~17h later), so
  every historical rerun is a RECONSTRUCTION, not a reproduction. todo 7 made this explicit
  in `RECONSTRUCTION-UNCERTAINTY: medium` and in the run artifacts: the effective earliest
  rerunnable point is 0973d76f (79654175 lacks the Makefile target), the W1.2 golden
  expected.npz was copied from the main worktree with 07-08 provenance, firmware hex was
  rebuilt on sz0002 (sz0001 lacks the RISC-V toolchain) and copied over, and the Makefile's
  hardcoded `./CaduceusCore/...` path forced `/tmp/bug007-parent-N/CaduceusCore` worktrees.
- All 3 rerun points (0973d76f, a8af3515, ef090b13) were CLEAR → `FLIP: reconstruction-failed`,
  i.e. the failure cannot be localized to any mainline commit — formally distinct from a
  dismissive "environmental flake" claim.
- Lesson: reconstruction artifacts must be listed as caveats in the same evidence as the
  verdict; a reconstruction failure is a valid, honest disposition when the original
  testcase never existed in git, and it must be routed to user acceptance (Blocker-6 path
  (b)), never auto-dismissed and never upgraded to "Fixed".

## The "cycles=0" measurement artifact trap (2026-09-01/02)
- The W1.3 summary shows `cycles = 0` for ALL MMUL ops — including the three attn_weight
  ops the ledger called "op never executed". Archaeology proved this is BY CONSTRUCTION:
  the test driver hardcodes `cycles = 0` right after `_run_streamed_mmul` in the original
  testcase (79654175:sim/cocotb_bridge.py:3798-3800, unchanged at HEAD :5382-5383), so a
  0 in the summary carries zero information about dispatch. The surviving 07-08 JSON shows
  attn_weight `passed=true, cycles=0` — i.e. the op DID execute.
- Real execution evidence had to come from elsewhere: `[mmul_probe] compute_en_cycles=19`
  (dbg_compute_en asserted 19 cycles, x2 N-tiles) + cos_sim=1.000000 / max_abs=0.0003
  (WARNING-level log lines), and in chain mode real per-op cycles (op07 = 31046).
- Lesson: before treating a suspicious numeric field as a failure signature, read the
  driver code that PRODUCES the field. A measurement artifact can turn a "failed op" ledger
  entry into "op executed fine" — and the plan's raw `cycles==0 ⇒ REPRO-FAIL` criterion had
  to be refined (REPRO-FAIL ⇔ ok=False OR compute_en_cycles==0) for MODE-A.

## MXU SCALE_ADDR cross-flow hazard (2026-09-02)
- H0's cheap firmware-resident differential (variant B: let firmware dispatch one real ring
  MMUL, then replay the 51-op per-op stream) FAILED many later MMUL ops (op07 cos=0.2308).
  Root cause: the firmware ring dispatch leaves MXU SCALE_ADDR=0x14000 latched in the
  bridge/wrapper; the per-op driver never writes SCALE_ADDR, and
  `MMIOBridge._run_mxu_compute` (and the RTL analogue `mxu_soc_wrapper.v:173-182`) switches
  to scaled-FP32 store-out whenever SCALE_ADDR != 0 — silently changing output semantics
  for an unrelated flow.
- A defensive `SCALE_ADDR=0` write per MMUL dispatch restores parity (51/51 both variants),
  proving the mechanism. Not the 07-07 cause (pure MODE-A never dispatches firmware →
  SCALE_ADDR stays 0) but a LIVE cross-flow stale-state hazard for mixed
  firmware-then-per-op flows.
- Lesson: cross-flow stale state (a register written by one dispatch path, consumed by a
  different one) is a distinct hazard class from within-flow bugs; cheap differentials that
  perturb dispatch order expose it in minutes, and a confirmed instrument-level finding
  with an RTL analogue must be recorded as a residual candidate, not swept under the
  attribution.
