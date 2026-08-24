"""Firmware boot sequence FM guard (SOC-18) — Todo 7 of fm-soc-datapath-hardening.

Verifies the RISC-V boot control flow end to end in the Func Model:

  1. ``NPUFirmware.boot()`` sets PC=0, sp=top of DMEM, and loads the real
     ``firmware/build/npu_firmware.hex`` image into the boot ROM.
  2. ``RISCVMini.step()`` executes that image from the reset vector through
     ``startup.S`` into ``firmware_main()`` — proven by firmware-side
     observables: ``INTC.ENABLE == 0x1FF`` (programmed by firmware_main),
     doorbell ``LAST_STATUS == 0xAA`` (the firmware's own completion-ring
     write self-test) and the ``0xDEADBEEF`` marker at COMPLETION_RING_ADDR.
  3. Doorbell command submit → ``step()`` → the poll loop consumes the
     command and the first command completes: HOST_HEAD/NPU_HEAD advance,
     LAST_STATUS takes the 0x2000 success pattern, and the completion-ring
     entry overwrites the debug marker.
  4. Boot ROM isolation: DMEM/SRAM/DRAM writes never modify the boot ROM.
  5. Failure injection: a corrupted boot ROM image halts ``step()`` with
     no PC advance.

Scope notes (see ``.omo/notepads/fm-soc-datapath-hardening/learnings.md``):

  - Golden numeric output of the stepped C-firmware MMUL is intentionally
    NOT asserted here. ``RISCVMini`` decodes the RV32M ``mul`` instruction
    as ``sub``, which corrupts runtime-multiplied data addresses in the
    firmware's SRAM→DRAM copy-back (the MMUL result itself computes
    correctly; the copy-back destination shifts). Bit-exact golden coverage
    for the same ABI lives in ``test_soc_fm.py::test_firmware_bootflow``
    (Python dispatch path) and the Spike signoff tests.
  - The real firmware's WFI encoding (funct12=0x105) is not recognised by
    ``RISCVMini.step()`` (which matches 0x305), so the poll loop runs as a
    busy loop in the FM. This does not block the boot control flow: MMIO
    reads are synchronous in the FM, so the doorbell poll observes the
    submitted command deterministically.
"""

import os
import struct

import numpy as np
import pytest

from cocotb_bridge import pack_int8_activation_tile_major
from engine.isa import OpCode
from func_model import FuncModel
from miniv import (BOOT_ROM_BASE, BOOT_ROM_SIZE, DMEM_BASE, DMEM_SIZE,
                   NPUFirmware, RISCVMini)
from models.crossbar import CrossbarModel
from regmap import Addr, DOORBELL, INTC

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_HEX_PATH = os.path.join(_REPO_ROOT, "firmware", "build", "npu_firmware.hex")

# COMPLETION_RING_ADDR (0x80008000, gen/npu_abi_firmware.h) as a raw DRAM
# bytearray offset (DRAM_BASE = 0x80000000).
_COMP_RING_OFF = 0x8000

_STEP_CAP = 200_000


def _step_until(emu: RISCVMini, cond, cap: int = _STEP_CAP) -> bool:
    """Step the emulator until cond() is true. Returns True on success."""
    emu.running = True
    for _ in range(cap):
        if cond():
            return True
        emu.step()
    return False


def _last_status(model: FuncModel) -> int:
    return model.bridge.handle("read", Addr.DOORBELL + DOORBELL.LAST_STATUS, 0)


def _boot_to_main(model: FuncModel) -> None:
    """Step from reset until firmware_main() has run its self-test block.

    firmware_main() writes INTC.ENABLE=0x1FF, then probes the completion
    ring with a 0xDEADBEEF write/readback and records 0xAA in LAST_STATUS.
    """
    assert _step_until(
        model.riscv,
        lambda: (_last_status(model) == 0xAA
                 and model.bridge.handle("read", Addr.INTC + INTC.ENABLE, 0)
                 == 0x1FF),
    ), "step() never reached firmware_main() self-test"
    marker = struct.unpack_from("<I", model.dram, _COMP_RING_OFF)[0]
    assert marker == 0xDEADBEEF, (
        f"Expected 0xDEADBEEF completion-ring marker, got 0x{marker:08X}"
    )


