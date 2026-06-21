# Spike RISC-V Firmware Integration

## Overview

This document describes how to build and run the Spike RISC-V simulator with the NPU firmware for MMIO-based integration testing. The setup uses a custom Spike MMIO plugin that forwards NPU register accesses to a Python bridge server over a Unix domain socket, enabling end-to-end verification of the firmware against golden model references.

## Repository

Spike is the official RISC-V ISA simulator from `riscv-software-src/riscv-isa-sim`.

| Item | Value |
|------|-------|
| Repository | `https://github.com/riscv-software-src/riscv-isa-sim` |
| Commit | `1280c3ca1ef0ce5ec95994cb9b7144f3ea2c655c` |
| Branch | `master` |
| Local path | `CaduceusCore/spike_src/` |

Clone with minimal history:

```
git clone --depth 1 https://github.com/riscv-software-src/riscv-isa-sim spike_src
```

## Build Spike

Spike uses a standard autotools build system. A separate `build/` directory keeps build artifacts isolated from the source tree.

```
cd spike_src/
mkdir -p build && cd build
../configure --prefix=$(pwd)
make -j$(nproc)
```

The Spike binary is produced at `spike_src/build/spike` (a 64-bit ELF with debug info).

## dtc (Device Tree Compiler) Dependency

Spike generates a Flattened Device Tree (FDT) at startup for device enumeration. The `dtc` binary must be in `PATH` at runtime or Spike will fail.

### Build dtc

```
cd CaduceusCore/
git clone https://github.com/dgibson/dtc.git dtc_src
cd dtc_src && make
```

This produces `dtc_src/dtc` (version 1.8.1 or later).

### Runtime PATH

The `dtc_src/` directory must be on `PATH` before launching Spike:

```
export PATH=/home/prj/zhengs/caduceuscore/dtc_src:$PATH
```

The Python host adapter (`sim/spike_host.py`) handles this automatically by prepending the path.

## Memory Map

The NPU system uses a flat memory map with DRAM for code and data, SRAM for firmware scratch space, and per-engine MMIO regions in a 4 KB aperture per block.

| Region | Address | Size |
|--------|---------|------|
| DRAM | `0x80000000` | 256 MB |
| SRAM | `0x20000000` | 4 MB |
| MXU | `0x40000000` | 4 KB |
| SFU | `0x40001000` | 4 KB |
| VECTOR | `0x40002000` | 4 KB |
| DMA | `0x40003000` | 4 KB |
| Doorbell | `0x40010000` | 4 KB |
| INTC | `0x40011000` | 4 KB |

The DRAM region is `0x80000000` to `0x8FFFFFFF` (256 MB). The SRAM region is `0x20000000` to `0x203FFFFF` (4 MB). Each NPU engine block (MXU, SFU, VECTOR, DMA) plus Doorbell and INTC gets a dedicated 4 KB page starting at the listed base address.

## Firmware Compilation

The NPU firmware is a bare-metal RISC-V program compiled with the system `riscv64-unknown-elf-gcc` toolchain. There is no HTIF support (no `tohost`/`fromhost` symbols) -- the firmware is entirely self-contained and uses the MMIO doorbell register for host synchronization.

### Toolchain

| Setting | Value |
|---------|-------|
| CC | `/usr/bin/riscv64-unknown-elf-gcc` |
| ARCH | `rv32im` |
| ABI | `ilp32` |

The `rv32im` target includes base integer instructions plus the M (multiply) extension. Floating-point and atomics are not used. The firmware is position-independent and uses a custom linker script (`firmware/link.ld`).

### Build

```
make -C firmware
```

This produces `firmware/build/npu_firmware.elf` (ELF) and `firmware/build/npu_firmware.bin` (raw binary). The `.bin` file is used by Spike with the `--kernel` option, while the `.elf` is the final target passed as the program argument.

Key compilation flags:

```
-march=rv32im -mabi=ilp32 -O2 -g3 -ffreestanding -nostdlib -nodefaultlibs
```

## Plugin Build

