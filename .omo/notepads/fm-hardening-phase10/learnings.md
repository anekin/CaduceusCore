# fm-hardening-phase10 learnings

## [2026-08-23] Start of work
- Plan approved and pushed to origin/main. Starting execution.
- Wave 1 dependency: todo 1 and todo 3 can run in parallel; todo 2 depends on 1; todos 4/5 depend on 2+3.

## [2026-08-23] Todo 1 done — sim/address_space.py contract module
- Created `sim/address_space.py` + 14 pytest cases in `sim/tests/test_address_space.py`; acceptance + QA scenarios all pass.
- Design decisions:
  - `REGIONS` = 5 named `(base, size)` half-open tuples: command_ring [0x80000000, 0x80008000), completion_ring [0x80008000, 0x80010000), descriptor_pool [0x80010000, 0x80020000), activation [0x80020000, 0x801E0000), weight [0x801E0000, 0x80800000). The C1 summary mentions an "output" region, but the detailed todo spec lists only these 5 and spike_host has no separate output-region constant (outputs live in the FP/activation arena) — no output region was invented.
  - `regions_overlap(a, b)` accepts a REGIONS key or a raw `(base, size)` tuple (todo 3's scoped per-runner checks reuse it); touching boundaries are NOT overlap.
  - `contract_check(ring_entries=1024, desc_base=None, desc_count=0, act_base=None)`: desc_base=None resolves to DESC_BASE; act_base=None SKIPS assertion (b) per todo 1 spec. TODO 2 NOTE: to enforce the default P10_ACT_BASE bound, pass `act_base=P10_ACT_BASE` explicitly — todo 2's "default parameters assert (b)" wording needs that explicit arg.
  - contract_check order: window checks (WindowError) first, then (a) OverlapError, then (b) OverlapError.
  - `addr_in_window` requires addr < DRAM_END (window end exclusive): zero-size probe at 0x80800000 is out of window.
- Discrepancies found (recorded, not silently resolved):
  - Todo 1 ("act_base=None skips assertion (b)") vs todo 2 ("default parameters = spike_host constants, (b) asserted against 0x80020000") are mutually inconsistent; followed todo 1 and documented the resolution in the module docstring.
  - Plan's completion-end formula uses `ring_entries*32` for the completion ring; module uses COMPLETION_ENTRY_SIZE (=32, npu_abi.json:1583) — same value.
- Tests pin constants against external truth sources only (`sim/spike_host.py:44,66,67,347-352`, `spec/npu_abi.json:1579-1582`); no magic literals elsewhere.
