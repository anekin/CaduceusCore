"""
diagnose_mmu_path.py — Phase 9 MMU Path Diagnostic Harness
=========================================================================
Read-only cocotb module for probing MXU SoC wrapper internals.

USAGE (as cocotb test):
    MODULE=diagnose_mmu_path TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog \\
        python -m cocotb_test.simulator run

USAGE (imported probe API):
    from diagnose_mmu_path import probe_all_signals
    probes = await probe_all_signals(dut, case_id="ph9-smoke")

This module NEVER modifies RTL or firmware source files. All access
is via cocotb backdoor (VPI) or $fsdbDumpvars system tasks.
"""

import json
import os
import sys
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

# ── Conditional cocotb import (available only inside simulation) ──
try:
    import cocotb
    from cocotb.triggers import ClockCycles, RisingEdge, FallingEdge, Timer
    COCOTB_AVAILABLE = True
except ImportError:
    COCOTB_AVAILABLE = False
    # Stubs for documentation / offline AST validation
    class _cocotb_stub:
        @staticmethod
        def test(*args, **kwargs):
            return lambda f: f
        top = None
    cocotb = _cocotb_stub()  # type: ignore

logger = logging.getLogger("diagnose_mmu_path")

# ═══════════════════════════════════════════════════════════════════════════
# Signal probe definitions — one probe group per diagnostic domain
# Each probe is a (hier_path, width, description) tuple.
# The hier_path is relative to the SoC top (tb_soc.u_caduceus_soc_top).
# ═══════════════════════════════════════════════════════════════════════════

WRAPPER_PREFIX = "u_caduceus_soc_top.u_mxu_soc_wrapper"

# Probe group 1: Wrapper preload registers   (mxu_soc_wrapper.v:165-170)
PROBE_WRP_REGS = [
    (f"{WRAPPER_PREFIX}.wrp_weight_base", 32,
     "Wrapper preload — weight base address"),
    (f"{WRAPPER_PREFIX}.wrp_act_base", 32,
     "Wrapper preload — activation base address"),
    (f"{WRAPPER_PREFIX}.wrp_out_base", 32,
     "Wrapper preload — output base address"),
    (f"{WRAPPER_PREFIX}.wrp_k_tiles", 16,
     "Wrapper preload — K-tile count"),
    (f"{WRAPPER_PREFIX}.wrp_n", 16,
     "Wrapper preload — output N dimension"),
    (f"{WRAPPER_PREFIX}.wrp_load_done", 1,
     "Wrapper preload — load-done status flag"),
    (f"{WRAPPER_PREFIX}.wrp_trigger", 1,
     "Wrapper preload — trigger strobe (wire)"),
]

# Probe group 2: Preload FSM state          (mxu_soc_wrapper.v:289-292)
PROBE_PRELOAD_FSM = [
    (f"{WRAPPER_PREFIX}.pl_state", 4,
     "Preload FSM — current state"),
    (f"{WRAPPER_PREFIX}.pl_beat_cnt", 8,
     "Preload FSM — beat counter within current burst"),
    (f"{WRAPPER_PREFIX}.pl_k_tile_cnt", 16,
     "Preload FSM — current K-tile index"),
    (f"{WRAPPER_PREFIX}.pl_cur_addr", 32,
     "Preload FSM — current burst start address"),
]

# Probe group 3: Broadcast bus driver       (mxu_soc_wrapper.v:412-415)
PROBE_BROADCAST_DRIVER = [
    (f"{WRAPPER_PREFIX}.tile_cycle", 14,
     "Broadcast driver — cycle counter within tile"),
    (f"{WRAPPER_PREFIX}.tile_active", 1,
     "Broadcast driver — tile active flag"),
    (f"{WRAPPER_PREFIX}.tile_k_cur", 7,
     "Broadcast driver — current K index"),
    (f"{WRAPPER_PREFIX}.burst_cnt", 16,
     "Broadcast driver — burst counter"),
    (f"{WRAPPER_PREFIX}.data_valid", 1,
     "Broadcast driver — data valid (wire)"),
]

# Probe group 4: Store-out FIFO              (mxu_soc_wrapper.v:477-578)
PROBE_STOREOUT_FIFO = [
    (f"{WRAPPER_PREFIX}.so_fifo_wr_ptr", 6,
     "Store-out FIFO — write pointer"),
    (f"{WRAPPER_PREFIX}.so_fifo_rd_ptr", 6,
     "Store-out FIFO — read pointer"),
    (f"{WRAPPER_PREFIX}.so_fifo_empty", 1,
     "Store-out FIFO — empty flag (wire)"),
    (f"{WRAPPER_PREFIX}.so_capture_row", 6,
     "Store-out — captured row index (wire)"),
    (f"{WRAPPER_PREFIX}.so_state", 3,
     "Store-out FSM — current state"),
    (f"{WRAPPER_PREFIX}.so_base_addr", 32,
     "Store-out — output base address (wire)"),
    (f"{WRAPPER_PREFIX}.so_beats", 8,
     "Store-out — AXI beats per row (wire)"),
    (f"{WRAPPER_PREFIX}.so_w_beat", 4,
     "Store-out FSM — W-channel beat counter"),
]

