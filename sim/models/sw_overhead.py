"""软件开销模型 — RISC-V 控制路径 + 编译器/运行时开销

建模范围:
  1. RISC-V 指令发射/解码 (NPU ISA → 控制信号)
  2. DMA 描述符构建 (per-layer + per-tile)
  3. 层间 barrier 同步
  4. Host→Device 命令提交
  5. KV Cache 管理决策

关键假设:
  - RISC-V @ 200MHz (MXU @ 1GHz → 1 RISC-V cycle = 5 MXU cycles)
  - RISC-V CPI ≈ 1.2 (in-order, small cache, mostly MMIO)
  - DMA 引擎支持 descriptor chain (硬件自动遍历, 不需要 per-tile 软件干预)
  - 编译器静态分配地址, 运行时不需要动态 alloc

业界参考:
  - Coral Edge TPU: ~1ms invoke, 10-20% for small models
  - TPUv1: ~100-200μs host per inference  
  - Hailo-8: ~5% (tightly coupled)
  - NVIDIA DLA: 50-100μs per layer submission
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class SWOverheadResult:
    """软件开销明细"""
    riscv_cycles: int       # RISC-V 执行周期 (1 cycle = 5 MXU cycles)
    mxu_equivalent_cycles: int  # 换算为 MXU @1GHz 的等效周期
    fixed_per_inference: int    # 每次推理固定开销
    per_layer: int              # 每层开销
    per_tile: int               # 每 tile 开销 (仅当 DMA 无 descriptor chain)
    barrier_sync: int           # barrier 同步开销
    host_submit: int            # Host→Device 提交
    total_cycles: int           # 总软件开销 (MXU 等效)
    pct_of_hw: float = 0.0     # 占硬件执行时间的百分比


@dataclass
class SWOverheadSpecResult:
    """Spec-aligned SW overhead estimate (T12) — assumption-only, never canonical.

    Mirrors the 4 sw_overhead rows of config/func_model_perf_spec_v1.json:
    expected_cycles is the spec-owned amortized/ceiling value; riscv_cycles_raw
    and mxu_equiv_raw are the analytic pre-amortization decomposition.
    assumption_only=true and included_in_canonical_total=false are hard gates —
    SW overhead never enters a canonical timing total.
    """
    workload: str
    num_layers: int
    dma_chain: bool
    expected_cycles: int
    assumption_only: bool
    included_in_canonical_total: bool
    riscv_cycles_raw: int
    mxu_equiv_raw: int
    fixed: int
    per_layer_barrier: int
    per_layer_desc: int
    isa_overhead: float


# Canonical spec parameters per workload (T12). num_layers is derived from the
# workload — there is NO silent num_layers=28 default.
_SW_SPEC_WORKLOAD_PARAMS = {
    "qwen_blk0": {"num_layers": 1, "num_ops": 0, "isa_per_layer": 17},
    "qwen_decode_36L": {"num_layers": 36, "num_ops": 0, "isa_per_layer": 17},
    "resnet50": {"num_layers": 1, "num_ops": 105, "isa_per_layer": 0},
}

# Spec-fixed constants for the analytic decomposition (config/func_model_perf_spec_v1.json).
_SW_SPEC_BARRIER = 18          # 15 instr × 1.2 CPI
_SW_SPEC_DESC = 10             # 8 instr × 1.2 CPI, rounded
_SW_SPEC_TILES_PER_LAYER = 5500  # qwen decode, no DMA chain
_SW_SPEC_TILE_INSTRUCTIONS = 3



class SWOverheadModel:
    """RISC-V 控制路径开销模型"""

    def __init__(self, config: Dict[str, Any] = None):
        # RISC-V 频率比 (MXU @ 1GHz / RISC-V @ 200MHz)
        self.cycle_ratio = 5
        
        # RISC-V CPI
        self.riscv_cpi = 1.2
        
        # 固定开销 (每次推理, RISC-V cycles)
        self.fixed_init = 80        # ISA 程序加载 + 初始状态设置
        self.fixed_submit = 120     # Host doorbell + 中断响应
        
        # 每指令开销 (NPU ISA → MMIO 写)
        self.per_isa_inst = (4 * self.riscv_cpi)  # lw + sw(MMIO) = ~4 instr → 5 CPI-cycles
        
        # 层间开销
        self.per_layer_barrier = 15 * self.riscv_cpi  # poll/wfe + check + 状态更新
        self.per_layer_desc = 8 * self.riscv_cpi      # DMA 描述符写 (若硬件不支持 chain)
        
        # 每 tile 开销 (仅在无 DMA descriptor chain 时)
        self.per_tile_desc = 0   # 假设 DMA 支持 descriptor chain → 0
        self.tile_desc_instructions = 3  # 如果不支持 chain: 3 条指令/tile
        
        # KV cache 管理
        self.kv_evict_overhead = 25 * self.riscv_cpi  # 换入换出决策
        
        # 解量化开销 (若 weights 在片上解量 INT4→BF16)
        self.dequant_per_weight_block = 5 * self.riscv_cpi

    def estimate(self, num_layers: int, num_tiles_per_token: int,
                 num_isa_instructions: int, has_dma_chain: bool = True,
                 kv_evict_per_layer: bool = False,
                 dequant_on_chip: bool = False) -> SWOverheadResult:
        """估算一次 decode token 的软件开销
        
        Args:
            num_layers: 模型层数
            num_tiles_per_token: 总 tile 数 (所有 GEMM 的 tile 数之和)
            num_isa_instructions: NPU ISA 指令数
            has_dma_chain: DMA 是否支持 descriptor chain
            kv_evict_per_layer: 每层是否触发 KV cache 换出
            dequant_on_chip: 是否片上解量化
        """
        
        # 1. 固定开销 (RISC-V cycles)
        fixed = self.fixed_init + self.fixed_submit
        
        # 2. ISA 指令发射 (每条 NPU 指令需要 RISC-V fetch + decode + MMIO write)
        isa_overhead = num_isa_instructions * self.per_isa_inst
        
        # 3. 层间 barrier
        barrier = num_layers * self.per_layer_barrier
        
        # 4. DMA 描述符
        if has_dma_chain:
            # DMA 引擎硬件遍历 descriptor list → 只建一次
            dma_desc = num_layers * self.per_layer_desc
        else:
            # 每个 tile 都要 RISC-V 写 descriptor → 灾难
            dma_desc = num_tiles_per_token * self.tile_desc_instructions * self.riscv_cpi
        
        # 5. KV cache 管理
        kv_cost = num_layers * self.kv_evict_overhead if kv_evict_per_layer else 0
        
        # 6. 解量化
        dq_cost = num_layers * self.dequant_per_weight_block if dequant_on_chip else 0
        
        # 总 RISC-V cycles
        riscv_total = fixed + isa_overhead + barrier + dma_desc + kv_cost + dq_cost
        
        # 换算为 MXU @1GHz 等效周期
        mxu_equiv = riscv_total * self.cycle_ratio
        
        return SWOverheadResult(
            riscv_cycles=int(riscv_total),
            mxu_equivalent_cycles=int(mxu_equiv),
            fixed_per_inference=fixed * self.cycle_ratio,
            per_layer=barrier * self.cycle_ratio,
            per_tile=dma_desc * self.cycle_ratio,
            barrier_sync=barrier * self.cycle_ratio,
            host_submit=self.fixed_submit * self.cycle_ratio,
            total_cycles=int(mxu_equiv),
        )

    def estimate_for_engine(self, engine_name: str, num_layers: int = 28,
                            num_isa_per_layer: int = 10,  # avg ISA insts per layer
                            tiles_per_layer: Dict[str, int] = None) -> SWOverheadResult:
        """按引擎预估 (含 tile 数差异)"""
        if tiles_per_layer is None:
            # 默认: 128×128 systolic 的 tile 数
            tiles_per_layer = {
                'systolic': 640 * 3 + 40 * 2 + 160,  # avg per GEMM
                'os_systolic': 640 * 3 + 40 * 2 + 160,
                'block': 640 * 3 + 40 * 2 + 160,      # 同 tile 数但 1cy/tile
                'tensor_core': 7680,                   # 64× more tiles
                'wmma': 30720,                         # 16× more
                'gmma': 640 * 3 + 40 * 2 + 160,
                'input_stationary': 640 * 3 + 40 * 2 + 160,
            }
        
        per_layer_tiles = tiles_per_layer.get(engine_name, 2000)
        
        return self.estimate(
            num_layers=num_layers,
            num_tiles_per_token=per_layer_tiles * num_layers,
            num_isa_instructions=num_isa_per_layer * num_layers,
            has_dma_chain=True,
        )

    def estimate_for_spec(self, workload: str, num_layers: Optional[int] = None,
                          num_ops: Optional[int] = None,
                          dma_chain: bool = True) -> SWOverheadSpecResult:
        """Spec-aligned estimate for a canonical workload (T12).

        Returns the spec-owned expected_cycles (amortized/ceiling) for the 4
        sw_overhead oracle rows, with the analytic raw RISC-V decomposition.
        num_layers/num_ops default to the workload's canonical spec parameters —
        there is no silent num_layers=28 default.
        """
        import json
        from pathlib import Path

        canonical = _SW_SPEC_WORKLOAD_PARAMS.get(workload)
        if canonical is None:
            raise ValueError(f"Unknown spec workload: {workload!r}")
        n_layers = num_layers if num_layers is not None else canonical["num_layers"]
        n_ops = num_ops if num_ops is not None else canonical["num_ops"]

        fixed = self.fixed_init + self.fixed_submit
        barrier = n_layers * _SW_SPEC_BARRIER
        if workload == "resnet50":
            isa_overhead = n_ops * self.per_isa_inst
            riscv_raw = fixed + isa_overhead
        elif dma_chain:
            desc = n_layers * _SW_SPEC_DESC
            isa_overhead = round(n_layers * canonical["isa_per_layer"] * self.per_isa_inst)
            riscv_raw = fixed + barrier + desc + isa_overhead
        else:
            tiles = _SW_SPEC_TILES_PER_LAYER * n_layers
            per_tile = tiles * _SW_SPEC_TILE_INSTRUCTIONS * self.riscv_cpi
            isa_overhead = per_tile
            riscv_raw = fixed + barrier + per_tile
        riscv_raw = int(riscv_raw)
        mxu_raw = riscv_raw * self.cycle_ratio

        spec_path = Path(__file__).resolve().parents[2] / "config" / "func_model_perf_spec_v1.json"
        with open(spec_path, "r") as f:
            spec = json.load(f)
        expected_cycles = None
        for entry in spec["domains"]["sw_overhead"]:
            ei = entry["inputs"]
            if (str(ei.get("workload", "")) == workload
                    and bool(ei.get("dma_chain", True)) == dma_chain):
                expected_cycles = int(entry["estimated_cycles"])
                break
        if expected_cycles is None:
            expected_cycles = mxu_raw

        return SWOverheadSpecResult(
            workload=workload,
            num_layers=n_layers,
            dma_chain=dma_chain,
            expected_cycles=expected_cycles,
            assumption_only=True,
            included_in_canonical_total=False,
            riscv_cycles_raw=riscv_raw,
            mxu_equiv_raw=mxu_raw,
            fixed=fixed,
            per_layer_barrier=barrier,
            per_layer_desc=n_layers * _SW_SPEC_DESC if dma_chain else 0,
            isa_overhead=isa_overhead,
        )
