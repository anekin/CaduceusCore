"""
CaduceusCore Python Runtime Bindings (ctypes)

Minimal ctypes wrapper around the C ABI (libcaduceus_runtime.so).
Provides Pythonic Device, Buffer, CommandList, Queue, and Fence classes.
"""

import ctypes
import os
from ctypes import (
    POINTER,
    Structure,
    c_char,
    c_uint32,
    c_uint64,
    c_void_p,
    byref,
    pointer,
)


class LibRuntime:
    """Load the shared runtime library.

    Search order:
      1. CADUCEUS_RUNTIME_LIB environment variable
      2. Install prefix: <module>/../../lib/libcaduceus_runtime.so
      3. Standard system paths via ctypes.util.find_library
      4. Development fallback: build/software/libcaduceus_runtime.so
    """

    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            lib_path = os.environ.get("CADUCEUS_RUNTIME_LIB")
            if lib_path is None:
                lib_path = cls._find_lib()
            cls._instance = ctypes.CDLL(lib_path)
        return cls._instance

    @classmethod
    def _find_lib(cls):
        """Search for libcaduceus_runtime.so in standard locations."""
        import ctypes.util

        # pip-installed layout: site-packages/caduceus_runtime.py
        #   → ../../lib/libcaduceus_runtime.so
        module_dir = os.path.dirname(os.path.abspath(__file__))
        prefix_lib = os.path.join(module_dir, "..", "..", "lib",
                                  "libcaduceus_runtime.so")
        if os.path.isfile(prefix_lib):
            return prefix_lib

        # Standard system search
        sys_lib = ctypes.util.find_library("caduceus_runtime")
        if sys_lib:
            return sys_lib

        # Development fallback
        return "build/software/libcaduceus_runtime.so"


# ── Constants ───────────────────────────────────────────────────────

CAD_ABI_MAJOR = 1
CAD_ABI_MINOR = 0
CAD_TIMEOUT_IMMEDIATE = 0
CAD_TIMEOUT_INFINITE = 0xFFFFFFFFFFFFFFFF

# Error codes
CAD_SUCCESS = 0
CAD_ERROR_INCOMPATIBLE_ABI = 1
CAD_ERROR_INVALID_HANDLE = 2
CAD_ERROR_INVALID_ARGUMENT = 3
CAD_ERROR_TIMEOUT = 4
CAD_ERROR_DEVICE_LOST = 5
CAD_ERROR_OUT_OF_MEMORY = 6
CAD_ERROR_NOT_READY = 7
CAD_ERROR_DEVICE_BUSY = 8
CAD_ERROR_UNSUPPORTED = 9

# Fence status
CAD_FENCE_NOT_READY = 0
CAD_FENCE_COMPLETED = 1
CAD_FENCE_ERROR = 2

_ERROR_NAMES = {
    0: "SUCCESS",
    1: "INCOMPATIBLE_ABI",
    2: "INVALID_HANDLE",
    3: "INVALID_ARGUMENT",
    4: "TIMEOUT",
    5: "DEVICE_LOST",
    6: "OUT_OF_MEMORY",
    7: "NOT_READY",
    8: "DEVICE_BUSY",
    9: "UNSUPPORTED",
}


def _check(err):
    if err != CAD_SUCCESS:
        name = _ERROR_NAMES.get(err, f"UNKNOWN({err})")
        raise RuntimeError(f"Caduceus error: {name} ({err})")


# ── C struct definitions ────────────────────────────────────────────

class CadDeviceOpenInfo(Structure):
    _fields_ = [
        ("struct_size", c_uint32),
        ("abi_major", c_uint32),
        ("abi_minor", c_uint32),
        ("uri", ctypes.c_char_p),
    ]


class CadDeviceCaps(Structure):
    _fields_ = [
        ("struct_size", c_uint32),
        ("abi_major", c_uint32),
        ("abi_minor", c_uint32),
        ("max_buffers", c_uint32),
        ("max_buffer_size", c_uint64),
        ("max_queues", c_uint32),
        ("max_command_lists", c_uint32),
        ("max_command_list_entries", c_uint32),
        ("device_name", c_char * 64),
        ("transport_name", c_char * 32),
    ]


class CadBufferCreateInfo(Structure):
    _fields_ = [
        ("struct_size", c_uint32),
        ("size", c_uint64),
        ("flags", c_uint32),
    ]


class CadCommandListCreateInfo(Structure):
    _fields_ = [
        ("struct_size", c_uint32),
        ("max_entries", c_uint32),
        ("flags", c_uint32),
    ]


class CadQueueCreateInfo(Structure):
    _fields_ = [
        ("struct_size", c_uint32),
        ("flags", c_uint32),
    ]


class CadFenceCreateInfo(Structure):
    _fields_ = [
        ("struct_size", c_uint32),
        ("flags", c_uint32),
    ]


