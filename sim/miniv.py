"""
Minimal RV32I Emulator — Python implementation for Func Model firmware testing.

Supports: lw, sw, addi, add, sub, beq, bne, jal, jalr, lui, auipc, slti, and, or, xor, sll, srl.
Goal: run NPU firmware logic before riscv-gcc cross-compilation is available.

When riscv-gcc is ready, replace with Spike + real ELF.
"""

import struct
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from sim.engine.isa import OpCode


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

    Usage:
        emu = RISCVMini(memory_size=128*1024)  # 128KB RAM
        emu.load_program(0x00000000, code_bytes)
        emu.mmio_callback = my_callback  # called on MMIO load/store
        emu.run(max_instructions=1000000)
    """

    def __init__(self, memory_size: int = 256 * 1024):
        self.mem = bytearray(memory_size)
        self.state = RV32State()
        self.instructions_executed = 0
        self.running = False

        # MMIO regions: (base, size, callback_name)
        self.mmio_regions: List[Tuple[int, int, str]] = []
        self.mmio_callback: Optional[Callable] = None
        self._insn_cache: Dict[int, int] = {}

    # ── Memory access ───────────────────────────────────────────────

    def _is_mmio(self, addr: int) -> bool:
        return addr >= 0x40000000

    def _mem_read(self, addr: int) -> int:
        addr &= 0xFFFFFFFF
        if self._is_mmio(addr):
            if self.mmio_callback:
                return self.mmio_callback('read', addr, 4) & 0xFFFFFFFF
            return 0
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

        elif opcode == 0x73:  # SYSTEM (ECALL/EBREAK)
            if funct3 == 0:
                addr = self.state.read(10)  # a0
                if (insn >> 20) == 0:  # ECALL
                    if addr == 0:
                        self.running = False  # exit(0)
                        return False
                elif (insn >> 20) == 1:  # EBREAK
                    self.running = False
                    return False
            elif funct3 == 0 and (insn >> 20) == 0x305:  # WFI — NOP for us
                pass

        else:
            pass  # Unknown instruction — skip

        self.state.pc = self.state.next_pc
        self.instructions_executed += 1
        return self.running if hasattr(self, 'running') else True

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
        for i, b in enumerate(code):
            if base_addr + i < len(self.mem):
                self.mem[base_addr + i] = b
        self.state.pc = base_addr

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
        self.mod = sim_modules
        self.bridge = bridge
        self.doorbell = {'host_tail': 0, 'npu_head': 0}
        self.ring_buffer_addr = 0x80000000  # Ring Buffer in DRAM
        self.ring_size = 64  # entries
        self.irq_pending = 0

    def run_loop(self, max_commands: int = 10) -> List[dict]:
        """Main firmware loop: poll doorbell → dispatch → complete."""
        results = []
        for _ in range(max_commands):
            # Wait for new command (WFI / poll)
            if self.doorbell['host_tail'] == self.doorbell['npu_head']:
                break

            # Read command descriptor from Ring Buffer
            cmd_entry = self._read_cmd_entry(self.doorbell['npu_head'])
            self.doorbell['npu_head'] = (self.doorbell['npu_head'] + 1) % self.ring_size

            # Dispatch
            result = self._dispatch(cmd_entry)
            results.append(result)

            # Write completion (simplified: would write to Completion Ring)
        return results

    def _dram_read(self, addr: int, size: int) -> bytes:
        """Read from DRAM with address translation."""
        from sim.regmap import Addr
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
        """Dispatch command to NPU modules via MMIO."""
        from sim.regmap import MXU, SFU, VECTOR, DMA

        desc = self._read_descriptor(cmd['desc_addr'])
        result = {'opcode': cmd['opcode'], 'status': 'unknown'}
        op = cmd['opcode']

        if op == OpCode.MMUL:  # MMUL — tile-level scheduling
            from sim.tile_scheduler import tile_mmul
            from sim.regmap import DMA, MXU

            def mwrite(base, off, val):
                self._mmio_write(base + off, val)

            def mread(base, off):
                if not self.bridge:
                    return 0
                v = self.bridge.handle('read', base + off, 0)
                return v if v is not None else 0

            def wdone(base, status_off):
                while True:
                    v = mread(base, status_off)
                    if not (v & 1):
                        break

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

        elif op in (OpCode.SOFTMAX, OpCode.LAYERNORM, OpCode.GELU,
                    OpCode.RELU, OpCode.SILU, OpCode.ROPE):
            sfu_op = {
                OpCode.SOFTMAX: 0,
                OpCode.LAYERNORM: 1,
                OpCode.GELU: 2,
                OpCode.RELU: 3,
                OpCode.SILU: 4,
                OpCode.ROPE: 5,
            }[op]
            self._mmio_write(SFU.BASE + SFU.CTRL, sfu_op)
            self._mmio_write(SFU.BASE + SFU.I_ADDR, desc['input_addr'])
            self._mmio_write(SFU.BASE + SFU.O_ADDR, desc['output_addr'])
            self._mmio_write(SFU.BASE + SFU.DIM, desc['input_size'])
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
            self._mmio_write(DMA.BASE + DMA.CMD, 1)
            self._wait_done(DMA.BASE + DMA.STATUS)
            result['status'] = 'done'

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

    def _mmio_write(self, addr: int, val: int):
        """MMIO write → route through bridge."""
        if self.bridge:
            self.bridge.handle('write', addr, val)

    def _wait_done(self, status_addr: int):
        """Spin until module reports done (BUSY=0)."""
        if self.bridge:
            while self.bridge.handle('read', status_addr, 0) & 1:
                pass
