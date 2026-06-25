# MXU — Matrix Multiplication Unit (Phase 1)

## Module Hierarchy

The MXU consists of **8 RTL files** (1,304 total lines). `mxu_top` integrates 5 submodules: `mmio_if`, `controller`, `mac_array`, `weight_buffer`, `activation_buffer`. The `accumulator` module lives inside `mac_array` and is not directly connected at the top level. `pe` is the leaf PE cell inside `mac_array`.

```
mxu_top.v  (314 lines) ── top-level wrapper
├── mmio_if.v         (172) ── MMIO register file (ctrl/cmd/status/dims)
├── controller.v      (329) ── tile-iteration FSM (IDLE→READ_DIMS→LOAD_W→LOAD_A→COMPUTE→STORE_OUT→DONE)
├── mac_array.v       (201) ── 64×64 PE grid with per-PE accumulation
│   ├── pe.v          ( 80) ── single MAC cell (INT4×INT8→INT32, 1-cycle pipeline)
│   └── accumulator.v (108) ── 64×64 INT32 storage for cross-tile accumulation
├── weight_buffer.v   ( 51) ── 64×64 INT4 SRAM (packed 2:1)
└── activation_buffer.v (49) ── 64×64 INT8 SRAM
```

## Module I/O Descriptions

### pe.v — Processing Element

Single pipelined MAC: `mac_out = saturate(weight × activation + acc_in)`.

| Port | Width | Direction | Description |
|------|-------|-----------|-------------|
| clk | 1 | input | Clock |
| rst_n | 1 | input | Active-low async reset |
| activation | 8 | input | Signed INT8 activation (row-broadcast) |
| weight | 4 | input | Signed INT4 weight (column-broadcast) |
| acc_in | 32 | input | Signed INT32 accumulator input (tied to 0 in 64×64 grid) |
| mac_out | 32 | output | Signed INT32 MAC output (registered, 1-cycle latency) |

### weight_buffer.v — Weight SRAM

64×64 INT4 storage (4096 weights, 2048 bytes). Packed 2:1 — low nibble = even index, high nibble = odd index. Synchronous read with 1-cycle latency.

| Port | Width | Direction | Description |
|------|-------|-----------|-------------|
| clk | 1 | input | Clock |
| rst_n | 1 | input | Active-low async reset |
| wr_en | 1 | input | Write enable |
| wr_addr | ADDR_WIDTH (10) | input | Write address |
| wr_data | DATA_WIDTH (32) | input | Write data |
| rd_en | 1 | input | Read enable |
| rd_addr | ADDR_WIDTH (10) | input | Read address |
| rd_data | DATA_WIDTH (32) | output | Read data (synchronous, 1-cycle latency) |

### activation_buffer.v — Activation SRAM

64×64 INT8 storage (4096 bytes). Synchronous read with 1-cycle latency. Same dual-port interface as weight_buffer but DEPTH=1024, ADDR_WIDTH=11.

| Port | Width | Direction | Description |
|------|-------|-----------|-------------|
| clk | 1 | input | Clock |
| rst_n | 1 | input | Active-low async reset |
| wr_en | 1 | input | Write enable |
| wr_addr | ADDR_WIDTH (11) | input | Write address |
| wr_data | DATA_WIDTH (32) | input | Write data |
| rd_en | 1 | input | Read enable |
| rd_addr | ADDR_WIDTH (11) | input | Read address |
| rd_data | DATA_WIDTH (32) | output | Read data (synchronous, 1-cycle latency) |

### accumulator.v — Accumulator Storage

64×64 INT32 storage with saturation clamping. Instantiated inside `mac_array`; not directly connected in `mxu_top`. Address format: `{row[5:0], col[5:0]}` (12-bit flattened).

