TODO 4 EVIDENCE — C3 README 项目状态总览 (plan option A)
========================================================
Date:       2026-09-02
Branch:     evidence-integrity-and-readme-status
File:       README.md
Pre-commit: 332a7d7 docs(bugs): module-level fix-commit integrity — real shas + stats refresh

====================================================================
PART A — Baseline (recorded BEFORE any edit)
====================================================================

A1. B0 = number of '（来源：' occurrences in README.md before editing
--------------------------------------------------------------------
COMMAND: grep -c '（来源：' README.md
OUTPUT:  0
RESULT:  B0 = 0

NOTE: inherited wisdom claimed README already had two '（来源：'
occurrences around lines 240/896. Actual grep shows the literal
marker '（来源：' count is 0 — those lines use the words '数据来源'
(数据来源 column headers etc.), NOT the '（来源：path）' marker
format. Incremental baseline B0 = 0 stands.

====================================================================
PART B — Edit
====================================================================

Insertion point: after README line 12 (blank line following the DSE
intro blockquote) and before former line 13 '## 文档索引'. New section
`## 项目状态总览` now occupies line 13, with blank lines preserved on
both sides (line 12 blank above, blank line + '## 文档索引' below).

B1. Heading line number
-----------------------
COMMAND: grep -n '^## 项目状态总览' README.md
OUTPUT:  13:## 项目状态总览
RESULT:  PASS — line 13 ∈ [13,15]

B2. Blank-line separation (no glued lines)
-------------------------------------------
COMMAND: sed -n '11,16p' README.md
OUTPUT:
> S2 和 S3 共享同一颗 die。两颗芯片覆盖三个产品线。面积模型经 TPUv1 ISCA 2017 die-shot 校准，BW 采用 area×7.5 GB/s/mm² 耦合模型。
<blank>
## 项目状态总览
<blank>
快照日期：2026-09-02
<blank>
RESULT:  PASS — blank lines above and below the new heading are intact.

B3. Source-cell incremental count
---------------------------------
COMMAND: grep -c '（来源：' README.md  (after edit)
OUTPUT:  9
RESULT:  delta = 9 - B0(0) = 9 >= 9  →  PASS

====================================================================
PART C — Content accuracy (each number/claim traced to a source file)
====================================================================

C1. Snapshot row 1 芯片/RTL 阶段 (source: README.md — RTL Phase 3 section)
    Verified text: "RTL Phase 3 SoC 集成完成（Ibex RV32IMC + AXI
    crossbar + APB + doorbell ring）" matches README 'RTL Phase 3 —
    SoC Integration' section + AGENTS.md STRUCTURE (Ibex RV32IMC + AXI
    crossbar + APB + doorbell ring). PASS

C2. Snapshot row 2 验证基线 (source: AGENTS.md)
    pytest 210 baseline (Quick Start 口径) + FM-SOC 33-case suite
    (`run_fm_soc_all.sh`) + counting-口径 warning → matches AGENTS.md
    COMMANDS ("PYTHONPATH=sim python -m pytest ... 210 baseline",
    "33-case FM-SOC regression") and NOTES test-count-discrepancy
    warning. PASS

C3. Snapshot row 3 Bug 台账 SoC RTL: 17 = 11 Fixed / 1 Pending /
    1 Accepted / 4 Open (source: docs/bugs/bugs-soc-rtl.md)
    Verified against bugs-soc-rtl.md:387 (Total: 17) and By-Status
    table (:411-425): Fixed 11, Pending (waiver 待用户签署) 1,
    Accepted (reconstruction-failure) 1, Open 4. PASS

C4. Snapshot row 4 module-level: 4 = 3 Fixed / 1 Open (WDT-001)
    (source: docs/bugs/bugs-module-level.md)
    Ledger refreshed by parallel todo-3 commit 332a7d7 → Stats table
    now Total 4 / Fixed 3 / Open 1 (WDT-001). PASS

C5. Snapshot row 5 Func Model: 全部 Fixed/Deferred、零 waiver
    (source: docs/bugs/bugs-soc-func-model.md)
    Verified: bugs-soc-func-model.md:205-207 → Open 0 / Fixed 8 /
    Deferred (info, non-blocking) 2; rows say "Deferred (zero waivers;
    no blocking open defect)". PASS

C6. Snapshot row 6 PCIe DMA: 4 UCOV uncovered gaps
    (source: docs/bugs/bugs-pcie-dma.md)
    Verified: UCOV-PCIE-001..004 all Status = Uncovered
    (bugs-pcie-dma.md:157-160). PASS

C7. Snapshot row 7 Blocker 全 8 项 #1..#8
    (source: docs/soc-rtl-review-remediation-blockers.md)
    All 8 blocker numbers present in the row:
    #1 perf-CI 17.4GB RSS 超限 (gating)
    #2 36 层连续 forward 定界 (defer FPGA)
    #3 FPGA L5 + ggml lifecycle (BLOCKED)
    #4 同 #2 defer FPGA
    #5 WVR-SOC-RTL-002 waiver 待用户签署
    #6 BUG-007 根因追查已关闭 (用户接受)
    #7 工作区状态清理 (open)
    #8 可重放 signoff manifest + 用户签收 (open)
COMMAND: grep -oE '#[1-8]' README.md | sort -u | wc -l
OUTPUT:  8
RESULT:  PASS — all 8 blocker items listed (no subset)

C8. Snapshot row 8 Defer: E2E-07 perf calibration → FPGA
    (source: AGENTS.md)
    Matches AGENTS.md NOTES "E2E-07 perf calibration deferred to
    FPGA" and blockers doc #2. PASS

