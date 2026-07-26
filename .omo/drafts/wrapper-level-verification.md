---
slug: wrapper-level-verification
status: review-complete
intent: clear
pending-action: none (plan written, review passed)
approach: Build 3 cocotb+VCS wrapper-level testbenches (tb_sfu_wrapper.v, tb_vector_wrapper.v, tb_mxu_wrapper.v) that independently drive each wrapper's APB slave via cocotbext-axi ApbMaster and connect AXI4 master to AxiRam with sparse memory for X-propagation testing. Verify APB→MMIO path, AXI4 burst geometry, width converter correctness, non-aligned padding edge cases (BUG-005), and multi-op chain dispatch timing (BUG-007). Add Makefile targets for CI regression.
---

# Draft: wrapper-level-verification

## Components (topology ledger)
<!-- Lock the SHAPE before depth. One row per top-level component that can succeed or fail independently. -->
| id | outcome (one line) | status | evidence path |
|----|-------------------|--------|---------------|
| C1 | Wrapper TB infrastructure: 3 Verilog testbench skeletons + 1 Python common lib + VCS flist | active | `rtl/tb/tb_*_wrapper.v`, `sim/tests/wrapper/` |
| C2 | SFU wrapper functional tests: APB regmap, normal burst, 32↔512 width converter | active | `build/evidence/wrap-sfu-*.txt` |
| C3 | Vector wrapper functional tests: APB regmap, 8-beat chunk burst, 4096↔512 adapter | active | `build/evidence/wrap-vec-*.txt` |
| C4 | MXU wrapper functional tests: APB regmap, preload/store-out, broadcast+accumulate | active | `build/evidence/wrap-mxu-*.txt` |
| C5 | BUG-005 directed tests: non-aligned burst padding + X-propagation from uninitialized memory | active | `build/evidence/wrap-bug005-*.txt` |
| C6 | BUG-007 directed test: multi-op chain dispatch (consecutive START without full idle) | active | `build/evidence/wrap-bug007-*.txt` |
| C7 | Regression integration: Makefile targets + evidence + CI hook | active | `sim/regression/Makefile` |

## Open assumptions (announced defaults)
| assumption | adopted default | rationale | reversible? |
|------------|----------------|-----------|-------------|
| APB driver choice | cocotbext-axi `ApbMaster` (not custom `SimpleAPBMaster`) | Consistent with existing `AxiMaster` from same library; `ApbBus.from_prefix(dut, "apb")` pattern | Yes |
| AXI slave model | cocotbext-axi `AxiRam` with sparse memory | Supports backdoor init + uninitialized regions for X-propagation | Yes |
| Scope: all 3 wrappers | SFU + Vector + MXU all covered | All three have wrapper logic never independently verified | Yes |
| Test depth | Functional + edge-case + bug-repro, not just smoke | "独立验证" implies comprehensive coverage | Yes |
| Execution environment | sz0001 via SSH (VCS), scripts local | Same convention as Phase 9 plan | Yes |
| Bug fix scope | Verify only, do NOT fix bugs in this plan | User said "验证计划" not "修复计划"; fixes would be separate plan | Yes |
| Module-level bug BUG-MXU-WDT-001 | Out of scope (controller feature, not wrapper) | Watchdog timer is inside mxu_top, not wrapper | Yes |

## Findings (cited - path:lines)

### Wrapper interfaces (from explorer subagent)
- All 3 wrappers export identical external bus: 2 clk/rst + 8 APB slave (12-bit addr, 32-bit data) + 29 AXI4 master (512-bit) + 1 irq
- `rtl/wrapper/sfu_soc_wrapper.v`: 32↔512-bit width converter, double-buffered line buffer, 8192-entry write FIFO, 2 FSMs (rd_state 3 states, wr_state 3 states)
- `rtl/wrapper/vector_soc_wrapper.v`: 4096↔512-bit via 8-beat AXI bursts, register-file buffer arrays (buf_a/buf_o, 4096-bit, CHUNKS_MAX=128), 1 sequencer FSM (8 states)
- `rtl/wrapper/mxu_soc_wrapper.v`: 512→broadcast (256/512-bit) + 2048→512 store-out, 2 FSMs (pl_state 6 states, so_state 3 states), 8 debug ports
- Cross-verified against `rtl/sfu/README.md` and `rtl/vector/README.md` — port counts match

