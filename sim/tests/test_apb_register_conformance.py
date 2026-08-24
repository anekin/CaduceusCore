"""APB register conformance replay gate (SOC-15).

Replays write->readback sequences against every peripheral factory in
``models/apb_peripheral.py`` and asserts that observed register semantics
(rw / r / w / w1c) are consistent with the field access modes declared by
the factories and with the offsets published by ``regmap.py``.

This gate intentionally does NOT refactor ``MMIOBridge.handle`` — it pins the
existing factory behaviour with a black-box replay so any future divergence
between ``regmap.py`` and the peripheral model fails fast.

Coverage groups (8, one per APB slave):
    MXU, SFU, VECTOR, DMA, PCIe, DOORBELL, INTC, PCIE_DMA
"""

import pytest

import regmap
from models.apb_peripheral import (
    make_mxu_peripheral,
    make_sfu_peripheral,
    make_vector_peripheral,
    make_dma_peripheral,
    make_pcie_peripheral,
    make_doorbell_peripheral,
    make_intc_peripheral,
    make_pcie_dma_peripheral,
)


# ══════════════════════════════════════════════════════════════════════════════
# Peripheral factory specs — (name, factory, regmap module class, Addr attr)
# ══════════════════════════════════════════════════════════════════════════════

FACTORY_SPECS = [
    ("MXU",      make_mxu_peripheral,      regmap.MXU,      "MXU"),
    ("SFU",      make_sfu_peripheral,      regmap.SFU,      "SFU"),
    ("VECTOR",   make_vector_peripheral,   regmap.VECTOR,   "VECTOR"),
    ("DMA",      make_dma_peripheral,      regmap.DMA,      "DMA"),
    ("PCIe",     make_pcie_peripheral,     None,            "PCIE"),
    ("DOORBELL", make_doorbell_peripheral, regmap.DOORBELL, "DOORBELL"),
    ("INTC",     make_intc_peripheral,     regmap.INTC,     "INTC"),
    ("PCIE_DMA", make_pcie_dma_peripheral, regmap.PCIE_DMA, "PCIE_DMA"),
]

FACTORY_IDS = [spec[0] for spec in FACTORY_SPECS]


def _field_names(factory):
    """Field names exposed by a peripheral factory."""
    return [f.name for f in factory()._fields]


# Factories exposing a CTRL / STATUS register (used by happy + failure tests).
CTRL_SPECS = [
    (name, factory)
    for name, factory, _, _ in FACTORY_SPECS
    if "CTRL" in _field_names(factory)
]
STATUS_SPECS = [
    (name, factory)
    for name, factory, _, _ in FACTORY_SPECS
    if "STATUS" in _field_names(factory)
]


# ══════════════════════════════════════════════════════════════════════════════
# Replay engine
# ══════════════════════════════════════════════════════════════════════════════

def replay_write_readback(periph):
    """Replay a write->readback sequence per field and assert semantics.

    For every field of *periph*:
    - ``rw``   — write 0x3 then 0x6; readback must equal the last written
                 value (proves store + overwrite, not OR-accumulate).
    - ``r``    — hostile write of 0xFFFF_FFFF; readback must be unchanged.
    - ``w``    — write 0x42; the model stores the value (matches documented
                 APBPeripheral behaviour for write-only fields).
    - ``w1c``  — seed 0xFFFF, write 0x00F0; only bits 4..7 may clear.

    Returns a list of ``(field_name, access, observed_value)`` entries so
    callers can assert coverage depth.
    """
    log = []
    for field in periph._fields:
        if field.access == "rw":
            periph.write(field.offset, 0x3)
            assert periph.read(field.offset) == 0x3, (
                f"{periph.name}.{field.name}: rw write 0x3 not readable"
            )
            periph.write(field.offset, 0x6)
            assert periph.read(field.offset) == 0x6, (
                f"{periph.name}.{field.name}: rw overwrite failed "
                "(OR-accumulate or stuck bits)"
            )
            log.append((field.name, "rw", 0x6))
        elif field.access == "r":
            before = periph.read(field.offset)
            periph.write(field.offset, 0xFFFF_FFFF)
            after = periph.read(field.offset)
            assert after == before, (
                f"{periph.name}.{field.name}: read-only register was "
                f"modified (0x{before:08X} -> 0x{after:08X})"
            )
            log.append((field.name, "r", before))
        elif field.access == "w":
            periph.write(field.offset, 0x42)
            assert periph.read(field.offset) == 0x42, (
                f"{periph.name}.{field.name}: write-only field did not "
                "store value"
            )
            log.append((field.name, "w", 0x42))
        elif field.access == "w1c":
            # w1c only clears bits — seed the register to observe behaviour.
            periph._values[field.offset] = 0xFFFF
            periph.write(field.offset, 0x00F0)
            assert periph.read(field.offset) == 0xFF0F, (
                f"{periph.name}.{field.name}: w1c cleared wrong bits "
                f"(expected 0xFF0F, got 0x{periph.read(field.offset):04X})"
            )
            log.append((field.name, "w1c", 0xFF0F))
        else:  # pragma: no cover — RegisterField guards this at construction
            raise AssertionError(f"unexpected access mode {field.access!r}")
    return log


