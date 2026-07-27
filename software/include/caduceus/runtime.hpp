/*
 * CaduceusCore Host Runtime — C++ RAII Wrapper
 *
 * Thin header-only wrappers over the C ABI (runtime.h).
 * Every class owns exactly one opaque handle and calls the corresponding
 * destroy/close function in its destructor.  No C ABI change.
 *
 * These wrappers are NOT required — the C API remains the authoritative
 * interface.  Use them when RAII semantics reduce error-prone manual
 * resource management in C++ application code.
 */

#ifndef CADUCEUS_RUNTIME_HPP
#define CADUCEUS_RUNTIME_HPP

#include "caduceus/runtime.h"

#include <cassert>
#include <stdexcept>
#include <string>
#include <utility>

namespace cad {

/* ── Exception type ──────────────────────────────────────────────── */

class RuntimeError : public std::runtime_error {
public:
    explicit RuntimeError(cad_error_t code)
        : std::runtime_error(cadErrorString(code))
        , code_(code) {}

    explicit RuntimeError(cad_error_t code, const std::string &msg)
        : std::runtime_error(msg)
        , code_(code) {}

    cad_error_t code() const noexcept { return code_; }

private:
    cad_error_t code_;
};

/* ── Helper: throw on non-success ────────────────────────────────── */

namespace detail {
inline void check(cad_error_t err, const char *caller) {
    if (err != CAD_SUCCESS) {
        throw RuntimeError(err, std::string(caller) + ": " + cadErrorString(err));
    }
}
}  // namespace detail

#define CAD_CHECK(expr) ::cad::detail::check((expr), #expr)

/* ── Device ─────────────────────────────────────────────────────── */

class Device {
public:
    Device() = default;

    explicit Device(const cad_device_open_info_t &open_info) {
        cad_device_caps_t caps{};
        caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
        CAD_CHECK(cadDeviceOpen(&open_info, &handle_, &caps));
        caps_ = caps;
    }

    ~Device() { reset(); }

    Device(const Device &) = delete;
    Device &operator=(const Device &) = delete;

    Device(Device &&other) noexcept
        : handle_(std::exchange(other.handle_, nullptr))
        , caps_(other.caps_) {}

    Device &operator=(Device &&other) noexcept {
        if (this != &other) {
            reset();
            handle_ = std::exchange(other.handle_, nullptr);
            caps_ = other.caps_;
        }
        return *this;
    }

    cad_device_t get() const noexcept { return handle_; }
    const cad_device_caps_t &caps() const noexcept { return caps_; }

    explicit operator bool() const noexcept { return handle_ != nullptr; }

    void reset() {
        if (handle_) {
            cadDeviceClose(handle_);
            handle_ = nullptr;
        }
    }

    cad_device_caps_t getCaps() const {
        cad_device_caps_t caps{};
        caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
        CAD_CHECK(cadDeviceGetCaps(handle_, &caps));
        return caps;
    }

    void deviceReset() {
        CAD_CHECK(cadDeviceReset(handle_));
    }

private:
    cad_device_t handle_ = nullptr;
    cad_device_caps_t caps_{};
};

/* ── Buffer ─────────────────────────────────────────────────────── */

class Buffer {
public:
    Buffer() = default;

    Buffer(cad_device_t device, const cad_buffer_create_info_t &create_info) {
        CAD_CHECK(cadBufferAllocate(device, &create_info, &handle_));
    }

    ~Buffer() { reset(); }

    Buffer(const Buffer &) = delete;
    Buffer &operator=(const Buffer &) = delete;

    Buffer(Buffer &&other) noexcept
        : handle_(std::exchange(other.handle_, nullptr)) {}

    Buffer &operator=(Buffer &&other) noexcept {
        if (this != &other) {
            reset();
            handle_ = std::exchange(other.handle_, nullptr);
        }
        return *this;
    }

    cad_buffer_t get() const noexcept { return handle_; }
    explicit operator bool() const noexcept { return handle_ != nullptr; }

    void reset() {
        if (handle_) {
            cadBufferFree(handle_);
            handle_ = nullptr;
        }
    }

    void read(uint64_t offset, uint64_t size, void *data) const {
        CAD_CHECK(cadBufferRead(handle_, offset, size, data));
    }

    void write(uint64_t offset, uint64_t size, const void *data) const {
        CAD_CHECK(cadBufferWrite(handle_, offset, size, data));
    }

private:
    cad_buffer_t handle_ = nullptr;
};

/* ── CommandList ─────────────────────────────────────────────────── */

class CommandList {
public:
    CommandList() = default;

