"""
NPU MMIO Register Map — Compatibility facade over generated gen/npu_abi.py.

This file is no longer the authoritative source. All ABI constants come from
spec/npu_abi.json via scripts/gen_npu_abi.py → gen/npu_abi.py.
DO NOT edit register offsets here — update the schema and regenerate.
"""

import gen.npu_abi as _abi
from gen.npu_abi import (  # noqa: F401 — re-export for consumers
    EngineOp, OpCode, StatusCode, SFUOp, VectorOp, MXUDType,
    CAP_MXU_SUPPORTED, CAP_SFU_SUPPORTED, CAP_VECTOR_SUPPORTED,
    CAP_DMA_SUPPORTED, CAP_PCIE_SUPPORTED, CAP_INTC_SUPPORTED,
    CAP_DOORBELL_SUPPORTED, CAP_INT4_SUPPORTED, CAP_INT8_SUPPORTED,
    CAP_BF16_SUPPORTED, CAP_FP16_SUPPORTED, CAP_ROPE_SUPPORTED,
    CAP_RMSNORM_SUPPORTED, CAP_DESCRIPTOR_CHAIN,
    INTC_MXU, INTC_SFU, INTC_VECTOR, INTC_DMA, INTC_PCIE,
    INTC_HOST, INTC_TIMER,
    RING_ENTRIES, CMD_ENTRY_SIZE, COMPLETION_ENTRY_SIZE,
    DESC_DMA_COPY_SIZE, DESC_MMUL_SIZE, DESC_PCIE_DMA_SIZE,
    DESC_SFU_SIZE, DESC_VECTOR_SIZE,
)
from typing import Dict, List, Tuple


# ══════════════════════════════════════════════════════════════════════
# Address map — compatibility layer: provide both old _BASE names and
# new ABI-standard names, all derived from the generated contract.
# ══════════════════════════════════════════════════════════════════════

class Addr:
    """Base addresses (byte-addressable, 32-bit aligned)."""
    # ── Standard ABI names (match gen/npu_abi.py) ──
    BOOT_ROM      = 0x0000_0000
    IBEX_DMEM     = 0x0001_0000
    MXU           = 0x4000_0000
    SFU           = 0x4000_1000
    VECTOR        = 0x4000_2000
    DMA           = 0x4000_3000
    PCIE          = 0x4000_4000
    DOORBELL      = 0x4000_5000
    INTC          = 0x4000_6000
    PCIE_DMA      = 0x4000_7000
    SRAM          = 0x2000_0000
    DRAM          = 0x8000_0000

    # ── Backward-compatible _BASE aliases ──
    MXU_BASE      = MXU
    SFU_BASE      = SFU
    VECTOR_BASE   = VECTOR
    DMA_BASE      = DMA
    PCIE_BASE     = PCIE
    PCIE_DMA_BASE = PCIE_DMA
    DOORBELL_BASE = DOORBELL
    INTC_BASE     = INTC
    DRAM_BASE     = DRAM
    SRAM_BASE     = SRAM
    SRAM_SIZE     = 4 * 1024 * 1024  # 4 MB (not in gen Addr)


# ══════════════════════════════════════════════════════════════════════
# Runtime sanity — facade must match generated contract exactly
# ══════════════════════════════════════════════════════════════════════

def _check_facade_consistency() -> None:
    """Verify the facade addresses match the generated contract."""
    _checks: list[tuple[str, int, int]] = [
        ("MXU",      Addr.MXU,      _abi.Addr.MXU),
        ("SFU",      Addr.SFU,      _abi.Addr.SFU),
        ("VECTOR",   Addr.VECTOR,   _abi.Addr.VECTOR),
        ("DMA",      Addr.DMA,      _abi.Addr.DMA),
        ("PCIE",     Addr.PCIE,     _abi.Addr.PCIE),
        ("DOORBELL", Addr.DOORBELL, _abi.Addr.DOORBELL),
        ("INTC",     Addr.INTC,     _abi.Addr.INTC),
        ("PCIE_DMA", Addr.PCIE_DMA, _abi.Addr.PCIE_DMA),
        ("SRAM",     Addr.SRAM,     _abi.Addr.SRAM),
        ("DRAM",     Addr.DRAM,     _abi.Addr.DRAM),
        ("BOOT_ROM", Addr.BOOT_ROM, _abi.Addr.BOOT_ROM),
        ("IBEX_DMEM",Addr.IBEX_DMEM,_abi.Addr.IBEX_DMEM),
    ]
    for name, facade_val, gen_val in _checks:
        if facade_val != gen_val:
            raise AssertionError(
                f"regmap facade mismatch: Addr.{name}=0x{facade_val:08X} "
                f"but gen/npu_abi.py has 0x{gen_val:08X}"
            )

_check_facade_consistency()


# ══════════════════════════════════════════════════════════════════════
# Re-export module classes with explicit attributes for AST parsers
# (check_mmio_map.py uses Python AST, which cannot see imported attrs)
# ══════════════════════════════════════════════════════════════════════

class MXU:
    BASE = Addr.MXU
    CTRL        = 0x00
    CMD         = 0x04
    STATUS      = 0x08
    DIM0        = 0x0C
    DIM1        = 0x10
    I_ADDR      = 0x14
    W_ADDR      = 0x18
    O_ADDR      = 0x1C
    BIAS_ADDR   = 0x20
    SCALE_ADDR  = 0x24
    IRQ_EN      = 0x28


