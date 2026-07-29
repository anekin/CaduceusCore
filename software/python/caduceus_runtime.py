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
    c_int,
    c_uint8,
    c_uint32,
    c_uint64,
    c_void_p,
    byref,
    pointer,
)


class LibCommandIR:
    """Load the command_ir shared library (libcaduceus_command_ir.so).

    Search order:
      1. Same directory as libcaduceus_runtime.so
      2. Install prefix: <module>/../../lib/libcaduceus_command_ir.so
      3. Standard system paths via ctypes.util.find_library
      4. Development fallback: build/software/libcaduceus_command_ir.so
    """

    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            lib_path = cls._find_lib()
            cls._instance = ctypes.CDLL(lib_path)
        return cls._instance

    @classmethod
    def _find_lib(cls):
        import ctypes.util

        # Same directory as the runtime lib
        runtime_path = os.environ.get("CADUCEUS_RUNTIME_LIB")
        if runtime_path:
            runtime_dir = os.path.dirname(runtime_path)
            cand = os.path.join(runtime_dir, "libcaduceus_command_ir.so")
            if os.path.isfile(cand):
                return cand

        module_dir = os.path.dirname(os.path.abspath(__file__))
        prefix_lib = os.path.join(module_dir, "..", "..", "lib",
                                  "libcaduceus_command_ir.so")
        if os.path.isfile(prefix_lib):
            return prefix_lib

        sys_lib = ctypes.util.find_library("caduceus_command_ir")
        if sys_lib:
            return sys_lib

        return "build/software/libcaduceus_command_ir.so"


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

    lib.cadCommandListAppendExecuteBlob.argtypes = [
        CadCommandList, CadBuffer, c_uint64, c_uint64,
    ]
    lib.cadCommandListAppendExecuteBlob.restype = c_uint32

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


# ── Command Blob / Lowering / Encoding API ──────────────────────────


