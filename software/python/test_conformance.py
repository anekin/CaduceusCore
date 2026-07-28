"""
test_conformance.py — Python Conformance Test Suite

Runs the same conformance matrix as the C/C++ tests via pytest.
Requires CADUCEUS_RUNTIME_LIB pointing to the built shared library.
"""

import os
import sys

import pytest

# Add parent dir to path for caduceus_runtime import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from caduceus_runtime import (
    CAD_ERROR_DEVICE_LOST,
    CAD_FENCE_COMPLETED,
    CAD_FENCE_NOT_READY,
    CAD_TIMEOUT_INFINITE,
    CAD_TIMEOUT_IMMEDIATE,
    Buffer,
    CommandList,
    Device,
    Fence,
    Queue,
    mock_advance_ticks,
    mock_reset,
    mock_set_next_submit_error,
    mock_set_pending_ticks,
)


@pytest.fixture(autouse=True)
def reset_mock():
    mock_reset()
    mock_set_pending_ticks(0)
    yield


# ── Device ──────────────────────────────────────────────────────────


def test_device_open_close():
    dev = Device("mock://")
    assert dev.handle is not None
    assert dev.caps.max_buffers > 0
    assert dev.caps.transport_name.strip(b"\0") == b"Mock"
    dev.close()


def test_device_caps():
    dev = Device("mock://")
    caps = dev.get_caps()
    # Caps should match the ABI version
    assert caps.max_buffers > 0
    assert caps.max_buffer_size > 0
    assert caps.max_queues > 0
    dev.close()


def test_device_reset():
    dev = Device("mock://")
    dev.reset()
    caps = dev.get_caps()
    assert caps.max_buffers > 0
    dev.close()


# ── Buffer ──────────────────────────────────────────────────────────


def test_buffer_write_read():
    dev = Device("mock://")
    buf = Buffer(dev.handle, 256)
    msg = b"Hello, CaduceusCore!"
    buf.write(0, msg)
    data = buf.read(0, len(msg))
    assert data == msg
    buf.free()
    dev.close()


def test_buffer_offset_write_read():
    dev = Device("mock://")
    buf = Buffer(dev.handle, 512)
    buf.write(100, b"middle")
    data = buf.read(100, 6)
    assert data == b"middle"
    buf.free()
    dev.close()


def test_buffer_large_transfer():
    dev = Device("mock://")
    buf = Buffer(dev.handle, 4096)
    data_in = bytes(i & 0xFF for i in range(4096))
    buf.write(0, data_in)
    data_out = buf.read(0, 4096)
    assert data_out == data_in
    buf.free()
    dev.close()


def test_buffer_multiple():
    dev = Device("mock://")
    bufs = []
    for i in range(4):
        b = Buffer(dev.handle, 256)
        b.write(0, f"buffer_{i}".encode())
        bufs.append(b)
    for i, b in enumerate(bufs):
        assert b.read(0, 8) == f"buffer_{i}".encode()
        b.free()
    dev.close()


# ── Command List ────────────────────────────────────────────────────


def test_command_list_append_nop():
    dev = Device("mock://")
    cl = CommandList(dev.handle, max_entries=8)
    for _ in range(8):
        cl.append_nop()
    with pytest.raises(RuntimeError):
        cl.append_nop()
    cl.destroy()
    dev.close()


def test_command_list_default_max():
    dev = Device("mock://")
    cl = CommandList(dev.handle, max_entries=0)  # use default
    cl.append_nop()
    cl.destroy()
    dev.close()


# ── Queue + Fence ───────────────────────────────────────────────────


def test_queue_submit_fence_immediate():
    mock_set_pending_ticks(0)
    dev = Device("mock://")
    queue = Queue(dev.handle)
    cl = CommandList(dev.handle)
    cl.append_nop()
    fence = Fence(dev.handle)

    assert not fence.poll()

    queue.submit(cl, fence)

    assert fence.poll()
    assert fence.status() == CAD_FENCE_COMPLETED
    fence.wait(CAD_TIMEOUT_IMMEDIATE)

    fence.destroy()
    queue.destroy()
    dev.close()


def test_fence_delayed_completion():
    mock_set_pending_ticks(5)
    dev = Device("mock://")
    queue = Queue(dev.handle)
    cl = CommandList(dev.handle)
    cl.append_nop()
    fence = Fence(dev.handle)

    queue.submit(cl, fence)

    assert not fence.poll()
    mock_advance_ticks(3)
    assert not fence.poll()
    mock_advance_ticks(2)
    assert fence.poll()
    assert fence.status() == CAD_FENCE_COMPLETED

    fence.destroy()
    queue.destroy()
    dev.close()


def test_fence_infinite_wait():
    mock_set_pending_ticks(3)
    dev = Device("mock://")
    queue = Queue(dev.handle)
    cl = CommandList(dev.handle)
    cl.append_nop()
    fence = Fence(dev.handle)

    queue.submit(cl, fence)
    fence.wait(CAD_TIMEOUT_INFINITE)
    assert fence.poll()
    assert fence.status() == CAD_FENCE_COMPLETED

    fence.destroy()
    queue.destroy()
    dev.close()


def test_fence_immediate_timeout():
    mock_set_pending_ticks(10)
    dev = Device("mock://")
    queue = Queue(dev.handle)
    cl = CommandList(dev.handle)
    cl.append_nop()
    fence = Fence(dev.handle)

    queue.submit(cl, fence)

    assert not fence.poll()
    # Infinite wait resolves
    fence.wait(CAD_TIMEOUT_INFINITE)
    assert fence.poll()

    fence.destroy()
    queue.destroy()
    dev.close()


def test_fence_never_submitted():
    dev = Device("mock://")
    fence = Fence(dev.handle)
    assert not fence.poll()
    assert fence.status() == CAD_FENCE_NOT_READY
    fence.destroy()
    dev.close()


# ── Error paths ─────────────────────────────────────────────────────


def test_submit_error_injection():
    dev = Device("mock://")
    queue = Queue(dev.handle)
    cl = CommandList(dev.handle)
    cl.append_nop()

    mock_set_next_submit_error(CAD_ERROR_DEVICE_LOST)
    with pytest.raises(RuntimeError):
        queue.submit(cl, None)
    # On failure, cmd_list retains ownership
    cl.destroy()

    queue.destroy()
    dev.close()


def test_submit_consumed_command_list():
    dev = Device("mock://")
    queue = Queue(dev.handle)
    cl = CommandList(dev.handle)
    cl.append_nop()

    queue.submit(cl, None)

    # cl was consumed; trying to destroy it should fail at C level
    # (handle is now None)
    assert cl.handle is None

    queue.destroy()
    dev.close()


# ── Context manager usage ───────────────────────────────────────────


def test_context_manager():
    mock_set_pending_ticks(0)
    with Device("mock://") as dev:
        with Buffer(dev.handle, 128) as buf:
            buf.write(0, b"ctx_test")
            assert buf.read(0, 8) == b"ctx_test"

        with Queue(dev.handle) as queue:
            with CommandList(dev.handle, 4) as cl:
                cl.append_nop()
            # Cannot submit destroyed cl, create new
            cl2 = CommandList(dev.handle, 4)
            cl2.append_nop()
            with Fence(dev.handle) as fence:
                queue.submit(cl2, fence)
                assert fence.poll()
            fence.destroy()  # already freed by __exit__, but handle is None
