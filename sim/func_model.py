#!/usr/bin/env python3
"""
Func Model 主入口 — RISC-V 固件 + MMIO Bridge + Golden Executor 集成。

Phase 2: Python 固件模拟器（riscv-gcc 就绪后切换 Spike + 真实 ELF）
"""

import numpy as np
import struct

from sim.regmap import Addr, print_map
from sim.golden_executor import GoldenMXU, GoldenSFU, GoldenVector, GoldenDMA
from sim.mmio_bridge import MMIOBridge
from sim.miniv import RISCVMini, NPUFirmware
from sim.axi_tracer import AXITracer


class FuncModel:
    """Top-level Func Model: DRAM + SRAM + MMIO Bridge + Modules + Firmware."""

    def __init__(self, dram_mb: int = 64, sram_kb: int = 512):
        # Memories
        self.dram = bytearray(dram_mb * 1024 * 1024)
        self.sram = bytearray(sram_kb * 1024)

        # Compute modules
        self.mxu = GoldenMXU()
        self.sfu = GoldenSFU()
        self.vector = GoldenVector()
        self.dma_engine = GoldenDMA()

        # MMIO Bridge
        self.bridge = MMIOBridge(modules={
            'mxu': self.mxu, 'sfu': self.sfu,
            'vector': self.vector, 'dma': self.dma_engine,
            'dram': self.dram, 'sram': self.sram,
        })

        # Firmware
        self.firmware = NPUFirmware(sim_modules={
            'mxu': self.mxu, 'sfu': self.sfu,
            'vector': self.vector, 'dma': self.dma_engine,
            'dram': self.dram, 'sram': self.sram,
        }, bridge=self.bridge)

    def _dram_write(self, addr: int, data: bytes):
        """Write to DRAM with address translation."""
        off = addr - Addr.DRAM_BASE
        self.dram[off:off + len(data)] = data

    def host_write_command(self, opcode: int, desc_addr: int, flags: int = 0):
        """Host CPU writes a command to the Ring Buffer (via PCIe → DRAM)."""
        head = self.firmware.doorbell['npu_head']
        addr = self.firmware.ring_buffer_addr + head * 32
        buf = struct.pack('<IQI8x', opcode, desc_addr, flags)  # 4+8+4+8pad=24
        self._dram_write(addr, buf)
        self.firmware.doorbell['host_tail'] = (head + 1) % self.firmware.ring_size

    def host_write_descriptor(self, desc_addr: int, **kwargs):
        """Host writes operation descriptor to DRAM.

        Fields: input_addr, weight_addr, output_addr, scale_addr,
                input_sram, weight_sram, output_sram, scale_sram,
                input_size, weight_size, output_size, scale_size,
                M, K, N
        """
        defaults = {
            'input_addr': 0x80010000, 'weight_addr': 0x80020000,
            'output_addr': 0x80030000, 'scale_addr': 0,
            'input_sram': 0x00000000, 'weight_sram': 0x00400000,
            'output_sram': 0x00800000, 'scale_sram': 0x00C00000,
            'M': 4, 'K': 8, 'N': 4,
            'input_size': 32, 'weight_size': 16, 'output_size': 32,
            'scale_size': 0,
        }
        defaults.update(kwargs)

        fields = [
            defaults['input_addr'], defaults['weight_addr'],
            defaults['output_addr'], defaults['scale_addr'],
            defaults['input_sram'], defaults['weight_sram'],
            defaults['output_sram'], defaults['scale_sram'],
            defaults['input_size'], defaults['weight_size'],
            defaults['output_size'], defaults['scale_size'],
            defaults['M'], defaults['K'], defaults['N'],
        ]
        # Pack: 15 uint32 values (M, K, N are split for alignment)
        buf = struct.pack('<15I', *fields)
        self._dram_write(desc_addr, buf)

    def host_write_data(self, addr: int, data: np.ndarray):
        """Host writes tensor data to DRAM at addr."""
        self._dram_write(addr, data.tobytes())

    def run(self) -> list:
        """Run firmware dispatch loop, return results."""
        results = self.firmware.run_loop(max_commands=10)

        # Verify: read output from DRAM
        for r in results:
            if r.get('status') == 'done':
                # Read output tensor from DRAM (simplified)
                pass
        return results

    def test_conv2d_smoke(self):
        """End-to-end smoke test: Host → CMD → MXU (tile-level scheduling)."""
        from sim.quantize import quantize_int4_per_block
        from sim.tile_scheduler import TILE_H, TILE_W, TILE_WEIGHT_BYTES, TILE_SCALE_BYTES

        print("=" * 60)
        print("Func Model — Tile-Level Per-Block INT4 Smoke Test")
        print("=" * 60)

        M, K, N = 1, 256, 256  # 2 K-blocks × 2 N-tiles
        rng = np.random.RandomState(42)
        W_f32 = rng.randn(K, N).astype(np.float32) * 0.5
        act = rng.randint(-128, 128, size=M * K, dtype=np.int8).reshape(M, K)

        # Per-block quantize
        wgt_row_packed, wgt_scales, _ = quantize_int4_per_block(W_f32, 128)
        num_blocks = (K + 127) // 128
        num_tiles = (N + 127) // 128

        # Convert to tile-major layout
        wgt_tile_major = bytearray()
        scale_tile_major = bytearray()
        for n_tile in range(num_tiles):
            nc = min(128, N - n_tile * 128)
            for k_block in range(num_blocks):
                kr = min(128, K - k_block * 128)
                # Extract tile from row-major packed
                for r in range(kr):
                    k_idx = k_block * 128 + r
                    row_start = k_idx * (N // 2) + n_tile * 64
                    wgt_tile_major.extend(wgt_row_packed[row_start:row_start + nc // 2])
                # Scale tile
                sc_start = (k_block * N + n_tile * 128) * 4
                scale_tile_major.extend(wgt_scales.tobytes()[sc_start:sc_start + nc * 4])

        wgt_tile_bytes = bytes(wgt_tile_major)
        scale_tile_bytes = bytes(scale_tile_major)

        # Host writes data to DRAM
        wgt_addr, act_addr, out_addr, scale_addr = (
            0x80020000, 0x80010000, 0x80030000, 0x80040000)
        self.host_write_data(wgt_addr, np.frombuffer(wgt_tile_bytes, dtype=np.uint8))
        self.host_write_data(act_addr, act)
        self.host_write_data(scale_addr, np.frombuffer(scale_tile_bytes, dtype=np.float32))

        desc_addr = 0x80000080
        self.host_write_descriptor(desc_addr,
            input_addr=act_addr, weight_addr=wgt_addr, output_addr=out_addr,
            scale_addr=scale_addr,
            scale_size=len(scale_tile_bytes),
            input_size=act.nbytes, weight_size=len(wgt_tile_bytes),
            output_size=M * N * 4,
            M=M, K=K, N=N)
        self.host_write_command(0, desc_addr)

        results = self.run()
        print(f"  Firmware results: {results}")

        # Verify
        out_off = out_addr - Addr.DRAM_BASE
        out_bytes = self.dram[out_off:out_off + M * N * 4]
        out_fw = np.frombuffer(out_bytes, dtype=np.float32).reshape(M, N)

        golden = self.mxu.matmul_int4_per_block(act, wgt_row_packed, wgt_scales,
                                                M, K, N, group_size=128)
        match = np.allclose(out_fw, golden, rtol=1e-5)
        print(f"  Firmware output (FP32) [0,:4]: {out_fw[0,:4]}")
        print(f"  Golden   output (FP32) [0,:4]: {golden[0,:4]}")
        print(f"  Match: {'✅ PASS' if match else '❌ FAIL'}")
        print()
        return match


if __name__ == "__main__":
    print_map()
    print()

    model = FuncModel()
    ok = model.test_conv2d_smoke()

    # Phase 3: AXI Tracer (tile-level)
    from sim.quantize import quantize_int4_per_block
    from sim.tile_scheduler import TILE_WEIGHT_BYTES, TILE_SCALE_BYTES

    M, K, N = 1, 256, 256
    rng = np.random.RandomState(99)
    W_f32 = rng.randn(K, N).astype(np.float32) * 0.5
    wgt_row, wgt_scales, _ = quantize_int4_per_block(W_f32, 128)
    act = rng.randint(-128, 128, size=M*K, dtype=np.int8).reshape(M, K)
    num_blocks = (K + 127) // 128
    num_tiles = (N + 127) // 128

    # Tile-major
    wgt_tm = bytearray(); sc_tm = bytearray()
    for nt in range(num_tiles):
        nc = min(128, N - nt*128)
        for kb in range(num_blocks):
            kr = min(128, K - kb*128)
            for r in range(kr):
                rs = (kb*128 + r)*(N//2) + nt*64
                wgt_tm.extend(wgt_row[rs:rs+nc//2])
            ss = (kb*N + nt*128)*4
            sc_tm.extend(wgt_scales.tobytes()[ss:ss+nc*4])
    wgt_bytes = bytes(wgt_tm); sc_bytes = bytes(sc_tm)

    tracer = AXITracer()
    model2 = FuncModel()
    model2.bridge.tracer = tracer
    model2.bridge.clear_trace()
    tracer.clear()

    model2.host_write_data(0x80020000, np.frombuffer(wgt_bytes, dtype=np.uint8))
    model2.host_write_data(0x80010000, act)
    model2.host_write_data(0x80040000, np.frombuffer(sc_bytes, dtype=np.float32))
    model2.host_write_descriptor(0x80000080,
        input_addr=0x80010000, weight_addr=0x80020000, output_addr=0x80030000,
        scale_addr=0x80040000, scale_size=len(sc_bytes),
        input_size=act.nbytes, weight_size=len(wgt_bytes), output_size=M*N*4,
        M=M, K=K, N=N)
    model2.host_write_command(0, 0x80000080)
    model2.run()

    print(tracer.summary())
    print()
    for w in tracer.verify_ordering():
        print(f"  {w}")

    # Export trace
    tracer.to_json('/Users/zheng/npu/traces/conv2d_smoke_axi.json')
    print("\nTrace exported to traces/conv2d_smoke_axi.json")
    print("\nPhase 3 AXI Tracer: ✅ DONE")

    if ok:
        print("Phase 2 smoke test: ✅ PASS")
    else:
        print("Phase 2 smoke test: ❌ FAIL")
