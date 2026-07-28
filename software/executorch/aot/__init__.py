"""CaduceusCore ExecuTorch Delegate AOT — partitioner and preprocess."""

from .partitioner import (
    OpNode,
    Partition,
    PartitionedGraph,
    SUPPORTED_OPS,
    UNSUPPORTED_OPS,
    CPU_ONLY_OPS,
    get_supported_ops,
    is_supported,
    partition_graph,
)

from .preprocess import (
    BufferSpec,
    PreprocessResult,
    PreprocessError,
    BlobCompatibilityError,
    compute_semantic_hash,
    preprocess_partition,
    validate_blob_compatibility,
)

__all__ = [
    "OpNode",
    "Partition",
    "PartitionedGraph",
    "SUPPORTED_OPS",
    "UNSUPPORTED_OPS",
    "CPU_ONLY_OPS",
    "get_supported_ops",
    "is_supported",
    "partition_graph",
    "BufferSpec",
    "PreprocessResult",
    "PreprocessError",
    "BlobCompatibilityError",
    "compute_semantic_hash",
    "preprocess_partition",
    "validate_blob_compatibility",
]
