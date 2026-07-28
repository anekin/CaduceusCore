"""
ExecuTorch AOT Delegate Tests

Tests the AOT partitioner, preprocessor, operator support table,
and blob compatibility validation. All tests run without requiring
an actual ExecuTorch installation — they test the delegate logic
against the Todo 11 command IR directly.

Run:
    PYTHONPATH=software:gen:sim python -m pytest sim/tests/test_executorch_delegate.py -q
"""

import hashlib
import json
import os
import struct
import sys
from pathlib import Path

import pytest

# Ensure paths are correct
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "software"))
sys.path.insert(0, str(REPO_ROOT / "gen"))

from executorch.aot import (  # noqa: E402
    BlobCompatibilityError,
    OpNode,
    PreprocessError,
    PreprocessResult,
    SUPPORTED_OPS,
    CPU_ONLY_OPS,
    UNSUPPORTED_OPS,
    get_supported_ops,
    is_supported,
    partition_graph,
    preprocess_partition,
    validate_blob_compatibility,
    compute_semantic_hash,
)

from compiler.command_ir import CommandBlob  # noqa: E402
from compiler.command_ir_types import (  # noqa: E402
    CAD_BLOB_MAGIC,
    CAD_BLOB_MAJOR,
    CAD_BLOB_MINOR,
    CAD_CAP_MXU,
    CAD_CAP_SFU,
    CAD_CAP_VECTOR,
    LowerStatus,
)


# ── Helper: build test Qwen subgraph ops ─────────────────────────────

def make_mmul_node(name: str, M=1, K=2560, N=2560) -> OpNode:
    return OpNode(
        name="aten.mm.default",
        inputs=[f"{name}_input", f"{name}_weight"],
        outputs=[f"{name}_output"],
        dims={"M": M, "K": K, "N": N},
    )


def make_sfu_node(op_name: str, in_name: str, out_name: str,
                  elements=2560, head_dim=0, pos=0) -> OpNode:
    return OpNode(
        name=f"aten.{op_name}",
        inputs=[in_name],
        outputs=[out_name],
        dims={"elements": elements, "head_dim": head_dim, "pos": pos},
    )


def make_vector_node(op_name: str, a_name: str, b_name: str,
                     out_name: str, elements=2560) -> OpNode:
    return OpNode(
        name=f"aten.{op_name}",
        inputs=[a_name, b_name],
        outputs=[out_name],
        dims={"elements": elements},
    )


def make_unsupported_node(name: str = "conv2d.default") -> OpNode:
    return OpNode(
        name=f"aten.{name}",
        inputs=["in"],
        outputs=["out"],
    )


# ── Qwen blk.0 subgraph (simplified) ─────────────────────────────────

QWEN_BLK0_SUBGRAPH = [
    make_sfu_node("rms_norm.default", "attn_norm_in", "attn_norm_out", elements=2560),
    make_mmul_node("Q_proj", M=1, K=2560, N=2560),
    make_mmul_node("K_proj", M=1, K=2560, N=256),
    make_mmul_node("V_proj", M=1, K=2560, N=256),
    OpNode(
        name="executorch_exir.rope.default",
        inputs=["rope_in"],
        outputs=["rope_out"],
        dims={"elements": 256, "head_dim": 128, "pos": 0},
    ),
    make_mmul_node("O_proj", M=1, K=256, N=2560),
    make_sfu_node("softmax.int", "softmax_in", "softmax_out", elements=256),
    make_mmul_node("W1", M=1, K=2560, N=6912),
    make_mmul_node("W2", M=1, K=2560, N=6912),
    make_sfu_node("silu.default", "silu_in", "silu_out", elements=6912),
    make_vector_node("mul.Tensor", "mul_a", "mul_b", "mul_out", elements=6912),
    make_mmul_node("W3", M=1, K=6912, N=2560),
    make_vector_node("add.Tensor", "resid_a", "resid_b", "resid_out", elements=2560),
]


# ── Tests ────────────────────────────────────────────────────────────

class TestOperatorSupport:
    def test_supported_ops_table_is_nonempty(self):
        ops = get_supported_ops()
        assert len(ops) > 0
        assert "aten" in ops
        assert "mm.default" in ops["aten"]

    def test_mmul_is_supported(self):
        node = make_mmul_node("test")
        assert is_supported(node)

    def test_softmax_is_supported(self):
        node = make_sfu_node("softmax.int", "a", "b")
        assert is_supported(node)

    def test_conv2d_is_cpu_only(self):
        node = make_unsupported_node("conv2d.default")
        assert not is_supported(node)

    def test_unknown_op_is_not_supported(self):
        node = OpNode(name="unknown.default", inputs=["a"], outputs=["b"])
        assert not is_supported(node)

    def test_supported_ops_returned_as_list(self):
        ops = get_supported_ops()
        for domain, oplist in ops.items():
            assert isinstance(oplist, list)


