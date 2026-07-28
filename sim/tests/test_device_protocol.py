"""Tests for the CaduceusCore Func Model device protocol (FlatBuffers)."""

import itertools
import os
import socket
import struct
import threading

import pytest

from caduceus_device_protocol.BufferAllocRequest import BufferAllocRequestT
from caduceus_device_protocol.BufferReadRequest import BufferReadRequestT
from caduceus_device_protocol.BufferWriteRequest import BufferWriteRequestT
from caduceus_device_protocol.DeviceCapsRequest import DeviceCapsRequestT
from caduceus_device_protocol.DeviceMessage import DeviceMessageT
from caduceus_device_protocol.DeviceOpcode import DeviceOpcode
from caduceus_device_protocol.DeviceStatus import DeviceStatus
from caduceus_device_protocol.FenceCreateRequest import FenceCreateRequestT
from caduceus_device_protocol.FenceDestroyRequest import FenceDestroyRequestT
from caduceus_device_protocol.FencePollRequest import FencePollRequestT
from caduceus_device_protocol.FenceStatusRequest import FenceStatusRequestT
from caduceus_device_protocol.FenceWaitRequest import FenceWaitRequestT
from caduceus_device_protocol.MessageHeader import MessageHeaderT
from caduceus_device_protocol.DeviceMessage import DeviceMessage

from sim.device_protocol import (
    MAGIC,
    PROTOCOL_VERSION,
    build_message,
    pack_table,
    parse_message,
    recv_framed,
    send_framed,
    unpack_table,
    validate_header,
)
from sim.device_server import FmDeviceServer, _ThreadedUnixFmServer


# ── Codec tests ────────────────────────────────────────────────────────────


def test_build_parse_roundtrip():
    msg = DeviceMessageT()
    msg.header = MessageHeaderT()
    msg.header.requestId = 7
    msg.header.opcode = DeviceOpcode.OPCODE_DEVICE_CAPS
    msg.payload = list(pack_table(DeviceCapsRequestT()))

    wire = build_message(msg)
    parsed, computed = parse_message(wire)

    validate_header(parsed)
    assert parsed.header.requestId == 7
    assert parsed.header.opcode == DeviceOpcode.OPCODE_DEVICE_CAPS
    assert parsed.header.status == DeviceStatus.STATUS_OK
    assert parsed.header.checksum == computed


def _header_offset(wire: bytearray) -> int:
    dm = DeviceMessage.GetRootAs(bytes(wire))
    return dm.Header()._tab.Pos


def test_bad_magic_detected():
    msg = DeviceMessageT()
    msg.header = MessageHeaderT()
    msg.header.requestId = 1
    msg.header.opcode = DeviceOpcode.OPCODE_DEVICE_CAPS
    wire = build_message(msg)
    hoff = _header_offset(wire)
    wire[hoff:hoff + 4] = struct.pack("<I", 0xDEADBEEF)
    parsed, _ = parse_message(wire)
    with pytest.raises(ValueError, match="bad magic"):
        validate_header(parsed)


def test_bad_version_detected():
    msg = DeviceMessageT()
    msg.header = MessageHeaderT()
    msg.header.requestId = 1
    msg.header.opcode = DeviceOpcode.OPCODE_DEVICE_CAPS
    wire = build_message(msg)
    hoff = _header_offset(wire)
    wire[hoff + 4:hoff + 8] = struct.pack("<I", 99)
    parsed, _ = parse_message(wire)
    with pytest.raises(ValueError, match="bad version"):
        validate_header(parsed)


def test_payload_length_mismatch_detected():
    msg = DeviceMessageT()
    msg.header = MessageHeaderT()
    msg.header.requestId = 1
    msg.header.opcode = DeviceOpcode.OPCODE_DEVICE_CAPS
    msg.payload = list(pack_table(DeviceCapsRequestT()))
    wire = build_message(msg)
    hoff = _header_offset(wire)
    wire[hoff + 20:hoff + 24] = struct.pack("<I", 123)  # wrong
    parsed, _ = parse_message(wire)
    with pytest.raises(ValueError, match="payload length mismatch"):
        validate_header(parsed)


# ── Server fixture ─────────────────────────────────────────────────────────


@pytest.fixture
def fm_server(tmp_path):
    sock_path = str(tmp_path / "caduceus_fm_test.sock")
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass

    srv = FmDeviceServer(sock_path=sock_path, use_spike=False)
    srv.start()
    ready = threading.Event()
    server = _ThreadedUnixFmServer(sock_path, srv, ready_event=ready)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ready.wait(timeout=2.0)

    yield sock_path

    server.shutdown()
    server.server_close()
    srv.stop()
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass


_request_id = itertools.count(1)


