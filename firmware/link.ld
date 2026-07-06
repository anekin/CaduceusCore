/* NPU Firmware — Linker Script for unified address space
 *
 * Memory map:
 *   ROM (0x0000_0000 .. 0x0000_FFFF, 64KB)     — .text + .rodata + .data LMA
 *   IBEX_DMEM (0x0001_0000 .. 0x0001_FFFF, 64KB) — .data VMA + .bss + stack
 *   SRAM  (0x2000_0000 .. 0x203F_FFFF, 4MB)    — NPU data (external)
 *   DRAM  (0x8000_0000 .. 0xFFFF_FFFF)         — Host DRAM
 *
 * .data is loaded by startup.S: copied from ROM (LMA) to IBEX_DMEM (VMA).
 */

OUTPUT_ARCH("riscv")
ENTRY(_start)

MEMORY
{
    ROM       (rx)  : ORIGIN = 0x00000000, LENGTH = 64K
    IBEX_DMEM (rw)  : ORIGIN = 0x00010000, LENGTH = 64K
    DRAM      (rw)  : ORIGIN = 0x80000000, LENGTH = 256M
}

STACK_SIZE = 16K;

SECTIONS
{
    /* Ibex reset vector is boot_addr + 0x80; reserve the first 0x80 bytes
     * for the default vectored trap table and place _start at 0x80. */
    .vectors 0x0 : {
        FILL(0x00);
        . = 0x7c;
        LONG(0x00000000);
    } > ROM

    .text 0x80 : {
        *(.text.startup)
        *(.text .text.*)
        *(.text.trap)
    } > ROM

    .rodata : {
        *(.rodata .rodata.*)
    } > ROM

    /* .data VMA in IBEX_DMEM, LMA follows .rodata in ROM */
    .data : {
        _data_start = .;
        *(.data .data.*)
        *(.sdata .sdata.*)
        . = ALIGN(4);
        _data_end = .;
    } > IBEX_DMEM AT > ROM

    _data_lma = LOADADDR(.data);

    .bss : {
        __bss_start = .;
        *(.bss .bss.*)
        *(.sbss .sbss.*)
        . = ALIGN(4);
        __bss_end = .;
    } > IBEX_DMEM

    /* Stack — grows downward from top of IBEX_DMEM */
    . = ALIGN(16);
    . += STACK_SIZE;
    _stack_top = .;

    /* DRAM pre-loaded test data (excluded from boot ROM .hex) */
    .data_dram : {
        *(.data_dram)
    } > DRAM

    /DISCARD/ : { *(.comment) *(.note) *(.eh_frame) }
}
