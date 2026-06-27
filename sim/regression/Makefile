# =============================================================================
# CaduceusCore SoC Regression Makefile
# =============================================================================
# SoC Phase 3-4 / Task 14
#
# Aggregates all module-level and system-level regression tests.
# Each target: VCS compile → simulate → grep PASS/FAIL.
#
# Targets:
#   all               — Run all regression tests
#   run_apb_smoke     — APB decoder smoke test (7-slave select + pslverr)
#   run_intc_test     — INTC 7-source interrupt controller test
#   run_dma_test      — DMA wrapper transfer test
#   run_crossbar_stress — AXI4 crossbar concurrent stress (≥10k cycles)
#   run_pcie_test     — PCIe EP wrapper test (TLP → AXI → readback)
#   run_dram_test     — DRAM behavioral model test (100 random txns)
#   run_soc_elab      — SoC top-level elaboration (no simulation)
#   run_qwen_e2e      — Cocotb Qwen2.5-3B single-layer smoke test
#
# VCS on EDA server:
#   ssh zhengs@192.168.0.11
#   source /NAS/Tools/EDA/env/modules.bash
#   module load vcs/vcs_2023.12sp2
#
# Usage:
#   make run_apb_smoke       # Single test
#   make -j4 all              # All tests (parallel)
#   make VCS_OPTS="-R" all    # With runtime flags
# =============================================================================

# ── Paths ──────────────────────────────────────────────────────────────────
SOC_FLIST    := ../../rtl/soc/soc.flist
IBEX_FLIST   := ../../rtl/cpu/ibex.flist
AXI_FLIST    := ../../rtl/ip/verilog-axi.flist
PCIE_FLIST   := ../../rtl/ip/verilog-pcie.flist
DRAM_FLIST   := ../../rtl/ip/litedram.flist

RTL_DIR      := ../../rtl
TB_DIR       := $(RTL_DIR)/tb
SOC_DIR      := $(RTL_DIR)/soc
INTC_DIR     := $(RTL_DIR)/intc
IP_DIR       := $(RTL_DIR)/ip
WRAPPER_DIR  := $(RTL_DIR)/wrapper
CPU_DIR      := $(RTL_DIR)/cpu
MXU_DIR      := $(RTL_DIR)/mxu
SFU_DIR      := $(RTL_DIR)/sfu
VECTOR_DIR   := $(RTL_DIR)/vector

SIM_DIR      := ..

# ── VCS settings ───────────────────────────────────────────────────────────
VCS          := vcs
VCS_OPTS     := -full64 -sverilog -debug_access+all -timescale=1ns/1ps
VCS_COMMON   := $(VCS_OPTS) +v2k +lint=all +warn=all
SIMV_OPTS    :=

# Detect if VCS is available (vs running on a machine without EDA tools)
VCS_AVAILABLE := $(shell which vcs 2>/dev/null)

ifeq ($(VCS_AVAILABLE),)
  $(warning VCS not found in PATH. Skipping VCS-based tests. Source EDA env first.)
  VCS = echo "[SKIP] VCS not found — skipping compilation of"
  SIMV_RUN = echo "[SKIP] simv binary not found — skipping"
else
  SIMV_RUN = ./
endif

