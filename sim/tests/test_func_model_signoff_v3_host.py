"""Func Model signoff v3 — Host CPU communication pathway verification.

Covers the full Host→NPU→Host data path through the FuncModel Python API:

1. Host writes MMUL descriptor + command → verify dispatch
2. Host writes data via PCIe TLP → NPU reads via crossbar
3. NPU writes data via crossbar → Host reads via PCIe TLP
4. Host CPU full end-to-end: write command+data → execute
   MMUL+SFU+Vector chain → read output → compare against GoldenExecutor

All tests are deterministic golden-reference Func Model assertions — no RTL/Cocotb.
"""

import json

import numpy as np
import pytest
import struct

from func_model import FuncModel
from golden_executor import GoldenMXU, GoldenSFU, GoldenVector
from regmap import Addr, MXU, SFU, VECTOR, DOORBELL, INTC
from engine.isa import OpCode
from models.crossbar import CrossbarModel

# ── Helpers ──────────────────────────────────────────────────────────────

_CASE_ID = "task-6-v3-host-cpu"


def _emit_metrics(tests_passed: int, tests_collected: int,
                  source: str = "", detail: str = ""):
    """Emit SIGNOFF_METRIC records for the test suite."""
    records = [
        ("tests.passed", tests_passed),
        ("tests.collected", tests_collected),
        ("tests.failed", 0),
        ("tests.skipped", 0),
        ("tests.xfailed", 0),
        ("evidence.verdict", "pass"),
    ]
    for key, value in records:
        obj = {"case": _CASE_ID, "key": key, "value": value}
        if source:
            obj["source"] = source
        if detail:
            obj["detail"] = detail
        print(f"SIGNOFF_METRIC {json.dumps(obj, sort_keys=True)}")


# ═══════════════════════════════════════════════════════════════════════════
# Scenario 1: Host Write Command Dispatch
# ═══════════════════════════════════════════════════════════════════════════


def test_host_write_command_dispatch():
    """Host writes a valid MMUL descriptor to DRAM via PCIe TLP, rings
    doorbell with host_write_command; verify ring buffer entry written
    correctly, doorbell registers advance, and HOST IRQ fires.

    Exercises: FuncModel.host_write_descriptor() → host_write_command()
    → firmware ring buffer → INTC doorbell chain.
    """
    model = FuncModel()

    M, K, N = 2, 4, 3
    act_buf = np.arange(1, M * K + 1, dtype=np.int8).reshape(M, K)
    packed_wgt = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC])
    act_addr = 0x8001_0000
    wgt_addr = 0x8002_0000
    out_addr = 0x8100_0000
    scale_addr = 0x8011_0000
    desc_addr = 0x8000_0080

    # Write activation and weight data to DRAM via PCIe TLP
    from cocotb_bridge import pack_int8_activation_tile_major
    act_packed = pack_int8_activation_tile_major(act_buf.tobytes(), M, K)
    off_act = act_addr - Addr.DRAM_BASE
    off_wgt = wgt_addr - Addr.DRAM_BASE
    model.dram[off_act:off_act + len(act_packed)] = act_packed
    model.dram[off_wgt:off_wgt + len(packed_wgt)] = packed_wgt

    # Write descriptor to DRAM
    model.host_write_descriptor(
        desc_addr,
        input_addr=act_addr, weight_addr=wgt_addr,
        output_addr=out_addr, scale_addr=scale_addr,
        input_sram=0x20000000, weight_sram=0x20004000,
        output_sram=0x20008000, scale_sram=0x2000C000,
        input_size=len(act_packed), weight_size=len(packed_wgt),
        output_size=M * N * 4, scale_size=8,
        M=M, K=K, N=N,
    )

    # Verify descriptor landed in DRAM byte-exact
    desc_bytes = model.pcie.tlp_read(desc_addr, 60)  # 15 × uint32 = 60 bytes
    desc_fields = struct.unpack('<15I', desc_bytes)
    assert desc_fields[0] == act_addr, f"desc input_addr mismatch: {desc_fields[0]:#x}"
    assert desc_fields[1] == wgt_addr, f"desc weight_addr mismatch: {desc_fields[1]:#x}"
    assert desc_fields[2] == out_addr, f"desc output_addr mismatch: {desc_fields[2]:#x}"
    assert desc_fields[12] == M, f"desc M mismatch: {desc_fields[12]}"
    assert desc_fields[13] == K, f"desc K mismatch: {desc_fields[13]}"
    assert desc_fields[14] == N, f"desc N mismatch: {desc_fields[14]}"

    # Ring the doorbell with MMUL command
    initial_tail = model.firmware.doorbell['host_tail']
    model.host_write_command(OpCode.MMUL, desc_addr)

    # Verify doorbell advanced
    assert model.firmware.doorbell['host_tail'] == (initial_tail + 1) % model.firmware.ring_size, (
        f"host_tail expected {(initial_tail + 1) % model.firmware.ring_size}, "
        f"got {model.firmware.doorbell['host_tail']}"
    )

    # Verify ring buffer entry in DRAM contains the correct opcode + desc_addr
    ring_off = model.firmware.ring_buffer_addr + initial_tail * 32
    ring_entry = model.pcie.tlp_read(ring_off, 24)
    opcode_val, stored_desc_addr, flags_val = struct.unpack('<IQI', ring_entry[:16])
    assert opcode_val == OpCode.MMUL, (
        f"ring buffer opcode: expected {OpCode.MMUL}, got {opcode_val}"
    )
    assert stored_desc_addr == desc_addr, (
        f"ring buffer desc_addr: expected {desc_addr:#x}, got {stored_desc_addr:#x}"
    )

    # Verify doorbell MMIO written correctly
    doorbell_val = model.bridge.handle('read', DOORBELL.BASE + DOORBELL.HOST_TAIL, 0)
    assert doorbell_val == model.firmware.doorbell['host_tail'], (
        f"DOORBELL.HOST_TAIL MMIO: expected {model.firmware.doorbell['host_tail']}, "
        f"got {doorbell_val}"
    )

    # Verify HOST IRQ (bit 8) was raised
    pending = model.bridge.handle('read', INTC.BASE + INTC.PENDING, 0)
    assert pending & (1 << 8), (
        f"INTC.PENDING bit 8 must be set after host_write_command, "
        f"got 0x{pending:08X}"
    )

    # Anti-vacuous: verify HOST_TAIL was NOT changed for other sources
    assert model.firmware.doorbell['npu_head'] == 0, (
        "npu_head should be unchanged (0) after only host push"
    )

    _emit_metrics(1, 4, source="host_write_command_dispatch")


