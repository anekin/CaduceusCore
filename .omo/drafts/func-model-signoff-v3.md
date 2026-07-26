# func-model-signoff-v3 — Planning Draft

## Intent
CLEAR — 创建 SoC 全栈功能签收计划。基于 func-model-signoff-v2 未覆盖的 10 个 SoC 差距。

## Scope
- Spike+firmware 4 modes functional verification
- PCIe DMA DmaEngine model verification
- Crossbar M=6/S=2 concurrent multi-master verification
- Doorbell ring buffer protocol verification
- INTC interrupt delivery chain verification
- Host CPU communication (PCIe TLP + doorbell + roundtrip)
- Full SoC integration chain

## Gate
status: plan-written
plan file: `.omo/plans/func-model-signoff-v3.md` (~300 lines, 8 tasks + F1-F4)

## Relationship to other plans
- **Prerequisite for**: `func-model-perf-signoff` — the Spike+firmware path (T0B/T8/T11) depends on v3-verified functional baseline; PCIe DMA perf model needs v3-verified DmaEngine correctness
- **Builds on**: `func-model-signoff-v2` — does NOT repeat v2's operator-level verification
- **Complements**: `rtl/testcase-list-soc-fm.md` — FM-SOC RTL tests are the RTL-level equivalent; v3 covers the Func Model Python level