class CommandBlob:
    """Pythonic wrapper around cad_command_blob_t.

    Usage:
        blob = CommandBlob(CAD_CAP_MXU | CAD_CAP_SFU | CAD_CAP_VECTOR | CAD_CAP_DMA)
        input_id = blob.declare_buffer(size=64, host_addr=0x80100000)
        weight_id = blob.declare_buffer(size=32, host_addr=0x80100040)
        ...
        blob.mmul(input_id, weight_id, output_id, scale_id, M=1, K=64, N=64)
        blob.lower()
        encoded = blob.encode()
        # Write encoded to a device buffer, then submit via CommandList + ExecuteBlob
    """

    def __init__(self, caps):
        blob = _lib_ci.cad_command_blob_create(c_uint32(caps))
        if not blob:
            raise RuntimeError("cad_command_blob_create returned NULL")
        self._handle = blob
        self._lowered = False

    def destroy(self):
        if self._handle:
            _lib_ci.cad_command_blob_destroy(self._handle)
            self._handle = None

    @property
    def handle(self):
        return self._handle

    def release(self):
        h = self._handle
        self._handle = None
        return h

    def declare_buffer(self, size, host_addr=0, alignment=64):
        bid = _lib_ci.cad_buffer_declare(
            self._handle,
            c_uint64(size),
            c_uint32(alignment),
            c_uint64(host_addr),
        )
        if bid == CAD_BUFFER_INVALID:
            raise RuntimeError("cad_buffer_declare returned CAD_BUFFER_INVALID")
        return bid

    def mmul(self, input_id, weight_id, output_id, scale_id, M, K, N,
             dep_count=0, deps=None):
        _check_blob_op(
            _lib_ci.cad_op_mmul(
                self._handle,
                c_uint32(input_id), c_uint32(weight_id),
                c_uint32(output_id), c_uint32(scale_id),
                c_uint32(M), c_uint32(K), c_uint32(N),
                c_uint32(dep_count),
                _deps_array(deps, dep_count),
            )
        )

    def sfu(self, sfu_op, input_id, output_id, elements, head_dim=0, pos=0,
            dep_count=0, deps=None):
        _check_blob_op(
            _lib_ci.cad_op_sfu(
                self._handle,
                c_uint32(sfu_op),
                c_uint32(input_id), c_uint32(output_id),
                c_uint32(elements), c_uint32(head_dim), c_uint32(pos),
                c_uint32(dep_count),
                _deps_array(deps, dep_count),
            )
        )

    def vector(self, vec_op, a_id, b_id, output_id, elements,
               dep_count=0, deps=None):
        _check_blob_op(
            _lib_ci.cad_op_vector(
                self._handle,
                c_uint32(vec_op),
                c_uint32(a_id), c_uint32(b_id), c_uint32(output_id),
                c_uint32(elements),
                c_uint32(dep_count),
                _deps_array(deps, dep_count),
            )
        )

    def dma_copy(self, src_id, src_offset, dst_id, dst_offset, size,
                 dep_count=0, deps=None):
        _check_blob_op(
            _lib_ci.cad_op_dma_copy(
                self._handle,
                c_uint32(src_id), c_uint64(src_offset),
                c_uint32(dst_id), c_uint64(dst_offset),
                c_uint64(size),
                c_uint32(dep_count),
                _deps_array(deps, dep_count),
            )
        )

    def barrier(self):
        _check_blob_op(_lib_ci.cad_op_barrier(self._handle))

    def lower(self):
        ls = _lib_ci.cad_command_blob_lower(self._handle)
        if ls != CAD_LOWER_OK:
            msg = _lib_ci.cad_lower_status_string(ls)
            raise RuntimeError(f"cad_command_blob_lower: {msg}")
        self._lowered = True

    def encode(self):
        if not self._lowered:
            raise RuntimeError("must lower() before encode()")
        out_buf = POINTER(c_uint8)()
        out_size = c_size_t()
        rc = _lib_ci.cad_command_blob_encode(
            self._handle, byref(out_buf), byref(out_size)
        )
        if rc != 0 or not out_buf or out_size.value == 0:
            raise RuntimeError("cad_command_blob_encode failed")
        data = bytes((c_uint8 * out_size.value).from_address(
            ctypes.addressof(out_buf.contents)
        ))
        _lib_ci.cad_command_blob_encoded_free(out_buf)
        return data

    @property
    def num_commands(self):
        return _lib_ci.cad_command_blob_num_commands(self._handle)

    @property
    def num_buffers(self):
        return _lib_ci.cad_command_blob_num_buffers(self._handle)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.destroy()


# Command blob constants
CAD_CAP_MXU = (1 << 0)
CAD_CAP_SFU = (1 << 1)
CAD_CAP_VECTOR = (1 << 2)
CAD_CAP_DMA = (1 << 3)
CAD_CAP_PCIE = (1 << 4)

CAD_OP_MMUL = 0x00
CAD_OP_SFU_RMSNORM = 0x17
CAD_OP_SFU_SOFTMAX = 0x01
CAD_OP_SFU_LAYERNORM = 0x02
CAD_OP_SFU_GELU = 0x03
CAD_OP_SFU_SILU = 0x06
CAD_OP_VADD = 0x0F
CAD_OP_VMUL = 0x10
CAD_OP_DMA_COPY = 0x09
CAD_OP_BARRIER = 0xFF

CAD_BUFFER_INVALID = 0

CAD_LOWER_OK = 0

_LOWER_STATUS = {
    0: "OK",
    1: "INVALID_SHAPE",
    2: "INVALID_ALIGNMENT",
    3: "BUFFER_OVERLAP",
    4: "ADDRESS_OVERFLOW",
    5: "UNSUPPORTED_OP",
    6: "BAD_TILE",
    7: "INVALID_DEPENDENCY",
    8: "OUT_OF_MEMORY",
    9: "INVALID_BLOB",
}


def _deps_array(deps, count):
    if count == 0 or deps is None:
        return None
    return (c_uint32 * len(deps))(*deps)


def _check_blob_op(rc):
    if rc != 0:
        raise RuntimeError(f"command blob operation failed: rc={rc}")


