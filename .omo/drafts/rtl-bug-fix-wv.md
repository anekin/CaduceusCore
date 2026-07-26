# rtl-bug-fix-wv — Draft

## Intent
- intent: clear
- review_required: false

## User decisions
1. Scope: Only the 3 WV-discovered/confirmed bugs (WV-001 SFU DONE, BUG-005 Vector X-prop, BUG-007 MXU DONE)
2. Verification: WV wrapper tests (15 cocotb) + module-level regression (SFU 319 + Vector 63 + MXU 9)
3. Branch: All work on main branch (user directive: "以后设计验证的工作都在main分支上推进")

## Root cause findings (verified by direct Read of RTL source)

### BUG-RTL-SOC-WV-001 — SFU status_done is 1-cycle pulse, APB posedge samples too late
- File: `rtl/sfu/sfu_top.v`
- Line 643: ST_DONE sets `status_done <= 1'b1` and immediately `state <= ST_IDLE`
- Line 435: ST_IDLE clears `status_done <= 1'b0` on next posedge
- Result: status_done is HIGH for exactly 1 clock cycle
- IP-level tb_sfu.v uses negedge sampling → catches the pulse (319/319 PASS)
- Wrapper cocotb uses APB posedge → misses the pulse (4/5 FAIL-TIMEOUT)
- Fix: Make status_done sticky. Remove clear from ST_IDLE (line 435). Clear on cmd_start instead.
- Fix location: `rtl/sfu/sfu_top.v` lines 435-436

### BUG-005 — Vector wrapper AXI read path stores unmasked X from uninitialized bytes
- File: `rtl/wrapper/vector_soc_wrapper.v`
- Line 423: `m_axi_arlen = BEATS_PER_CHUNK - 1` (always 7 = 8 beats, fixed)
- Lines 299, 344: `buf_a/b[chunk] <= m_axi_rdata` (unmasked, full 512 bytes)
- Previous partial fixes already applied: variable wrp_chunks (line 167), wstrb masking on STORE (lines 448-474)
- Remaining bug: READ path still reads full 8-beat burst, uninitialized padding X enters buffer
- Fix: Variable arlen for last chunk + mask read data for partial beats in buffer write
- Fix location: `rtl/wrapper/vector_soc_wrapper.v` lines 299, 344, 423

### BUG-007 — MXU status_done is 1-cycle pulse + cmd_start only sampled in S_IDLE
- File: `rtl/mxu/controller.v`
- Line 309: S_DONE sets `status_done <= 1'b1` and immediately `state <= S_IDLE`
- Line 144: Default clears `status_done <= 1'b0` every cycle
- Line 156: `cmd_start` only checked in S_IDLE
- File: `rtl/mxu/mmio_if.v`
- Line 123: `cmd_start_r` is 1-cycle pulse
- Fix: Make status_done sticky (remove default clear, clear on cmd_start). Same pattern as SFU fix.
- Fix location: `rtl/mxu/controller.v` lines 144, 154-161, 307-312

## Components (topology lock)
1. SFU DONE fix → sfu_top.v sticky done
2. Vector X-prop fix → vector_soc_wrapper.v read masking
3. MXU DONE fix → controller.v sticky done + mmio_if.v
4. SFU X-prop follow-up → sfu_soc_wrapper.v (check if same pattern, fix if needed)
5. Module regression → SFU 319 + Vector 63 + MXU 9
6. Docs + closure → bugs-soc-rtl.md, issues_found.md

## Status
- status: awaiting-approval
- pending action: write .omo/plans/rtl-bug-fix-wv.md