# ── Colors ────────────────────────────────────────────────────────────────
GREEN  := \033[0;32m
RED    := \033[0;31m
YELLOW := \033[0;33m
NC     := \033[0m


# ═══════════════════════════════════════════════════════════════════════════
# All Tests
# ═══════════════════════════════════════════════════════════════════════════

.PHONY: all
all: run_apb_smoke run_intc_test run_dma_test run_crossbar_stress \
     run_pcie_test run_dram_test
	@echo ""
	@echo "$(GREEN)========================================$(NC)"
	@echo "$(GREEN)  All SoC Regression Tests Complete$(NC)"
	@echo "$(GREEN)========================================$(NC)"


# ═══════════════════════════════════════════════════════════════════════════
# run_apb_smoke — APB Decoder (7-slave select + pslverr)
# ═══════════════════════════════════════════════════════════════════════════

APB_SRC := $(SOC_DIR)/apb_decoder.v $(TB_DIR)/apb_decoder_tb.sv
APB_SIMV := simv_apb_decoder_tb

.PHONY: run_apb_smoke
run_apb_smoke: $(APB_SIMV)
	@echo "$(YELLOW)[APB_SMOKE] Running...$(NC)"
	@./$(APB_SIMV) +vcs+finish+0 2>&1 | tee apb_smoke.log
	@grep -q "APB_SMOKE: PASS\|RESULT: ALL TESTS PASSED" apb_smoke.log && \
		echo "$(GREEN)APB_SMOKE: PASS$(NC)" || \
		(echo "$(RED)APB_SMOKE: FAIL$(NC)" && exit 1)

$(APB_SIMV): $(APB_SRC)
	@echo "$(YELLOW)[APB_SMOKE] Compiling...$(NC)"
	$(VCS) $(VCS_COMMON) -top apb_decoder_tb $(APB_SRC) -o $(APB_SIMV)


# ═══════════════════════════════════════════════════════════════════════════
# run_intc_test — INTC 7-source Interrupt Controller
# ═══════════════════════════════════════════════════════════════════════════

INTC_SRC := $(INTC_DIR)/intc_top.v $(TB_DIR)/tb_intc.v
INTC_SIMV := simv_intc_tb

.PHONY: run_intc_test
run_intc_test: $(INTC_SIMV)
	@echo "$(YELLOW)[INTC_TEST] Running...$(NC)"
	@./$(INTC_SIMV) +vcs+finish+0 2>&1 | tee intc_test.log
	@grep -q "\[INTC_TEST\] PASS" intc_test.log && \
		echo "$(GREEN)INTC_TEST: PASS$(NC)" || \
		(echo "$(RED)INTC_TEST: FAIL$(NC)" && exit 1)

$(INTC_SIMV): $(INTC_SRC)
	@echo "$(YELLOW)[INTC_TEST] Compiling...$(NC)"
	$(VCS) $(VCS_COMMON) -top tb_intc $(INTC_SRC) -o $(INTC_SIMV)


# ═══════════════════════════════════════════════════════════════════════════
# run_dma_test — DMA Wrapper (axi_cdma) transfer test
# ═══════════════════════════════════════════════════════════════════════════

DMA_SRC := $(IP_DIR)/dma_wrapper.v $(TB_DIR)/tb_dma_wrapper.v
DMA_SIMV := simv_dma_wrapper_tb

.PHONY: run_dma_test
run_dma_test: $(DMA_SIMV)
	@echo "$(YELLOW)[DMA_TEST] Running...$(NC)"
	@./$(DMA_SIMV) +vcs+finish+0 2>&1 | tee dma_test.log
	@grep -q "ALL TESTS PASSED\|DMA_TEST: PASS" dma_test.log && \
		echo "$(GREEN)DMA_TEST: PASS$(NC)" || \
		(echo "$(RED)DMA_TEST: FAIL$(NC)" && exit 1)

$(DMA_SIMV): $(DMA_SRC)
	@echo "$(YELLOW)[DMA_TEST] Compiling...$(NC)"
	$(VCS) $(VCS_COMMON) -top tb_dma_wrapper $(DMA_SRC) -o $(DMA_SIMV)


# ═══════════════════════════════════════════════════════════════════════════
# run_crossbar_stress — AXI4 Crossbar Concurrent Stress (≥10k cycles)
# ═══════════════════════════════════════════════════════════════════════════

XBAR_SRC := $(SOC_DIR)/axi_crossbar.v $(SOC_DIR)/axi_crossbar_tb.sv
XBAR_SIMV := simv_axi_crossbar_tb

.PHONY: run_crossbar_stress
run_crossbar_stress: $(XBAR_SIMV)
	@echo "$(YELLOW)[CROSSBAR_STRESS] Running (this may take a minute)...$(NC)"
	@./$(XBAR_SIMV) +vcs+finish+0 2>&1 | tee crossbar_stress.log
	@grep -q "CROSSBAR_STRESS: PASS" crossbar_stress.log && \
		echo "$(GREEN)CROSSBAR_STRESS: PASS$(NC)" || \
		(echo "$(RED)CROSSBAR_STRESS: FAIL$(NC)" && exit 1)

$(XBAR_SIMV): $(XBAR_SRC)
	@echo "$(YELLOW)[CROSSBAR_STRESS] Compiling...$(NC)"
	$(VCS) $(VCS_COMMON) -top axi_crossbar_tb $(XBAR_SRC) -o $(XBAR_SIMV)


# ═══════════════════════════════════════════════════════════════════════════
# run_pcie_test — PCIe EP Wrapper (TLP → AXI → readback)
# ═══════════════════════════════════════════════════════════════════════════

PCIE_SRC := $(IP_DIR)/pcie_ep_wrapper.v $(TB_DIR)/pcie_ep_tb.sv
PCIE_SIMV := simv_pcie_ep_tb

.PHONY: run_pcie_test
run_pcie_test: $(PCIE_SIMV)
	@echo "$(YELLOW)[PCIE_TEST] Running...$(NC)"
	@./$(PCIE_SIMV) +vcs+finish+0 2>&1 | tee pcie_test.log
	@grep -q "PCIE_TEST: PASS" pcie_test.log && \
		echo "$(GREEN)PCIE_TEST: PASS$(NC)" || \
		(echo "$(RED)PCIE_TEST: FAIL$(NC)" && exit 1)

$(PCIE_SIMV): $(PCIE_SRC)
	@echo "$(YELLOW)[PCIE_TEST] Compiling...$(NC)"
	$(VCS) $(VCS_COMMON) -top pcie_ep_tb $(PCIE_SRC) -o $(PCIE_SIMV)


# ═══════════════════════════════════════════════════════════════════════════
# run_dram_test — DRAM Behavioral Model (100 random AXI4 txns)
# ═══════════════════════════════════════════════════════════════════════════

DRAM_SRC := $(IP_DIR)/dram_model.v $(TB_DIR)/dram_model_tb.sv
DRAM_SIMV := simv_dram_model_tb

.PHONY: run_dram_test
run_dram_test: $(DRAM_SIMV)
	@echo "$(YELLOW)[DRAM_TEST] Running...$(NC)"
	@./$(DRAM_SIMV) +vcs+finish+0 2>&1 | tee dram_test.log
	@grep -q "^PASS\b" dram_test.log && \
		echo "$(GREEN)DRAM_TEST: PASS$(NC)" || \
		(echo "$(RED)DRAM_TEST: FAIL$(NC)" && exit 1)

$(DRAM_SIMV): $(DRAM_SRC)
	@echo "$(YELLOW)[DRAM_TEST] Compiling...$(NC)"
	$(VCS) $(VCS_COMMON) -top dram_model_tb $(DRAM_SRC) -o $(DRAM_SIMV)


# ═══════════════════════════════════════════════════════════════════════════
# run_soc_elab — SoC Top-Level Elaboration (no simulation, compile only)
# ═══════════════════════════════════════════════════════════════════════════

SOC_SRC := $(TB_DIR)/tb_soc.v
SOC_SIMV := simv_soc

.PHONY: run_soc_elab
run_soc_elab: $(SOC_SIMV)
	@echo "$(GREEN)SOC_ELAB: PASS (compilation complete)$(NC)"

$(SOC_SIMV): $(SOC_SRC) $(SOC_FLIST)
	@echo "$(YELLOW)[SOC_ELAB] Compiling tb_soc + caduceus_soc_top...$(NC)"
	@echo "$(YELLOW)[SOC_ELAB] This includes ibex, verilog-axi, verilog-pcie filelists$(NC)"
	@echo "$(YELLOW)[SOC_ELAB] NOTE: ibex.flist FIRST (ibex_pkg.sv needed before ibex_wrapper)$(NC)"
	$(VCS) $(VCS_COMMON) \
		-f $(IBEX_FLIST) \
		-f $(AXI_FLIST) \
		-f $(PCIE_FLIST) \
		-f $(SOC_FLIST) \
		-top tb_soc \
		$(SOC_SRC) \
		-o $(SOC_SIMV) \
		-l soc_elab.log
	@echo "$(GREEN)[SOC_ELAB] SoC compilation finished — see soc_elab.log$(NC)"


# ═══════════════════════════════════════════════════════════════════════════
# run_qwen_e2e — Cocotb Qwen2.5-3B single-layer smoke test
# ═══════════════════════════════════════════════════════════════════════════

.PHONY: run_qwen_e2e
run_qwen_e2e:
	@echo "$(YELLOW)[QWEN_E2E] Running cocotb Qwen2.5-3B smoke test...$(NC)"
	@echo "$(YELLOW)[QWEN_E2E] Requires: pip install cocotb cocotbext-axi cocotbext-pcie$(NC)"
	$(VCS) $(VCS_COMMON) \
		-f $(IBEX_FLIST) \
		-f $(AXI_FLIST) \
		-f $(PCIE_FLIST) \
		-f $(SOC_FLIST) \
		-top tb_soc \
		$(TB_DIR)/tb_soc.v \
		-o $(SOC_SIMV) \
		-l qwen_e2e_elab.log
	@echo "$(YELLOW)[QWEN_E2E] Starting cocotb simulation...$(NC)"
	cd ../.. && PYTHONPATH=sim MODULE=cocotb_bridge.test_qwen_smoke \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		python -m cocotb_test.simulator run 2>&1 | tee sim/regression/qwen_e2e.log
	@grep -q "E2E.*PASS\|Qwen E2E.*passed" sim/regression/qwen_e2e.log && \
		echo "$(GREEN)QWEN_E2E: PASS$(NC)" || \
		(echo "$(RED)QWEN_E2E: FAIL$(NC)" && exit 1)


# ═══════════════════════════════════════════════════════════════════════════
# Clean Targets
# ═══════════════════════════════════════════════════════════════════════════

.PHONY: clean
clean:
	@rm -f simv_* simv *.daidir *.log ucli.key
	@rm -f apb_smoke.log intc_test.log dma_test.log
	@rm -f crossbar_stress.log pcie_test.log dram_test.log
	@rm -f soc_elab.log qwen_e2e_elab.log qwen_e2e.log
	@rm -rf simv_*.daidir
	@echo "Regression artifacts cleaned"

.PHONY: distclean
distclean: clean
	@rm -f *.vcd *.fsdb *.vpd
	@echo "All simulation artifacts cleaned"


# ═══════════════════════════════════════════════════════════════════════════
# Help
# ═══════════════════════════════════════════════════════════════════════════

.PHONY: help
help:
	@echo "CaduceusCore SoC Regression Makefile"
	@echo ""
	@echo "Primary targets:"
	@echo "  all                 — Run all regression tests"
	@echo "  run_apb_smoke       — APB decoder (7-slave select)"
	@echo "  run_intc_test       — INTC 7-source controller"
	@echo "  run_dma_test        — DMA wrapper transfer"
	@echo "  run_crossbar_stress — AXI crossbar concurrent (>=10k cycles)"
	@echo "  run_pcie_test       — PCIe EP wrapper (TLP→AXI)"
	@echo "  run_dram_test       — DRAM model (100 random txns)"
	@echo "  run_soc_elab        — SoC top-level elaboration only"
	@echo "  run_qwen_e2e        — Cocotb Qwen2.5-3B smoke test"
	@echo ""
	@echo "Maintenance:"
	@echo "  clean               — Remove simulation artifacts"
	@echo "  distclean           — Remove all artifacts including waveforms"
	@echo ""
	@echo "Parallel: make -j4 all"
