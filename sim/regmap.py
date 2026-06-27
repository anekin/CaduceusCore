"""
NPU MMIO Register Map — Func Model 与 RTL 共用规格。

每个模块 4KB 地址空间。寄存器 32-bit。
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, List, Tuple


# ══════════════════════════════════════════════════════════════════════
# Address map
# ══════════════════════════════════════════════════════════════════════

class Addr:
    """Base addresses (byte-addressable, 32-bit aligned)."""
    MXU_BASE     = 0x4000_0000
    SFU_BASE     = 0x4000_1000
    VECTOR_BASE  = 0x4000_2000
    DMA_BASE     = 0x4000_3000
    PCIE_BASE    = 0x4000_4000
    DOORBELL     = 0x4000_5000
    INTC_BASE    = 0x4000_6000
    DRAM_BASE    = 0x8000_0000   # DDR start
    SRAM_BASE    = 0x2000_0000   # Local SRAM start (4 MB)
    SRAM_SIZE    = 4 * 1024 * 1024  # 4 MB


# ══════════════════════════════════════════════════════════════════════
# MXU — Matrix Multiply Unit  (0x4000_0000)
# ══════════════════════════════════════════════════════════════════════

class MXU:
    BASE = Addr.MXU_BASE

    # Offsets
    CTRL    = 0x00   # R/W: 控制 [1:0]=dtype(0=INT4xINT8,1=INT8xINT8,2=BF16)
    CMD     = 0x04   # W:   bit[0]=START, bit[1]=ABORT
    STATUS  = 0x08   # R:   bit[0]=BUSY, bit[1]=DONE, bit[2]=ERROR
    DIM0    = 0x0C   # R/W: [15:0]=M, [31:16]=K (矩阵维度 A: M×K)
    DIM1    = 0x10   # R/W: [15:0]=N, [31:16]=reserved (矩阵维度 B: K×N)
    I_ADDR  = 0x14   # R/W: SRAM 中 activation 起始地址
    W_ADDR  = 0x18   # R/W: SRAM 中 weight 起始地址
    O_ADDR  = 0x1C   # R/W: SRAM 中 output 起始地址
    BIAS_ADDR = 0x20 # R/W: SRAM 中 bias 起始地址 (0 = no bias)
    SCALE_ADDR=0x24  # R/W: SRAM 中 scale 起始地址 (0 = no scale)
    IRQ_EN  = 0x28   # R/W: bit[0]=完成中断使能


# ══════════════════════════════════════════════════════════════════════
# SFU — Special Function Unit  (0x4000_1000)
# ══════════════════════════════════════════════════════════════════════

class SFU:
    BASE = Addr.SFU_BASE

    CTRL    = 0x00   # R/W: [3:0]=OP (0=SOFTMAX,1=LAYERNORM,2=GELU,3=RELU,4=SILU,5=ROPE,6=RMSNORM)
    CMD     = 0x04   # W:   bit[0]=START
    STATUS  = 0x08   # R:   bit[0]=BUSY, bit[1]=DONE
    I_ADDR  = 0x0C   # R/W: SRAM 中输入起始地址
    O_ADDR  = 0x10   # R/W: SRAM 中输出起始地址
    DIM     = 0x14   # R/W: [15:0]=elements, [31:16]=head_dim (ROPE)
    POS     = 0x18   # R/W: position (ROPE)
    IRQ_EN  = 0x1C   # R/W: bit[0]=完成中断使能


# ══════════════════════════════════════════════════════════════════════
# VECTOR — Vector Unit  (0x4000_2000)
# ══════════════════════════════════════════════════════════════════════

class VECTOR:
    BASE = Addr.VECTOR_BASE

    CTRL    = 0x00   # R/W: [3:0]=OP(0=ADD,1=MUL,2=MAX,3=SUM,4=CONV,5=RESID)
    CMD     = 0x04   # W:   bit[0]=START
    STATUS  = 0x08   # R:   bit[0]=BUSY, bit[1]=DONE
    A_ADDR  = 0x0C   # R/W: SRAM 中操作数 A 地址
    B_ADDR  = 0x10   # R/W: SRAM 中操作数 B 地址 (单目运算忽略)
    O_ADDR  = 0x14   # R/W: SRAM 中输出地址
    DIM     = 0x18   # R/W: [15:0]=elements
    IRQ_EN  = 0x1C   # R/W: bit[0]=完成中断使能


# ══════════════════════════════════════════════════════════════════════
# DMA — Direct Memory Access  (0x4000_3000)
# ══════════════════════════════════════════════════════════════════════

class DMA:
    BASE = Addr.DMA_BASE

    CTRL    = 0x00   # R/W: [0]=linked_list_en, [1:2]=channel_mode
    CMD     = 0x04   # W:   bit[0]=START, bit[1]=ABORT
    STATUS  = 0x08   # R:   bit[0]=BUSY, bit[1]=DONE, [7:4]=active_channel

    # Channel 0 — Weight/Data load
    CH0_SRC  = 0x10  # R/W: DRAM 源地址 (0x8000_0000+)
    CH0_DST  = 0x14  # R/W: SRAM 目标地址
    CH0_SIZE = 0x18  # R/W: 传输字节数
    CH0_STRIDE=0x1C  # R/W: 2D stride

    # Channel 1 — Output store
    CH1_SRC  = 0x20  # R/W: SRAM 源地址
    CH1_DST  = 0x24  # R/W: DRAM 目标地址
    CH1_SIZE = 0x28  # R/W: 传输字节数
    CH1_STRIDE=0x2C  # R/W: 2D stride

    # Descriptor mode (linked list)
    DESC_ADDR= 0x30  # R/W: 描述符链基地址 (DRAM)
    DESC_CNT = 0x34  # R/W: 描述符数量

    IRQ_EN  = 0x38   # R/W: bit[0]=完成中断使能


# ══════════════════════════════════════════════════════════════════════
# DOORBELL — Host↔NPU 通知  (0x4000_5000)
# ══════════════════════════════════════════════════════════════════════

class DOORBELL:
    BASE = Addr.DOORBELL

    HOST_TAIL = 0x00  # W: Host 写完 CMD 后更新 tail → 触发 NPU 唤醒
    NPU_HEAD  = 0x04  # R/W: NPU 固件更新 head（已消费到哪）
    HOST_HEAD = 0x08  # R: NPU 更新 → Host 看到完成
    NPU_TAIL  = 0x0C  # R: Host 更新 → NPU 看到新 CMD (只读)


# ══════════════════════════════════════════════════════════════════════
# INTC — 中断控制器  (0x4001_1000)
# ══════════════════════════════════════════════════════════════════════

class INTC:
    BASE = Addr.INTC_BASE

    PENDING  = 0x00  # R: 各模块中断 pending: bit0=MXU,1=SFU,2=VECTOR,3=DMA,8=HOST
    ENABLE   = 0x04  # R/W: 中断使能 mask
    THRESHOLD= 0x08  # R/W: 优先级阈值
    ACK      = 0x0C  # W: 写 1 清除对应中断


# ══════════════════════════════════════════════════════════════════════
# Validation
# ══════════════════════════════════════════════════════════════════════

def validate():
    """检查地址空间无冲突。"""
    regions: List[Tuple[str, int, int]] = [
        ("MXU",    Addr.MXU_BASE,    0x1000),
        ("SFU",    Addr.SFU_BASE,    0x1000),
        ("VECTOR", Addr.VECTOR_BASE, 0x1000),
        ("DMA",    Addr.DMA_BASE,    0x1000),
        ("DOORBELL", Addr.DOORBELL,  0x1000),
        ("INTC",   Addr.INTC_BASE,   0x1000),
    ]

    for i, (name_a, base_a, size_a) in enumerate(regions):
        for name_b, base_b, size_b in regions[i+1:]:
            if base_a < base_b + size_b and base_a + size_a > base_b:
                raise ValueError(f"地址冲突: {name_a} [{base_a:08x}] vs {name_b} [{base_b:08x}]")

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
    for mod_name, mod in [("MXU", MXU), ("SFU", SFU), ("VECTOR", VECTOR), ("DMA", DMA),
                           ("DOORBELL", DOORBELL), ("INTC", INTC)]:
        print(f"\n{mod_name} (0x{mod.BASE:08X}):")
        for attr in dir(mod):
            if attr.startswith('_') or attr == 'BASE':
                continue
            val = getattr(mod, attr)
            if isinstance(val, int):
                print(f"  +0x{val:04X}  {attr}")


if __name__ == "__main__":
    print_map()
