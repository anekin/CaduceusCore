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
