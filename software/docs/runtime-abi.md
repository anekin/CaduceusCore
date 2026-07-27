# CaduceusCore Host Runtime ABI — Design Document

> Version 1.0 | 2026-07-27

## 1. Purpose

The CaduceusCore Host Runtime ABI (`runtime.h`) is the **stable C-level contract**
between application code (llama.cpp backend, ExecuTorch delegate, test harness)
and the CaduceusCore NPU hardware through interchangeable transports
(Func Model, RTL simulation, FPGA, mock).

The ABI is patterned on **Vulkan/CUDA/OpenCL host API conventions**:
opaque handles, typed versioned structs with `struct_size`, explicit
create/destroy lifecycle, and extension query via capability structs.

## 2. Design Principles

### 2.1 Opaque Handles

All resources are represented by opaque pointer-to-incomplete-type handles:
`cad_device_t`, `cad_buffer_t`, `cad_queue_t`, `cad_command_list_t`,
`cad_fence_t`.  Application code never dereferences or allocates these
directly — they are created by API calls and consumed by API calls.

### 2.2 Versioned Structs

Every struct passed across the API boundary starts with a `uint32_t struct_size`
field.  This is a **Vulkan-style extensibility mechanism**:

- The caller sets `struct_size = sizeof(struct_type)`.
- The callee checks that `struct_size >= minimum_expected`.
- If the caller's struct is larger (from a newer header), the callee
  uses only the fields it knows about.
- New fields are always added at the end of a struct — never inserted,
  removed, or reordered.

**Rule:** the runtime rejects a struct whose `struct_size` is smaller than
the minimum it requires, returning `CAD_ERROR_INVALID_ARGUMENT`.

### 2.3 ABI Version Negotiation

Two integer versions govern compatibility: **Major** and **Minor**.

| Field | Semantics |
|-------|-----------|
| `abi_major` | Incompatible changes (struct layout reorder, handle semantic change, removed API). |
| `abi_minor` | Additive changes (new fields at struct end, new API functions, new capability bits). |

**Negotiation on `cadDeviceOpen`**:

1. Client sets `open_info.abi_major` and `open_info.abi_minor` to the
   version it was compiled against.