# ══════════════════════════════════════════════════════════════════════════════
# Coverage groups — one per peripheral factory (8 total)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "name,factory",
    [(n, f) for n, f, _, _ in FACTORY_SPECS],
    ids=FACTORY_IDS,
)
def test_peripheral_register_conformance(name, factory):
    """Given: a peripheral factory, When: full write->readback replay,
    Then: every field honours its declared access semantics."""
    periph = factory()
    log = replay_write_readback(periph)
    assert len(log) == len(periph._fields), (
        f"{name}: replay covered {len(log)} fields, expected "
        f"{len(periph._fields)}"
    )


@pytest.mark.parametrize(
    "name,factory", CTRL_SPECS, ids=[n for n, _ in CTRL_SPECS]
)
def test_ctrl_happy_write_readback(name, factory):
    """Given: a peripheral with a CTRL register, When: CTRL=0x3 is written,
    Then: readback of CTRL equals 0x3."""
    periph = factory()
    periph.write_field("CTRL", 0x3)
    assert periph.read_field("CTRL") == 0x3


@pytest.mark.parametrize(
    "name,factory", STATUS_SPECS, ids=[n for n, _ in STATUS_SPECS]
)
def test_status_readonly_write_ignored(name, factory):
    """Given: a peripheral with a read-only STATUS register, When: STATUS is
    written, Then: readback is unchanged (failure-injection scenario)."""
    periph = factory()
    before = periph.read_field("STATUS")
    periph.write_field("STATUS", 0xDEAD_BEEF)
    assert periph.read_field("STATUS") == before, (
        f"{name}.STATUS modified by write to read-only register"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Conformance against regmap.py offsets + base addresses
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "name,factory,regmap_cls,addr_attr", FACTORY_SPECS, ids=FACTORY_IDS
)
def test_factory_fields_match_regmap(name, factory, regmap_cls, addr_attr):
    """Given: a peripheral factory, Then: base address and every register
    offset agree with sim/regmap.py."""
    periph = factory()
    assert periph.base_addr == getattr(regmap.Addr, addr_attr), (
        f"{name}: base 0x{periph.base_addr:08X} != regmap.Addr.{addr_attr} "
        f"0x{getattr(regmap.Addr, addr_attr):08X}"
    )

    if regmap_cls is None:
        # PCIe has no regmap register class (base address only); the layout
        # mirrors rtl/ip/pcie_ep_wrapper. Sanity-check the window instead.
        offsets = [f.offset for f in periph._fields]
        assert len(offsets) == len(set(offsets)), "PCIe duplicate offsets"
        assert all(0 <= o < 0x1000 for o in offsets), "PCIe offset out of window"
        return

    # Every integer (non-BASE) attribute of the regmap class must appear as a
    # field at the same offset in the peripheral model.
    for attr, off in vars(regmap_cls).items():
        if attr == "BASE" or not isinstance(off, int):
            continue
        field = periph._by_name.get(attr)
        assert field is not None, (
            f"{name}: field {attr} (offset 0x{off:02X}) missing from model"
        )
        assert field.offset == off, (
            f"{name}.{attr}: model offset 0x{field.offset:02X} != regmap "
            f"0x{off:02X}"
        )

    # Converse: every model field must exist in the regmap class.
    regmap_offsets = {
        v for k, v in vars(regmap_cls).items()
        if k != "BASE" and isinstance(v, int)
    }
    for field in periph._fields:
        assert field.offset in regmap_offsets, (
            f"{name}.{field.name}: offset 0x{field.offset:02X} not in regmap"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Explicit w1c semantics + anti-vacuous coverage proof
# ══════════════════════════════════════════════════════════════════════════════

def test_intc_ack_w1c_clears_only_written_bits():
    """Given: INTC.ACK seeded to 0xFFFF, When: ACK written with 0x00F0,
    Then: only bits 4..7 clear (readback 0xFF0F)."""
    intc = make_intc_peripheral()
    intc._values[regmap.INTC.ACK] = 0xFFFF
    intc.write_field("ACK", 0x00F0)
    assert intc.read_field("ACK") == 0xFF0F


def test_all_four_access_modes_exercised():
    """Given: the full factory set, Then: rw/r/w/w1c are all represented —
    proving the replay gate genuinely exercises every semantic class."""
    modes = set()
    for _, factory, _, _ in FACTORY_SPECS:
        modes.update(f.access for f in factory()._fields)
    assert modes == {"rw", "r", "w", "w1c"}, f"access modes = {modes}"
