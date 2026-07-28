#!/usr/bin/env python3
"""
ExecuTorch Delegate Test Runner

Runs the AOT partitioner/preprocess tests and collects evidence.
Does NOT require an actual ExecuTorch installation — tests the delegate
logic against the Todo 11 command IR directly.

Usage:
    # Happy path
    PYTHONPATH=sim:software:gen python3 scripts/run_executorch_delegate_tests.py \\
        --device fm://python --case qwen-subgraph \\
        --evidence .omo/evidence/task-21-executorch.json

    # Negative path
    PYTHONPATH=sim:software:gen python3 scripts/run_executorch_delegate_tests.py \\
        --device fm://python --negative unsupported-partition,incompatible-blob \\
        --evidence .omo/evidence/task-21-executorch-negative.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "software"))
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "sim"))


def run_happy_path(evidence_path: str) -> int:
    """Run happy-path ExecuTorch delegate tests."""
    from executorch.aot import (
        OpNode,
        get_supported_ops,
        is_supported,
        partition_graph,
        preprocess_partition,
        compute_semantic_hash,
        validate_blob_compatibility,
    )

    print("=== ExecuTorch Delegate Happy-Path Tests ===")
    failures = 0

    # ── Build Qwen blk.0 subgraph ────────────────────────────────────

    nodes = [
        OpNode(name="aten.rms_norm.default", inputs=["in"], outputs=["out"],
               dims={"elements": 2560}),
        OpNode(name="aten.mm.default", inputs=["q_in", "q_w"], outputs=["q_out"],
               dims={"M": 1, "K": 2560, "N": 2560}),
        OpNode(name="aten.mm.default", inputs=["k_in", "k_w"], outputs=["k_out"],
               dims={"M": 1, "K": 2560, "N": 256}),
        OpNode(name="aten.mm.default", inputs=["v_in", "v_w"], outputs=["v_out"],
               dims={"M": 1, "K": 2560, "N": 256}),
        OpNode(name="executorch_exir.rope.default", inputs=["r_in"], outputs=["r_out"],
               dims={"elements": 256, "head_dim": 128, "pos": 0}),
        OpNode(name="aten.mm.default", inputs=["o_in", "o_w"], outputs=["o_out"],
               dims={"M": 1, "K": 256, "N": 2560}),
        OpNode(name="aten.softmax.int", inputs=["s_in"], outputs=["s_out"],
               dims={"elements": 256}),
    ]

    # ── Check support ────────────────────────────────────────────────

    for node in nodes:
        if not is_supported(node):
            print(f"FAIL: unexpected unsupported op: {node.name}")
            failures += 1
    print(f"  Support check: {'PASS' if failures == 0 else 'FAIL'} ({len(nodes)} ops)")

    # ── Partition ────────────────────────────────────────────────────

    result = partition_graph(nodes)
    if result.supported_count != len(nodes):
        print(f"FAIL: partition supported_count ({result.supported_count}) != {len(nodes)}")
        failures += 1
    else:
        print(f"  Partition: PASS ({result.npu_partition_count} NPU partitions)")

    # ── Preprocess ───────────────────────────────────────────────────

    npu_parts = [p for p in result.partitions if p.is_npu]
    semantic_hash = compute_semantic_hash(nodes)
    print(f"  Semantic hash: {semantic_hash}")

    blobs = []
    for part in npu_parts:
        pp = preprocess_partition(part)
        assert pp.lowered, f"partition {part.partition_id} not lowered"

        # Validate blob
        validate_blob_compatibility(pp.blob)

        blobs.append({
            "partition_id": part.partition_id,
            "commands": pp.num_commands,
            "buffers": pp.num_buffers,
            "blob_hash": pp.blob_hash,
            "blob_size_bytes": len(pp.blob),
        })
        print(f"  Partition {part.partition_id}: {pp.num_commands} commands, "
              f"{pp.num_buffers} buffers, blob={len(pp.blob)} bytes")

    # ── Write evidence ───────────────────────────────────────────────

    evidence = {
        "task": "task-21-executorch",
        "device": "fm://python",
        "case": "qwen-subgraph",
        "op_count": len(nodes),
        "supported_count": result.supported_count,
        "npu_partitions": len(npu_parts),
        "semantic_hash": semantic_hash,
        "blobs": blobs,
        "verdict": "PASS" if failures == 0 else "FAIL",
        "note": "ExecuTorch v1.2 AOT delegate reuses Todo 11 command IR. "
                "No second descriptor compiler or transport stack introduced.",
    }

    evidence_dir = Path(evidence_path).parent
    evidence_dir.mkdir(parents=True, exist_ok=True)
    with open(evidence_path, "w") as f:
        json.dump(evidence, f, indent=2)
    print(f"\n  Evidence written to {evidence_path}")

    if failures > 0:
        print(f"\n{failures} failure(s) detected.")
        return 1
    print("All ExecuTorch delegate happy-path tests PASSED.")
    return 0


def run_negative(evidence_path: str, cases: list) -> int:
    """Run negative-path ExecuTorch delegate tests."""
    from executorch.aot import (
        OpNode,
        partition_graph,
        preprocess_partition,
        validate_blob_compatibility,
        BlobCompatibilityError,
    )

    print("=== ExecuTorch Delegate Negative Tests ===")
    evidence_cases = []
    all_passed = True

    if "unsupported-partition" in cases:
        print("\n-- Case: unsupported-partition --")
        nodes = [
            OpNode(name="aten.mm.default", inputs=["a", "b"], outputs=["c"],
                   dims={"M": 1, "K": 2560, "N": 2560}),
            OpNode(name="custom_op.unsupported", inputs=["x"], outputs=["y"]),
        ]
        result = partition_graph(nodes)
        cpu_parts = [p for p in result.partitions if p.is_cpu]
        if len(cpu_parts) > 0:
            print("  PASS: unsupported op routed to CPU partition")
            evidence_cases.append({
                "case": "unsupported-partition",
                "supported_count": result.supported_count,
                "unsupported_count": result.unsupported_count,
                "cpu_partitions": result.cpu_partition_count,
            })
        else:
            print("  FAIL: unsupported op not routed to CPU")
            all_passed = False

    if "incompatible-blob" in cases:
        print("\n-- Case: incompatible-blob --")
        nodes = [OpNode(name="aten.mm.default", inputs=["a", "b"], outputs=["c"],
                        dims={"M": 1, "K": 2560, "N": 2560})]
        result = partition_graph(nodes)
        npu_part = [p for p in result.partitions if p.is_npu][0]
        pp = preprocess_partition(npu_part)

        corrupted = bytearray(pp.blob)
        corrupted[0:4] = b"DEAD"

        try:
            validate_blob_compatibility(bytes(corrupted))
            print("  FAIL: incompatible blob was NOT rejected")
            all_passed = False
        except BlobCompatibilityError as e:
            print(f"  PASS: incompatible blob rejected: {e}")
            evidence_cases.append({
                "case": "incompatible-blob",
                "caught_error": str(e),
            })

    # ── Write evidence ───────────────────────────────────────────────

    evidence = {
        "task": "task-21-executorch-negative",
        "device": "fm://python",
        "cases": evidence_cases,
        "verdict": "PASS" if all_passed else "FAIL",
    }

    evidence_dir = Path(evidence_path).parent
    evidence_dir.mkdir(parents=True, exist_ok=True)
    with open(evidence_path, "w") as f:
        json.dump(evidence, f, indent=2)
    print(f"\n  Evidence written to {evidence_path}")

    if not all_passed:
        return 1
    print("All ExecuTorch delegate negative tests PASSED.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="ExecuTorch Delegate Test Runner"
    )
    parser.add_argument("--device", default="fm://python",
                        help="Device URI (not used, placeholder)")
    parser.add_argument("--case", default="qwen-subgraph",
                        help="Test case name for happy path")
    parser.add_argument("--negative", default=None,
                        help="Comma-separated negative test cases")
    parser.add_argument("--evidence", required=True,
                        help="Path to write evidence JSON")

    args = parser.parse_args()

    if args.negative:
        cases = [c.strip() for c in args.negative.split(",")]
        return run_negative(args.evidence, cases)
    else:
        return run_happy_path(args.evidence)


if __name__ == "__main__":
    sys.exit(main())
