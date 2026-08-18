# Func Model SRAM Address Space Map

**Purpose:** Define the exact byte-level allocation of the NPU SRAM address
space as seen by the Func Model and the RTL SoC. RTL designers can implement
the SRAM controller address decode, engine buffer base registers, and firmware
memory map from this document without reading the Python model.

**Source files defining this spec:**
- `sim/regmap.py` — `SRAM_BASE`, `SRAM_SIZE`
- `sim/config/npu_config.yaml` — `sram.l1_per_core_kb`, `sram.l2_shared_kb`, `kv_cache.sram_kb`
- `sim/golden_executor.py` — `SRAM` class region definitions
- `sim/tile_scheduler.py` — tiled MMUL internal buffer layout
- `sim/models/kv_cache.py` — KV cache SRAM size
- `rtl/soc/README.md` — SoC unified address space
- `rtl/wrapper/mxu_soc_wrapper.v` — default wrapper base addresses

---

## 1. Top-Level Address Space

The SoC reserves a single contiguous SRAM region at the bottom of the NPU
memory map:

| Region | Start Address | End Address   | Size   | Notes |
|--------|--------------:|--------------:|-------:|-------|
| SRAM   | `0x2000_0000` | `0x203F_FFFF` | 4 MB   | Physical SoC SRAM window |

**Config parameters (from `sim/config/npu_config.yaml`):**

| Parameter | Value | Meaning |
|-----------|------:|---------|
| `sram.l1_per_core_kb` | 512 | Per-core L1 data SRAM (2 × 256 KB dual-port) |
| `sram.l2_shared_kb`   | 2048 | Shared L2 SRAM |
| `kv_cache.sram_kb`    | 256  | Per-layer KV cache window |

The Func Model currently maps engine buffers directly into the 4 MB physical
SRAM window. The table below is the canonical allocation used by the Func Model
golden reference. The L1/L2 parameters above describe the target hierarchy;
the current buffer map consumes **3.75 MB** of the 4 MB physical SRAM.

---

## 2. Engine Buffer Map (Func Model Canonical)

All addresses are byte addresses in the SoC global address space
(`SRAM_BASE + offset`).

| Region | Start Addr | End Addr | Size | Owner | R/W | Notes |
|--------|-----------:|---------:|:----:|------|:---:|:------|
| Weight Buffer Bank A (Ping) | `0x2000_0000` | `0x200F_FFFF` | 1 MB | MXU weight load | R/W | Ping buffer for weight double-buffering |
| Weight Buffer Bank B (Pong) | `0x2010_0000` | `0x201F_FFFF` | 1 MB | MXU weight load | R/W | Pong buffer for weight double-buffering |
| Activation Buffer | `0x2020_0000` | `0x2027_FFFF` | 512 KB | MXU activation load | R/W | INT8 activations / input features |
| Accumulator / Output Buffer | `0x2028_0000` | `0x202B_FFFF` | 256 KB | MXU output | R/W | INT32 partial sums and final MMUL output |
| SFU Workspace (I/O) | `0x202C_0000` | `0x202F_FFFF` | 256 KB | SFU | R/W | FP16 input/output vectors for SFU ops |
| Vector Workspace (I/O) | `0x2030_0000` | `0x2033_FFFF` | 256 KB | Vector Engine | R/W | INT32/FP16 operand and result vectors |
| Scratch / Dtype-Convert Buffer | `0x2034_0000` | `0x2037_FFFF` | 256 KB | Vector Engine | R/W | VCONV/VCONV_F16_I32 staging area |
| KV Cache Window | `0x2038_0000` | `0x203B_FFFF` | 256 KB | KV Cache | R/W | Current-layer K/V token cache |
| **Free / Unused** | `0x203C_0000` | `0x203F_FFFF` | 256 KB | — | — | Reserved for future expansion |

**Total mapped:** 3.75 MB  
**Total physical SRAM:** 4.00 MB  
**Free/unused:** 0.25 MB

---

## 3. Region Details

### 3.1 Weight Buffer — Ping-Pong Bank A / Bank B

- **Total weight SRAM:** 2 MB (2 × 1 MB).
- **Bank A:** `0x2000_0000` – `0x200F_FFFF`.
- **Bank B:** `0x2010_0000` – `0x201F_FFFF`.
- **Data format:** INT4 weights packed 2 per byte (low nibble first), or INT8
  weights 1 per byte depending on `MXU.CTRL.dtype`.
- **Owner:** MXU weight DMA load path (`DMA.CH0_DST` or `mxu_soc_wrapper`
  `WRP_WEIGHT_BASE`).
- **Usage model:** While the MXU computes using Bank A, the DMA prefetches the
  next tile into Bank B; roles swap per K-tile / N-tile.

### 3.2 Activation Buffer

- **Address:** `0x2020_0000` – `0x2027_FFFF` (512 KB).
- **Data format:** INT8 (signed), one byte per element.
- **Owner:** MXU activation load path (`MXU.I_ADDR`).
- **Capacity example:** 524,288 INT8 elements, enough for a decode token with
  `K ≤ 524288`.

