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
REPO_ROOT    := $(abspath $(SIM_DIR)/..)

# ── VCS settings ───────────────────────────────────────────────────────────
VCS          := vcs
VCS_OPTS     := -full64 -sverilog -debug_access+all -timescale=1ns/1ps
VCS_COMMON   := $(VCS_OPTS) +v2k +lint=all +warn=all
SIMV_OPTS    :=

# ── Cocotb environment (EDA server has Python 2.7 as system python) ────────
# Use the pre-installed Anaconda Python 3.11 env that has cocotb/cocotbext-*.
COCOTB_PY_ENV ?= /NAS/Tools/anaconda3/envs/py3.11
export PATH   := $(COCOTB_PY_ENV)/bin:$(PATH)
PYTHON        := $(COCOTB_PY_ENV)/bin/python
COCOTB_VPI_LIB := $(shell $(COCOTB_PY_ENV)/bin/cocotb-config --lib-name-path vpi vcs 2>/dev/null)
COCOTB_LIB_DIR := $(COCOTB_PY_ENV)/lib/python3.11/site-packages/cocotb/libs

# VPI PLI table required by VCS to grant cocotb read/write access
PLI_TAB       := $(REPO_ROOT)/sim/regression/pli.tab

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
# Cocotb VPI support
# ═══════════════════════════════════════════════════════════════════════════

$(PLI_TAB):
	@echo "acc+=rw,wn:*" > $@

SOC_SIMV_COCOTB := simv_soc_cocotb

$(SOC_SIMV_COCOTB): $(SOC_SRC) $(SOC_FLIST) $(PLI_TAB)
	@echo "$(YELLOW)[SOC_COCOTB] Compiling tb_soc + cocotb VPI...$(NC)"
	cd $(REPO_ROOT) && $(VCS) $(VCS_COMMON) \
		+define+COCOTB_SIM=1 +vpi -P $(PLI_TAB) \
		-load $(COCOTB_VPI_LIB) \
		-f rtl/cpu/ibex.flist \
		-f rtl/ip/verilog-axi.flist \
		-f rtl/ip/verilog-pcie.flist \
		-f rtl/soc/soc.flist \
		-top tb_soc \
		rtl/tb/tb_soc.v \
		-o sim/regression/$(SOC_SIMV_COCOTB) \
		-l sim/regression/qwen_blk0_elab.log
	@echo "$(GREEN)[SOC_COCOTB] Compilation finished$(NC)"


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
	@./$(APB_SIMV) 2>&1 | tee apb_smoke.log
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
	@./$(INTC_SIMV) 2>&1 | tee intc_test.log
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
	@./$(DMA_SIMV) 2>&1 | tee dma_test.log
	@grep -q "ALL TESTS PASSED\|DMA_TEST: PASS" dma_test.log && \
		echo "$(GREEN)DMA_TEST: PASS$(NC)" || \
		(echo "$(RED)DMA_TEST: FAIL$(NC)" && exit 1)

$(DMA_SIMV): $(DMA_SRC)
	@echo "$(YELLOW)[DMA_TEST] Compiling...$(NC)"
	cd $(REPO_ROOT) && $(VCS) $(VCS_COMMON) -top tb_dma_wrapper \
		-f rtl/ip/verilog-axi.flist \
		$(subst $(RTL_DIR)/,rtl/,$(DMA_SRC)) \
		-o sim/regression/$(DMA_SIMV)


# ═══════════════════════════════════════════════════════════════════════════
# run_crossbar_stress — AXI4 Crossbar Concurrent Stress (≥10k cycles)
# ═══════════════════════════════════════════════════════════════════════════

XBAR_SRC := $(SOC_DIR)/axi_crossbar.v $(SOC_DIR)/axi_crossbar_tb.sv
XBAR_SIMV := simv_axi_crossbar_tb

