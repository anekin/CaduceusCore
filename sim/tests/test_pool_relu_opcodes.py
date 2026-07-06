"""Tests for MAXPOOL, AVGPOOL, and RELU ISA opcodes.

Covers:
  - OpCode values and mnemonic mapping
  - Step() execution with synthetic FP16 tensors
  - SRAM readback correctness vs expected golden output
"""

import numpy as np

from engine.isa import NPUInstruction, OpCode
from golden_executor import GoldenExecutor, GoldenSFU


class TestRELUOpcode:
    """Unit tests for RELU = 0x04 opcode."""

    def test_opcode_value(self):
        assert OpCode.RELU == 0x04
        assert int(OpCode.RELU) == 4

    def test_from_mnemonic(self):
        assert OpCode.from_mnemonic("relu") == OpCode.RELU
        assert OpCode.from_mnemonic("RELU") == OpCode.RELU

    def test_execute_random_fp16_vector(self):
        """Given: random FP16 vector in SRAM with positive and negative values.
        When: GoldenExecutor.step() executes RELU.
        Then: all negative values become 0, positives unchanged.
        """
        rng = np.random.default_rng(42)
        length = 128
        sa = 0x200000
        da = 0x2C0000

        raw_fp32 = rng.standard_normal(length, dtype=np.float32) * 3.0
        raw_fp16 = raw_fp32.astype(np.float16)
        # Expected = relu of FP16-quantized input (executor reads FP16 from SRAM)
        expected = np.maximum(raw_fp16.astype(np.float32), 0.0)

        executor = GoldenExecutor()
        executor.sram.write_float16(sa, raw_fp16)

        instr = NPUInstruction(
            opcode=OpCode.RELU,
            operands={"sa": sa, "da": da, "len": length},
        )
        executor.step(instr)

        result_fp16 = executor.sram.read_float16(da, length)
        result_f32 = result_fp16.astype(np.float32)
        assert np.allclose(result_f32, expected, atol=2e-3), (
            f"RELU output mismatch: max_abs_diff={np.max(np.abs(result_f32 - expected)):.2e}"
        )

    def test_all_negative_becomes_zero(self):
        """Given: all-negative FP16 vector.
        When: RELU is executed.
        Then: output is all zeros.
        """
        length = 64
        sa = 0x200000
        da = 0x2C0000

        raw_fp32 = -np.abs(np.random.default_rng(7).standard_normal(length, dtype=np.float32))
        raw_fp16 = raw_fp32.astype(np.float16)

        executor = GoldenExecutor()
        executor.sram.write_float16(sa, raw_fp16)

        instr = NPUInstruction(
            opcode=OpCode.RELU,
            operands={"sa": sa, "da": da, "len": length},
        )
        executor.step(instr)

        result_fp16 = executor.sram.read_float16(da, length)
        assert np.all(result_fp16.astype(np.float32) == 0.0), (
            f"RELU all-negative: expected all zeros, got non-zero values"
        )

    def test_all_positive_passthrough(self):
        """Given: all-positive FP16 vector.
        When: RELU is executed.
        Then: output matches input.
        """
        length = 64
        sa = 0x200000
        da = 0x2C0000

        raw_fp32 = np.abs(np.random.default_rng(11).standard_normal(length, dtype=np.float32)) + 0.1
        raw_fp16 = raw_fp32.astype(np.float16)

        executor = GoldenExecutor()
        executor.sram.write_float16(sa, raw_fp16)

        instr = NPUInstruction(
            opcode=OpCode.RELU,
            operands={"sa": sa, "da": da, "len": length},
        )
        executor.step(instr)

        result_fp16 = executor.sram.read_float16(da, length)
        result_f32 = result_fp16.astype(np.float32)
        expected_f32 = raw_fp16.astype(np.float32)
        assert np.allclose(result_f32, expected_f32, atol=1e-6), (
            f"RELU all-positive: values changed. max_diff={np.max(np.abs(result_f32 - expected_f32)):.2e}"
        )


