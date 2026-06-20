/* NPU Firmware — Linker Script with DRAM data section
 *
 * 内存映射:
 *   SRAM 0x80000000-0x80040000 (256KB) — firmware code + stack
 *   DRAM 0x80100000+ — pre-loaded test data (.data_dram)
 *   MMIO 0x40000000+ — NPU device registers
 */

OUTPUT_ARCH("riscv")
ENTRY(_start)

MEMORY
{
    SRAM (rwx)  : ORIGIN = 0x80000000, LENGTH = 256K
    DDR  (rw)   : ORIGIN = 0x80100000, LENGTH = 256K
}

STACK_SIZE = 16K;

SECTIONS
{
    .text : {
        *(.text.startup)
        *(.text .text.*)
        *(.text.trap)
    } > SRAM

    .rodata : {
        *(.rodata .rodata.*)
    } > SRAM

    .data : {
        *(.data .data.*)
        *(.sdata .sdata.*)
    } > SRAM

    .bss : {
        __bss_start = .;
        *(.bss .bss.*)
        *(.sbss .sbss.*)
        . = ALIGN(4);
        __bss_end = .;
    } > SRAM

    /* 栈 (顶部留 16 KB) */
    . = ALIGN(16);
    . += STACK_SIZE;
    _stack_top = .;

    /* DRAM 预加载测试数据 */
    .data_dram : {
        *(.data_dram)
    } > DDR

    /DISCARD/ : { *(.comment) *(.note) *(.eh_frame) }
}