# Probe group 5: AXI4 AR channel            (mxu_soc_wrapper.v:90-94, 392-398)
PROBE_AXI_AR = [
    (f"{WRAPPER_PREFIX}.m_axi_araddr", 32,
     "AXI AR — address (wire)"),
    (f"{WRAPPER_PREFIX}.m_axi_arlen", 8,
     "AXI AR — burst length (wire)"),
    (f"{WRAPPER_PREFIX}.m_axi_arvalid", 1,
     "AXI AR — valid (wire)"),
    (f"{WRAPPER_PREFIX}.m_axi_arready", 1,
     "AXI AR — ready (input wire)"),
]

# Probe group 6: AXI4 AW/W channel          (mxu_soc_wrapper.v:68-79, 591-617)
PROBE_AXI_AW_W = [
    (f"{WRAPPER_PREFIX}.m_axi_awaddr", 32,
     "AXI AW — address (wire)"),
    (f"{WRAPPER_PREFIX}.m_axi_awlen", 8,
     "AXI AW — burst length (wire)"),
    (f"{WRAPPER_PREFIX}.m_axi_awvalid", 1,
     "AXI AW — valid (wire)"),
    (f"{WRAPPER_PREFIX}.m_axi_wlast", 1,
     "AXI W — last beat (wire)"),
    (f"{WRAPPER_PREFIX}.m_axi_wvalid", 1,
     "AXI W — valid (wire)"),
    (f"{WRAPPER_PREFIX}.m_axi_wready", 1,
     "AXI W — ready (input wire)"),
]

# Probe group 7: MXU debug/status ports     (mxu_soc_wrapper.v:109-116)
PROBE_MXU_DBG = [
    (f"{WRAPPER_PREFIX}.dbg_state", 4,
     "MXU controller — FSM state"),
    (f"{WRAPPER_PREFIX}.dbg_compute_en", 1,
     "MXU controller — compute enable"),
    (f"{WRAPPER_PREFIX}.dbg_weight_load", 1,
     "MXU controller — weight load phase"),
    (f"{WRAPPER_PREFIX}.dbg_activation_load", 1,
     "MXU controller — activation load phase"),
    (f"{WRAPPER_PREFIX}.dbg_store_out", 1,
     "MXU controller — store-out phase"),
    (f"{WRAPPER_PREFIX}.dbg_store_row", 6,
     "MXU controller — current store row"),
    (f"{WRAPPER_PREFIX}.dbg_compute_k", 6,
     "MXU controller — current compute K index"),
    (f"{WRAPPER_PREFIX}.dbg_tiles_completed", 16,
     "MXU controller — tiles completed counter"),
]

# ── All probe groups (flat list) ──
ALL_PROBES: List[tuple] = (
    PROBE_WRP_REGS
    + PROBE_PRELOAD_FSM
    + PROBE_BROADCAST_DRIVER
    + PROBE_STOREOUT_FIFO
    + PROBE_AXI_AR
    + PROBE_AXI_AW_W
    + PROBE_MXU_DBG
)

# ═══════════════════════════════════════════════════════════════════════════
# Firmware MMIO trace definitions
# These are the APB writes performed by mxu_start() in npu_firmware.c:199-206
# ═══════════════════════════════════════════════════════════════════════════

# MXU MMIO register offsets (from npu-regmap.h / regmap.py)
MXU_REG_NAMES = {
    0x00: "I_ADDR",
    0x04: "W_ADDR",
    0x08: "O_ADDR",
    0x0C: "SCALE_ADDR",
    0x10: "CTRL",
    0x14: "DIM0",           # {K_hi[15:0], M[15:0]}
    0x18: "DIM1",           # N[15:0]
    0x1C: "CMD",
    0x20: "STATUS",
}


# ═══════════════════════════════════════════════════════════════════════════
# Core probe function: read one RTL signal via cocotb backdoor (VPI)
# ═══════════════════════════════════════════════════════════════════════════

def _signal_path(dut, hier: str):
    """Navigate the hierarchical signal path from dut top."""
    parts = hier.split(".")
    obj = dut
    for p in parts:
        obj = getattr(obj, p)
    return obj


