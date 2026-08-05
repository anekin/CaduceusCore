"""Low-level blob codec helpers for command_ir.py."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .command_ir import CommandBlob

from .command_ir_types import (
    CAD_BLOB_MAGIC,
    CAD_BLOB_MAJOR,
    CAD_BLOB_MINOR,
    CAD_CMD_ENTRY_BYTES,
    CAD_DESC_BYTES,
    CAD_DESC_WORDS,
    HEADER_SIZE,
    Buffer,
    Command,
    LowerStatus,
)

import gen.npu_abi as abi


def encode_blob(blob: "CommandBlob") -> bytes:
    if not blob.lowered:
        raise RuntimeError("blob must be lowered before encode")

    desc_count = sum(1 for c in blob.commands if c.kind != "barrier")
    size = (
        HEADER_SIZE
        + len(blob.buffers) * 32
        + len(blob.commands) * CAD_CMD_ENTRY_BYTES
        + desc_count * CAD_DESC_BYTES
    )
    buf = bytearray(size)

    buf[0:4] = struct.pack("<I", CAD_BLOB_MAGIC)
    buf[4:8] = struct.pack("<I", (blob.version_major << 16) | blob.version_minor)
    buf[8:12] = struct.pack("<I", HEADER_SIZE)
    buf[12:16] = struct.pack("<I", blob.caps)
    buf[16:20] = struct.pack("<I", len(blob.buffers))
    buf[20:24] = struct.pack("<I", len(blob.commands))
    buf[24:28] = struct.pack("<I", desc_count * CAD_DESC_BYTES)
    buf[32:36] = struct.pack("<I", len(blob.commands) * CAD_CMD_ENTRY_BYTES)
    buf[40:44] = struct.pack("<I", len(blob.buffers) * 32)

    off = HEADER_SIZE
    buf_table_off = off
    for b in blob.buffers:
        buf[off : off + 4] = struct.pack("<I", b.id)
        buf[off + 4 : off + 8] = struct.pack("<I", b.size)
        buf[off + 8 : off + 12] = struct.pack("<I", b.alignment)
        buf[off + 12 : off + 20] = struct.pack("<Q", b.phys_addr)
        off += 32

    cmd_ring_off = off
    for i, cmd in enumerate(blob.commands):
        entry_off = cmd_ring_off + i * CAD_CMD_ENTRY_BYTES
        buf[entry_off : entry_off + 4] = struct.pack("<I", cmd.opcode)
        buf[entry_off + 4 : entry_off + 8] = struct.pack("<I", cmd.desc_index * CAD_DESC_BYTES)
        dep_mask = 0
        for dep in cmd.deps:
            if 1 <= dep <= 32:
                dep_mask |= 1 << (dep - 1)
        buf[entry_off + 8 : entry_off + 12] = struct.pack("<I", dep_mask)
        off += CAD_CMD_ENTRY_BYTES

    desc_table_off = off
    d = 0
    for cmd in blob.commands:
        if cmd.kind == "barrier":
            continue
        desc = _build_descriptor(blob, cmd)
        buf[off + d * CAD_DESC_BYTES : off + (d + 1) * CAD_DESC_BYTES] = desc
        d += 1

    buf[28:32] = struct.pack("<I", desc_table_off)
    buf[36:40] = struct.pack("<I", cmd_ring_off)
    buf[44:48] = struct.pack("<I", buf_table_off)
    return bytes(buf)


def decode_blob(data: bytes) -> "CommandBlob":
    from .command_ir import CommandBlob

    if len(data) < HEADER_SIZE:
        raise ValueError("truncated blob")
    magic = struct.unpack("<I", data[0:4])[0]
    if magic != CAD_BLOB_MAGIC:
        raise ValueError("bad magic")
    version = struct.unpack("<I", data[4:8])[0]
    major, minor = version >> 16, version & 0xFFFF
    if major != CAD_BLOB_MAJOR or minor > CAD_BLOB_MINOR:
        raise ValueError("unsupported blob version")
    caps = struct.unpack("<I", data[12:16])[0]
    buf_count = struct.unpack("<I", data[16:20])[0]
    cmd_count = struct.unpack("<I", data[20:24])[0]
    desc_size = struct.unpack("<I", data[24:28])[0]
    desc_off = struct.unpack("<I", data[28:32])[0]
    cmd_size = struct.unpack("<I", data[32:36])[0]
    cmd_off = struct.unpack("<I", data[36:40])[0]
    bt_size = struct.unpack("<I", data[40:44])[0]
    bt_off = struct.unpack("<I", data[44:48])[0]

    if cmd_size != cmd_count * CAD_CMD_ENTRY_BYTES:
        raise ValueError("command ring size mismatch")
    if (
        desc_off + desc_size > len(data)
        or cmd_off + cmd_size > len(data)
        or bt_off + bt_size > len(data)
    ):
        raise ValueError("table extends past blob")

    blob = CommandBlob(caps=caps, version_major=major, version_minor=minor, lowered=True)
    for i in range(buf_count):
        off = bt_off + i * 32
        blob.buffers.append(
            Buffer(
                id=struct.unpack("<I", data[off : off + 4])[0],
                size=struct.unpack("<I", data[off + 4 : off + 8])[0],
                alignment=struct.unpack("<I", data[off + 8 : off + 12])[0],
                phys_addr=struct.unpack("<Q", data[off + 12 : off + 20])[0],
                host_addr=struct.unpack("<Q", data[off + 12 : off + 20])[0],
            )
        )

    d = 0
    for i in range(cmd_count):
        entry_off = cmd_off + i * CAD_CMD_ENTRY_BYTES
        opcode = struct.unpack("<I", data[entry_off : entry_off + 4])[0]
        if opcode == 0xFF:
            blob.commands.append(Command(opcode=0xFF, kind="barrier"))
            continue
        desc = data[desc_off + d * CAD_DESC_BYTES : desc_off + (d + 1) * CAD_DESC_BYTES]
        cmd = _decode_descriptor(blob, opcode, desc)
        cmd.desc_index = d
        blob.commands.append(cmd)
        d += 1

    return blob


def _build_descriptor(blob: "CommandBlob", cmd: Command) -> bytes:
    d = [0] * CAD_DESC_WORDS
    if cmd.kind == "mmul":
        bufs = [blob.buffers[b - 1] if b else None for b in cmd.buffers]
        M, K, N = cmd.mmul  # type: ignore[misc]
        d[0] = bufs[0].phys_addr
        d[1] = bufs[1].phys_addr
        d[2] = bufs[2].phys_addr
        d[3] = bufs[3].phys_addr if bufs[3] else 0
        d[8] = M * K
        d[9] = K * N // 2
        d[10] = M * N * 4
        d[11] = bufs[3].phys_addr if bufs[3] else 0
        d[12] = M
        d[13] = K
        d[14] = N
    elif cmd.kind == "sfu":
        bufs = [blob.buffers[b - 1] for b in cmd.buffers[:2]]
        sfu_op, elements, head_dim, pos = cmd.sfu  # type: ignore[misc]
        d[0] = bufs[0].phys_addr
        d[2] = bufs[1].phys_addr
        if blob.version_minor >= 1:
            d[8] = elements
            d[9] = head_dim
            d[10] = pos
            d[11] = sfu_op
        else:
            d[8] = (head_dim << 16) | (elements & 0xFFFF)
            d[9] = pos
            d[10] = sfu_op
    elif cmd.kind == "vector":
        bufs = [blob.buffers[b - 1] if b else None for b in cmd.buffers[:3]]
        _, elements = cmd.vector  # type: ignore[misc]
        d[0] = bufs[0].phys_addr
        d[1] = bufs[1].phys_addr if bufs[1] else 0
        d[2] = bufs[2].phys_addr
        d[8] = elements
    elif cmd.kind == "dma_copy":
        bufs = [blob.buffers[b - 1] for b in cmd.buffers[:2]]
        src_offset, dst_offset, size = cmd.dma  # type: ignore[misc]
        d[0] = bufs[0].phys_addr + src_offset
        d[2] = bufs[1].phys_addr + dst_offset
        d[8] = size
    return struct.pack("<15I", *d)


def _decode_descriptor(blob: "CommandBlob", opcode: int, desc: bytes) -> Command:
    d = list(struct.unpack("<15I", desc))
    if opcode == abi.EngineOp.MMUL:
        return Command(
            opcode=opcode,
            kind="mmul",
            buffers=[
                _find_buf(blob, d[0]),
                _find_buf(blob, d[1]),
                _find_buf(blob, d[2]),
                _find_buf(blob, d[3]) if d[3] else 0,
            ],
            mmul=(d[12], d[13], d[14]),
        )
    elif opcode in (
        abi.EngineOp.SFU_SOFTMAX,
        abi.EngineOp.SFU_LAYERNORM,
        abi.EngineOp.SFU_GELU,
        abi.EngineOp.SFU_RELU,
        abi.EngineOp.SFU_SILU,
        abi.EngineOp.ROPE,
        abi.EngineOp.SFU_RMSNORM,
    ):
        if blob.version_minor >= 1:
            elements = d[8]
            head_dim = d[9]
            pos = d[10]
            sfu_op = d[11]
        else:
            elements = d[8] & 0xFFFF
            head_dim = (d[8] >> 16) & 0xFFFF
            pos = d[9]
            sfu_op = d[10]
        return Command(
            opcode=opcode,
            kind="sfu",
            buffers=[_find_buf(blob, d[0]), _find_buf(blob, d[2]), 0, 0],
            sfu=(sfu_op, elements, head_dim, pos),
        )
    elif opcode in (
        abi.EngineOp.VADD,
        abi.EngineOp.VMUL,
        abi.EngineOp.VRED_MAX,
        abi.EngineOp.VRED_SUM,
        abi.EngineOp.VCONV,
        abi.EngineOp.VRESID,
    ):
        return Command(
            opcode=opcode,
            kind="vector",
            buffers=[
                _find_buf(blob, d[0]),
                _find_buf(blob, d[1]) if d[1] else 0,
                _find_buf(blob, d[2]),
                0,
            ],
            vector=(opcode - abi.EngineOp.VADD, d[8]),
        )
    elif opcode == abi.EngineOp.DMA_COPY:
        return Command(
            opcode=opcode,
            kind="dma_copy",
            buffers=[_find_buf(blob, d[0]), _find_buf(blob, d[2]), 0, 0],
            dma=(0, 0, d[8]),
        )
    raise ValueError(f"cannot decode opcode {opcode}")


def _find_buf(blob: "CommandBlob", addr: int) -> int:
    for buf in blob.buffers:
        if buf.phys_addr == addr:
            return buf.id
    raise ValueError(f"address 0x{addr:x} not found in buffer table")