### 3.3 Accumulator / Output Buffer

- **Address:** `0x2028_0000` – `0x202B_FFFF` (256 KB).
- **Data format:** INT32, little-endian, 4 bytes per element.
- **Owner:** MXU output write path (`MXU.O_ADDR`).
- **Usage:** Stores per-tile partial sums during K-tile accumulation and the
  final MMUL result before the next op (e.g., `VCONV`) consumes it.

### 3.4 SFU Workspace

- **Address:** `0x202C_0000` – `0x202F_FFFF` (256 KB).
- **Data format:** IEEE 754 FP16, 2 bytes per element.
- **Owner:** SFU (`SFU.I_ADDR`, `SFU.O_ADDR`).
- **Usage:** Input/output vectors for softmax, layernorm, gelu, silu, relu,
  rope, and rmsnorm ops.

### 3.5 Vector Workspace

- **Address:** `0x2030_0000` – `0x2033_FFFF` (256 KB).
- **Data format:** INT32 for `ADD/MUL/MAX/SUM/RESID`; FP16 for `CONV` output.
- **Owner:** Vector Engine (`VECTOR.A_ADDR`, `VECTOR.B_ADDR`, `VECTOR.O_ADDR`).
- **Usage:** Operand and result staging for vector ops.

### 3.6 Scratch / Dtype-Convert Buffer

- **Address:** `0x2034_0000` – `0x2037_FFFF` (256 KB).
- **Data format:** Mixed INT32 and FP16 depending on conversion direction.
- **Owner:** Vector Engine (`VCONV`, `VCONV_F16_I32`).
- **Usage:** The Func Model's auto dtype-converter insertion writes converted
  results here and updates the consuming op's source address
  (`scratch_base = 0x2034_0000` in `GoldenExecutor.run_op_chain`).

### 3.7 KV Cache Window

- **Address:** `0x2038_0000` – `0x203B_FFFF` (256 KB).
- **Data format:** INT8 per `kv_cache.precision_bits = 8`.
- **Owner:** KV Cache manager (`KVCacheModel`).
- **Usage:** Holds the most recent `max_sram_tokens` of K/V data for the
  current attention layer. Older tokens spill to the DRAM KV region
  (`dram_region_mb = 96`).
- **Capacity example:** For Qwen2.5-3B (`num_kv_heads=16`, `head_dim=128`,
  INT8), the window holds 256 KB / (16 × 128 × 2) ≈ **512 tokens** per layer.

### 3.8 Free / Unused Region

- **Address:** `0x203C_0000` – `0x203F_FFFF` (256 KB).
- Reserved for future expansion (larger activation/output buffers, additional
  ping-pong banks, or debug/trace buffers).

---

## 4. L1 / L2 Hierarchy vs. Physical SRAM

The YAML config describes a logical hierarchy; the Func Model maps engine
buffers into the physical 4 MB SRAM as follows:

| Hierarchy | Config Size | Mapped Regions |
|-----------|------------:|----------------|
| L2 Shared | 2048 KB | Weight Bank A (1024 KB) + Weight Bank B (1024 KB) |
| L1 Per-Core | 512 KB | Activation Buffer (512 KB) |
| KV Cache | 256 KB | KV Cache Window (256 KB) |
| Engine Scratch | 1024 KB | Accumulator (256 KB) + SFU I/O (256 KB) + Vector I/O (256 KB) + Scratch (256 KB) |
| Free | 256 KB | Free / Unused (256 KB) |
| **Total** | **4096 KB** | **Physical SRAM (4 MB)** |

> **Note to RTL implementers:** The physical SRAM controller must decode the
> full 4 MB window at `0x2000_0000`. The L1/L2 split is an implementation / QoS
> attribute; the address ranges above are the exact ranges firmware and the
> Func Model use.

---

## 5. Tile-Scheduler Internal Layout (256 KB Sub-Map)

For tile-level MMUL dispatch (`sim/tile_scheduler.py`), a smaller 256 KB SRAM
window is used inside the larger map. This is relevant when implementing the
firmware tile loop or the `mxu_soc_wrapper` preload sequencer:

| Offset | Region | Size |
|-------:|--------|-----:|
| `0x00000` | Activation tile buffer | 64 KB |
| `0x10000` | Weight tile buf0 | 8 KB |
| `0x12000` | Weight tile buf1 | 8 KB |
| `0x14000` | Scale buf0 | 512 B |
| `0x15000` | Scale buf1 | 512 B |
| `0x18000` | Output accumulator tile | ~160 KB |

These offsets are relative to the tile scheduler's local base and are **not**
the same as the global SoC SRAM addresses in Section 2. The global activation
and weight regions (Section 2) contain the data that the tile scheduler
subdivides into these local buffers.

---

## 6. Engine SoC Wrapper Default Bases

### 6.1 MXU Wrapper (`rtl/wrapper/mxu_soc_wrapper.v`)

The MXU wrapper exposes three base registers (`WRP_WEIGHT_BASE` `0x30`,
`WRP_ACT_BASE` `0x34`, `WRP_OUT_BASE` `0x38`) that anchor the AXI4 preload and
store-out sequencers. The **current RTL reset defaults** (verified against
`mxu_soc_wrapper.v`, reset block, P9-B workaround) are:

