/* Spike Built-in Device — NPU
 * 
 * 编译进 Spike: riscv/riscv.mk.in 的 riscv_srcs 列表 + REGISTER_BUILTIN_DEVICE
 * sim.cc 中 extern + device_factories 列表引用
 *
 * 注意: bus_t 调用 load/store 时传入的是 相对地址 (已减去基地址)！
 */

#include "riscv/abstract_device.h"
#include "riscv/sim.h"
#include "riscv/devices.h"
#include <cstring>
#include <cstdio>

/* ── 偏移地址 (相对 NPU 基地址 0x40000000) ─────────────────────── */

enum : uint32_t {
    OFF_MXU      = 0x00000000,
    OFF_SFU      = 0x00001000,
    OFF_VECTOR   = 0x00002000,
    OFF_DMA      = 0x00003000,
    OFF_DOORBELL = 0x00010000,
    OFF_INTC     = 0x00011000,
    MMIO_END     = 0x00012000,
    MMIO_SIZE    = MMIO_END
};

/* ── NPU 设备类 ─────────────────────────────────────────────────── */

class npu_t : public abstract_device_t {
public:
    npu_t(sim_t* sim) : sim_(sim) {
        std::memset(regs_, 0, sizeof(regs_));
        regs_[(OFF_INTC + 0x04) >> 2] = 0xFF;  // ENABLE all
        fprintf(stderr, "[NPU] Initialized (base=0x40000000, size=0x%x)\n", MMIO_SIZE);
    }

    bool load(reg_t addr, size_t len, uint8_t* bytes) override {
        uint64_t off = (uint64_t)addr;
        if (off >= MMIO_SIZE) return false;

        uint32_t word_off = (off & ~3u) >> 2;
        uint32_t val = regs_[word_off];

        if (off >= OFF_DOORBELL)
            fprintf(stderr, "[NPU] R off=0x%04llx = 0x%08x\n", (unsigned long long)off, val);

        std::memcpy(bytes, &val, len < 4 ? len : 4);
        return true;
    }

    bool store(reg_t addr, size_t len, const uint8_t* bytes) override {
        uint64_t off = (uint64_t)addr;
        if (off >= MMIO_SIZE) return false;

        uint32_t val;
        std::memcpy(&val, bytes, len < 4 ? len : 4);

        if (off >= OFF_DOORBELL)
            fprintf(stderr, "[NPU] W off=0x%04llx = 0x%08x\n", (unsigned long long)off, val);

        // ── MXU CMD ──────────────────────────────────────────
        if (off == OFF_MXU + 0x04 && (val & 1)) {
            regs_[(OFF_MXU + 0x08) >> 2] = 2;  // DONE
            if (regs_[(OFF_MXU + 0x28) >> 2] & 1)
                regs_[(OFF_INTC + 0x00) >> 2] |= 1;
            fprintf(stderr, "[NPU] MXU DONE\n");
            return true;
        }
        // ── SFU CMD ──────────────────────────────────────────
        if (off == OFF_SFU + 0x04 && (val & 1)) {
            regs_[(OFF_SFU + 0x08) >> 2] = 2;
            if (regs_[(OFF_SFU + 0x1C) >> 2] & 1)
                regs_[(OFF_INTC + 0x00) >> 2] |= 2;
            return true;
        }
        // ── VECTOR CMD ───────────────────────────────────────
        if (off == OFF_VECTOR + 0x04 && (val & 1)) {
            regs_[(OFF_VECTOR + 0x08) >> 2] = 2;
            if (regs_[(OFF_VECTOR + 0x1C) >> 2] & 1)
                regs_[(OFF_INTC + 0x00) >> 2] |= 4;
            return true;
        }
        // ── DMA CMD ──────────────────────────────────────────
        if (off == OFF_DMA + 0x04 && (val & 1)) {
            regs_[(OFF_DMA + 0x08) >> 2] = 2;
            if (regs_[(OFF_DMA + 0x38) >> 2] & 1)
                regs_[(OFF_INTC + 0x00) >> 2] |= 8;
            fprintf(stderr, "[NPU] DMA DONE\n");
            return true;
        }
        // ── INTC ACK ─────────────────────────────────────────
        if (off == OFF_INTC + 0x0C) {
            regs_[(OFF_INTC + 0x00) >> 2] &= ~val;
            return true;
        }

        regs_[(off & ~3u) >> 2] = val;
        return true;
    }

    reg_t size() override { return MMIO_SIZE; }

private:
    sim_t* sim_;
    uint32_t regs_[MMIO_SIZE / 4];
};

/* ── 设备注册 ───────────────────────────────────────────────────── */

static npu_t* npu_parse_from_fdt(
    const void* fdt, const sim_t* sim, reg_t* base,
    const std::vector<std::string>& /*sargs*/)
{
    *base = 0x40000000ULL;
    return new npu_t(const_cast<sim_t*>(sim));
}

static std::string npu_generate_dts(
    const sim_t* /*sim*/, const std::vector<std::string>& /*sargs*/)
{
    return "    npu@40000000 {\n"
           "      compatible = \"npu,mmio\";\n"
           "      reg = <0x0 0x40000000 0x0 0x12000>;\n"
           "    };\n";
}

REGISTER_BUILTIN_DEVICE(npu, npu_parse_from_fdt, npu_generate_dts)
