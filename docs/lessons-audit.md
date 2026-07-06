# 14-Lesson Checklist Status Audit

> **Version**: v1 | **Date**: 2026-07-06
> **Plan**: soc-verification-gaps-phase5
> **Purpose**: Map each verification lesson to coverage in this plan. At least 10/14 must be ✅ or ⚠️.

| Lesson ID | Title | Covered? | Evidence / Plan Reference | Notes |
|:---------:|-------|:--------:|---------------------------|-------|
| L01 | Golden Model 是唯一真相来源 | ✅ | Func Model bit-exact golden reference per §Verification strategy / §Golden Reference 约定; `.npz` vectors sole PASS/FAIL benchmark | Golden already validated (FM-SOC 33/33, SFU 319/319, Vector 63/63). Plan reuses same mechanism. |
| L02 | 测试数据 build 代码只写一套，两边复用 | ✅ | §Testbench 复用: "Func Model 阶段的 testbench 与 RTL 阶段使用同一套代码"; §Testcase 共用: `.npz` vectors shared between FM and RTL | Plan enforces single build path via `.npz` generation. No separate `_build_*()` for RTL. |
| L03 | 模块测试至少 1 case 走真实数据通路 | ⚠️ | SoC-level tests (W1 multi-layer, W3 CV) cover real pack→SRAM→wrapper→engine path implicitly | Module-level (MXU/SFU/Vector) tests use `$readmemh` direct port access. SoC E2E validates the full path, but no explicit module-level case forces the pack→SRAM→wrapper path. |
| L04 | 性能测试覆盖到 wrapper 集成层，不是裸引擎 | ✅ | Wave 4 (SoC-Level Performance): PERF-01 measures wrapper overhead (≤ 5× MX-P01 module cycles); PERF-02 DMA overlap; PERF-03 NoC latency | Wrapper AXI4 sequencer costs explicitly measured in SoC context. |
| L05 | Func Model 阶段就用真实 CPU + 真实固件 binary | ✅ | §环境就绪 Gate 0.1: Spike + `make -C firmware` verified; W5 T31 (Descriptor alignment): Spike + real `npu_firmware.elf` | `miniv.py` limitations documented and superseded by Spike verification. |
| L06 | 每次 op 结果独立保存比对，不只比最后一次 | ✅ | W1 T5: "Multi-op back-to-back intermediate result comparison"; anti-vacuous test for accumulator non-clear | Per-op snapshots saved for 17-op blk.0 chain. |
| L07 | E2E case 做双路比对 — backdoor read + interface read | ✅ | W3 T17/T17: Func Model + RTL dual-path comparison (bk_match + pcie_match); anti-vacuous for corrupt PCIe routing | Explicitly implements the diagnostic matrix from Lesson 7. |
| L08 | 每个 wave 结束设 Review Gate，审计证据后放行 | ✅ | Every wave ends with Atlas final-review (T6, T15, T20, T26, T33); W5 T32 standardizes checklist | Atlas agent invoked per-plan; fallback to manual Oracle review documented. |
| L09 | SRAM 峰值在架构阶段就用真实模型跑 | ⚠️ | T28 (issues_found.md) lists SRAM peak as known concern; Arc Model Phase 0 already computed Qwen2.5-3B > 4MB tile streaming | Not actively re-validated in this plan. Architecture-stage analysis exists from earlier phases. |
| L10 | 所有编译/运行环境差异在 Phase 3 前解决 | ✅ | §环境约束: All execution on sz0001 EDA server; §环境就绪 Gate 0.1 verifies VCS, Python, cocotb, Spike on server | No cross-machine compilation. GLIBC/plugin environment issues pre-addressed. |
| L11 | 按验证阶段拆分 bug 文件，发现即 commit | ✅ | W5 T27: "Split bug tracking by verification phase" — creates 3 files: bugs-module-level.md, bugs-soc-func-model.md, bugs-soc-rtl.md | Each bug committed per-fix; no batch recording. |
| L12 | Testbench 脚本在进 VCS 之前 dry-run 一遍 | ✅ | §dry-run 机制: "所有 _build_*() / descriptor / MMIO 配置逻辑在 sz0001 上用纯 Python 先跑通" | 5-minute pre-check prevents hour-scale VCS failures. |
| L13 | 增量替换 RTL，从风险最高的模块开始 | ⚠️ | Phase 3 integration already completed; plan works with full SoC. Verification order (Func Model → SoC testbench → Bug report → Fix → Regress) follows same incremental discipline | Principle was applied during Phase 3 RTL rollout (PCIe wrapper first). This plan validates integrated SoC, not incremental swap. |
| L14 | 已知未覆盖点显式记录，不盲目信任覆盖率 | ✅ | W5 T28: "Create and maintain issues_found.md with known uncovered points"; updated per-Wave with CV gaps, ISA gaps, PCIe limitations | Explicit sections for each uncovered area. |

## Summary

| Metric | Count |
|--------|:-----:|
| ✅ Fully covered | 11 |
| ⚠️ Partially covered | 3 |
| ❌ Not covered | 0 |
| **Total ≥ ✅/⚠️** | **14 / 14 (100%)** |

All 14 lessons are addressed. Lessons L03, L09, and L13 are marked ⚠️ — they are partially covered by SoC-level testing, prior-phase analysis, or existing architecture decisions rather than direct plan tasks. No lesson is left uncovered.

## Coverage Detail

**Fully covered (11):** Golden Model (L01), shared build code (L02), wrapper-level perf (L04), real CPU+firmware (L05), per-op intermediate comparison (L06), dual-path E2E compare (L07), Review Gates (L08), environment resolution (L10), bug file splitting (L11), testbench dry-run (L12), known gap documentation (L14).

**Partially covered (3):**
- L03: Module-level tests do not explicitly mandate the pack→SRAM→wrapper path. SoC E2E tests (W1, W3) cover this at system level but the lesson's target is module-level testbenches.
- L09: SRAM peak validation was done in earlier architecture phases. The plan documents remaining concerns in issues_found.md but does not re-run the analysis.
- L13: Incremental RTL replacement was applied in Phase 3. This plan validates the fully integrated SoC, following incremental discipline in its verification order rather than RTL swap order.
