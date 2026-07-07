# Draft: soc-verification-gaps-phase5
intent: clear
review_required: true
status: awaiting-approval
round: 3
decisions:
  - INT8 mode deferred to future phase (not in current plan)
  - Golden reference: Func Model `.npz` only; llama.cpp for tolerance derivation
  - RTL bug fixes allowed in INT4×INT8 datapath scope
  - Spike + real firmware for descriptor alignment (not miniv.py)
round2_fixes_applied:
  - Duplicate T10 numbering fixed (Wave 2: 10→11, 11→12, 12→13, 13→14)
  - Success criteria: "vs llama.cpp" → "vs Func Model golden `.npz`"
  - Success criteria: "miniv.py" → "C firmware + C header + Python Func Model + RTL MMIO"
  - T8 QA fail added
  - T11-T14 QA fail added (Wave 2 sweeps + edge cases)
  - T20 QA fail added (W3 Review Gate)
  - F2 QA fail added; "INT8=3" → "ISA-opcode=3"
  - Wave 3 start number 15→16
known_remaining:
  - Wave 3/4/5 todo cascade renumbering (17→18, 18→19, ...) — non-blocking, executor can follow
  - T23-T26 QA fail not added (W4 perf + gate) — best-effort, thresholds exist in acceptance
metis_round1: 12 issues found, all HIGH addressed
momus_round1: REJECT, 3 blockers resolved
metis_round2: REJECT, 3 issues: duplicate numbering, llama.cpp, miniv.py — ALL FIXED
momus_round2: REJECT, 3 issues: T8 QA fail, Wave 2 perf QA fails, W4+F2 QA fails — ALL FIXED