| Port | Width | Direction | Description |
|------|-------|-----------|-------------|
| clk | 1 | input | Clock |
| rst_n | 1 | input | Active-low async reset |
| addr | 12 | input | Flattened address {row, col} |
| acc_in | 32 | input | Signed INT32 partial sum |
| acc_out | 32 | output | Signed INT32 registered output |
| accumulate | 1 | input | Add acc_in to stored value with saturation |
| read_out | 1 | input | Output stored value to acc_out (next cycle) |
| reset_cmd | 1 | input | Clear stored value to 0 |

### mac_array.v — 64×64 MAC Array

64×64 PE grid with per-PE accumulator registers. Broadcast scheme: weight bus `[4*c +: 4]` to column `c`, activation bus `[8*r +: 8]` to row `r`. Accumulation uses `local_acc <= local_acc + pe_d1` with 2-cycle feedback timing.

| Port | Width | Direction | Description |
|------|-------|-----------|-------------|
| clk | 1 | input | Clock |
| rst_n | 1 | input | Active-low async reset |
| weight_bus | 256 | input | 64×INT4 weight broadcast (4 bits per column) |
| activation_bus | 512 | input | 64×INT8 activation broadcast (8 bits per row) |
| compute_en | 1 | input | PE compute strobe |
| reset_acc | 1 | input | Synchronous clear of all per-PE accumulators |
| read_out | 1 | input | Output selected row's accumulator values |
| row_addr | 6 | input | Row to read (0..63) |
| acc_load | 1 | input | Load acc_in_bus into selected row |
| acc_in_bus | 2048 | input | 64×INT32 load data for selected row |
| acc_out_bus | 2048 | output | 64×INT32 result for selected row |
| ext_acc_* | various | input | External accumulator module access (controller multi-tile) |

### mmio_if.v — MMIO Register File

MMIO slave matching `CaduceusCore/sim/regmap.py`. Register map within 4KB window:

| Offset | Name | Access | Fields |
|--------|------|--------|--------|
| 0x00 | CTRL | R/W | [1:0]=dtype (0=INT4×INT8) |
| 0x04 | CMD | W | [0]=START, [1]=ABORT (write-only, single-cycle pulses) |
| 0x08 | STATUS | R | [0]=BUSY, [1]=DONE, [2]=ERROR |
| 0x0C | DIM0 | R/W | [15:0]=M, [31:16]=K |
| 0x10 | DIM1 | R/W | [15:0]=N |
| 0x14 | I_ADDR | R/W | Activation SRAM address |
| 0x18 | W_ADDR | R/W | Weight SRAM address |
| 0x1C | O_ADDR | R/W | Output SRAM address |
| 0x20 | BIAS_ADDR | R/W | Bias SRAM address |
| 0x24 | SCALE_ADDR | R/W | Scale SRAM address |
| 0x28 | IRQ_EN | R/W | [0]=completion irq enable |

| Port | Width | Direction | Description |
|------|-------|-----------|-------------|
| clk | 1 | input | Clock |
| rst_n | 1 | input | Active-low async reset |
| cs | 1 | input | Chip-select |
| we | 1 | input | Write-enable (1=write, 0=read) |
| addr | 12 | input | Byte offset within 4KB |
| wdata | 32 | input | Write data |
| rdata | 32 | output | Read data (combinatorial) |
| ready | 1 | output | Transaction accepted |
| status_* | 1 each | input | External status (from controller) |
| cmd_start/abort | 1 each | output | Single-cycle pulses on CMD write |
| dim0_m / dim0_k | 16 each | output | Dimension registers |
| dim1_n | 16 | output | N dimension |

### controller.v — Tile-Iteration FSM

Single unified always-block FSM managing N/M/K tile iteration. Hardware constraint: `MAX_TILE=64`.

