# Learnings — soc-verification-gaps-phase5

## 2026-07-06 — W5.3: 14-lesson checklist status audit

**What**: Performed a full coverage audit mapping each of the 14 verification lessons from `docs/caduceus-verification-lessons.md` to plan tasks and mechanisms in the Phase 5 plan.

**Result**: docs/lessons-audit.md created. 11/14 fully covered (✅), 3/14 partially covered (⚠️), 0 uncovered (❌).

**Gaps noted**:
- L03 (module-level real data path): SoC E2E covers this at system level but no module-level testbench explicitly forces the pack→SRAM→wrapper path. Consider adding a dedicated module-level case in a future phase.
- L09 (SRAM peak with real models): Already analyzed in Arc Model Phase 0. Not re-validated in this plan. Documented in issues_found.md.
- L13 (incremental RTL replacement): Applied during Phase 3 integration. This plan validates the fully integrated SoC; follow the same incremental discipline in verification ordering.

**Key decision**: Lessons were mapped at the plan-mechanism level, not per-plan-todo. A lesson counts as ✅ if the plan explicitly addresses its core concern through infrastructure, process requirements, or specific tasks. ⚠️ means the concern is partially addressed or inherited from prior phases.