The MMIO plugin is a shared object that Spike loads at runtime via `--extlib`. It intercepts load/store operations in the NPU address range and forwards them to the Python MMIO bridge server.

```
make -C spike_src/plugins npu_mmio_plugin.so
```

This produces `spike_src/plugins/npu_mmio_plugin.so`. A copy deployed to `plugins/npu_mmio_plugin.so` at the project root is what the host adapter references.

## MMIO Protocol

The Spike plugin communicates with the Python bridge server over a Unix domain socket at `/tmp/npu_mmio.sock`. The protocol is a simple text-based request-response:

### Requests

| Direction | Format | Response | Description |
|-----------|--------|----------|-------------|
| Read | `R 0xADDR\n` | `0xVALUE\n` | Read 32-bit value from address `ADDR` |
| Write | `W 0xADDR 0xVALUE\n` | `OK\n` | Write 32-bit `VALUE` to address `ADDR` |

Both addresses and values are hexadecimal integers with `0x` prefix. Responses end with a newline. On protocol errors, the server returns `ERR <reason>\n`.

### Address Normalization

Addresses in the firmware SRAM range (`0x20000000` to `0x203FFFFF`) are normalized by the server: the firmware base `0x20000000` is subtracted, converting them to Python-side SRAM offsets (starting at offset `0x00000000`). NPU register addresses (`0x40000000` and above) are passed through unchanged because the register map already uses absolute bases.

## Run Command

The full Spike invocation that the host adapter uses:

```
/home/prj/zhengs/caduceuscore/spike_src/build/spike \
  --isa=RV32IM \
  -m0x80000000:0x10000000,0x20000000:0x400000 \
  --kernel=ddr.bin \
  --extlib=plugins/npu_mmio_plugin.so \
  --device=npu,0x20000000 \
  firmware/build/npu_firmware.elf
```

### Argument Breakdown

| Argument | Purpose |
|----------|---------|
| `--isa=RV32IM` | 32-bit RISC-V with M extension only |
| `-m0x80000000:0x10000000,0x20000000:0x400000` | Memory map: 256 MB DRAM at `0x80000000`, 4 MB SRAM at `0x20000000` |
| `--kernel=ddr.bin` | Preload DRAM with the serialized binary image (weights, activations, descriptors) |
| `--extlib=plugins/npu_mmio_plugin.so` | Load the custom MMIO plugin |
| `--device=npu,0x20000000` | Register the NPU device at SRAM base; the plugin intercepts all loads/stores in the `0x20000000-0x40011FFF` range |
| `firmware/build/npu_firmware.elf` | The firmware ELF to execute |

## Host Adapter

The Python host adapter (`sim/spike_host.py`) orchestrates the full workflow: preparing DRAM data, launching the MMIO server, running Spike, and verifying results.

### Usage

```
PYTHONPATH=sim python3 sim/spike_host.py \
  --model /path/to/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  --layers 2 \
  --ops Q_proj,K_proj,V_proj
```

Arguments:

| Argument | Default | Description |
|----------|---------|-------------|
| `--model` | `~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf` | Path to GGUF model file |
| `--layers` | `2` | Number of transformer layers to test |
| `--ops` | `Q_proj,K_proj,V_proj` | Comma-separated list of attention projection ops |

### Workflow

1. **Weight extraction**: Loads GGUF weights via `q4_dequant.load_weights_from_gguf()`.
2. **Quantization**: Converts FP32 weights to blockwise INT4 with per-block scales.
3. **DRAM preparation**: Writes the combined weight blob, random activations, and an MMUL descriptor into a `FuncModel` DRAM buffer.
4. **Descriptor write**: The `mmul_desc_t` (12-field packed struct) encodes input, weight, output addresses in both DRAM and SRAM along with matrix dimensions `M`, `K`, `N`.
5. **Doorbell signal**: Sets `HOST_TAIL = 1` via the MMIO bridge. This signals the firmware to start processing.
6. **MMIO server**: Starts `sim/spike_mmio_server.py` as a background thread listening on `/tmp/npu_mmio.sock`.
7. **DRAM serialization**: Writes the complete `FuncModel.dram` buffer to `ddr.bin` (64 MB).
8. **Spike launch**: Runs the Spike command with `--kernel=ddr.bin` and the firmware ELF.
9. **Poll for completion**: Polls the `NPU_HEAD` doorbell register every 50 ms. When `NPU_HEAD` equals the expected command count (mod 64), the firmware has consumed the command.
10. **Output verification**: Reads the output tensor from DRAM and compares against `GoldenMXU.matmul_int4_per_block()` reference using `np.allclose()` with `rtol=1e-5`.
11. **Cleanup**: Terminates Spike, shuts down the MMIO server, removes the socket file.