| Port | Width | Direction | Description |
|------|-------|-----------|-------------|
| clk | 1 | input | Clock |
| rst_n | 1 | input | Active-low async reset |
| cmd_start/abort | 1 each | input | From mmio_if |
| dim0_m, dim0_k, dim1_n | 16 each | input | From mmio_if |
| irq_en | 1 | input | Interrupt enable |
| status_busy/done/error | 1 each | output | Status register inputs |
| irq | 1 | output | Interrupt request |
| weight_load_en | 1 | output | Weight load strobe |
| activation_load_en | 1 | output | Activation load strobe |
| compute_en | 1 | output | MAC array compute strobe |
| compute_k | 6 | output | K tile elements (0 = full 64) |
| mac_reset_acc | 1 | output | Reset accumulators |
| store_out | 1 | output | Store output strobe |
| store_row | 6 | output | Row address during store |
| state | 4 | output | Current FSM state (debug) |
| tiles_completed | 16 | output | Tiles processed (debug) |

FSM: `IDLE → READ_DIMS → LOAD_W → LOAD_A → COMPUTE → STORE_OUT → (tile loop) → DONE`
Tile order: inner K-tile (accumulate) → middle N-tile → outer M-tile.

### mxu_top.v — Top-Level Integration

Integrates all 5 submodules and implements output SRAM serialization (2048-bit row → 32-bit sram_wdata).

| Port | Width | Direction | Description |
|------|-------|-----------|-------------|
| clk | 1 | input | Clock |
| rst_n | 1 | input | Active-low async reset |
| cs, we, addr(12), wdata(32) | - | input | MMIO slave interface |
| rdata | 32 | output | MMIO read data |
| ready | 1 | output | MMIO ready |
| sram_rdata | 32 | input | Shared SRAM read data |
| weight_sram_addr/wr_en/rd_en | - | output | Weight SRAM interface |
| activation_sram_addr/wr_en/rd_en | - | output | Activation SRAM interface |
| output_sram_addr/wr_en/wdata | - | output | Output SRAM interface (32-bit serialized) |
| irq | 1 | output | Interrupt |
| weight_bus_i | 256 | input | MAC array weight broadcast bus |
| activation_bus_i | 511 | input | MAC array activation broadcast bus |
| acc_out_bus_o | 2048 | output | MAC array full-row output |
| state / compute_en_o / etc. | - | output | Debug status outputs |

## How to Run

### Prerequisites

VCS W-2024.09-SP2 is available on the EDA server (192.168.0.11). All simulation commands run via SSH.

```bash
export USER=zhengs
export SERVER=192.168.0.11
export VCS_ENV="source /NAS/Tools/methodology/modules/init/bash && module load vcs/vcs_vW-2024.09-SP2_P"
```

### Step 1: Generate Test Vectors

```bash
python3 CaduceusCore/scripts/gen_mxu_vectors.py --scenario all \
    --out-dir CaduceusCore/rtl/test_vectors/mxu
```

This generates 9 named scenario directories + 100 random cases under `CaduceusCore/rtl/test_vectors/mxu/`.

### Step 2: Compile VCS Simulator

```bash
ssh zhengs@192.168.0.11 "cd /home/prj/zhengs/caduceuscore && \
  $VCS_ENV && \
  vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -top tb_mxu \
      CaduceusCore/rtl/tb/tb_mxu.v CaduceusCore/rtl/mxu/*.v \
      -o simv_mxu -l CaduceusCore/rtl/results/vcs_compile_tb_mxu.log"
```

### Step 3: Simulate a Single Scenario

```bash
SCENARIO=single_tile
TESTDIR=CaduceusCore/rtl/test_vectors/mxu/$SCENARIO

ssh zhengs@192.168.0.11 "cd /home/prj/zhengs/caduceuscore && \
  $VCS_ENV && \
  ./simv_mxu +testdir=$TESTDIR +scenario=$SCENARIO \
      -l CaduceusCore/rtl/results/vcs_sim_${SCENARIO}.log"
```

### Step 4: Compare RTL Output to Golden

```bash
python3 CaduceusCore/sim/compare_rtl.py \
    CaduceusCore/rtl/test_vectors/mxu/$SCENARIO \
    CaduceusCore/rtl/results/mxu_${SCENARIO}.hex
```

### Batch Regression

