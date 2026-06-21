/* npu_mmio_plugin.cc — Spike MMIO plugin that forwards NPU register
 * loads/stores to a Python server over a Unix domain socket.
 *
 * Registers with Spike as a plugin device:
 *   --extlib=plugins/npu_mmio_plugin.so --device=npu,0x20000000
 *
 * Protocol (text line oriented, \n terminated):
 *   R 0xADDR        -> 0xVALUE
 *   W 0xADDR 0xVALUE -> OK
 */

#include "abstract_device.h"
#include "devices.h"

#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <sstream>
#include <string>
#include <vector>

#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>

/* NPU address map (from firmware/npu-regmap.h) */
static constexpr reg_t NPU_SRAM_BASE = 0x20000000ULL;
static constexpr reg_t NPU_END       = 0x40011fffULL;

static constexpr const char* NPU_SOCK_PATH = "/tmp/npu_mmio.sock";
static constexpr int NPU_SOCK_TIMEOUT_MS   = 2000;
static constexpr int NPU_CONNECT_RETRIES   = 40;   /* brief retry on first access */
static constexpr int NPU_CONNECT_DELAY_US  = 50000;

class npu_t : public abstract_device_t {
 public:
  explicit npu_t(reg_t base)
    : base_addr(base), sock_fd(-1) {}

  ~npu_t() override { disconnect(); }

  bool load(reg_t addr, size_t len, uint8_t* bytes) override {
    if (!ensure_connected()) return false;
    if (len != 1 && len != 2 && len != 4) return false;

    reg_t abs_addr = base_addr + addr;
    char req[64];
    std::snprintf(req, sizeof(req), "R 0x%x\n", static_cast<uint32_t>(abs_addr));

    char resp[64];
    if (!transaction(req, resp, sizeof(resp))) return false;

    uint32_t value = static_cast<uint32_t>(std::strtoul(resp, nullptr, 0));
    for (size_t i = 0; i < len; i++) {
      bytes[i] = static_cast<uint8_t>((value >> (8 * i)) & 0xff);
    }
    return true;
  }

  bool store(reg_t addr, size_t len, const uint8_t* bytes) override {
    if (!ensure_connected()) return false;
    if (len != 1 && len != 2 && len != 4) return false;

    reg_t abs_addr = base_addr + addr;
    uint32_t value = 0;
    for (size_t i = 0; i < len; i++) {
      value |= static_cast<uint32_t>(bytes[i]) << (8 * i);
    }

    char req[80];
    std::snprintf(req, sizeof(req), "W 0x%x 0x%x\n",
                  static_cast<uint32_t>(abs_addr), value);

    char resp[16];
    if (!transaction(req, resp, sizeof(resp))) return false;
    return std::strncmp(resp, "OK", 2) == 0;
  }

  reg_t size() override {
    if (base_addr > NPU_END) return 0;
    return NPU_END - base_addr + 1;
  }

 private:
  reg_t base_addr;
  int sock_fd;
  bool connect_permanently_failed = false;
  bool connect_failed_logged = false;

  void disconnect() {
    if (sock_fd >= 0) {
      close(sock_fd);
      sock_fd = -1;
    }
  }

  bool set_timeouts(int fd) {
    struct timeval tv;
    tv.tv_sec  = NPU_SOCK_TIMEOUT_MS / 1000;
    tv.tv_usec = (NPU_SOCK_TIMEOUT_MS % 1000) * 1000;
    if (setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv)) < 0) return false;
    if (setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv)) < 0) return false;
    return true;
  }

  bool try_connect() {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return false;

    if (!set_timeouts(fd)) {
      close(fd);
      return false;
    }

    struct sockaddr_un addr;
    std::memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    std::strncpy(addr.sun_path, NPU_SOCK_PATH, sizeof(addr.sun_path) - 1);

    if (::connect(fd, reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr)) < 0) {
      close(fd);
      return false;
    }

    sock_fd = fd;
    return true;
  }

  bool ensure_connected() {
    if (sock_fd >= 0) return true;
    if (connect_permanently_failed) return false;

    for (int i = 0; i < NPU_CONNECT_RETRIES; i++) {
      if (try_connect()) return true;
      usleep(NPU_CONNECT_DELAY_US);
    }

    connect_permanently_failed = true;
    if (!connect_failed_logged) {
      fprintf(stderr, "npu_mmio_plugin: unable to connect to %s\n", NPU_SOCK_PATH);
      connect_failed_logged = true;
    }
    return false;
  }

  bool transaction(const char* req, char* resp, size_t resp_sz) {
    size_t req_len = std::strlen(req);
    ssize_t sent = 0;
    while (sent < static_cast<ssize_t>(req_len)) {
      ssize_t n = ::send(sock_fd, req + sent, req_len - sent, 0);
      if (n <= 0) {
        disconnect();
        return false;
      }
      sent += n;
    }

    size_t i = 0;
    while (i + 1 < resp_sz) {
      char c;
      ssize_t n = ::recv(sock_fd, &c, 1, 0);
      if (n <= 0) {
        disconnect();
        return false;
      }
      resp[i++] = c;
      if (c == '\n') break;
    }
    resp[i] = '\0';
    return true;
  }
};

static npu_t* npu_parse_from_fdt(
    const void* fdt,
    const sim_t* sim,
    reg_t* base,
    const std::vector<std::string>& sargs) {
  (void)fdt;
  (void)sim;

  if (sargs.empty()) {
    fprintf(stderr, "npu_mmio_plugin: --device=npu,<base_addr> requires a base address\n");
    return nullptr;
  }

  try {
    *base = std::stoull(sargs[0], nullptr, 0);
  } catch (const std::exception& e) {
    fprintf(stderr, "npu_mmio_plugin: invalid base address '%s': %s\n", sargs[0].c_str(), e.what());
    return nullptr;
  }

  if (*base != NPU_SRAM_BASE) {
    fprintf(stderr, "npu_mmio_plugin: warning: base 0x%lx is not the expected 0x%lx\n",
            static_cast<unsigned long>(*base), static_cast<unsigned long>(NPU_SRAM_BASE));
  }

  return new npu_t(*base);
}

static std::string npu_generate_dts(const sim_t* sim,
                                    const std::vector<std::string>& sargs) {
  (void)sim;
  reg_t base = NPU_SRAM_BASE;
  if (!sargs.empty()) {
    try { base = std::stoull(sargs[0], nullptr, 0); } catch (...) {}
  }
  reg_t size = (base <= NPU_END) ? (NPU_END - base + 1) : 0;

  std::ostringstream oss;
  oss << std::hex
      << "    npu@" << base << " {\n"
      << "      compatible = \"npu\";\n"
      << "      reg = <0x0 0x" << base << " 0x0 0x" << size << ">;\n"
      << "    };\n";
  return oss.str();
}

REGISTER_DEVICE(npu, npu_parse_from_fdt, npu_generate_dts)
