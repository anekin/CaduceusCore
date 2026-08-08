#!/usr/bin/env python3
"""CaduceusCore RTL Protocol Endpoint — Python mock/skeleton server.

FEASIBILITY-ONLY: implements the binary DeviceMessage protocol
(FlatBuffers + CRC-32 over Unix socket) and returns deterministic
responses for contract validation against the RTL transport.

For rtl://mock, the C Runtime transport connects here and exercises
the full protocol stack (magic, version, opcodes, checksum, framing).

When the real SoC RTL is ready, this endpoint is replaced by the
actual VCS/Cocotb-driven RTL simulator bridge.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import socketserver
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
_PACKAGE_ROOT = _SCRIPT_DIR.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from device_protocol import (
    MAGIC,
    PROTOCOL_VERSION,
    build_message,
    parse_message,
    recv_framed,
    send_framed,
    validate_header,
)

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

DEFAULT_SOCK_PATH = "/tmp/caduceus_rtl_mock.sock"

# ── Deterministic mock state ────────────────────────────────────────────────


class RtlMockState:
    """In-memory state for the RTL protocol mock endpoint."""

    def __init__(self):
        self._buf_id = 0
        self._fence_id = 0
        self._buffers: dict[int, bytearray] = {}     # handle → data
        self._buffer_sizes: dict[int, int] = {}      # handle → size
        self._fences: dict[int, int] = {}             # handle → status (0/1/2)
        self._next_request_id: int = 0

    def alloc_buffer(self, size: int) -> int:
        self._buf_id += 1
        handle = self._buf_id
        self._buffers[handle] = bytearray(size)
        self._buffer_sizes[handle] = size
        return handle

    def free_buffer(self, handle: int):
        self._buffers.pop(handle, None)
        self._buffer_sizes.pop(handle, None)

    def read_buffer(self, handle: int, offset: int, size: int) -> bytes:
        buf = self._buffers.get(handle)
        if buf is None:
            raise ValueError(f"invalid buffer handle {handle}")
        if offset + size > len(buf):
            raise ValueError(f"read out of bounds {offset}+{size} > {len(buf)}")
        return bytes(buf[offset:offset + size])

    def write_buffer(self, handle: int, offset: int, data: bytes):
        buf = self._buffers.get(handle)
        if buf is None:
            raise ValueError(f"invalid buffer handle {handle}")
        if offset + len(data) > len(buf):
            raise ValueError(f"write out of bounds {offset}+{len(data)} > {len(buf)}")
        buf[offset:offset + len(data)] = data

    def create_fence(self) -> int:
        self._fence_id += 1
        handle = self._fence_id
        self._fences[handle] = 0  # NOT_READY
        return handle

    def destroy_fence(self, handle: int):
        self._fences.pop(handle, None)

    def signal_fence(self, handle: int):
        if handle in self._fences:
            self._fences[handle] = 1  # COMPLETED

    def fence_status(self, handle: int) -> int:
        return self._fences.get(handle, 2)  # ERROR if unknown

    def reset(self):
        self._buffers.clear()
        self._buffer_sizes.clear()
        self._fences.clear()


# ── Protocol handler ────────────────────────────────────────────────────────


class RtlProtocolHandler:
    """Process incoming DeviceMessage requests and produce responses.

    Implements the same binary device protocol as the Func Model server
    (device_server.py) but with deterministic, in-memory mock behaviour
    — no actual RTL simulation occurs.
    """

    def __init__(self, state: Optional[RtlMockState] = None):
        self._state = state or RtlMockState()

    def handle(self, wire: bytearray) -> bytearray:
        """Parse request, apply operation, return framed response."""
        try:
            msg, computed = parse_message(bytes(wire))
        except (ValueError, Exception) as e:
            return self._error_response(
                DeviceStatus.STATUS_INVALID_MESSAGE,
                f"parse error: {e}",
                request_id=0,
            )

        # Validate checksum over raw wire bytes
        if msg.header.checksum != computed:
            return self._error_response(
                DeviceStatus.STATUS_INVALID_MESSAGE,
                f"checksum mismatch: claimed={msg.header.checksum:#010x} computed={computed:#010x}",
                request_id=msg.header.requestId if msg.header else 0,
            )

        try:
            validate_header(msg)
        except (ValueError, Exception) as e:
            return self._error_response(
                DeviceStatus.STATUS_INVALID_MESSAGE,
                f"header validation: {e}",
                request_id=msg.header.requestId if msg.header else 0,
            )

        h = msg.header
        if h is None:
            return self._error_response(
                DeviceStatus.STATUS_INVALID_MESSAGE, "missing header", request_id=0
            )

        request_id = h.requestId
        opcode = h.opcode

        try:
            resp = self._dispatch(opcode, msg)
            resp.header = MessageHeaderT()
            resp.header.magic = MAGIC
            resp.header.protocolVersion = PROTOCOL_VERSION
            resp.header.requestId = request_id
            resp.header.opcode = opcode
            resp.header.status = DeviceStatus.STATUS_OK
            return build_message(resp)
        except ValueError as e:
            return self._error_response(
                DeviceStatus.STATUS_INVALID_ARGUMENT,
                str(e),
                request_id=request_id,
                opcode=opcode,
            )
        except Exception as e:
            return self._error_response(
                DeviceStatus.STATUS_BUSY,
                f"internal error: {e}",
                request_id=request_id,
                opcode=opcode,
            )

    def _dispatch(self, opcode: int, msg: DeviceMessageT) -> DeviceMessageT:
        s = self._state
        pl = bytes(msg.payload) if msg.payload else b""

        if opcode == DeviceOpcode.OPCODE_BUFFER_ALLOC:
            req = BufferAllocRequestT.InitFromPackedBuf(pl, 0)
            handle = s.alloc_buffer(req.size)
            resp = DeviceMessageT()
            inner = BufferAllocResponseT()
            inner.handle = handle
            inner.size = req.size
            resp.payload = _pack_table(inner)
            return resp

        elif opcode == DeviceOpcode.OPCODE_BUFFER_FREE:
            req = BufferFreeRequestT.InitFromPackedBuf(pl, 0)
            s.free_buffer(req.handle)
            resp = DeviceMessageT()
            inner = BufferFreeResponseT()
            resp.payload = _pack_table(inner)
            return resp

        elif opcode == DeviceOpcode.OPCODE_BUFFER_READ:
            req = BufferReadRequestT.InitFromPackedBuf(pl, 0)
            data = s.read_buffer(req.handle, req.offset, req.size)
            resp = DeviceMessageT()
            inner = BufferReadResponseT()
            inner.data = list(data)
            resp.payload = _pack_table(inner)
            return resp

        elif opcode == DeviceOpcode.OPCODE_BUFFER_WRITE:
            req = BufferWriteRequestT.InitFromPackedBuf(pl, 0)
            s.write_buffer(req.handle, req.offset, bytes(req.data))
            resp = DeviceMessageT()
            inner = BufferWriteResponseT()
            resp.payload = _pack_table(inner)
            return resp

        elif opcode == DeviceOpcode.OPCODE_BUFFER_SIZE:
            req = BufferSizeRequestT.InitFromPackedBuf(pl, 0)
            size = s._buffer_sizes.get(req.handle, 0)
            resp = DeviceMessageT()
            inner = BufferSizeResponseT()
            inner.size = size
            resp.payload = _pack_table(inner)
            return resp

        elif opcode == DeviceOpcode.OPCODE_FENCE_CREATE:
            handle = s.create_fence()
            resp = DeviceMessageT()
            inner = FenceCreateResponseT()
            inner.handle = handle
            resp.payload = _pack_table(inner)
            return resp

        elif opcode == DeviceOpcode.OPCODE_FENCE_DESTROY:
            req = FenceDestroyRequestT.InitFromPackedBuf(pl, 0)
            s.destroy_fence(req.handle)
            resp = DeviceMessageT()
            inner = FenceDestroyResponseT()
            resp.payload = _pack_table(inner)
            return resp

        elif opcode == DeviceOpcode.OPCODE_FENCE_WAIT:
            req = FenceWaitRequestT.InitFromPackedBuf(pl, 0)
            status = s.fence_status(req.handle)
            if status == 0:
                resp = DeviceMessageT()
                inner = FenceWaitResponseT()
                inner.signalled = False
                resp.payload = _pack_table(inner)
                # Force signal on wait (mock behaviour)
                s.signal_fence(req.handle)
                return resp
            elif status == 1:
                resp = DeviceMessageT()
                inner = FenceWaitResponseT()
                inner.signalled = True
                resp.payload = _pack_table(inner)
                return resp
            else:
                raise ValueError(f"invalid fence handle {req.handle}")

        elif opcode == DeviceOpcode.OPCODE_FENCE_POLL:
            req = FencePollRequestT.InitFromPackedBuf(pl, 0)
            status = s.fence_status(req.handle)
            if status in (0, 2):
                raise ValueError(f"fence {req.handle} not ready")
            resp = DeviceMessageT()
            inner = FencePollResponseT()
            inner.signalled = (status == 1)
            resp.payload = _pack_table(inner)
            return resp

        elif opcode == DeviceOpcode.OPCODE_FENCE_STATUS:
            req = FenceStatusRequestT.InitFromPackedBuf(pl, 0)
            status = s.fence_status(req.handle)
            resp = DeviceMessageT()
            inner = FenceStatusResponseT()
            inner.status = status
            resp.payload = _pack_table(inner)
            return resp

        elif opcode == DeviceOpcode.OPCODE_SUBMIT:
            req = SubmitRequestT.InitFromPackedBuf(pl, 0)
            # Auto-signal fence on submit (mock behaviour)
            if req.fenceHandle:
                s.signal_fence(req.fenceHandle)
            resp = DeviceMessageT()
            inner = SubmitResponseT()
            resp.payload = _pack_table(inner)
            return resp

        elif opcode == DeviceOpcode.OPCODE_DEVICE_RESET:
            s.reset()
            resp = DeviceMessageT()
            inner = DeviceResetResponseT()
            resp.payload = _pack_table(inner)
            return resp

        elif opcode == DeviceOpcode.OPCODE_DEVICE_CAPS:
            resp = DeviceMessageT()
            inner = DeviceCapsResponseT()
            inner.deviceName = "CaduceusCore NPU (RTL Mock)"
            inner.transportName = "RTL"
            inner.maxBuffers = 4096
            inner.maxBufferSize = 1024 * 1024 * 1024
            inner.maxQueues = 8
            inner.maxCmdLists = 256
            inner.abiMajor = 1
            inner.abiMinor = 0
            resp.payload = _pack_table(inner)
            return resp

        else:
            raise ValueError(f"unknown opcode: {opcode}")

    def _error_response(
        self,
        status: int,
        message: str,
        request_id: int = 0,
        opcode: int = 0,
    ) -> bytearray:
        resp = DeviceMessageT()
        resp.header = MessageHeaderT()
        resp.header.magic = MAGIC
        resp.header.protocolVersion = PROTOCOL_VERSION
        resp.header.requestId = request_id
        resp.header.opcode = opcode
        resp.header.status = status

        err = ErrorResponseT()
        err.message = message
        resp.payload = _pack_table(err)

        return build_message(resp)


# ── Helper to pack FlatBuffers tables ───────────────────────────────────────


def _pack_table(table_t) -> bytes:
    import flatbuffers

    builder = flatbuffers.Builder(256)
    root = table_t.Pack(builder)
    builder.Finish(root)
    return bytes(builder.Output())


# ── Unix socket server ─────────────────────────────────────────────────────


class _RtlFrameHandler(socketserver.BaseRequestHandler):
    """Handle one Unix socket connection for the RTL mock endpoint."""

    def handle(self):
        handler = self.server.protocol_handler  # type: ignore[attr-defined]
        while True:
            try:
                wire = recv_framed(self.request)
            except (ConnectionError, BrokenPipeError):
                break
            except ValueError:
                break

            try:
                resp = handler.handle(wire)
                send_framed(self.request, resp)
            except (BrokenPipeError, OSError):
                break
            except Exception:
                break


class ThreadedRtlMockServer(socketserver.ThreadingUnixStreamServer):
    allow_reuse_address = True

    def __init__(self, sock_path: str, state: Optional[RtlMockState] = None):
        self.state = state or RtlMockState()
        self.protocol_handler = RtlProtocolHandler(self.state)
        try:
            os.unlink(sock_path)
        except FileNotFoundError:
            pass
        super().__init__(sock_path, _RtlFrameHandler)

    def server_close(self):
        super().server_close()
        try:
            os.unlink(self.server_address)
        except (FileNotFoundError, OSError):
            pass


def serve(sock_path: str = DEFAULT_SOCK_PATH) -> ThreadedRtlMockServer:
    """Start the RTL mock endpoint on a Unix socket."""
    server = ThreadedRtlMockServer(sock_path)
    return server


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="CaduceusCore RTL Protocol Mock Endpoint"
    )
    parser.add_argument(
        "--sock", default=DEFAULT_SOCK_PATH, help="Unix socket path"
    )
    args = parser.parse_args(argv)

    server = serve(sock_path=args.sock)
    print(f"RTL mock endpoint listening on {args.sock}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("RTL mock endpoint shut down", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