class TestMAXPOOLOpcode:
    """Unit tests for MAXPOOL = 0x07 opcode."""

    def test_opcode_value(self):
        assert OpCode.MAXPOOL == 0x07
        assert int(OpCode.MAXPOOL) == 7

    def test_from_mnemonic(self):
        assert OpCode.from_mnemonic("maxpool") == OpCode.MAXPOOL
        assert OpCode.from_mnemonic("MAXPOOL") == OpCode.MAXPOOL

    def test_execute_2x2_on_4x4(self):
        """Given: 4x4 FP16 tensor in SRAM.
        When: MAXPOOL with H=4, W=4.
        Then: output is 2x2 with per-window max values.
        """
        H, W = 4, 4
        sa = 0x200000
        da = 0x2C0000

        inp = np.array([
            [1.0, 2.0, 3.0, 4.0],
            [5.0, 6.0, 7.0, 8.0],
            [9.0, 10.0, 11.0, 12.0],
            [13.0, 14.0, 15.0, 16.0],
        ], dtype=np.float32)

        expected = np.array([
            [6.0, 8.0],
            [14.0, 16.0],
        ], dtype=np.float32)

        executor = GoldenExecutor()
        executor.sram.write_float16(sa, inp.astype(np.float16).flatten())

        instr = NPUInstruction(
            opcode=OpCode.MAXPOOL,
            operands={"sa": sa, "da": da, "H": H, "W": W},
        )
        executor.step(instr)

        result_fp16 = executor.sram.read_float16(da, 4)
        result_f32 = result_fp16.astype(np.float32).reshape(2, 2)
        assert np.array_equal(result_f32, expected), (
            f"MAXPOOL 4x4: expected\n{expected}\ngot\n{result_f32}"
        )

    def test_execute_2x2_on_6x6(self):
        """Given: 6x6 FP16 tensor.
        When: MAXPOOL with H=6, W=6.
        Then: output is 3x3 with per-window max values.
        """
        H, W = 6, 6
        sa = 0x200000
        da = 0x2C0000

        rng = np.random.default_rng(99)
        inp = rng.normal(size=(H, W)).astype(np.float32) * 5.0
        # Executor reads FP16-quantized input from SRAM, compute expected from that
        inp_fp16 = inp.astype(np.float16).astype(np.float32)

        expected = np.zeros((3, 3), dtype=np.float32)
        for i in range(3):
            for j in range(3):
                expected[i, j] = np.max(inp_fp16[i*2:i*2+2, j*2:j*2+2])

        executor = GoldenExecutor()
        executor.sram.write_float16(sa, inp.astype(np.float16).flatten())

        instr = NPUInstruction(
            opcode=OpCode.MAXPOOL,
            operands={"sa": sa, "da": da, "H": H, "W": W},
        )
        executor.step(instr)

        result_fp16 = executor.sram.read_float16(da, 9)
        result_f32 = result_fp16.astype(np.float32).reshape(3, 3)
        assert np.array_equal(result_f32, expected), (
            f"MAXPOOL 6x6: max_abs_diff={np.max(np.abs(result_f32 - expected)):.2e}"
        )

    def test_execute_all_negative(self):
        """Given: all-negative 4x4 tensor.
        When: MAXPOOL.
        Then: selects the least-negative value per window.
        """
        H, W = 4, 4
        sa = 0x200000
        da = 0x2C0000

        inp = np.array([
            [-5.0, -3.0, -8.0, -2.0],
            [-1.0, -4.0, -6.0, -7.0],
            [-9.0, -2.0, -3.0, -1.0],
            [-4.0, -8.0, -5.0, -6.0],
        ], dtype=np.float32)

        expected = np.array([
            [-1.0, -2.0],
            [-2.0, -1.0],
        ], dtype=np.float32)

        executor = GoldenExecutor()
        executor.sram.write_float16(sa, inp.astype(np.float16).flatten())

        instr = NPUInstruction(
            opcode=OpCode.MAXPOOL,
            operands={"sa": sa, "da": da, "H": H, "W": W},
        )
        executor.step(instr)

        result_fp16 = executor.sram.read_float16(da, 4)
        result_f32 = result_fp16.astype(np.float32).reshape(2, 2)
        assert np.array_equal(result_f32, expected), (
            f"MAXPOOL all-negative: expected\n{expected}\ngot\n{result_f32}"
        )


