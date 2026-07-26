#!/usr/bin/env python3
"""
Func Model 主入口 — RISC-V 固件 + MMIO Bridge + Golden Executor 集成。

Phase 2: Python 固件模拟器（riscv-gcc 就绪后切换 Spike + 真实 ELF）
"""

import hashlib
import os
from typing import Optional

import numpy as np
import struct

from sim.regmap import Addr, print_map
from sim.golden_executor import GoldenMXU, GoldenSFU, GoldenVector, GoldenDMA
from sim.mmio_bridge import MMIOBridge
from sim.miniv import RISCVMini, NPUFirmware, BOOT_ROM_SIZE, BOOT_ROM_BASE
from sim.axi_tracer import AXITracer
from models.pcie import PCIeModel, DmaEngine
from models.crossbar import CrossbarModel


class FuncModel:
    """Top-level Func Model: DRAM + SRAM + MMIO Bridge + Modules + Firmware."""

    def __init__(self, dram_mb: int = 64, sram_kb: int = 512, use_spike: Optional[bool] = None):
        # Memories
        self.dram = bytearray(dram_mb * 1024 * 1024)
        self.sram = bytearray(sram_kb * 1024)
        self.boot_rom = bytearray(BOOT_ROM_SIZE)

        self.crossbar = CrossbarModel(sram=self.sram, dram=self.dram)
        self.pcie = PCIeModel(crossbar=self.crossbar)
        self.pcie_dma = DmaEngine(crossbar=self.crossbar)

        # Compute modules
        self.mxu = GoldenMXU()
        self.sfu = GoldenSFU()
        self.vector = GoldenVector()
        self.dma_engine = GoldenDMA()

        self.bridge = MMIOBridge(modules={
            'mxu': self.mxu, 'sfu': self.sfu,
            'vector': self.vector, 'dma': self.dma_engine,
            'pcie_dma': self.pcie_dma,
            'crossbar': self.crossbar,
            'dram': self.dram, 'sram': self.sram,
        })

        sim_modules = {
            'mxu': self.mxu, 'sfu': self.sfu,
            'vector': self.vector, 'dma': self.dma_engine,
            'pcie_dma': self.pcie_dma,
            'crossbar': self.crossbar,
            'dram': self.dram, 'sram': self.sram,
        }
        self.firmware = self._create_firmware(sim_modules, self.bridge, use_spike)

        # RISC-V emulator in SoC mode (Ibex replacement)
        self.riscv = RISCVMini(
            crossbar=self.crossbar,
            sram=self.sram,
            dram=self.dram,
            boot_rom=self.boot_rom,
        )
        self.riscv.mmio_callback = self.bridge.handle
        self.riscv.irq_handler = self.firmware.dispatch_interrupt
        self.bridge.irq_notify_callback = self.riscv.set_interrupt_pending

        # Bind firmware to RISC-V emulator for interrupt-driven completion
        self.firmware.bind_riscv(self.riscv)
        self.firmware.boot(self.riscv, boot_rom_path=os.path.join(
            os.path.dirname(__file__), "..", "firmware", "build", "npu_firmware.hex",
        ))

    @staticmethod
    def _create_firmware(sim_modules: dict, bridge: MMIOBridge, use_spike: Optional[bool]) -> "NPUFirmware":
        if use_spike is None:
            env = os.environ.get("CADUCEUS_USE_SPIKE", "").lower()
            if not env:
                # No explicit preference: default to NPUFirmware. Spike is
                # opt-in via use_spike=True or CADUCEUS_USE_SPIKE=1.
                return NPUFirmware(sim_modules=sim_modules, bridge=bridge)
            use_spike = env not in ("0", "false", "no")

        if use_spike is False:
            return NPUFirmware(sim_modules=sim_modules, bridge=bridge)

        # Lazy import breaks a circular dependency with sim.spike_mmio_server.
        from sim.spike_firmware import SpikeFirmware, _spike_available

        if _spike_available():
            return SpikeFirmware(sim_modules=sim_modules, bridge=bridge)

        raise RuntimeError(
            "use_spike=True but Spike firmware artifacts are missing: "
            "build spike, plugins/npu_mmio_plugin.so, and firmware/npu_firmware.elf"
        )

    def load_boot_rom(self, path: str) -> int:
        """Load Intel HEX firmware into boot ROM.

        Returns number of bytes loaded. If path does not exist, returns 0
        without raising.
        """
        if not os.path.exists(path):
            return 0
        return RISCVMini.load_hex(path, self.boot_rom, BOOT_ROM_BASE)

    def _dram_write(self, addr: int, data: bytes):
        """Direct write to DRAM with address translation."""
        off = addr - Addr.DRAM_BASE
        self.dram[off:off + len(data)] = data

    def _dram_read(self, addr: int, size: int) -> bytes:
        """Direct read from DRAM with address translation."""
        off = addr - Addr.DRAM_BASE
        return bytes(self.dram[off:off + size])

    def host_write_command(self, opcode: int, desc_addr: int, flags: int = 0):
        """Host CPU writes a command to the Ring Buffer (via PCIe → DRAM).

        Writes at the current host_tail and advances it modulo ring_size.
        Raises RuntimeError if the ring buffer is full. Optionally raises a
        doorbell HOST interrupt so firmware can wake from WFI.
        """
        tail = self.firmware.doorbell['host_tail']
        head = self.firmware.doorbell['npu_head']
        ring_size = self.firmware.ring_size

        # Ring-full check: next position would catch up to head.
        if (tail + 1) % ring_size == head:
            raise RuntimeError(f"Doorbell ring buffer full (size={ring_size})")

        addr = self.firmware.ring_buffer_addr + tail * 32
        buf = struct.pack('<IQI8x', opcode, desc_addr, flags)  # 4+8+4+8pad=24
        self.pcie.tlp_write(addr, buf)
        new_tail = (tail + 1) % ring_size
        self.firmware.doorbell['host_tail'] = new_tail

        # Mirror host_tail to doorbell MMIO and raise HOST doorbell interrupt.
        if self.bridge:
            from sim.regmap import DOORBELL, INTC
            self.bridge.handle('write', DOORBELL.BASE + DOORBELL.HOST_TAIL, new_tail)
            self.bridge._set_irq(8)  # HOST doorbell interrupt source

    def host_write_descriptor(self, desc_addr: int, **kwargs):
        """Host writes operation descriptor to DRAM.

        Fields: input_addr, weight_addr, output_addr, scale_addr,
                input_sram, weight_sram, output_sram, scale_sram,
                input_size, weight_size, output_size, scale_size,
                M, K, N
        """
        defaults = {
            'input_addr': 0x80010000, 'weight_addr': 0x80020000,
            'output_addr': 0x81000000, 'scale_addr': 0,
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
        self.pcie.tlp_write(desc_addr, buf)

    def host_write_data(self, addr: int, data: np.ndarray):
        """Host writes tensor data to DRAM at addr."""
        self.pcie.tlp_write(addr, data.tobytes())

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
            0x80020000, 0x80010000, 0x81000000, 0x80100000)
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
    model2.host_write_data(0x80100000, np.frombuffer(sc_bytes, dtype=np.float32))
    model2.host_write_descriptor(0x80000080,
        input_addr=0x80010000, weight_addr=0x80020000, output_addr=0x81000000,
        scale_addr=0x80100000, scale_size=len(sc_bytes),
        input_size=act.nbytes, weight_size=len(wgt_bytes), output_size=M*N*4,
        M=M, K=K, N=N)
    model2.host_write_command(0, 0x80000080)
    model2.run()

    print(tracer.summary())
    print()
    for w in tracer.verify_ordering():
        print(f"  {w}")

    # Export trace
    from pathlib import Path
    trace_path = Path(__file__).resolve().parent.parent / 'traces' / 'conv2d_smoke_axi.json'
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    tracer.to_json(str(trace_path))
    print(f"\nTrace exported to {trace_path}")
    print("\nPhase 3 AXI Tracer: ✅ DONE")

    if ok:
        print("Phase 2 smoke test: ✅ PASS")
    else:
        print("Phase 2 smoke test: ❌ FAIL")


