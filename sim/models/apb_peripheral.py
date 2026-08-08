"""APB Peripheral register model — base class + 8 engine factory functions.

Provides a type-safe register field model for MMIO peripherals attached
to the APB decoder in rtl/soc/apb_decoder.v (8 slaves, 4 KB each).

Each factory function returns an ``APBPeripheral`` with the complete
register set matching ``sim/regmap.py`` offsets and access semantics.

Usage::

    from models.apb_peripheral import make_mxu_peripheral
    mxu = make_mxu_peripheral()
    mxu.write_field("DIM0", 0x1234)
    assert mxu.read(0x0C) == value
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Dict, List, Optional


# ══════════════════════════════════════════════════════════════════════════════
# Register field dataclass
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class RegisterField:
    """A single 32-bit register within a peripheral's 4 KB window.

    Attributes:
        name: Human-readable field name (e.g. ``"CTRL"``, ``"DIM0"``).
        offset: Byte offset from the peripheral base address.
        default: Power-on-reset value.
        access: ``'rw'`` (read-write), ``'r'`` (read-only), ``'w'`` (write-only),
                ``'w1c'`` (write-1-to-clear).
        callback: Optional callable invoked with ``(value)`` when a write
                  targets this field.  Only fired for ``'w'`` and ``'w1c'``
                  access modes.
    """

    name: str
    offset: int
    default: int = 0
    access: str = "rw"
    callback: Optional[Callable[[int], None]] = None

    def __post_init__(self) -> None:
        allowed = {"rw", "r", "w", "w1c"}
        if self.access not in allowed:
            raise ValueError(
                f"RegisterField access={self.access!r} must be one of {allowed}"
            )
        if self.offset < 0 or self.offset >= 0x1000:
            raise ValueError(
                f"RegisterField offset=0x{self.offset:03X} out of 4 KB window"
            )
        if self.offset % 4 != 0:
            raise ValueError(
                f"RegisterField offset=0x{self.offset:03X} not 32-bit aligned"
            )


# ══════════════════════════════════════════════════════════════════════════════
# APB Peripheral base class
# ══════════════════════════════════════════════════════════════════════════════

class APBPeripheral:
    """Base class for a 4 KB APB-attached register peripheral.

    Manages a register bank as a dict of ``RegisterField`` objects keyed
    by both byte offset and field name.  Read/write semantics honour the
    ``access`` attribute of each field.
    """

    def __init__(
        self,
        name: str,
        base_addr: int,
        fields: List[RegisterField],
    ) -> None:
        """Initialise peripheral.

        Args:
            name: Human-readable peripheral identifier (e.g. ``"MXU"``).
            base_addr: Base address in the SoC MMIO map.
            fields: Ordered list of ``RegisterField`` objects.  Duplicate
                    offsets are **not** checked — the last field at a given
                    offset wins in the offset-indexed dict.
        """
        self.name = name
        self.base_addr = base_addr

        self._fields: List[RegisterField] = list(fields)
        self._by_name: Dict[str, RegisterField] = {}
        self._by_offset: Dict[int, RegisterField] = {}
        self._values: Dict[int, int] = {}

        for f in self._fields:
            self._by_name[f.name] = f
            self._by_offset[f.offset] = f
            self._values[f.offset] = f.default

    # ── Public API ───────────────────────────────────────────────────────

    def read(self, offset: int) -> int:
        """Read the 32-bit register at *offset*.

        Args:
            offset: Byte offset (0 .. 0xFFC, 4-byte aligned).

        Returns:
            32-bit register value.

        Raises:
            ValueError: If *offset* is not mapped to any field.
        """
        if offset not in self._by_offset:
            raise ValueError(
                f"{self.name}: no register at offset 0x{offset:03X}"
            )
        return self._values.get(offset, 0) & 0xFFFF_FFFF

    def write(self, offset: int, value: int) -> None:
        """Write *value* to the 32-bit register at *offset*.

        Access semantics:
        - ``'rw'`` — value written.
        - ``'r'`` — write silently ignored (read-only).
        - ``'w'`` — value written, callback fired (value not readable back).
        - ``'w1c'`` — each '1' bit in *value* clears the corresponding bit
          in the register; callable fired.

        Args:
            offset: Byte offset (0 .. 0xFFC, 4-byte aligned).
            value: 32-bit write data (masked to 32 bits).

        Raises:
            ValueError: If *offset* is not mapped to any field.
        """
        if offset not in self._by_offset:
            raise ValueError(
                f"{self.name}: no register at offset 0x{offset:03X}"
            )

        field = self._by_offset[offset]
        masked = value & 0xFFFF_FFFF

        if field.access == "r":
            return  # read-only — silently ignore write

        if field.access == "w1c":
            # Write-1-to-clear: each '1' in 'value' clears the corresponding bit
            self._values[offset] &= ~masked
            self._values[offset] &= 0xFFFF_FFFF
        else:
            # 'rw' or 'w'
            self._values[offset] = masked

        if field.callback is not None and field.access in ("w", "w1c"):
            field.callback(masked)

    def read_field(self, name: str) -> int:
        """Read register value by *name*.

        Args:
            name: ``RegisterField.name`` (e.g. ``"CTRL"``).

        Returns:
            32-bit register value.

        Raises:
            KeyError: If *name* is not a known field.
        """
        f = self._by_name[name]
        return self._values.get(f.offset, 0) & 0xFFFF_FFFF

    def write_field(self, name: str, value: int) -> None:
        """Write register value by *name*.

        Delegates to :meth:`write` using the field's offset.

        Args:
            name: ``RegisterField.name``.
            value: 32-bit write data.

        Raises:
            KeyError: If *name* is not a known field.
        """
        f = self._by_name[name]
        self.write(f.offset, value)

    def __repr__(self) -> str:
        return (
            f"APBPeripheral(name={self.name!r}, base=0x{self.base_addr:08X}, "
            f"num_fields={len(self._fields)})"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Factory functions — one per APB slave (matching rtl/soc/apb_decoder.v)
# ══════════════════════════════════════════════════════════════════════════════


def make_mxu_peripheral() -> APBPeripheral:
    """Factory: MXU engine (slave 0, 0x4000_0000).

    Matches ``sim/regmap.py:MXU`` register offsets.
    """
    from regmap import Addr, MXU

    return APBPeripheral(
        name="MXU",
        base_addr=Addr.MXU_BASE,
        fields=[
            RegisterField("CTRL",     MXU.CTRL,     access="rw"),
            RegisterField("CMD",      MXU.CMD,      access="w"),
            RegisterField("STATUS",   MXU.STATUS,   access="r"),
            RegisterField("DIM0",     MXU.DIM0,     access="rw"),
            RegisterField("DIM1",     MXU.DIM1,     access="rw"),
            RegisterField("I_ADDR",   MXU.I_ADDR,   access="rw"),
            RegisterField("W_ADDR",   MXU.W_ADDR,   access="rw"),
            RegisterField("O_ADDR",   MXU.O_ADDR,   access="rw"),
            RegisterField("BIAS_ADDR", MXU.BIAS_ADDR, access="rw"),
            RegisterField("SCALE_ADDR", MXU.SCALE_ADDR, access="rw"),
            RegisterField("IRQ_EN",   MXU.IRQ_EN,   access="rw"),
        ],
    )


def make_sfu_peripheral() -> APBPeripheral:
    """Factory: SFU engine (slave 1, 0x4000_1000).

    Matches ``sim/regmap.py:SFU`` register offsets.
    """
    from regmap import Addr, SFU

    return APBPeripheral(
        name="SFU",
        base_addr=Addr.SFU_BASE,
        fields=[
            RegisterField("CTRL",    SFU.CTRL,    access="rw"),
            RegisterField("CMD",     SFU.CMD,     access="w"),
            RegisterField("STATUS",  SFU.STATUS,   access="r"),
            RegisterField("I_ADDR",  SFU.I_ADDR,  access="rw"),
            RegisterField("O_ADDR",  SFU.O_ADDR,  access="rw"),
            RegisterField("DIM",     SFU.DIM,     access="rw"),
            RegisterField("POS",     SFU.POS,     access="rw"),
            RegisterField("IRQ_EN",  SFU.IRQ_EN,  access="rw"),
        ],
    )


def make_vector_peripheral() -> APBPeripheral:
    """Factory: Vector engine (slave 2, 0x4000_2000).

    Matches ``sim/regmap.py:VECTOR`` register offsets.
    """
    from regmap import Addr, VECTOR

    return APBPeripheral(
        name="VECTOR",
        base_addr=Addr.VECTOR_BASE,
        fields=[
            RegisterField("CTRL",   VECTOR.CTRL,   access="rw"),
            RegisterField("CMD",    VECTOR.CMD,    access="w"),
            RegisterField("STATUS", VECTOR.STATUS, access="r"),
            RegisterField("A_ADDR", VECTOR.A_ADDR, access="rw"),
            RegisterField("B_ADDR", VECTOR.B_ADDR, access="rw"),
            RegisterField("O_ADDR", VECTOR.O_ADDR, access="rw"),
            RegisterField("DIM",    VECTOR.DIM,    access="rw"),
            RegisterField("IRQ_EN", VECTOR.IRQ_EN, access="rw"),
        ],
    )


def make_dma_peripheral() -> APBPeripheral:
    """Factory: DMA engine (slave 3, 0x4000_3000).

    Matches ``sim/regmap.py:DMA`` register offsets.
    """
    from regmap import Addr, DMA

    return APBPeripheral(
        name="DMA",
        base_addr=Addr.DMA_BASE,
        fields=[
            RegisterField("CTRL",       DMA.CTRL,       access="rw"),
            RegisterField("CMD",        DMA.CMD,        access="w"),
            RegisterField("STATUS",     DMA.STATUS,     access="r"),
            RegisterField("CH0_SRC",    DMA.CH0_SRC,    access="rw"),
            RegisterField("CH0_DST",    DMA.CH0_DST,    access="rw"),
            RegisterField("CH0_SIZE",   DMA.CH0_SIZE,   access="rw"),
            RegisterField("CH0_STRIDE", DMA.CH0_STRIDE, access="rw"),
            RegisterField("CH1_SRC",    DMA.CH1_SRC,    access="rw"),
            RegisterField("CH1_DST",    DMA.CH1_DST,    access="rw"),
            RegisterField("CH1_SIZE",   DMA.CH1_SIZE,   access="rw"),
            RegisterField("CH1_STRIDE", DMA.CH1_STRIDE, access="rw"),
            RegisterField("DESC_ADDR",  DMA.DESC_ADDR,  access="rw"),
            RegisterField("DESC_CNT",   DMA.DESC_CNT,   access="rw"),
            RegisterField("IRQ_EN",     DMA.IRQ_EN,     access="rw"),
        ],
    )


def make_pcie_peripheral() -> APBPeripheral:
    """Factory: PCIe EP (slave 4, 0x4000_4000).

    Uses fields from ``sim/models/pcie.py:PCIeState``, mapped to offsets
    inside the 4 KB window.  ``regmap.py`` does not define a PCIE register
    class (only the base address); the register layout here mirrors the
    ``pcie_ep_wrapper`` APB register block.
    """
    from regmap import Addr

    return APBPeripheral(
        name="PCIe",
        base_addr=Addr.PCIE_BASE,
        fields=[
            RegisterField("COMPLETER_ID",      0x00, default=0x0001, access="rw"),
            RegisterField("MAX_PAYLOAD_SIZE",   0x04, default=3,      access="rw"),
            RegisterField("MSIX_ENABLE",        0x08, default=0,      access="rw"),
            RegisterField("MSIX_VECTOR",        0x0C, default=0,      access="rw"),
            RegisterField("IRQ_ENABLE",         0x10, default=0,      access="rw"),
            RegisterField("IRQ_PENDING",        0x14, default=0,      access="rw"),
            RegisterField("BAR0_BASE",          0x18, default=0x2000_0000, access="rw"),
            RegisterField("BAR0_MASK",          0x1C, default=0x003F_FFFF, access="rw"),
            RegisterField("BAR1_BASE",          0x20, default=0x8000_0000, access="rw"),
            RegisterField("BAR1_MASK",          0x24, default=0x7FFF_FFFF, access="rw"),
        ],
    )


def make_doorbell_peripheral() -> APBPeripheral:
    """Factory: Doorbell (slave 5, 0x4000_5000).

    Matches ``sim/regmap.py:DOORBELL`` register offsets.
    """
    from regmap import Addr, DOORBELL

    return APBPeripheral(
        name="DOORBELL",
        base_addr=Addr.DOORBELL,
        fields=[
            RegisterField("HOST_TAIL",            DOORBELL.HOST_TAIL,            access="w"),
            RegisterField("NPU_HEAD",             DOORBELL.NPU_HEAD,             access="rw"),
            RegisterField("HOST_HEAD",            DOORBELL.HOST_HEAD,            access="r"),
            RegisterField("NPU_TAIL",             DOORBELL.NPU_TAIL,             access="r"),
            RegisterField("LAST_STATUS",          DOORBELL.LAST_STATUS,          access="rw"),
            RegisterField("COMPLETION_STATUS",    DOORBELL.COMPLETION_STATUS,    access="rw"),
        ],
    )


def make_intc_peripheral() -> APBPeripheral:
    """Factory: INTC (slave 6, 0x4000_6000).

    Matches ``sim/regmap.py:INTC`` register offsets.
    """
    from regmap import Addr, INTC

    return APBPeripheral(
        name="INTC",
        base_addr=Addr.INTC_BASE,
        fields=[
            RegisterField("PENDING",   INTC.PENDING,   access="r"),
            RegisterField("ENABLE",    INTC.ENABLE,    access="rw"),
            RegisterField("THRESHOLD", INTC.THRESHOLD, access="rw"),
            RegisterField("ACK",       INTC.ACK,       access="w1c"),
        ],
    )


def make_pcie_dma_peripheral() -> APBPeripheral:
    """Factory: PCIe DMA engine (slave 7, 0x4000_7000).

    Matches ``sim/regmap.py:PCIE_DMA`` register offsets.
    """
    from regmap import Addr, PCIE_DMA

    return APBPeripheral(
        name="PCIE_DMA",
        base_addr=Addr.PCIE_DMA_BASE,
        fields=[
            RegisterField("CTRL",          PCIE_DMA.CTRL,         access="rw"),
            RegisterField("STATUS",        PCIE_DMA.STATUS,       access="r"),
            RegisterField("PCIE_ADDR_LO",  PCIE_DMA.PCIE_ADDR_LO, access="rw"),
            RegisterField("PCIE_ADDR_HI",  PCIE_DMA.PCIE_ADDR_HI, access="rw"),
            RegisterField("AXI_ADDR",      PCIE_DMA.AXI_ADDR,     access="rw"),
            RegisterField("LEN",           PCIE_DMA.LEN,          access="rw"),
            RegisterField("TAG",           PCIE_DMA.TAG,          access="rw"),
            RegisterField("RD_ERR_CODE",   PCIE_DMA.RD_ERR_CODE,  access="r"),
            RegisterField("WR_ERR_CODE",   PCIE_DMA.WR_ERR_CODE,  access="r"),
        ],
    )
