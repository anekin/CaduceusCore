#!/usr/bin/env python3
"""
rtl_soc_l19_full.py — full L19 layer through the segment-run code path.

Calls rtl_soc_segment_run.ibex_execute_layer() with the clean spike L18 input
(identical to the segment run's L19 dispatch) and compares the resulting
hw l_out against the segment-run-stored L19 and the golden.
"""
import os
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "ggml-npu"))

import cocotb  # noqa: E402
from cocotb_bridge import CocotbBridge  # noqa: E402
from func_model import FuncModel  # noqa: E402
from q4_dequant import load_weights_from_gguf  # noqa: E402
import spike_host as sh  # noqa: E402
import rtl_soc_segment_run as segrun  # noqa: E402


def _cos(a, b):
    return sh._cosine_similarity(np.asarray(a, np.float64).ravel(),
                                 np.asarray(b, np.float64).ravel())


@cocotb.test()
async def test_l19_full(dut):
    bridge = CocotbBridge(dut)
    await bridge.start_clock()
    await bridge.reset(5)

    model_path = os.environ.get("QWEN3B_GGUF",
                                str(Path.home() / "models" / "qwen2.5-3b-instruct-q4_k_m.gguf"))
    print(f"[L19FULL] loading weights from {model_path}", flush=True)
    weights = load_weights_from_gguf(model_path)
    spike = np.load(str(_REPO / "build" / "evidence" / "ph10-36layer-spike.npz"),
                    allow_pickle=True)
    hidden = spike["layer_18_output"].astype(np.float32)
    ibex = np.load(str(_REPO / "build" / "evidence" / "ph10-36layer-ibex-checkpoints.npz"),
                   allow_pickle=True)
    l19_ref = ibex["hw_layer_19_output"].astype(np.float32).ravel()

    H = 2048
    I = 11008
    dims = {
        "hidden_size": H, "intermediate_size": I, "q_dim": 2048, "kv_dim": 256,
        "num_heads": 16, "num_kv_heads": 2, "heads": 16, "kv_heads": 2,
        "head_dim": 128, "num_hidden_layers": 36,
    }
    model = FuncModel(dram_mb=8, sram_kb=4096)
    print(f"[L19FULL] dispatching full L19 via ibex_execute_layer", flush=True)
    hw_l_out, consumed = await segrun.ibex_execute_layer(
        bridge, model, hidden, weights, 19, dims, M=1, ring_offset=0)

    l_out = hw_l_out.astype(np.float64).ravel()
    print(f"[L19FULL] l_out range [{l_out.min()},{l_out.max()}]", flush=True)
    print(f"[L19FULL] l_out vs segment-run-stored-L19 cos={_cos(l_out, l19_ref):.6f}",
          flush=True)
    golden_fp32 = sh._forward_layer(hidden, weights, 19, n_heads=16, n_kv_heads=2,
                                    head_dim=128)
    golden_l = np.rint(golden_fp32 * segrun.P10_RESID_SCALE)
    print(f"[L19FULL] l_out vs python golden cos={_cos(l_out, golden_l):.6f}",
          flush=True)
    print(f"[L19FULL] golden range [{golden_l.min()},{golden_l.max()}]", flush=True)