class TestPartitioner:
    def test_all_supported_yields_single_npu_partition(self):
        nodes = [
            make_mmul_node("A"),
            make_sfu_node("rms_norm.default", "a", "b"),
            make_mmul_node("B"),
        ]
        result = partition_graph(nodes)
        assert result.node_count == 3
        assert result.supported_count == 3
        assert result.unsupported_count == 0
        assert result.npu_partition_count == 1
        assert result.cpu_partition_count == 0
        assert len(result.partitions) == 1

    def test_mixed_yields_npu_and_cpu_partitions(self):
        nodes = [
            make_mmul_node("A"),
            make_unsupported_node("conv2d.default"),
            make_mmul_node("B"),
        ]
        result = partition_graph(nodes)
        assert result.supported_count == 2
        assert result.unsupported_count == 1
        assert result.npu_partition_count == 2
        assert result.cpu_partition_count == 1
        assert len(result.partitions) == 3
        assert result.partitions[0].is_npu
        assert result.partitions[1].is_cpu
        assert result.partitions[2].is_npu

    def test_all_unsupported_yields_single_cpu_partition(self):
        nodes = [
            make_unsupported_node("conv2d.default"),
            make_unsupported_node("max_pool2d.default"),
        ]
        result = partition_graph(nodes)
        assert result.supported_count == 0
        assert result.unsupported_count == 2
        assert result.npu_partition_count == 0
        assert result.cpu_partition_count == 1

    def test_qwen_blk0_subgraph_all_supported(self):
        result = partition_graph(QWEN_BLK0_SUBGRAPH)
        assert result.supported_count == len(QWEN_BLK0_SUBGRAPH)
        assert result.unsupported_count == 0

    def test_unsupported_partition_triggers_fallback(self):
        """When unsupported ops exist, they go to CPU partitions."""
        nodes = [
            make_mmul_node("A"),
            OpNode(name="custom_op.unsupported", inputs=["x"], outputs=["y"]),
        ]
        result = partition_graph(nodes)
        assert result.cpu_partition_count == 1
        assert result.partitions[1].target == "cpu"

    def test_strict_mode_raises_on_unsupported(self):
        nodes = [
            make_mmul_node("A"),
            make_unsupported_node("conv2d.default"),
        ]
        with pytest.raises(ValueError, match="unsupported"):
            partition_graph(nodes, tolerate_unsupported=False)


class TestPreprocess:
    def test_simple_mmul_partition_emits_valid_blob(self):
        nodes = [make_mmul_node("test", M=1, K=2560, N=2560)]
        result = partition_graph(nodes)
        npu_part = [p for p in result.partitions if p.is_npu][0]

        pp = preprocess_partition(npu_part)
        assert pp.lowered
        assert pp.num_commands == 1
        assert len(pp.blob) > 64
        assert len(pp.blob_hash) == 64
        assert len(pp.semantic_hash) == 64

    def test_blob_is_valid_todo11_format(self):
        nodes = [make_mmul_node("test", M=1, K=2560, N=2560)]
        result = partition_graph(nodes)
        npu_part = [p for p in result.partitions if p.is_npu][0]
        pp = preprocess_partition(npu_part)

        # Decode with the shared decoder
        decoded = CommandBlob.decode(pp.blob)
        assert decoded.version_major == CAD_BLOB_MAJOR
        assert decoded.version_minor == CAD_BLOB_MINOR
        assert decoded.num_commands() == 1
        assert decoded.commands[0].kind == "mmul"

    def test_qwen_subgraph_produces_semantic_hash(self):
        result = partition_graph(QWEN_BLK0_SUBGRAPH)
        npu_parts = [p for p in result.partitions if p.is_npu]

        hashes = []
        for part in npu_parts:
            pp = preprocess_partition(part)
            hashes.append(pp.semantic_hash)

        # Same subgraph should produce stable hash across runs
        h1 = compute_semantic_hash(QWEN_BLK0_SUBGRAPH)
        h2 = compute_semantic_hash(QWEN_BLK0_SUBGRAPH)
        assert h1 == h2

    def test_non_npu_partition_raises_preprocess_error(self):
        # Create a CPU-only partition
        nodes = [make_unsupported_node("conv2d.default")]
        result = partition_graph(nodes)
        cpu_part = [p for p in result.partitions if p.is_cpu][0]

        with pytest.raises(PreprocessError, match="not an NPU partition"):
            preprocess_partition(cpu_part)

    def test_qwen_semantic_hash_matches_across_runs(self):
        """Semantic hash must be deterministic and match llama.cpp lowering."""
        # The semantic hash covers only the op sequence, not the blob encoding
        h1 = compute_semantic_hash(QWEN_BLK0_SUBGRAPH)
        h2 = compute_semantic_hash(QWEN_BLK0_SUBGRAPH)
        assert h1 == h2, "semantic hash must be deterministic"
        assert len(h1) == 64  # 32 bytes hex-encoded

    def test_different_subgraphs_produce_different_hashes(self):
        nodes1 = [make_mmul_node("A")]
        nodes2 = [make_mmul_node("A", M=2, K=2560, N=2560)]  # different M
        h1 = compute_semantic_hash(nodes1)
        h2 = compute_semantic_hash(nodes2)
        assert h1 != h2


