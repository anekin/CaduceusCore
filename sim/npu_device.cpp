/* Spike MMIO Extension — NPU Device
 *
 * 拦截 0x40000000-0x4001FFFF 的 load/store，
 * 模拟 MXU/SFU/Vector/DMA/Doorbell/INTC 寄存器行为。
 *
 * 构建: 需要 Spike 已编译, 头文件路径在 spike_src/ 下。
 *   g++ -std=c++17 -fPIC -shared \
 *       -I spike_src/ -I spike_src/riscv/ -I spike_src/fesvr/ \
 *       -o npu_device.so npu_device.cpp
 *
 * 运行:
 *   spike --isa=RV32IM -m0x80000000:0x10000000,0x00000000:0x00400000 \
 *         --extlib=npu_device.so firmware/build/npu_firmware.elf
 */

#include <cstdint>
#include <cstring>
#include <cstdio>
#include <cstdlib>

/* ── Spike 接口 (手动声明, 避免引入全部头文件) ─────────────────── */

// Spike 的 sim_t / abstract_device_t 接口
// 实际链接时由 spike 主程序提供符号

struct reg_t {
    uint64_t val;
    reg_t() : val(0) {}
    reg_t(uint64_t v) : val(v) {}
    operator uint64_t() const { return val; }
};

struct abstract_device_t {
    virtual ~abstract_device_t() {}
    virtual bool load(reg_t addr, size_t len, uint8_t* bytes) = 0;
    virtual bool store(reg_t addr, size_t len, const uint8_t* bytes) = 0;
};

struct sim_t {
    // 我们只需要 sim_t 作为设备构造参数
};

// Spike 期望的插件入口: 返回 device 指针的工厂函数
// 签名: abstract_device_t* create_npu_device(sim_t* sim, const char* args);

/* ── 寄存器地址 (与 regmap.py / npu-regmap.h 一致) ─────────────── */

enum : uint32_t {
    MXU_BASE     = 0x40000000,
    SFU_BASE     = 0x40001000,
    VECTOR_BASE  = 0x40002000,
    DMA_BASE     = 0x40003000,
    DOORBELL_BASE= 0x40010000,
    INTC_BASE    = 0x40011000,

    MMIO_START = MXU_BASE,
    MMIO_END   = INTC_BASE + 0x1000,
    MMIO_SIZE  = MMIO_END - MMIO_START
};

/* ── NPU 设备 ────────────────────────────────────────────────────── */

class npu_device_t : public abstract_device_t {
public:
    npu_device_t(sim_t* sim) {
        std::memset(regs, 0, sizeof(regs));
        regs[INTC_BASE - MMIO_START + 0x04] = 0xFF;  // ENABLE = all on
    }

    bool load(reg_t addr, size_t len, uint8_t* bytes) override {
        uint64_t paddr = (uint64_t)addr;
        if (paddr < MMIO_START || paddr >= MMIO_END) return false;

        uint32_t off = (paddr - MMIO_START) & ~3u;
        uint32_t val = regs[off >> 2];

        // Debug trace
        if (paddr >= 0x40010000) {  // Doorbell / INTC
            fprintf(stderr, "[NPU] R 0x%08llx = 0x%08x\n",
                    (unsigned long long)paddr, val);
        }

        std::memcpy(bytes, &val, len < 4 ? len : 4);
        return true;
    }