```bash
# Run all 9 named scenarios
for s in single_tile multi_tile_K multi_tile_N multi_tile_M \
         overflow zero_dim partial_tile_K partial_tile_N partial_tile_M; do
  ./simv_mxu +testdir=CaduceusCore/rtl/test_vectors/mxu/$s +scenario=$s \
      -l CaduceusCore/rtl/results/vcs_sim_$s.log
  cp CaduceusCore/rtl/results/mxu_$s.hex \
     CaduceusCore/rtl/test_vectors/mxu/$s/result.hex
  python3 CaduceusCore/sim/compare_rtl.py CaduceusCore/rtl/test_vectors/mxu/$s
done

# Run 100 random cases (parallel)
for i in $(seq -f '%03g' 0 99); do
  ./simv_mxu -no_save \
      +testdir=CaduceusCore/rtl/test_vectors/mxu/random_regression/random_$i \
      +scenario=random_$i \
      -l CaduceusCore/rtl/results/vcs_sim_random_$i.log
  cp CaduceusCore/rtl/results/mxu_random_$i.hex \
     CaduceusCore/rtl/test_vectors/mxu/random_regression/random_$i/result.hex
done
python3 CaduceusCore/sim/compare_rtl.py --batch \
    CaduceusCore/rtl/test_vectors/mxu/random_regression
```

## Verification Results

### Single Tile (64×64)
4096/4096 INT32 values bit-exact against GoldenMXU. **PASSED**.

### Named Scenarios (9/9 PASSED)

| Scenario | Golden Shape | Key Verification Point |
|----------|-------------|----------------------|
| single_tile | (64, 64) | Basic single-block functionality |
| multi_tile_K | (64, 64) | K-dim accumulation across 4 tiles (K=256) |
| multi_tile_N | (64, 128) | N-dim traversal, multi-block output concatenation |
| multi_tile_M | (128, 64) | Batch-dim traversal (M=128) |
| overflow | (64, 64) | INT32 saturation clamping (extreme INT4/INT8 values) |
| zero_dim | (0, 64) | Zero-dimension boundary (M=0) |
| partial_tile_K | (64, 64) | Non-power-of-2 K remaining |
| partial_tile_N | (64, 33) | Non-power-of-2 N dimension |
| partial_tile_M | (33, 64) | Non-power-of-2 M dimension |

### Random Regression (100/100 PASSED)
100 random (M, N, K) combinations with full range of dimensions. All passed via `compare_rtl.py --batch`.

### Qwen2.5-3B E2E (PASSED)
Real Qwen2.5-3B-Instruct Q4_K_M weights from `blk.0.attn_q.weight` (K=2048, N=2048, M=1). Simulation: 32 K-tiles × 32 N-tiles = 1024 compute phases, ~44 CPU seconds. Shape: golden=(1, 2048), result=(1, 2048). **PASSED**.

### Pytest Regression
210/210 pytest tests pass (150 sim + 60 timing).

## Key Scripts

| Script | Purpose |
|--------|---------|
| `CaduceusCore/scripts/gen_mxu_vectors.py` | Generate test vectors (weights, activations, golden output) for MXU scenarios. Supports `--scenario all` for full batch. |
| `CaduceusCore/sim/compare_rtl.py` | Compare RTL simulation output hex file against golden output. Supports single-scenario and `--batch` modes. |

## Known Deviations from Original Plan

1. **Qwen2.5-3B dimensions**: The original plan assumed K=2560, N=4096 for Qwen2.5-3B Q_proj. Actual dimensions from `blk.0.attn_q.weight` in GGUF Q4_K_M format are K=2048, N=2048 (hidden_size=2048).
2. **M=1 decode scenario**: The single-row (M=1) case is functionally correct but uses only PE row 0. Rows 1..63 receive uninitialized activation data that does not affect the output.
3. **gen_mxu_vectors.py vs gen_rtl_tests.py**: Phase 1 uses `scripts/gen_mxu_vectors.py` (not `sim/gen_rtl_tests.py` from the original plan) for test vector generation, because the test vector format and scenario generation differ from the original plan.
