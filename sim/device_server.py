#!/usr/bin/env python3
"""CaduceusCore Func Model Device Server.

Implements the binary device protocol over a Unix domain socket.  The server
routes every operation through FuncModel's PCIe/BAR, doorbell, and completion
behaviour: host memory is modelled as device DRAM accessible via PCIe TLPs,
and compute is dispatched by the firmware run-loop, not by the server itself.
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import socket
import socketserver
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Allow the script to be run directly as `python3 sim/device_server.py`
_SCRIPT_DIR = Path(__file__).resolve().parent
_PACKAGE_ROOT = _SCRIPT_DIR.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from caduceus_device_protocol.BufferAllocRequest import BufferAllocRequestT
from caduceus_device_protocol.BufferAllocResponse import BufferAllocResponseT
from caduceus_device_protocol.BufferFreeRequest import BufferFreeRequestT
from caduceus_device_protocol.BufferFreeResponse import BufferFreeResponseT
from caduceus_device_protocol.BufferReadRequest import BufferReadRequestT
from caduceus_device_protocol.BufferReadResponse import BufferReadResponseT
from caduceus_device_protocol.BufferSizeRequest import BufferSizeRequestT
from caduceus_device_protocol.BufferSizeResponse import BufferSizeResponseT
from caduceus_device_protocol.BufferWriteRequest import BufferWriteRequestT
from caduceus_device_protocol.BufferWriteResponse import BufferWriteResponseT
from caduceus_device_protocol.DeviceCapsRequest import DeviceCapsRequestT
from caduceus_device_protocol.DeviceCapsResponse import DeviceCapsResponseT
from caduceus_device_protocol.DeviceMessage import DeviceMessageT
from caduceus_device_protocol.DeviceOpcode import DeviceOpcode
from caduceus_device_protocol.DeviceResetRequest import DeviceResetRequestT
from caduceus_device_protocol.DeviceResetResponse import DeviceResetResponseT
from caduceus_device_protocol.DeviceStatus import DeviceStatus
from caduceus_device_protocol.ErrorResponse import ErrorResponseT
from caduceus_device_protocol.FenceCreateRequest import FenceCreateRequestT
from caduceus_device_protocol.FenceCreateResponse import FenceCreateResponseT
from caduceus_device_protocol.FenceDestroyRequest import FenceDestroyRequestT
from caduceus_device_protocol.FenceDestroyResponse import FenceDestroyResponseT
from caduceus_device_protocol.FencePollRequest import FencePollRequestT
from caduceus_device_protocol.FencePollResponse import FencePollResponseT
from caduceus_device_protocol.FenceStatusRequest import FenceStatusRequestT
from caduceus_device_protocol.FenceStatusResponse import FenceStatusResponseT
from caduceus_device_protocol.FenceWaitRequest import FenceWaitRequestT
from caduceus_device_protocol.FenceWaitResponse import FenceWaitResponseT
from caduceus_device_protocol.MessageHeader import MessageHeaderT
from caduceus_device_protocol.SubmitRequest import SubmitRequestT
from caduceus_device_protocol.SubmitResponse import SubmitResponseT

from gen.npu_abi import Addr, DOORBELL
from sim.device_protocol import (
    MAGIC,
    PROTOCOL_VERSION,
    build_message,
    cad_error_to_device_status,
    pack_table,
    parse_message,
    recv_framed,
    send_framed,
    unpack_table,
    validate_header,
)
from sim.func_model import FuncModel


LOGGER = logging.getLogger("caduceus.device")

DEFAULT_SOCK_PATH = "/tmp/caduceus_fm.sock"

# DRAM region used for host-visible buffers (BAR1, 0x80000000+).
# Keep the first 1 MiB free for the firmware ring/completion area, then use a
# large window for host buffers.  Descriptors live above the buffer window to
# avoid address collisions with allocated buffers.
DRAM_BUFFER_BASE = Addr.DRAM + 0x0010_0000  # 1 MiB above base
DRAM_BUFFER_END = Addr.DRAM + 0x0300_0000   # 48 MiB buffer window

# Firmware ring buffer layout (matches NPUFirmware defaults).
RING_BUFFER_ADDR = 0x8000_0000
RING_SLOT_SIZE = 32   # bytes per slot in DRAM ring buffer
RING_ENTRY_SIZE = 24  # bytes of payload carried in the protocol cmd_blob
RING_SIZE = 16

# Blob flattening constants.
CAD_BLOB_MAGIC = 0x43414442   # "CADB" little-endian
CAD_DESC_WORDS = 15
CAD_DESC_BYTES = CAD_DESC_WORDS * 4   # 60 bytes per descriptor
CAD_CMD_ENTRY_BYTES = 32              # 8 × uint32 ring entry in blob
DESC_ADDR_BASE = Addr.DRAM + 0x0300_0000  # above buffer window, avoids collision

# CV blob execution uses a dedicated DRAM region above the host-allocator
# window to avoid address collisions with firmware-path buffers.
CV_BUF_BASE = Addr.DRAM + 0x0100_0000  # 16 MB offset → 0x81000000


def _align(v: int, a: int = 64) -> int:
    """Align *v* up to the next multiple of *a* (power-of-two)."""
    return (v + a - 1) & ~(a - 1)


def _payload_bytes(msg: DeviceMessageT) -> bytes:
    """Return the message payload as bytes."""
    if msg.payload is None:
        return b""
    return bytes(msg.payload)


def _set_payload(msg: DeviceMessageT, data: bytes) -> None:
    """Set the message payload from bytes."""
    msg.payload = list(data)


# ── DRAM buffer allocator ──────────────────────────────────────────────────


@dataclass
class _BufferRegion:
    addr: int
    size: int
    free: bool = True


class _BufferAllocator:
    """Simple first-fit allocator inside the DRAM buffer window."""

    def __init__(self, base: int, end: int):
        self._lock = threading.Lock()
        self._regions = [_BufferRegion(base, end - base, free=True)]
        self._sizes: dict[int, int] = {}  # addr → allocated size

    def alloc(self, size: int) -> Optional[int]:
        with self._lock:
            for region in self._regions:
                if region.free and region.size >= size:
                    addr = region.addr
                    if region.size == size:
                        region.free = False
                    else:
                        region.free = False
                        remaining = _BufferRegion(
                            addr + size, region.size - size, free=True
                        )
                        region.size = size
                        self._regions.append(remaining)
                        self._regions.sort(key=lambda r: r.addr)
                    self._sizes[addr] = size
                    return addr
            return None

    def free(self, addr: int) -> bool:
        with self._lock:
            for region in self._regions:
                if region.addr == addr and not region.free:
                    region.free = True
                    self._sizes.pop(addr, None)
                    self._coalesce()
                    return True
            return False

    def size_of(self, addr: int) -> int:
        with self._lock:
            return self._sizes.get(addr, 0)

    def _coalesce(self) -> None:
        self._regions.sort(key=lambda r: r.addr)
        merged: list[_BufferRegion] = []
        for r in self._regions:
            if merged and merged[-1].free and r.free and (
                merged[-1].addr + merged[-1].size == r.addr
            ):
                merged[-1].size += r.size
            else:
                merged.append(r)
        self._regions = merged

    def contains(self, addr: int) -> bool:
        with self._lock:
            return any(
                r.addr <= addr < r.addr + r.size and not r.free
                for r in self._regions
            )


# ── Fence table ────────────────────────────────────────────────────────────


@dataclass
class _Fence:
    handle: int
    event: threading.Event = field(default_factory=threading.Event)
    status: int = 0  # 0=not ready, 1=completed, 2=error
    signalled: bool = False


class _FenceTable:
    def __init__(self):
        self._lock = threading.Lock()
        self._fences: dict[int, _Fence] = {}
        self._next_handle = 1

    def create(self) -> int:
        with self._lock:
            handle = self._next_handle
            self._next_handle += 1
            self._fences[handle] = _Fence(handle=handle)
            return handle

    def get(self, handle: int) -> Optional[_Fence]:
        with self._lock:
            return self._fences.get(handle)

    def destroy(self, handle: int) -> bool:
        with self._lock:
            return self._fences.pop(handle, None) is not None

    def signal(self, handle: int, status: int = 1) -> bool:
        with self._lock:
            fence = self._fences.get(handle)
            if fence is None:
                return False
            fence.status = status
            fence.signalled = True
            fence.event.set()
            return True

    def error_all(self) -> None:
        with self._lock:
            for fence in self._fences.values():
                fence.status = 2
                fence.signalled = True
                fence.event.set()

    def clear(self) -> None:
        with self._lock:
            self._fences.clear()
            self._next_handle = 1


# ── Command queue for asynchronous execution ───────────────────────────────


@dataclass
class _PendingCommand:
    fence_handle: int
    cmd_count: int
    cmd_blob: bytes


# ── Server core ────────────────────────────────────────────────────────────


class FmDeviceServer:
    """Func Model device server: sockets + protocol + FuncModel dispatch."""

    def __init__(self, sock_path: str = DEFAULT_SOCK_PATH, use_spike: bool = False):
        self.sock_path = sock_path
        self.use_spike = use_spike
        self._model: Optional[FuncModel] = None
        self._model_lock = threading.Lock()
        self._buffers = _BufferAllocator(DRAM_BUFFER_BASE, DRAM_BUFFER_END)
        self._fences = _FenceTable()
        self._request_lock = threading.Lock()
        self._next_conn_id = 0
        self._per_conn_last_id: dict[int, int] = {}
        self._cmd_queue: queue.Queue[_PendingCommand] = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._shutdown = threading.Event()
        self._caps = DeviceCapsResponseT()
        self._caps.abiMajor = 1
        self._caps.abiMinor = 0
        self._caps.maxBuffers = 4096
        self._caps.maxBufferSize = 1024 * 1024 * 1024
        self._caps.maxQueues = 8
        self._caps.maxCommandLists = 256
        self._caps.maxCommandListEntries = 65536
        self._caps.deviceName = "CaduceusCore NPU"
        self._caps.transportName = "FuncModel"

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Create FuncModel and start the worker thread."""
        if self.use_spike:
            self._model = FuncModel(use_spike=True, sram_kb=4096)
        else:
            self._model = FuncModel(dram_mb=256)
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def stop(self) -> None:
        """Signal shutdown and wake the worker."""
        self._shutdown.set()
        self._cmd_queue.put(_PendingCommand(0, 0, b""))
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=2.0)

    # ── Worker loop: executes commands on FuncModel ───────────────────────

    def _worker_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                pending = self._cmd_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if self._shutdown.is_set() and pending.fence_handle == 0:
                break
            try:
                self._execute_on_model(pending.cmd_count, pending.cmd_blob)
                self._fences.signal(pending.fence_handle, status=1)
            except Exception as exc:
                self._fences.signal(pending.fence_handle, status=2)
                # Surface the failure through the next fence_status poll.
                if self._model is not None:
                    with self._model_lock:
                        try:
                            self._model.bridge.handle(
                                "write",
                                Addr.DOORBELL + DOORBELL.LAST_STATUS,
                                1,
                            )
                        except Exception:
                            pass

    def _execute_on_model(self, cmd_count: int, cmd_blob: bytes) -> None:
        """Write descriptors + ring entries and run the firmware loop.

        Auto-detects between three formats:
        1. W2-T7 CV blob: no DMA/copy opcodes → direct CV execution path.
        2. W2-T7 Qwen blob: contains DMA/copy opcodes → firmware run_loop.
        3. Legacy flat format: ring_entries (24B each) || descriptors
           (60B each).
        """
        if self._model is None:
            raise RuntimeError("model not initialized")

        # Detect W2-T7 headered format: first blob starts with CADB magic
        # at offset 12 (after 3×uint32 header).
        if (len(cmd_blob) >= 16
                and struct.unpack_from("<I", cmd_blob, 12)[0] == CAD_BLOB_MAGIC):
            ring_data, desc_data, actual_count = self._flatten_blobs(cmd_blob)
            # Route CV blobs (no DMA/copy ops AND descriptor addresses
            # are the B1 collision value 0x80000000) to direct golden
            # execution.  Qwen/first-Conv blobs with unique addresses
            # go through the firmware path.
            if self._is_cv_blob(ring_data, desc_data, actual_count):
                self._execute_cv_blob(cmd_blob)
                return
            self._execute_flat(ring_data, desc_data, actual_count)
            return

        # Legacy flat format.
        ring_bytes = cmd_count * RING_ENTRY_SIZE
        desc_bytes = cmd_count * 60
        if len(cmd_blob) < ring_bytes + desc_bytes:
            raise ValueError("cmd_blob too short")
        self._execute_flat(
            cmd_blob[:ring_bytes],
            cmd_blob[ring_bytes: ring_bytes + desc_bytes],
            cmd_count,
        )

    # ── CV blob helpers ──────────────────────────────────────────────────

    @staticmethod
    def _is_cv_blob(ring_data: bytes, desc_data: bytes, cmd_count: int) -> bool:
        """Return True if this blob is a CV blob (B1/B5 converter output).

        Detection uses a single signal: no DMA/copy opcodes in the ring
        entries.  Qwen blobs always have DMA_COPY, DMA_ST, or PCIE_DMA for
        DRAM↔SRAM movement; CV blobs never have DMA ops because the golden
        execution reads/writes DRAM directly.
        """
        _DMA_OPS = frozenset({0x07, 0x09, 0x0A, 0x15, 0x16})
        for i in range(cmd_count):
            offset = i * RING_ENTRY_SIZE
            if offset + 4 > len(ring_data):
                break
            opcode = struct.unpack_from("<I", ring_data, offset)[0]
            if opcode in _DMA_OPS:
                return False

        return True

    def _execute_cv_blob(self, cmd_blob: bytes) -> None:
        """Execute a CV command blob directly via golden modules.

        Two execution modes are supported:

        * **INT mode** (B1 blobs with collision ``host_addr=0x80000000``):
          buffers are remapped to unique addresses starting at
          ``CV_BUF_BASE`` and executed via INT8/INT4→INT32 golden modules
          (original B4 behaviour).

        * **F32 mode** (B5 blobs with unique sequential addresses from
          ``0x80100000``): buffers keep their declared addresses and
          execution uses pure float32 golden modules so output logits can
          be compared against ONNX Runtime.
        """
        import numpy as np

        from software.compiler.command_ir import CommandBlob  # noqa: F811

        nop_count, blob_count, total_cmd_count = struct.unpack_from(
            "<III", cmd_blob, 0
        )
        blob_offset = 12
        next_addr = CV_BUF_BASE

        for _blob_idx in range(blob_count):
            if blob_offset + 64 > len(cmd_blob):
                raise ValueError("truncated CV blob header")
            blk = cmd_blob[blob_offset:]

            magic = struct.unpack_from("<I", blk, 0)[0]
            if magic != CAD_BLOB_MAGIC:
                raise ValueError(f"bad CV blob magic: {magic:#x}")

            bt_size = struct.unpack_from("<I", blk, 40)[0]
            bt_off = struct.unpack_from("<I", blk, 44)[0]
            cmd_size = struct.unpack_from("<I", blk, 32)[0]
            cmd_off = struct.unpack_from("<I", blk, 36)[0]
            desc_size = struct.unpack_from("<I", blk, 24)[0]
            desc_off = struct.unpack_from("<I", blk, 28)[0]

            blob_end = blob_offset + max(
                64, bt_off + bt_size, cmd_off + cmd_size,
                desc_off + desc_size,
            )
            if blob_end > len(cmd_blob):
                raise ValueError("CV blob directory out of bounds")

            blob = CommandBlob.decode(bytes(blk[:blob_end - blob_offset]))

            use_f32 = (
                len(blob.buffers) > 0
                and blob.buffers[0].phys_addr != Addr.DRAM
            )

            if not use_f32:
                for buf in blob.buffers:
                    new_addr = _align(next_addr, buf.alignment)
                    buf.phys_addr = new_addr
                    next_addr = new_addr + _align(buf.size, buf.alignment)

            self._exec_cv_commands(blob, use_f32=use_f32)

            blob_offset = blob_end

    def _exec_cv_commands(self, blob, use_f32: bool = False) -> None:
        """Execute every non-barrier command in *blob* via golden modules."""
        with self._model_lock:
            model = self._model

            for cmd in blob.commands:
                if cmd.kind == "barrier":
                    continue

                if cmd.kind == "mmul":
                    if use_f32:
                        self._exec_cv_mmul_f32(model, blob, cmd)
                    else:
                        self._exec_cv_mmul(model, blob, cmd)
                elif cmd.kind == "sfu":
                    if use_f32:
                        self._exec_cv_sfu_f32(model, blob, cmd)
                    else:
                        self._exec_cv_sfu(model, blob, cmd)
                elif cmd.kind == "vector":
                    if use_f32:
                        self._exec_cv_vector_f32(model, blob, cmd)
                    else:
                        self._exec_cv_vector(model, blob, cmd)
                elif cmd.kind == "dma_copy":
                    pass
                else:
                    raise RuntimeError(
                        f"unsupported CV opcode kind={cmd.kind} op={cmd.opcode:#x}"
                    )

    # ── Per-engine executors ────────────────────────────────────────────

    def _exec_cv_mmul(self, model, blob, cmd) -> None:  # noqa: ANN001
        """Execute a CV MMUL: read INT8 act + INT4 wt from DRAM, call
        GoldenMXU.matmul_int32, write INT32 output to DRAM."""
        import numpy as np

        input_id, weight_id, output_id, scale_id = cmd.buffers
        M, K, N = cmd.mmul  # type: ignore[misc]

        input_buf = blob.buffers[input_id - 1]
        weight_buf = blob.buffers[weight_id - 1]
        output_buf = blob.buffers[output_id - 1]

        act_bytes = model.pcie.tlp_read(input_buf.phys_addr, M * K)
        act = np.frombuffer(act_bytes, dtype=np.int8).reshape(M, K)

        wt_size = max((K * N) // 2, 1)
        wt_bytes = model.pcie.tlp_read(weight_buf.phys_addr, wt_size)
        wt = np.frombuffer(wt_bytes, dtype=np.uint8)

        result = model.mxu.matmul_int32(act, wt, M, K, N)
        model.pcie.tlp_write(output_buf.phys_addr, result.tobytes())

    def _exec_cv_sfu(self, model, blob, cmd) -> None:  # noqa: ANN001
        """Execute a CV SFU op: read FP16 from DRAM, call appropriate
        GoldenSFU method, write FP16 output to DRAM."""
        import numpy as np

        sfu_op, elements, head_dim, pos = cmd.sfu  # type: ignore[misc]
        input_id, output_id = cmd.buffers[:2]

        input_buf = blob.buffers[input_id - 1]
        output_buf = blob.buffers[output_id - 1]

        fp_bytes = model.pcie.tlp_read(input_buf.phys_addr, elements * 2)
        x = np.frombuffer(fp_bytes, dtype=np.float16).astype(np.float32)

        result: np.ndarray
        if sfu_op == 3:  # ReLU
            result = model.sfu.relu_hw(x)
        elif sfu_op == 2:  # GELU
            result = model.sfu.gelu_hw(x)
        elif sfu_op == 4:  # SiLU
            result = model.sfu.silu_hw(x)
        else:
            raise RuntimeError(f"unsupported CV SFU op={sfu_op}")

        model.pcie.tlp_write(
            output_buf.phys_addr, result.astype(np.float16).tobytes()
        )

    def _exec_cv_vector(self, model, blob, cmd) -> None:  # noqa: ANN001
        """Execute a CV Vector op: read INT32 from DRAM, call appropriate
        GoldenVector method, write INT32 output to DRAM."""
        import numpy as np

        vec_op, elements = cmd.vector  # type: ignore[misc]
        a_id, b_id, output_id = cmd.buffers[:3]

        a_buf = blob.buffers[a_id - 1]
        b_buf = blob.buffers[b_id - 1] if b_id else None
        output_buf = blob.buffers[output_id - 1]

        a_bytes = model.pcie.tlp_read(a_buf.phys_addr, elements * 4)
        a = np.frombuffer(a_bytes, dtype=np.int32)

        if b_buf:
            b_bytes = model.pcie.tlp_read(b_buf.phys_addr, elements * 4)
            b = np.frombuffer(b_bytes, dtype=np.int32)
        else:
            b = np.zeros(elements, dtype=np.int32)

        result: np.ndarray
        if vec_op == 0:  # VADD
            result = model.vector.add(a, b)
        elif vec_op == 1:  # VMUL
            result = model.vector.mul(a, b)
        elif vec_op == 2:  # VRED_MAX
            val = model.vector.max_reduce(a)
            result = np.array([int(val)], dtype=np.int32)
        elif vec_op == 3:  # VRED_SUM
            val = model.vector.sum_reduce(a)
            result = np.array([int(val)], dtype=np.int32)
        elif vec_op == 4:  # VCONV (INT32→FP16)
            result_f16 = model.vector.conv_i32_to_f16(a)
            result = result_f16.view(np.int16).astype(np.int32)
        elif vec_op == 5:  # VRESID
            result = model.vector.residual_add(a, b)
        else:
            raise RuntimeError(f"unsupported CV vector op={vec_op}")

        out_bytes = np.asarray(result, dtype=np.int32).tobytes()
        if len(out_bytes) < output_buf.size:
            out_bytes = out_bytes.ljust(output_buf.size, b"\x00")
        model.pcie.tlp_write(output_buf.phys_addr, out_bytes[:output_buf.size])

    # ── Float32 CV executors ────────────────────────────────────────────

    _CONV_META_MAGIC = 0xCADB0001
    _CONV_META_SIZE = 20 * 4

    @staticmethod
    def _im2col_nchw(x, kh, kw, sh, sw, pt, pl, pb, pr, out_h, out_w):
        """im2col for NCHW input, returning (N*out_h*out_w, C*kh*kw)."""
        import numpy as np

        N, C, H, W = x.shape
        x_pad = np.pad(
            x, ((0, 0), (0, 0), (pt, pb), (pl, pr)), mode="constant"
        )
        shape = (N, C, kh, kw, out_h, out_w)
        strides = (
            x_pad.strides[0],
            x_pad.strides[1],
            x_pad.strides[2],
            x_pad.strides[3],
            x_pad.strides[2] * sh,
            x_pad.strides[3] * sw,
        )
        cols = np.lib.stride_tricks.as_strided(
            x_pad, shape=shape, strides=strides
        )
        return cols.transpose(0, 4, 5, 1, 2, 3).reshape(
            N * out_h * out_w, C * kh * kw
        )

    def _exec_cv_conv_f32(
        self, model, input_buf, weight_buf, output_buf, wt_bytes, meta, M, K, N
    ) -> None:
        """Execute a standard or depthwise convolution from embedded metadata."""
        import numpy as np

        (
            _magic, op_kind, kh, kw, sh, sw, pt, pl, pb, pr,
            in_h, in_w, out_h, out_w, cin, cout, _groups, layout_code, _, _,
        ) = meta

        weight_data = np.frombuffer(wt_bytes[:-self._CONV_META_SIZE], dtype=np.float32)
        wt_count = cout * (cin // max(_groups, 1)) * kh * kw
        if weight_data.size < wt_count:
            weight_data = np.pad(
                weight_data, (0, wt_count - weight_data.size), constant_values=0.0
            )
        weight = weight_data[:wt_count].reshape(
            cout, cin // max(_groups, 1), kh, kw
        )

        act_bytes = model.pcie.tlp_read(input_buf.phys_addr, input_buf.size)
        act = np.frombuffer(act_bytes, dtype=np.float32)
        in_elems = 1 * in_h * in_w * cin
        if act.size < in_elems:
            act = np.pad(act, (0, in_elems - act.size), constant_values=0.0)
        act = act[:in_elems]
        if layout_code == 1:
            act = act.reshape(1, in_h, in_w, cin).transpose(0, 3, 1, 2)
        else:
            act = act.reshape(1, cin, in_h, in_w)

        cols = self._im2col_nchw(
            act, kh, kw, sh, sw, pt, pl, pb, pr, out_h, out_w
        )

        if op_kind == 2:  # depthwise
            cols = cols.reshape(out_h * out_w, cin, kh * kw)
            weight_2d = weight.reshape(cin, kh * kw)
            out = np.einsum("ncp,cp->nc", cols, weight_2d)
            out = out.reshape(1, out_h, out_w, cin)
        else:  # standard conv
            weight_2d = weight.reshape(cout, cin * kh * kw).T
            out = cols @ weight_2d
            out = out.reshape(1, out_h, out_w, cout)

        model.pcie.tlp_write(
            output_buf.phys_addr, out.astype(np.float32).tobytes()
        )

    def _exec_cv_mmul_f32(self, model, blob, cmd) -> None:
        """Float32 MMUL: plain GEMM, or im2col convolution via embedded metadata."""
        import numpy as np
        import struct

        input_id, weight_id, output_id, scale_id = cmd.buffers
        M, K, N = cmd.mmul  # type: ignore[misc]

        input_buf = blob.buffers[input_id - 1]
        weight_buf = blob.buffers[weight_id - 1]
        output_buf = blob.buffers[output_id - 1]

        wt_bytes = model.pcie.tlp_read(weight_buf.phys_addr, weight_buf.size)

        op_kind = 0
        meta = None
        if len(wt_bytes) >= K * N * 4 + self._CONV_META_SIZE:
            meta_bytes = wt_bytes[-self._CONV_META_SIZE:]
            vals = struct.unpack("<20I", meta_bytes)
            if vals[0] == self._CONV_META_MAGIC:
                op_kind = vals[1]
                meta = vals

        if op_kind in (1, 2):
            self._exec_cv_conv_f32(
                model, input_buf, weight_buf, output_buf, wt_bytes, meta, M, K, N
            )
            return

        act_bytes = model.pcie.tlp_read(input_buf.phys_addr, input_buf.size)
        act = np.frombuffer(act_bytes, dtype=np.float32)
        if act.size < M * K:
            act = np.pad(act, (0, M * K - act.size), constant_values=0.0)
        act = act[:M * K].reshape(M, K)

        wt = np.frombuffer(wt_bytes, dtype=np.float32)
        if wt.size < K * N:
            wt = np.pad(wt, (0, K * N - wt.size), constant_values=0.0)
        wt = wt[:K * N].reshape(K, N)

        result = np.matmul(act, wt).astype(np.float32)
        model.pcie.tlp_write(output_buf.phys_addr, result.tobytes())

    def _exec_cv_sfu_f32(self, model, blob, cmd) -> None:
        """Float32 SFU: read float32 from DRAM, apply activation, write float32."""
        import numpy as np

        sfu_op, elements, head_dim, pos = cmd.sfu  # type: ignore[misc]
        input_id, output_id = cmd.buffers[:2]

        input_buf = blob.buffers[input_id - 1]
        output_buf = blob.buffers[output_id - 1]

        fp_bytes = model.pcie.tlp_read(input_buf.phys_addr, elements * 4)
        x = np.frombuffer(fp_bytes, dtype=np.float32)

        if sfu_op == 3:  # ReLU
            result = np.maximum(x, 0.0)
        elif sfu_op == 2:  # GELU
            result = 0.5 * x * (1.0 + np.tanh(
                np.sqrt(2.0 / np.pi) * (x + 0.044715 * x**3)))
        elif sfu_op == 4:  # SiLU
            result = x / (1.0 + np.exp(-x))
        elif sfu_op == 5:  # HardSwish
            result = x * np.clip(x + 3.0, 0.0, 6.0) / 6.0
        elif sfu_op == 6:  # HardSigmoid
            result = np.clip(x + 3.0, 0.0, 6.0) / 6.0
        else:
            raise RuntimeError(f"unsupported CV F32 SFU op={sfu_op}")

        model.pcie.tlp_write(output_buf.phys_addr, result.astype(np.float32).tobytes())

    def _exec_cv_vector_f32(self, model, blob, cmd) -> None:
        """Float32 Vector: element-wise, reduction, with broadcast support."""
        import numpy as np

        vec_op, elements = cmd.vector  # type: ignore[misc]
        a_id, b_id, output_id = cmd.buffers[:3]

        a_buf = blob.buffers[a_id - 1]
        b_buf = blob.buffers[b_id - 1] if b_id else None
        output_buf = blob.buffers[output_id - 1]

        a_bytes = model.pcie.tlp_read(a_buf.phys_addr, elements * 4)
        a = np.frombuffer(a_bytes, dtype=np.float32)

        if b_buf is not None:
            b_bytes = model.pcie.tlp_read(
                b_buf.phys_addr, min(b_buf.size, elements * 4)
            )
            b = np.frombuffer(b_bytes, dtype=np.float32)
            if 0 < b.size < elements and elements % b.size == 0:
                b = np.tile(b, elements // b.size)
            elif b.size < elements:
                b = np.pad(b, (0, elements - b.size), constant_values=0.0)
        else:
            b = np.zeros(elements, dtype=np.float32)

        if vec_op == 0:  # VADD
            result = a + b
        elif vec_op == 1:  # VMUL
            result = a * b
        elif vec_op == 2:  # VRED_MAX
            result = np.array([float(np.max(a[:elements]))], dtype=np.float32)
        elif vec_op == 3:  # VRED_SUM
            out_elems = output_buf.size // 4
            if (
                out_elems > 1
                and out_elems <= elements
                and elements % out_elems == 0
            ):
                segment = elements // out_elems
                if b_id == 0:
                    # CV F32 ReduceMean/GAP on NHWC data: each row of the
                    # reshaped view is one spatial position (all channels), so
                    # the mean over rows is the per-channel mean.
                    result = (a[:elements].reshape(-1, out_elems).mean(
                        axis=0
                    )).astype(np.float32)
                else:
                    result = (a[:elements].reshape(out_elems, segment).sum(
                        axis=1
                    ) / segment).astype(np.float32)
            else:
                result = np.array(
                    [float(np.sum(a[:elements]))], dtype=np.float32
                )
        else:
            raise RuntimeError(f"unsupported CV F32 vector op={vec_op}")

        out_bytes = result.astype(np.float32).tobytes()
        if len(out_bytes) < output_buf.size:
            out_bytes = out_bytes.ljust(output_buf.size, b"\x00")
        model.pcie.tlp_write(output_buf.phys_addr, out_bytes[:output_buf.size])

    def _flatten_blobs(self, cmd_blob: bytes):
        """Parse W2-T7 serialized format and flatten to legacy layout.

        Returns (ring_data, desc_data, total_cmd_count) where ring_data
        has 24B entries and desc_data has 60B entries — matching the
        format expected by _execute_flat().
        """
        nop_count, blob_count, total_cmd_count = struct.unpack_from(
            "<III", cmd_blob, 0
        )
        blob_offset = 12  # past 3×uint32 header

        flat_ring = bytearray()
        flat_desc = bytearray()
        flat_cmd_count = 0

        for _ in range(blob_count):
            if blob_offset + 64 > len(cmd_blob):
                raise ValueError("blob too short for header")
            blk = cmd_blob[blob_offset:]

            magic = struct.unpack_from("<I", blk, 0)[0]
            if magic != CAD_BLOB_MAGIC:
                raise ValueError(f"bad blob magic: {magic:#x}")

            cmd_count_here = struct.unpack_from("<I", blk, 20)[0]
            desc_size = struct.unpack_from("<I", blk, 24)[0]
            desc_off = struct.unpack_from("<I", blk, 28)[0]
            cmd_size = struct.unpack_from("<I", blk, 32)[0]
            cmd_off = struct.unpack_from("<I", blk, 36)[0]
            bt_size = struct.unpack_from("<I", blk, 40)[0]
            bt_off = struct.unpack_from("<I", blk, 44)[0]

            blob_end = blob_offset + max(
                64, bt_off + bt_size, cmd_off + cmd_size, desc_off + desc_size
            )
            if blob_end > len(cmd_blob):
                raise ValueError("blob directory out of bounds")

            ring_raw = blk[cmd_off: cmd_off + cmd_size]
            desc_raw = blk[desc_off: desc_off + desc_size]

            for i in range(cmd_count_here):
                ring_entry = ring_raw[i * 32: (i + 1) * 32]
                opcode = struct.unpack_from("<I", ring_entry, 0)[0]
                desc_offset = struct.unpack_from("<I", ring_entry, 4)[0]
                flags = struct.unpack_from("<I", ring_entry, 8)[0]

                desc_addr = DESC_ADDR_BASE + desc_offset

                # 24B flat ring entry: opcode + desc_addr + flags + pad
                # All three fields are uint32_t (matches firmware cmd_entry_t).
                entry_24 = struct.pack("<III", opcode, desc_addr, flags)
                entry_24 += b"\x00" * 12
                flat_ring.extend(entry_24)

                # Descriptor: 60B
                desc_bytes_here = desc_raw[
                    desc_offset: desc_offset + CAD_DESC_BYTES
                ]
                if len(desc_bytes_here) != CAD_DESC_BYTES:
                    desc_bytes_here = desc_bytes_here.ljust(CAD_DESC_BYTES, b"\x00")
                flat_desc.extend(desc_bytes_here)

                flat_cmd_count += 1

            blob_offset = blob_end

        _ = nop_count  # NOPs carry no work; already counted in total_cmd_count.
        return bytes(flat_ring), bytes(flat_desc), flat_cmd_count

    def _execute_flat(self, ring_data, desc_data, cmd_count):
        """Core execution: write ring entries + descriptors, run firmware.

        Returns an ExecutionStatsT with per-engine op counts derived from
        the command ring entries and descriptor data.
        """
        from caduceus_device_protocol.ExecutionStats import ExecutionStatsT

        stats = ExecutionStatsT()
        stats.mmulOps = 0
        stats.sfuOps = 0
        stats.vectorOps = 0
        stats.dmaOps = 0
        stats.dmaBytesRead = 0
        stats.dmaBytesWritten = 0

        # Opcode constants matching firmware and command_ir.h.
        OP_MMUL       = 0x00
        OP_SFU        = 0x01
        OP_SFU_LAYERNORM = 0x02
        OP_SFU_GELU   = 0x03
        OP_SFU_RELU   = 0x04
        OP_ROPE       = 0x05
        OP_SFU_SILU   = 0x06
        OP_PCIE_DMA   = 0x07
        OP_DMA_COPY   = 0x09
        OP_DMA_ST     = 0x0A
        OP_VECTOR_ADD = 0x0F
        OP_VECTOR_MUL = 0x10
        OP_VRED_MAX   = 0x11
        OP_VRED_SUM   = 0x12
        OP_VCONV      = 0x13
        OP_VRESID     = 0x14
        OP_DMA_COPY_LDD = 0x15
        OP_DMA_COPY_STD = 0x16
        OP_SFU_RMSNORM = 0x17
        OP_BARRIER    = 0xFF

        with self._model_lock:
            model = self._model

            tail = model.firmware.doorbell["host_tail"]
            if tail + cmd_count > RING_SIZE:
                raise RuntimeError("ring buffer overflow")

            for i in range(cmd_count):
                entry_offset = i * RING_ENTRY_SIZE
                opcode, desc_addr, flags = struct.unpack_from(
                    "<III", ring_data, entry_offset
                )
                desc_offset = i * 60
                descriptor = desc_data[desc_offset: desc_offset + 60]

                model.pcie.tlp_write(desc_addr, descriptor)

                ring_idx_addr = RING_BUFFER_ADDR + (tail + i) * RING_SLOT_SIZE
                entry = struct.pack("<III", opcode, desc_addr, flags) + b"\x00" * 12
                model.pcie.tlp_write(ring_idx_addr, entry)

                # ── Track per-engine stats from the command descriptor ──
                # Descriptor layout matches compiler/lower.c and firmware npu_firmware.c.
                if opcode == OP_MMUL:
                    stats.mmulOps += 1
                    # MMUL descriptor: sizes at offsets 32/36/40/44 (bytes).
                    if len(descriptor) >= 48:
                        inp_bytes = struct.unpack_from("<I", descriptor, 32)[0]
                        wt_bytes = struct.unpack_from("<I", descriptor, 36)[0]
                        out_bytes = struct.unpack_from("<I", descriptor, 40)[0]
                        scale_bytes = struct.unpack_from("<I", descriptor, 44)[0]
                        stats.dmaBytesRead += inp_bytes + wt_bytes + scale_bytes
                        stats.dmaBytesWritten += out_bytes
                elif opcode in (
                    OP_SFU, OP_SFU_LAYERNORM, OP_SFU_GELU, OP_SFU_RELU,
                    OP_ROPE, OP_SFU_SILU, OP_SFU_RMSNORM,
                ):
                    stats.sfuOps += 1
                    # SFU descriptor: dim at offset 32 (elements in low 16 bits).
                    if len(descriptor) >= 36:
                        dim = struct.unpack_from("<I", descriptor, 32)[0]
                        elements = dim & 0xFFFF
                        stats.dmaBytesRead += elements * 2
                        stats.dmaBytesWritten += elements * 2
                elif opcode in (
                    OP_VECTOR_ADD, OP_VECTOR_MUL, OP_VRED_MAX, OP_VRED_SUM,
                    OP_VCONV, OP_VRESID,
                ):
                    stats.vectorOps += 1
                    # Vector descriptor: element count at offset 32.
                    if len(descriptor) >= 36:
                        elements = struct.unpack_from("<I", descriptor, 32)[0]
                        stats.dmaBytesRead += elements * 4
                        stats.dmaBytesWritten += elements * 4
                elif opcode in (OP_DMA_COPY, OP_DMA_ST, OP_DMA_COPY_LDD, OP_DMA_COPY_STD):
                    stats.dmaOps += 1
                    # DMA descriptor: transfer size at offset 32.
                    if len(descriptor) >= 36:
                        db = struct.unpack_from("<I", descriptor, 32)[0]
                        stats.dmaBytesRead += db
                        stats.dmaBytesWritten += db
                elif opcode == OP_PCIE_DMA:
                    # PCIe DMA descriptor is 6 words; length at offset 12.
                    if len(descriptor) >= 16:
                        db = struct.unpack_from("<I", descriptor, 12)[0]
                        stats.dmaBytesRead += db
                        stats.dmaBytesWritten += db

            new_tail = (tail + cmd_count) % RING_SIZE
            model.firmware.doorbell["host_tail"] = new_tail
            model.bridge.handle(
                "write",
                Addr.DOORBELL + DOORBELL.HOST_TAIL,
                new_tail,
            )
            model.bridge._set_irq(8)  # HOST doorbell interrupt

            model.firmware.run_loop(max_commands=cmd_count)

        return stats

    # ── Connection management ─────────────────────────────────────────────

    def _allocate_conn_id(self) -> int:
        with self._request_lock:
            conn_id = self._next_conn_id
            self._next_conn_id += 1
            self._per_conn_last_id[conn_id] = 0
            return conn_id

    def _release_conn_id(self, conn_id: int) -> None:
        with self._request_lock:
            self._per_conn_last_id.pop(conn_id, None)

    # ── Request handling ──────────────────────────────────────────────────

    def _next_request_id_ok(self, conn_id: int, request_id: int) -> bool:
        with self._request_lock:
            last = self._per_conn_last_id.get(conn_id, 0)
            if request_id <= last:
                return False
            self._per_conn_last_id[conn_id] = request_id
            return True

    def _handle_message(self, wire: bytes, conn_id: int = 0) -> bytearray:
        try:
            msg, computed_checksum = parse_message(wire)
        except Exception as exc:
            return self._error_response(
                0, DeviceOpcode.OPCODE_DEVICE_RESET, DeviceStatus.STATUS_INVALID_MESSAGE, str(exc)
            )

        h = msg.header
        opcode = h.opcode
        rid = h.requestId

        if h.magic != MAGIC:
            LOGGER.debug("bad magic rid=%s opcode=%s magic=%#x", rid, opcode, h.magic)
            return self._error_response(
                rid, opcode, DeviceStatus.STATUS_INVALID_MESSAGE, "bad magic"
            )
        if h.protocolVersion != PROTOCOL_VERSION:
            LOGGER.debug("version mismatch rid=%s version=%s", rid, h.protocolVersion)
            return self._error_response(
                rid, opcode, DeviceStatus.STATUS_INVALID_MESSAGE, "version mismatch"
            )
        if h.checksum != computed_checksum:
            LOGGER.debug("checksum mismatch rid=%s claimed=%s computed=%s", rid, h.checksum, computed_checksum)
            return self._error_response(
                rid, opcode, DeviceStatus.STATUS_INVALID_MESSAGE, "checksum mismatch"
            )
        try:
            validate_header(msg)
        except Exception as exc:
            LOGGER.debug("validate_header failed rid=%s: %s", rid, exc)
            return self._error_response(
                rid, opcode, DeviceStatus.STATUS_INVALID_MESSAGE, str(exc)
            )
        if not self._next_request_id_ok(conn_id, rid):
            last = self._per_conn_last_id.get(conn_id, 0)
            LOGGER.debug("request out of order rid=%s conn=%s last=%s", rid, conn_id, last)
            return self._error_response(
                rid, opcode, DeviceStatus.STATUS_INVALID_MESSAGE, "request out of order"
            )

        try:
            return self._dispatch(opcode, rid, msg, conn_id=conn_id)
        except Exception as exc:
            return self._error_response(
                rid, opcode, DeviceStatus.STATUS_INVALID_MESSAGE, str(exc)
            )

    def _dispatch(self, opcode: int, rid: int, msg: DeviceMessageT, conn_id: int = 0) -> bytearray:
        if opcode == DeviceOpcode.OPCODE_DEVICE_RESET:
            return self._do_device_reset(rid, msg, conn_id=conn_id)
        if opcode == DeviceOpcode.OPCODE_DEVICE_CAPS:
            return self._do_device_caps(rid, msg)
        if opcode == DeviceOpcode.OPCODE_BUFFER_ALLOC:
            return self._do_buffer_alloc(rid, msg)
        if opcode == DeviceOpcode.OPCODE_BUFFER_FREE:
            return self._do_buffer_free(rid, msg)
        if opcode == DeviceOpcode.OPCODE_BUFFER_READ:
            return self._do_buffer_read(rid, msg)
        if opcode == DeviceOpcode.OPCODE_BUFFER_WRITE:
            return self._do_buffer_write(rid, msg)
        if opcode == DeviceOpcode.OPCODE_BUFFER_SIZE:
            return self._do_buffer_size(rid, msg)
        if opcode == DeviceOpcode.OPCODE_SUBMIT:
            return self._do_submit(rid, msg)
        if opcode == DeviceOpcode.OPCODE_FENCE_CREATE:
            return self._do_fence_create(rid, msg)
        if opcode == DeviceOpcode.OPCODE_FENCE_DESTROY:
            return self._do_fence_destroy(rid, msg)
        if opcode == DeviceOpcode.OPCODE_FENCE_WAIT:
            return self._do_fence_wait(rid, msg)
        if opcode == DeviceOpcode.OPCODE_FENCE_POLL:
            return self._do_fence_poll(rid, msg)
        if opcode == DeviceOpcode.OPCODE_FENCE_STATUS:
            return self._do_fence_status(rid, msg)
        return self._error_response(
            rid, opcode, DeviceStatus.STATUS_UNKNOWN_OPCODE, "unknown opcode"
        )

    def _error_response(
        self,
        request_id: int,
        opcode: int,
        status: int,
        message: str,
    ) -> bytearray:
        resp = DeviceMessageT()
        resp.header = MessageHeaderT()
        resp.header.requestId = request_id
        resp.header.opcode = opcode
        resp.header.status = status
        err = ErrorResponseT()
        err.code = status
        err.message = message
        _set_payload(resp, pack_table(err))
        return build_message(resp)

    # ── Operation implementations ─────────────────────────────────────────

    def _do_device_reset(self, rid: int, msg: DeviceMessageT, conn_id: int = 0) -> bytearray:
        req = unpack_table(DeviceResetRequestT, _payload_bytes(msg))
        self._buffers = _BufferAllocator(DRAM_BUFFER_BASE, DRAM_BUFFER_END)
        self._fences.error_all()
        self._fences.clear()
        with self._request_lock:
            self._per_conn_last_id[conn_id] = 0
        resp = DeviceMessageT()
        resp.header = MessageHeaderT()
        resp.header.requestId = rid
        resp.header.opcode = DeviceOpcode.OPCODE_DEVICE_RESET
        resp.header.status = DeviceStatus.STATUS_OK
        _set_payload(resp, pack_table(DeviceResetResponseT()))
        return build_message(resp)

    def _do_device_caps(self, rid: int, msg: DeviceMessageT) -> bytearray:
        req = unpack_table(DeviceCapsRequestT, _payload_bytes(msg))
        resp = DeviceMessageT()
        resp.header = MessageHeaderT()
        resp.header.requestId = rid
        resp.header.opcode = DeviceOpcode.OPCODE_DEVICE_CAPS
        resp.header.status = DeviceStatus.STATUS_OK
        _set_payload(resp, pack_table(self._caps))
        return build_message(resp)

    def _do_buffer_alloc(self, rid: int, msg: DeviceMessageT) -> bytearray:
        req = unpack_table(BufferAllocRequestT, _payload_bytes(msg))
        if req.size == 0 or req.size > DRAM_BUFFER_END - DRAM_BUFFER_BASE:
            return self._error_response(
                rid, DeviceOpcode.OPCODE_BUFFER_ALLOC, DeviceStatus.STATUS_INVALID_ARGUMENT, "bad size"
            )
        addr = self._buffers.alloc(req.size)
        if addr is None:
            return self._error_response(
                rid, DeviceOpcode.OPCODE_BUFFER_ALLOC, DeviceStatus.STATUS_OUT_OF_MEMORY, "no memory"
            )
        resp = DeviceMessageT()
        resp.header = MessageHeaderT()
        resp.header.requestId = rid
        resp.header.opcode = DeviceOpcode.OPCODE_BUFFER_ALLOC
        resp.header.status = DeviceStatus.STATUS_OK
        inner = BufferAllocResponseT()
        inner.handle = addr
        inner.size = req.size
        _set_payload(resp, pack_table(inner))
        return build_message(resp)

    def _do_buffer_free(self, rid: int, msg: DeviceMessageT) -> bytearray:
        req = unpack_table(BufferFreeRequestT, _payload_bytes(msg))
        if not self._buffers.free(req.handle):
            return self._error_response(
                rid, DeviceOpcode.OPCODE_BUFFER_FREE, DeviceStatus.STATUS_INVALID_HANDLE, "bad handle"
            )
        resp = DeviceMessageT()
        resp.header = MessageHeaderT()
        resp.header.requestId = rid
        resp.header.opcode = DeviceOpcode.OPCODE_BUFFER_FREE
        resp.header.status = DeviceStatus.STATUS_OK
        _set_payload(resp, pack_table(BufferFreeResponseT()))
        return build_message(resp)

    def _do_buffer_read(self, rid: int, msg: DeviceMessageT) -> bytearray:
        req = unpack_table(BufferReadRequestT, _payload_bytes(msg))
        if not self._buffers.contains(req.handle):
            return self._error_response(
                rid, DeviceOpcode.OPCODE_BUFFER_READ, DeviceStatus.STATUS_INVALID_HANDLE, "bad handle"
            )
        if self._model is None:
            raise RuntimeError("model not initialized")
        addr = req.handle + req.offset
        with self._model_lock:
            data = self._model.pcie.tlp_read(addr, req.size)
        resp = DeviceMessageT()
        resp.header = MessageHeaderT()
        resp.header.requestId = rid
        resp.header.opcode = DeviceOpcode.OPCODE_BUFFER_READ
        resp.header.status = DeviceStatus.STATUS_OK
        inner = BufferReadResponseT()
        inner.data = list(data)
        _set_payload(resp, pack_table(inner))
        return build_message(resp)

    def _do_buffer_write(self, rid: int, msg: DeviceMessageT) -> bytearray:
        req = unpack_table(BufferWriteRequestT, _payload_bytes(msg))
        if not self._buffers.contains(req.handle):
            return self._error_response(
                rid, DeviceOpcode.OPCODE_BUFFER_WRITE, DeviceStatus.STATUS_INVALID_HANDLE, "bad handle"
            )
        if self._model is None:
            raise RuntimeError("model not initialized")
        addr = req.handle + req.offset
        with self._model_lock:
            self._model.pcie.tlp_write(addr, bytes(req.data))
        resp = DeviceMessageT()
        resp.header = MessageHeaderT()
        resp.header.requestId = rid
        resp.header.opcode = DeviceOpcode.OPCODE_BUFFER_WRITE
        resp.header.status = DeviceStatus.STATUS_OK
        _set_payload(resp, pack_table(BufferWriteResponseT()))
        return build_message(resp)

    def _do_buffer_size(self, rid: int, msg: DeviceMessageT) -> bytearray:
        req = unpack_table(BufferSizeRequestT, _payload_bytes(msg))
        if not self._buffers.contains(req.handle):
            return self._error_response(
                rid, DeviceOpcode.OPCODE_BUFFER_SIZE, DeviceStatus.STATUS_INVALID_HANDLE, "bad handle"
            )
        resp = DeviceMessageT()
        resp.header = MessageHeaderT()
        resp.header.requestId = rid
        resp.header.opcode = DeviceOpcode.OPCODE_BUFFER_SIZE
        resp.header.status = DeviceStatus.STATUS_OK
        inner = BufferSizeResponseT()
        inner.size = self._buffers.size_of(req.handle)
        _set_payload(resp, pack_table(inner))
        return build_message(resp)

    def _do_submit(self, rid: int, msg: DeviceMessageT) -> bytearray:
        req = unpack_table(SubmitRequestT, _payload_bytes(msg))
        if req.fenceHandle != 0 and self._fences.get(req.fenceHandle) is None:
            return self._error_response(
                rid, DeviceOpcode.OPCODE_SUBMIT, DeviceStatus.STATUS_INVALID_HANDLE, "bad fence"
            )

        cmd_blob = bytes(req.cmdBlob)
        cmd_count = req.cmdCount

        # Compute execution stats from the submitted blob by flattening
        # it through the same code path used during execution.
        from caduceus_device_protocol.ExecutionStats import ExecutionStatsT
        stats = ExecutionStatsT()
        stats.mmulOps = 0
        stats.sfuOps = 0
        stats.vectorOps = 0
        stats.dmaOps = 0
        stats.dmaBytesRead = 0
        stats.dmaBytesWritten = 0
        stats_populated = 0

        # If blob uses W2-T7 headered format, flatten and count ops.
        if (len(cmd_blob) >= 16
                and struct.unpack_from("<I", cmd_blob, 12)[0] == CAD_BLOB_MAGIC):
            try:
                ring_data, desc_data, actual_count = self._flatten_blobs(cmd_blob)
                # Count per-engine ops from the ring entries + descriptors.
                self._count_blob_stats(ring_data, desc_data, actual_count, stats)
                stats_populated = 1
            except Exception:
                pass  # stats remain zero on parse failure
        else:
            # Legacy flat format: count from ring + descriptor data.
            ring_bytes = cmd_count * RING_ENTRY_SIZE
            desc_bytes = cmd_count * 60
            if len(cmd_blob) >= ring_bytes + desc_bytes:
                try:
                    self._count_blob_stats(
                        cmd_blob[:ring_bytes],
                        cmd_blob[ring_bytes: ring_bytes + desc_bytes],
                        cmd_count,
                        stats,
                    )
                    stats_populated = 1
                except Exception:
                    pass

        self._cmd_queue.put(
            _PendingCommand(
                fence_handle=req.fenceHandle or 0,
                cmd_count=cmd_count,
                cmd_blob=cmd_blob,
            )
        )

        resp = DeviceMessageT()
        resp.header = MessageHeaderT()
        resp.header.requestId = rid
        resp.header.opcode = DeviceOpcode.OPCODE_SUBMIT
        resp.header.status = DeviceStatus.STATUS_OK
        inner = SubmitResponseT()
        if stats_populated:
            inner.execStats = stats
        _set_payload(resp, pack_table(inner))
        return build_message(resp)

    @staticmethod
    def _count_blob_stats(ring_data, desc_data, cmd_count, stats):
        """Count per-engine ops and DMA bytes from flattened ring entries."""
        # Engine opcodes from gen/npu_abi.h / command_ir.h.
        OP_MMUL          = 0x00
        OP_SFU           = 0x01
        OP_SFU_LAYERNORM = 0x02
        OP_SFU_GELU      = 0x03
        OP_SFU_RELU      = 0x04
        OP_ROPE          = 0x05
        OP_SFU_SILU      = 0x06
        OP_PCIE_DMA      = 0x07
        OP_DMA_COPY      = 0x09
        OP_DMA_ST        = 0x0A
        OP_VECTOR_ADD    = 0x0F
        OP_VECTOR_MUL    = 0x10
        OP_VRED_MAX      = 0x11
        OP_VRED_SUM      = 0x12
        OP_VCONV         = 0x13
        OP_VRESID        = 0x14
        OP_DMA_COPY_LDD  = 0x15
        OP_DMA_COPY_STD  = 0x16
        OP_SFU_RMSNORM   = 0x17
        OP_BARRIER       = 0xFF

        for i in range(cmd_count):
            offset = i * RING_ENTRY_SIZE
            if offset + 12 > len(ring_data):
                break
            opcode = struct.unpack_from("<I", ring_data, offset)[0]
            d_off = i * 60
            desc = desc_data[d_off: d_off + 60] if d_off + 60 <= len(desc_data) else b""

            # Descriptor layout matches compiler/lower.c and firmware npu_firmware.c.
            if opcode == OP_MMUL:
                stats.mmulOps += 1
                if len(desc) >= 48:
                    inp = struct.unpack_from("<I", desc, 32)[0]
                    wt  = struct.unpack_from("<I", desc, 36)[0]
                    out = struct.unpack_from("<I", desc, 40)[0]
                    sc  = struct.unpack_from("<I", desc, 44)[0]
                    stats.dmaBytesRead += inp + wt + sc
                    stats.dmaBytesWritten += out
            elif opcode in (
                OP_SFU, OP_SFU_LAYERNORM, OP_SFU_GELU, OP_SFU_RELU,
                OP_ROPE, OP_SFU_SILU, OP_SFU_RMSNORM,
            ):
                stats.sfuOps += 1
                if len(desc) >= 36:
                    dim = struct.unpack_from("<I", desc, 32)[0]
                    elements = dim & 0xFFFF
                    stats.dmaBytesRead += elements * 2
                    stats.dmaBytesWritten += elements * 2
            elif opcode in (
                OP_VECTOR_ADD, OP_VECTOR_MUL, OP_VRED_MAX, OP_VRED_SUM,
                OP_VCONV, OP_VRESID,
            ):
                stats.vectorOps += 1
                if len(desc) >= 36:
                    elements = struct.unpack_from("<I", desc, 32)[0]
                    stats.dmaBytesRead += elements * 4
                    stats.dmaBytesWritten += elements * 4
            elif opcode in (OP_DMA_COPY, OP_DMA_ST, OP_DMA_COPY_LDD, OP_DMA_COPY_STD):
                stats.dmaOps += 1
                if len(desc) >= 36:
                    db = struct.unpack_from("<I", desc, 32)[0]
                    stats.dmaBytesRead += db
                    stats.dmaBytesWritten += db
            elif opcode == OP_PCIE_DMA:
                # PCIe DMA descriptor is 6 words; length at offset 12.
                if len(desc) >= 16:
                    db = struct.unpack_from("<I", desc, 12)[0]
                    stats.dmaBytesRead += db
                    stats.dmaBytesWritten += db

    def _do_fence_create(self, rid: int, msg: DeviceMessageT) -> bytearray:
        req = unpack_table(FenceCreateRequestT, _payload_bytes(msg))
        handle = self._fences.create()
        resp = DeviceMessageT()
        resp.header = MessageHeaderT()
        resp.header.requestId = rid
        resp.header.opcode = DeviceOpcode.OPCODE_FENCE_CREATE
        resp.header.status = DeviceStatus.STATUS_OK
        inner = FenceCreateResponseT()
        inner.handle = handle
        _set_payload(resp, pack_table(inner))
        return build_message(resp)

    def _do_fence_destroy(self, rid: int, msg: DeviceMessageT) -> bytearray:
        req = unpack_table(FenceDestroyRequestT, _payload_bytes(msg))
        if not self._fences.destroy(req.handle):
            return self._error_response(
                rid, DeviceOpcode.OPCODE_FENCE_DESTROY, DeviceStatus.STATUS_INVALID_HANDLE, "bad fence"
            )
        resp = DeviceMessageT()
        resp.header = MessageHeaderT()
        resp.header.requestId = rid
        resp.header.opcode = DeviceOpcode.OPCODE_FENCE_DESTROY
        resp.header.status = DeviceStatus.STATUS_OK
        _set_payload(resp, pack_table(FenceDestroyResponseT()))
        return build_message(resp)

    def _do_fence_wait(self, rid: int, msg: DeviceMessageT) -> bytearray:
        req = unpack_table(FenceWaitRequestT, _payload_bytes(msg))
        fence = self._fences.get(req.handle)
        if fence is None:
            return self._error_response(
                rid, DeviceOpcode.OPCODE_FENCE_WAIT, DeviceStatus.STATUS_INVALID_HANDLE, "bad fence"
            )
        timeout_s = None
        if req.timeoutNs != 0xFFFFFFFFFFFFFFFF:
            timeout_s = req.timeoutNs / 1e9
        signalled = fence.event.wait(timeout=timeout_s)
        resp = DeviceMessageT()
        resp.header = MessageHeaderT()
        resp.header.requestId = rid
        resp.header.opcode = DeviceOpcode.OPCODE_FENCE_WAIT
        resp.header.status = DeviceStatus.STATUS_OK if signalled else DeviceStatus.STATUS_TIMEOUT
        inner = FenceWaitResponseT()
        inner.signalled = signalled
        _set_payload(resp, pack_table(inner))
        return build_message(resp)

    def _do_fence_poll(self, rid: int, msg: DeviceMessageT) -> bytearray:
        req = unpack_table(FencePollRequestT, _payload_bytes(msg))
        fence = self._fences.get(req.handle)
        if fence is None:
            return self._error_response(
                rid, DeviceOpcode.OPCODE_FENCE_POLL, DeviceStatus.STATUS_INVALID_HANDLE, "bad fence"
            )
        signalled = fence.signalled
        status = DeviceStatus.STATUS_OK if signalled else DeviceStatus.STATUS_NOT_READY
        resp = DeviceMessageT()
        resp.header = MessageHeaderT()
        resp.header.requestId = rid
        resp.header.opcode = DeviceOpcode.OPCODE_FENCE_POLL
        resp.header.status = status
        inner = FencePollResponseT()
        inner.signalled = signalled
        _set_payload(resp, pack_table(inner))
        return build_message(resp)

    def _do_fence_status(self, rid: int, msg: DeviceMessageT) -> bytearray:
        req = unpack_table(FenceStatusRequestT, _payload_bytes(msg))
        fence = self._fences.get(req.handle)
        if fence is None:
            return self._error_response(
                rid, DeviceOpcode.OPCODE_FENCE_STATUS, DeviceStatus.STATUS_INVALID_HANDLE, "bad fence"
            )
        resp = DeviceMessageT()
        resp.header = MessageHeaderT()
        resp.header.requestId = rid
        resp.header.opcode = DeviceOpcode.OPCODE_FENCE_STATUS
        resp.header.status = DeviceStatus.STATUS_OK
        inner = FenceStatusResponseT()
        inner.status = fence.status
        _set_payload(resp, pack_table(inner))
        return build_message(resp)


# ── Socket server glue ─────────────────────────────────────────────────────


class _FmRequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: FmDeviceServer = self.server.fm_server
        conn_id = server._allocate_conn_id()
        sock = self.request
        try:
            while True:
                try:
                    wire = recv_framed(sock)
                except ConnectionError:
                    break
                if not wire:
                    break
                response = server._handle_message(wire, conn_id=conn_id)
                send_framed(sock, response)
        finally:
            server._release_conn_id(conn_id)
            try:
                sock.close()
            except Exception:
                pass


class _ThreadedUnixFmServer(socketserver.ThreadingUnixStreamServer):
    allow_reuse_address = True

    def __init__(
        self,
        sock_path: str,
        fm_server: FmDeviceServer,
        ready_event: Optional[threading.Event] = None,
    ):
        self.fm_server = fm_server
        self.ready_event = ready_event
        try:
            os.unlink(sock_path)
        except FileNotFoundError:
            pass
        super().__init__(sock_path, _FmRequestHandler)

    def service_actions(self) -> None:
        if self.ready_event is not None:
            self.ready_event.set()
        super().service_actions()


def serve(
    sock_path: str = DEFAULT_SOCK_PATH,
    use_spike: bool = False,
    ready_event: Optional[threading.Event] = None,
) -> _ThreadedUnixFmServer:
    """Start a threaded Unix socket FM device server."""
    fm = FmDeviceServer(sock_path=sock_path, use_spike=use_spike)
    fm.start()
    server = _ThreadedUnixFmServer(sock_path, fm, ready_event=ready_event)
    return server


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="CaduceusCore Func Model device server")
    parser.add_argument(
        "--sock", default=DEFAULT_SOCK_PATH, help="Unix socket path"
    )
    parser.add_argument(
        "--spike", action="store_true", help="Use Spike firmware if available"
    )
    args = parser.parse_args(argv)

    level_name = os.environ.get("CADUCEUS_LOG_LEVEL", "WARN").upper()
    level_map = {
        "TRACE": logging.DEBUG,
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    log_level = level_map.get(level_name, logging.WARNING)
    logging.basicConfig(
        level=log_level,
        format="[%(name)s] [%(levelname)s] %(filename)s:%(lineno)d: %(message)s",
    )

    server = serve(sock_path=args.sock, use_spike=args.spike)
    LOGGER.info("FM device server listening on %s", args.sock)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.fm_server.stop()
        server.server_close()
        try:
            os.unlink(args.sock)
        except FileNotFoundError:
            pass
        LOGGER.info("FM device server shut down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
