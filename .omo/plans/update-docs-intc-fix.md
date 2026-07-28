# update-docs-intc-fix - Work Plan

## TL;DR (For humans)

**What you'll get:** Project doc updates reflecting the completed INTC KeyError fix (BUG-SOC-FM-008). `docs/func-model-signoff-checklist.md` gets a new F-FM-32 entry. `docs/bugs/bugs-soc-func-model.md` gets the bug entry + updated stats (8 bugs, all fixed). README.md is unchanged (only has a navigation pointer, no counts). Both doc changes are committed and pushed alongside the existing unpushed commit 72ccbf7.

**Why this approach:** The INTC fix was completed and verified in a prior plan session but the project docs (signoff checklist, bug tracker) still need to reflect the new bug and its fix. This is a documentation-only change — no product code is modified.

**What it will NOT do:** Not modify any product code (`.py`, `.v`, `.c`, `.cpp`). Not change README.md. Not touch RTL, firmware, Spike, or DSE engine files.

**Effort:** Trivial (1 todo)
**Risk:** None — documentation-only, no code changes

Your next move: approve to start execution.

---

> TL;DR (machine): Trivial effort, 1 todo updates 2 doc files with INTC KeyError fix entry + git push. No code changes.

## Scope

### Must have
- `docs/func-model-signoff-checklist.md` — add F-FM-32 entry, update date/scope, add BUG-SOC-FM-008 to bug fix table
- `docs/bugs/bugs-soc-func-model.md` — add BUG-SOC-FM-008 entry, update stats (Total 7→8, Fixed 7→8, Major 5→6)

### Must NOT have
- 不修改 README.md（仅有导航指针，无 bug 计数/状态）
- 不修改任何产品代码（`sim/`、`rtl/`、`firmware/`、`spike_src/`）
- 不修改其他 `.omo/` 文件（evidence/notepads 保持不变）
- 不修改 `build/` 目录

## Verification strategy
- Git diff: 仅 `docs/func-model-signoff-checklist.md` + `docs/bugs/bugs-soc-func-model.md`
- Semantic checker: `python3 scripts/check_func_model_signoff_docs.py` — PASS
- Bug tracker consistency: BUG-SOC-FM-008 appears in both checklist and bug tracker
- Stats consistency: `grep -c "Total bugs" docs/bugs/bugs-soc-func-model.md` → 8

## Execution strategy
### Parallel execution waves
> Wave 1 (1 todo): Todo 1 — update both docs + commit + push