    CommandList(cad_device_t device,
                const cad_command_list_create_info_t &create_info) {
        CAD_CHECK(cadCommandListCreate(device, &create_info, &handle_));
    }

    ~CommandList() { reset(); }

    CommandList(const CommandList &) = delete;
    CommandList &operator=(const CommandList &) = delete;

    CommandList(CommandList &&other) noexcept
        : handle_(std::exchange(other.handle_, nullptr)) {}

    CommandList &operator=(CommandList &&other) noexcept {
        if (this != &other) {
            reset();
            handle_ = std::exchange(other.handle_, nullptr);
        }
        return *this;
    }

    cad_command_list_t get() const noexcept { return handle_; }
    explicit operator bool() const noexcept { return handle_ != nullptr; }

    /* Release ownership so the caller can submit and transfer
     * ownership to the queue.  Returns the raw handle. */
    cad_command_list_t release() noexcept {
        return std::exchange(handle_, nullptr);
    }

    void reset() {
        if (handle_) {
            cadCommandListDestroy(handle_);
            handle_ = nullptr;
        }
    }

    void appendNop() {
        CAD_CHECK(cadCommandListAppendNop(handle_));
    }

private:
    cad_command_list_t handle_ = nullptr;
};

/* ── Queue ──────────────────────────────────────────────────────── */

class Queue {
public:
    Queue() = default;

    Queue(cad_device_t device, const cad_queue_create_info_t &create_info) {
        CAD_CHECK(cadQueueCreate(device, &create_info, &handle_));
    }

    ~Queue() { reset(); }

    Queue(const Queue &) = delete;
    Queue &operator=(const Queue &) = delete;

    Queue(Queue &&other) noexcept
        : handle_(std::exchange(other.handle_, nullptr)) {}

    Queue &operator=(Queue &&other) noexcept {
        if (this != &other) {
            reset();
            handle_ = std::exchange(other.handle_, nullptr);
        }
        return *this;
    }

    cad_queue_t get() const noexcept { return handle_; }
    explicit operator bool() const noexcept { return handle_ != nullptr; }

    void reset() {
        if (handle_) {
            cadQueueDestroy(handle_);
            handle_ = nullptr;
        }
    }

    /* Submit a command list.  On success, the CommandList's ownership is
     * consumed; on failure the CommandList is untouched. */
    void submit(CommandList &cmd_list, cad_fence_t fence = nullptr) {
        cad_command_list_t raw = cmd_list.get();
        cad_error_t err = cadQueueSubmit(handle_, raw, fence);
        if (err != CAD_SUCCESS) {
            throw RuntimeError(err, "cadQueueSubmit: " +
                               std::string(cadErrorString(err)));
        }
        /* Success — consume ownership so caller can't double-free. */
        cmd_list.release();
    }

private:
    cad_queue_t handle_ = nullptr;
};

/* ── Fence ──────────────────────────────────────────────────────── */

class Fence {
public:
    Fence() = default;

    Fence(cad_device_t device, const cad_fence_create_info_t &create_info) {
        CAD_CHECK(cadFenceCreate(device, &create_info, &handle_));
    }

    ~Fence() { reset(); }

    Fence(const Fence &) = delete;
    Fence &operator=(const Fence &) = delete;

    Fence(Fence &&other) noexcept
        : handle_(std::exchange(other.handle_, nullptr)) {}

    Fence &operator=(Fence &&other) noexcept {
        if (this != &other) {
            reset();
            handle_ = std::exchange(other.handle_, nullptr);
        }
        return *this;
    }

    cad_fence_t get() const noexcept { return handle_; }
    explicit operator bool() const noexcept { return handle_ != nullptr; }

    void reset() {
        if (handle_) {
            cadFenceDestroy(handle_);
            handle_ = nullptr;
        }
    }

    void wait(uint64_t timeout_ns = CAD_TIMEOUT_INFINITE) {
        CAD_CHECK(cadFenceWait(handle_, timeout_ns));
    }

    bool poll() {
        cad_error_t err = cadFencePoll(handle_);
        if (err == CAD_SUCCESS) return true;
        if (err == CAD_ERROR_NOT_READY) return false;
        throw RuntimeError(err, "cadFencePoll: " +
                           std::string(cadErrorString(err)));
    }

    cad_fence_status_t getStatus() {
        cad_fence_status_t status = CAD_FENCE_NOT_READY;
        CAD_CHECK(cadFenceGetStatus(handle_, &status));
        return status;
    }

private:
    cad_fence_t handle_ = nullptr;
};

}  // namespace cad

#endif /* CADUCEUS_RUNTIME_HPP */