### Existing cocotb patterns (from explorer subagent)
- `sim/cocotb_bridge.py:68`: imports `AxiBus, AxiMaster, AxiRam` from cocotbext-axi
- `sim/cocotb_bridge.py:452-457`: `AxiMaster(AxiBus.from_prefix(dut, "s_axi"), clk, rst_n, reset_active_level=False)` for SRAM init
- `sim/rtl_soc_runner.py:81,1064-1066`: `AxiMaster(AxiBus.from_prefix(dut, "cpu_m_axi"), clk, rst_n)` for data-space AXI
- `ApbMaster` from cocotbext-axi is **NOT** used anywhere — project has custom `SimpleAPBMaster` in `sim/spike_rtl_bridge.py:49-105`
- `AxiRam` imported but **not instantiated** — available but unused
- SRAM init prefers VPI backdoor (`_sram_backdoor_write`) over AXI for speed
- `sim/regression/Makefile`: compile→run→grep PASS pattern, 25+ cocotb targets

### Bug root causes (from explorer subagent)
- **BUG-005**: Vector wrapper over-reads when element count ≠ multiple of 128 (512 bytes). RTL fix (wstrb masking in `vector_soc_wrapper.v:446-474`) is already applied but NEVER independently verified. SFU wrapper may have same issue (32-bit line buffer prefetch reads 64-byte cache lines regardless of actual DIM). Workaround (SRAM scratch buffers) broke at 51-op scale.
- **BUG-007**: `attn_weight` op (position 7 in 17-op chain) reports cycles=0 in 3-layer forward — op never executes. Likely firmware ring buffer overflow (32-entry ring, 51 commands) or address allocation overlap, not necessarily RTL wrapper bug. But wrapper-level TB can isolate wrapper-side START gating from firmware dispatch.

### cocotbext-axi capabilities (from Context7)
- `AxiMaster.read(addr, length)`: supports arbitrary length, auto-burst-split at 4KB
- `AxiRam.write(addr, data)`: backdoor direct write (no bus transaction); sparse memory, unwritten regions return X/Z
- `ApbMaster(ApbBus.from_prefix(dut, prefix), clk, rst)`: APB master with from_prefix
- `AxiRam` supports `size` parameter for large address spaces (up to 2^N bytes)

## Decisions (with rationale)
1. **Use cocotbext-axi ApbMaster** (not custom SimpleAPBMaster): consistency with existing AxiMaster usage, less custom code
2. **Use AxiRam sparse memory** for X-propagation testing: write only valid bytes, leave padding uninitialized → X propagation
3. **New Verilog testbenches** (not reuse tb_soc.v): wrappers need isolation from crossbar/DRAM/CPU
4. **Include all 3 wrappers**: each has unique width conversion that needs independent testing
5. **Verify only, no RTL fix**: separate concerns; if bugs found, log and defer to fix plan
6. **sz0001 execution via scripts**: follow established `p9_lib/p9_sz0001.sh` pattern from Phase 9

## Scope IN
- 3 new Verilog testbenches: `rtl/tb/tb_sfu_wrapper.v`, `tb_vector_wrapper.v`, `tb_mxu_wrapper.v`
- 1 Python common lib: `sim/tests/wrapper/wrapper_common.py` (ApbMaster/AxiRam helpers, non-aligned data generators)
- 3 Python test modules: `sim/tests/wrapper/test_sfu_wrapper.py`, `test_vector_wrapper.py`, `test_mxu_wrapper.py`
- BUG-005 directed tests: non-aligned burst padding, X-propagation from uninitialized memory
- BUG-007 directed test: consecutive START dispatch without full idle between ops
- Makefile targets for wrapper regression
- VCS flist for wrapper compilation (`rtl/tb/wrapper.flist`)
- Evidence files: `build/evidence/wrap-*.txt`
- Bug log entries for any newly discovered wrapper issues (append to `docs/bugs/bugs-soc-rtl.md`)

## Scope OUT (Must NOT have)
- No RTL changes to `sfu_soc_wrapper.v`, `vector_soc_wrapper.v`, `mxu_soc_wrapper.v`, or any `rtl/` file
- No firmware changes
- No changes to existing `sim/cocotb_bridge.py`, `sim/rtl_soc_runner.py`
- No MXU controller watchdog (BUG-MXU-WDT-001) testing — that's inside `mxu_top.v`, not wrapper
- No SoC-level integration tests — this plan is wrapper-only
- No Arc Model / DSE changes
- No `cocotb_bridge.py` modifications

## Open questions
None — all forks resolved by exploration + best-practice defaults.

## Approval gate
status: awaiting-approval
<!-- When exploration is exhausted and unknowns are answered, set status: awaiting-approval. -->
<!-- That durable record is the loop guard: on a later turn read it and resume at the gate instead of re-running exploration. -->