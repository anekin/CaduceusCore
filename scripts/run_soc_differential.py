#!/usr/bin/env python3
"""run_soc_differential.py — Func Model / golden differential signoff runner.

Todo 14: [FEASIBILITY-ONLY] Run identical scenarios through the Func Model DUT
adapter and compare numerical outputs, visible memory effects, command order,
head/tail, completion, status/error, interrupt, and reset behavior against
independent golden oracles. RTL differential is deferred.

Usage:
    PYTHONPATH=sim python3 scripts/run_soc_differential.py \
        --matrix software-functional \
        --evidence .omo/evidence/task-14-differential.json
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "sim"))

import numpy as np

from gen.npu_abi import Addr, DMA, DOORBELL, INTC, MXU, SFU, VECTOR
from sim.verification import Action, FuncModelAdapter, Observation, Scenario
from sim.verification.differential import (
    GoldenExecutorOracle,
    MemoryGoldenOracle,
    run_differential_scenario,
)
from sim.verification.observation import ObservationType
from sim.verification.tolerance import ToleranceConfig

SRAM_BASE = Addr.SRAM
DRAM_BASE = Addr.DRAM


def _sram(addr: int) -> int:
    return SRAM_BASE + addr


def _dram(addr: int) -> int:
    return DRAM_BASE + addr


def build_software_functional_scenarios():
    """Build the software-functional differential scenario matrix.

    Covers APB, PCIe/BAR, MMUL, SFU, Vector, DMA, command ring, and fault.
    Each scenario pairs FuncModelAdapter actions with independent golden
    expectations computed by GoldenExecutorOracle or MemoryGoldenOracle.
    """

    scenarios = []
    golden_inputs: Dict[str, Dict[str, Any]] = {}

    tol_int32 = ToleranceConfig(int32_bit_exact=True)
    tol_fp16 = ToleranceConfig(int32_bit_exact=False, fp16_abs_tol=2e-3, fp16_rel_tol=1e-2)

    # ── 1. APB / MMIO frontdoor ─────────────────────────────────────
    mmio_value = 0x12345678
    s1 = Scenario(
        scenario_id="diff-apb-mmio",
        scenario_version=1,
        description="APB MMIO write/readback to MXU CTRL",
        actions=[
            Action.mmio_write(MXU.BASE + MXU.CTRL, mmio_value),
        ],
        expected_observations=[
            Observation.mmio_read("mxu_ctrl", MXU.BASE + MXU.CTRL, mmio_value),
        ],
        tolerance=tol_int32,
    )
    scenarios.append(s1)
    golden_inputs[s1.scenario_id] = {
        "oracle": "memory",
        "expected_specs": [
            {
                "observation_id": "mxu_ctrl",
                "observation_type": "mmio_value",
                "address": MXU.BASE + MXU.CTRL,
                "value": mmio_value,
            }
        ],
    }

    # ── 2. PCIe / BAR frontdoor ─────────────────────────────────────
    pcie_data = bytes(range(32))
    s2 = Scenario(
        scenario_id="diff-pcie-bar",
        scenario_version=1,
        description="PCIe TLP write to DRAM, read back via obs backdoor",
        actions=[
            Action.pcie_write(_dram(0x1000), pcie_data),
        ],
        expected_observations=[
            Observation(
                observation_id="pcie_dram",
                observation_type=ObservationType.dram_data,
                address=0x1000,
                size=len(pcie_data),
                data={"raw_hex": pcie_data.hex(), "dtype": "int32"},
            ),
        ],
        tolerance=tol_int32,
    )
    scenarios.append(s2)
    golden_inputs[s2.scenario_id] = {
        "oracle": "memory",
        "expected_specs": [
            {
                "observation_id": "pcie_dram",
                "observation_type": "dram_data",
                "address": 0x1000,
                "size": len(pcie_data),
                "raw_hex": pcie_data.hex(),
                "dtype": "int32",
            }
        ],
    }

    # ── 3. MMUL compute ─────────────────────────────────────────────
    M, K, N = 1, 8, 4
    rng = np.random.RandomState(42)
    act = rng.randint(-5, 6, size=M * K, dtype=np.int8).reshape(M, K)
    wgt_unpacked = rng.randint(-8, 8, size=K * N, dtype=np.int8)
    from sim.golden_executor import GoldenMXU
    wgt_packed = GoldenMXU.pack_int4(wgt_unpacked)

    act_off = 0x1000
    wgt_off = 0x2000
    out_off = 0x3000

    s3 = Scenario(
        scenario_id="diff-mmul",
        scenario_version=1,
        description="MMUL INT4xINT8→INT32 through MMIO frontdoor",
        actions=[
            Action.sram_preload(act_off, act.tobytes()),
            Action.sram_preload(wgt_off, wgt_packed.tobytes()),
            Action.mmio_write(MXU.BASE + MXU.CTRL, 0x0),
            Action.mmio_write(MXU.BASE + MXU.DIM0, (K << 16) | M),
            Action.mmio_write(MXU.BASE + MXU.DIM1, N),
            Action.mmio_write(MXU.BASE + MXU.I_ADDR, _sram(act_off)),
            Action.mmio_write(MXU.BASE + MXU.W_ADDR, _sram(wgt_off)),
            Action.mmio_write(MXU.BASE + MXU.O_ADDR, _sram(out_off)),
            Action.mmio_write(MXU.BASE + MXU.SCALE_ADDR, 0),
            Action.mmio_write(MXU.BASE + MXU.CMD, 0x1),
            Action.poll_status(MXU.BASE + MXU.STATUS, mask=0x2),
        ],
        expected_observations=[
            Observation.sram_readback("mmul_out", out_off, M * N * 4, dtype="int32"),
        ],
        tolerance=tol_int32,
    )
    scenarios.append(s3)
    golden_inputs[s3.scenario_id] = {
        "oracle": "executor",
        "kind": "mmul",
        "M": M,
        "K": K,
        "N": N,
        "activation": act.tolist(),
        "weight_packed": wgt_packed.tolist(),
        "observation_id": "mmul_out",
        "output_offset": out_off,
    }

    # ── 4. SFU Softmax compute ──────────────────────────────────────
    sfu_len = 16
    sfu_in = np.linspace(-1.0, 1.0, sfu_len, dtype=np.float32)
    sfu_in_off = 0x4000
    sfu_out_off = 0x5000

    s4 = Scenario(
        scenario_id="diff-sfu-softmax",
        scenario_version=1,
        description="SFU Softmax through MMIO frontdoor",
        actions=[
            Action.sram_preload(sfu_in_off, sfu_in.astype(np.float16).tobytes()),
            Action.mmio_write(SFU.BASE + SFU.CTRL, 0x0),  # op=0 softmax
            Action.mmio_write(SFU.BASE + SFU.I_ADDR, _sram(sfu_in_off)),
            Action.mmio_write(SFU.BASE + SFU.O_ADDR, _sram(sfu_out_off)),
            Action.mmio_write(SFU.BASE + SFU.DIM, sfu_len),
            Action.mmio_write(SFU.BASE + SFU.CMD, 0x1),
            Action.poll_status(SFU.BASE + SFU.STATUS, mask=0x2),
        ],
        expected_observations=[
            Observation.sram_readback("softmax_out", sfu_out_off, sfu_len * 2, dtype="fp16"),
        ],
        tolerance=tol_fp16,
    )
    scenarios.append(s4)
    golden_inputs[s4.scenario_id] = {
        "oracle": "executor",
        "kind": "sfu_softmax",
        "input": sfu_in.tolist(),
        "observation_id": "softmax_out",
        "output_offset": sfu_out_off,
    }

    # ── 5. Vector add compute ───────────────────────────────────────
    vec_len = 8
    a = np.arange(vec_len, dtype=np.int32)
    b = np.arange(vec_len, dtype=np.int32) * 2
    vec_a_off = 0x6000
    vec_b_off = 0x7000
    vec_o_off = 0x8000

    s5 = Scenario(
        scenario_id="diff-vector-add",
        scenario_version=1,
        description="Vector element-wise ADD through MMIO frontdoor",
        actions=[
            Action.sram_preload(vec_a_off, a.tobytes()),
            Action.sram_preload(vec_b_off, b.tobytes()),
            Action.mmio_write(VECTOR.BASE + VECTOR.CTRL, 0x0),  # op=0 add
            Action.mmio_write(VECTOR.BASE + VECTOR.A_ADDR, _sram(vec_a_off)),
            Action.mmio_write(VECTOR.BASE + VECTOR.B_ADDR, _sram(vec_b_off)),
            Action.mmio_write(VECTOR.BASE + VECTOR.O_ADDR, _sram(vec_o_off)),
            Action.mmio_write(VECTOR.BASE + VECTOR.DIM, vec_len),
            Action.mmio_write(VECTOR.BASE + VECTOR.CMD, 0x1),
            Action.poll_status(VECTOR.BASE + VECTOR.STATUS, mask=0x2),
        ],
        expected_observations=[
            Observation.sram_readback("vadd_out", vec_o_off, vec_len * 4, dtype="int32"),
        ],
        tolerance=tol_int32,
    )
    scenarios.append(s5)
    golden_inputs[s5.scenario_id] = {
        "oracle": "executor",
        "kind": "vector_vadd",
        "a": a.tolist(),
        "b": b.tolist(),
        "observation_id": "vadd_out",
        "output_offset": vec_o_off,
    }

    # ── 6. DMA copy ─────────────────────────────────────────────────
    dma_data = bytes(range(16))
    dma_dram_off = 0x3000
    dma_sram_off = 0x9000

    s6 = Scenario(
        scenario_id="diff-dma-copy",
        scenario_version=1,
        description="DMA load DRAM→SRAM through MMIO frontdoor",
        actions=[
            Action.dram_preload(dma_dram_off, dma_data),
            Action.mmio_write(DMA.BASE + DMA.CH0_SRC, _dram(dma_dram_off)),
            Action.mmio_write(DMA.BASE + DMA.CH0_DST, _sram(dma_sram_off)),
            Action.mmio_write(DMA.BASE + DMA.CH0_SIZE, len(dma_data)),
            Action.mmio_write(DMA.BASE + DMA.CMD, 0x1),
            Action.poll_status(DMA.BASE + DMA.STATUS, mask=0x2),
        ],
        expected_observations=[
            Observation(
                observation_id="dma_sram",
                observation_type=ObservationType.sram_data,
                address=dma_sram_off,
                size=len(dma_data),
                data={"raw_hex": dma_data.hex(), "dtype": "int32"},
            ),
        ],
        tolerance=tol_int32,
    )
    scenarios.append(s6)
    golden_inputs[s6.scenario_id] = {
        "oracle": "executor",
        "kind": "dma_copy",
        "data_hex": dma_data.hex(),
        "observation_id": "dma_sram",
        "dst_offset": dma_sram_off,
    }

    # ── 7. Command ring / doorbell + IRQ ────────────────────────────
    s7 = Scenario(
        scenario_id="diff-command-ring",
        scenario_version=1,
        description="Host doorbell write triggers firmware dispatch path",
        actions=[
            Action.doorbell(host_tail=1),
            Action.wait_irq(source=8),
        ],
        expected_observations=[
            Observation(
                observation_id="host_tail",
                observation_type=ObservationType.mmio_value,
                address=DOORBELL.BASE + DOORBELL.HOST_TAIL,
                data={"value": 1},
            ),
        ],
        tolerance=tol_int32,
    )
    scenarios.append(s7)
    golden_inputs[s7.scenario_id] = {
        "oracle": "memory",
        "expected_specs": [
            {
                "observation_id": "host_tail",
                "observation_type": "mmio_value",
                "address": DOORBELL.BASE + DOORBELL.HOST_TAIL,
                "value": 1,
            }
        ],
    }

    # ── 8. Fault-injected data corruption ───────────────────────────
    corrupt_data = b"\xAA" * 16
    corrupt_off = 0xA000
    s8 = Scenario(
        scenario_id="diff-fault-data-corruption",
        scenario_version=1,
        description="Fault-injected SRAM data corruption detected as divergence",
        actions=[
            Action.sram_preload(corrupt_off, corrupt_data),
        ],
        expected_observations=[
            Observation(
                observation_id="sram_corrupt",
                observation_type=ObservationType.sram_data,
                address=corrupt_off,
                size=len(corrupt_data),
                data={"raw_hex": corrupt_data.hex(), "dtype": "int32"},
            ),
        ],
        tolerance=tol_int32,
        metadata={
            "fault_class": "data_corruption",
            "fault_params": {"offset": 0, "count": 4},
            "expected_classification": "data_corruption",
        },
    )
    scenarios.append(s8)
    golden_inputs[s8.scenario_id] = {
        "oracle": "memory",
        "expected_specs": [
            {
                "observation_id": "sram_corrupt",
                "observation_type": "sram_data",
                "address": corrupt_off,
                "size": len(corrupt_data),
                "raw_hex": corrupt_data.hex(),
                "dtype": "int32",
            }
        ],
    }

    return scenarios, golden_inputs


def _make_oracle(name: str):
    if name == "executor":
        return GoldenExecutorOracle()
    return MemoryGoldenOracle()


async def run_differential_matrix(
    matrix: str,
    evidence_path: str,
    firmware: str = "python",
):
    if matrix != "software-functional":
        raise ValueError(f"Unsupported matrix: {matrix}")

    scenarios, golden_inputs = build_software_functional_scenarios()

    adapter = FuncModelAdapter(firmware_mode=firmware)
    await adapter.connect()
    await adapter.reset()

    reports = []
    try:
        for scenario in scenarios:
            inputs = golden_inputs[scenario.scenario_id]
            oracle = _make_oracle(inputs["oracle"])
            report = await run_differential_scenario(adapter, scenario, oracle, inputs)
            reports.append(report)
            status = "PASS" if report.gate_pass else "FAIL"
            print(f"  [{scenario.scenario_id}] {status}")
    finally:
        await adapter.disconnect()

    os.makedirs(os.path.dirname(evidence_path) or ".", exist_ok=True)

    evidence = {
        "task": "task-14-differential",
        "phase": "feasibility-only",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dut_type": "fm",
        "firmware_mode": firmware,
        "test_matrix": matrix,
        "scenarios_total": len(scenarios),
        "scenarios_pass": sum(1 for r in reports if r.gate_pass),
        "scenarios_fail": sum(1 for r in reports if not r.gate_pass),
        "scenarios_error": 0,
        "records": [r.to_dict() for r in reports],
        "scenario_details": {
            s.scenario_id: {
                **s.to_dict(),
                "metadata": {
                    **s.metadata,
                    "scenario_content_hash": "",
                },
            }
            for s in scenarios
        },
    }

    with open(evidence_path, "w") as f:
        json.dump(evidence, f, indent=2, sort_keys=True)

    total = len(reports)
    passed = sum(1 for r in reports if r.gate_pass)
    failed = total - passed
    print(f"\n=== Results ===")
    print(f"  Total: {total}, Pass: {passed}, Fail: {failed}")
    print(f"  Evidence: {evidence_path}")

    return 0 if failed == 0 else 1


def main():
    parser = argparse.ArgumentParser(
        description="Func Model / golden differential signoff runner (Todo 14)"
    )
    parser.add_argument(
        "--matrix",
        default="software-functional",
        help="Test matrix to run",
    )
    parser.add_argument(
        "--evidence",
        default=".omo/evidence/task-14-differential.json",
        help="Path for evidence JSON output",
    )
    parser.add_argument(
        "--firmware",
        choices=["python", "spike"],
        default="python",
        help="Firmware mode for FuncModelAdapter",
    )
    args = parser.parse_args()

    return asyncio.run(run_differential_matrix(
        args.matrix, args.evidence, firmware=args.firmware,
    ))


if __name__ == "__main__":
    sys.exit(main())
