# pcie-dma-implementation Issues

## Open
(none)

## Closed

### ISSUE-001: Wave 5 cocotb E2E read path stuck at `rd_busy`
- **Symptom**: TC1/TC3/TC5/TC6 failed with `STATUS=0x00000001` (`rd_busy` never cleared).
- **Root cause**: `send_cpl_for_mrd()` in `sim/cocotb_bridge.py` built the CplD header with RequesterID/Tag in wrong positions, so `dma_if_pcie_rd.v` rejected completions.
- **Fix**: Aligned CplD header field extraction and construction with `dma_if_pcie_rd.v:971-992`.
- **Evidence**: `.omo/evidence/cocotb_e2e.log` now shows 6/6 PASS.

### ISSUE-002: Makefile falsely reported all cocotb tests PASS
- **Symptom**: Only 1/6 cocotb tests actually passed, but `make run_pcie_dma_e2e` printed "all 6/6 tests passed".
- **Root cause**: Result check used VCS exit code (`PIPESTATUS[0]`) which is always 0 even when cocotb reports FAIL.
- **Fix**: Switched to cocotb JUnit XML checking (`<testcase>` present + no `<failure>`/`<error>`).
- **Evidence**: `sim/regression/Makefile` lines 630-642.

### ISSUE-003: TC3 concurrent bridge+DMA test missed MRd valid pulse
- **Symptom**: TC3 timed out because `receive_pcie_tlp()` started after the DMA had already issued MRd.
- **Root cause**: `tb_soc.v` initializes `pcie_dma_tx_rd_req_tlp_ready=1`, so MRd can fire before the test starts monitoring.
- **Fix**: Launch `receive_pcie_tlp()` as a background task via `cocotb.start_soon()` before concurrent SRAM writes.
- **Evidence**: `sim/tests/test_soc_pcie_dma.py` lines 362-369.