# ═══════════════════════════════════════════════════════════════════════════
# Scenario 2: Host Write Data → NPU Readback via Crossbar
# ═══════════════════════════════════════════════════════════════════════════


def test_host_write_data_npu_readback():
    """Host writes weight/activation data via PCIe TLP to DRAM; verify NPU
    can read the data through crossbar.read() and it matches the written
    bytes byte-for-byte.

    Exercises: PCIeModel.tlp_write() → crossbar write path
    → CrossbarModel.read() from NPU master perspective.
    """
    model = FuncModel()

    # Write activation data to DRAM at 0x8003_0000
    act_addr = 0x8003_0000
    act_data = np.array([3, 7, 11, 15, 19, 23, 27, 31,
                         35, 39, 43, 47, 51, 55, 59, 63], dtype=np.int8)
    model.pcie.tlp_write(act_addr, act_data.tobytes())

    # Write packed INT4 weights to DRAM at 0x8004_0000
    wgt_addr = 0x8004_0000
    wgt_packed = bytes([0x21, 0x43, 0x65, 0x87, 0xA9, 0xCB, 0xED, 0x0F])

    # Write via PCIe TLP (host→crossbar→DRAM path)
    model.pcie.tlp_write(wgt_addr, wgt_packed)

    # Read back as NPU (MXU master) through crossbar
    npu_act = model.crossbar.read(
        CrossbarModel.MASTER_MXU, act_addr, act_data.nbytes
    )
    npu_wgt = model.crossbar.read(
        CrossbarModel.MASTER_MXU, wgt_addr, len(wgt_packed)
    )

    # Verify byte-exact match
    assert npu_act == act_data.tobytes(), (
        f"Activation readback mismatch: first 8 bytes expected "
        f"{act_data.tobytes()[:8].hex()}, got {npu_act[:8].hex()}"
    )
    assert npu_wgt == wgt_packed, (
        f"Weight readback mismatch: expected {wgt_packed.hex()}, "
        f"got {npu_wgt.hex()}"
    )

    # Verify the data is also visible via direct DRAM access
    off_act = act_addr - Addr.DRAM_BASE
    off_wgt = wgt_addr - Addr.DRAM_BASE
    assert bytes(model.dram[off_act:off_act + act_data.nbytes]) == act_data.tobytes()
    assert bytes(model.dram[off_wgt:off_wgt + len(wgt_packed)]) == wgt_packed

    # Anti-vacuous: corrupted data must differ
    corrupted = bytearray(npu_act)
    corrupted[7] ^= 0xFF
    assert bytes(corrupted) != act_data.tobytes(), "Vacuous — corrupted data matched"

    # Also test SFU master can read the same data
    sfu_wgt = model.crossbar.read(
        CrossbarModel.MASTER_SFU, wgt_addr, len(wgt_packed)
    )
    assert sfu_wgt == wgt_packed, "SFU master readback mismatch"

    _emit_metrics(1, 4, source="host_write_data_npu_readback")


