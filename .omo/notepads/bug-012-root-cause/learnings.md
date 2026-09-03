## 2026-09-03T09:55:00Z - bug-012-root-cause final wave APPROVED

- H-GOLDEN refuted (verify_ops fresh PASS + byte-identical regen).
- H-STRIDE-STATIC supported; INTRODUCING-COMMIT 8dd5dbe.
- ISO-VERDICT repro-fail-identical; OVERLAP-HAZARD none.
- H-STRIDE-EMPIRICAL confirmed via 8KB backdoor layout; H-TRIGGER-DIM1 actual-N-sufficient; H-DIM1-CLAIM refuted.
- ATTRIBUTION: wrapper dim1_n priority x driver DIM1 padding; non-stale-golden, non-drain.
- BUG-012 ledger updated: Status Open, two fix directions (driver-side preferred, wrapper-side heavier), RED anchor test added.
- F1-F4 all APPROVED; plan marked complete and branch merged to main with --no-ff.
- 7 pre-existing dirty files untouched throughout; no push.
