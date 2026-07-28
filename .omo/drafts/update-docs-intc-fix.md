# update-docs-intc-fix — Draft

## Intent Decision
- intent: CLEAR
- review_required: false

## Ground Truth (from exploration)

### Git State
- branch: main, tracking origin/main
- commit 72ccbf7 (INTC fix) is **unpushed** (1 commit ahead of origin)
- 8 modified files (evidence/notepads, not product code)
- Many untracked evidence/build files

### README.md
- NO changes needed — only has a navigation pointer to bug docs (line 32), no counts/status/enumeration

### bugs-soc-func-model.md
- Currently has BUG-SOC-FM-001 through -007
- Stats: Total=7, Fixed=7, Open=0, Major=5, Minor=2
- NEEDS: BUG-SOC-FM-008 entry + stats update (8/8/0/5/3)

### func-model-signoff-checklist.md
- Currently has F-FM-01 through F-FM-31
- Date: 2026-07-26, Scope: v2+v3+bug-fix (FM-004/005/006/007)+bridge-accum
- BUG-SOC-FM-008 / F-FM-32 NOT present
- NEEDS: F-FM-32 entry, update date→2026-07-27, add FM-008 to scope, add to bug fix table

## Changes Required

1. docs/func-model-signoff-checklist.md:
   - Title: v3 (with Bug Fix + Bridge-Accum Fix) → v3 (with Bug Fix + Bridge-Accum Fix + INTC Fix)
   - Date: 2026-07-26 → 2026-07-27
   - Scope: add "+ INTC KeyError fix (FM-008)"
   - Add F-FM-32 row to Status Summary table
   - Update Bug Fix section: add BUG-SOC-FM-008 row + update text

2. docs/bugs/bugs-soc-func-model.md:
   - Add BUG-SOC-FM-008 [Major] entry before Stats section
   - Update Stats: Total 7→8, Fixed 7→8, Major 5→6

3. Git: commit both doc changes + push (commit 72ccbf7 + doc commit)

## Approval Gate
status: approved
approved_at: 2026-07-27
pending action: user runs /start-work to begin execution