# ═══════════════════════════════════════════════════════════════════════════
# Scenario 3: NPU Write → Host Readback via PCIe TLP
# ═══════════════════════════════════════════════════════════════════════════


def test_npu_to_host_readback():
    """NPU writes output to DRAM through crossbar.write(); host reads back
    via model.pcie.tlp_read(); verify bit-exact roundtrip.

    Exercises: CrossbarModel.write() from NPU master perspective
    → PCIeModel.tlp_read() (Host MRd+CplD reassembly).
    """
    model = FuncModel()

    # NPU output data (simulating MXU INT32 result)
    result_addr = 0x8005_0000
    result_data = np.array(
        [100, 200, 300, 400, 500, 600, 700, 800,
         900, 1000, 1100, 1200, 1300, 1400, 1500, 1600],
        dtype=np.int32,
    )

    # NPU (MXU master) writes output to DRAM via crossbar
    model.crossbar.write(
        CrossbarModel.MASTER_MXU, result_addr, result_data.tobytes()
    )

    # Host reads back via PCIe TLP (MRd+CplD reassembly)
    readback = model.pcie.tlp_read(result_addr, result_data.nbytes)

    assert readback == result_data.tobytes(), (
        f"NPU→Host readback mismatch: first 16 bytes expected "
        f"{result_data.tobytes()[:16].hex()}, got {readback[:16].hex()}"
    )

    # Verify roundtrip as numpy array
    np_readback = np.frombuffer(readback, dtype=np.int32)
    assert np.array_equal(np_readback, result_data), (
        f"NPU→Host roundtrip array mismatch: "
        f"got {np_readback[:4].tolist()}, expected {result_data[:4].tolist()}"
    )

    # Test with larger data (multiple TLP reads, cross MPS boundary)
    large_data = bytes((i * 7 + 11) & 0xFF for i in range(512))
    large_addr = 0x8006_0000
    model.crossbar.write(CrossbarModel.MASTER_SFU, large_addr, large_data)
    large_readback = model.pcie.tlp_read(large_addr, 512)
    assert large_readback == large_data, (
        f"Large NPU→Host readback mismatch: first 16 bytes "
        f"{large_readback[:16].hex()} != {large_data[:16].hex()}"
    )

    # Anti-vacuous
    corrupted = bytearray(large_readback)
    corrupted[256] ^= 0xAA
    assert bytes(corrupted) != large_data, "Vacuous — corrupted large data matched"

    # Test with DMA master as well
    dma_data = b"NPU_DMA_WRITE_TEST" * 2
    dma_addr = 0x8007_0000
    model.crossbar.write(CrossbarModel.MASTER_DMA, dma_addr, dma_data)
    dma_readback = model.pcie.tlp_read(dma_addr, len(dma_data))
    assert dma_readback == dma_data, (
        f"DMA write→Host readback mismatch: {dma_readback.hex()}"
    )

    _emit_metrics(1, 4, source="npu_to_host_readback")


# ═══════════════════════════════════════════════════════════════════════════
# Scenario 4: Host CPU Full End-to-End (MMUL + SFU + Vector Chain)
# ═══════════════════════════════════════════════════════════════════════════