# ── Command list: AppendExecuteBlob extension ───────────────────────


def _setup_command_ir_prototypes(lib):
    lib.cad_command_blob_create.argtypes = [c_uint32]
    lib.cad_command_blob_create.restype = c_void_p

    lib.cad_command_blob_destroy.argtypes = [c_void_p]
    lib.cad_command_blob_destroy.restype = None

    lib.cad_buffer_declare.argtypes = [
        c_void_p, c_uint64, c_uint32, c_uint64,
    ]
    lib.cad_buffer_declare.restype = c_uint32

    lib.cad_op_mmul.argtypes = [
        c_void_p,
        c_uint32, c_uint32, c_uint32, c_uint32,
        c_uint32, c_uint32, c_uint32,
        c_uint32, c_void_p,
    ]
    lib.cad_op_mmul.restype = c_int

    lib.cad_op_sfu.argtypes = [
        c_void_p,
        c_uint32, c_uint32, c_uint32,
        c_uint32, c_uint32, c_uint32,
        c_uint32, c_void_p,
    ]
    lib.cad_op_sfu.restype = c_int

    lib.cad_op_vector.argtypes = [
        c_void_p,
        c_uint32, c_uint32, c_uint32, c_uint32,
        c_uint32, c_uint32, c_void_p,
    ]
    lib.cad_op_vector.restype = c_int

    lib.cad_op_dma_copy.argtypes = [
        c_void_p,
        c_uint32, c_uint64, c_uint32, c_uint64,
        c_uint64, c_uint32, c_void_p,
    ]
    lib.cad_op_dma_copy.restype = c_int

    lib.cad_op_barrier.argtypes = [c_void_p]
    lib.cad_op_barrier.restype = c_int

    lib.cad_command_blob_lower.argtypes = [c_void_p]
    lib.cad_command_blob_lower.restype = c_uint32

    lib.cad_command_blob_encode.argtypes = [
        c_void_p, POINTER(POINTER(c_uint8)), POINTER(c_size_t),
    ]
    lib.cad_command_blob_encode.restype = c_int

    lib.cad_command_blob_encoded_free.argtypes = [c_void_p]
    lib.cad_command_blob_encoded_free.restype = None

    lib.cad_command_blob_num_commands.argtypes = [c_void_p]
    lib.cad_command_blob_num_commands.restype = c_size_t

    lib.cad_command_blob_num_buffers.argtypes = [c_void_p]
    lib.cad_command_blob_num_buffers.restype = c_size_t

    lib.cad_lower_status_string.argtypes = [c_uint32]
    lib.cad_lower_status_string.restype = ctypes.c_char_p

    lib.cad_test_set_buffer_phys_addr.argtypes = [
        c_void_p, c_uint32, c_uint64,
    ]
    lib.cad_test_set_buffer_phys_addr.restype = c_int


c_size_t = c_uint64
_lib_ci = LibCommandIR.get()
_setup_command_ir_prototypes(_lib_ci)


def append_execute_blob(cmd_list, blob_buffer, blob_offset, blob_size):
    err = _lib.cadCommandListAppendExecuteBlob(
        cmd_list.handle if hasattr(cmd_list, 'handle') else cmd_list,
        blob_buffer.handle if hasattr(blob_buffer, 'handle') else blob_buffer,
        c_uint64(blob_offset),
        c_uint64(blob_size),
    )
    if err != CAD_SUCCESS:
        name = _ERROR_NAMES.get(err, f"UNKNOWN({err})")
        raise RuntimeError(f"cadCommandListAppendExecuteBlob error: {name} ({err})")


def test_set_buffer_phys_addr(blob, buffer_id, addr):
    rc = _lib_ci.cad_test_set_buffer_phys_addr(
        blob.handle if hasattr(blob, 'handle') else blob,
        c_uint32(buffer_id),
        c_uint64(addr),
    )
    if rc != 0:
        raise RuntimeError(f"cad_test_set_buffer_phys_addr failed: rc={rc}")