.PHONY: run_crossbar_stress
run_crossbar_stress: $(XBAR_SIMV)
	@echo "$(YELLOW)[CROSSBAR_STRESS] Running (this may take a minute)...$(NC)"
	@./$(XBAR_SIMV) 2>&1 | tee crossbar_stress.log
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
	@./$(PCIE_SIMV) 2>&1 | tee pcie_test.log
	@grep -q "PCIE_TEST: PASS" pcie_test.log && \
		echo "$(GREEN)PCIE_TEST: PASS$(NC)" || \
		(echo "$(RED)PCIE_TEST: FAIL$(NC)" && exit 1)

$(PCIE_SIMV): $(PCIE_SRC)
	@echo "$(YELLOW)[PCIE_TEST] Compiling...$(NC)"
	cd $(REPO_ROOT) && $(VCS) $(VCS_COMMON) -top pcie_ep_tb \
		-f rtl/ip/verilog-axi.flist \
		-f rtl/ip/verilog-pcie.flist \
		$(subst $(RTL_DIR)/,rtl/,$(PCIE_SRC)) \
		-o sim/regression/$(PCIE_SIMV)


# ═══════════════════════════════════════════════════════════════════════════
# run_dram_test — DRAM Behavioral Model (100 random AXI4 txns)
# ═══════════════════════════════════════════════════════════════════════════

DRAM_SRC := $(IP_DIR)/dram_model.v $(TB_DIR)/dram_model_tb.sv
DRAM_SIMV := simv_dram_model_tb

.PHONY: run_dram_test
run_dram_test: $(DRAM_SIMV)
	@echo "$(YELLOW)[DRAM_TEST] Running...$(NC)"
	@./$(DRAM_SIMV) 2>&1 | tee dram_test.log
	@grep -q "^PASS\b" dram_test.log && \
		echo "$(GREEN)DRAM_TEST: PASS$(NC)" || \
		(echo "$(RED)DRAM_TEST: FAIL$(NC)" && exit 1)

$(DRAM_SIMV): $(DRAM_SRC)
	@echo "$(YELLOW)[DRAM_TEST] Compiling...$(NC)"
	$(VCS) $(VCS_COMMON) +define+SIMULATION -top dram_model_tb $(DRAM_SRC) -o $(DRAM_SIMV)


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
	cd $(REPO_ROOT) && $(VCS) $(VCS_COMMON) \
		-f rtl/cpu/ibex.flist \
		-f rtl/ip/verilog-axi.flist \
		-f rtl/ip/verilog-pcie.flist \
		-f rtl/soc/soc.flist \
		-top tb_soc \
		rtl/tb/tb_soc.v \
		-o sim/regression/$(SOC_SIMV) \
		-l sim/regression/soc_elab.log
	@echo "$(GREEN)[SOC_ELAB] SoC compilation finished — see sim/regression/soc_elab.log$(NC)"


# ═══════════════════════════════════════════════════════════════════════════
# run_qwen_e2e — Cocotb Qwen2.5-3B single-layer smoke test
# ═══════════════════════════════════════════════════════════════════════════

.PHONY: run_qwen_e2e
run_qwen_e2e: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[QWEN_E2E] Running cocotb Qwen2.5-3B smoke test...$(NC)"
	@echo "$(YELLOW)[QWEN_E2E] Using cocotb env: $(COCOTB_PY_ENV)$(NC)"
	cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_qwen_smoke \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_results.xml \
		./CaduceusCore/sim/regression/$(SOC_SIMV_COCOTB) +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e.log
	@grep -q "E2E.*PASS\|Qwen E2E.*passed\|test_qwen_smoke passed" $(REPO_ROOT)/sim/regression/qwen_e2e.log && \
		echo "$(GREEN)QWEN_E2E: PASS$(NC)" || \
		(echo "$(RED)QWEN_E2E: FAIL$(NC)" && exit 1)


# ═══════════════════════════════════════════════════════════════════════════
# run_e2e_blk0 — Cocotb Qwen2.5-3B blk.0 all 17 ops
# ═══════════════════════════════════════════════════════════════════════════

