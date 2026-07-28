"""
CaduceusCore ExecuTorch AOT Preprocess

Takes a partitioned NPU subgraph and emits the Todo 11 compiled-command blob.
Reuses the shared compiler (software/compiler/command_ir.py) for blob generation.
No second descriptor compiler or transport stack is introduced.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import sys
from pathlib import Path

# Ensure the repo-root software/compiler is importable
_REPO = Path(__file__).resolve().parents[3]
_SOFTWARE = _REPO / "software"
if str(_SOFTWARE) not in sys.path:
    sys.path.insert(0, str(_SOFTWARE))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from compiler.command_ir import CommandBlob  # noqa: E402
from compiler.command_ir_types import (  # noqa: E402
    CAD_CAP_MXU,
    CAD_CAP_SFU,
    CAD_CAP_VECTOR,
    CAD_CAP_DMA,
    LowerStatus,
)

from .partitioner import Partition, OpNode  # noqa: E402


# ── Op-to-IR mapping ────────────────────────────────────────────────

MMUL_OPS = {"mm.default", "bmm.default", "addmm.default", "linear.default"}
SFU_OPS = {
    "softmax.int": 0,       # SFU_SOFTMAX
    "layer_norm.default": 1, # SFU_LAYERNORM
    "gelu.default": 2,       # SFU_GELU
    "relu.default": 3,       # SFU_RELU
    "silu.default": 4,       # SFU_SILU
    "rope.default": 5,       # ROPE
    "rms_norm.default": 6,   # SFU_RMSNORM
}
VECTOR_OPS = {
    "add.Tensor": 0,         # VADD
    "mul.Tensor": 1,         # VMUL
    "amax.default": 2,       # VRED_MAX
    "sum.default": 3,        # VRED_SUM
}


@dataclass
class PreprocessResult:
    """Result of preprocessing an NPU partition."""
    blob: bytes                       # The encoded command blob
    blob_hash: str                    # SHA-256 hex digest of blob
    semantic_hash: str                # Blake2b-256 of op sequence (stable across runs)
    num_buffers: int
    num_commands: int
    lowered: bool


@dataclass
class BufferSpec:
    """Specification for a buffer declaration before lowering."""
    id: int
    size: int
    alignment: int
    host_addr: int = 0
    name: str = ""


@dataclass
class PreprocessError(Exception):
    """Error during preprocessing."""
    def __init__(self, code: str, message: str, node_name: Optional[str] = None):
        self.code = code
        self.message = message
        self.node_name = node_name
        super().__init__(message)


def compute_semantic_hash(ops: List[OpNode]) -> str:
    """Compute a deterministic semantic hash of the operator sequence.

    This hash is stable across runs and matches the hash produced by
    llama.cpp lowering for the same logical subgraph.
    """
    h = hashlib.blake2b(digest_size=32)
    for node in ops:
        h.update(node.name.encode())
        h.update(b"\x00")
        for k in sorted(node.dims.keys()):
            h.update(k.encode())
            h.update(struct.pack("<i", node.dims[k]))
        h.update(b"\x01")
    return h.hexdigest()


def preprocess_partition(partition: Partition) -> PreprocessResult:
    """Preprocess a single NPU partition into a compiled command blob.

    This reuses the Todo 11 CommandBlob builder and lowerer.
    No second descriptor compiler or transport stack is introduced.
    """
    if not partition.is_npu:
        raise PreprocessError(
            code="NOT_NPU",
            message=f"partition {partition.partition_id} is not an NPU partition",
        )

    # Determine required capabilities
    caps = 0
    for node in partition.nodes:
        op = node.op_name
        if op in MMUL_OPS:
            caps |= CAD_CAP_MXU
        if op in SFU_OPS:
            caps |= CAD_CAP_SFU
        if op in VECTOR_OPS:
            caps |= CAD_CAP_VECTOR

    if caps == 0:
        caps = CAD_CAP_MXU | CAD_CAP_SFU | CAD_CAP_VECTOR | CAD_CAP_DMA

    blob = CommandBlob(caps=caps)

    # Phase 1: Declare buffers for each distinct tensor in the partition
    buffer_map: Dict[str, int] = {}  # tensor_name → buffer_id
    DEFAULT_SIZE = 65536  # conservative default for 256×256 int32
    DEFAULT_ALIGN = 64

    for node in partition.nodes:
        for inp in node.inputs:
            if inp not in buffer_map:
                size = node.dims.get(f"{inp}_size", DEFAULT_SIZE)
                bid = blob.declare_buffer(size, DEFAULT_ALIGN, 0x80000000 + len(buffer_map) * 0x10000)
                buffer_map[inp] = bid
        for out in node.outputs:
            if out not in buffer_map:
                size = node.dims.get(f"{out}_size", DEFAULT_SIZE)
                bid = blob.declare_buffer(size, DEFAULT_ALIGN, 0x80000000 + len(buffer_map) * 0x10000)
                buffer_map[out] = bid

    # Phase 2: Emit commands
    for node in partition.nodes:
        op = node.op_name
        inp_ids = [buffer_map.get(inp, 0) for inp in node.inputs]
        out_ids = [buffer_map.get(out, 0) for out in node.outputs]

        if op in MMUL_OPS:
            M = node.dims.get("M", 1)
            K = node.dims.get("K", 2560)
            N = node.dims.get("N", 2560)
            blob.add_mmul(
                input_id=inp_ids[0] if inp_ids else 0,
                weight_id=inp_ids[1] if len(inp_ids) > 1 else 0,
                output_id=out_ids[0] if out_ids else 0,
                scale_id=inp_ids[2] if len(inp_ids) > 2 else 0,
                M=M, K=K, N=N,
            )
        elif op in SFU_OPS:
            sfu_op = SFU_OPS[op]
            elements = node.dims.get("elements", 2560)
            head_dim = node.dims.get("head_dim", 0)
            pos = node.dims.get("pos", 0)
            blob.add_sfu(
                sfu_op=sfu_op,
                input_id=inp_ids[0] if inp_ids else 0,
                output_id=out_ids[0] if out_ids else 0,
                elements=elements,
                head_dim=head_dim,
                pos=pos,
            )
        elif op in VECTOR_OPS:
            vec_op = VECTOR_OPS[op]
            elements = node.dims.get("elements", 2560)
            blob.add_vector(
                vec_op=vec_op,
                a_id=inp_ids[0] if inp_ids else 0,
                b_id=inp_ids[1] if len(inp_ids) > 1 else 0,
                output_id=out_ids[0] if out_ids else 0,
                elements=elements,
            )

    # Phase 3: Lower and encode
    status = blob.lower()
    if status != LowerStatus.OK:
        raise PreprocessError(
            code=status.name,
            message=f"lowering failed: {status.name}",
        )

    encoded = blob.encode()

    return PreprocessResult(
        blob=encoded,
        blob_hash=hashlib.sha256(encoded).hexdigest(),
        semantic_hash=compute_semantic_hash(partition.nodes),
        num_buffers=len(blob.buffers),
        num_commands=blob.num_commands(),
        lowered=True,
    )


class BlobCompatibilityError(ValueError):
    """Raised when a preprocessed blob is incompatible with the current runtime."""
    pass


def validate_blob_compatibility(blob: bytes) -> None:
    """Validate that an encoded blob is compatible with the current ABI.

    Checks magic number and version. Raises BlobCompatibilityError on mismatch.
    """
    from compiler.command_ir_types import CAD_BLOB_MAGIC, CAD_BLOB_MAJOR, CAD_BLOB_MINOR
    if len(blob) < 8:
        raise BlobCompatibilityError("blob too short for header")
    magic = struct.unpack("<I", blob[0:4])[0]
    if magic != CAD_BLOB_MAGIC:
        raise BlobCompatibilityError(f"bad magic: expected 0x{CAD_BLOB_MAGIC:08X}, got 0x{magic:08X}")
    version = struct.unpack("<I", blob[4:8])[0]
    major, minor = version >> 16, version & 0xFFFF
    if major != CAD_BLOB_MAJOR:
        raise BlobCompatibilityError(f"major version mismatch: expected {CAD_BLOB_MAJOR}, got {major}")
    if minor > CAD_BLOB_MINOR:
        raise BlobCompatibilityError(f"minor version too high: expected <= {CAD_BLOB_MINOR}, got {minor}")
