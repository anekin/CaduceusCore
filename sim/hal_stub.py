#!/usr/bin/env python3
"""HAL Stub — 演示 L3 接口：IREE HAL → NPU Simulator 的对接层

这是 IREE HAL 后端的简化原型。实际 IREE HAL 适配需要 C API，
这里用 Python mock 展示接口契约和 auto-tuning 反馈循环。

流程:
  模型 (PyTorch) → IREE Compiler → NPU ISA → HAL Stub → Simulator
                                                    ↓
                                            性能反馈 → Compiler (re-tile)
"""

import json
from pathlib import Path
from typing import List, Dict, Any


class NPUHALStub:
    """IREE HAL NPU backend 的 Python 原型。

    真实版本需要用 IREE 的 C HAL API (iree/hal/api.h)，
    注册 NPU 驱动 → 创建 device → 提交 command buffers。
    这里是等价概念演示。
    """

    def __init__(self, sim_config: str = None):
        self.commands: List[Dict] = []
        self.stats: Dict[str, Any] = {}
        self._tiling_strategy = 64  # default tiling

    # ── HAL API (概念等价) ──────────────────────────────────────

    def create_device(self, device_id: int = 0):
        """iree_hal_create_device('npu', &device)"""
        return {"device_id": device_id, "type": "npu-sim"}

    def allocate_buffer(self, size_bytes: int, usage: str = "input"):
        """iree_hal_allocator_allocate_buffer()"""
        return {"addr": hash(usage) & 0xFFFF, "size": size_bytes, "usage": usage}

    def submit_command_buffer(self, commands: List[Dict]):
        """iree_hal_semaphore_signal() after submitting work.

        In real IREE: serialize NPU ISA → write to command buffer →
        submit to device queue → signal semaphore on completion.
        """
        self.commands = commands

    # ── Compiler feedback loop (auto-tuning) ────────────────────

    def run_with_tiling(self, tiling_size: int, layer_dims: Dict) -> Dict:
        """Run one layer with given tiling strategy, return performance.

        This is what the IREE compiler's auto-tuner would call:
        try tiling=64, run sim → try tiling=128, run sim → pick best.
        """
        # Simulate compiling a layer with this tiling
        M, K, N = layer_dims["M"], layer_dims["K"], layer_dims["N"]
        num_tiles = (K + tiling_size - 1) // tiling_size

        # Sim: larger tiles = better utilization, but more contention
        utilization = min(0.95, tiling_size / self._get_optimal_tile(K, N))
        compute_cycles = M * K * N / (128 * 128 * 2) / utilization
        stall_cycles = compute_cycles * (1 - utilization)

        return {
            "tiling": tiling_size,
            "num_tiles": num_tiles,
            "utilization": round(utilization, 3),
            "compute_cycles": int(compute_cycles),
            "stall_cycles": int(stall_cycles),
            "total_cycles": int(compute_cycles + stall_cycles),
        }

    def auto_tune(self, layer_dims: Dict, search_space: List[int] = None) -> Dict:
        """IREE auto-tuner: try tilings, pick best.

        Real IREE does this via iree_hal_npu_auto_tune() extension.
        """
        if search_space is None:
            search_space = [32, 64, 128, 256]

        results = []
        for tiling in search_space:
            r = self.run_with_tiling(tiling, layer_dims)
            results.append(r)

        best = min(results, key=lambda r: r["total_cycles"])
        worst = max(results, key=lambda r: r["total_cycles"])
        improvement = (worst["total_cycles"] - best["total_cycles"]) / worst["total_cycles"] * 100

        return {
            "best": best,
            "worst": worst,
            "improvement_pct": round(improvement, 1),
            "all": results,
        }

    @staticmethod
    def _get_optimal_tile(K: int, N: int) -> int:
        """Optimal tile size for given matrix dimensions."""
        # Weight-stationary: prefer tiles that fit SRAM (2MB for weights)
        # Each PE stores weight: ceil(K*N/(128*128)) values
        sram_per_tile = K * N * 4 / 8  # bytes for INT4 weights
        max_tile = 2 * 1024 * 1024 / (sram_per_tile / K)  # very rough
        return min(256, max(32, int(max_tile)))


def demo_hal_workflow():
    """完整 IREE HAL 工作流演示。"""
    print("=" * 60)
    print("  IREE HAL → NPU Simulator Integration Demo")
    print("=" * 60)

    hal = NPUHALStub()

    # 1. Create device
    device = hal.create_device(0)
    print(f"\n[1] Device created: {device}")

    # 2. Allocate buffers
    input_buf = hal.allocate_buffer(2560 * 4, "input")
    weight_buf = hal.allocate_buffer(2560 * 4096, "weight")
    output_buf = hal.allocate_buffer(2560 * 4, "output")
    print(f"[2] Buffers allocated: input={input_buf['size']}B, "
          f"weight={weight_buf['size']}B, output={output_buf['size']}B")

    # 3. Compile layer to NPU ISA (from compiler step)
    isa_commands = [
        {"op": "DMA_LD", "dram": weight_buf["addr"], "sram": 0, "size": weight_buf["size"]},
        {"op": "DMA_LD", "dram": input_buf["addr"], "sram": 0x20000, "size": input_buf["size"]},
        {"op": "MMUL", "wa": 0, "ia": 0x20000, "oa": 0x40000, "N": 4096},
        {"op": "DMA_ST", "sram": 0x40000, "dram": output_buf["addr"], "size": output_buf["size"]},
    ]
    print(f"[3] ISA program: {len(isa_commands)} instructions")

    # 4. Submit to device
    hal.submit_command_buffer(isa_commands)
    print(f"[4] Command buffer submitted")

    # 5. Auto-tune tiling for Q projection layer
    print(f"\n[5] Auto-tuning Q projection (1×2560×4096)...")
    layer_dims = {"M": 1, "K": 2560, "N": 4096}
    tune_result = hal.auto_tune(layer_dims)
    print(f"    Best tiling: {tune_result['best']['tiling']} "
          f"(util={tune_result['best']['utilization']:.1%})")
    print(f"    Improvement: {tune_result['improvement_pct']:.1f}% over worst")

    # 6. Show all tiling results
    print(f"\n    Tiling scan:")
    print(f"    {'Tiling':>8s} {'Util':>6s} {'Compute':>10s} {'Total':>10s}")
    for r in tune_result["all"]:
        marker = " ← BEST" if r == tune_result["best"] else ""
        print(f"    {r['tiling']:>8d} {r['utilization']:>5.1%} "
              f"{r['compute_cycles']:>10d} {r['total_cycles']:>10d}{marker}")

    print(f"\n[HAL Demo complete] ✓")


if __name__ == "__main__":
    demo_hal_workflow()
