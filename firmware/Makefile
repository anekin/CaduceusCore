# NPU Firmware Makefile
# 使用 riscv-none-elf-gcc (xPack) 交叉编译

TOOLCHAIN_DIR ?= $(HOME)/tools/xpack-riscv-none-elf-gcc-15.2.0-1
PREFIX       ?= riscv-none-elf-
CC            = $(TOOLCHAIN_DIR)/bin/$(PREFIX)gcc
OBJCOPY       = $(TOOLCHAIN_DIR)/bin/$(PREFIX)objcopy
OBJDUMP       = $(TOOLCHAIN_DIR)/bin/$(PREFIX)objdump
SIZE          = $(TOOLCHAIN_DIR)/bin/$(PREFIX)size

BUILD_DIR     = build
TARGET        = $(BUILD_DIR)/npu_firmware

# RV32IM: Integer + Multiply
ARCH          = rv32im_zicsr
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

all: $(TARGET).elf $(TARGET).bin $(TARGET).hex

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(BUILD_DIR)/%.o: %.S | $(BUILD_DIR)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD_DIR)/%.o: %.c | $(BUILD_DIR)
	$(CC) $(CFLAGS) -c $< -o $@

$(TARGET).elf: $(OBJS) link.ld
	$(CC) $(OBJS) $(LDFLAGS) -o $@
	$(SIZE) $@

$(TARGET).bin: $(TARGET).elf
	$(OBJCOPY) -O binary $< $@

$(TARGET).hex: $(TARGET).bin
	xxd -g 1 $< | cut -d' ' -f2-17 | tr -d ' \n' > $@

disasm: $(TARGET).elf
	$(OBJDUMP) -d -S $< | less

clean:
	rm -rf $(BUILD_DIR)
