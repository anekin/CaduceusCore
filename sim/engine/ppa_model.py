"""PPA 模型 — 面积/功耗/性能 综合评估"""

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class PPA:
    """Performance, Power, Area"""
    tok_s: float
    area_mm2: float
    power_w: float
    efficiency_tok_per_watt: float = 0.0
    efficiency_tok_per_mm2: float = 0.0
    config_label: str = ""

    def __post_init__(self):
        self.efficiency_tok_per_watt = self.tok_s / max(self.power_w, 0.1)
        self.efficiency_tok_per_mm2 = self.tok_s / max(self.area_mm2, 0.1)

    def __repr__(self):
        return (f"PPA(tok={self.tok_s:.0f}/s, {self.area_mm2:.0f}mm², "
                f"{self.power_w:.1f}W, {self.efficiency_tok_per_watt:.1f}tok/W)")


class AreaModel:
    """面积估算模型 — 基于配置参数"""

    def __init__(self, config: Dict[str, Any]):
        am = config.get("area_model", {})
        self.systolic_pe_baseline = float(am.get("systolic_pe_area_mm2", 8.0))
        self.block_pe_baseline = float(am.get("block_pe_area_mm2", 32.0))
        self.os_pe_baseline = float(am.get("os_pe_area_mm2", 32.0))
        self.input_stationary_pe_baseline = float(am.get("is_pe_area_mm2", 24.0))
        self.tensor_core_pe_baseline = float(am.get("tc_pe_area_mm2", 32.0))
        self.sfu = float(am.get("sfu_area_mm2", 2.0))
        self.l1_per_kb = float(am.get("l1_sram_per_kb_mm2", 0.002))
        self.l2_per_kb = float(am.get("l2_sram_per_kb_mm2", 0.0015))
        self.dma = float(am.get("dma_area_mm2", 1.0))
        self.riscv = float(am.get("riscv_area_mm2", 1.0))
        self.pcie = float(am.get("pcie_area_mm2", 3.5))
        self.dram_phy = float(am.get("dram_phy_area_mm2", 7.0))
        self.crossbar = float(am.get("crossbar_area_mm2", 1.5))
        self.dma_per_ch = float(am.get("dma_channels_area_per_channel_mm2", 0.5))

    def estimate(self, config: Dict[str, Any], engine_type: str) -> float:
        """估算总面积"""
        mac = config.get("mac_engine", {})
        H = int(mac.get("array_height", 128))
        W = int(mac.get("array_width", 128))
        scale = (H * W) / (128 * 128)

        # PE array
        if engine_type == "systolic":
            pe_area = self.systolic_pe_baseline * scale
        else:  # block
            pe_area = self.block_pe_baseline * scale

        # SRAM
        sram = config.get("sram", {})
        l1 = float(sram.get("l1_per_core_kb", 512)) * self.l1_per_kb
        l2 = float(sram.get("l2_shared_kb", 2048)) * self.l2_per_kb

        # DMA channels
        dma_cfg = config.get("dma", {})
        dma_channels = int(dma_cfg.get("channels", 2))
        opts = config.get("optimizations", {})
        if float(opts.get("dma_bw_multiplier", 1.0)) >= 2.0:
            # 128-bit DRAM or 4ch DMA
            dma_channels = max(dma_channels, 4)

        dma_area = self.dma + (dma_channels - 2) * self.dma_per_ch

        # DRAM PHY: wider bus = larger PHY
        mem = config.get("memory", {})
        dram_width = int(mem.get("dram_width_bits", 64))
        dram_phy_area = self.dram_phy * (dram_width / 64)

        total = (pe_area + self.sfu + self.riscv + self.pcie +
                 self.crossbar + l1 + l2 + dma_area + dram_phy_area)

        return round(total, 1)


class PowerModel:
    """功耗估算模型 — 粗略 but proportional"""

    def __init__(self, config: Dict[str, Any]):
        # 12nm: ~0.5 W/mm² for logic, ~0.1 W/mm² for SRAM (active)
        self.logic_power_density = 0.5   # W/mm²
        self.sram_power_density = 0.1    # W/mm²
        self.dram_phy_power = 3.0        # W (fixed overhead)

    def estimate(self, area_model: AreaModel, config: Dict[str, Any],
                 engine_type: str) -> float:
        """粗略功耗估算"""
        mac = config.get("mac_engine", {})
        H = int(mac.get("array_height", 128))
        W = int(mac.get("array_width", 128))
        scale = (H * W) / (128 * 128)
        freq_scale = float(mac.get("frequency_mhz", 1000)) / 1000

        # Logic power
        if engine_type == "systolic":
            logic_mm2 = area_model.systolic_pe_baseline * scale + area_model.sfu
        else:
            logic_mm2 = area_model.block_pe_baseline * scale + area_model.sfu

        logic_power = logic_mm2 * self.logic_power_density * freq_scale

        # SRAM power
        sram = config.get("sram", {})
        sram_kb = float(sram.get("l1_per_core_kb", 512)) + float(sram.get("l2_shared_kb", 2048))
        sram_mm2 = sram_kb * area_model.l1_per_kb  # rough
        sram_power = sram_mm2 * self.sram_power_density

        # DRAM bandwidth proportional power
        mem = config.get("memory", {})
        bw_ratio = float(mem.get("bandwidth_gbps", 51.2)) / 51.2
        dram_power = self.dram_phy_power * bw_ratio

        total = logic_power + sram_power + dram_power + 2.0  # +2W misc
        return round(total, 1)