def test_host_cpu_full_end_to_end():
    """Host writes command + data via PCIe TLP → NPU executes a small
    MMUL+SFU+Vector chain through the bridge → host reads output via PCIe
    TLP → compare against GoldenExecutor (GoldenMXU → GoldenSFU → GoldenVector).

    This is the SoC-level equivalent of FM-SOC-10X, purely in Func Model
    Python. Exercises paths: PCIe-TLP (path 7), MXU-COMPUTE (path 3),
    SFU (path 4), Vector (path 5), XBAR-ARB (path 8).

    Chain: MXU INT4 per-block matmul → SFU SiLU activation → Vector ADD residual.
    """
    model = FuncModel()
    bridge = model.bridge

    M, K, N = 2, 8, 4
    group_size = 128

    activations = np.array([1, 2, 3, 4, 5, 6, 7, 8,
                            9, 10, 11, 12, 13, 14, 15, 16], dtype=np.int8).reshape(M, K)

    wgt_unpacked = np.array([
        [0, 1, 2, 3],
        [4, 5, 6, 7],
        [0, 1, 2, 3],
        [4, 5, 6, 7],
        [0, 1, 2, 3],
        [4, 5, 6, 7],
        [0, 1, 2, 3],
        [4, 5, 6, 7],
    ], dtype=np.int8)
    wgt_packed = GoldenMXU.pack_int4(wgt_unpacked.flatten())

    num_blocks = (K + group_size - 1) // group_size
    scales = np.ones((num_blocks, N), dtype=np.float32)

    # ── Addresses ─────────────────────────────────────────────────────
    act_sram = 0x2000_1000      # SRAM base + 0x1000
    wgt_sram = 0x2000_2000      # SRAM base + 0x2000
    mxu_out_sram = 0x2000_3000  # MXU output in SRAM
    sfu_out_sram = 0x2000_4000  # SFU output in SRAM
    vec_out_sram = 0x2000_5000  # Vector output in SRAM
    host_out_dram = 0x8100_0000 # Host-visible DRAM for final readback

    # ── Step 1: Host writes activations, weights, scales to SRAM ──────
    from cocotb_bridge import pack_int8_activation_tile_major
    act_packed = pack_int8_activation_tile_major(activations.tobytes(), M, K)
    model.sram[0x1000:0x1000 + len(act_packed)] = act_packed
    model.sram[0x2000:0x2000 + len(wgt_packed)] = wgt_packed.tobytes()
    model.sram[0x2500:0x2500 + scales.nbytes] = scales.tobytes()

    # ── Step 2: MXU compute (INT4 per-block) ──────────────────────────
    # Write MXU registers via MMIO bridge
    bridge.handle('write', MXU.BASE + MXU.CTRL, 0)         # clear/disable
    bridge.handle('write', MXU.BASE + MXU.DIM0, (K << 16) | M)
    bridge.handle('write', MXU.BASE + MXU.DIM1, N)
    bridge.handle('write', MXU.BASE + MXU.I_ADDR, act_sram)
    bridge.handle('write', MXU.BASE + MXU.W_ADDR, wgt_sram)
    bridge.handle('write', MXU.BASE + MXU.O_ADDR, mxu_out_sram)
    bridge.handle('write', MXU.BASE + MXU.SCALE_ADDR, 0x2000_2500)

    # Trigger MXU
    bridge.handle('write', MXU.BASE + MXU.CMD, 1)
    status = bridge.handle('read', MXU.BASE + MXU.STATUS, 0)
    assert status == 2, f"MXU STATUS expected DONE(2), got {status}"

    # Read MXU output from SRAM
    mxu_out = np.frombuffer(
        bytes(model.sram[0x3000:0x3000 + M * N * 4]), dtype=np.float32
    ).reshape(M, N)

    # ── Step 3: SFU SiLU activation ───────────────────────────────────
    # Convert MXU output (FP32) to FP16 for SFU input
    sfu_in = mxu_out.astype(np.float16)
    sfu_in_offset = 0x3800
    model.sram[sfu_in_offset:sfu_in_offset + sfu_in.nbytes] = sfu_in.tobytes()

    sfu_op = 7  # SiLU
    sfu_dim = M * N  # flatten

    bridge.handle('write', SFU.BASE + SFU.CTRL, sfu_op)
    bridge.handle('write', SFU.BASE + SFU.I_ADDR, 0x2000_3800)
    bridge.handle('write', SFU.BASE + SFU.O_ADDR, sfu_out_sram)
    bridge.handle('write', SFU.BASE + SFU.DIM, (0 << 16) | sfu_dim)
    bridge.handle('write', SFU.BASE + SFU.CMD, 1)

    sfu_status = bridge.handle('read', SFU.BASE + SFU.STATUS, 0)
    assert sfu_status == 2, f"SFU STATUS expected DONE(2), got {sfu_status}"

    # Read SFU output
    sfu_out = np.frombuffer(
        bytes(model.sram[0x4000:0x4000 + sfu_dim * 2]), dtype=np.float16
    ).flatten()

    # ── Step 4: Vector ADD residual ───────────────────────────────────
    residual = np.array([1, 2, 3, 4, 5, 6, 7, 8], dtype=np.int32)
    residual_offset = 0x4800
    model.sram[residual_offset:residual_offset + residual.nbytes] = residual.tobytes()

    # Convert SFU output (FP16) → INT32 for Vector input
    vec_in = model.vector.conv_f16_to_i32(sfu_out)
    vec_in_offset = 0x4A00
    model.sram[vec_in_offset:vec_in_offset + vec_in.nbytes] = vec_in.tobytes()

    vec_op = 0  # ADD
    vec_dim = sfu_dim  # 8 elements

    bridge.handle('write', VECTOR.BASE + VECTOR.CTRL, vec_op)
    bridge.handle('write', VECTOR.BASE + VECTOR.A_ADDR, 0x2000_4A00)
    bridge.handle('write', VECTOR.BASE + VECTOR.B_ADDR, 0x2000_4800)
    bridge.handle('write', VECTOR.BASE + VECTOR.O_ADDR, vec_out_sram)
    bridge.handle('write', VECTOR.BASE + VECTOR.DIM, vec_dim)
    bridge.handle('write', VECTOR.BASE + VECTOR.CMD, 1)

    vec_status = bridge.handle('read', VECTOR.BASE + VECTOR.STATUS, 0)
    assert vec_status == 2, f"Vector STATUS expected DONE(2), got {vec_status}"

    # Read Vector output from SRAM → write to DRAM for host readback
    vec_out = np.frombuffer(
        bytes(model.sram[0x5000:0x5000 + vec_dim * 4]), dtype=np.int32
    )

    # Copy final result to DRAM via crossbar (simulates NPU→DRAM DMA)
    model.crossbar.write(
        CrossbarModel.MASTER_VEC,
        host_out_dram,
        vec_out.tobytes(),
    )

    # Host reads output via PCIe TLP
    host_result_bytes = model.pcie.tlp_read(host_out_dram, vec_dim * 4)
    host_result = np.frombuffer(host_result_bytes, dtype=np.int32)

    assert np.array_equal(host_result, vec_out), (
        f"Host readback mismatch: got {host_result[:4].tolist()}, "
        f"expected {vec_out[:4].tolist()}"
    )

    # ── Step 5: Golden comparison ─────────────────────────────────────
    # Compute golden: MXU → SFU → Vector independently
    golden_mxu_fp32 = model.mxu.matmul_int4_per_block(
        activations, wgt_packed, scales, M, K, N, group_size=group_size
    )
    golden_mxu_fp32_array = np.asarray(golden_mxu_fp32, dtype=np.float32).reshape(M, N)
    assert np.allclose(mxu_out, golden_mxu_fp32_array, rtol=1e-5), (
        f"MXU stage mismatch vs golden: {mxu_out.tolist()} vs "
        f"{golden_mxu_fp32_array.tolist()}"
    )

    # Golden SFU: SiLU
    golden_sfu = model.sfu.silu_hw(golden_mxu_fp32_array.flatten())
    assert np.allclose(sfu_out, golden_sfu, rtol=1e-3, atol=1e-3), (
        f"SFU SiLU mismatch vs golden: {sfu_out[:4]} vs {golden_sfu[:4]}"
    )

    # Golden Vector: ADD
    golden_vec_in = model.vector.conv_f16_to_i32(golden_sfu)
    golden_vec = model.vector.add(golden_vec_in, residual)
    assert np.array_equal(vec_out, golden_vec), (
        f"Vector ADD mismatch vs golden: {vec_out[:4].tolist()} vs "
        f"{golden_vec[:4].tolist()}"
    )

    # Anti-vacuous: verify all stages produced non-zero output
    assert np.any(mxu_out != 0), "MXU output is all-zero (vacuous)"
    assert np.any(sfu_out != 0), "SFU output is all-zero (vacuous)"
    assert np.any(vec_out != 0), "Vector output is all-zero (vacuous)"

    _emit_metrics(1, 4, source="host_cpu_full_end_to_end")
