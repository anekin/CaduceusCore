TODO 3 EVIDENCE — C2b module-level Fix Commit integrity + Stats refresh
========================================================================
Date:       2026-09-02
Branch:     evidence-integrity-and-readme-status
Ledger:     docs/bugs/bugs-module-level.md
Pre-commit: 46019b4 chore(omo): P0 baseline — branch + ingestion preflight

====================================================================
PART A — Git archaeology (background context justifying ledger text)
====================================================================

A1. Prove `if (perf_counting)` gating never existed in any commit
------------------------------------------------------------------
COMMAND: git log --all -S "if (perf_counting)" -- '*tb_mxu_perf*'
OUTPUT:  (empty — zero commits matched; exit code 0)
RESULT:  PASS — the string "if (perf_counting)" appears in NO commit
         of rtl/tb/tb_mxu_perf.v. The ledger statement "the
         `if (perf_counting)` accumulation gating never existed in
         any commit" is archaeologically verified.

A2. Real fix-commit candidate for BUG-MX-PERF-000: 675afe0a
------------------------------------------------------------
COMMAND: git show --stat 675afe0a18ff1a739f8ddae2a6c7ec331e93a40b
OUTPUT:
commit 675afe0a18ff1a739f8ddae2a6c7ec331e93a40b
Author: zhengs <zhengs@caduceuscore.dev>
Date:   Thu Jul 2 22:26:33 2026 +0800

    [MXU-Perf] Phase 0 infrastructure: --shape CLI, tb_mxu_perf.v, analyze_perf.py

 rtl/tb/tb_mxu_perf.v       | 919 +++++++++++++++++++++++++++++++++++++++++++++
 scripts/analyze_perf.py    | 348 +++++++++++++++++
 scripts/gen_mxu_vectors.py |  28 +-
 3 files changed, 1294 insertions(+), 1 deletion(-)

COMMAND: git show 675afe0a -- rtl/tb/tb_mxu_perf.v | grep -n "state != S_IDLE && state != S_DONE"
OUTPUT:
302:+            if (state != S_IDLE && state != S_DONE) begin

COMMAND: git show HEAD:rtl/tb/tb_mxu_perf.v | grep -n "state != S_IDLE && state != S_DONE"
OUTPUT:
290:            if (state != S_IDLE && state != S_DONE) begin

RESULT:  PASS — tb_mxu_perf.v was CREATED by 675afe0a (2026-07-02) and
         already carried the correct state-based gating
         `if (state != S_IDLE && state != S_DONE)` from birth
         (HEAD :290; diff hunk :302 in the creating commit). No
         separate later "fix" commit for this gating exists, so the
         ledger entry must point at 675afe0a as the commit that
         introduced the correct gating, and must NOT imply a
         pre-fix diff existed (git log -S on `if (perf_counting)`
         is empty per A1).

A3. Origin of the fake sha: docs-split commit 2983e97b
------------------------------------------------------
COMMAND: git log --oneline --follow -- docs/bugs/bugs-module-level.md
OUTPUT:
513fba6 fix(perf): correct prefill bottleneck from DMA-bound to compute-bound
2983e97 [Doc] Split bug tracking into per-phase files

COMMAND: git show 2983e97b:docs/bugs/bugs-module-level.md | grep -n "a1b2c3d4"
OUTPUT:
68:`a1b2c3d4` — Changed accumulation condition from `if (perf_counting)` ...

COMMAND: git show 2983e97b --stat | head -12
OUTPUT:
commit 2983e97b9e3b57fb4bb7549d3c0879fe5a112eef
Author: zhengs <zhengs@caduceuscore.dev>
Date:   Mon Jul 6 14:38:54 2026 +0800

    [Doc] Split bug tracking into per-phase files

    Migrate docs/bugs.md into per-phase files per Lesson 11:
    - docs/bugs/bugs-module-level.md (module-level MXU/SFU/Vector bugs)
    - docs/bugs/bugs-soc-func-model.md (SoC Func Model golden reference bugs)
    - docs/bugs/bugs-soc-rtl.md (already existed with 6 RTL bugs)
    - docs/bugs/bugs-archive.md (archived original)
    - .omo/notepads/soc-verification-gaps-phase5/learnings.md

RESULT:  PASS — BUG-MX-PERF-000's Fix Commit sha `a1b2c3d4` is a
         placeholder. It was introduced when docs-split commit
         2983e97b (2026-07-06) migrated docs/bugs.md into
         bugs-module-level.md. No real commit 675afe0a-prefix or
         full sha equals `a1b2c3d4`. The entry mirrors the archive
         placeholder example at docs/bugs/bugs-archive.md:101
         (grep -n "占位示例" -> line 101, NOT 109).

A4. Real sha for BUG-PERF-MXU-001: 513fba6 (full stat, for context only)
-----------------------------------------------------------------------
COMMAND: git show --stat 513fba6b7a1319542276e6cd2fedaac959c4aa8a
OUTPUT:
commit 513fba6b7a1319542276e6cd2fedaac959c4aa8a
Author: zhengs <zhengs@caduceuscore.dev>
Date:   Wed Aug 12 11:16:58 2026 +0800

    fix(perf): correct prefill bottleneck from DMA-bound to compute-bound

 docs/bugs/bugs-module-level.md                 | 33 ++++++++++++-
 reports/func-model-perf-verification-report.md | 68 ++++++++++++++------------
 sim/timing/model_scaling.py                    |  2 -
 sim/timing/qwen_spec_gates.py                  |  2 -
 4 files changed, 69 insertions(+), 36 deletions(-)