.PHONY: run_e2e_blk0
run_e2e_blk0: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_BLK0] Running cocotb blk.0 17-op chain...$(NC)"
	@echo "$(YELLOW)[E2E_BLK0] Using cocotb env: $(COCOTB_PY_ENV)$(NC)"
	cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_qwen_blk0 \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_blk0_results.xml \
		./CaduceusCore/sim/regression/simv_soc_cocotb +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_blk0.log
	@grep -q "BLK0.*PASS\|blk0.*passed\|all 17 ops" $(REPO_ROOT)/sim/regression/qwen_blk0.log && \
		echo "$(GREEN)E2E_BLK0: PASS$(NC)" || \
		(echo "$(RED)E2E_BLK0: FAIL$(NC)" && exit 1)


# ═══════════════════════════════════════════════════════════════════════════
# Per-op e2e targets for isolated P0 debugging
# ═══════════════════════════════════════════════════════════════════════════

.PHONY: run_e2e_rmsnorm run_e2e_softmax run_e2e_rope run_e2e_silu \
        run_e2e_mxu_single run_e2e_mxu_multi run_e2e_attn_score \
        run_e2e_mxu_op05 run_e2e_mxu_op07 \
        run_e2e_vresid run_e2e_vmul run_e2e_sfu_rmsnorm_post \
        run_e2e_intc_irq run_e2e_status_prop run_e2e_abort run_e2e_multi_irq \
        run_e2e_dma_load run_e2e_dma_concurrent run_e2e_crossbar_stress

run_e2e_rmsnorm: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_RMSNORM] Running single-op cocotb test test_e2e_sfu_rmsnorm...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_sfu_rmsnorm \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_rmsnorm_results.xml \
		./CaduceusCore/sim/regression/simv_soc_cocotb +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_rmsnorm.log
	@grep -q "test_e2e_sfu_rmsnorm.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_rmsnorm.log && \
		echo "$(GREEN)E2E_RMSNORM: PASS$(NC)" || \
		(echo "$(RED)E2E_RMSNORM: FAIL$(NC)" && exit 1)

run_e2e_softmax: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_SOFTMAX] Running single-op cocotb test test_e2e_sfu_softmax...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_sfu_softmax \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_softmax_results.xml \
		./CaduceusCore/sim/regression/simv_soc_cocotb +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_softmax.log
	@grep -q "test_e2e_sfu_softmax.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_softmax.log && \
		echo "$(GREEN)E2E_SOFTMAX: PASS$(NC)" || \
		(echo "$(RED)E2E_SOFTMAX: FAIL$(NC)" && exit 1)

run_e2e_rope: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_ROPE] Running single-op cocotb test test_e2e_sfu_rope...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_sfu_rope \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_rope_results.xml \
		./CaduceusCore/sim/regression/simv_soc_cocotb +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_rope.log
	@grep -q "test_e2e_sfu_rope.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_rope.log && \
		echo "$(GREEN)E2E_ROPE: PASS$(NC)" || \
		(echo "$(RED)E2E_ROPE: FAIL$(NC)" && exit 1)

run_e2e_silu: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_SILU] Running single-op cocotb test test_e2e_sfu_silu...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_sfu_silu \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_silu_results.xml \
		./CaduceusCore/sim/regression/simv_soc_cocotb +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_silu.log
	@grep -q "test_e2e_sfu_silu.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_silu.log && \
		echo "$(GREEN)E2E_SILU: PASS$(NC)" || \
		(echo "$(RED)E2E_SILU: FAIL$(NC)" && exit 1)

run_e2e_mxu_single: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_MXU_SINGLE] Running single-op cocotb test test_e2e_mxu_single_tile...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_mxu_single_tile \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_mxu_single_results.xml \
		./CaduceusCore/sim/regression/simv_soc_cocotb +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_mxu_single.log
	@grep -q "test_e2e_mxu_single_tile.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_mxu_single.log && \
		echo "$(GREEN)E2E_MXU_SINGLE: PASS$(NC)" || \
		(echo "$(RED)E2E_MXU_SINGLE: FAIL$(NC)" && exit 1)