| Register | Reset Default | Purpose |
|----------|--------------:|---------|
| `WRP_WEIGHT_BASE` (`0x30`) | `0x8002_0000` | Weight tile base (perf-test DRAM layout) |
| `WRP_ACT_BASE`    (`0x34`) | `0x8001_0000` | Activation tile base (perf-test DRAM layout) |
| `WRP_OUT_BASE`    (`0x38`) | `0x8003_0000` | Output tile base (perf-test DRAM layout) |

> **Why DRAM addresses?** The reset defaults are hardcoded to the Cocotb perf
> testbench DRAM layout (`act=0x8001_0000`, `wgt=0x8002_0000`,
> `out=0x8003_0000`) as a workaround: GCC `-O2` was observed to misroute
> `WRP_WEIGHT_BASE` / `WRP_ACT_BASE` APB writes to DMA MMIO space
> (`0x4000_30xx`), so the wrapper needs correct defaults even when those writes
> never arrive. These are DRAM addresses (`0x8000_0000` base), not SRAM map
> addresses.

> **Production / spec-canonical bases:** For SRAM-backed operation, firmware
> must program these registers to the Section 2 map: weight `0x2000_0000`
> (Bank A start), activation `0x2020_0000`, output `0x2028_0000`
> (Accumulator/Output region). The registers are R/W and firmware writes
> override the reset defaults. The perf-test DRAM defaults are a Phase 10
> testbench workaround, not the canonical SRAM map.

### 6.2 Vector Wrapper (`rtl/wrapper/vector_soc_wrapper.v`)

The Vector wrapper exposes `WRP_A_BASE` `0x30`, `WRP_B_BASE` `0x34`,
`WRP_O_BASE` `0x38`. Current RTL reset defaults match the Section 2 map:

| Register | Reset Default | Purpose |
|----------|--------------:|---------|
| `WRP_A_BASE` (`0x30`) | `0x2030_0000` | Operand A base (Vector Workspace) |
| `WRP_B_BASE` (`0x34`) | `0x2030_0000` | Operand B base (contiguous after A) |
| `WRP_O_BASE` (`0x38`) | `0x2034_0000` | Output base (Scratch / Dtype-Convert) |

A and B share the Vector Workspace base; operand B follows A contiguously at
offset `length x 4` bytes, matching `GoldenExecutor` buffer allocation.
`WRP_O_BASE` defaults into the Scratch region so VCONV results land in the
dtype-convert window.

---

## 7. Alignment and Access Rules

- All engine MMIO address registers are 32-bit byte addresses. No alignment
  restriction is enforced by the MMIO slave, but AXI transfers must be
  burst-aligned (64-byte boundary recommended for 512-bit AXI data width).
- Weight INT4 data is packed 2 weights per byte, low nibble first.
- INT32 and FP16 data are little-endian.
- The SRAM controller supports 512-bit read/write bursts; byte-level writes use
  `WSTRB`.

---

## 8. Summary Table

| Region | Start | End | Size | Data Type | Owner |
|--------|------:|----:|:----:|:---------:|-------|
| Weight Bank A | `0x2000_0000` | `0x200F_FFFF` | 1 MB | INT4/INT8 | MXU |
| Weight Bank B | `0x2010_0000` | `0x201F_FFFF` | 1 MB | INT4/INT8 | MXU |
| Activation | `0x2020_0000` | `0x2027_FFFF` | 512 KB | INT8 | MXU |
| Accumulator/Output | `0x2028_0000` | `0x202B_FFFF` | 256 KB | INT32 | MXU |
| SFU I/O | `0x202C_0000` | `0x202F_FFFF` | 256 KB | FP16 | SFU |
| Vector I/O | `0x2030_0000` | `0x2033_FFFF` | 256 KB | INT32/FP16 | Vector |
| Scratch / VCONV | `0x2034_0000` | `0x2037_FFFF` | 256 KB | INT32/FP16 | Vector |
| KV Cache | `0x2038_0000` | `0x203B_FFFF` | 256 KB | INT8 | KV Cache |
| Free | `0x203C_0000` | `0x203F_FFFF` | 256 KB | — | — |

---

## 9. References

- SRAM base/size: `sim/regmap.py` (`Addr.SRAM_BASE`, `Addr.SRAM_SIZE`)
- SRAM config: `sim/config/npu_config.yaml` (`sram`, `kv_cache`)
- Func Model region definitions: `sim/golden_executor.py` (`SRAM` class, lines 1033–1106)
- Tile scheduler local layout: `sim/tile_scheduler.py` (header comment)
- KV cache sizing: `sim/models/kv_cache.py`
- SoC address map: `rtl/soc/README.md`
- MXU wrapper bases: `rtl/wrapper/mxu_soc_wrapper.v`
- Vector wrapper bases: `rtl/wrapper/vector_soc_wrapper.v`
- Perf-test DRAM base workaround: `rtl/wrapper/mxu_soc_wrapper.v` reset block (P9-B comment)