def _request(sock_path, opcode, inner_table):
    req = DeviceMessageT()
    req.header = MessageHeaderT()
    req.header.requestId = next(_request_id)
    req.header.opcode = opcode
    req.payload = list(pack_table(inner_table))
    wire = build_message(req)

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sock_path)
    try:
        send_framed(s, wire)
        return parse_message(recv_framed(s))
    finally:
        s.close()


# ── Server tests ───────────────────────────────────────────────────────────


def test_server_device_caps(fm_server):
    resp, cs = _request(fm_server, DeviceOpcode.OPCODE_DEVICE_CAPS,
                        DeviceCapsRequestT())
    assert resp.header.opcode == DeviceOpcode.OPCODE_DEVICE_CAPS
    assert resp.header.status == DeviceStatus.STATUS_OK
    assert resp.header.checksum == cs
    from caduceus_device_protocol.DeviceCapsResponse import DeviceCapsResponseT
    caps = unpack_table(DeviceCapsResponseT, resp.payload)
    assert caps.abiMajor == 1
    assert caps.deviceName.decode("utf-8") == "CaduceusCore NPU"


def test_server_buffer_alloc_read_write_free(fm_server):
    alloc_req = BufferAllocRequestT()
    alloc_req.size = 64
    resp, _ = _request(fm_server, DeviceOpcode.OPCODE_BUFFER_ALLOC, alloc_req)
    from caduceus_device_protocol.BufferAllocResponse import BufferAllocResponseT
    alloc = unpack_table(BufferAllocResponseT, resp.payload)
    assert alloc.size == 64
    handle = alloc.handle

    write_req = BufferWriteRequestT()
    write_req.handle = handle
    write_req.offset = 0
    write_req.data = list(b"hello caduceus")
    resp, _ = _request(fm_server, DeviceOpcode.OPCODE_BUFFER_WRITE, write_req)
    assert resp.header.status == DeviceStatus.STATUS_OK

    read_req = BufferReadRequestT()
    read_req.handle = handle
    read_req.offset = 0
    read_req.size = 14
    resp, _ = _request(fm_server, DeviceOpcode.OPCODE_BUFFER_READ, read_req)
    from caduceus_device_protocol.BufferReadResponse import BufferReadResponseT
    read = unpack_table(BufferReadResponseT, resp.payload)
    assert bytes(read.data) == b"hello caduceus"

    from caduceus_device_protocol.BufferFreeRequest import BufferFreeRequestT
    free_req = BufferFreeRequestT()
    free_req.handle = handle
    resp, _ = _request(fm_server, DeviceOpcode.OPCODE_BUFFER_FREE, free_req)
    assert resp.header.status == DeviceStatus.STATUS_OK


def test_server_fence_lifecycle(fm_server):
    resp, _ = _request(fm_server, DeviceOpcode.OPCODE_FENCE_CREATE,
                       FenceCreateRequestT())
    from caduceus_device_protocol.FenceCreateResponse import FenceCreateResponseT
    created = unpack_table(FenceCreateResponseT, resp.payload)
    handle = created.handle
    assert handle != 0

    from caduceus_device_protocol.FenceStatusRequest import FenceStatusRequestT
    status_req = FenceStatusRequestT()
    status_req.handle = handle
    resp, _ = _request(fm_server, DeviceOpcode.OPCODE_FENCE_STATUS, status_req)
    from caduceus_device_protocol.FenceStatusResponse import FenceStatusResponseT
    status = unpack_table(FenceStatusResponseT, resp.payload)
    assert status.status == 0  # not ready

    from caduceus_device_protocol.FencePollRequest import FencePollRequestT
    poll_req = FencePollRequestT()
    poll_req.handle = handle
    resp, _ = _request(fm_server, DeviceOpcode.OPCODE_FENCE_POLL, poll_req)
    from caduceus_device_protocol.FencePollResponse import FencePollResponseT
    poll = unpack_table(FencePollResponseT, resp.payload)
    assert poll.signalled is False
    assert resp.header.status == DeviceStatus.STATUS_NOT_READY

    from caduceus_device_protocol.FenceWaitRequest import FenceWaitRequestT
    wait_req = FenceWaitRequestT()
    wait_req.handle = handle
    wait_req.timeoutNs = 1_000_000  # 1 ms
    resp, _ = _request(fm_server, DeviceOpcode.OPCODE_FENCE_WAIT, wait_req)
    from caduceus_device_protocol.FenceWaitResponse import FenceWaitResponseT
    wait = unpack_table(FenceWaitResponseT, resp.payload)
    assert wait.signalled is False
    assert resp.header.status == DeviceStatus.STATUS_TIMEOUT

    from caduceus_device_protocol.FenceDestroyRequest import FenceDestroyRequestT
    destroy_req = FenceDestroyRequestT()
    destroy_req.handle = handle
    resp, _ = _request(fm_server, DeviceOpcode.OPCODE_FENCE_DESTROY, destroy_req)
    assert resp.header.status == DeviceStatus.STATUS_OK
