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
    _SRAM_SIZE = 0x40000  # 256 KB

    # ── Descriptor type check ─────────────────────────────────────
    if not isinstance(desc, dict):
        raise ValueError(
            f"descriptor must be a dict, got {type(desc).__name__}")

    # ── Required keys ─────────────────────────────────────────────
    required_keys = {'M', 'K', 'N', 'input_addr', 'input_size',
                     'weight_addr', 'scale_addr', 'output_addr'}
    missing = required_keys - desc.keys()
    if missing:
        raise ValueError(
            "descriptor missing required keys: "
            f"{', '.join(sorted(missing))}")

    # ── Shape fields (M, K, N) must be positive integers ─────────
    for field in ('M', 'K', 'N'):
        val = desc[field]
        if not isinstance(val, int) or val <= 0:
            raise ValueError(
                f"descriptor field '{field}' must be a positive "
                f"integer, got {val!r}")

    # ── Address / size fields must be non-negative integers ───────
    for field in ('input_addr', 'weight_addr', 'scale_addr',
                  'output_addr'):
        val = desc[field]
        if not isinstance(val, int) or val < 0:
            raise ValueError(
                f"descriptor field '{field}' must be a non-negative "
                f"integer address, got {val!r}")

    input_size = desc['input_size']
    if not isinstance(input_size, int) or input_size < 0:
        raise ValueError(
            "descriptor field 'input_size' must be a non-negative "
            f"integer, got {input_size!r}")

    # ── SRAM address range validation ─────────────────────────────
    sram_addrs = [0x000000, 0x010000, 0x012000, 0x014000, 0x015000, 0x018000]
    for addr in sram_addrs:
        if not (0 <= addr <= _SRAM_SIZE):
            raise ValueError(
                f"internal SRAM address 0x{addr:06x} out of range "
                f"(0-{_SRAM_SIZE})")

    # ── MMIO write failure wrapper ────────────────────────────────
    def _write(base, offset, val, label=""):
        ret = mmio_write(base, offset, val)
        if ret is False:
            raise ValueError(
                f"MMIO write failed: {label} "
                f"(base=0x{base:x}, offset=0x{offset:x})")

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
    _write(DMA_BASE, DMA.CH0_SRC, desc['input_addr'], "CH0_SRC")
    _write(DMA_BASE, DMA.CH0_DST, act_sram, "CH0_DST")
    _write(DMA_BASE, DMA.CH0_SIZE, desc['input_size'], "CH0_SIZE")
    _write(DMA_BASE, DMA.CMD, 1, "DMA_CMD")
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
            _write(DMA_BASE, DMA.CH0_SRC, desc['weight_addr'] + wgt_offset, "CH0_SRC")
            _write(DMA_BASE, DMA.CH0_DST, w_addr, "CH0_DST")
            _write(DMA_BASE, DMA.CH0_SIZE, wgt_bytes, "CH0_SIZE")
            _write(DMA_BASE, DMA.CMD, 1, "DMA_CMD")
            wait_done(DMA_BASE, DMA.STATUS)

            # ── DMA scale tile ─────────────────────────────────
            scale_offset = (n_tile * num_blocks + k_block) * TILE_SCALE_BYTES
            _write(DMA_BASE, DMA.CH0_SRC, desc['scale_addr'] + scale_offset, "CH0_SRC")
            _write(DMA_BASE, DMA.CH0_DST, s_addr, "CH0_DST")
            _write(DMA_BASE, DMA.CH0_SIZE, tile_width * 4, "CH0_SIZE")
            _write(DMA_BASE, DMA.CMD, 1, "DMA_CMD")
            wait_done(DMA_BASE, DMA.STATUS)

            # ── MXU partial compute ────────────────────────────
            act_offset = act_sram + k_start
            dim0 = (M & 0xFFFF) | ((block_height & 0xFFFF) << 16)
            ctrl_val = 4 if k_block > 0 else 0  # bit[2] = ACCUMULATE after first block

            _write(MXU_BASE, MXU.I_ADDR, act_offset, "I_ADDR")
            _write(MXU_BASE, MXU.W_ADDR, w_addr, "W_ADDR")
            _write(MXU_BASE, MXU.SCALE_ADDR, s_addr, "SCALE_ADDR")
            _write(MXU_BASE, MXU.O_ADDR, out_offset, "O_ADDR")
            _write(MXU_BASE, MXU.CTRL, ctrl_val, "CTRL")
            _write(MXU_BASE, MXU.DIM0, dim0, "DIM0")
            _write(MXU_BASE, MXU.DIM1, tile_width & 0xFFFF, "DIM1")
            _write(MXU_BASE, MXU.CMD, 1, "MXU_CMD")
            wait_done(MXU_BASE, MXU.STATUS)

        # ── DMA output tile → DRAM ────────────────────────────────
        _write(DMA_BASE, DMA.CH1_SRC, out_offset, "CH1_SRC")
        _write(DMA_BASE, DMA.CH1_DST, desc['output_addr'] + n_start * 4, "CH1_DST")
        _write(DMA_BASE, DMA.CH1_SIZE, M * tile_width * 4, "CH1_SIZE")
        _write(DMA_BASE, DMA.CMD, 1, "DMA_CMD")
        wait_done(DMA_BASE, DMA.STATUS)