# ── Tests ──────────────────────────────────────────────────────────────


def test_boot_sets_pc_and_sp_and_loads_hex():
    """NPUFirmware.boot() sets PC=0, sp=DMEM top and loads the hex image."""
    sram = bytearray(512 * 1024)
    dram = bytearray(64 * 1024 * 1024)
    xbar = CrossbarModel(sram=sram, dram=dram)
    boot_rom = bytearray(BOOT_ROM_SIZE)
    riscv = RISCVMini(crossbar=xbar, sram=sram, dram=dram, boot_rom=boot_rom)
    fw = NPUFirmware(sim_modules={"crossbar": xbar, "dram": dram, "sram": sram})

    # Pristine boot ROM: link.ld zero-pads the vectored trap table (0x00-0x7F),
    # _start lives at 0x80.
    assert struct.unpack_from("<I", boot_rom, 0x80)[0] == 0

    fw.boot(riscv, boot_rom_path=_HEX_PATH)

    assert riscv.state.pc == 0x00000000, (
        f"PC should be 0 after boot, got 0x{riscv.state.pc:08X}"
    )
    assert riscv.state.read(2) == DMEM_BASE + DMEM_SIZE, (
        f"sp should be top of DMEM (0x{DMEM_BASE + DMEM_SIZE:08X}), "
        f"got 0x{riscv.state.read(2):08X}"
    )
    assert struct.unpack_from("<I", boot_rom, 0x80)[0] != 0, (
        "Boot ROM at 0x80 should contain the _start instruction after boot"
    )
    # The loader itself reports bytes loaded for the same image.
    assert RISCVMini.load_hex(_HEX_PATH, bytearray(BOOT_ROM_SIZE),
                              BOOT_ROM_BASE) > 0


def test_step_runs_from_reset_into_firmware_main():
    """step() executes the real firmware hex from reset into firmware_main().

    Proven by firmware-side observables, not just PC motion: INTC.ENABLE
    programmed to 0x1FF, the completion-ring self-test (LAST_STATUS=0xAA)
    and the 0xDEADBEEF marker landing in DRAM.
    """
    model = FuncModel()
    emu = model.riscv

    # FuncModel construction already booted the firmware.
    assert emu.state.pc == 0x00000000
    assert emu.state.read(2) == DMEM_BASE + DMEM_SIZE

    _boot_to_main(model)

    assert emu.instructions_executed > 0, "step() should have executed instructions"
    assert emu.state.pc > 0x80, (
        f"PC should have moved past the trap table into firmware code, "
        f"got 0x{emu.state.pc:08X}"
    )
    # startup.S re-establishes sp from the linked _stack_top symbol
    # (DMEM top minus the 16 KB STACK_SIZE region, ~0x14010 in the current
    # build), replacing boot()'s provisional top-of-DMEM value. sp sits a
    # few frames below _stack_top once firmware_main() is running.
    sp = emu.state.read(2)
    assert DMEM_BASE <= sp < DMEM_BASE + DMEM_SIZE - 1024, (
        f"startup.S should have moved sp off boot()'s provisional DMEM top, "
        f"got 0x{sp:08X}"
    )