run_e2e_mxu_multi: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_MXU_MULTI] Running single-op cocotb test test_e2e_mxu_multi_tile...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_mxu_multi_tile \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_mxu_multi_results.xml \
		./CaduceusCore/sim/regression/simv_soc_cocotb +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_mxu_multi.log
	@grep -q "test_e2e_mxu_multi_tile.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_mxu_multi.log && \
		echo "$(GREEN)E2E_MXU_MULTI: PASS$(NC)" || \
		(echo "$(RED)E2E_MXU_MULTI: FAIL$(NC)" && exit 1)

run_e2e_attn_score: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_ATTN_SCORE] Running single-op cocotb test test_e2e_attn_score...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_attn_score \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_attn_score_results.xml \
		./CaduceusCore/sim/regression/simv_soc_cocotb +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_attn_score.log
	@grep -q "test_e2e_attn_score.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_attn_score.log && \
		echo "$(GREEN)E2E_ATTN_SCORE: PASS$(NC)" || \
		(echo "$(RED)E2E_ATTN_SCORE: FAIL$(NC)" && exit 1)

run_e2e_mxu_op05: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_MXU_OP05] Running single-op cocotb test test_e2e_mxu_op05...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_mxu_op05 \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_mxu_op05_results.xml \
		./CaduceusCore/sim/regression/simv_soc_cocotb +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_mxu_op05.log
	@grep -q "test_e2e_mxu_op05.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_mxu_op05.log && \
		echo "$(GREEN)E2E_MXU_OP05: PASS$(NC)" || \
		(echo "$(RED)E2E_MXU_OP05: FAIL$(NC)" && exit 1)

run_e2e_mxu_op07: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_MXU_OP07] Running single-op cocotb test test_e2e_mxu_op07...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_mxu_op07 \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_mxu_op07_results.xml \
		./CaduceusCore/sim/regression/simv_soc_cocotb +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_mxu_op07.log
	@grep -q "test_e2e_mxu_op07.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_mxu_op07.log && \
		echo "$(GREEN)E2E_MXU_OP07: PASS$(NC)" || \
		(echo "$(RED)E2E_MXU_OP07: FAIL$(NC)" && exit 1)

run_e2e_vresid: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_VRESID] Running single-op cocotb test test_e2e_vector_vresid...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_vector_vresid \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_vresid_results.xml \
		./CaduceusCore/sim/regression/simv_soc_cocotb +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_vresid.log
	@grep -q "test_e2e_vector_vresid.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_vresid.log && \
		echo "$(GREEN)E2E_VRESID: PASS$(NC)" || \
		(echo "$(RED)E2E_VRESID: FAIL$(NC)" && exit 1)

run_e2e_vmul: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_VMUL] Running single-op cocotb test test_e2e_vector_vmul...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_vector_vmul \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_vmul_results.xml \
		./CaduceusCore/sim/regression/simv_soc_cocotb +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_vmul.log
	@grep -q "test_e2e_vector_vmul.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_vmul.log && \
		echo "$(GREEN)E2E_VMUL: PASS$(NC)" || \
		(echo "$(RED)E2E_VMUL: FAIL$(NC)" && exit 1)

run_e2e_sfu_rmsnorm_post: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_RMSNORM_POST] Running single-op cocotb test test_e2e_sfu_rmsnorm_post...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_sfu_rmsnorm_post \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_rmsnorm_post_results.xml \
		./CaduceusCore/sim/regression/simv_soc_cocotb +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_rmsnorm_post.log
	@grep -q "test_e2e_sfu_rmsnorm_post.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_rmsnorm_post.log && \
		echo "$(GREEN)E2E_RMSNORM_POST: PASS$(NC)" || \
		(echo "$(RED)E2E_RMSNORM_POST: FAIL$(NC)" && exit 1)


