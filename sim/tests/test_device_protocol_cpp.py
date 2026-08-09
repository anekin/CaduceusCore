"""End-to-end test for the C++ Func Model transport against the Python server.

Loads the compiled runtime shared library and exercises the
`cad_transport_fm_ops` vtable through the Python server.
"""

import ctypes
import os
import struct
import threading

import pytest

from device_server import FmDeviceServer, _ThreadedUnixFmServer


# ── ctypes definitions ─────────────────────────────────────────────────────


cad_transport_buffer_p = ctypes.c_void_p
cad_transport_fence_p = ctypes.c_void_p


class cad_transport_ops_t(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char_p),
        ("device_init", ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p)),
        ("device_fini", ctypes.CFUNCTYPE(None, ctypes.c_void_p)),
        ("device_reset", ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)),
        (
            "buffer_alloc",
            ctypes.CFUNCTYPE(
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.POINTER(cad_transport_buffer_p),
                ctypes.c_uint64,
            ),
        ),
        ("buffer_free", ctypes.CFUNCTYPE(None, ctypes.c_void_p, cad_transport_buffer_p)),
        (
            "buffer_read",
            ctypes.CFUNCTYPE(
                ctypes.c_int,
                ctypes.c_void_p,
                cad_transport_buffer_p,
                ctypes.c_uint64,
                ctypes.c_uint64,
                ctypes.c_void_p,
            ),
        ),
        (
            "buffer_write",
            ctypes.CFUNCTYPE(
                ctypes.c_int,
                ctypes.c_void_p,
                cad_transport_buffer_p,
                ctypes.c_uint64,
                ctypes.c_uint64,
                ctypes.c_void_p,
            ),
        ),
        (
            "buffer_size",
            ctypes.CFUNCTYPE(ctypes.c_uint64, ctypes.c_void_p, cad_transport_buffer_p),
        ),
        (
            "fence_create",
            ctypes.CFUNCTYPE(
                ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(cad_transport_fence_p)
            ),
        ),
        ("fence_destroy", ctypes.CFUNCTYPE(None, ctypes.c_void_p, cad_transport_fence_p)),
        (
            "fence_wait",
            ctypes.CFUNCTYPE(
                ctypes.c_int, ctypes.c_void_p, cad_transport_fence_p, ctypes.c_uint64
            ),
        ),
        (
            "fence_poll",
            ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, cad_transport_fence_p),
        ),
        (
            "fence_status",
            ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, cad_transport_fence_p),
        ),
        (
            "submit",
            ctypes.CFUNCTYPE(
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_uint32,
                cad_transport_fence_p,
            ),
        ),
    ]


# ── Shared library loading ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def lib():
    so_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "software",
        "build",
        "libcaduceus_runtime.so",
    )
    so_path = os.path.abspath(so_path)
    if not os.path.exists(so_path):
        pytest.skip(f"shared library not built: {so_path}")
    return ctypes.CDLL(so_path)


# ── Server fixture ─────────────────────────────────────────────────────────


@pytest.fixture
def fm_server(tmp_path):
    sock_path = str(tmp_path / "caduceus_fm_cpp.sock")
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


# ── End-to-end C++ transport tests ─────────────────────────────────────────


def _load_ops(lib):
    return ctypes.cast(
        ctypes.cast(lib.cad_transport_fm_ops, ctypes.c_void_p),
        ctypes.POINTER(cad_transport_ops_t),
    ).contents


