#!/usr/bin/env python3
"""Offline profile of todo-13 DRAM preload dirty-word counts.

Replays the exact wave-scheduling code of rtl_soc_segment_run.ibex_execute_layer
for layer 0 with a stub bridge, snapshotting the FuncModel 8 MB DRAM image
after each wave's schedule.  Reports how many 64-byte words differ from the
previous preloaded image (dirty) and how many are all-zero — the two numbers
that drive segment_preload cost.  No RTL involved.
"""
import asyncio
import sys
import time
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "ggml-npu"))

from func_model import FuncModel  # noqa: E402
from q4_dequant import load_weights_from_gguf  # noqa: E402
import spike_host as sh  # noqa: E402
import rtl_soc_segment_run as rsr  # noqa: E402

WB = 64


class StubBridge:
    """Minimal async stand-in for CocotbBridge used by ibex_execute_layer."""

    def __init__(self, model):
        self.model = model
        self.preloads = []
        self.last = None
        self._snapshot = None

    class _SimCycle:
        value = 0

    class _Dut:
        pass

    _Dut.sim_cycle = _SimCycle()
    dut = _Dut()

    async def segment_preload(self, dram, sram=b""):
        img = bytes(dram)
        arr = np.frombuffer(img, dtype=np.uint8)
        words = arr.reshape(-1, WB)
        nz = int(np.count_nonzero(np.any(words != 0, axis=1)))
        if self.last is None:
            dirty = len(words)
            full = True
        else:
            dirty = int(np.count_nonzero(
                np.any((arr != np.frombuffer(self.last, dtype=np.uint8))
                       .reshape(-1, WB), axis=1)))
            full = False
        self.preloads.append((len(words), dirty, nz, full))
        self.last = img

    async def segment_kick(self, host_tail):
        pass

    async def segment_wait(self, expected_head, timeout, poll):
        return True

    async def segment_read_dram(self, addr, length):
        return b"\x00" * length

    async def segment_read_head(self):
        return 0


async def main():
    model_path = "/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf"
    t0 = time.time()
    weights = load_weights_from_gguf(model_path)
    H = int(weights["blk.0.attn_norm.weight"].shape[0])
    I = int(weights["blk.0.ffn_gate.weight"].shape[0])
    QD = int(weights["blk.0.attn_q.weight"].shape[0])
    KD = int(weights["blk.0.attn_k.weight"].shape[0])
    dims = {"hidden_size": H, "intermediate_size": I, "q_dim": QD, "kv_dim": KD,
            "heads": QD // 128, "kv_heads": KD // 128, "head_dim": 128}
    model = FuncModel(dram_mb=8, sram_kb=4096)
    bridge = StubBridge(model)
    hidden = (np.random.rand(1, H).astype(np.float32) - 0.5) * 0.2

    print(f"[PROFILE] weights loaded in {time.time() - t0:.1f}s "
          f"H={H} I={I} QD={QD} KD={KD}")
    t0 = time.time()
    await rsr.ibex_execute_layer(bridge, model, hidden, weights, 0, dims,
                                 M=1, ring_offset=0)
    print(f"[PROFILE] layer 0 scheduling took {time.time() - t0:.1f}s")
    print(f"[PROFILE] dram image size = {len(model.dram)} B "
          f"({len(model.dram) // WB} words)")
    print("[PROFILE] per-wave: total_words dirty nonzero full")
    tot_dirty = 0
    for i, (total, dirty, nz, full) in enumerate(bridge.preloads):
        tot_dirty += dirty
        print(f"  wave {i + 1:2d}: {total:6d} {dirty:6d} {nz:6d} "
              f"{'FULL' if full else 'delta'}")
    print(f"[PROFILE] sum dirty words across 11 waves = {tot_dirty} "
          f"({tot_dirty * WB / 1e6:.2f} MB)")
    est_full_ms = 0.23  # ms/word from 30.1 s / 131072 words (completed run)
    print(f"[PROFILE] est preload @ {est_full_ms} ms/word: "
          f"full-handshake {tot_dirty * est_full_ms / 1e3:.0f}s, "
          f"streaming(0.5x) {tot_dirty * est_full_ms / 2e3:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