run_e2e_intc_irq: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_INTC_IRQ] Running cocotb test test_e2e_intc_irq...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_intc_irq \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_intc_irq_results.xml \
		./CaduceusCore/sim/regression/simv_soc_cocotb +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_intc_irq.log
	@grep -q "test_e2e_intc_irq.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_intc_irq.log && \
		echo "$(GREEN)E2E_INTC_IRQ: PASS$(NC)" || \
		(echo "$(RED)E2E_INTC_IRQ: FAIL$(NC)" && exit 1)

run_e2e_status_prop: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_STATUS_PROP] Running cocotb test test_e2e_status_propagation...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_status_propagation \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_status_prop_results.xml \
		./CaduceusCore/sim/regression/simv_soc_cocotb +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_status_prop.log
	@grep -q "test_e2e_status_propagation.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_status_prop.log && \
		echo "$(GREEN)E2E_STATUS_PROP: PASS$(NC)" || \
		(echo "$(RED)E2E_STATUS_PROP: FAIL$(NC)" && exit 1)

run_e2e_abort: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_ABORT] Running cocotb test test_e2e_abort...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_abort \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_abort_results.xml \
		./CaduceusCore/sim/regression/simv_soc_cocotb +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_abort.log
	@grep -q "test_e2e_abort.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_abort.log && \
		echo "$(GREEN)E2E_ABORT: PASS$(NC)" || \
		(echo "$(RED)E2E_ABORT: FAIL$(NC)" && exit 1)

run_e2e_multi_irq: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_MULTI_IRQ] Running cocotb test test_e2e_multi_irq...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_multi_irq \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_multi_irq_results.xml \
		./CaduceusCore/sim/regression/$(SOC_SIMV_COCOTB) +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_multi_irq.log
	@grep -q "test_e2e_multi_irq.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_multi_irq.log && \
		echo "$(GREEN)E2E_MULTI_IRQ: PASS$(NC)" || \
		(echo "$(RED)E2E_MULTI_IRQ: FAIL$(NC)" && exit 1)

run_e2e_dma_load: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_DMA_LOAD] Running cocotb test test_e2e_dma_load_store...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_dma_load_store \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_dma_load_results.xml \
		./CaduceusCore/sim/regression/$(SOC_SIMV_COCOTB) +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_dma_load.log
	@grep -q "test_e2e_dma_load_store.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_dma_load.log && \
		echo "$(GREEN)E2E_DMA_LOAD: PASS$(NC)" || \
		(echo "$(RED)E2E_DMA_LOAD: FAIL$(NC)" && exit 1)

run_e2e_dma_concurrent: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_DMA_CONCURRENT] Running cocotb test test_e2e_dma_mxu_concurrent...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_dma_mxu_concurrent \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_dma_concurrent_results.xml \
		./CaduceusCore/sim/regression/$(SOC_SIMV_COCOTB) +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_dma_concurrent.log
	@grep -q "test_e2e_dma_mxu_concurrent.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_dma_concurrent.log && \
		echo "$(GREEN)E2E_DMA_CONCURRENT: PASS$(NC)" || \
		(echo "$(RED)E2E_DMA_CONCURRENT: FAIL$(NC)" && exit 1)

run_e2e_crossbar_stress: $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[E2E_CROSSBAR_STRESS] Running cocotb test test_e2e_crossbar_stress...$(NC)"
	@cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=cocotb_bridge TESTCASE=test_e2e_crossbar_stress \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/qwen_e2e_crossbar_stress_results.xml \
		./CaduceusCore/sim/regression/$(SOC_SIMV_COCOTB) +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/qwen_e2e_crossbar_stress.log
	@grep -q "test_e2e_crossbar_stress.*PASS" $(REPO_ROOT)/sim/regression/qwen_e2e_crossbar_stress.log && \
		echo "$(GREEN)E2E_CROSSBAR_STRESS: PASS$(NC)" || \
		(echo "$(RED)E2E_CROSSBAR_STRESS: FAIL$(NC)" && exit 1)