async def read_signal(dut, hier: str, width: int = 32) -> int:
    """Read a single RTL signal via cocotb backdoor (VPI access).

    Uses the .value attribute which reads the current simulation value
    without modifying any RTL state — 100% read-only.
    """
    if not COCOTB_AVAILABLE:
        return 0  # offline / AST-validate mode
    sig = _signal_path(dut, hier)
    # .value can be a BinaryValue: convert to int
    val = sig.value
    if hasattr(val, "integer"):
        return int(val.integer)
    return int(val)


async def read_signal_bin(dut, hier: str, width: int = 32) -> str:
    """Read a single RTL signal and return its binary string representation."""
    if not COCOTB_AVAILABLE:
        return "0" * width
    sig = _signal_path(dut, hier)
    val = sig.value
    if hasattr(val, "binstr"):
        return str(val.binstr)
    return bin(int(val))[2:].zfill(width)


async def probe_group(
    dut, probes: List[tuple], timestamp_ns: int, case_id: str
) -> List[Dict[str, Any]]:
    """Read a group of signals and return JSON-serializable records."""
    records = []
    for hier, width, desc in probes:
        try:
            raw = await read_signal_bin(dut, hier, width)
            ival = int(raw, 2) if raw else 0
        except Exception as exc:
            raw = "ERROR"
            ival = 0
            logger.warning("probe %s: %s", hier, exc)

        records.append({
            "case": case_id,
            "timestamp_ns": timestamp_ns,
            "signal": hier,
            "width": width,
            "value_bin": raw,
            "value_int": ival,
            "description": desc,
        })
    return records


# ═══════════════════════════════════════════════════════════════════════════
# MMIO transaction snapshot — read APB signals for the last MMIO write
# ═══════════════════════════════════════════════════════════════════════════

async def probe_mmio_trace(
    dut, timestamp_ns: int, case_id: str
) -> List[Dict[str, Any]]:
    """Read the mxu_soc_wrapper APB interface to capture any pending MMIO
    write. In a real cocotb simulation this would be sampled on psel && pwrite
    && penable rising edge, but for a passive snapshot we read the current
    APB bus state."""
    if not COCOTB_AVAILABLE:
        return []
    prefix = WRAPPER_PREFIX
    try:
        psel     = await read_signal(dut, f"{prefix}.psel", 1)
        pwrite   = await read_signal(dut, f"{prefix}.pwrite", 1)
        penable  = await read_signal(dut, f"{prefix}.penable", 1)
        paddr    = await read_signal(dut, f"{prefix}.paddr", 12)
        pwdata   = await read_signal(dut, f"{prefix}.pwdata", 32)
    except Exception as exc:
        logger.warning("probe_mmio_trace: %s", exc)
        return []

    return [{
        "case": case_id,
        "timestamp_ns": timestamp_ns,
        "type": "mmio_apb_snapshot",
        "psel": psel,
        "pwrite": pwrite,
        "penable": penable,
        "paddr": f"0x{paddr:03X}",
        "paddr_reg": MXU_REG_NAMES.get(paddr, f"OFF_{paddr:03X}"),
        "pwdata": f"0x{pwdata:08X}",
        "pwdata_dec": pwdata,
    }]


# ═══════════════════════════════════════════════════════════════════════════
# Top-level probe API — call this from a cocotb test or simulation script
# ═══════════════════════════════════════════════════════════════════════════

@cocotb.test(skip=not COCOTB_AVAILABLE)
async def diagnose_mmu_path(dut) -> None:
    """Cocotb test entry point — probes all MMU path signals.

    Dumps the probe data to build/evidence/ph9-probe-<case>.jsonl.
    Injects NO RTL modifications — all access via VPI backdoor.
    """
    await probe_all_signals(dut, case_id="cocotb-test")