    bool store(reg_t addr, size_t len, const uint8_t* bytes) override {
        uint64_t paddr = (uint64_t)addr;
        if (paddr < MMIO_START || paddr >= MMIO_END) return false;

        uint32_t off = (paddr - MMIO_START) & ~3u;
        uint32_t val;
        std::memcpy(&val, bytes, len < 4 ? len : 4);

        // Debug trace
        if (paddr >= 0x40010000) {
            fprintf(stderr, "[NPU] W 0x%08llx = 0x%08x\n",
                    (unsigned long long)paddr, val);
        }

        // ── MXU CMD ─────────────────────────────────────────
        if (paddr == MXU_BASE + 0x04 && (val & 1)) {
            regs[(MXU_BASE - MMIO_START + 0x08) >> 2] = 1;  // STATUS = BUSY
            // 模拟计算 (零延迟 → 立即完成)
            regs[(MXU_BASE - MMIO_START + 0x08) >> 2] = 2;  // STATUS = DONE
            // 触发中断
            if (regs[(MXU_BASE - MMIO_START + 0x28) >> 2] & 1) {
                regs[(INTC_BASE - MMIO_START + 0x00) >> 2] |= 1;  // PENDING
            }
            fprintf(stderr, "[NPU] MXU START → DONE\n");
            return true;
        }

        // ── SFU CMD ─────────────────────────────────────────
        if (paddr == SFU_BASE + 0x04 && (val & 1)) {
            regs[(SFU_BASE - MMIO_START + 0x08) >> 2] = 1;
            regs[(SFU_BASE - MMIO_START + 0x08) >> 2] = 2;
            if (regs[(SFU_BASE - MMIO_START + 0x1C) >> 2] & 1) {
                regs[(INTC_BASE - MMIO_START + 0x00) >> 2] |= 2;
            }
            return true;
        }

        // ── VECTOR CMD ──────────────────────────────────────
        if (paddr == VECTOR_BASE + 0x04 && (val & 1)) {
            regs[(VECTOR_BASE - MMIO_START + 0x08) >> 2] = 1;
            regs[(VECTOR_BASE - MMIO_START + 0x08) >> 2] = 2;
            if (regs[(VECTOR_BASE - MMIO_START + 0x1C) >> 2] & 1) {
                regs[(INTC_BASE - MMIO_START + 0x00) >> 2] |= 4;
            }
            return true;
        }

        // ── DMA CMD ─────────────────────────────────────────
        if (paddr == DMA_BASE + 0x04 && (val & 1)) {
            regs[(DMA_BASE - MMIO_START + 0x08) >> 2] = 1;
            // 模拟 DMA 搬运: 如果设置了 CH0/CH1, 执行 memcpy
            do_dma_transfer();
            regs[(DMA_BASE - MMIO_START + 0x08) >> 2] = 2;
            if (regs[(DMA_BASE - MMIO_START + 0x38) >> 2] & 1) {
                regs[(INTC_BASE - MMIO_START + 0x00) >> 2] |= 8;
            }
            fprintf(stderr, "[NPU] DMA START → DONE\n");
            return true;
        }

        // ── INTC ACK ────────────────────────────────────────
        if (paddr == INTC_BASE + 0x0C) {
            regs[(INTC_BASE - MMIO_START + 0x00) >> 2] &= ~val;
            return true;
        }

        // ── Doorbell write ─────────────────────────────────
        if (paddr >= DOORBELL_BASE && paddr < DOORBELL_BASE + 0x10) {
            regs[off >> 2] = val;
            fprintf(stderr, "[NPU] DOORBELL W off=%x val=%x\n", off, val);
            return true;
        }

        // Default: store value
        regs[off >> 2] = val;
        return true;
    }

private:
    uint32_t regs[MMIO_SIZE / 4];

    void do_dma_transfer() {
        // CH0: DRAM → SRAM
        uint32_t ch0_src  = regs[(DMA_BASE - MMIO_START + 0x10) >> 2];
        uint32_t ch0_dst  = regs[(DMA_BASE - MMIO_START + 0x14) >> 2];
        uint32_t ch0_size = regs[(DMA_BASE - MMIO_START + 0x18) >> 2];

        // CH1: SRAM → DRAM
        uint32_t ch1_src  = regs[(DMA_BASE - MMIO_START + 0x20) >> 2];
        uint32_t ch1_dst  = regs[(DMA_BASE - MMIO_START + 0x24) >> 2];
        uint32_t ch1_size = regs[(DMA_BASE - MMIO_START + 0x28) >> 2];

        // Spike 通过 DTM/HTIF 访问物理内存
        // 这里通过 Spike 的 sim_t 接口做 memcpy
        // 简化: 直接通过页表访问 (Spike 提供了 memif 接口)
        // 如果无法直接访问, 跳过 DMA (不影响固件逻辑验证)

        if (ch0_size > 0 && ch0_size < (1<<20)) {
            fprintf(stderr, "[NPU] DMA CH0: 0x%08x → 0x%08x (%u B)\n",
                    ch0_src, ch0_dst, ch0_size);
            // memcpy 需要访问 Spike 的物理内存
            // 通过 sim_t->memif 实现, 此处简化
            uint8_t buf[4096];
            size_t copied = 0;
            while (copied < ch0_size) {
                size_t chunk = ch0_size - copied > 4096 ? 4096 : ch0_size - copied;
                // read from src, write to dst
                // (完整版需要 sim_t 引用, 此处验证固件逻辑即可)
                copied += chunk;
            }
        }

        if (ch1_size > 0 && ch1_size < (1<<20)) {
            fprintf(stderr, "[NPU] DMA CH1: 0x%08x → 0x%08x (%u B)\n",
                    ch1_src, ch1_dst, ch1_size);
        }
    }
};

/* ── 工厂函数 (Spike 通过 dlsym 查找) ──────────────────────────── */

extern "C" {

abstract_device_t* create_npu_device(sim_t* sim, const char* args) {
    fprintf(stderr, "[NPU] Device registered (args: %s)\n", args ? args : "");
    return new npu_device_t(sim);
}

}  // extern "C"