COMMAND: git log --oneline --follow -- docs/bugs/bugs-module-level.md | head -2
OUTPUT:
513fba6 fix(perf): correct prefill bottleneck from DMA-bound to compute-bound
2983e97 [Doc] Split bug tracking into per-phase files

RESULT:  PASS — 513fba6b7a1319542276e6cd2fedaac959c4aa8a (2026-08-12,
         "fix(perf): correct prefill bottleneck from DMA-bound to
         compute-bound") is the commit that BOTH removed the two-line
         override in qwen_spec_gates.py / model_scaling.py AND wrote
         the BUG-PERF-MXU-001 ledger entry. Full stat (4 files,
         +69/-36) captured here as background context only; ledger
         body carries sha+date+message only (no stat transcription).

====================================================================
PART B — Edits applied to docs/bugs/bugs-module-level.md
====================================================================

B1. BUG-MX-PERF-000 Fix Commit (:66-68)
    - Removed fake sha `a1b2c3d4`.
    - Now states: 675afe0a (2026-07-02) created tb_mxu_perf.v with
      the correct state-based gating (HEAD :290); no separate fix
      commit; `if (perf_counting)` never existed (git log -S empty);
      placeholder sha written by docs-split 2983e97b (2026-07-06);
      cross-ref docs/bugs/bugs-archive.md:101 "占位示例 — 非真实 Bug".
    - Does NOT imply a pre-fix diff existed (per A1/A2).

B2. BUG-PERF-MXU-001 Fix Commit (:114-116)
    - Inserted real sha 513fba6 with full hash + date + message.
    - Ledger body = sha+date+message + original one-line fix summary;
      NO stat details pasted (stat lives in PART A4 only).

B3. Stats table (:128-134) refreshed: Total bugs 3 -> 4,
    Fixed 2 -> 3 (BUG-MX-PERF-000 / BUG-001 / BUG-PERF-MXU-001),
    Open 1 unchanged (BUG-MXU-WDT-001).

Untouched: BUG-001 entry (sha 295d6b9 valid), BUG-MXU-WDT-001 body.

====================================================================
PART C — Acceptance assertions (pre-commit)
====================================================================

grep -c "a1b2c3d4" docs/bugs/bugs-module-level.md            -> 0     PASS
grep -n "675afe0a" docs/bugs/bugs-module-level.md            -> >= 1  PASS
grep -n "513fba6" docs/bugs/bugs-module-level.md             -> >= 1  PASS
grep -n "bugs-archive.md:101" docs/bugs/bugs-module-level.md -> >= 1  PASS
grep -n "bugs-archive.md:109" docs/bugs/bugs-module-level.md -> 0     PASS (no stale :109)
grep -E "Total bugs.*\| 4|Fixed.*\| 3" docs/bugs/bugs-module-level.md -> hit PASS

====================================================================
PART D — Staging & commit (pathspec only)
====================================================================

git add docs/bugs/bugs-module-level.md .omo/evidence/task-3-evidence-integrity-and-readme-status.txt
git diff --cached --name-only  (must equal exactly those 2 files)
git commit -m "docs(bugs): module-level fix-commit integrity — real shas + stats refresh" \
    -- docs/bugs/bugs-module-level.md .omo/evidence/task-3-evidence-integrity-and-readme-status.txt

====================================================================
PART E — Post-commit working-tree snapshot (appended after commit)
====================================================================
command: git status --porcelain
 M .omo/evidence/task-0-signoff-v3-runner.txt
 M .omo/evidence/task-2-evidence-integrity-and-readme-status.txt
 M .omo/evidence/task-20-uncertainty-kpis.json
 M .omo/evidence/task-23-perf-spec-ci.txt
 M .omo/evidence/task-3-evidence-integrity-and-readme-status.txt
 M .omo/notepads/evidence-integrity-and-readme-status/learnings.md
 M .omo/notepads/fm-e2e-qwen-cv-software-stack/learnings.md
 M .omo/notepads/phase6-rtl-verification/learnings.md
 M .omo/plans/evidence-integrity-and-readme-status.md
 M README.md
 M build/evidence/fm-cv-chain.txt
 M build/evidence/w3-4-mobilenetv3-fm.txt

MUST-NOT lines present: 7/7 (task-0-signoff-v3-runner.txt, task-20-uncertainty-kpis.json, task-23-perf-spec-ci.txt, fm-e2e-qwen-cv-software-stack/learnings.md, phase6-rtl-verification/learnings.md, fm-cv-chain.txt, w3-4-mobilenetv3-fm.txt)
SNAPSHOT_VALIDATION: PASS (all 7 Must-NOT dirty files present, none staged/committed by todo 3; git diff --cached empty after commit)

Extra in-flight lines (NOT committed by todo 3, NOT Must-NOT, orchestrator/parallel-session owned -> plan merge commit):
- .omo/evidence/task-2-evidence-integrity-and-readme-status.txt (parallel todo 2 post-commit snapshot append)
- .omo/evidence/task-3-evidence-integrity-and-readme-status.txt (this file: post-commit snapshot append per todo instruction #10)
- .omo/notepads/evidence-integrity-and-readme-status/learnings.md (this plan's notepad, appended finding)
- .omo/plans/evidence-integrity-and-readme-status.md (P0 todo-0 checkbox tick / orchestrator)
- README.md (parallel Wave-2 todo 4 in-flight edit)

RESULT:  PASS — pathspec discipline held; no `git add .`/-A/-a used; no Must-NOT file staged or committed.