## Todos
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [x] 1. Update signoff checklist + bug tracker to reflect INTC KeyError fix (BUG-SOC-FM-008) and git push

  What to do:
  - **Update `docs/func-model-signoff-checklist.md`**:
    - Line 1: Title — add "+ INTC Fix" suffix
    - Line 3: Date — `2026-07-26` → `2026-07-27`
    - Line 4: Scope — add "+ INTC KeyError fix (FM-008)"
    - After F-FM-31 (line 47): Add F-FM-32 row:

      | **F-FM-32** | INTC KeyError ACK-before-PENDING fix (`.get()` defense in `_handle_intc`) | ✅ Fixed (`72ccbf7`) | task1-intc-keyerror-fix.txt; 13/13 INTC tests PASS |
    - In Bug Fix Cycle table (after FM-005 row, ~line 115): Add BUG-SOC-FM-008 row:

      | BUG-SOC-FM-008 | P2 | ✅ Fixed | `_handle_intc` used `self._status[key] &= ~value` without `.get()` fallback; ACK-before-PENDING raised KeyError | One-line `.get(...,0)` defense at `sim/mmio_bridge.py:590`, matching `_set_irq()` safe pattern (commit `72ccbf7`) |
    - Update "Impact on Signoff" text (~line 128): Add INTC entry:
      "INTC KeyError: **Was KeyError crash on ACK-before-PENDING → Now handled gracefully**. 13/13 INTC tests PASS."
    - Update "Known Remaining Issues" INTC gap entry: mark as **RESOLVED** (was listed as Gap #9 IRQ-CHAIN)
    
  - **Update `docs/bugs/bugs-soc-func-model.md`**:
    - Update Stats: Total bugs `7` → `8`, Fixed `7` → `8`, Major `5` → `6`
    - After BUG-SOC-FM-007 entry (~line 440): Add BUG-SOC-FM-008 entry (use standard format):
      ```
      ### 2026-07-27 [Major] INTC PENDING KeyError on ACK-before-PENDING (BUG-SOC-FM-008)

      **Case**: Spike forward pass — MMIO server logs `KeyError: 1073766400`
      **Status**: Fixed

      #### Description
      `_handle_intc` at `sim/mmio_bridge.py:590` used `self._status[INTC.BASE + INTC.PENDING] &= ~value`, 
      which raises `KeyError` when INTC.ACK is written before any INTC.PENDING write has populated the key.

      #### Root Cause
      `_handle_intc` assumed PENDING register was pre-initialized via a prior write. 
      When Spike firmware issues ACK before PENDING register has been touched, `self._status` dict has no key 
      (starts empty per `__post_init__`), causing `KeyError` on the `&=` operation.

      #### Fix Commit
      `72ccbf7` — Changed `self._status[key] &= ~value` to `self._status[key] = self._status.get(key, 0) & ~value`, 
      matching the safe `.get(..., 0)` pattern already used in `_set_irq()` (lines 625-626).

      #### Evidence
      - `test_intc_keyerror_fix.py`: 4 new regression tests (ACK-before-PENDING no-crash, set-then-ACK, multi-ACK, normal-flow)
      - `test_func_model_signoff_v3_intc.py`: 9/9 PASS (no regression)
      - Total: 13/13 INTC tests PASS
      - `.omo/evidence/task1-intc-keyerror-fix.txt`: traceback, diff, test output
      ```

  - **Git commit + push**:
    - Stage: `docs/func-model-signoff-checklist.md docs/bugs/bugs-soc-func-model.md`
    - Commit message: `docs(signoff): add F-FM-32 INTC KeyError fix + BUG-SOC-FM-008 entry`
    - Push: `git push origin main` (includes both this commit + unpushed commit 72ccbf7)

  Must NOT do: 不修改 README.md、不修改任何产品代码、不修改 build/、不修改其他 .omo/ 文件

  Parallelization: Wave 1 | Blocked by: none | Blocks: —

  References:
  - `docs/func-model-signoff-checklist.md` — full file (229 lines), target for F-FM-32 + BUG-SOC-FM-008 additions
  - `docs/bugs/bugs-soc-func-model.md` — full file (440 lines), target for BUG-SOC-FM-008 entry + stats update
  - `.omo/evidence/task1-intc-keyerror-fix.txt` — evidence for the INTC fix (traceback, diff, test output)
  - `.omo/notepads/func-model-remaining-fixes/learnings.md` — learnings from the fix plan
  - `sim/mmio_bridge.py:590` — the fixed line
  - `sim/tests/test_intc_keyerror_fix.py` — the new regression test file
  - Commit `72ccbf7` — the fix commit (unpushed, on main branch)

  Acceptance criteria:
  - `docs/func-model-signoff-checklist.md` contains F-FM-32 row with status ✅ Fixed
  - `docs/func-model-signoff-checklist.md` contains BUG-SOC-FM-008 in bug fix table
  - `docs/bugs/bugs-soc-func-model.md` contains BUG-SOC-FM-008 entry with standard format
  - `docs/bugs/bugs-soc-func-model.md` Stats: Total=8, Fixed=8, Major=6
  - `git diff --stat HEAD~1..HEAD` shows only the two doc files changed
  - `git log --oneline origin/main..main` is empty after push (all commits pushed)

  QA scenarios:
  - Happy: `grep -c "F-FM-32" docs/func-model-signoff-checklist.md` → 1
  - Happy: `grep -c "BUG-SOC-FM-008" docs/bugs/bugs-soc-func-model.md` → ≥2 (header + stats reference)
  - Failure: `grep -c "Total bugs.*7" docs/bugs/bugs-soc-func-model.md` → 0 (must be 8)
  - Evidence: `.omo/evidence/update-docs-intc-fix.txt`

  Commit: Y | `docs(signoff): add F-FM-32 INTC KeyError fix + BUG-SOC-FM-008 entry`

## Commit strategy

| Task | Commit | Message |
|------|--------|---------|
| 1 | Y | `docs(signoff): add F-FM-32 INTC KeyError fix + BUG-SOC-FM-008 entry` |

## Success criteria

- [x] `docs/func-model-signoff-checklist.md` — F-FM-32 条目存在，状态 ✅ Fixed，日期更新为 2026-07-27
- [x] `docs/bugs/bugs-soc-func-model.md` — BUG-SOC-FM-008 条目存在，统计: Total=8, Fixed=8, Major=6
- [x] Git push 成功 — 72ccbf7 + doc commit 均已推送，`git log origin/main..main` 为空
