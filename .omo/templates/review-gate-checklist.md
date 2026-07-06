# Per-Wave Review Gate Checklist

## Purpose

This checklist is executed at the end of every Wave to confirm that the Wave's
deliverables are consistent, verifiable, and ready to merge or to advance to the
next Wave. The Final Verification Wave additionally requires an Atlas approval
verdict before sign-off.

## When to use

- End of every Wave (W5.x, W6.x, etc.).
- Before any merge to `main` or release branch.
- During the Final Verification Wave, after all other gates pass.

## Roles

- **Wave Owner**: executes the checklist and collects evidence.
- **Atlas** (Final Wave only): independent design/verification review agent.
- **Oracle**: fallback reviewer when Atlas is unavailable.

---

## Gate 1 — SUMMARY Consistency Check

Verify that every work product produced in the Wave points to the same outcome.

| # | Check Item | PASS Criteria | FAIL Criteria |
|---|------------|---------------|---------------|
| 1.1 | Wave plan `SUMMARY`/`Outcome` matches committed changes | The commit diff directly implements the stated outcome | Commits implement unrelated work or outcome is vague |
| 1.2 | Notepad `learnings.md` references the correct Wave and task ID | Entry is dated and tagged with the Wave/task identifier | Entry is undated, mis-tagged, or missing |
| 1.3 | Evidence files are placed under `build/evidence/` with Wave/task prefix | At least one evidence file exists and is named `*W<wave>-<task>*` or contains the Wave/task in its header | Evidence is scattered, missing, or unfindable |
| 1.4 | Test/status logs match the claimed result | PASS/FAIL counts and logs support the outcome statement | Claimed PASS but logs show failures or no logs |

**Verdict**: PASS only if all items are PASS.

---

## Gate 2 — FAIL → Bug Mapping

Every FAIL or regression must be traceable to a tracked bug or a waived gap.

| # | Check Item | PASS Criteria | FAIL Criteria |
|---|------------|---------------|---------------|
| 2.1 | Each FAIL has a linked issue/bug ID | FAIL is recorded in `issues.md`, project tracker, or commit message with reference | FAIL is undocumented or only mentioned orally |
| 2.2 | Bug severity and owner are assigned | Severity (`blocker`/`critical`/`major`/`minor`) and owner are recorded | Severity/owner missing |
| 2.3 | Blocker/critical bugs have a fix-or-waive decision | Decision recorded before gate closes; waiver approved if applicable | Gate closed with unresolved blocker/critical bug |
| 2.4 | Deferred bugs are carried to known gaps | Bug copied to `known_gaps` section of this checklist or notepad | Bug disappears without resolution or follow-up |

**Verdict**: PASS only if all FAILs are mapped; blocker/critical bugs must be resolved or waived.

---

## Gate 3 — Anti-Vacuous Verification

Confirm that passing results actually exercise the intended functionality and
are not artifacts of disabled checks, empty tests, or trivial inputs.

| # | Check Item | PASS Criteria | FAIL Criteria |
|---|------------|---------------|---------------|
| 3.1 | Tests cover the stated requirement, not only happy path | At least one negative/stress/corner-case test exists and passes | Only trivial or happy-path tests run |
| 3.2 | Assertions/checks are active and meaningful | No `skip`, `xfail`, or commented assertions mask the real check | Checks disabled or marked expected-fail without waiver |
| 3.3 | Coverage or trace evidence shows the target code path executed | Coverage report, waveform signal trace, or log proves the path ran | No evidence the target path was exercised |
| 3.4 | Golden reference or baseline is unchanged unless justified | Baseline diff reviewed and approved; no silent baseline drift | Baseline changed without documented reason |

**Verdict**: PASS only if verification is demonstrated to be non-vacuous.

---

## Gate 4 — Regression Baseline Check

Ensure the Wave does not regress previously passing behavior.

