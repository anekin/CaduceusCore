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
                    return addr
            return None

    def free(self, addr: int) -> bool:
        with self._lock:
            for region in self._regions:
                if region.addr == addr and not region.free:
                    region.free = True
                    self._coalesce()
                    return True
            return False

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
        self._model = FuncModel(use_spike=(self.use_spike or None))
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
        """Write descriptors + ring entries and run the firmware loop."""
        if self._model is None:
            raise RuntimeError("model not initialized")

        ring_bytes = cmd_count * RING_ENTRY_SIZE
        desc_bytes = cmd_count * 60
        if len(cmd_blob) < ring_bytes + desc_bytes:
            raise ValueError("cmd_blob too short")

        with self._model_lock:
            model = self._model
            ring_data = cmd_blob[:ring_bytes]
            desc_data = cmd_blob[ring_bytes: ring_bytes + desc_bytes]

            tail = model.firmware.doorbell["host_tail"]
            if tail + cmd_count > RING_SIZE:
                raise RuntimeError("ring buffer overflow")

            for i in range(cmd_count):
                entry_offset = i * RING_ENTRY_SIZE
                opcode, desc_addr, flags = struct.unpack_from(
                    "<IQI", ring_data, entry_offset
                )
                desc_offset = i * 60
                descriptor = desc_data[desc_offset: desc_offset + 60]

                model.pcie.tlp_write(desc_addr, descriptor)

                ring_idx_addr = RING_BUFFER_ADDR + (tail + i) * RING_SLOT_SIZE
                entry = struct.pack("<IQI", opcode, desc_addr, flags) + b"\x00" * 8
                model.pcie.tlp_write(ring_idx_addr, entry)

            new_tail = (tail + cmd_count) % RING_SIZE
            model.firmware.doorbell["host_tail"] = new_tail
            model.bridge.handle(
                "write",
                Addr.DOORBELL + DOORBELL.HOST_TAIL,
                new_tail,
            )
            model.bridge._set_irq(8)  # HOST doorbell interrupt

            # Run firmware dispatch loop.
            model.firmware.run_loop(max_commands=cmd_count)

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
        # Size is not tracked per-handle in the simple allocator; return max.
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
        inner.size = 0
        _set_payload(resp, pack_table(inner))
        return build_message(resp)

    def _do_submit(self, rid: int, msg: DeviceMessageT) -> bytearray:
        req = unpack_table(SubmitRequestT, _payload_bytes(msg))
        if req.fenceHandle != 0 and self._fences.get(req.fenceHandle) is None:
            return self._error_response(
                rid, DeviceOpcode.OPCODE_SUBMIT, DeviceStatus.STATUS_INVALID_HANDLE, "bad fence"
            )
        self._cmd_queue.put(
            _PendingCommand(
                fence_handle=req.fenceHandle or 0,
                cmd_count=req.cmdCount,
                cmd_blob=bytes(req.cmdBlob),
            )
        )
        resp = DeviceMessageT()
        resp.header = MessageHeaderT()
        resp.header.requestId = rid
        resp.header.opcode = DeviceOpcode.OPCODE_SUBMIT
        resp.header.status = DeviceStatus.STATUS_OK
        _set_payload(resp, pack_table(SubmitResponseT()))
        return build_message(resp)

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