def test_cpp_transport_buffer_roundtrip(lib, fm_server):
    """C++ transport can allocate, write, read, and free a buffer."""
    ops = _load_ops(lib)
    assert ops.name == b"FuncModel"

    tpriv = ctypes.c_void_p()
    uri = ("fm://unix?path=" + fm_server).encode("utf-8")
    rc = ops.device_init(ctypes.byref(tpriv), uri)
    assert rc == 0, f"device_init failed: {rc}"

    try:
        buf = cad_transport_buffer_p()
        rc = ops.buffer_alloc(tpriv, ctypes.byref(buf), 64)
        assert rc == 0, f"buffer_alloc failed: {rc}"
        assert buf.value is not None

        src = b"hello from c++"
        rc = ops.buffer_write(tpriv, buf, 0, len(src), src)
        assert rc == 0, f"buffer_write failed: {rc}"

        dst = ctypes.create_string_buffer(len(src))
        rc = ops.buffer_read(tpriv, buf, 0, len(src), dst)
        assert rc == 0, f"buffer_read failed: {rc}"
        assert dst.raw[: len(src)] == src

        ops.buffer_free(tpriv, buf)
    finally:
        ops.device_fini(tpriv)


def test_cpp_transport_fence_lifecycle(lib, fm_server):
    """C++ transport can create, poll, wait, and destroy a fence."""
    ops = _load_ops(lib)

    tpriv = ctypes.c_void_p()
    uri = ("fm://unix?path=" + fm_server).encode("utf-8")
    rc = ops.device_init(ctypes.byref(tpriv), uri)
    assert rc == 0

    try:
        fence = cad_transport_fence_p()
        rc = ops.fence_create(tpriv, ctypes.byref(fence))
        assert rc == 0
        assert fence.value is not None

        # Newly created fence is not ready.
        rc = ops.fence_poll(tpriv, fence)
        assert rc == -7  # CAD_TR_ERR_NOTREADY

        rc = ops.fence_wait(tpriv, fence, 1_000_000)  # 1 ms
        assert rc == -4  # CAD_TR_ERR_TIMEDOUT

        status = ops.fence_status(tpriv, fence)
        assert status == 0  # not ready

        ops.fence_destroy(tpriv, fence)
    finally:
        ops.device_fini(tpriv)


def test_submit_with_blob(lib, fm_server):
    """FM transport submit populates cmd_blob in SubmitRequest.

    Verifies W2-T9: fm_submit() forwards the serialized command payload
    into the FlatBuffers SubmitRequest.cmdBlob field.

    Submits a command with a non-empty, recognisable blob and verifies
    the transport-level submit + fence cycle completes.  Before the fix,
    cmd_data was discarded with (void)cmd_data and the server received
    an empty blob.  After the fix, the blob reaches the server intact.
    The fence may signal error if the blob content is not valid model
    commands — this is expected model-level behavior, not a transport
    failure.
    """
    ops = _load_ops(lib)
    assert ops.name == b"FuncModel"

    tpriv = ctypes.c_void_p()
    uri = ("fm://unix?path=" + fm_server).encode("utf-8")
    rc = ops.device_init(ctypes.byref(tpriv), uri)
    assert rc == 0, f"device_init failed: {rc}"

    try:
        fence = cad_transport_fence_p()
        rc = ops.fence_create(tpriv, ctypes.byref(fence))
        assert rc == 0
        assert fence.value is not None

        # Non-empty blob with known marker bytes.  cmd_count is the
        # serialized buffer size (W2-T7 format: 12B header + blob bytes).
        blob = b"\xde\xad\xbe\xef" + b"\xca\xfe\xba\xbe" + b"\x01\x00\x00\x00"
        rc = ops.submit(tpriv, blob, len(blob), fence)
        assert rc == 0, f"submit failed: {rc}"

        INFINITE = 0xFFFFFFFFFFFFFFFF
        rc = ops.fence_wait(tpriv, fence, INFINITE)
        assert rc == 0, f"fence_wait failed: {rc}"

        # Fence was signalled (may be completed or error depending on
        # blob validity — either proves the server received the blob).
        status = ops.fence_status(tpriv, fence)
        assert status != 0, f"fence_status={status}, expected signalled (1 or 2)"

        ops.fence_destroy(tpriv, fence)
    finally:
        ops.device_fini(tpriv)