# Opaque handle types
CadDevice = c_void_p
CadBuffer = c_void_p
CadQueue = c_void_p
CadCommandList = c_void_p
CadFence = c_void_p

# ── Function prototypes ─────────────────────────────────────────────


def _setup_prototypes(lib):
    lib.cadDeviceOpen.argtypes = [
        POINTER(CadDeviceOpenInfo),
        POINTER(CadDevice),
        POINTER(CadDeviceCaps),
    ]
    lib.cadDeviceOpen.restype = c_uint32

    lib.cadDeviceClose.argtypes = [CadDevice]
    lib.cadDeviceClose.restype = c_uint32

    lib.cadDeviceGetCaps.argtypes = [CadDevice, POINTER(CadDeviceCaps)]
    lib.cadDeviceGetCaps.restype = c_uint32

    lib.cadDeviceReset.argtypes = [CadDevice]
    lib.cadDeviceReset.restype = c_uint32

    lib.cadBufferAllocate.argtypes = [
        CadDevice,
        POINTER(CadBufferCreateInfo),
        POINTER(CadBuffer),
    ]
    lib.cadBufferAllocate.restype = c_uint32

    lib.cadBufferFree.argtypes = [CadBuffer]
    lib.cadBufferFree.restype = c_uint32

    lib.cadBufferRead.argtypes = [CadBuffer, c_uint64, c_uint64, c_void_p]
    lib.cadBufferRead.restype = c_uint32

    lib.cadBufferWrite.argtypes = [CadBuffer, c_uint64, c_uint64, c_void_p]
    lib.cadBufferWrite.restype = c_uint32

    lib.cadCommandListCreate.argtypes = [
        CadDevice,
        POINTER(CadCommandListCreateInfo),
        POINTER(CadCommandList),
    ]
    lib.cadCommandListCreate.restype = c_uint32

    lib.cadCommandListDestroy.argtypes = [CadCommandList]
    lib.cadCommandListDestroy.restype = c_uint32

    lib.cadCommandListAppendNop.argtypes = [CadCommandList]
    lib.cadCommandListAppendNop.restype = c_uint32

    lib.cadQueueCreate.argtypes = [
        CadDevice,
        POINTER(CadQueueCreateInfo),
        POINTER(CadQueue),
    ]
    lib.cadQueueCreate.restype = c_uint32

    lib.cadQueueDestroy.argtypes = [CadQueue]
    lib.cadQueueDestroy.restype = c_uint32

    lib.cadQueueSubmit.argtypes = [CadQueue, CadCommandList, CadFence]
    lib.cadQueueSubmit.restype = c_uint32

    lib.cadFenceCreate.argtypes = [
        CadDevice,
        POINTER(CadFenceCreateInfo),
        POINTER(CadFence),
    ]
    lib.cadFenceCreate.restype = c_uint32

    lib.cadFenceDestroy.argtypes = [CadFence]
    lib.cadFenceDestroy.restype = c_uint32

    lib.cadFenceWait.argtypes = [CadFence, c_uint64]
    lib.cadFenceWait.restype = c_uint32

    lib.cadFencePoll.argtypes = [CadFence]
    lib.cadFencePoll.restype = c_uint32

    lib.cadFenceGetStatus.argtypes = [CadFence, POINTER(c_uint32)]
    lib.cadFenceGetStatus.restype = c_uint32

    lib.cadErrorString.argtypes = [c_uint32]
    lib.cadErrorString.restype = ctypes.c_char_p

    # Mock transport control
    lib.cad_mock_set_pending_ticks.argtypes = [ctypes.c_int]
    lib.cad_mock_set_pending_ticks.restype = None
    lib.cad_mock_set_next_submit_error.argtypes = [ctypes.c_int]
    lib.cad_mock_set_next_submit_error.restype = None
    lib.cad_mock_advance_ticks.argtypes = [ctypes.c_int]
    lib.cad_mock_advance_ticks.restype = None
    lib.cad_mock_reset.argtypes = []
    lib.cad_mock_reset.restype = None


_setup_prototypes(LibRuntime.get())

_lib = LibRuntime.get()


# ── Pythonic wrappers ────────────────────────────────────────────────