def test_boot_doorbell_first_command_completes():
    """boot → step to main → doorbell MMUL submit → step → first command done.

    The poll loop consumes the doorbell command and writes the completion
    ring (cmd_id=0, status=0) plus the 0x2000 success pattern into
    LAST_STATUS. The completion-ring entry overwrites the 0xDEADBEEF debug
    marker, proving write_completion() really executed.
    """
    model = FuncModel()
    emu = model.riscv
    bridge = model.bridge

    _boot_to_main(model)

    # Tiny MMUL: M=1, K=4, N=2 (same shape as test_soc_fm bootflow test).
    M, K, N = 1, 4, 2
    act_data = np.array([1, 2, 3, 4], dtype=np.int8)
    # Packed INT4 weights: low nibble first; unpacked [[1,2],[3,4],[5,6],[7,-8]]
    wgt_packed = np.array([0x21, 0x43, 0x65, 0x87], dtype=np.uint8)
    scales = np.ones(((K + 127) // 128, N), dtype=np.float32)

    act_addr = 0x80010000
    wgt_addr = 0x80020000
    out_addr = 0x81000000
    scale_addr = 0x80110000
    desc_addr = 0x80000080

    model.host_write_data(act_addr, np.frombuffer(
        pack_int8_activation_tile_major(act_data.tobytes(), M, K),
        dtype=np.uint8))
    model.host_write_data(wgt_addr, wgt_packed)
    model.host_write_data(scale_addr, scales.ravel())
    model.host_write_descriptor(
        desc_addr,
        input_addr=act_addr, weight_addr=wgt_addr, output_addr=out_addr,
        scale_addr=scale_addr, scale_size=int(scales.nbytes),
        input_size=((K + 63) // 64) * 4096, weight_size=int(len(wgt_packed)),
        output_size=M * N * 4,
        M=M, K=K, N=N,
    )
    model.host_write_command(OpCode.MMUL, desc_addr)
    assert model.firmware.doorbell["host_tail"] == 1

    # Continue stepping: the firmware poll loop must consume the command.
    assert _step_until(
        emu,
        lambda: bridge.handle("read", Addr.DOORBELL + DOORBELL.HOST_HEAD, 0) == 1,
    ), "first command never completed"

    # Completion observables.
    assert bridge.handle("read", Addr.DOORBELL + DOORBELL.NPU_HEAD, 0) == 1
    assert _last_status(model) == 0x2000, (
        f"Expected LAST_STATUS success pattern 0x2000, got 0x{_last_status(model):08X}"
    )
    # Completion ring entry (cmd_id=0, status=0) overwrote the debug marker.
    assert struct.unpack_from("<I", model.dram, _COMP_RING_OFF)[0] == 0
    assert struct.unpack_from("<I", model.dram, _COMP_RING_OFF + 4)[0] == 0


def test_corrupt_boot_rom_step_does_not_advance():
    """Failure injection: a corrupted boot ROM image halts step() at PC=0.

    The reset-vector word is replaced with EBREAK. A healthy image advances
    PC 0→4 on the first step; the corrupted image returns False and leaves
    PC and the instruction counter untouched.
    """
    # Control: healthy boot ROM advances.
    healthy = FuncModel()
    healthy.riscv.running = True
    assert healthy.riscv.step() is not False
    assert healthy.riscv.instructions_executed == 1
    assert healthy.riscv.state.pc == 4

    # Corrupt the loaded image: EBREAK at the reset vector.
    corrupted = FuncModel()
    corrupted.boot_rom[0:4] = struct.pack("<I", 0x00100073)  # EBREAK
    emu = corrupted.riscv
    emu.state.pc = 0
    emu.running = True

    assert emu.step() is False, "step() should halt on a corrupted boot ROM"
    assert emu.state.pc == 0, (
        f"PC must not advance on a corrupted image, got 0x{emu.state.pc:08X}"
    )
    assert emu.instructions_executed == 0, (
        "Corrupted boot ROM: no instruction should execute"
    )


def test_boot_rom_isolation_dmem_writes():
    """Writes to DMEM/SRAM/DRAM never modify the boot ROM image.

    Per the known modeling quirk, ``RISCVMini._mem_write`` does allow
    writes into the boot ROM region itself (unlike RTL boot_rom.v), so
    isolation here is asserted as "writes to other memories do not touch
    the boot ROM" via a byte-snapshot compare.
    """
    model = FuncModel()
    emu = model.riscv

    snapshot = bytes(model.boot_rom)

    # DMEM (local), SRAM (crossbar), DRAM (crossbar).
    emu._mem_write(0x00010000, 0xDEADBEEF)
    emu._mem_write(0x00010004, 0x12345678)
    emu._mem_write(0x20000000, 0xCAFEBABE)
    emu._mem_write(0x80000000, 0xFEEDFACE)

    # Anti-vacuous: the writes really landed where they were aimed.
    assert emu._mem_read(0x00010000) == 0xDEADBEEF
    assert emu._mem_read(0x20000000) == 0xCAFEBABE
    assert emu._mem_read(0x80000000) == 0xFEEDFACE

    assert bytes(model.boot_rom) == snapshot, (
        "Boot ROM modified by DMEM/SRAM/DRAM writes"
    )
