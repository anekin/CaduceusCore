#!/usr/bin/env python3
"""CV inference host runner for CaduceusCore.

Loads a MobileNetV3-Small ONNX model, converts it to Caduceus command IR
using B1's converter, submits the command blob through the Host Runtime
Python API (cadDeviceOpen, cadBufferAlloc, cadQueueSubmit) to ``device_server``
via ``fm://python``, reads the result buffer, and returns the classification
output.

Usage::

    PYTHONPATH=sim python3 sim/cv/cv_host_runner.py \\
        --model assets/mobilenetv3_small.onnx --device fm://python
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
from pathlib import Path

# ── Path setup (mirrors cv_command_ir.py) ─────────────────────────────────
_cv_dir = Path(__file__).resolve().parent
_sim_dir = _cv_dir.parent
_repo_root = _sim_dir.parent
_sw_dir = _repo_root / "software"
sys.path.insert(0, str(_repo_root / "sim"))

from signoff._ensure_pythonpath import ensure_repo_pythonpath  # noqa: E402

ensure_repo_pythonpath(_repo_root)

import gen.npu_abi as _abi  # noqa: E402

from cv.cv_command_ir import (
    UnsupportedCVOp,
    convert_layer_list,
    convert_mobilenetv3_graph,
)  # noqa: E402
from cv.onnx_importer import import_mobilenetv3  # noqa: E402
from signoff.device_server_fixture import managed_device_server  # noqa: E402

# ── Constants ──────────────────────────────────────────────────────────────
# Must match device_server.py DRAM_BUFFER_BASE for buffer address alignment.
DRAM_BUF_BASE: int = _abi.Addr.DRAM + 0x0010_0000  # 0x80100000

# Default MobileNetV3-Small input shape.
_DEFAULT_INPUT_SHAPE: list[int] = [1, 3, 224, 224]

# First Conv layer GEMM dimensions for MobileNetV3-Small:
#   Conv(in=3, out=16, k=3x3, stride=2, pad=1) on 224x224 input.
# Via im2col: M = 112×112 = 12544, K = 3×3×3 = 27, N = 16.
_FIRST_CONV_M: int = 12544
_FIRST_CONV_K: int = 27
_FIRST_CONV_N: int = 16

# Evidence output directory.
_EVIDENCE_DIR: Path = _repo_root / ".omo" / "evidence"


# ── Runtime environment ────────────────────────────────────────────────────


def _ensure_runtime_paths() -> None:
    """Add required paths for the Caduceus runtime Python bindings."""
    _runtime_lib = _repo_root / "build" / "software" / "libcaduceus_runtime.so"
    if _runtime_lib.is_file():
        os.environ["CADUCEUS_RUNTIME_LIB"] = str(_runtime_lib)

    _python_runtime = str(_sw_dir / "python")
    if _python_runtime not in sys.path:
        sys.path.insert(0, _python_runtime)

    _gen_dir = str(_repo_root / "gen")
    if _gen_dir not in sys.path:
        sys.path.insert(0, _gen_dir)


# ── Blob builder ───────────────────────────────────────────────────────────


def _build_first_conv_blob(
    dram_base: int = DRAM_BUF_BASE,
) -> tuple[bytes, int, int, int]:
    """Build a standalone MMUL command blob for the first Conv layer.

    Uses the same ``software/compiler/command_ir.CommandBlob`` API as B1's
    converter, but assigns unique sequential DRAM addresses for every buffer
    so the device server can execute the blob.

    Returns
    -------
    (blob_bytes, input_size, weight_size, output_size)
        Encoded blob bytes and the sizes of the three data buffers.
    """
    from software.compiler.command_ir import CommandBlob, LowerStatus  # noqa: F811
    from software.compiler.command_ir_types import CAD_CAP_MXU

    M, K, N = _FIRST_CONV_M, _FIRST_CONV_K, _FIRST_CONV_N
    # INT8 input:  M×K bytes; INT4 packed weight: K×N/2 bytes;
    # INT32 output: M×N×4 bytes; INT32 per-channel scale: N×4 bytes.
    input_size = M * K
    weight_size = max((K * N) // 2, 1)
    output_size = M * N * 4
    scale_size = N * 4

    blob = CommandBlob(caps=CAD_CAP_MXU)

    # Assign sequential unique DRAM addresses so device_server can execute.
    # Each buffer must be 64-byte aligned; sizes may not be aligned, so we
    # advance the cursor by the aligned-up size.
    def _align(v: int, a: int = 64) -> int:
        return (v + a - 1) & ~(a - 1)

    addr = dram_base
    input_id = blob.declare_buffer(input_size, 64, host_addr=addr)
    addr += _align(input_size)
    weight_id = blob.declare_buffer(weight_size, 64, host_addr=addr)
    addr += _align(weight_size)
    output_id = blob.declare_buffer(output_size, 64, host_addr=addr)
    addr += _align(output_size)
    scale_id = blob.declare_buffer(scale_size, 64, host_addr=addr)

    blob.add_mmul(input_id, weight_id, output_id, scale_id, M, K, N)
    blob.add_barrier()

    status = blob.lower()
    if status != LowerStatus.OK:
        raise RuntimeError(f"Blob lowering failed: {status!r}")

    return blob.encode(), input_size, weight_size, output_size


def _compute_buffer_layout(blob_bytes: bytes) -> dict[int, dict]:
    """Parse encoded blob to extract per-buffer (id, phys_addr, size)."""
    from software.compiler.command_ir import CommandBlob  # noqa: F811

    blob = CommandBlob.decode(blob_bytes)
    layout: dict[int, dict] = {}
    for buf in blob.buffers:
        layout[buf.id] = {
            "phys_addr": buf.phys_addr,
            "size": buf.size,
            "alignment": buf.alignment,
        }
    return layout


# ── Evidence ───────────────────────────────────────────────────────────────


def _write_evidence(evidence: dict, path: str | None = None) -> Path:
    """Write evidence JSON to ``.omo/evidence/cv-host-runner-<ts>.json``."""
    _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    if path is None:
        ts = time.strftime("%Y%m%dT%H%M%S")
        path = str(_EVIDENCE_DIR / f"cv-host-runner-{ts}.json")
    out = Path(path)
    out.write_text(json.dumps(evidence, indent=2))
    return out


# ── Core runner ────────────────────────────────────────────────────────────


def run_cv(
    onnx_path: str,
    device_url: str = "fm://python",
    evidence_path: str | None = None,
    input_shape: list[int] | None = None,
    full_graph: bool = False,
) -> int:
    """Run CV inference: convert ONNX → submit blob → read result → evidence.

    When *full_graph* is True, submits the complete MobileNetV3-Small graph
    blob (B4 path).  The device server remaps buffer addresses and executes
    each op directly via golden modules.  No model weights are written —
    the execution operates on zero-initialised DRAM, which is sufficient
    for path-verification (numerical correctness is B5 scope).

    Returns 0 on success, non-zero on failure.
    """
    start = time.perf_counter()

    if input_shape is None:
        input_shape = _DEFAULT_INPUT_SHAPE

    evidence: dict = {
        "model": onnx_path,
        "device": device_url,
        "input_shape": input_shape,
        "output_shape": [],
        "first_conv_passed": False,
        "full_graph_passed": False,
        "error": None,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    # ── Step 1: Validate B1 converter works on the full model ──────────
    print(f"[cv_host] Converting ONNX model (B1 validation): {onnx_path}")
    try:
        full_blob = convert_mobilenetv3_graph(onnx_path)
        print(f"[cv_host] B1 full-model conversion OK: {len(full_blob)} bytes")
    except (FileNotFoundError, UnsupportedCVOp, RuntimeError) as exc:
        evidence["error"] = f"B1 conversion failed: {exc}"
        evidence["elapsed_sec"] = time.perf_counter() - start
        _write_evidence(evidence, evidence_path)
        print(f"[cv_host] FAIL: {exc}", file=sys.stderr)
        return 1

    # ── B4 full-graph path ────────────────────────────────────────────
    if full_graph:
        evidence["elapsed_sec"] = time.perf_counter() - start
        return _run_full_graph(full_blob, onnx_path, device_url, evidence,
                               evidence_path)

    # ── Step 2: Build first-Conv-only blob with unique DRAM addresses ──
    print("[cv_host] Building first-Conv command blob…")
    blob_bytes, input_size, weight_size, output_size = _build_first_conv_blob()
    layout = _compute_buffer_layout(blob_bytes)
    print(f"[cv_host] First-Conv blob: {len(blob_bytes)} bytes, "
          f"input={input_size} weight={weight_size} output={output_size}")

    # ── Step 3: Set up runtime and submit ──────────────────────────────
    _ensure_runtime_paths()

    # Compute total DRAM window needed for all buffers.
    all_addrs = [b["phys_addr"] for b in layout.values()]
    all_ends = [b["phys_addr"] + b["size"] for b in layout.values()]
    dram_start = min(all_addrs)
    dram_end = max(all_ends)
    dram_window = dram_end - dram_start
    print(f"[cv_host] DRAM window: 0x{dram_start:x}–0x{dram_end:x} "
          f"({dram_window} bytes)")

    print(f"[cv_host] Starting device server for {device_url}…")
    try:
        with managed_device_server(device_url) as resolved_uri:
            print(f"[cv_host] Device server ready at {resolved_uri}")

            # Lazy import runtime bindings after path setup.
            from caduceus_runtime import (  # noqa: E402
                CAD_ERROR_INVALID_ARGUMENT,
                Buffer,
                CommandList,
                Device,
                Fence,
                Queue,
                append_execute_blob,
            )

            dev = Device(resolved_uri)
            caps = dev.caps
            print(f"[cv_host] Device: {caps.device_name} "
                  f"(transport: {caps.transport_name})")

            # The Python runtime wrappers expect the raw CadDevice handle
            # (c_void_p), not the Device wrapper object.
            dh = dev.handle

            try:
                # Allocate one big data buffer first so it lands at
                # DRAM_BUF_BASE (= dram_start), matching blob addresses.
                data_buf = Buffer(dh, dram_window)
                cmd_buf = Buffer(dh, len(blob_bytes))

                # Write input data (random INT8 bytes) and weight data
                # (random INT4 nibbles) into the data buffer at the
                # correct offsets so the MMUL produces non-zero output.
                # Scale buffer must be filled with float32 1.0 so the
                # per-channel scale doesn't zero out the result.
                input_addr = layout[1]["phys_addr"]
                weight_addr = layout[2]["phys_addr"]
                scale_addr = layout[4]["phys_addr"]
                scale_size = layout[4]["size"]

                input_data = os.urandom(input_size)
                weight_data = os.urandom(weight_size)

                data_buf.write(input_addr - dram_start, input_data)
                data_buf.write(weight_addr - dram_start, weight_data)

                # Fill scale buffer with float32 1.0 (0x3F800000 LE).
                scale_data = struct.pack(f"<{scale_size // 4}I",
                                         *([0x3F800000] * (scale_size // 4)))
                data_buf.write(scale_addr - dram_start, scale_data)

                # Write encoded blob into the command buffer.
                cmd_buf.write(0, blob_bytes)

                # Create command list with ExecuteBlob.
                cl = CommandList(dh, max_entries=4)
                append_execute_blob(cl, cmd_buf, 0, len(blob_bytes))

                # Submit and wait.
                queue = Queue(dh)
                fence = Fence(dh)
                print("[cv_host] Submitting first-Conv blob…")
                queue.submit(cl, fence)
                fence.wait(timeout_ns=10_000_000_000)  # 10 s

                status = fence.status()
                print(f"[cv_host] Fence status: {status} "
                      f"({'COMPLETED' if status == 1 else 'ERROR' if status == 2 else 'UNKNOWN'})")
                if status != 1:
                    evidence["error"] = (
                        f"Fence status {status} (expected 1=COMPLETED)"
                    )
                    evidence["elapsed_sec"] = time.perf_counter() - start
                    _write_evidence(evidence, evidence_path)
                    print(f"[cv_host] FAIL: fence status={status}", file=sys.stderr)
                    return 1

                # Read output buffer.
                output_addr = layout[3]["phys_addr"]
                output_data = data_buf.read(
                    output_addr - dram_start, output_size
                )

                non_zero = any(b != 0 for b in output_data)
                if non_zero:
                    # Decode first few float32 values for logging.
                    floats = struct.unpack_from(
                        f"<{min(4, output_size // 4)}f", output_data, 0
                    )
                    print(f"[cv_host] Output (first 4 f32): {floats}")

                evidence["first_conv_passed"] = non_zero
                evidence["output_shape"] = [
                    1, _FIRST_CONV_N,
                    _FIRST_CONV_M // (_FIRST_CONV_K * 1), _FIRST_CONV_K * 1,
                ]
                if not non_zero:
                    evidence["error"] = "Output buffer is all zeros"

                fence.destroy()
                queue.destroy()
                cmd_buf.free()
                data_buf.free()

            except RuntimeError as exc:
                err_msg = str(exc)
                evidence["error"] = err_msg
                evidence["elapsed_sec"] = time.perf_counter() - start
                _write_evidence(evidence, evidence_path)
                print(f"[cv_host] FAIL: {err_msg}", file=sys.stderr)
                return 1

            dev.close()

    except RuntimeError as exc:
        evidence["error"] = f"Device server error: {exc}"
        evidence["elapsed_sec"] = time.perf_counter() - start
        _write_evidence(evidence, evidence_path)
        print(f"[cv_host] FAIL: {exc}", file=sys.stderr)
        return 1

    evidence["elapsed_sec"] = time.perf_counter() - start
    out_path = _write_evidence(evidence, evidence_path)
    verdict = "PASS" if evidence["first_conv_passed"] else "FAIL"
    print(f"[cv_host] {verdict} — evidence at {out_path}")
    return 0 if evidence["first_conv_passed"] else 1


def _run_full_graph(
    full_blob: bytes,
    onnx_path: str,
    device_url: str,
    evidence: dict,
    evidence_path: str | None,
) -> int:
    """Submit the full MobileNetV3-Small graph blob and check fence.

    The device server detects this as a CV blob (no DMA ops), remaps
    buffer addresses, and executes each command directly via golden
    modules.  No input/weight data is written — the DRAM contains
    zeros, producing zero outputs (acceptable for path verification).
    """
    start = time.perf_counter()
    print(f"[cv_host] Full-graph mode: submitting {len(full_blob)}-byte blob")
    _ensure_runtime_paths()

    print(f"[cv_host] Starting device server for {device_url}…")
    try:
        with managed_device_server(device_url) as resolved_uri:
            print(f"[cv_host] Device server ready at {resolved_uri}")

            from caduceus_runtime import (  # noqa: E402
                Buffer, CommandList, Device, Fence, Queue,
                append_execute_blob,
            )

            dev = Device(resolved_uri)
            dh = dev.handle
            try:
                cmd_buf = Buffer(dh, len(full_blob))
                cmd_buf.write(0, full_blob)

                cl = CommandList(dh, max_entries=4)
                append_execute_blob(cl, cmd_buf, 0, len(full_blob))

                queue = Queue(dh)
                fence = Fence(dh)
                print("[cv_host] Submitting full MobileNetV3-Small graph…")
                queue.submit(cl, fence)
                fence.wait(timeout_ns=60_000_000_000)  # 60 s for full graph

                status = fence.status()
                print(f"[cv_host] Full graph fence status: {status} "
                      f"({'COMPLETED' if status == 1 else 'ERROR'})")

                evidence["full_graph_passed"] = (status == 1)
                if status != 1:
                    evidence["error"] = (
                        f"Full graph fence status {status} (expected 1)"
                    )
                    evidence["elapsed_sec"] = time.perf_counter() - start
                    fence.destroy(); queue.destroy(); cmd_buf.free()
                    _write_evidence(evidence, evidence_path)
                    return 1

                fence.destroy(); queue.destroy(); cmd_buf.free()

            except RuntimeError as exc:
                evidence["error"] = str(exc)
                evidence["elapsed_sec"] = time.perf_counter() - start
                _write_evidence(evidence, evidence_path)
                print(f"[cv_host] FAIL: {exc}", file=sys.stderr)
                return 1

            dev.close()

    except RuntimeError as exc:
        evidence["error"] = f"Device server error: {exc}"
        evidence["elapsed_sec"] = time.perf_counter() - start
        _write_evidence(evidence, evidence_path)
        print(f"[cv_host] FAIL: {exc}", file=sys.stderr)
        return 1

    evidence["elapsed_sec"] = time.perf_counter() - start
    out_path = _write_evidence(evidence, evidence_path)
    print(f"[cv_host] Full graph PASS — evidence at {out_path}")
    return 0


# ── E2E full-graph runner ──────────────────────────────────────────────────


def run_cv_e2e_full(
    onnx_path: str,
    device_url: str = "fm://python",
) -> tuple[list[int], list[float], int]:
    """Run the full MobileNetV3-Small graph E2E and return top-5 predictions.

    Converts the ONNX model to a float32 command blob with unique DRAM
    addresses, loads the ONNX weights, submits the blob through the Host
    Runtime, reads the output logits, and returns top-5 (indices, logits)
    plus the number of NPU-dispatched ops (non-barrier commands).

    Returns ``(top5_indices, top5_logits, npu_ops_executed)``.
    """
    from cv.cv_command_ir import convert_mobilenetv3_graph_full
    from software.compiler.command_ir import CommandBlob

    import numpy as np

    # ── Convert graph ──────────────────────────────────────────────────
    blob_bytes, buf_map, weight_map, bias_map, scale_map = (
        convert_mobilenetv3_graph_full(onnx_path)
    )

    decoded_blob = CommandBlob.decode(blob_bytes)
    npu_ops_executed = sum(
        1 for cmd in decoded_blob.commands if cmd.kind != "barrier"
    )

    # ── Prepare input tensor (seed=42, same as golden) ────────────────
    rng = np.random.RandomState(42)
    input_tensor = rng.randn(1, 3, 224, 224).astype(np.float32)

    # ── Compute DRAM window ────────────────────────────────────────────
    all_addrs = [b["phys_addr"] for b in buf_map.values()]
    all_ends = [
        b["phys_addr"] + b["size"] for b in buf_map.values()
    ]
    dram_start = min(all_addrs)
    dram_end = max(all_ends)
    dram_window = dram_end - dram_start

    # ── Identify output buffer (last declared buffer with size=1000*4) ──
    output_buf_id = max(buf_map.keys())
    output_info = buf_map[output_buf_id]

    # ── Start device server ────────────────────────────────────────────
    _ensure_runtime_paths()

    from signoff.device_server_fixture import managed_device_server

    with managed_device_server(device_url) as resolved_uri:
        from caduceus_runtime import (
            Buffer, CommandList, Device, Fence, Queue,
            append_execute_blob,
        )

        dev = Device(resolved_uri)
        dh = dev.handle

        try:
            data_buf = Buffer(dh, dram_window)
            cmd_buf = Buffer(dh, len(blob_bytes))

            # Write input tensor to buffer 1 (first declared buffer).
            input_addr = buf_map[1]["phys_addr"]
            data_buf.write(
                input_addr - dram_start,
                input_tensor.astype(np.float32).tobytes(),
            )

            for buf_id, wt_array, _dims, _meta in weight_map:
                if wt_array is None:
                    continue
                buf_info = buf_map[buf_id]
                if isinstance(wt_array, (bytes, bytearray)):
                    raw = bytes(wt_array)
                else:
                    raw = np.asarray(wt_array, dtype=np.float32).tobytes()
                if len(raw) < buf_info["size"]:
                    raw = raw.ljust(buf_info["size"], b"\x00")
                data_buf.write(
                    buf_info["phys_addr"] - dram_start,
                    raw[:buf_info["size"]],
                )

            # Write bias tensors.
            for buf_id, bias_array in bias_map.items():
                buf_info = buf_map[buf_id]
                raw = np.asarray(bias_array, dtype=np.float32).tobytes()
                if len(raw) < buf_info["size"]:
                    raw = raw.ljust(buf_info["size"], b"\x00")
                data_buf.write(
                    buf_info["phys_addr"] - dram_start,
                    raw[:buf_info["size"]],
                )

            # Write scale tensors (e.g. GAP averaging factors).
            for buf_id, scale_array in scale_map.items():
                buf_info = buf_map[buf_id]
                raw = np.asarray(scale_array, dtype=np.float32).tobytes()
                if len(raw) < buf_info["size"]:
                    raw = raw.ljust(buf_info["size"], b"\x00")
                data_buf.write(
                    buf_info["phys_addr"] - dram_start,
                    raw[:buf_info["size"]],
                )

            # Write command blob.
            cmd_buf.write(0, blob_bytes)

            # Submit.
            cl = CommandList(dh, max_entries=4)
            append_execute_blob(cl, cmd_buf, 0, len(blob_bytes))

            queue = Queue(dh)
            fence = Fence(dh)
            queue.submit(cl, fence)
            fence.wait(timeout_ns=120_000_000_000)  # 120 s

            status = fence.status()
            if status != 1:
                raise RuntimeError(
                    f"Full graph fence status {status} (expected 1=COMPLETED)"
                )

            # Read output.
            output_raw = data_buf.read(
                output_info["phys_addr"] - dram_start,
                output_info["size"],
            )
            logits = np.frombuffer(output_raw, dtype=np.float32)

            # Top-5.
            top5_indices = np.argsort(logits)[-5:][::-1].astype(int).tolist()
            top5_logits = logits[top5_indices].tolist()

            fence.destroy()
            queue.destroy()
            cmd_buf.free()
            data_buf.free()

        finally:
            dev.close()

    return top5_indices, top5_logits, npu_ops_executed


# ── CLI ────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CaduceusCore CV inference host runner"
    )
    parser.add_argument(
        "--model",
        default="assets/mobilenetv3_small.onnx",
        help="Path to MobileNetV3-Small ONNX model",
    )
    parser.add_argument(
        "--device",
        default="fm://python",
        help="Caduceus device URI (default: fm://python)",
    )
    parser.add_argument(
        "--evidence",
        default=None,
        help="Path for evidence JSON output",
    )
    parser.add_argument(
        "--input-shape",
        nargs="+",
        type=int,
        default=None,
        help="Input tensor shape (default: 1 3 224 224)",
    )
    parser.add_argument(
        "--full-graph",
        action="store_true",
        help="Submit the full MobileNetV3-Small graph blob (B4 path)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return run_cv(
        onnx_path=args.model,
        device_url=args.device,
        evidence_path=args.evidence,
        input_shape=args.input_shape,
        full_graph=args.full_graph,
    )


if __name__ == "__main__":
    sys.exit(main())
