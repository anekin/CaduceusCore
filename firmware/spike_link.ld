/* NPU Firmware — Linker Script for Spike simulation
 *
 * Same memory map as link.ld, but:
 *   - excludes the .data_dram section so Spike loads only the ROM/DMEM image;
 *   - ROM/DMEM are shifted up by 0x1000 because Spike refuses to load a payload
 *     ELF segment at physical address 0x0.  The --pc option is used to start
 *     execution at the shifted entry point.
 * Host-provided DRAM data (descriptors, activations, weights) is supplied via
 * ddr.bin at runtime.
 *
 * Memory map:
 *   ROM (0x0001_0000 .. 0x0001_FFFF, 64KB)     — .text + .rodata + .data LMA
 *   IBEX_DMEM (0x0002_0000 .. 0x0002_FFFF, 64KB) — .data VMA + .bss + stack
 *   SRAM  (0x2000_0000 .. 0x203F_FFFF, 4MB)    — NPU data (external)
 *   DRAM  (0x8000_0000 .. 0xFFFF_FFFF)         — Host DRAM
 */

OUTPUT_ARCH("riscv")
ENTRY(_start)

MEMORY
{
    ROM       (rx)  : ORIGIN = 0x00010000, LENGTH = 64K
    IBEX_DMEM (rw)  : ORIGIN = 0x00020000, LENGTH = 64K
}

STACK_SIZE = 16K;

SECTIONS
{
    .text : {
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

    /DISCARD/ : { *(.data_dram) *(.comment) *(.note) *(.eh_frame) }
}