2. Runtime compares:
   - `client.major != runtime.major` → `CAD_ERROR_INCOMPATIBLE_ABI`
   - `client.minor > runtime.minor` → `CAD_ERROR_INCOMPATIBLE_ABI`
     (client expects features the runtime doesn't have)
   - Otherwise → `CAD_SUCCESS`, the runtime fills `caps.abi_major` and
     `caps.abi_minor` with its actual version.

This lets an older-minor client compiled against `1.0` talk to a runtime
supporting `1.5` — the runtime simply ignores newer fields the client
never set.

### 2.4 URI-Based Device Selection

The `cad_device_open_info_t::uri` field selects the transport backend:

| URI Prefix | Transport | Use Case |
|------------|-----------|----------|
| `fm://`    | Func Model | Early software development, golden reference |
| `rtl://`   | RTL simulation | VCS/Cocotb-based hardware verification |
| `fpga://`  | FPGA userspace | Real FPGA board over UIO/VFIO |
| `mock://`  | Mock device | Unit testing, CI, transport conformance |

The runtime is responsible for parsing the URI and instantiating the
correct backend.  Application code never contains transport-specific logic.

### 2.5 No Python, cocotb, BAR offsets, or physical pointers

The C ABI deliberately excludes:
- Python objects, numpy arrays, cocotb handles
- BAR offset constants, MMIO register addresses
- Physical PCIe addresses, DMA descriptor layouts
- Framework-specific types (ggml_tensor, ExecuTorch tensor)

These are implementation details of the transport backend behind the
opaque handle.

## 3. Ownership and Lifecycle

| Resource | Created by | Destroyed by | Special rules |
|----------|-----------|-------------|---------------|
| Device   | `cadDeviceOpen` | `cadDeviceClose` | Must be last to close; owns all child resources implicitly (they must be freed first) |
| Buffer   | `cadBufferAllocate` | `cadBufferFree` | Must not be in use by in-flight command lists |
| Queue    | `cadQueueCreate` | `cadQueueDestroy` | Must have no in-flight submissions |
| Command list | `cadCommandListCreate` | `cadCommandListDestroy` or consumed by `cadQueueSubmit` | **Single-use**: on successful submit, ownership transfers to the queue; caller must not use or free |
| Fence    | `cadFenceCreate` | `cadFenceDestroy` | Can be reused after signal (reset not yet defined) |

### 3.1 Command List Ownership Transfer

This is the most important ownership rule:

```
cadQueueSubmit(queue, cmd_list, fence)
```

On **success** (`CAD_SUCCESS`): the runtime takes ownership of `cmd_list`.
The caller's handle is dead — further operations on it return
`CAD_ERROR_INVALID_HANDLE`.

On **failure**: the caller retains ownership and may retry or destroy.

This prevents use-after-submit and double-submit bugs.

### 3.2 Buffer Stability

A buffer allocated with `cadBufferAllocate` has a **stable device address**
for its entire lifetime.  It must not be freed while any command list
referencing it is in-flight.  The runtime does **not** track buffer
references — the application is responsible for ordering.

## 4. Thread Safety

The C ABI makes the following guarantees:

| Operation | Thread safety |
|-----------|--------------|
| `cadDeviceOpen` / `cadDeviceClose` | Not thread-safe — single-threaded setup/teardown |
| `cadDeviceGetCaps` | Safe to call from any thread |
| `cadDeviceReset` | Safe; cancels all in-flight work on all queues |
| `cadBufferAllocate` / `cadBufferFree` | Thread-safe with respect to the **same** device; not safe on the same buffer concurrently |
| `cadBufferRead` / `cadBufferWrite` | Not safe on the same buffer concurrently |
| `cadQueueSubmit` | Thread-safe across **different** queues; submission order within a single queue requires external synchronization |
| `cadFenceWait` / `cadFencePoll` | Safe to call from any thread; multiple waiters on the same fence is allowed |
| `cadCommandListCreate` / `cadCommandListDestroy` | Not safe on the same command list concurrently |

**Rule of thumb**: synchronization on a single opaque handle is the
caller's responsibility.  Operations on different handles of the same
device are thread-safe.

## 5. Timeout Semantics

`cadFenceWait` accepts a nanosecond timeout.  Two sentinels are defined:

| Value | Meaning |
|-------|---------|
| `CAD_TIMEOUT_IMMEDIATE` (0) | Return immediately — never blocks |
| `CAD_TIMEOUT_INFINITE` (UINT64_MAX) | Block until the fence is signalled |

Implementations **must** support `CAD_TIMEOUT_INFINITE` and **should**
support sub-millisecond granularity for finite timeouts.  The Func Model
and mock transports implement immediate signalling (the fence is signalled
before `cadQueueSubmit` returns).

## 6. Error Lifetime

Error information is available only through the `cad_error_t` return code
and `cadErrorString()`.  There is **no persistent error state** on any
opaque handle — every call returns its own error.

The `cadErrorString()` return pointer is valid for the lifetime of the
process (it points to a static string table).

Error codes are **not** bitmaskable.  The caller must check for a single
specific code or for `!= CAD_SUCCESS`.

## 7. Reset Semantics

`cadDeviceReset` is a **hard abort**:

1. All in-flight command lists on all queues are cancelled.
2. All pending fences transition to `CAD_FENCE_ERROR`.
3. All buffers retain their allocated device memory but their contents
   are undefined.
4. The device handle remains valid — the caller may submit new work
   immediately.

Reset does **not** free any handles.  Buffers, queues, command lists, and
fences must be individually re-created if needed.

## 8. Extension Query Pattern

The `cad_device_caps_t` struct is the primary extension query mechanism.
Newer minor versions may add fields at the end (e.g., `supports_bf16`,
`max_tile_size`, `preferred_alignment`).  A client compiled against a
newer header can check `caps.struct_size >= offsetof(cad_device_caps_t, new_field)`
before accessing the field.

Future extensions (e.g., multi-device, sparse buffers, event objects)
may add new struct types following the same `struct_size` pattern.

## 9. C++ RAII Wrapper (`runtime.hpp`)

The header-only C++ wrapper provides:

- `cad::Device` — automatic `cadDeviceClose` on destruction
- `cad::Buffer` — automatic `cadBufferFree` on destruction
- `cad::Queue` — automatic `cadQueueDestroy` on destruction
- `cad::CommandList` — automatic `cadCommandListDestroy` on destruction; `release()` for transfer to queue
- `cad::Fence` — automatic `cadFenceDestroy` on destruction

All classes are move-only (no copy).  The `Queue::submit()` method takes
a `CommandList&` and calls `release()` internally — the caller's
command-list object becomes empty after a successful submit.

The `CAD_CHECK()` macro converts C error codes to `cad::RuntimeError`
exceptions.

The C++ wrapper makes **no ABI change** — it is a pure header that calls
the same C functions.  Applications that prefer manual C resource
management are not forced to use it.
