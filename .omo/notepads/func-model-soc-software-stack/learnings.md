
# Learnings — Func Model / SoC RTL / FPGA Unified Software Stack

## Task 4: Shared scenario, observation, scoreboard, and DUT-adapter contract (2026-07-27)

### Design decisions

- **Transport independence**: The scenario schema uses abstract action types (`mmio_write`, `sram_preload`, `doorbell`, etc.) rather than cocotb signal names or Func Model objects. Concrete adapters translate these into transport-specific operations.
- **Operation classification**: Each action is classified as `frontdoor`, `allowed_init_backdoor`, `allowed_obs_backdoor`, or `diagnostic`. The `Scenario.validate()` method rejects actions with incorrect classifications (e.g., backdoor mislabeled as frontdoor) and diagnostic-only actions in signoff scenarios.
- **Deterministic serialization**: `to_dict()` produces deterministic key-ordered output. Fixed timestamps are needed for `content_hash()` stability — the `Provenance` default timestamp makes serialization non-deterministic unless overridden.
- **Scoreboard independence**: The `Scoreboard` class ONLY compares `expected_observations` from the scenario to `actual_observations` from the adapter. It never reads expected data from the DUT itself. This ensures the golden reference stays independent.
- **Migration path**: `migrate_testcase_config()` converts existing `TestCaseConfig` objects into the new `Scenario` format without modifying the original loader. This preserves backward compatibility for all existing FM-SOC `.npz` vectors.

### Implementation notes

- **Action auto-classification**: `Action.__post_init__` auto-classifies action types only when no explicit classification is given (default = `None`). Explicit classifications are preserved to allow test-specific overrides, but `Scenario.validate()` still catches misclassified actions.
- **FakeDUTAdapter**: Implements the full `DUTAdapter` contract in-memory. Accepts diagnostic actions only when `accept_diagnostics=True`. Unknown action types no-op when both diagnostic and accepted, otherwise raise `ValueError`.
- **Async pattern**: The `DUTAdapter` contract is declared async (for future cocotb/FPGA compatibility), but tests use `asyncio.run()` to drive it synchronously since `pytest-asyncio` is not a dependency.
- **Scoreboard comparison**: Handles INT32 bit-exact, FP16 tolerance-based, and generic data-dict comparisons. Missing expected observations and data mismatches are reported as structured failures.

### Files created

| File | Purpose |
|------|---------|
| `sim/verification/__init__.py` | Public API exports |
| `sim/verification/operation_classifier.py` | Operation classification enum + validation |
| `sim/verification/tolerance.py` | ToleranceConfig + Provenance models |
| `sim/verification/observation.py` | Typed Observation records with factories |
| `sim/verification/scenario.py` | Versioned Scenario + Action + EvidenceRecord |
| `sim/verification/dut_adapter.py` | DUTAdapter ABC + FakeDUTAdapter |
| `sim/verification/scoreboard.py` | Scoreboard comparison engine |
| `sim/verification/migration.py` | TestCaseConfig → Scenario migration |
| `sim/tests/test_verification_scenario.py` | 59 tests covering all modules |

### Test results

- `PYTHONPATH=sim python3 -m pytest sim/tests/test_verification_scenario.py -q -k 'roundtrip or fake_dut'` → **17 passed**
- `PYTHONPATH=sim python3 -m pytest sim/tests/test_verification_scenario.py -q -k 'rejects_malformed or rejects_forbidden_backdoor'` → **6 passed**
- Full suite: **59 passed**
- Existing `TestCaseConfig`/`RTLSoCRunner`/`load_golden_vectors` imports unaffected

### Known limitations

- The migration does not yet handle `sram_initial` / `dram_initial` legacy fields from older `.npz` files — these are present in the migration code but untested against real vectors.
- The `Provenance.created_at` auto-timestamp must be overridden for deterministic serialization tests.
- The async DUT adapter contract is not yet integrated with cocotb's event loop.
