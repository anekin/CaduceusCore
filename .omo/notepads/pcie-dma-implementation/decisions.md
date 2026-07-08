# pcie-dma-implementation Decisions

## Locked Decisions (from plan)
- D1: dma_if_pcie as DMA module
- D2: TLP port separation at SoC boundary (bridge TLP ports inside pcie_ep_wrapper.v; DMA TLP ports exposed separately at caduceus_soc_top.v). pcie_tlp_mux/demux integration deferred to future phase.
- D3: dma_if_axi as AXI bridge
- D4: crossbar NUM_M 6→7
- D5: APB decoder 7→8 slaves
- D6: APB→stream descriptor adapter in pcie_dma_wrapper.v
- D7: OP_PCIE_DMA = 7
- D8: Func Model → RTL → cocotb test-first
- D9: never modify vendored verilog-pcie