class SFU:
    BASE = Addr.SFU
    CTRL    = 0x00
    CMD     = 0x04
    STATUS  = 0x08
    I_ADDR  = 0x0C
    O_ADDR  = 0x10
    DIM     = 0x14
    POS     = 0x18
    IRQ_EN  = 0x1C


class VECTOR:
    BASE = Addr.VECTOR
    CTRL    = 0x00
    CMD     = 0x04
    STATUS  = 0x08
    A_ADDR  = 0x0C
    B_ADDR  = 0x10
    O_ADDR  = 0x14
    DIM     = 0x18
    IRQ_EN  = 0x1C


class DMA:
    BASE = Addr.DMA
    CTRL       = 0x00
    CMD        = 0x04
    STATUS     = 0x08
    CH0_SRC    = 0x10
    CH0_DST    = 0x14
    CH0_SIZE   = 0x18
    CH0_STRIDE = 0x1C
    CH1_SRC    = 0x20
    CH1_DST    = 0x24
    CH1_SIZE   = 0x28
    CH1_STRIDE = 0x2C
    DESC_ADDR  = 0x30
    DESC_CNT   = 0x34
    IRQ_EN     = 0x38


class DOORBELL:
    BASE = Addr.DOORBELL
    HOST_TAIL          = 0x00
    NPU_HEAD           = 0x04
    HOST_HEAD          = 0x08
    NPU_TAIL           = 0x0C
    LAST_STATUS        = 0x10
    COMPLETION_STATUS  = 0x14  # array of 16 uint32 per ABI; firmware uses up to 1024


class INTC:
    BASE = Addr.INTC
    PENDING   = 0x00
    ENABLE    = 0x04
    THRESHOLD = 0x08
    ACK       = 0x0C


class PCIE_DMA:
    BASE = Addr.PCIE_DMA
    CTRL         = 0x00
    STATUS       = 0x04
    PCIE_ADDR_LO = 0x08
    PCIE_ADDR_HI = 0x0C
    AXI_ADDR     = 0x10
    LEN          = 0x14
    TAG          = 0x18
    RD_ERR_CODE  = 0x1C
    WR_ERR_CODE  = 0x20


# ══════════════════════════════════════════════════════════════════════
# Runtime sanity for module offsets — match generated contract
# ══════════════════════════════════════════════════════════════════════

def _check_module_offsets() -> None:
    """Verify that facade module offsets match the generated contract."""
    _mod_checks: list[tuple[str, type, type]] = [
        ("MXU",      MXU,      _abi.MXU),
        ("SFU",      SFU,      _abi.SFU),
        ("VECTOR",   VECTOR,   _abi.VECTOR),
        ("DMA",      DMA,      _abi.DMA),
        ("DOORBELL", DOORBELL, _abi.DOORBELL),
        ("INTC",     INTC,     _abi.INTC),
        ("PCIE_DMA", PCIE_DMA, _abi.PCIE_DMA),
    ]
    for mod_name, facade_cls, gen_cls in _mod_checks:
        gen_attrs = {k: v for k, v in vars(gen_cls).items()
                     if not k.startswith('_')}
        for attr_name, gen_val in gen_attrs.items():
            if not isinstance(gen_val, int):
                continue
            facade_val = getattr(facade_cls, attr_name, None)
            if facade_val is None:
                # Facade has extra attrs (BASE) — not an error
                continue
            if isinstance(facade_val, int) and facade_val != gen_val:
                raise AssertionError(
                    f"regmap facade mismatch: {mod_name}.{attr_name}"
                    f"=0x{facade_val:02X} but gen has 0x{gen_val:02X}"
                )

_check_module_offsets()


# ══════════════════════════════════════════════════════════════════════
# Validation (unchanged from original)
# ══════════════════════════════════════════════════════════════════════

def validate():
    """检查地址空间无冲突。"""
    regions: List[Tuple[str, int, int]] = [
        ("MXU",      Addr.MXU,      0x1000),
        ("SFU",      Addr.SFU,      0x1000),
        ("VECTOR",   Addr.VECTOR,   0x1000),
        ("DMA",      Addr.DMA,      0x1000),
        ("PCIE",     Addr.PCIE,     0x1000),
        ("DOORBELL", Addr.DOORBELL, 0x1000),
        ("INTC",     Addr.INTC,     0x1000),
        ("PCIE_DMA", Addr.PCIE_DMA, 0x1000),
    ]

    for i, (name_a, base_a, size_a) in enumerate(regions):
        for name_b, base_b, size_b in regions[i+1:]:
            if base_a < base_b + size_b and base_a + size_a > base_b:
                raise ValueError(
                    f"地址冲突: {name_a} [{base_a:08x}]"
                    f" vs {name_b} [{base_b:08x}]"
                )

    print("✅ 地址空间无冲突")
    return regions


def print_map():
    """打印完整寄存器地址表。"""
    print(f"{'模块':10s} {'基地址':12s} {'大小':8s}")
    print("-" * 32)
    for name, base, size in validate():
        print(f"{name:10s} 0x{base:08X}  {size//1024}KB")
    print()

    # Per-module registers
    for mod_name, mod in [
        ("MXU", MXU), ("SFU", SFU), ("VECTOR", VECTOR),
        ("DMA", DMA), ("DOORBELL", DOORBELL), ("INTC", INTC),
        ("PCIE_DMA", PCIE_DMA),
    ]:
        print(f"\n{mod_name} (0x{mod.BASE:08X}):")
        for attr in dir(mod):
            if attr.startswith('_') or attr == 'BASE':
                continue
            val = getattr(mod, attr)
            if isinstance(val, int):
                print(f"  +0x{val:04X}  {attr}")


if __name__ == "__main__":
    print_map()
