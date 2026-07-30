"""Tests for per-connection request ID tracking in device_server.py.

Verifies: multi-connection independence, close-reopen cycle, reset-after-submit,
and out-of-order rejection (I-007, I-016).
"""

import os
import socket
import threading

import pytest

from caduceus_device_protocol.DeviceCapsRequest import DeviceCapsRequestT
from caduceus_device_protocol.DeviceMessage import DeviceMessageT
from caduceus_device_protocol.DeviceOpcode import DeviceOpcode
from caduceus_device_protocol.DeviceResetRequest import DeviceResetRequestT
from caduceus_device_protocol.DeviceStatus import DeviceStatus
from caduceus_device_protocol.MessageHeader import MessageHeaderT

from sim.device_protocol import (
    build_message,
    pack_table,
    parse_message,
    recv_framed,
    send_framed,
)
from sim.device_server import FmDeviceServer, _ThreadedUnixFmServer


# ── Shared server fixture ─────────────────────────────────────────────────


@pytest.fixture
def fm_server(tmp_path):
    sock_path = str(tmp_path / "caduceus_fm_perconn.sock")
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


# ── Helpers ───────────────────────────────────────────────────────────────


def _connect(sock_path):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sock_path)
    return s


def _send_caps(sock, request_id):
    req = DeviceMessageT()
    req.header = MessageHeaderT()
    req.header.requestId = request_id
    req.header.opcode = DeviceOpcode.OPCODE_DEVICE_CAPS
    req.payload = list(pack_table(DeviceCapsRequestT()))
    send_framed(sock, build_message(req))
    resp, _ = parse_message(recv_framed(sock))
    return resp


def _send_req(sock, request_id, opcode, inner_table=None):
    req = DeviceMessageT()
    req.header = MessageHeaderT()
    req.header.requestId = request_id
    req.header.opcode = opcode
    inner = inner_table if inner_table is not None else DeviceCapsRequestT()
    req.payload = list(pack_table(inner))
    send_framed(sock, build_message(req))
    resp, _ = parse_message(recv_framed(sock))
    return resp


# ── Tests ─────────────────────────────────────────────────────────────────


class TestPerConnectionRequestId:
    """Per-connection request ID tracking."""

    def test_close_reopen_same_id_allowed(self, fm_server):
        """Closing and reopening a connection with the same request ID works."""
        sock_path = fm_server

        # Connection 1: send request ID 1, close
        s1 = _connect(sock_path)
        resp = _send_caps(s1, 1)
        assert resp.header.status == DeviceStatus.STATUS_OK
        s1.close()

        # Connection 2: send request ID 1 again (fresh connection, must work)
        s2 = _connect(sock_path)
        resp = _send_caps(s2, 1)
        assert resp.header.status == DeviceStatus.STATUS_OK
        s2.close()

    def test_reset_after_request_allows_new_sequence(self, fm_server):
        """Device reset on a connection resets the request ID counter."""
        sock_path = fm_server
        s = _connect(sock_path)

        resp = _send_caps(s, 10)
        assert resp.header.status == DeviceStatus.STATUS_OK

        resp = _send_req(s, 11, DeviceOpcode.OPCODE_DEVICE_RESET,
                         DeviceResetRequestT())
        assert resp.header.status == DeviceStatus.STATUS_OK

        # After reset, request ID 1 must work again (counter reset to 0).
        resp = _send_caps(s, 1)
        assert resp.header.status == DeviceStatus.STATUS_OK

        s.close()

    def test_two_connections_independent_ids(self, fm_server):
        """Two concurrent connections may use the same request IDs."""
        sock_path = fm_server
        s1 = _connect(sock_path)
        s2 = _connect(sock_path)

        resp1 = _send_caps(s1, 1)
        resp2 = _send_caps(s2, 1)
        assert resp1.header.status == DeviceStatus.STATUS_OK
        assert resp2.header.status == DeviceStatus.STATUS_OK

        resp1 = _send_caps(s1, 2)
        resp2 = _send_caps(s2, 2)
        assert resp1.header.status == DeviceStatus.STATUS_OK
        assert resp2.header.status == DeviceStatus.STATUS_OK

        s1.close()
        s2.close()

    def test_out_of_order_rejected_per_connection(self, fm_server):
        """Out-of-order request IDs are still rejected on the same connection."""
        sock_path = fm_server
        s = _connect(sock_path)

        resp = _send_caps(s, 5)
        assert resp.header.status == DeviceStatus.STATUS_OK

        # request ID 3 is <= 5 — must be rejected.
        resp = _send_caps(s, 3)
        assert resp.header.status == DeviceStatus.STATUS_INVALID_MESSAGE

        s.close()

    def test_sequential_three_connections(self, fm_server):
        """Three sequential connections all work with the same request IDs."""
        sock_path = fm_server

        for _ in range(3):
            s = _connect(sock_path)
            resp = _send_caps(s, 1)
            assert resp.header.status == DeviceStatus.STATUS_OK
            resp = _send_caps(s, 2)
            assert resp.header.status == DeviceStatus.STATUS_OK
            s.close()

    def test_many_requests_then_reset_then_more(self, fm_server):
        """Many requests, then reset, then more requests on same connection."""
        sock_path = fm_server
        s = _connect(sock_path)

        for rid in range(1, 11):
            resp = _send_caps(s, rid)
            assert resp.header.status == DeviceStatus.STATUS_OK

        resp = _send_req(s, 11, DeviceOpcode.OPCODE_DEVICE_RESET,
                         DeviceResetRequestT())
        assert resp.header.status == DeviceStatus.STATUS_OK

        for rid in range(1, 6):
            resp = _send_caps(s, rid)
            assert resp.header.status == DeviceStatus.STATUS_OK

        s.close()
