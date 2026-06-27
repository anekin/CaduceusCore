/* NPU Firmware — RISC-V startup code (unified address space)
 *
 * Bare-metal entry:
 *   1. Copy .data from ROM LMA to IBEX_DMEM VMA
 *   2. Zero BSS
 *   3. Set stack pointer (IBEX_DMEM top, grows down)
 *   4. Jump to firmware_main()
 */

    .section .text.startup, "ax"
    .globl _start
    .type _start, @function

_start:
    /* ── Step 1: Copy .data from ROM (LMA) to IBEX_DMEM (VMA) ──── */
    la   t0, _data_start     /* destination (VMA in IBEX_DMEM) */
    la   t1, _data_end       /* end of .data VMA */
    la   t2, _data_lma       /* source (LMA in ROM) */
1:
    bge  t0, t1, 2f
    lw   t3, 0(t2)           /* load from ROM */
    sw   t3, 0(t0)           /* store to DMEM */
    addi t0, t0, 4
    addi t2, t2, 4
    j    1b
2:

    /* ── Step 2: Zero BSS ───────────────────────────────────────── */
    la   t0, __bss_start
    la   t1, __bss_end
3:
    bge  t0, t1, 4f
    sw   zero, 0(t0)
    addi t0, t0, 4
    j    3b
4:

    /* ── Step 3: Set stack pointer (top of IBEX_DMEM) ───────────── */
    la   sp, _stack_top

    /* ── Step 4: Set mtvec → trap_handler (direct mode) ─────────── */
    la   t0, trap_handler
    csrw mtvec, t0

    /* ── Step 5: Enable global interrupts (MIE) ─────────────────── */
    li   t0, 0x880        /* MIE | MPIE */
    csrw mstatus, t0

    /* ── Step 6: Jump to C firmware main loop ───────────────────── */
    call firmware_main

    /* Should never return */
    j    .

/* ── Default trap handler (weak, overridable by firmware_main) ──── */

    .weak trap_handler
    .section .text.trap, "ax"
trap_handler:
    csrr t0, mcause
    bltz t0, 1f             /* cause[31]=1 → interrupt */
    j    .                  /* exception — halt for debug */
1:
    ret                     /* interrupt — return to C handler */

/* ── Data / BSS marker sections ─────────────────────────────────── */

    .section .data
    .align 4

    .section .bss
    .align 4
