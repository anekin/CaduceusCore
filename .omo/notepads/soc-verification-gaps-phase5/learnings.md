# Learnings — SoC Verification Gaps Phase 5

## 2026-07-06 — W5.1: Split bug tracking into per-phase files

- `docs/bugs/bugs.md` was a monolithic file mixing module-level (MXU), SoC Func Model (Golden Reference), and SoC RTL bugs.
- Per Lesson 11 in `docs/caduceus-verification-lessons.md`, bugs must be split by verification phase.
- Three files created:
  - `docs/bugs/bugs-module-level.md` — MXU/SFU/Vector module-level bugs (migrated BUG-MXU-WDT-001, BUG-MX-PERF-000, BUG-001)
  - `docs/bugs/bugs-soc-func-model.md` — SoC Func Model golden reference bugs (migrated BUG-SOC-FM-001/002/003, cross-ref BUG-001)
  - `docs/bugs/bugs-soc-rtl.md` — already existed with 6 real RTL bugs (BUG-RTL-SOC-001..006)
- Old `docs/bugs.md` archived to `docs/bugs/bugs-archive.md` with redirect header.
- Cross-references added between all three per-phase files.
- Future bugs should be appended to the appropriate phase file immediately (no batching).