### Completion Mechanism

The completion handshake uses two doorbell registers:

- **Host writes** `HOST_TAIL` register at `0x40010000` to signal a new command is available.
- **Firmware polls** `HOST_TAIL` in its main loop, processes the MMUL descriptor, and writes back `NPU_HEAD` at `0x40010004` with the consumed command count.
- **Host polls** `NPU_HEAD` until it matches the expected value, then reads the output from DRAM.

Once all commands are consumed (i.e., `NPU_HEAD` reaches the target), the host adapter terminates Spike.

### SRAM Address Handling

The firmware references the SRAM area at absolute address `0x20000000` (the `NPU_SRAM_BASE`). The Python bridge server normalizes these addresses by subtracting `0x20000000`, converting them to a zero-based offset into the FuncModel's internal SRAM buffer. NPU register accesses at `0x40000000` and above pass through without modification.

## E2E Smoke Test

A verified smoke test runs 6 ops (2 layers x 3 projections) against the `qwen2.5-1.5b-instruct-q4_k_m.gguf` model. The expected result is 6/6 PASS:

```
PYTHONPATH=sim python3 sim/spike_host.py \
  --model ~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  --layers 2 \
  --ops Q_proj,K_proj,V_proj
```

Expected output:

```
======================================================================
Spike Host: qwen2.5-1.5b-instruct-q4_k_m.gguf  layers=2  ops=['Q_proj', 'K_proj', 'V_proj']
======================================================================
  [PASS] L0 Q_proj    (4096x4096)
  [PASS] L0 K_proj    (4096x1024)
  [PASS] L0 V_proj    (4096x1024)
  [PASS] L1 Q_proj    (4096x4096)
  [PASS] L1 K_proj    (4096x1024)
  [PASS] L1 V_proj    (4096x1024)

======================================================================
Spike Host Summary: 6 PASS, 0 FAIL
======================================================================
```

## Known Limitations

| Limitation | Detail |
|------------|--------|
| `--enable-commitlog` unavailable | This Spike build option is not enabled. Use `--log-commits` for commit logging instead. |
| No HTIF | The firmware is bare-metal with no HTIF console. The `tohost`/`fromhost` warning from Spike is expected and harmless. |
| Weight blob placement | The combined weight blob (packed INT4 + scales) must be placed above the ring buffer. The working address is `0x80200000` (2 MB into DRAM start). |
| Model reload per op | Each op reloads weights from the GGUF file into the DRAM image. This is acceptable for testing a few ops but becomes a bottleneck for full-layer runs. |
| Polling-based completion | The host adapter uses 50 ms polling on `NPU_HEAD` rather than interrupt-driven notification. |
| Unix socket serialization | All MMIO accesses go through a single Unix socket, introducing latency per load/store. |

## Future Work

| Item | Description |
|------|-------------|
| AXI fabric | Replace the MMIO plugin with an AXI bus model for more realistic memory access patterns |
| RTL co-simulation | Drive the same firmware against an RTL model of the NPU via the MMIO bridge |
| Interrupt-driven completion | Wire the INTC module (at `0x40011000`) to signal completion via RISC-V interrupts instead of polling |
| Batch model loading | Hold the decompressed model in memory across ops to avoid per-op GGUF reload |
| Multiple command descriptors | Extend the ring buffer to support multiple outstanding MMUL descriptors |
