"""Shared types and constants for the command IR."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional, Tuple

import gen.npu_abi as abi

CAD_BLOB_MAGIC = 0x43414442
CAD_BLOB_MAJOR = 1
CAD_BLOB_MINOR = 1
CAD_MAX_BUFFERS = 256
CAD_MAX_COMMANDS = 1024
CAD_MAX_DEPS = 32
CAD_DESC_WORDS = 15
CAD_DESC_BYTES = CAD_DESC_WORDS * 4
CAD_CMD_ENTRY_WORDS = 8
CAD_CMD_ENTRY_BYTES = CAD_CMD_ENTRY_WORDS * 4
HEADER_SIZE = 64

CAD_CAP_MXU = 1
CAD_CAP_SFU = 2
CAD_CAP_VECTOR = 4
CAD_CAP_DMA = 8
CAD_CAP_PCIE = 16

CAD_OP_MMUL = abi.EngineOp.MMUL

TILE_H = 64
TILE_W = 64
SRAM_BASE = abi.Addr.SRAM
SRAM_SIZE = 0x00400000
DRAM_BASE = abi.Addr.DRAM
DRAM_SIZE = 0x80000000


class LowerStatus(IntEnum):
    OK = 0
    INVALID_SHAPE = 1
    INVALID_ALIGNMENT = 2
    BUFFER_OVERLAP = 3
    ADDRESS_OVERFLOW = 4
    UNSUPPORTED_OP = 5
    BAD_TILE = 6
    INVALID_DEPENDENCY = 7
    OUT_OF_MEMORY = 8
    INVALID_BLOB = 9


@dataclass
class Buffer:
    id: int
    size: int
    alignment: int
    host_addr: int = 0
    phys_addr: int = 0


@dataclass
class Command:
    opcode: int
    kind: str
    buffers: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    deps: List[int] = field(default_factory=list)
    desc_index: int = 0
    mmul: Optional[Tuple[int, int, int]] = None
    sfu: Optional[Tuple[int, int, int, int]] = None
    vector: Optional[Tuple[int, int]] = None
    dma: Optional[Tuple[int, int, int]] = None
