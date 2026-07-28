"""
CaduceusCore ExecuTorch AOT Partitioner

Operator support table and partitioner for the Caduceus NPU delegate.
Delegates supported ops to NPU (emitting Todo 11 command blobs) and
unsupported ops to CPU fallback.

Pin: ExecuTorch v1.2.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# ── Operator support table ──────────────────────────────────────────

SUPPORTED_OPS: Dict[str, Set[str]] = {
    "aten": {
        "mm.default",           # Matrix multiply → MMUL
        "bmm.default",          # Batch matrix multiply → MMUL
        "addmm.default",        # Add + mm → MMUL
        "linear.default",       # Linear → MMUL
        "softmax.int",          # Softmax → SFU
        "layer_norm.default",   # LayerNorm → SFU
        "rms_norm.default",     # RMSNorm → SFU
        "gelu.default",         # GELU → SFU
        "silu.default",         # SiLU → SFU
        "relu.default",         # ReLU → SFU (passthrough to CPU unless chained)
        "add.Tensor",           # Vector add
        "mul.Tensor",           # Vector mul
        "amax.default",         # Vector max reduce
        "sum.default",          # Vector sum reduce
        "reshape.default",      # Reshape (passthrough)
        "view.default",         # View (passthrough)
        "permute.default",      # Permute (passthrough)
    },
    "executorch_exir": {
        "rope.default",         # RoPE → SFU
        "rms_norm.default",     # RMSNorm (EXIR dialect) → SFU
        "sdpa_with_kv_cache.default",  # SDPA → MMUL + SFU
    },
}

# Operators always delegated to CPU
CPU_ONLY_OPS: Set[str] = {
    "conv2d.default",    # 2D convolution — may be supported later
    "max_pool2d.default",
    "avg_pool2d.default",
    "embedding.default", # Embedding lookup
    "topk.default",
    "argmax.default",
    "gather.default",
    "scatter.default",
    "random.default",
    "dropout.default",
    "slice.default",
}

# Operators that are always unsupported
UNSUPPORTED_OPS: Set[str] = {
    "custom_op.unsupported",
    "unknown.default",
}


@dataclass
class OpNode:
    """A single operator node in the model graph."""
    name: str           # e.g., "aten.mm.default"
    inputs: List[str]   # input node names
    outputs: List[str]  # output node names
    dims: Dict[str, int] = field(default_factory=dict)  # M, K, N, elements, etc.
    meta: Dict = field(default_factory=dict)  # additional metadata

    @property
    def domain(self) -> str:
        # "aten.mm.default" → domain="aten"
        return self.name.split(".", 1)[0] if "." in self.name else self.name

    @property
    def op_name(self) -> str:
        # "aten.mm.default" → op_name="mm.default"
        return self.name.split(".", 1)[1] if "." in self.name else self.name


@dataclass
class Partition:
    """A partition of operators — either delegated to NPU or falling back to CPU."""
    nodes: List[OpNode]
    target: str  # "npu" or "cpu"
    partition_id: int

    @property
    def is_npu(self) -> bool:
        return self.target == "npu"

    @property
    def is_cpu(self) -> bool:
        return self.target == "cpu"


@dataclass
class PartitionedGraph:
    """Result of partitioning a model graph."""
    partitions: List[Partition]
    node_count: int
    supported_count: int
    unsupported_count: int
    npu_partition_count: int
    cpu_partition_count: int


def is_supported(node: OpNode) -> bool:
    """Check if a single operator is supported by the NPU delegate."""
    if node.name in UNSUPPORTED_OPS:
        return False
    if node.name in CPU_ONLY_OPS:
        return False

    domain = node.domain
    op = node.op_name
    if domain in SUPPORTED_OPS:
        return op in SUPPORTED_OPS[domain]
    return False


def partition_graph(nodes: List[OpNode], tolerate_unsupported: bool = True) -> PartitionedGraph:
    """Partition a list of operator nodes into NPU and CPU partitions.

    Adjacent supported nodes are grouped into one NPU partition.
    Adjacent unsupported nodes are grouped into one CPU partition.
    GPU-to-CPU and CPU-to-GPU edges become partition boundaries.

    If `tolerate_unsupported` is False, raises ValueError on first unsupported op.
    """
    partitions: List[Partition] = []
    current_partition: List[OpNode] = []
    current_target: Optional[str] = None
    supported_count = 0
    unsupported_count = 0
    pid = 0

    for node in nodes:
        supported = is_supported(node)
        if supported:
            supported_count += 1
            target = "npu"
        else:
            unsupported_count += 1
            if not tolerate_unsupported:
                raise ValueError(f"unsupported operator in strict mode: {node.name}")
            target = "cpu"

        if current_target is None:
            current_target = target
            current_partition.append(node)
        elif current_target == target:
            current_partition.append(node)
        else:
            partitions.append(Partition(
                nodes=current_partition,
                target=current_target,
                partition_id=pid,
            ))
            pid += 1
            current_partition = [node]
            current_target = target

    if current_partition:
        partitions.append(Partition(
            nodes=current_partition,
            target=current_target,
            partition_id=pid,
        ))

    npu_count = sum(1 for p in partitions if p.is_npu)
    cpu_count = sum(1 for p in partitions if p.is_cpu)

    return PartitionedGraph(
        partitions=partitions,
        node_count=len(nodes),
        supported_count=supported_count,
        unsupported_count=unsupported_count,
        npu_partition_count=npu_count,
        cpu_partition_count=cpu_count,
    )


def get_supported_ops() -> Dict[str, List[str]]:
    """Return the complete operator support table."""
    result: Dict[str, List[str]] = {}
    for domain, ops in SUPPORTED_OPS.items():
        result[domain] = sorted(ops)
    return result
