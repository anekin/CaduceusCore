# NPU Firmware Makefile — unified address space (boot ROM + IBEX DMEM)
# 使用系统 riscv64-unknown-elf-gcc 交叉编译

TOOLCHAIN_DIR ?= /usr
PREFIX       ?= riscv64-unknown-elf-
CC            = $(TOOLCHAIN_DIR)/bin/$(PREFIX)gcc
OBJCOPY       = $(TOOLCHAIN_DIR)/bin/$(PREFIX)objcopy
OBJDUMP       = $(TOOLCHAIN_DIR)/bin/$(PREFIX)objdump
SIZE          = $(TOOLCHAIN_DIR)/bin/$(PREFIX)size

BUILD_DIR     = build
TARGET        = $(BUILD_DIR)/npu_firmware

# RV32IM: Integer + Multiply
ARCH          = rv32im
ABI           = ilp32

CFLAGS        = -march=$(ARCH) -mabi=$(ABI) \
                -Wall -Wextra -O2 -g3 \
                -ffreestanding -nostdlib -nodefaultlibs \
                -fno-common -fno-builtin \
                -I.

LDFLAGS        = -march=$(ARCH) -mabi=$(ABI) \
                -nostdlib -nodefaultlibs \
                -T link.ld \
                -Wl,-Map=$(TARGET).map

SRCS           = startup.S npu_firmware.c test_data.S
OBJS           = $(patsubst %.S,$(BUILD_DIR)/%.o,$(patsubst %.c,$(BUILD_DIR)/%.o,$(SRCS)))

.PHONY: all clean disasm hex

all: $(TARGET).elf $(TARGET).hex $(BUILD_DIR)/dram_init.hex

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(BUILD_DIR)/%.o: %.S | $(BUILD_DIR)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD_DIR)/%.o: %.c | $(BUILD_DIR)
	$(CC) $(CFLAGS) -c $< -o $@

$(TARGET).elf: $(OBJS) link.ld
	$(CC) $(OBJS) $(LDFLAGS) -o $@
	$(SIZE) $@

# ── Boot ROM hex: .text + .rodata + .data (≤64KB) ─────────────────
# od -t x4 produces 32-bit hex words (little-endian). awk splits into
# one word per line, compatible with Verilog $readmemh (32-bit wide).
$(TARGET).hex: $(TARGET).elf
	$(OBJCOPY) -O binary -R .data_dram $< $(TARGET).tmp.bin
	od -A n -t x4 -v $(TARGET).tmp.bin | awk '{for(i=1;i<=NF;i++) print $$i}' > $@
	rm -f $(TARGET).tmp.bin

# ── DRAM init hex: .data_dram section (loaded by host at runtime) ──
$(BUILD_DIR)/dram_init.hex: $(TARGET).elf
	$(OBJCOPY) -O binary -j .data_dram $< $(TARGET).dram.bin
	od -A n -t x4 -v $(TARGET).dram.bin | awk '{for(i=1;i<=NF;i++) print $$i}' > $@
	rm -f $(TARGET).dram.bin

disasm: $(TARGET).elf
	$(OBJDUMP) -d -S $< | less

clean:
	rm -rf $(BUILD_DIR)
