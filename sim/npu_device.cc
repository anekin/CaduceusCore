/* Spike MMIO Device — NPU (DEBUG: accept all addresses) */

#include "riscv/abstract_device.h"
#include "riscv/sim.h"
#include "riscv/devices.h"
#include <cstring>
#include <cstdio>

enum : uint32_t {
    MXU_BASE      = 0x40000000,
    SFU_BASE      = 0x40001000,
    VECTOR_BASE   = 0x40002000,
    DMA_BASE      = 0x40003000,
    DOORBELL_BASE = 0x40010000,
    INTC_BASE     = 0x40011000,
    MMIO_START    = MXU_BASE,
    MMIO_END      = INTC_BASE + 0x1000,
    MMIO_SIZE     = MMIO_END - MMIO_START
};

class npu_t : public abstract_device_t {
public:
    npu_t(sim_t* sim) : sim_(sim) {
        std::memset(regs_, 0, sizeof(regs_));
        regs_[(INTC_BASE - MMIO_START + 0x04) >> 2] = 0xFF;
        fprintf(stderr, "[NPU] Initialized, MMIO range 0x%08x-0x%08x\n", MMIO_START, MMIO_END-1);
    }

    bool load(reg_t addr, size_t len, uint8_t* bytes) override {
        uint64_t paddr = (uint64_t)addr;
        if (paddr >= MMIO_START && paddr < MMIO_END) {
            uint32_t off = (paddr - MMIO_START) & ~3u;
            uint32_t val = regs_[off >> 2];
            fprintf(stderr, "[NPU] LOAD  0x%08llx = 0x%08x\n", (unsigned long long)paddr, val);
            std::memcpy(bytes, &val, len < 4 ? len : 4);
            return true;
        }
        return false;
    }

    bool store(reg_t addr, size_t len, const uint8_t* bytes) override {
        uint64_t paddr = (uint64_t)addr;
        uint32_t val;
        std::memcpy(&val, bytes, len < 4 ? len : 4);

        if (paddr >= MMIO_START && paddr < MMIO_END) {
            uint32_t off = (paddr - MMIO_START) & ~3u;
            fprintf(stderr, "[NPU] STORE 0x%08llx = 0x%08x\n", (unsigned long long)paddr, val);

            // MXU CMD
            if (paddr == MXU_BASE + 0x04 && (val & 1)) {
                regs_[(MXU_BASE - MMIO_START + 0x08) >> 2] = 2;  // DONE
                if (regs_[(MXU_BASE - MMIO_START + 0x28) >> 2] & 1)
                    regs_[(INTC_BASE - MMIO_START + 0x00) >> 2] |= 1;
                fprintf(stderr, "[NPU] MXU DONE\n");
                return true;
            }
            // SFU CMD
            if (paddr == SFU_BASE + 0x04 && (val & 1)) {
                regs_[(SFU_BASE - MMIO_START + 0x08) >> 2] = 2;
                if (regs_[(SFU_BASE - MMIO_START + 0x1C) >> 2] & 1)
                    regs_[(INTC_BASE - MMIO_START + 0x00) >> 2] |= 2;
                return true;
            }
            // VECTOR CMD
            if (paddr == VECTOR_BASE + 0x04 && (val & 1)) {
                regs_[(VECTOR_BASE - MMIO_START + 0x08) >> 2] = 2;
                if (regs_[(VECTOR_BASE - MMIO_START + 0x1C) >> 2] & 1)
                    regs_[(INTC_BASE - MMIO_START + 0x00) >> 2] |= 4;
                return true;
            }
            // DMA CMD
            if (paddr == DMA_BASE + 0x04 && (val & 1)) {
                regs_[(DMA_BASE - MMIO_START + 0x08) >> 2] = 2;
                if (regs_[(DMA_BASE - MMIO_START + 0x38) >> 2] & 1)
                    regs_[(INTC_BASE - MMIO_START + 0x00) >> 2] |= 8;
                fprintf(stderr, "[NPU] DMA DONE\n");
                return true;
            }
            // INTC ACK
            if (paddr == INTC_BASE + 0x0C) {
                regs_[(INTC_BASE - MMIO_START + 0x00) >> 2] &= ~val;
                return true;
            }
            regs_[off >> 2] = val;
            return true;
        }
        return false;
    }

    reg_t size() override { return MMIO_SIZE; }

private:
    sim_t* sim_;
    uint32_t regs_[MMIO_SIZE / 4];
};

static npu_t* npu_parse_from_fdt(
    const void* fdt, const sim_t* sim, reg_t* base,
    const std::vector<std::string>& /*sargs*/)
{
    *base = MMIO_START;
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

REGISTER_DEVICE(npu, npu_parse_from_fdt, npu_generate_dts)
