# fm-hardening-phase10 decisions

## [2026-08-23] Execution decisions
- Execute Wave 1 todos in dependency order: 1 & 3 parallel → 2 → 4 & 5 parallel.
- No RTL changes; all work confined to sim/, firmware constants, scripts, docs.
- Tests-after strategy: new assertions assume current correct behavior.