class Device:
    def __init__(self, uri="mock://"):
        oi = CadDeviceOpenInfo()
        oi.struct_size = ctypes.sizeof(CadDeviceOpenInfo)
        oi.abi_major = CAD_ABI_MAJOR
        oi.abi_minor = CAD_ABI_MINOR
        oi.uri = uri.encode() if isinstance(uri, str) else uri
        caps = CadDeviceCaps()
        caps.struct_size = ctypes.sizeof(CadDeviceCaps)
        dev = CadDevice()
        _check(_lib.cadDeviceOpen(byref(oi), byref(dev), byref(caps)))
        self._handle = dev
        self._caps = caps

    def close(self):
        if self._handle:
            _check(_lib.cadDeviceClose(self._handle))
            self._handle = None

    @property
    def handle(self):
        return self._handle

    @property
    def caps(self):
        return self._caps

    def get_caps(self):
        caps = CadDeviceCaps()
        caps.struct_size = ctypes.sizeof(CadDeviceCaps)
        _check(_lib.cadDeviceGetCaps(self._handle, byref(caps)))
        return caps

    def reset(self):
        _check(_lib.cadDeviceReset(self._handle))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Buffer:
    def __init__(self, device, size):
        bi = CadBufferCreateInfo()
        bi.struct_size = ctypes.sizeof(CadBufferCreateInfo)
        bi.size = size
        bi.flags = 0
        buf = CadBuffer()
        _check(_lib.cadBufferAllocate(device, byref(bi), byref(buf)))
        self._handle = buf
        self._device = device
        self._size = size

    def free(self):
        if self._handle:
            _check(_lib.cadBufferFree(self._handle))
            self._handle = None

    @property
    def handle(self):
        return self._handle

    @property
    def size(self):
        return self._size

    def read(self, offset, size):
        data = (ctypes.c_uint8 * size)()
        _check(
            _lib.cadBufferRead(self._handle, offset, size, ctypes.cast(data, c_void_p))
        )
        return bytes(data)

    def write(self, offset, data):
        if isinstance(data, str):
            data = data.encode()
        buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        _check(
            _lib.cadBufferWrite(
                self._handle, offset, len(data), ctypes.cast(buf, c_void_p)
            )
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.free()


class CommandList:
    def __init__(self, device, max_entries=0):
        ci = CadCommandListCreateInfo()
        ci.struct_size = ctypes.sizeof(CadCommandListCreateInfo)
        ci.max_entries = max_entries
        ci.flags = 0
        cl = CadCommandList()
        _check(_lib.cadCommandListCreate(device, byref(ci), byref(cl)))
        self._handle = cl

    def append_nop(self):
        _check(_lib.cadCommandListAppendNop(self._handle))

    def destroy(self):
        if self._handle:
            _check(_lib.cadCommandListDestroy(self._handle))
            self._handle = None

    @property
    def handle(self):
        return self._handle

    def release(self):
        h = self._handle
        self._handle = None
        return h

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self._handle:
            self.destroy()


class Queue:
    def __init__(self, device):
        qi = CadQueueCreateInfo()
        qi.struct_size = ctypes.sizeof(CadQueueCreateInfo)
        qi.flags = 0
        q = CadQueue()
        _check(_lib.cadQueueCreate(device, byref(qi), byref(q)))
        self._handle = q

    def destroy(self):
        if self._handle:
            _check(_lib.cadQueueDestroy(self._handle))
            self._handle = None

    @property
    def handle(self):
        return self._handle

    def submit(self, cmd_list, fence=None):
        err = _lib.cadQueueSubmit(
            self._handle,
            cmd_list.handle,
            fence.handle if fence else None,
        )
        if err != CAD_SUCCESS:
            name = _ERROR_NAMES.get(err, f"UNKNOWN({err})")
            raise RuntimeError(f"cadQueueSubmit error: {name} ({err})")
        # Consume command list ownership
        cmd_list.release()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.destroy()


class Fence:
    def __init__(self, device):
        fi = CadFenceCreateInfo()
        fi.struct_size = ctypes.sizeof(CadFenceCreateInfo)
        fi.flags = 0
        f = CadFence()
        _check(_lib.cadFenceCreate(device, byref(fi), byref(f)))
        self._handle = f

    def destroy(self):
        if self._handle:
            _check(_lib.cadFenceDestroy(self._handle))
            self._handle = None

    @property
    def handle(self):
        return self._handle

    def wait(self, timeout_ns=CAD_TIMEOUT_INFINITE):
        _check(_lib.cadFenceWait(self._handle, timeout_ns))

    def poll(self):
        err = _lib.cadFencePoll(self._handle)
        if err == CAD_SUCCESS:
            return True
        if err == CAD_ERROR_NOT_READY:
            return False
        name = _ERROR_NAMES.get(err, f"UNKNOWN({err})")
        raise RuntimeError(f"cadFencePoll error: {name} ({err})")

    def status(self):
        s = c_uint32()
        _check(_lib.cadFenceGetStatus(self._handle, byref(s)))
        return s.value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.destroy()


# ── Mock transport control ──────────────────────────────────────────

def mock_set_pending_ticks(n):
    _lib.cad_mock_set_pending_ticks(n)


def mock_set_next_submit_error(e):
    _lib.cad_mock_set_next_submit_error(e)


def mock_advance_ticks(n):
    _lib.cad_mock_advance_ticks(n)


def mock_reset():
    _lib.cad_mock_reset()