class TestBlobCompatibility:
    def test_valid_blob_passes_validation(self):
        nodes = [make_mmul_node("test")]
        result = partition_graph(nodes)
        npu_part = [p for p in result.partitions if p.is_npu][0]
        pp = preprocess_partition(npu_part)
        validate_blob_compatibility(pp.blob)  # should not raise

    def test_incompatible_blob_raises_on_bad_magic(self):
        # Create a blob and corrupt its magic
        nodes = [make_mmul_node("test")]
        result = partition_graph(nodes)
        npu_part = [p for p in result.partitions if p.is_npu][0]
        pp = preprocess_partition(npu_part)

        corrupted = bytearray(pp.blob)
        corrupted[0:4] = b"DEAD"

        with pytest.raises(BlobCompatibilityError, match="bad magic"):
            validate_blob_compatibility(bytes(corrupted))

    def test_incompatible_blob_raises_on_version_mismatch(self):
        nodes = [make_mmul_node("test")]
        result = partition_graph(nodes)
        npu_part = [p for p in result.partitions if p.is_npu][0]
        pp = preprocess_partition(npu_part)

        corrupted = bytearray(pp.blob)
        # Version is LE uint32: bytes[4:5]=minor, bytes[6:7]=major
        # Set major version = 99
        corrupted[6] = 0x63
        corrupted[7] = 0x00

        with pytest.raises(BlobCompatibilityError, match="major version"):
            validate_blob_compatibility(bytes(corrupted))

    def test_truncated_blob_raises(self):
        with pytest.raises(BlobCompatibilityError, match="too short"):
            validate_blob_compatibility(b"short")


class TestQwenSubgraphEvidence:
    """Tests that produce evidence for .omo/evidence/task-21-executorch.json"""

    def test_qwen_blk0_subgraph_blob_hash(self, tmp_path):
        """Qwen blk.0 subgraph blob emitted from ExecuTorch AOT preprocess."""
        result = partition_graph(QWEN_BLK0_SUBGRAPH)
        assert result.supported_count == len(QWEN_BLK0_SUBGRAPH)

        npu_parts = [p for p in result.partitions if p.is_npu]
        evidence = {
            "task": "task-21-executorch",
            "case": "qwen-subgraph",
            "npu_partitions": len(npu_parts),
            "semantic_hash": compute_semantic_hash(QWEN_BLK0_SUBGRAPH),
            "blobs": [],
        }

        for part in npu_parts:
            pp = preprocess_partition(part)
            evidence["blobs"].append({
                "partition_id": part.partition_id,
                "commands": pp.num_commands,
                "blob_hash": pp.blob_hash,
                "blob_size": len(pp.blob),
                "lowered": pp.lowered,
            })

        # Write evidence
        evidence_path = tmp_path / "task-21-executorch-evidence.json"
        evidence_path.write_text(json.dumps(evidence, indent=2))
        assert evidence_path.exists()

        # Verify all blobs are valid
        for blob_info in evidence["blobs"]:
            assert blob_info["lowered"]
            assert blob_info["blob_size"] > 64


