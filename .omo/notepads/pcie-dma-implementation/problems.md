# pcie-dma-implementation Problems

## Open

### PROB-001: Firmware doorbell path blocked by WFI
- **Description**: TC-SOC4 sub-test B depends on firmware dispatch via doorbell, but Ibex may hang in `WFI` after boot because no machine timer interrupt is configured in simulation.
- **Workaround**: Direct APB programming of `pcie_dma_wrapper` registers provides equivalent hardware-path coverage.
- **Resolution plan**: Configure a periodic timer interrupt or replace `WFI` with NOP loop in simulation firmware to enable true doorbell E2E testing.

### PROB-002: TLP mux/demux deferred to future phase
- **Description**: C1 originally required `pcie_tlp_mux` + `pcie_tlp_demux` inside `pcie_ep_wrapper.v`. Implementation instead exposed separate bridge and DMA TLP port groups at the SoC boundary.
- **Workaround**: Plan amended to document this architectural decision; cocotb host model handles logical merge/split.
- **Resolution plan**: Implement mux/demux when a single external TLP link is required.

### PROB-003: Pre-existing simulation warnings
- **Description**: `sram_init.hex` and `rope_theta_inv_freq.hex` missing warnings appear in every SoC simulation. These are pre-existing and unrelated to PCIe DMA.
- **Workaround**: None needed; warnings do not affect PCIe DMA test results.
- **Resolution plan**: Generate or stub the missing init files in a separate cleanup task.

## Closed
(none)

