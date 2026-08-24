"""Ibex shared address space cross-engine FM guard (SOC-16).

Todo 5 of fm-soc-datapath-hardening: verify that ``RISCVMini`` in SoC mode
(Ibex replacement) shares SRAM/DRAM with the engines through the crossbar,
while DMEM and boot ROM stay locally isolated.

Routing under test (``sim/miniv.py`` ``RISCVMini._mem_read/_mem_write``,
SoC mode, unchanged):

    Boot ROM  0x0000_0000–0x0000_FFFF  -> self._boot_rom
    DMEM      0x0001_0000–0x0001_FFFF  -> self.dmem (local, not on crossbar)
    MMIO      0x4000_0000–0x7FFF_FFFF  -> bridge callback
    SRAM      0x2000_0000+             -> crossbar slave 0 (shared)
    DRAM      0x8000_0000+             -> crossbar slave 1 (shared)

Each test constructs a fresh ``FuncModel`` so memory state is zeroed and
there is no cross-test leakage.
"""

import struct

import pytest

from func_model import FuncModel
from miniv import BOOT_ROM_BASE, DMEM_BASE
from models.crossbar import CrossbarModel
from regmap import Addr


def test_ibex_sram_write_mxu_read_consistent():
    """Happy path: Ibex writes SRAM[0x100]; MXU reads the same address.

    Verifies the shared SRAM is coherent across engines in both
    directions (Ibex->MXU and MXU->Ibex) through the crossbar.
    """
    model = FuncModel()
    emu = model.riscv

    sram_addr = Addr.SRAM_BASE + 0x100
    pattern = 0x5A5AA5A5

    # Ibex writes SRAM[0x100] and reads its own write back.
    emu._mem_write(sram_addr, pattern)
    assert emu._mem_read(sram_addr) == pattern, (
        f"Ibex SRAM readback: expected 0x{pattern:08X}, "
        f"got 0x{emu._mem_read(sram_addr):08X}"
    )

    # MXU (separate crossbar master) sees the same value.
    mxu_bytes = model.crossbar.read(CrossbarModel.MASTER_MXU, sram_addr, 4)
    mxu_val = struct.unpack_from("<I", mxu_bytes, 0)[0]
    assert mxu_val == pattern, (
        f"MXU crossbar read: expected 0x{pattern:08X}, got 0x{mxu_val:08X}"
    )

    # Reverse direction: MXU writes, Ibex reads the updated value.
    pattern2 = 0x0BADF00D
    model.crossbar.write(
        CrossbarModel.MASTER_MXU, sram_addr, struct.pack("<I", pattern2)
    )
    assert emu._mem_read(sram_addr) == pattern2, (
        f"Ibex readback after MXU write: expected 0x{pattern2:08X}, "
        f"got 0x{emu._mem_read(sram_addr):08X}"
    )


def test_ibex_dram_write_mxu_read_consistent():
    """Happy path: Ibex writes DRAM; MXU reads the same address.

    Same cross-engine coherence check on the DRAM slave, plus a
    cross-slave sanity check that SRAM at the equivalent offset does
    not alias the DRAM value.
    """
    model = FuncModel()
    emu = model.riscv

    dram_addr = Addr.DRAM_BASE + 0x100
    pattern = 0xDEADBEEF

    emu._mem_write(dram_addr, pattern)
    assert emu._mem_read(dram_addr) == pattern

    mxu_bytes = model.crossbar.read(CrossbarModel.MASTER_MXU, dram_addr, 4)
    mxu_val = struct.unpack_from("<I", mxu_bytes, 0)[0]
    assert mxu_val == pattern, (
        f"MXU DRAM read: expected 0x{pattern:08X}, got 0x{mxu_val:08X}"
    )

    # SRAM at the same offset must NOT see the DRAM write.
    sram_val = emu._mem_read(Addr.SRAM_BASE + 0x100)
    assert sram_val != pattern, (
        "cross-slave aliasing: DRAM write leaked into SRAM at offset 0x100"
    )


