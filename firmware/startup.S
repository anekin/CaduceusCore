/* NPU Firmware — RISC-V startup code
 *
 * Bare-metal entry: zero BSS, set stack pointer, jump to C.
 */

    .section .text.startup, "ax"
    .globl _start
    .type _start, @function

_start:
    /* 设置栈指针 — 栈从 SRAM 顶部向下生长 */
    la   sp, _stack_top

    /* 清零 BSS */
    la   t0, __bss_start
    la   t1, __bss_end
1:
    bge  t0, t1, 2f
    sw   zero, 0(t0)
    addi t0, t0, 4
    j    1b
2:

    /* 设置 mtvec → trap_handler (直接模式) */
    la   t0, trap_handler
    csrw mtvec, t0

    /* 使能全局中断 (MIE) */
    li   t0, 0x880        /* MIE | MPIE */
    csrw mstatus, t0

    /* 跳转到 C 固件主循环 */
    call firmware_main

    /* 不应返回 */
    j    .

/* ── 默认陷阱处理 (弱引用, 可被 firmware_main 覆盖) ─────────────── */

    .weak trap_handler
    .section .text.trap, "ax"
trap_handler:
    /* 简单实现: 读 mcause, ACK 中断 */
    csrr t0, mcause
    /* 如果 cause[31]=1 → 中断; 否则 → 异常 */
    bltz t0, 1f
    /* 异常 — 停在这里 (调试用) */
    j    .
1:
    /* 中断 — 调用 C handler 返回 */
    ret

/* ── 数据段 ─────────────────────────────────────────────────────── */

    .section .data
    .align 4

/* ── BSS 段标记 ─────────────────────────────────────────────────── */

    .section .bss
    .align 4
