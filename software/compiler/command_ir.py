"""Pure-Python CaduceusCore command IR and lowering."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from .command_ir_codec import decode_blob, encode_blob
from .command_ir_types import (
    CAD_BLOB_MAJOR,
    CAD_BLOB_MINOR,
    CAD_CAP_DMA,
    CAD_CAP_MXU,
    CAD_CAP_PCIE,
    CAD_CAP_SFU,
    CAD_CAP_VECTOR,
    CAD_MAX_BUFFERS,
    CAD_MAX_COMMANDS,
    CAD_OP_MMUL,
    DRAM_BASE,
    DRAM_SIZE,
    SRAM_BASE,
    SRAM_SIZE,
    TILE_H,
    TILE_W,
    Buffer,
    Command,
    LowerStatus,
)

import gen.npu_abi as abi


@dataclass
class CommandBlob:
    caps: int
    version_major: int = CAD_BLOB_MAJOR
    version_minor: int = CAD_BLOB_MINOR
    buffers: List[Buffer] = field(default_factory=list)
    commands: List[Command] = field(default_factory=list)
    lowered: bool = False

    def declare_buffer(self, size: int, alignment: int, host_addr: int = 0) -> int:
        if size == 0 or alignment == 0 or (alignment & (alignment - 1)):
            raise ValueError("invalid buffer size/alignment")
        if len(self.buffers) >= CAD_MAX_BUFFERS:
            raise ValueError("too many buffers")
        buf = Buffer(
            id=len(self.buffers) + 1,
            size=size,
            alignment=alignment,
            host_addr=host_addr,
        )
        self.buffers.append(buf)
        return buf.id

    def add_mmul(
        self,
        input_id: int,
        weight_id: int,
        output_id: int,
        scale_id: int,
        M: int,
        K: int,
        N: int,
        deps: Optional[List[int]] = None,
    ) -> None:
        self.commands.append(
            Command(
                opcode=abi.EngineOp.MMUL,
                kind="mmul",
                buffers=[input_id, weight_id, output_id, scale_id],
                deps=deps or [],
                mmul=(M, K, N),
            )
        )

    def add_sfu(
        self,
        sfu_op: int,
        input_id: int,
        output_id: int,
        elements: int,
        head_dim: int = 0,
        pos: int = 0,
        deps: Optional[List[int]] = None,
    ) -> None:
        op_map = {
            0: abi.EngineOp.SFU_SOFTMAX,
            1: abi.EngineOp.SFU_LAYERNORM,
            2: abi.EngineOp.SFU_GELU,
            3: abi.EngineOp.SFU_RELU,
            4: abi.EngineOp.SFU_SILU,
            5: abi.EngineOp.ROPE,
            6: abi.EngineOp.SFU_RMSNORM,
        }
        self.commands.append(
            Command(
                opcode=op_map[sfu_op],
                kind="sfu",
                buffers=[input_id, output_id, 0, 0],
                deps=deps or [],
                sfu=(sfu_op, elements, head_dim, pos),
            )
        )

    def add_vector(
        self,
        vec_op: int,
        a_id: int,
        b_id: int,
        output_id: int,
        elements: int,
        deps: Optional[List[int]] = None,
    ) -> None:
        self.commands.append(
            Command(
                opcode=abi.EngineOp.VADD + vec_op,
                kind="vector",
                buffers=[a_id, b_id, output_id, 0],
                deps=deps or [],
                vector=(vec_op, elements),
            )
        )

    def add_dma_copy(
        self,
        src_id: int,
        src_offset: int,
        dst_id: int,
        dst_offset: int,
        size: int,
        deps: Optional[List[int]] = None,
    ) -> None:
        self.commands.append(
            Command(
                opcode=abi.EngineOp.DMA_COPY,
                kind="dma_copy",
                buffers=[src_id, dst_id, 0, 0],
                deps=deps or [],
                dma=(src_offset, dst_offset, size),
            )
        )

    def add_barrier(self) -> None:
        self.commands.append(Command(opcode=0xFF, kind="barrier"))

    def num_commands(self) -> int:
        return len(self.commands)

    def lower(self) -> LowerStatus:
        if self.lowered:
            return LowerStatus.OK

        next_sram = 0
        for buf in self.buffers:
            if buf.host_addr:
                buf.phys_addr = buf.host_addr
                if not (DRAM_BASE <= buf.phys_addr < DRAM_BASE + DRAM_SIZE):
                    return LowerStatus.ADDRESS_OVERFLOW
            else:
                next_sram = _align_up(next_sram, buf.alignment)
                if next_sram + buf.size > SRAM_SIZE:
                    return LowerStatus.ADDRESS_OVERFLOW
                buf.phys_addr = SRAM_BASE + next_sram
                next_sram += buf.size
            if buf.phys_addr & (buf.alignment - 1):
                return LowerStatus.INVALID_ALIGNMENT

        internal = [b for b in self.buffers if not b.host_addr]
        for i, a in enumerate(internal):
            for b in internal[i + 1 :]:
                a0, a1 = a.phys_addr, a.phys_addr + a.size
                b0, b1 = b.phys_addr, b.phys_addr + b.size
                if a0 < b1 and b0 < a1:
                    return LowerStatus.BUFFER_OVERLAP

        for idx, cmd in enumerate(self.commands):
            status = _validate_command(self, idx)
            if status != LowerStatus.OK:
                return status
            if cmd.kind != "barrier":
                cmd.desc_index = sum(
                    1 for c in self.commands[:idx] if c.kind != "barrier"
                )

        self.lowered = True
        return LowerStatus.OK

    def encode(self) -> bytes:
        return encode_blob(self)

    @staticmethod
    def decode(data: bytes) -> "CommandBlob":
        return decode_blob(data)


def _align_up(v: int, a: int) -> int:
    return (v + a - 1) & ~(a - 1)


def _validate_command(blob: CommandBlob, idx: int) -> LowerStatus:
    cmd = blob.commands[idx]
    if cmd.kind == "barrier":
        return LowerStatus.OK
    if cmd.kind == "mmul":
        M, K, N = cmd.mmul  # type: ignore[misc]
        if M == 0 or K == 0 or N == 0:
            return LowerStatus.INVALID_SHAPE
        num_k = (K + TILE_H - 1) // TILE_H
        num_n = (N + TILE_W - 1) // TILE_W
        last_k = K - (num_k - 1) * TILE_H
        last_n = N - (num_n - 1) * TILE_W
        if last_k == 0 or last_k > TILE_H or last_n == 0 or last_n > TILE_W:
            return LowerStatus.BAD_TILE
    elif cmd.kind == "sfu":
        if cmd.sfu[1] == 0:
            return LowerStatus.INVALID_SHAPE
    elif cmd.kind == "vector":
        if cmd.vector[1] == 0:
            return LowerStatus.INVALID_SHAPE
    elif cmd.kind == "dma_copy":
        if cmd.dma[2] == 0:
            return LowerStatus.INVALID_SHAPE
    return LowerStatus.OK


def load_manifest(path: str | Path) -> dict:
    with open(path, "r") as f:
        return json.load(f)