class DualPathChecker:
    """Dual-path readback verifier for blk.0 chain results.

    Reads SRAM output data via two independent paths:
      1. Backdoor (bk): direct bytearray slice of model.sram
      2. PCIe TLP (pcie): model.pcie.tlp_read() through crossbar routing

    Compares both against a golden reference and asserts they match.
    Supports anti-vacuous PCIe corruption to prove the check is genuine.

    Typical usage::

        checker = DualPathChecker(model)
        res = checker.verify(sram_offset=0x280000, size=N*4,
                             golden=np.array([...], dtype=np.int32),
                             dtype="int32")
        assert res["bk_match"]
        assert res["pcie_match"]
    """

    def __init__(self, model: FuncModel):
        self.model = model

    @staticmethod
    def _read_backdoor(model: FuncModel, sram_offset: int, size: int) -> bytes:
        """Direct SRAM bytearray slice (backdoor path)."""
        return bytes(model.sram[sram_offset:sram_offset + size])

    @staticmethod
    def _read_pcie(model: FuncModel, sram_offset: int, size: int) -> bytes:
        """PCIe TLP read through crossbar routing."""
        from sim.regmap import Addr
        phys_addr = Addr.SRAM_BASE + sram_offset
        return model.pcie.tlp_read(phys_addr, size)

    @staticmethod
    def _compare(got: bytes, golden: np.ndarray, dtype: str) -> bool:
        """Compare readback bytes against golden ndarray.

        dtype="int32" — exact comparison
        dtype="fp16"  — tolerance-based (abs=2e-3, rel=1e-2), OR logic
                     matching GoldenSFU.compare_hw_vs_ref behaviour.
        """
        if dtype == "int32":
            actual = np.frombuffer(got, dtype=np.int32)
            if actual.shape != golden.shape:
                actual = actual.reshape(golden.shape)
            return bool(np.array_equal(actual, golden))
        elif dtype == "fp16":
            actual = np.frombuffer(got, dtype=np.float16).astype(np.float32)
            golden_f32 = golden.astype(np.float32).flatten()
            if actual.shape != golden_f32.shape:
                return False
            abs_diff = np.abs(actual - golden_f32)
            rel_diff = abs_diff / (np.abs(golden_f32) + 1e-12)
            return bool(np.all(abs_diff < 2e-3) or np.all(rel_diff < 1e-2))
        else:
            raise ValueError(f"Unsupported dtype for comparison: {dtype}")

    def verify(self, sram_offset: int, size: int,
               golden: np.ndarray, dtype: str = "int32") -> dict:
        """Run dual-path readback and compare both against golden.

        Returns:
            dict with keys: bk_match, pcie_match, bk_data, pcie_data, bk_hash, pcie_hash
        """
        bk_bytes = self._read_backdoor(self.model, sram_offset, size)
        pcie_bytes = self._read_pcie(self.model, sram_offset, size)

        bk_match = self._compare(bk_bytes, golden, dtype)
        pcie_match = self._compare(pcie_bytes, golden, dtype)

        return {
            "bk_match": bk_match,
            "pcie_match": pcie_match,
            "bk_data": bk_bytes,
            "pcie_data": pcie_bytes,
            "bk_hash": hashlib.md5(bk_bytes).hexdigest()[:8],
            "pcie_hash": hashlib.md5(pcie_bytes).hexdigest()[:8],
        }

    @staticmethod
    def corrupt_pcie_read(model: FuncModel) -> None:
        """Inject corruption into the PCIe read path for anti-vacuous testing.

        Monkey-patches pcie.tlp_read so that every read returns garbage
        (byte-values shifted by +1 modulo 256).  Caller must restore the
        original method after verification.
        """
        original_read = model.pcie.tlp_read

        def corrupted_tlp_read(addr: int, size: int) -> bytes:
            real = original_read(addr, size)
            return bytes((b + 1) & 0xFF for b in real)

        model.pcie._original_tlp_read = original_read  # keep reference
        model.pcie.tlp_read = corrupted_tlp_read  # type: ignore[method-assign]

    @staticmethod
    def restore_pcie_read(model: FuncModel) -> None:
        """Restore original pcie.tlp_read after corruption injection."""
        if hasattr(model.pcie, "_original_tlp_read"):
            model.pcie.tlp_read = model.pcie._original_tlp_read  # type: ignore[method-assign]
            del model.pcie._original_tlp_read