# ═══════════════════════════════════════════════════════════════════════════
# Func Model SoC Case Targets (Todo 3: RTL SoC e2e test execution)
# ═══════════════════════════════════════════════════════════════════════════
#
# Usage:
#   make run_fm_soc_case CASE_ID=FM-SOC-001
#   make run_fm_soc_case CASE_ID=FM-SOC-001 EXTRA_DEFINES="+define+USE_RTL_PCIE"
#
# For full RTL mode (default, no defines needed), reuses the existing
# soc_cocotb simv. For mixed-mode, compiles tb_mixed.v with defines.
#
# The CASE_ID variable selects which cocotb test to run inside rtl_soc_runner.
# EXTRA_DEFINES is optional and passed to VCS compilation for mixed-mode.

FM_SOC_CASE_ID     ?= FM-SOC-001
FM_SOC_EXTRA_DEFINES ?=
MIXED_TOP           = tb_mixed
MIXED_SIMV_COCOTB   = simv_mixed_cocotb

$(MIXED_SIMV_COCOTB): $(PLI_TAB)
	@echo "$(YELLOW)[MIXED_COMPILE] Compiling tb_mixed.v + cocotb VPI$(NC)"
	@echo "$(YELLOW)[MIXED_COMPILE] Defines: $(FM_SOC_EXTRA_DEFINES)$(NC)"
	cd $(REPO_ROOT) && $(VCS) $(VCS_COMMON) \
		+define+COCOTB_SIM=1 +vpi -P $(PLI_TAB) \
		-load $(COCOTB_VPI_LIB) \
		$(FM_SOC_EXTRA_DEFINES) \
		-f rtl/cpu/ibex.flist \
		-f rtl/ip/verilog-axi.flist \
		-f rtl/ip/verilog-pcie.flist \
		-f rtl/soc/soc.flist \
		-top $(MIXED_TOP) \
		rtl/tb/tb_mixed.v \
		-o sim/regression/$(MIXED_SIMV_COCOTB) \
		-l sim/regression/mixed_elab.log
	@echo "$(GREEN)[MIXED_COMPILE] Compilation finished$(NC)"

.PHONY: run_fm_soc_case
run_fm_soc_case:
ifneq ($(strip $(FM_SOC_EXTRA_DEFINES)),)
	@# Mixed-mode: recompile with tb_mixed.v and defines
	@$(MAKE) $(MIXED_SIMV_COCOTB)
	@echo "$(YELLOW)[FM_SOC_CASE] Running $(FM_SOC_CASE_ID) with mixed-mode defines...$(NC)"
	cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=rtl_soc_runner TESTCASE=test_soc_rtl_runner_smoke \
		FM_SOC_CASE_ID=$(FM_SOC_CASE_ID) \
		FM_SOC_RTL_MODULE=$(FM_SOC_RTL_MODULE) \
		TOPLEVEL=$(MIXED_TOP) TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/fm_soc_$(FM_SOC_CASE_ID)_results.xml \
		./CaduceusCore/sim/regression/$(MIXED_SIMV_COCOTB) +define+COCOTB_SIM=1 +COCOTB -no_save \
		$(FM_SOC_EXTRA_DEFINES) \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/fm_soc_$(FM_SOC_CASE_ID).log
else
	@# Full RTL mode: reuse existing soc_cocotb simv
	@$(MAKE) $(SOC_SIMV_COCOTB)
	@echo "$(YELLOW)[FM_SOC_CASE] Running $(FM_SOC_CASE_ID) with full RTL...$(NC)"
	cd $(REPO_ROOT)/.. && \
		LD_LIBRARY_PATH=$(COCOTB_LIB_DIR):$(COCOTB_PY_ENV)/lib:$(LD_LIBRARY_PATH) \
		PYTHONPATH=$(REPO_ROOT)/sim MODULE=rtl_soc_runner TESTCASE=test_soc_rtl_runner_smoke \
		FM_SOC_CASE_ID=$(FM_SOC_CASE_ID) \
		TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \
		PYTHONIOENCODING=utf-8 \
		COCOTB_RESULTS_FILE=$(REPO_ROOT)/sim/regression/fm_soc_$(FM_SOC_CASE_ID)_results.xml \
		./CaduceusCore/sim/regression/$(SOC_SIMV_COCOTB) +define+COCOTB_SIM=1 +COCOTB -no_save \
		+BOOTROM_HEX=$(REPO_ROOT)/firmware/build/npu_firmware.hex 2>&1 | tee $(REPO_ROOT)/sim/regression/fm_soc_$(FM_SOC_CASE_ID).log
