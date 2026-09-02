# Task 1: C1 evidence ingestion — fixed 8 non-ph9 files + 2 untracked ph9-probe jsonl files
event: C1 evidence ingestion for evidence-integrity-and-readme-status
branch: evidence-integrity-and-readme-status
timestamp: HEADER
2026-09-02T05:40:30Z

## Full ingestion set (10 unique paths)
| path | bug references |
|------|----------------|
| build/evidence/fix-module-regression.txt | BUG-005 / WV-001 / WV-007 (docs/bugs/bugs-soc-rtl.md) |
| build/evidence/l0l19-probe-evidence.txt | BUG-008 (docs/bugs/bugs-soc-rtl.md) |
| build/evidence/l0l19-probe.json | BUG-008 (docs/bugs/bugs-soc-rtl.md) |
| build/evidence/task-18-phase10-rtl-verification.txt | WV-001 (docs/bugs/bugs-soc-rtl.md) |
| build/evidence/wrap-sfu-regression.txt | WV-001 (docs/bugs/bugs-soc-rtl.md) |
| .omo/evidence/task-14-blk0-repro.log | BUG-012 (docs/bugs/bugs-soc-rtl.md) |
| .omo/evidence/task-14-blk0-baseline.log | BUG-012 (docs/bugs/bugs-soc-rtl.md) |
| build/p1_full_rtl/evidence/FM-SOC-026.log | BUG-006 (docs/bugs/bugs-soc-rtl.md:263) |
| build/evidence/ph9-probe-case1-direct-K2048-N64.jsonl | BUG-MXU-P9-001 / BUG-MXU-P9-00B / bugs-soc-rtl.md |
| build/evidence/ph9-probe-case1-firmware-K2048-N64.jsonl | BUG-MXU-P9-001 / BUG-MXU-P9-00B / bugs-soc-rtl.md |

## Stale check (pre-staging)
All 10 paths exist on disk and are untracked before this todo's staging.
EXISTS: build/evidence/fix-module-regression.txt
EXISTS: build/evidence/l0l19-probe-evidence.txt
EXISTS: build/evidence/l0l19-probe.json
EXISTS: build/evidence/task-18-phase10-rtl-verification.txt
EXISTS: build/evidence/wrap-sfu-regression.txt
EXISTS: .omo/evidence/task-14-blk0-repro.log
EXISTS: .omo/evidence/task-14-blk0-baseline.log
EXISTS: build/p1_full_rtl/evidence/FM-SOC-026.log
EXISTS: build/evidence/ph9-probe-case1-direct-K2048-N64.jsonl
EXISTS: build/evidence/ph9-probe-case1-firmware-K2048-N64.jsonl
UNTRACKED: build/evidence/fix-module-regression.txt
UNTRACKED: build/evidence/l0l19-probe-evidence.txt
UNTRACKED: build/evidence/l0l19-probe.json
UNTRACKED: build/evidence/task-18-phase10-rtl-verification.txt
UNTRACKED: build/evidence/wrap-sfu-regression.txt
UNTRACKED: .omo/evidence/task-14-blk0-repro.log
UNTRACKED: .omo/evidence/task-14-blk0-baseline.log
UNTRACKED: build/p1_full_rtl/evidence/FM-SOC-026.log
UNTRACKED: build/evidence/ph9-probe-case1-direct-K2048-N64.jsonl
UNTRACKED: build/evidence/ph9-probe-case1-firmware-K2048-N64.jsonl

## Pre-staging git diff --cached --name-only
(no output)

## Post-commit verification

## Post-commit git status --porcelain
STATUS_LINE_COUNT: 12
 M .omo/evidence/task-0-signoff-v3-runner.txt
 M .omo/evidence/task-2-evidence-integrity-and-readme-status.txt
 M .omo/evidence/task-3-evidence-integrity-and-readme-status.txt
 M .omo/evidence/task-20-uncertainty-kpis.json
 M .omo/evidence/task-23-perf-spec-ci.txt
 M .omo/evidence/task-4-evidence-integrity-and-readme-status.txt
 M .omo/notepads/evidence-integrity-and-readme-status/learnings.md
 M .omo/notepads/fm-e2e-qwen-cv-software-stack/learnings.md
 M .omo/notepads/phase6-rtl-verification/learnings.md
 M .omo/plans/evidence-integrity-and-readme-status.md
 M build/evidence/fm-cv-chain.txt
 M build/evidence/w3-4-mobilenetv3-fm.txt

STATUS_ASSERTION: FAIL
Expected 7 dirty lines matching the Must-NOT list; observed 12.
Reason: parallel Wave-2 agents produced additional dirty files while todo 1 was executing:
- .omo/evidence/task-2-evidence-integrity-and-readme-status.txt (todo 2 evidence)
- .omo/evidence/task-3-evidence-integrity-and-readme-status.txt (todo 3 evidence)
- .omo/evidence/task-4-evidence-integrity-and-readme-status.txt (todo 4 evidence)
- .omo/notepads/evidence-integrity-and-readme-status/learnings.md (parallel notepad update)
Additionally, .omo/plans/evidence-integrity-and-readme-status.md remains dirty from the todo 0 checkbox update.
The 7 original Must-NOT files are present and were NOT staged in this commit.

## Verification summary
- git ls-files --error-unmatch <10 ingestion paths>: PASS (all exit 0)
- git show --stat HEAD -- <10 ingestion paths>: PASS (exactly 10 files)
- git log -1 --name-only: PASS (contains none of the 7 Must-NOT dirty files)
- git status --porcelain line count == 7: FAIL (12 lines due to parallel activity)
