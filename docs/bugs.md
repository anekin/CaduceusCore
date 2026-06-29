# Bug Tracking — Func Model Verification

This document tracks bugs found during Func Model verification. Each code bug receives a detailed report under `bugs/BUG-XXX.md`. Test quality issues are listed here in summary form without individual reports.

> **Historical issues**: See [`docs/issues_found.md`](issues_found.md) for pre-verification issues found during earlier development phases.

## Code Bugs

| ID | Title | Module | Severity | Discovered | Status | Fix Commit |
|----|-------|--------|----------|------------|--------|------------|
| [BUG-001](bugs/BUG-001.md) | exp LUT default entries=256 causes interpolation error > 1e-5 | `GoldenSFU._build_exp_lut` | Medium | SF-02 | FIXED | `295d6b9` |
| [BUG-MXU-WDT-001](bugs/BUG-MXU-WDT-001.md) | Controller Watchdog Timer Missing | `controller.v` | Medium | MX-10 | Open | |

## Test Quality Issues

| ID | Description | Fix Commit |
|----|-------------|------------|
| TQ-01 | V-09 introduced scipy dependency (violated pure-numpy requirement) | `ce775b3` |
| TQ-02 | XL-03 tolerance 1e-4 vs testplan requirement 1e-3 mismatch | `9243679` |
| TQ-03 | DM-01 dead code: `randint` upper bound exclusive (off-by-one, no behavioral impact) | `b2a5c7b` |
| TQ-04 | SF-02 test verified interpolation instead of LUT entries (wrong test target) | `5b69e2c` |

## Statistics

- **Code bugs found**: 2
- **Test quality issues found**: 4
- **Code bugs fixed**: 1
- **Code bugs open**: 1