endif
	@grep -q "FM-SOC.*PASS\|test_soc_rtl_runner.*PASS" $(REPO_ROOT)/sim/regression/fm_soc_$(FM_SOC_CASE_ID).log && \
		echo "$(GREEN)FM_SOC_$(FM_SOC_CASE_ID): PASS$(NC)" || \
		(echo "$(RED)FM_SOC_$(FM_SOC_CASE_ID): FAIL$(NC)" && exit 1)

.PHONY: run_fm_soc_001 run_fm_soc_002 run_fm_soc_003 run_fm_soc_004 \
        run_fm_soc_005 run_fm_soc_006 run_fm_soc_007 run_fm_soc_008
run_fm_soc_001:
	@$(MAKE) run_fm_soc_case CASE_ID=FM-SOC-001
run_fm_soc_002:
	@$(MAKE) run_fm_soc_case CASE_ID=FM-SOC-002
run_fm_soc_003:
	@$(MAKE) run_fm_soc_case CASE_ID=FM-SOC-003
run_fm_soc_004:
	@$(MAKE) run_fm_soc_case CASE_ID=FM-SOC-004
run_fm_soc_005:
	@$(MAKE) run_fm_soc_case CASE_ID=FM-SOC-005
run_fm_soc_006:
	@$(MAKE) run_fm_soc_case CASE_ID=FM-SOC-006
run_fm_soc_007:
	@$(MAKE) run_fm_soc_case CASE_ID=FM-SOC-007
run_fm_soc_008:
	@$(MAKE) run_fm_soc_case CASE_ID=FM-SOC-008

# ═══════════════════════════════════════════════════════════════════════════
# Clean Targets
# ═══════════════════════════════════════════════════════════════════════════
# Clean Targets
# ═══════════════════════════════════════════════════════════════════════════

.PHONY: clean
clean:
	@rm -f simv_* simv *.daidir *.log ucli.key
	@rm -f apb_smoke.log intc_test.log dma_test.log
	@rm -f crossbar_stress.log pcie_test.log dram_test.log
	@rm -f soc_elab.log qwen_e2e_elab.log qwen_e2e.log
	@rm -f qwen_blk0_elab.log qwen_blk0.log pli.tab
	@rm -f qwen_e2e_results.xml qwen_blk0_results.xml
	@rm -f qwen_e2e_*.log qwen_e2e_*_results.xml
	@rm -rf simv_*.daidir
	@rm -f mixed_elab.log fm_soc_*.log fm_soc_*_results.xml
	@rm -rf simv_mixed_cocotb.daidir
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
	@echo "  run_e2e_blk0        — Cocotb E2E Qwen2.5-3B blk.0 all 17 ops"
	@echo "  run_e2e_dma_load    — Cocotb E2E-10 DMA load->compute->store"
	@echo "  run_e2e_dma_concurrent — Cocotb E2E-11 DMA+MXU concurrent"
	@echo "  run_e2e_crossbar_stress — Cocotb E2E-12 6-master crossbar stress"
	@echo "  run_fm_soc_case CASE_ID=<id> — Run one FM-SOC-NNN case (default: FM-SOC-001)"
	@echo "  run_fm_soc_001 ... run_fm_soc_008 — P0 cases FM-SOC-001 through FM-SOC-008"
	@echo "    EXTRA_DEFINES=+define+USE_RTL_PCIE   — optional mixed-mode defines"
	@echo ""
	@echo "Maintenance:"
	@echo "  clean               — Remove simulation artifacts"
	@echo "  distclean           — Remove all artifacts including waveforms"
	@echo ""
	@echo "Parallel: make -j4 all"
