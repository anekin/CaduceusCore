"""CaduceusCore Func Model Device Protocol — Python codec helpers (FlatBuffers).

Defines serialization, checksum (CRC-32/IEEE), and validation for the
DeviceMessage protocol used between the C/C++ runtime client and the
Python Func Model server.
"""

from __future__ import annotations

import struct
import zlib
from typing import Tuple

import flatbuffers

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
from caduceus_device_protocol.DeviceMessage import DeviceMessage
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


# Protocol constants
MAGIC = 0x43414455  # 'CADU'
PROTOCOL_VERSION = 1

# Offset of checksum within the MessageHeader struct.
# Layout: magic(4) + protocol_version(4) + request_id(8) + opcode(4) +
#         payload_length(4) + status(4) + checksum(4) = 32 bytes.
_CHECKSUM_OFFSET_IN_HEADER = 28

# Status mapping helpers
_CAD_TO_DEVICE_STATUS = {
    0: DeviceStatus.STATUS_OK,
    1: DeviceStatus.STATUS_INVALID_MESSAGE,
    2: DeviceStatus.STATUS_INVALID_HANDLE,
    3: DeviceStatus.STATUS_INVALID_ARGUMENT,
    4: DeviceStatus.STATUS_TIMEOUT,
    5: DeviceStatus.STATUS_DEVICE_LOST,
    6: DeviceStatus.STATUS_OUT_OF_MEMORY,
    7: DeviceStatus.STATUS_NOT_READY,
    8: DeviceStatus.STATUS_BUSY,
    9: DeviceStatus.STATUS_UNKNOWN_OPCODE,
}


def cad_error_to_device_status(cad_err: int) -> int:
    """Map a cad_error_t value to a DeviceStatus value."""
    return _CAD_TO_DEVICE_STATUS.get(cad_err, DeviceStatus.STATUS_INVALID_MESSAGE)


def compute_checksum(wire: bytearray) -> int:
    """Compute CRC-32/IEEE over the given wire bytes."""
    return zlib.crc32(wire) & 0xFFFFFFFF


def _patch_checksum(wire: bytearray, checksum: int) -> None:
    """Patch the checksum field in-place in serialized DeviceMessage bytes."""
    msg = DeviceMessage.GetRootAs(wire)
    header = msg.Header()
    if header is None:
        raise ValueError("missing header")
    offset = header._tab.Pos + _CHECKSUM_OFFSET_IN_HEADER
    wire[offset:offset + 4] = struct.pack("<I", checksum)


def _header_from_message(msg: DeviceMessageT) -> MessageHeaderT:
    if msg.header is None:
        msg.header = MessageHeaderT()
    return msg.header


def build_message(msg: DeviceMessageT) -> bytearray:
    """Serialize a DeviceMessage with a valid checksum.

    Mutates msg.header.checksum to the computed value.
    """
    h = _header_from_message(msg)
    h.magic = MAGIC
    h.protocolVersion = PROTOCOL_VERSION
    h.payloadLength = len(msg.payload) if msg.payload is not None else 0
    h.checksum = 0

    builder = flatbuffers.Builder(256)
    root = msg.Pack(builder)
    builder.Finish(root)
    wire = builder.Output()

    h.checksum = compute_checksum(wire)
    _patch_checksum(wire, h.checksum)
    return wire


def parse_message(wire: bytes) -> Tuple[DeviceMessageT, int]:
    """Parse and validate a DeviceMessage from wire bytes.

    Computes checksum over the raw wire bytes with the header checksum
    field zeroed in-place, so C++ and Python clients agree on CRC-32.

    Returns:
        (message, computed_checksum)
    """
    # Compute checksum over the raw wire bytes (mutable copy) with the
    # header checksum field zeroed at header._tab.Pos + 28.
    wire_buf = bytearray(wire)
    msg_view = DeviceMessage.GetRootAs(wire_buf)
    header_view = msg_view.Header()
    if header_view is None:
        raise ValueError("missing header")
    offset = header_view._tab.Pos + _CHECKSUM_OFFSET_IN_HEADER
    claimed = struct.unpack("<I", wire_buf[offset:offset + 4])[0]
    wire_buf[offset:offset + 4] = b'\x00\x00\x00\x00'
    computed = compute_checksum(wire_buf)

    # Unpack the original wire for the caller.
    msg = DeviceMessageT.InitFromPackedBuf(bytes(wire), 0)
    if msg.payload is not None:
        msg.payload = bytes(msg.payload)
    if msg.header is None:
        raise ValueError("missing header")
    msg.header.checksum = claimed

    return msg, computed


def validate_header(msg: DeviceMessageT) -> None:
    """Validate a parsed message header (raises ValueError on failure)."""
    h = msg.header
    if h is None:
        raise ValueError("missing header")
    if h.magic != MAGIC:
        raise ValueError(f"bad magic: 0x{h.magic:08x}")
    if h.protocolVersion != PROTOCOL_VERSION:
        raise ValueError(f"bad version: {h.protocolVersion}")
    actual_len = len(msg.payload) if msg.payload is not None else 0
    if h.payloadLength != actual_len:
        raise ValueError(
            f"payload length mismatch: header={h.payloadLength} actual={actual_len}"
        )


# ── Helpers for packing object-API tables ──────────────────────────────────


def pack_table(table_t) -> bytes:
    """Pack an object-API table instance into a bytes payload."""
    builder = flatbuffers.Builder(256)
    root = table_t.Pack(builder)
    builder.Finish(root)
    return bytes(builder.Output())


def unpack_table(table_t_cls, payload: bytes):
    """Unpack a bytes payload into an object-API table instance."""
    return table_t_cls.InitFromPackedBuf(bytes(payload), 0)


# ── Helpers for framing over a stream ──────────────────────────────────────


def send_framed(sock, wire: bytearray) -> None:
    """Send a length-prefixed message over a stream socket."""
    frame = struct.pack(">I", len(wire)) + bytes(wire)
    sock.sendall(frame)


def recv_exact(sock, n: int) -> bytes:
    """Receive exactly n bytes from a stream socket."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed while receiving")
        buf += chunk
    return buf


def recv_framed(sock) -> bytearray:
    """Receive a length-prefixed message from a stream socket."""
    length_bytes = recv_exact(sock, 4)
    length = struct.unpack(">I", length_bytes)[0]
    if length > 16 * 1024 * 1024:
        raise ValueError(f"message too large: {length}")
    return bytearray(recv_exact(sock, length))