def test_dmem_isolation_from_sram_dram():
    """DMEM is local to Ibex and does not alias SRAM or DRAM.

    Seeds SRAM and DRAM with sentinels, writes DMEM, then verifies the
    shared memories are untouched and the DMEM address range is not
    routed through the crossbar (DECERR).
    """
    model = FuncModel()
    emu = model.riscv

    dmem_addr = DMEM_BASE + 0x100
    sram_addr = Addr.SRAM_BASE + 0x100
    dram_addr = Addr.DRAM_BASE + 0x100

    emu._mem_write(sram_addr, 0x12345678)
    emu._mem_write(dram_addr, 0x9ABCDEF0)
    emu._mem_write(dmem_addr, 0xFEEDFACE)

    assert emu._mem_read(dmem_addr) == 0xFEEDFACE
    assert emu._mem_read(sram_addr) == 0x12345678, (
        "DMEM write corrupted SRAM"
    )
    assert emu._mem_read(dram_addr) == 0x9ABCDEF0, (
        "DMEM write corrupted DRAM"
    )

    # DMEM is not a crossbar-visible region; the crossbar must DECERR.
    with pytest.raises(ValueError):
        model.crossbar.read(CrossbarModel.MASTER_IBEX, dmem_addr, 4)


def test_boot_rom_isolation():
    """Boot ROM is isolated from DMEM/SRAM/DRAM writes.

    Seeds a known instruction word into boot ROM (offset 0x80, right
    after the zero-padded vectored trap table), then writes to DMEM
    (immediately adjacent to boot ROM), SRAM, and DRAM, and verifies
    the entire boot ROM image is byte-identical afterwards.
    """
    model = FuncModel()
    emu = model.riscv

    insn = 0x00000013  # nop
    boot_word_off = 0x80
    model.boot_rom[boot_word_off:boot_word_off + 4] = struct.pack("<I", insn)
    assert emu._mem_read(BOOT_ROM_BASE + boot_word_off) == insn

    snapshot = bytes(model.boot_rom)

    # DMEM base sits directly above boot ROM; SRAM/DRAM are shared
    # crossbar regions. None of these writes may touch boot ROM.
    emu._mem_write(DMEM_BASE, 0xDEADBEEF)
    emu._mem_write(Addr.SRAM_BASE + 0x100, 0xCAFEBABE)
    emu._mem_write(Addr.DRAM_BASE + 0x100, 0x0BADF00D)

    assert bytes(model.boot_rom) == snapshot, (
        "boot ROM isolation violated: a non-boot-ROM write modified boot ROM"
    )
    assert emu._mem_read(BOOT_ROM_BASE + boot_word_off) == insn


def test_failure_injection_dmem_write_not_visible_in_sram():
    """Failure injection: a DMEM write must NOT appear at the SRAM address.

    This guard is the negative proof of the decoder: if DMEM were
    aliased onto SRAM (or the crossbar wrongly accepted the low
    address range), the readback would match and this test would fail.
    """
    model = FuncModel()
    emu = model.riscv

    dmem_addr = DMEM_BASE + 0x100
    sram_addr = Addr.SRAM_BASE + 0x100

    emu._mem_write(dmem_addr, 0xFEEDFACE)
    sram_val = emu._mem_read(sram_addr)
    assert sram_val != 0xFEEDFACE, (
        "aliasing detected: DMEM write leaked into SRAM at offset 0x100"
    )

    # The MXU engine port must be equally blind to the DMEM value.
    mxu_bytes = model.crossbar.read(CrossbarModel.MASTER_MXU, sram_addr, 4)
    mxu_val = struct.unpack_from("<I", mxu_bytes, 0)[0]
    assert mxu_val != 0xFEEDFACE, (
        "aliasing detected: DMEM write visible to MXU via SRAM read"
    )