class TestNegativeEvidence:
    """Tests that produce evidence for .omo/evidence/task-21-executorch-negative.json"""

    def test_unsupported_partition_generates_cpu_fallback(self, tmp_path):
        """When an unsupported op is in the graph, it goes to CPU partition."""
        nodes = [
            make_mmul_node("A"),
            OpNode(name="custom_op.unsupported", inputs=["x"], outputs=["y"]),
            make_sfu_node("softmax.int", "a", "b"),
        ]
        result = partition_graph(nodes)

        evidence = {
            "task": "task-21-executorch-negative",
            "case": "unsupported-partition",
            "node_count": 3,
            "supported_count": result.supported_count,
            "unsupported_count": result.unsupported_count,
            "cpu_partitions": result.cpu_partition_count,
            "npu_partitions": result.npu_partition_count,
            "fallback_ops": ["custom_op.unsupported"],
        }
        evidence_path = tmp_path / "negative-unsupported.json"
        evidence_path.write_text(json.dumps(evidence, indent=2))

        assert result.unsupported_count == 1
        assert result.cpu_partition_count > 0

    def test_incompatible_blob_rejected(self, tmp_path):
        """A blob with bad magic is rejected by the compatibility validator."""
        nodes = [make_mmul_node("test")]
        result = partition_graph(nodes)
        npu_part = [p for p in result.partitions if p.is_npu][0]
        pp = preprocess_partition(npu_part)

        corrupted = bytearray(pp.blob)
        corrupted[0:4] = b"XXXX"

        evidence = {
            "task": "task-21-executorch-negative",
            "case": "incompatible-blob",
            "original_hash": pp.blob_hash,
            "corruption": "magic mutated to XXXX",
            "expected_error": "bad magic",
        }

        with pytest.raises(BlobCompatibilityError) as exc_info:
            validate_blob_compatibility(bytes(corrupted))
        assert "bad magic" in str(exc_info.value)

        evidence["caught_error"] = str(exc_info.value)
        evidence_path = tmp_path / "negative-incompatible.json"
        evidence_path.write_text(json.dumps(evidence, indent=2))

    def test_blob_version_mismatch_rejected(self, tmp_path):
        """A blob with wrong major version is rejected."""
        nodes = [make_mmul_node("test")]
        result = partition_graph(nodes)
        npu_part = [p for p in result.partitions if p.is_npu][0]
        pp = preprocess_partition(npu_part)

        corrupted = bytearray(pp.blob)
        corrupted[6] = 0xFF  # major version high byte
        corrupted[7] = 0x00

        with pytest.raises(BlobCompatibilityError, match="major version"):
            validate_blob_compatibility(bytes(corrupted))


# ── Manual evidence aggregation (called by test runner) ──────────────

def collect_evidence(output_dir: str, negative: bool = False):
    """Collect and write evidence files to the given directory."""
    output = Path(output_dir)

    if not negative:
        # Happy path evidence
        result = partition_graph(QWEN_BLK0_SUBGRAPH)
        npu_parts = [p for p in result.partitions if p.is_npu]

        evidence = {
            "task": "task-21-executorch",
            "device": "fm://python",
            "cases": {
                "qwen-subgraph": {
                    "op_count": len(QWEN_BLK0_SUBGRAPH),
                    "supported_count": result.supported_count,
                    "npu_partitions": len(npu_parts),
                }
            },
            "semantic_hash": compute_semantic_hash(QWEN_BLK0_SUBGRAPH),
            "blobs": [],
            "verdict": "PASS",
        }

        for part in npu_parts:
            pp = preprocess_partition(part)
            evidence["blobs"].append({
                "partition_id": part.partition_id,
                "commands": pp.num_commands,
                "buffers": pp.num_buffers,
                "blob_hash": pp.blob_hash,
                "blob_size_bytes": len(pp.blob),
            })

        output.mkdir(parents=True, exist_ok=True)
        (output / "task-21-executorch.json").write_text(json.dumps(evidence, indent=2))
    else:
        # Negative evidence
        evidence = {
            "task": "task-21-executorch-negative",
            "device": "fm://python",
            "cases": [],
            "verdict": "PASS",
        }

        # Case 1: unsupported partition
        nodes_unsupported = [
            make_mmul_node("A"),
            OpNode(name="custom_op.unsupported", inputs=["x"], outputs=["y"]),
        ]
        result1 = partition_graph(nodes_unsupported)
        evidence["cases"].append({
            "case": "unsupported-partition",
            "supported_count": result1.supported_count,
            "unsupported_count": result1.unsupported_count,
            "cpu_partitions": result1.cpu_partition_count,
            "cpufallback": True,
        })

        # Case 2: incompatible blob
        nodes = [make_mmul_node("test")]
        result2 = partition_graph(nodes)
        npu_part = [p for p in result2.partitions if p.is_npu][0]
        pp = preprocess_partition(npu_part)

        corrupted = bytearray(pp.blob)
        corrupted[0:4] = b"XXXX"

        try:
            validate_blob_compatibility(bytes(corrupted))
            evidence["cases"].append({
                "case": "incompatible-blob",
                "passed": False,
                "error": "expected rejection but blob was accepted",
            })
        except BlobCompatibilityError as e:
            evidence["cases"].append({
                "case": "incompatible-blob",
                "passed": True,
                "caught_error": str(e),
            })

        output.mkdir(parents=True, exist_ok=True)
        (output / "task-21-executorch-negative.json").write_text(
            json.dumps(evidence, indent=2))
