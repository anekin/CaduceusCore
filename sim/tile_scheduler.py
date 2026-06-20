#!/usr/bin/env python3
"""
Tile-level SRAM scheduler — Weight Stationary + Double Buffer.

SRAM layout (512KB → 256KB target):
  0x00000-0x0FFFF: activation buffer (64KB, fits K≤16384 INT8)
  0x10000-0x11FFF: weight tile buf0 (8KB)
  0x12000-0x13FFF: weight tile buf1 (8KB)  
  0x14000-0x14FFF: scale buf0 (512B)
  0x15000-0x15FFF: scale buf1 (512B)
  0x18000-0x3FFFF: output accumulator (~160KB)
"""

TILE_H = 128
TILE_W = 128
TILE_WEIGHT_BYTES = TILE_H * TILE_W // 2  # 8KB INT4 packed
TILE_SCALE_BYTES = TILE_W * 4              # 512B FP32


def tile_mmul(desc: dict, mmio_write, mmio_read, wait_done,
              DMA_BASE: int, MXU_BASE: int,
              DMA, MXU):
    """Tile-level double-buffered MMUL dispatch.

    mmio_write(base, offset, value) — write to MMIO register
    mmio_read(base, offset) — read MMIO register
    wait_done(base, status_offset) — spin until STATUS & 1 == 0

    Weight layout in DRAM: tile-major.
    Tile (k_block, n_tile) at byte offset:
      (n_tile * num_blocks + k_block) * TILE_WEIGHT_BYTES (weights)
      (n_tile * num_blocks + k_block) * TILE_SCALE_BYTES (scales)
    """

    M = desc['M']
    K = desc['K']
    N = desc['N']
    num_blocks = (K + TILE_H - 1) // TILE_H
    num_tiles = (N + TILE_W - 1) // TILE_W

    act_sram = 0x000000
    wbuf = [0x010000, 0x012000]
    sbuf = [0x014000, 0x015000]
    out_sram = 0x018000

    # ── Step 1: DMA activation → SRAM (once) ─────────────────────
    mmio_write(DMA_BASE, DMA.CH0_SRC, desc['input_addr'])
    mmio_write(DMA_BASE, DMA.CH0_DST, act_sram)
    mmio_write(DMA_BASE, DMA.CH0_SIZE, desc['input_size'])
    mmio_write(DMA_BASE, DMA.CMD, 1)
    wait_done(DMA_BASE, DMA.STATUS)

    # ── Step 2: N-tile outer loop ────────────────────────────────
    for n_tile in range(num_tiles):
        n_start = n_tile * TILE_W
        n_end = min(n_start + TILE_W, N)
        tile_width = n_end - n_start
        out_offset = out_sram + n_start * 4

        for k_block in range(num_blocks):
            k_start = k_block * TILE_H
            k_end = min(k_start + TILE_H, K)
            block_height = k_end - k_start

            buf_idx = k_block % 2
            w_addr = wbuf[buf_idx]
            s_addr = sbuf[buf_idx]

            # ── DMA weight tile ────────────────────────────────
            wgt_offset = (n_tile * num_blocks + k_block) * TILE_WEIGHT_BYTES
            wgt_bytes = (block_height * tile_width + 1) // 2
            mmio_write(DMA_BASE, DMA.CH0_SRC, desc['weight_addr'] + wgt_offset)
            mmio_write(DMA_BASE, DMA.CH0_DST, w_addr)
            mmio_write(DMA_BASE, DMA.CH0_SIZE, wgt_bytes)
            mmio_write(DMA_BASE, DMA.CMD, 1)
            wait_done(DMA_BASE, DMA.STATUS)

            # ── DMA scale tile ─────────────────────────────────
            scale_offset = (n_tile * num_blocks + k_block) * TILE_SCALE_BYTES
            mmio_write(DMA_BASE, DMA.CH0_SRC, desc['scale_addr'] + scale_offset)
            mmio_write(DMA_BASE, DMA.CH0_DST, s_addr)
            mmio_write(DMA_BASE, DMA.CH0_SIZE, tile_width * 4)
            mmio_write(DMA_BASE, DMA.CMD, 1)
            wait_done(DMA_BASE, DMA.STATUS)

            # ── MXU partial compute ────────────────────────────
            act_offset = act_sram + k_start
            dim0 = (M & 0xFFFF) | ((block_height & 0xFFFF) << 16)
            ctrl_val = 4 if k_block > 0 else 0  # bit[2] = ACCUMULATE after first block

            mmio_write(MXU_BASE, MXU.I_ADDR, act_offset)
            mmio_write(MXU_BASE, MXU.W_ADDR, w_addr)
            mmio_write(MXU_BASE, MXU.SCALE_ADDR, s_addr)
            mmio_write(MXU_BASE, MXU.O_ADDR, out_offset)
            mmio_write(MXU_BASE, MXU.CTRL, ctrl_val)
            mmio_write(MXU_BASE, MXU.DIM0, dim0)
            mmio_write(MXU_BASE, MXU.DIM1, tile_width & 0xFFFF)
            mmio_write(MXU_BASE, MXU.CMD, 1)
            wait_done(MXU_BASE, MXU.STATUS)

        # ── DMA output tile → DRAM ────────────────────────────────
        mmio_write(DMA_BASE, DMA.CH1_SRC, out_offset)
        mmio_write(DMA_BASE, DMA.CH1_DST, desc['output_addr'] + n_start * 4)
        mmio_write(DMA_BASE, DMA.CH1_SIZE, M * tile_width * 4)
        mmio_write(DMA_BASE, DMA.CMD, 1)
        wait_done(DMA_BASE, DMA.STATUS)
