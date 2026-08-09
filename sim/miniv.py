"""
Minimal RV32I Emulator — Python implementation for Func Model firmware testing.

Supports: lw, sw, addi, add, sub, beq, bne, jal, jalr, lui, auipc, slti, and, or, xor, sll, srl.
Goal: run NPU firmware logic before riscv-gcc cross-compilation is available.

When riscv-gcc is ready, replace with Spike + real ELF.
"""

import os
import struct
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from engine.isa import OpCode
from models.crossbar import CrossbarModel


# ── Address constants ────────────────────────────────────────────────

BOOT_ROM_BASE = 0x0000_0000
BOOT_ROM_SIZE = 0x0001_0000  # 64 KB
DMEM_BASE     = 0x0001_0000
DMEM_SIZE     = 0x0001_0000  # 64 KB


@dataclass
class RV32State:
    """RV32I architectural state."""
    pc: int = 0
    regs: List[int] = field(default_factory=lambda: [0] * 32)  # x0-x31
    next_pc: int = 0

    def read(self, idx: int) -> int:
        return 0 if idx == 0 else self.regs[idx]

    def write(self, idx: int, val: int):
        if idx != 0:
            self.regs[idx] = val & 0xFFFFFFFF


class RISCVMini:
    """Minimal RV32I emulator with MMIO callback support.

    Two modes:
      - Legacy (no crossbar): uses self.mem for all addresses < 0x4000_0000.
      - SoC mode (crossbar provided): shared sram/dram through crossbar,
        local boot ROM and DMEM, unified address decoder.

    Usage:
        emu = RISCVMini(memory_size=128*1024)  # 128KB RAM (legacy)
        emu = RISCVMini(crossbar=xbar, sram=sram, dram=dram)  # SoC mode
        emu.load_program(0x00000000, code_bytes)
        emu.mmio_callback = my_callback
        emu.run(max_instructions=1000000)
    """

    def __init__(
        self,
        memory_size: int = 256 * 1024,
        crossbar: Optional[CrossbarModel] = None,
        sram: Optional[bytearray] = None,
        dram: Optional[bytearray] = None,
        boot_rom: Optional[bytearray] = None,
        dmem_size: int = DMEM_SIZE,
    ):
        self.mem = bytearray(memory_size)
        self.state = RV32State()
        self.instructions_executed = 0
        self.running = False

        # SoC mode: shared memories through crossbar
        self._crossbar: Optional[CrossbarModel] = crossbar
        self._sram: Optional[bytearray] = sram
        self._dram: Optional[bytearray] = dram
        self._boot_rom: Optional[bytearray] = boot_rom
        self.dmem = bytearray(dmem_size)
        self._soc_mode = crossbar is not None

        # MMIO regions: (base, size, callback_name)
        self.mmio_regions: List[Tuple[int, int, str]] = []
        self.mmio_callback: Optional[Callable] = None
        self._insn_cache: Dict[int, int] = {}

        # Interrupt handling
        self.interrupt_pending: bool = False
        self.irq_handler: Optional[Callable[[int], None]] = None

    # ── Memory access ───────────────────────────────────────────────

    def _is_mmio(self, addr: int) -> bool:
        # MMIO range: 0x4000_0000 – 0x7FFF_FFFF (below DRAM at 0x8000_0000)
        return 0x40000000 <= addr < 0x80000000

    def _mem_read(self, addr: int) -> int:
        addr &= 0xFFFFFFFF
        if self._is_mmio(addr):
            if self.mmio_callback:
                return self.mmio_callback('read', addr, 4) & 0xFFFFFFFF
            return 0

        # SoC mode: route through address decoder
        if self._soc_mode:
            size = 4
            # Boot ROM (read-only)
            if BOOT_ROM_BASE <= addr < BOOT_ROM_BASE + BOOT_ROM_SIZE:
                if self._boot_rom is not None:
                    off = addr - BOOT_ROM_BASE
                    if off + size <= len(self._boot_rom):
                        return struct.unpack_from('<I', self._boot_rom, off)[0]
                return 0
            # DMEM
            if DMEM_BASE <= addr < DMEM_BASE + len(self.dmem):
                off = addr - DMEM_BASE
                return struct.unpack_from('<I', self.dmem, off)[0]
            # SRAM / DRAM through crossbar
            try:
                sram_bytes = self._crossbar.read(
                    CrossbarModel.MASTER_IBEX, addr, size)
                if sram_bytes is not None and len(sram_bytes) >= size:
                    return struct.unpack_from('<I', sram_bytes, 0)[0]
            except ValueError:
                pass
            return 0

        # Legacy mode
        if addr + 4 <= len(self.mem):
            return struct.unpack_from('<I', self.mem, addr)[0]
        return 0

    def _mem_write(self, addr: int, val: int):
        addr &= 0xFFFFFFFF
        val &= 0xFFFFFFFF
        if self._is_mmio(addr):
            if self.mmio_callback:
                self.mmio_callback('write', addr, val)
            return

        # SoC mode: route through address decoder
        if self._soc_mode:
            data = struct.pack('<I', val)
            # Boot ROM / DMEM (low address range)
            if BOOT_ROM_BASE <= addr < BOOT_ROM_BASE + BOOT_ROM_SIZE:
                if self._boot_rom is not None:
                    off = addr - BOOT_ROM_BASE
                    if off + 4 <= len(self._boot_rom):
                        self._boot_rom[off:off + 4] = data
                return
            if DMEM_BASE <= addr < DMEM_BASE + len(self.dmem):
                off = addr - DMEM_BASE
                self.dmem[off:off + 4] = data
                return
            # SRAM / DRAM through crossbar
            try:
                self._crossbar.write(CrossbarModel.MASTER_IBEX, addr, data)
            except ValueError:
                pass
            return

        # Legacy mode
        if addr + 4 <= len(self.mem):
            struct.pack_into('<I', self.mem, addr, val)

    # ── Instruction fetch & decode ──────────────────────────────────

    def _fetch(self) -> int:
        addr = self.state.pc & 0xFFFFFFFF
        return self._mem_read(addr)

    # ── Execute one instruction ─────────────────────────────────────

    def step(self) -> bool:
        """Execute one instruction. Returns True if more instructions should run."""
        try:
            insn = self._fetch()
        except Exception:
            return False

        self.state.next_pc = (self.state.pc + 4) & 0xFFFFFFFF
        opcode = insn & 0x7F
        rd = (insn >> 7) & 0x1F
        funct3 = (insn >> 12) & 0x7
        rs1_idx = (insn >> 15) & 0x1F
        rs2_idx = (insn >> 20) & 0x1F
        funct7 = (insn >> 25) & 0x7F

        rs1 = self.state.read(rs1_idx)
        rs2 = self.state.read(rs2_idx)

        # Immediates
        i_imm = self._sext((insn >> 20) & 0xFFF, 12)
        s_imm = self._sext(((insn >> 7) & 0x1F) | ((insn >> 25) << 5), 12)
        b_imm = self._sext(
            ((insn >> 8) & 0xF) << 1 | ((insn >> 25) & 0x3F) << 5 |
            ((insn >> 7) & 1) << 11 | ((insn >> 31) << 12), 13
        )
        u_imm = insn & 0xFFFFF000
        j_imm = self._sext(
            ((insn >> 21) & 0x3FF) << 1 | ((insn >> 20) & 1) << 11 |
            ((insn >> 12) & 0xFF) << 12 | ((insn >> 31) << 20), 21
        )

        if opcode == 0x03:  # LOAD (lw)
            addr = (rs1 + i_imm) & 0xFFFFFFFF
            self.state.write(rd, self._mem_read(addr))

        elif opcode == 0x23:  # STORE (sw)
            addr = (rs1 + s_imm) & 0xFFFFFFFF
            self._mem_write(addr, rs2)

        elif opcode == 0x13:  # OP-IMM
            if funct3 == 0:   self.state.write(rd, rs1 + i_imm)       # addi
            elif funct3 == 2: self.state.write(rd, 1 if rs1 < i_imm else 0)  # slti
            elif funct3 == 4: self.state.write(rd, rs1 ^ i_imm)       # xori
            elif funct3 == 6: self.state.write(rd, rs1 | i_imm)       # ori
            elif funct3 == 7: self.state.write(rd, rs1 & i_imm)       # andi
            elif funct3 == 1: self.state.write(rd, rs1 << (i_imm & 0x1F))  # slli
            elif funct3 == 5:
                if funct7 == 0:
                    self.state.write(rd, rs1 >> (i_imm & 0x1F))       # srli
                else:
                    self.state.write(rd, self._sra(rs1, i_imm & 0x1F))  # srai

        elif opcode == 0x33:  # OP
            if funct3 == 0:
                self.state.write(rd, (rs1 + rs2) if funct7 == 0 else (rs1 - rs2))  # add/sub
            elif funct3 == 1: self.state.write(rd, rs1 << (rs2 & 0x1F))   # sll
            elif funct3 == 2: self.state.write(rd, 1 if rs1 < rs2 else 0)  # slt
            elif funct3 == 4: self.state.write(rd, rs1 ^ rs2)             # xor
            elif funct3 == 5:
                self.state.write(rd, (rs1 >> (rs2 & 0x1F)) if funct7 == 0
                                 else self._sra(rs1, rs2 & 0x1F))          # srl/sra
            elif funct3 == 6: self.state.write(rd, rs1 | rs2)             # or
            elif funct3 == 7: self.state.write(rd, rs1 & rs2)             # and

        elif opcode == 0x63:  # BRANCH
            take = False
            if funct3 == 0:   take = rs1 == rs2                          # beq
            elif funct3 == 1: take = rs1 != rs2                          # bne
            elif funct3 == 4: take = rs1 < rs2                           # blt
            elif funct3 == 5: take = rs1 >= rs2                          # bge
            elif funct3 == 6: take = (rs1 & 0xFFFFFFFF) < (rs2 & 0xFFFFFFFF)  # bltu
            elif funct3 == 7: take = (rs1 & 0xFFFFFFFF) >= (rs2 & 0xFFFFFFFF) # bgeu
            if take:
                self.state.next_pc = (self.state.pc + b_imm) & 0xFFFFFFFF

        elif opcode == 0x6F:  # JAL
            self.state.write(rd, (self.state.pc + 4) & 0xFFFFFFFF)
            self.state.next_pc = (self.state.pc + j_imm) & 0xFFFFFFFF

        elif opcode == 0x67:  # JALR
            target = (rs1 + i_imm) & 0xFFFFFFFE
            self.state.write(rd, (self.state.pc + 4) & 0xFFFFFFFF)
            self.state.next_pc = target

        elif opcode == 0x37:  # LUI
            self.state.write(rd, u_imm)

        elif opcode == 0x17:  # AUIPC
            self.state.write(rd, (self.state.pc + u_imm) & 0xFFFFFFFF)

        elif opcode == 0x0F:  # FENCE / FENCE.I — NOP
            pass

        elif opcode == 0x73:  # SYSTEM (ECALL/EBREAK/WFI)
            if funct3 == 0:
                funct12 = (insn >> 20) & 0xFFF
                addr = self.state.read(10)  # a0
                if funct12 == 0:  # ECALL
                    if addr == 0:
                        self.running = False  # exit(0)
                        return False
                elif funct12 == 1:  # EBREAK
                    self.running = False
                    return False
                elif funct12 == 0x305:  # WFI — wake on interrupt
                    if self.interrupt_pending:
                        self._handle_irq()

        else:
            pass  # Unknown instruction — skip

        self.state.pc = self.state.next_pc
        self.instructions_executed += 1
        return self.running if hasattr(self, 'running') else True

    # ── Interrupt handling ──────────────────────────────────────────

    def set_interrupt_pending(self):
        """Mark an interrupt as pending. Called by MMIOBridge._set_irq()."""
        self.interrupt_pending = True

    def _handle_irq(self):
        """Trap handler: read INTC.PENDING via MMIO, find highest-priority
        source, dispatch to irq_handler, write ACK, clear flag if done."""
        from regmap import INTC
        if not self.mmio_callback:
            self.interrupt_pending = False
            return

        pending = self.mmio_callback('read', INTC.BASE + INTC.PENDING, 0) & 0xFFFFFFFF
        if pending == 0:
            self.interrupt_pending = False
            return

        source_bit = 0
        while source_bit < 32:
            if pending & (1 << source_bit):
                break
            source_bit += 1
        if source_bit == 32:
            self.interrupt_pending = False
            return

        if self.irq_handler:
            self.irq_handler(source_bit)

        self.mmio_callback('write', INTC.BASE + INTC.ACK, 1 << source_bit)

        pending_after = self.mmio_callback('read', INTC.BASE + INTC.PENDING, 0) & 0xFFFFFFFF
        if pending_after == 0:
            self.interrupt_pending = False

    # ── Run ─────────────────────────────────────────────────────────

    def run(self, max_instructions: int = 10_000_000) -> int:
        """Run until exit or max_instructions reached."""
        self.running = True
        while self.running and self.instructions_executed < max_instructions:
            if not self.step():
                break
        return self.instructions_executed

    def load_program(self, base_addr: int, code: bytes):
        """Load raw RISC-V binary at base_addr, set PC."""
        if self._soc_mode and base_addr < BOOT_ROM_BASE + BOOT_ROM_SIZE:
            target = self._boot_rom if (
                self._boot_rom is not None and
                base_addr + len(code) <= len(self._boot_rom)
            ) else self.dmem
        else:
            target = self.mem
        for i, b in enumerate(code):
            if base_addr + i < len(target):
                target[base_addr + i] = b
        self.state.pc = base_addr

    @staticmethod
    def load_hex(
        path: str,
        mem: bytearray,
        base_addr: int = 0x00000000,
    ) -> int:
        """Load a hex firmware file into a bytearray at base_addr.

        Supports two formats:
          - Intel HEX (:LLAAAATT[DD...]CC)
          - Raw 32-bit word hex (one 8-char hex word per line)

        Returns number of bytes loaded. Raises FileNotFoundError if the
        file does not exist.
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"HEX file not found: {path}")

        bytes_loaded = 0
        with open(path, 'r') as f:
            first_line = f.readline().strip()
            f.seek(0)
            if first_line.startswith(':'):
                bytes_loaded = RISCVMini._load_intel_hex(f, mem, base_addr)
            else:
                bytes_loaded = RISCVMini._load_raw_word_hex(f, mem, base_addr)
        return bytes_loaded

    @staticmethod
    def _load_intel_hex(f, mem: bytearray, base_addr: int) -> int:
        bytes_loaded = 0
        for line in f:
            line = line.strip()
            if not line.startswith(':'):
                continue
            byte_count = int(line[1:3], 16)
            addr = int(line[3:7], 16)
            record_type = int(line[7:9], 16)
            if record_type == 0x00:
                data = bytes.fromhex(line[9:9 + byte_count * 2])
                target_addr = base_addr + addr
                mem[target_addr:target_addr + byte_count] = data
                bytes_loaded += byte_count
            elif record_type == 0x01:
                break
        return bytes_loaded

    @staticmethod
    def _load_raw_word_hex(f, mem: bytearray, base_addr: int) -> int:
        bytes_loaded = 0
        addr = 0
        for line in f:
            line = line.strip()
            if not line or line.startswith('@'):
                if line.startswith('@'):
                    addr = int(line[1:], 16)
                continue
            try:
                word = int(line, 16) & 0xFFFFFFFF
            except ValueError:
                continue
            target = base_addr + addr
            if target + 4 <= len(mem):
                struct.pack_into('<I', mem, target, word)
                bytes_loaded += 4
            addr += 4
        return bytes_loaded

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _sext(val: int, bits: int) -> int:
        sign_bit = 1 << (bits - 1)
        return (val & (sign_bit - 1)) - (val & sign_bit)

    @staticmethod
    def _sra(val: int, shift: int) -> int:
        if val & 0x80000000:
            return (val >> shift) | (0xFFFFFFFF << (32 - shift))
        return val >> shift


# ══════════════════════════════════════════════════════════════════════
# NPU Firmware Emulator — same logic as C firmware
# ══════════════════════════════════════════════════════════════════════

class NPUFirmware:
    """Python implementation of NPU firmware logic.

    This mirrors exactly what the C firmware will do when compiled for RISC-V.
    Uses the same MMIO register addresses from regmap.py.
    """

    def __init__(self, sim_modules: dict, bridge=None):
        """
        sim_modules: {'mxu': GoldenMXU, 'sfu': GoldenSFU, 'vector': GoldenVector,
                       'dma': GoldenDMA, 'dram': bytearray, 'sram': bytearray}
        bridge: MMIOBridge instance for register communication
        """
        import warnings
        warnings.warn(
            "NPUFirmware is deprecated; use Spike + real firmware ELF for golden "
            "reference verification. NPUFirmware remains available for fast smoke tests.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.mod = sim_modules
        self.bridge = bridge
        self.doorbell = {'host_tail': 0, 'npu_head': 0}
        self.ring_buffer_addr = 0x80000000  # Ring Buffer in DRAM
        self.ring_size = 16  # entries
        self.irq_pending = 0
        self._irq_serviced = False

        # RISC-V binding (injected after construction, matches FuncModel order)
        self.riscv: Optional["RISCVMini"] = None
        self._irq_enabled: Dict[int, bool] = {}

    def bind_riscv(self, riscv: "RISCVMini"):
        """Inject RISCVMini reference after construction.
        
        Called by FuncModel after both firmware and riscv are created.
        After binding, MMIO and interrupt paths route through the emulator.
        """
        self.riscv = riscv

    def boot(self, riscv: "RISCVMini", boot_rom_path: Optional[str] = None):
        """Initialize RISC-V state for firmware boot.

        Sets PC=0x0000_0000, stack pointer to top of DMEM, and optionally
        loads a firmware hex file into boot ROM.
        """
        self.riscv = riscv
        riscv.state.pc = 0x00000000
        riscv.state.write(2, DMEM_BASE + DMEM_SIZE)  # x2 = sp

        if boot_rom_path is not None and os.path.exists(boot_rom_path):
            RISCVMini.load_hex(boot_rom_path, riscv._boot_rom, BOOT_ROM_BASE)

    def run_loop(self, max_commands: int = 10) -> List[dict]:
        """Main firmware loop: poll doorbell → dispatch → complete."""
        from regmap import DOORBELL
        results = []
        for _ in range(max_commands):
            # Service any pending doorbell HOST interrupt first.
            if self.riscv is not None and self.riscv.interrupt_pending:
                self.riscv._handle_irq()

            # Wait for new command (WFI / poll)
            if self.doorbell['host_tail'] == self.doorbell['npu_head']:
                break

            # Read command descriptor from Ring Buffer
            cmd_entry = self._read_cmd_entry(self.doorbell['npu_head'])
            self.doorbell['npu_head'] = (self.doorbell['npu_head'] + 1) % self.ring_size

            # Mirror NPU_HEAD to doorbell MMIO.
            if self.bridge:
                self._mmio_write(DOORBELL.BASE + DOORBELL.NPU_HEAD, self.doorbell['npu_head'])

            # Dispatch
            result = self._dispatch(cmd_entry)
            results.append(result)

            # Signal completion to host via HOST_HEAD.
            if self.bridge:
                self._mmio_write(DOORBELL.BASE + DOORBELL.HOST_HEAD, self.doorbell['npu_head'])

        return results

    def dispatch_interrupt(self, source_bit: int):
        """Called by RISCVMini trap handler when an IRQ fires.

        Reads INTC.PENDING to determine which engine completed,
        records IRQ service for _wait_done to continue.
        Supports all engine source bits: 0=MXU, 1=SFU, 2=VECTOR, 3=DMA.
        """
        from regmap import INTC, MXU, SFU, VECTOR, DMA
        pending = self.irq_pending
        if self.bridge:
            pending = self.bridge.handle('read', INTC.BASE + INTC.PENDING, 0)
        self.irq_pending = pending

        # Map source bit to engine IRQ_EN register for cleanup
        engine_irq_en = {
            0: (MXU.BASE + MXU.IRQ_EN),
            1: (SFU.BASE + SFU.IRQ_EN),
            2: (VECTOR.BASE + VECTOR.IRQ_EN),
            3: (DMA.BASE + DMA.IRQ_EN),
        }
        if source_bit in engine_irq_en:
            self._mmio_write(engine_irq_en[source_bit], 0)
            self._irq_serviced = True
        elif source_bit == 8:
            # Doorbell/host interrupt — just record service.
            self._irq_serviced = True

    def _dram_read(self, addr: int, size: int) -> bytes:
        """Read from DRAM with address translation."""
        xbar = self.mod.get('crossbar')
        if xbar is not None:
            return xbar.read(CrossbarModel.MASTER_IBEX, addr, size)
        from regmap import Addr
        off = addr - Addr.DRAM_BASE
        dram = self.mod.get('dram', bytearray())
        return bytes(dram[off:off + size])

    def _read_cmd_entry(self, idx: int) -> dict:
        """Read command descriptor from Ring Buffer in DRAM."""
        addr = self.ring_buffer_addr + idx * 32
        data = self._dram_read(addr, 24)
        opcode, desc_addr, flags = struct.unpack_from('<IQI', data, 0)
        return {'opcode': opcode, 'desc_addr': desc_addr, 'flags': flags}

    def _dispatch(self, cmd: dict) -> dict:
        """DEPRECATED — Dispatch command to NPU modules via MMIO.

        Deprecated: Use ``sim/spike_host.py`` (Spike + real firmware ELF) as the
        golden reference path for firmware dispatch logic. NPUFirmware remains
        available for fast smoke tests but is not guaranteed to match the real
        C firmware behaviour.
        """
        from regmap import MXU, SFU, VECTOR, DMA

        desc = self._read_descriptor(cmd['desc_addr'])
        result = {'opcode': cmd['opcode'], 'status': 'unknown'}
        op = cmd['opcode']

        try:
            if op == OpCode.MMUL:  # MMUL — tile-level scheduling
                from tile_scheduler import tile_mmul
                from regmap import DMA, MXU

                def mwrite(base, off, val):
                    if self.riscv is not None and (val & 1):
                        if base == MXU.BASE and off == MXU.CMD:
                            self._mmio_write(MXU.BASE + MXU.IRQ_EN, 1)
                        elif base == DMA.BASE and off == DMA.CMD:
                            self._mmio_write(DMA.BASE + DMA.IRQ_EN, 1)
                    self._mmio_write(base + off, val)

                def mread(base, off):
                    return self._mmio_read(base + off)

                def wdone(base, status_off):
                    self._wait_done(base + status_off)

                tile_mmul(
                    desc=desc,
                    mmio_write=mwrite,
                    mmio_read=mread,
                    wait_done=wdone,
                    DMA_BASE=DMA.BASE,
                    MXU_BASE=MXU.BASE,
                    DMA=DMA,
                    MXU=MXU,
                )

                result['status'] = 'done'

                if self.riscv is not None and self.riscv.interrupt_pending:
                    self.riscv._handle_irq()

            elif op in (OpCode.SOFTMAX, OpCode.LAYERNORM, OpCode.GELU,
                        OpCode.RELU, OpCode.SILU, OpCode.ROPE, OpCode.RMSNORM):
                sfu_op = {
                    OpCode.SOFTMAX: 0,
                    OpCode.LAYERNORM: 1,
                    OpCode.GELU: 2,
                    OpCode.RELU: 3,
                    OpCode.SILU: 4,
                    OpCode.ROPE: 5,
                    OpCode.RMSNORM: 6,
                }[op]
                self._mmio_write(SFU.BASE + SFU.CTRL, sfu_op)
                self._mmio_write(SFU.BASE + SFU.I_ADDR, desc['input_addr'])
                self._mmio_write(SFU.BASE + SFU.O_ADDR, desc['output_addr'])
                self._mmio_write(SFU.BASE + SFU.DIM, desc['input_size'])
                if self.riscv is not None:
                    self._mmio_write(SFU.BASE + SFU.IRQ_EN, 1)
                self._mmio_write(SFU.BASE + SFU.CMD, 1)
                self._wait_done(SFU.BASE + SFU.STATUS)
                result['status'] = 'done'

            elif op in (OpCode.VADD, OpCode.VMUL, OpCode.VRED_MAX,
                        OpCode.VRED_SUM, OpCode.VCONV, OpCode.VRESID):
                vec_op = {
                    OpCode.VADD: 0,
                    OpCode.VMUL: 1,
                    OpCode.VRED_MAX: 2,
                    OpCode.VRED_SUM: 3,
                    OpCode.VCONV: 4,
                    OpCode.VRESID: 5,
                }[op]
                self._mmio_write(VECTOR.BASE + VECTOR.CTRL, vec_op)
                self._mmio_write(VECTOR.BASE + VECTOR.A_ADDR, desc['input_addr'])
                self._mmio_write(VECTOR.BASE + VECTOR.B_ADDR, desc['weight_addr'])
                self._mmio_write(VECTOR.BASE + VECTOR.O_ADDR, desc['output_addr'])
                self._mmio_write(VECTOR.BASE + VECTOR.DIM, desc['input_size'])
                if self.riscv is not None:
                    self._mmio_write(VECTOR.BASE + VECTOR.IRQ_EN, 1)
                self._mmio_write(VECTOR.BASE + VECTOR.CMD, 1)
                self._wait_done(VECTOR.BASE + VECTOR.STATUS)
                result['status'] = 'done'

            elif op in (OpCode.DMA_LD, OpCode.DMA_ST, OpCode.DMA_LDD, OpCode.DMA_STD):
                if op in (OpCode.DMA_LD, OpCode.DMA_LDD):
                    self._mmio_write(DMA.BASE + DMA.CH0_SRC, desc['input_addr'])
                    self._mmio_write(DMA.BASE + DMA.CH0_DST, desc['input_sram'])
                    self._mmio_write(DMA.BASE + DMA.CH0_SIZE, desc['input_size'])
                else:
                    self._mmio_write(DMA.BASE + DMA.CH1_SRC, desc['weight_addr'])
                    self._mmio_write(DMA.BASE + DMA.CH1_DST, desc['weight_sram'])
                    self._mmio_write(DMA.BASE + DMA.CH1_SIZE, desc['weight_size'])
                if self.riscv is not None:
                    self._mmio_write(DMA.BASE + DMA.IRQ_EN, 1)
                self._mmio_write(DMA.BASE + DMA.CMD, 1)
                self._wait_done(DMA.BASE + DMA.STATUS)
                result['status'] = 'done'

        except (ValueError, KeyError) as exc:
            result['status'] = 'error'
            result['error'] = str(exc)

        return result

    def _read_descriptor(self, addr: int) -> dict:
        """Read operation descriptor from DRAM. v2: includes scale fields.
        
        Field order (matches func_model.py host_write_descriptor):
        [0] input_addr   [1] weight_addr   [2] output_addr   [3] scale_addr
        [4] input_sram   [5] weight_sram   [6] output_sram   [7] scale_sram
        [8] input_size   [9] weight_size  [10] output_size  [11] scale_size
        [12] M          [13] K           [14] N
        """
        data = self._dram_read(addr, 60)  # 15 uint32
        fields = struct.unpack_from('<15I', data, 0)
        return {
            'input_addr':  fields[0],
            'weight_addr': fields[1],
            'output_addr': fields[2],
            'scale_addr':  fields[3],
            'input_sram':  fields[4],
            'weight_sram': fields[5],
            'output_sram': fields[6],
            'scale_sram':  fields[7],
            'input_size':  fields[8],
            'weight_size': fields[9],
            'output_size': fields[10],
            'scale_size':  fields[11],
            'M': fields[12], 'K': fields[13], 'N': fields[14],
        }

    def _mmio_read(self, addr: int) -> int:
        """MMIO read → route through RISCVMini when bound, else bridge."""
        if self.riscv is not None:
            return self.riscv._mem_read(addr)
        if self.bridge:
            result = self.bridge.handle('read', addr, 0)
            return result if result is not None else 0
        return 0

    def _mmio_write(self, addr: int, val: int):
        """MMIO write → route through RISCVMini when bound, else bridge."""
        if self.riscv is not None:
            self.riscv._mem_write(addr, val)
        elif self.bridge:
            self.bridge.handle('write', addr, val)

    def _wait_done(self, status_addr: int):
        """Wait for engine completion.

        When RISCVMini is bound: interrupt-driven via WFI+trap.
        Otherwise: polling loop on STATUS.BUSY.
        """
        if self.riscv is not None:
            self._irq_serviced = False
            while not self._irq_serviced:
                if self.riscv.interrupt_pending:
                    self.riscv._handle_irq()
            self._irq_serviced = False
            return
        if self.bridge:
            while self.bridge.handle('read', status_addr, 0) & 1:
                pass
