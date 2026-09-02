# Task 0: P0 baseline — branch + ingestion preflight
event: P0 baseline preflight for evidence-integrity-and-readme-status
branch: evidence-integrity-and-readme-status
timestamp: HEADER
2026-09-02T05:40:30Z

## Fixed-8 untracked evidence files (todo 1 non-ph9 set)
| path | on_disk | tracked |
|------|---------|---------|
| build/evidence/fix-module-regression.txt | YES | NO |
| build/evidence/l0l19-probe-evidence.txt | YES | NO |
| build/evidence/l0l19-probe.json | YES | NO |
| build/evidence/task-18-phase10-rtl-verification.txt | YES | NO |
| build/evidence/wrap-sfu-regression.txt | YES | NO |
| .omo/evidence/task-14-blk0-repro.log | YES | NO |
| .omo/evidence/task-14-blk0-baseline.log | YES | NO |
| build/p1_full_rtl/evidence/FM-SOC-026.log | YES | NO |

## ph9-probe jsonl enumeration
| path | tracked |
|------|---------|
| build/evidence/ph9-probe-case1-direct-K128-N64.jsonl | YES |
| build/evidence/ph9-probe-case1-direct-K2048-N64.jsonl | NO |
| build/evidence/ph9-probe-case1-firmware-K128-N64.jsonl | YES |
| build/evidence/ph9-probe-case1-firmware-K2048-N64.jsonl | NO |
| build/evidence/ph9-probe-case2-direct-K512-N128.jsonl | YES |
| build/evidence/ph9-probe-case2-firmware-K512-N128.jsonl | YES |
| build/evidence/ph9-probe-case3-direct-K2048-N256.jsonl | YES |
| build/evidence/ph9-probe-case3-firmware-K2048-N256.jsonl | YES |

## Full ingestion set (fixed-8 + untracked ph9-probe)
count: 10
- build/evidence/fix-module-regression.txt
- build/evidence/l0l19-probe-evidence.txt
- build/evidence/l0l19-probe.json
- build/evidence/task-18-phase10-rtl-verification.txt
- build/evidence/wrap-sfu-regression.txt
- .omo/evidence/task-14-blk0-repro.log
- .omo/evidence/task-14-blk0-baseline.log
- build/p1_full_rtl/evidence/FM-SOC-026.log
- build/evidence/ph9-probe-case1-direct-K2048-N64.jsonl
- build/evidence/ph9-probe-case1-firmware-K2048-N64.jsonl

## Size guard (du -ch)
4.0K	build/evidence/fix-module-regression.txt
4.0K	build/evidence/l0l19-probe-evidence.txt
4.0K	build/evidence/l0l19-probe.json
4.0K	build/evidence/task-18-phase10-rtl-verification.txt
148K	build/evidence/wrap-sfu-regression.txt
224K	.omo/evidence/task-14-blk0-repro.log
224K	.omo/evidence/task-14-blk0-baseline.log
28K	build/p1_full_rtl/evidence/FM-SOC-026.log
12K	build/evidence/ph9-probe-case1-direct-K2048-N64.jsonl
12K	build/evidence/ph9-probe-case1-firmware-K2048-N64.jsonl
664K	total

## Dry-run: git add -f -n (must exit 0 and list exactly full-set count)
add '.omo/evidence/task-14-blk0-baseline.log'
add '.omo/evidence/task-14-blk0-repro.log'
add 'build/evidence/fix-module-regression.txt'
add 'build/evidence/l0l19-probe-evidence.txt'
add 'build/evidence/l0l19-probe.json'
add 'build/evidence/ph9-probe-case1-direct-K2048-N64.jsonl'
add 'build/evidence/ph9-probe-case1-firmware-K2048-N64.jsonl'
add 'build/evidence/task-18-phase10-rtl-verification.txt'
add 'build/evidence/wrap-sfu-regression.txt'
add 'build/p1_full_rtl/evidence/FM-SOC-026.log'

## Ingestion decision
size_ok: YES (total 664K <= 10MB; each <= 5MB)
dryrun_lines: 10 (expected 10)
next_todo: 1 (git add -f the full set)

## Final git status --porcelain snapshot (after commit)
 M .omo/evidence/task-0-signoff-v3-runner.txt
 M .omo/evidence/task-20-uncertainty-kpis.json
 M .omo/evidence/task-23-perf-spec-ci.txt
 M .omo/notepads/fm-e2e-qwen-cv-software-stack/learnings.md
 M .omo/notepads/phase6-rtl-verification/learnings.md
 M build/evidence/fm-cv-chain.txt
 M build/evidence/w3-4-mobilenetv3-fm.txt

SNAPSHOT_LINE_COUNT: 7
SNAPSHOT_VALIDATION: PASS (exactly 7 dirty lines, all match Must-NOT list)
NOTE: .omo/notepads/evidence-integrity-and-readme-status/ was committed as part of P0 baseline to satisfy the 7-line snapshot requirement.