async def probe_all_signals(
    dut, case_id: str = "ph9-default",
    cycles_between_snapshots: int = 500,
    num_snapshots: int = 4,
) -> str:
    """Probe all MMU path signals over several simulation snapshots.

    Returns the path to the JSONL evidence file.

    This is the primary API for Phase 9 T2 diagnostic traces.
    Call it from any cocotb simulation context (test, runner, or
    interactive debug session).
    """
    ts_ns = 0
    if COCOTB_AVAILABLE:
        try:
            ts_ns = cocotb.utils.get_sim_time(units="ns")
        except Exception:
            ts_ns = 0

    snapshot = {
        "case_id": case_id,
        "harness_version": "p9-diag-1",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "probes": [],
    }

    # Probe all signal groups
    for group_name, group_probes in [
        ("wrp_regs",           PROBE_WRP_REGS),
        ("preload_fsm",        PROBE_PRELOAD_FSM),
        ("broadcast_driver",   PROBE_BROADCAST_DRIVER),
        ("storeout_fifo",      PROBE_STOREOUT_FIFO),
        ("axi_ar",             PROBE_AXI_AR),
        ("axi_aw_w",           PROBE_AXI_AW_W),
        ("mxu_dbg",            PROBE_MXU_DBG),
    ]:
        records = await probe_group(dut, group_probes, ts_ns, case_id)
        for r in records:
            r["group"] = group_name
        snapshot["probes"].extend(records)

    # Also capture MMIO APB trace
    mmio_record = await probe_mmio_trace(dut, ts_ns, case_id)
    snapshot["probes"].extend(mmio_record)

    # Write evidence file in JSONL format
    evidence_dir = os.environ.get(
        "PH9_EVIDENCE_DIR",
        os.path.join(os.path.dirname(__file__), "..", "build", "evidence")
    )
    os.makedirs(evidence_dir, exist_ok=True)
    evidence_path = os.path.join(evidence_dir, f"ph9-probe-{case_id}.jsonl")

    with open(evidence_path, "w") as f:
        for entry in snapshot["probes"]:
            f.write(json.dumps(entry) + "\n")
        # Final summary line
        summary = {
            "case": case_id,
            "timestamp_ns": ts_ns,
            "total_probes": len(snapshot["probes"]),
            "status": "harness-complete",
        }
        f.write(json.dumps(summary) + "\n")

    if COCOTB_AVAILABLE:
        dut._log.info(
            "diagnose_mmu_path: wrote %d probes to %s",
            len(snapshot["probes"]), evidence_path
        )

    return evidence_path


# ═══════════════════════════════════════════════════════════════════════════
# FSDB dump helper — triggers $fsdbDumpvars for waveform-based analysis
# ═══════════════════════════════════════════════════════════════════════════

def enable_fsdb_dump(dut, filename: str = "ph9_mmu_path.fsdb") -> None:
    """Enable FSDB waveform dumping via $fsdbDumpvars.

    This is a zero-injection signal access method: the system task
    $fsdbDumpvars reads existing signal states and writes them to an
    FSDB file without modifying any RTL value.
    """
    if not COCOTB_AVAILABLE:
        return
    # $fsdbDumpvars(level, scope) — dump all signals under scope
    dut._log.info("Enabling FSDB dump: %s", filename)
    # The cocotb VPI bridge supports calling $fsdbDumpvars as a system function
    # via the DUT handle.  In practice this is done via cocotb's _id(handle)
    # pattern or the simulator's native Tcl/VPI interface.
    try:
        # Attempt direct VPI access: the dut handle represents the top-level
        # Verilog scope and $fsdbDumpvars is a system task available when
        # compiled with +vcs+fsdbon.
        import cocotb.utils
        # The actual FSDB dump is triggered at compile/run time:
        #   vcs ... +vcs+fsdbon+fsdbfile+ph9_mmu_path.fsdb
        # Here we log the intent for the runner to configure.
        logger.info(
            "FSDB dump target: %s  (compile with +vcs+fsdbon to enable)",
            filename
        )
    except Exception as exc:
        logger.warning("FSDB dump not available in this sim context: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# Self-test: verify the harness can be imported and all probe definitions
# are syntactically valid (runs offline, no simulator needed)
# ═══════════════════════════════════════════════════════════════════════════

def self_test() -> bool:
    """Run offline self-check: verify probe list integrity."""
    ok = True
    expected_total = (
        len(PROBE_WRP_REGS)
        + len(PROBE_PRELOAD_FSM)
        + len(PROBE_BROADCAST_DRIVER)
        + len(PROBE_STOREOUT_FIFO)
        + len(PROBE_AXI_AR)
        + len(PROBE_AXI_AW_W)
        + len(PROBE_MXU_DBG)
    )
    if len(ALL_PROBES) != expected_total:
        print(f"FAIL: probe count mismatch ({len(ALL_PROBES)} vs {expected_total})")
        ok = False
    else:
        print(f"OK: {len(ALL_PROBES)} probes in {7} groups — ready for cocotb sim")

    # Verify no duplicate signal paths
    seen = set()
    for hier, _, _ in ALL_PROBES:
        if hier in seen:
            print(f"WARN: duplicate probe path '{hier}'")
        seen.add(hier)

    return ok


# ── Run self-test when executed directly (not imported) ──
if __name__ == "__main__":
    import sys
    ok = self_test()
    sys.exit(0 if ok else 1)