| # | Check Item | PASS Criteria | FAIL Criteria |
|---|------------|---------------|---------------|
| 4.1 | Previous Wave regression suite passes | All previously passing tests still pass | Any previously passing test now fails |
| 4.2 | New tests are added to the regression manifest | New test cases appear in the CI/regression script or Makefile | New tests exist but are not run in regression |
| 4.3 | Performance/coverage metrics are within baseline tolerance | Metrics within ±5% or documented threshold | Regression metric degraded beyond threshold |
| 4.4 | Regression evidence is archived under `build/evidence/` | Log or report saved with timestamp | No regression log preserved |

**Verdict**: PASS only if regression suite passes and evidence is archived.

---

## Gate 5 — Known Gaps Update

Document what is intentionally left open and who owns the follow-up.

| # | Check Item | PASS Criteria | FAIL Criteria |
|---|------------|---------------|---------------|
| 5.1 | All unresolved gaps are listed in `issues.md` or notepad | Each gap has one-line description and owner | Gaps only discussed in chat/meeting |
| 5.2 | Each gap has a target Wave or decision date | Follow-up Wave or date is assigned | Gap has no planned resolution date |
| 5.3 | Wave sign-off acknowledges known gaps | Reviewer explicitly accepts the gap list | Gaps hidden or deferred without acknowledgement |
| 5.4 | Closed gaps are marked with resolution evidence | Closed gaps reference commit, test, or evidence file | Gaps marked closed without evidence |

**Verdict**: PASS only if the known-gaps list is current and acknowledged.

---

## Final Verification Wave — Atlas Approval

For the final Wave of a phase, Atlas must provide an independent verdict in
addition to the five gates above.

### Invoking Atlas

Atlas is invoked as an OMO background agent:

```typescript
task(
  subagent_type = "atlas",
  prompt = "Review the phase deliverables against the Review Gate checklist at .omo/templates/review-gate-checklist.md. Focus on cross-gate consistency, risk items, and whether known gaps are acceptable. Return APPROVE, CONDITIONAL, or REJECT with rationale.",
  run_in_background = true
)
```

Equivalent inline call from within an OMO-enabled OpenCode session:

```typescript
task(subagent_type="atlas", prompt="...", run_in_background=true)
```

### Accepted Atlas Verdicts

| Verdict | Meaning | Action |
|---------|---------|--------|
| `APPROVE` | All gates satisfied; known gaps acceptable | Proceed with merge/sign-off |
| `CONDITIONAL` | Minor concerns; gaps acceptable with documented follow-up | Proceed only if all conditions are recorded in `known gaps` |
| `REJECT` | Material inconsistency, missing evidence, or unacceptable risk | Fix and re-run Atlas review |

A Final Wave sign-off is valid only with Atlas `APPROVE` or `CONDITIONAL`.

---

## Oracle Fallback Path

If OMO is unavailable (< 4.14) or Atlas cannot be invoked, use the Oracle agent
as the fallback reviewer.

### Trigger conditions

- `opencode plugin list` does not show `oh-my-openagent` at version `>= 4.14`.
- `task(subagent_type="atlas", ...)` returns an error or times out.
- Atlas agent is not defined in the active `oh-my-openagent.json`.

### Oracle invocation

```typescript
task(
  subagent_type = "oracle",
  prompt = "Act as the Review Gate reviewer. Read .omo/templates/review-gate-checklist.md and the current Wave evidence under build/evidence/. Return APPROVE, CONDITIONAL, or REJECT with rationale, and explicitly call out any known gaps.",
  run_in_background = true
)
```

### Oracle verdict handling

- Oracle verdicts use the same `APPROVE` / `CONDITIONAL` / `REJECT` vocabulary as Atlas.
- Record the fallback reason in `learnings.md` and in the evidence file.
- A Final Wave sign-off with Oracle fallback must be noted in the commit message
  with `[Atlas-fallback: Oracle]`.

---

## Evidence Archive

After completing this checklist, place a dated evidence snippet in
`build/evidence/review-gate-<wave>-<task>.txt` containing:

1. Wave/task identifier.
2. Result of each gate (`PASS`/`FAIL`).
3. Atlas/Oracle verdict (Final Wave) or note if not applicable.
4. List of known gaps and owners.
5. Signature of the Wave Owner.

---

## Version

- Checklist version: 1.0
- Established: W5.6
- Owner: SoC Verification Phase 5
