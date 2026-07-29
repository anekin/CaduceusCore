#!/usr/bin/env python3
"""CaduceusCore Func Model Device Server.

Implements the binary device protocol over a Unix domain socket.  The server
routes every operation through FuncModel's PCIe/BAR, doorbell, and completion
behaviour: host memory is modelled as device DRAM accessible via PCIe TLPs,
and compute is dispatched by the firmware run-loop, not by the server itself.
"""

from __future__ import annotations

import argparse
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


DEFAULT_SOCK_PATH = "/tmp/caduceus_fm.sock"

# DRAM region used for host-visible buffers (BAR1, 0x80000000+).
DRAM_BUFFER_BASE = Addr.DRAM + 0x0010_0000  # 1 MiB above base
DRAM_BUFFER_END = Addr.DRAM + 0x0100_0000   # 16 MiB total window

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
DESC_ADDR_BASE = 0x80F00000           # DRAM base for descriptors (unused above buffer window)


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
        self._last_request_id = 0
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
            self._model = FuncModel()
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

        Auto-detects between two formats:
        1. W2-T7 headered format: {uint32 nop_count, uint32 blob_count,
           uint32 total_cmd_count, raw blob bytes...} where each blob is
           a cad_command_blob_t encoded binary.
        2. Legacy flat format: ring_entries (24B each) || descriptors
           (60B each).
        """
        if self._model is None:
            raise RuntimeError("model not initialized")

        # Detect W2-T7 headered format: first blob starts with CADB magic
        # at offset 12 (after 3×uint32 header).
        if (len(cmd_blob) >= 16
                and struct.unpack_from("<I", cmd_blob, 12)[0] == CAD_BLOB_MAGIC):
            ring_data, desc_data, actual_count = self._flatten_blobs(cmd_blob)
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
        OP_VECTOR_ADD = 0x0F
        OP_VECTOR_MUL = 0x0E
        OP_DMA_COPY   = 0x09

        OP_SFU_SILU = 0x06

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
                if opcode == OP_MMUL:
                    stats.mmulOps += 1
                    # MMUL descriptor: bytes at offsets 0:4 are input size (M*K),
                    # offsets 4:8 are weight size (K*N), offsets 8:12 are output size (M*N)
                    # DMA reads: input + weight + scale; DMA writes: output.
                    # Read from descriptor for DMA byte tracking.
                    # Input (INT8 M×K), Weight (INT4 packed K×N/2), Scale (float32 N).
                    if len(descriptor) >= 12:
                        inp_bytes = struct.unpack_from("<I", descriptor, 0)[0]
                        wt_bytes  = struct.unpack_from("<I", descriptor, 4)[0]
                        out_bytes = struct.unpack_from("<I", descriptor, 8)[0]
                        scale_bytes = struct.unpack_from("<I", descriptor, 20)[0]
                        stats.dmaBytesRead += inp_bytes + wt_bytes + scale_bytes
                        stats.dmaBytesWritten += out_bytes
                elif opcode == OP_SFU or opcode == OP_SFU_SILU:
                    stats.sfuOps += 1
                    # SFU descriptor: input bytes at offset 4, output at offset 8
                    if len(descriptor) >= 12:
                        sf_in = struct.unpack_from("<I", descriptor, 4)[0]
                        sf_out = struct.unpack_from("<I", descriptor, 8)[0]
                        stats.dmaBytesRead += sf_in
                        stats.dmaBytesWritten += sf_out
                elif opcode == OP_VECTOR_ADD or opcode == OP_VECTOR_MUL:
                    stats.vectorOps += 1
                    if len(descriptor) >= 20:
                        va = struct.unpack_from("<I", descriptor, 4)[0]
                        vb = struct.unpack_from("<I", descriptor, 8)[0]
                        vo = struct.unpack_from("<I", descriptor, 12)[0]
                        stats.dmaBytesRead += va + vb
                        stats.dmaBytesWritten += vo
                elif opcode == OP_DMA_COPY:
                    stats.dmaOps += 1
                    # DMA descriptor: src bytes at offset 8, dst bytes at offset 12
                    if len(descriptor) >= 16:
                        dst_bytes = struct.unpack_from("<I", descriptor, 12)[0]
                        stats.dmaBytesRead += dst_bytes
                        stats.dmaBytesWritten += dst_bytes

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

    # ── Request handling ──────────────────────────────────────────────────

    def _next_request_id_ok(self, request_id: int) -> bool:
        with self._request_lock:
            if request_id <= self._last_request_id:
                return False
            self._last_request_id = request_id
            return True

    def _handle_message(self, wire: bytes) -> bytearray:
        """Parse one request and return the response wire bytes."""
        try:
            msg, computed_checksum = parse_message(wire)
        except Exception as exc:
            return self._error_response(
                0, DeviceOpcode.OPCODE_DEVICE_RESET, DeviceStatus.STATUS_INVALID_MESSAGE, str(exc)
            )

        h = msg.header
        opcode = h.opcode
        rid = h.requestId

        # Magic / version / checksum / ordering checks, in deterministic order.
        if h.magic != MAGIC:
            print(f"DEBUG: bad magic rid={rid} opcode={opcode} magic={h.magic:#x}")
            return self._error_response(
                rid, opcode, DeviceStatus.STATUS_INVALID_MESSAGE, "bad magic"
            )
        if h.protocolVersion != PROTOCOL_VERSION:
            print(f"DEBUG: version mismatch rid={rid} version={h.protocolVersion}")
            return self._error_response(
                rid, opcode, DeviceStatus.STATUS_INVALID_MESSAGE, "version mismatch"
            )
        if h.checksum != computed_checksum:
            print(f"DEBUG: checksum mismatch rid={rid} claimed={h.checksum} computed={computed_checksum}")
            return self._error_response(
                rid, opcode, DeviceStatus.STATUS_INVALID_MESSAGE, "checksum mismatch"
            )
        try:
            validate_header(msg)
        except Exception as exc:
            print(f"DEBUG: validate_header failed rid={rid}: {exc}")
            return self._error_response(
                rid, opcode, DeviceStatus.STATUS_INVALID_MESSAGE, str(exc)
            )
        if not self._next_request_id_ok(rid):
            print(f"DEBUG: request out of order rid={rid} last={self._last_request_id}")
            return self._error_response(
                rid, opcode, DeviceStatus.STATUS_INVALID_MESSAGE, "request out of order"
            )

        try:
            return self._dispatch(opcode, rid, msg)
        except Exception as exc:
            return self._error_response(
                rid, opcode, DeviceStatus.STATUS_INVALID_MESSAGE, str(exc)
            )

    def _dispatch(self, opcode: int, rid: int, msg: DeviceMessageT) -> bytearray:
        if opcode == DeviceOpcode.OPCODE_DEVICE_RESET:
            return self._do_device_reset(rid, msg)
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

    def _do_device_reset(self, rid: int, msg: DeviceMessageT) -> bytearray:
        req = unpack_table(DeviceResetRequestT, _payload_bytes(msg))
        self._buffers = _BufferAllocator(DRAM_BUFFER_BASE, DRAM_BUFFER_END)
        self._fences.error_all()
        self._fences.clear()
        with self._request_lock:
            self._last_request_id = 0
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
        OP_MMUL       = 0x00
        OP_SFU        = 0x01
        OP_SFU_SILU   = 0x06
        OP_VECTOR_ADD = 0x0F
        OP_VECTOR_MUL = 0x0E
        OP_DMA_COPY   = 0x09

        for i in range(cmd_count):
            offset = i * RING_ENTRY_SIZE
            if offset + 12 > len(ring_data):
                break
            opcode = struct.unpack_from("<I", ring_data, offset)[0]
            d_off = i * 60
            desc = desc_data[d_off: d_off + 60] if d_off + 60 <= len(desc_data) else b""

            if opcode == OP_MMUL:
                stats.mmulOps += 1
                if len(desc) >= 24:
                    inp = struct.unpack_from("<I", desc, 0)[0]
                    wt  = struct.unpack_from("<I", desc, 4)[0]
                    out = struct.unpack_from("<I", desc, 8)[0]
                    sc  = struct.unpack_from("<I", desc, 20)[0]
                    stats.dmaBytesRead += inp + wt + sc
                    stats.dmaBytesWritten += out
            elif opcode in (OP_SFU, OP_SFU_SILU):
                stats.sfuOps += 1
                if len(desc) >= 12:
                    sf_in = struct.unpack_from("<I", desc, 4)[0]
                    sf_out = struct.unpack_from("<I", desc, 8)[0]
                    stats.dmaBytesRead += sf_in
                    stats.dmaBytesWritten += sf_out
            elif opcode in (OP_VECTOR_ADD, OP_VECTOR_MUL):
                stats.vectorOps += 1
                if len(desc) >= 16:
                    va = struct.unpack_from("<I", desc, 4)[0]
                    vb = struct.unpack_from("<I", desc, 8)[0]
                    vo = struct.unpack_from("<I", desc, 12)[0]
                    stats.dmaBytesRead += va + vb
                    stats.dmaBytesWritten += vo
            elif opcode == OP_DMA_COPY:
                stats.dmaOps += 1
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
        sock = self.request
        try:
            while True:
                try:
                    wire = recv_framed(sock)
                except ConnectionError:
                    break
                if not wire:
                    break
                response = server._handle_message(wire)
                send_framed(sock, response)
        finally:
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

    server = serve(sock_path=args.sock, use_spike=args.spike)
    print(f"FM device server listening on {args.sock}", flush=True)

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
        print("FM device server shut down", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