class TestAVGPOOLOpcode:
    """Unit tests for AVGPOOL = 0x08 opcode."""

    def test_opcode_value(self):
        assert OpCode.AVGPOOL == 0x08
        assert int(OpCode.AVGPOOL) == 8

    def test_from_mnemonic(self):
        assert OpCode.from_mnemonic("avgpool") == OpCode.AVGPOOL
        assert OpCode.from_mnemonic("AVGPOOL") == OpCode.AVGPOOL

    def test_execute_2x2_on_4x4(self):
        """Given: 4x4 FP16 tensor with uniform window values.
        When: AVGPOOL with H=4, W=4.
        Then: output is 2x2 with per-window means.
        """
        H, W = 4, 4
        sa = 0x200000
        da = 0x2C0000

        inp = np.array([
            [1.0, 3.0, 5.0, 7.0],
            [1.0, 3.0, 5.0, 7.0],
            [2.0, 4.0, 6.0, 8.0],
            [2.0, 4.0, 6.0, 8.0],
        ], dtype=np.float32)

        expected = np.array([
            [2.0, 6.0],
            [3.0, 7.0],
        ], dtype=np.float32)

        executor = GoldenExecutor()
        executor.sram.write_float16(sa, inp.astype(np.float16).flatten())

        instr = NPUInstruction(
            opcode=OpCode.AVGPOOL,
            operands={"sa": sa, "da": da, "H": H, "W": W},
        )
        executor.step(instr)

        result_fp16 = executor.sram.read_float16(da, 4)
        result_f32 = result_fp16.astype(np.float32).reshape(2, 2)
        assert np.allclose(result_f32, expected, atol=1e-3), (
            f"AVGPOOL 4x4: expected\n{expected}\ngot\n{result_f32}"
        )

    def test_execute_2x2_on_6x6_random(self):
        """Given: 6x6 random FP16 tensor.
        When: AVGPOOL.
        Then: output matches per-window mean within FP16 tolerance.
        """
        H, W = 6, 6
        sa = 0x200000
        da = 0x2C0000

        rng = np.random.default_rng(42)
        inp = rng.normal(size=(H, W)).astype(np.float32) * 3.0
        inp_fp16 = inp.astype(np.float16).astype(np.float32)

        expected = np.zeros((3, 3), dtype=np.float32)
        for i in range(3):
            for j in range(3):
                expected[i, j] = np.mean(inp_fp16[i*2:i*2+2, j*2:j*2+2])

        executor = GoldenExecutor()
        executor.sram.write_float16(sa, inp.astype(np.float16).flatten())

        instr = NPUInstruction(
            opcode=OpCode.AVGPOOL,
            operands={"sa": sa, "da": da, "H": H, "W": W},
        )
        executor.step(instr)

        result_fp16 = executor.sram.read_float16(da, 9)
        result_f32 = result_fp16.astype(np.float32).reshape(3, 3)
        assert np.allclose(result_f32, expected, atol=2e-3), (
            f"AVGPOOL 6x6: max_abs_diff={np.max(np.abs(result_f32 - expected)):.2e}"
        )

    def test_constant_input_output_matches(self):
        """Given: all-constant 4x4 tensor.
        When: AVGPOOL.
        Then: output is the same constant value.
        """
        H, W = 4, 4
        sa = 0x200000
        da = 0x2C0000
        const = 3.14

        inp = np.full((H, W), const, dtype=np.float32)
        expected = np.full((2, 2), const, dtype=np.float32)

        executor = GoldenExecutor()
        executor.sram.write_float16(sa, inp.astype(np.float16).flatten())

        instr = NPUInstruction(
            opcode=OpCode.AVGPOOL,
            operands={"sa": sa, "da": da, "H": H, "W": W},
        )
        executor.step(instr)

        result_fp16 = executor.sram.read_float16(da, 4)
        result_f32 = result_fp16.astype(np.float32).reshape(2, 2)
        assert np.allclose(result_f32, expected, atol=1e-3), (
            f"AVGPOOL constant: expected all {const}, got\n{result_f32}"
        )