C9. Snapshot row 9 live hazard: MXU SCALE_ADDR 跨流程状态泄漏
    (source: .omo/evidence/task-8-bug-007-root-cause.txt)
    Verified in task-8 evidence residual candidate #3 (lines 121-128):
    "Cross-engine / cross-flow stale-state leak (MXU SCALE_ADDR) —
    LIVE HAZARD for mixed firmware-then-per-op flows". PASS

====================================================================
PART D — Source-cell bare paths all exist (test -f)
====================================================================

COMMAND: for each of the 9 bare paths in the 9 来源 cells: test -f
LIST (9 cells):
  README.md                                          → PASS (exists)
  AGENTS.md                                          → PASS (exists)
  docs/bugs/bugs-soc-rtl.md                          → PASS (exists)
  docs/bugs/bugs-module-level.md                     → PASS (exists)
  docs/bugs/bugs-soc-func-model.md                   → PASS (exists)
  docs/bugs/bugs-pcie-dma.md                         → PASS (exists)
  docs/soc-rtl-review-remediation-blockers.md        → PASS (exists)
  AGENTS.md                                          → PASS (exists)
  .omo/evidence/task-8-bug-007-root-cause.txt        → PASS (exists)
RESULT:  PASS — 9/9 来源 cells contain only bare repo-relative paths,
         all on disk; no prose inside （来源：…） cells.

====================================================================
PART E — Key-file index table rows (one-sentence purpose each)
====================================================================
Rows added: docs/bugs/bugs-soc-rtl.md, docs/bugs/bugs-soc-func-model.md,
docs/bugs/bugs-module-level.md, docs/bugs/bugs-pcie-dma.md (the four
exact ledger filenames), docs/soc-rtl-review-remediation-blockers.md,
docs/waivers/, .omo/plans/, .omo/evidence/, .omo/notepads/,
build/evidence/ (gitignored, key files add -f), and
reports/CaduceusCore-review-report-2026-08-28.md. All 11 rows present.
RESULT:  PASS

====================================================================
PART F — README diff integrity
====================================================================
COMMAND: git diff --stat README.md
OUTPUT:  1 file changed, 36 insertions(+)
COMMAND: git diff README.md | grep -v '^---' | grep -c '^-'
OUTPUT:  0
RESULT:  PASS — purely additive; no existing README content modified
         outside the new section.

====================================================================
PART G — Staged-set assertion + commit (todo 4 pathspec)
====================================================================
See staged-set output below (asserted == exactly README.md +
.omo/evidence/task-4-evidence-integrity-and-readme-status.txt)
RESULT: PASS — staged count 2/2; both paths match; zero Must-NOT files
        staged. (git add + git commit each retried with 3-15s backoff
        on index.lock; both succeeded on attempt 1.)

COMMIT MESSAGE: docs(readme): add project status snapshot + key file index
COMMIT SHA: 3bc1301
COMMITTED FILES (git show --name-only --format= HEAD):
  README.md
  .omo/evidence/task-4-evidence-integrity-and-readme-status.txt
RESULT: PASS — commit contains exactly the two pathspec files; all 7
        Must-NOT parallel-session dirty files absent from the commit.

====================================================================
PART H — Post-commit git status --porcelain
====================================================================
COMMAND: git status --porcelain
OUTPUT:  (verbatim below)
RESULT:  7 Must-NOT parallel-session dirty files remain unstaged and
         uncommitted. Two extra dirty files are workflow bookkeeping
         produced by the parallel Wave-2 execution itself, NOT by this
         todo and NOT committed by it: .omo/plans/evidence-integrity-
         and-readme-status.md (start-work checkbox [x] flip on todo 0)
         and .omo/notepads/evidence-integrity-and-readme-status/
         learnings.md (todo 2/4 shared-notepad appends). Both are
         excluded from the todo-4 staging and left for the plan-level
         bookkeeping pass.

PORCELAIN (verbatim, post-commit 3bc1301):
 M .omo/evidence/task-0-signoff-v3-runner.txt
 M .omo/evidence/task-20-uncertainty-kpis.json
 M .omo/evidence/task-23-perf-spec-ci.txt
 M .omo/notepads/evidence-integrity-and-readme-status/learnings.md
 M .omo/notepads/fm-e2e-qwen-cv-software-stack/learnings.md
 M .omo/notepads/phase6-rtl-verification/learnings.md
 M .omo/plans/evidence-integrity-and-readme-status.md
 M build/evidence/fm-cv-chain.txt
 M build/evidence/w3-4-mobilenetv3-fm.txt

Must-NOT 7 present and untouched (unstaged/uncommitted):
  1. .omo/evidence/task-0-signoff-v3-runner.txt              ✓
  2. .omo/evidence/task-20-uncertainty-kpis.json             ✓
  3. .omo/evidence/task-23-perf-spec-ci.txt                  ✓
  4. .omo/notepads/fm-e2e-qwen-cv-software-stack/learnings.md ✓
  5. .omo/notepads/phase6-rtl-verification/learnings.md      ✓
  6. build/evidence/fm-cv-chain.txt                          ✓
  7. build/evidence/w3-4-mobilenetv3-fm.txt                  ✓

Workflow bookkeeping (NOT staged/committed by todo 4):
  - .omo/plans/evidence-integrity-and-readme-status.md (checkbox [x] flip)
  - .omo/notepads/evidence-integrity-and-readme-status/learnings.md
    (todo 2/4 shared-notepad append; my todo-4 entry appended per
     notepad convention, never overwritten)

OVERALL RESULT: PASS
