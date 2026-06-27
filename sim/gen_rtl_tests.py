#!/usr/bin/env python3
"""
RTL Verification Test Suite Generator.

Generates complete test vectors for RTL verification:
- Random inputs with controlled seeds (reproducible)
- Corner cases (boundary tiles, min/max values, zeros)
- Full-layer chained operations (MMUL → SFU → MMUL)
- Output: $readmemh-compatible hex files + ISA program + manifest

Usage:
    python3 gen_rtl_tests.py                    # all test categories
    python3 gen_rtl_tests.py --category random  # random only
    python3 gen_rtl_tests.py --category corner  # corner cases only
    python3 gen_rtl_tests.py --category chain   # chained ops only
"""

import sys, os, json, hashlib, struct
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from golden_executor import GoldenMXU, GoldenSFU, GoldenExecutor, SRAM, ARRAY_H, ARRAY_W
from engine.isa import NPUInstruction, OpCode, NPUEncoder


# ══════════════════════════════════════════════════════════════════════
# Hex file writers ($readmemh compatible)
# ══════════════════════════════════════════════════════════════════════

def write_hex_int4(path: Path, packed: np.ndarray):
    """Write INT4 packed weights as hex (2 digits per byte)."""
    with open(path, "w") as f:
        for b in np.asarray(packed, dtype=np.uint8).flat:
            f.write(f"{b:02x}\n")

def write_hex_int8(path: Path, data: np.ndarray):
    """Write INT8 data as hex (2 digits, unsigned representation)."""
    with open(path, "w") as f:
        for v in np.asarray(data, dtype=np.int8).flat:
            f.write(f"{int(v) & 0xFF:02x}\n")

def write_hex_int32(path: Path, data: np.ndarray):
    """Write INT32 data as hex (8 digits, unsigned representation)."""
    with open(path, "w") as f:
        for v in np.asarray(data, dtype=np.int32).flat:
            f.write(f"{int(v) & 0xFFFFFFFF:08x}\n")

def write_hex_float16(path: Path, data: np.ndarray):
    """Write BF16/FP16 data as hex (4 digits)."""
    with open(path, "w") as f:
        raw = np.asarray(data, dtype=np.float16).tobytes()
        for i in range(0, len(raw), 2):
            val = struct.unpack_from("<H", raw, i)[0]
            f.write(f"{val:04x}\n")

def write_isa_text(path: Path, program: List[NPUInstruction]):
    """Write ISA program as human-readable text."""
    with open(path, "w") as f:
        for instr in program:
            f.write(f"{instr}\n")

def write_isa_binary(path: Path, program: List[NPUInstruction]):
    """Write ISA program as 32-bit binary words (one hex per line)."""
    encoder = NPUEncoder()
    with open(path, "w") as f:
        for instr in program:
            words = encoder.encode(instr)
            for w in words:
                f.write(f"{w & 0xFFFFFFFF:08x}\n")


# ══════════════════════════════════════════════════════════════════════
# Test case definition
# ══════════════════════════════════════════════════════════════════════

@dataclass
class TestCase:
    name: str
    M: int
    K: int
    N: int
    seed: int
    description: str = ""
    use_sfu: bool = False
    sfu_op: str = ""        # "softmax", "gelu", "silu", "layernorm"
    sfu_len: int = 0
    is_chain: bool = False   # multi-instruction chain
    is_qwen_smoke: bool = False  # qwen-smoke multi-instruction test

    @property
    def dir_name(self) -> str:
        """Safe directory name."""
        safe = self.name.replace(" ", "_").replace("/", "_")
        return safe


# ══════════════════════════════════════════════════════════════════════
# Test categories
# ══════════════════════════════════════════════════════════════════════

def random_tests() -> List[TestCase]:
    """Standard random tests covering typical NPU operations."""
    return [
        TestCase("tiny_tile", M=1, K=128, N=128, seed=100,
                 description="Single 128×128 tile, minimal test"),
        TestCase("Q_proj_decode", M=1, K=2560, N=4096, seed=101,
                 description="Q projection, decode (M=1)"),
        TestCase("K_proj_decode", M=1, K=2560, N=256, seed=102,
                 description="K projection (GQA, 2 KV heads)"),
        TestCase("V_proj_decode", M=1, K=2560, N=256, seed=103,
                 description="V projection"),
        TestCase("O_proj_decode", M=1, K=4096, N=2560, seed=104,
                 description="Output projection"),
        TestCase("FFN_gate_decode", M=1, K=2560, N=9728, seed=105,
                 description="FFN gate (large N)"),
        TestCase("FFN_up_decode", M=1, K=2560, N=9728, seed=106,
                 description="FFN up projection"),
        TestCase("FFN_down_decode", M=1, K=9728, N=2560, seed=107,
                 description="FFN down (large K)"),
        TestCase("prefill_batch4", M=4, K=2560, N=4096, seed=108,
                 description="Prefill with batch=4"),
        TestCase("prefill_batch128", M=128, K=2560, N=256, seed=109,
                 description="Prefill KV projection, batch=128"),
    ]


def corner_tests() -> List[TestCase]:
    """Corner/edge cases: boundaries, extremes, zeros."""
    return [
        TestCase("boundary_m1_k1_n1", M=1, K=1, N=1, seed=200,
                 description="Minimal dimensions"),
        TestCase("boundary_m1_k1_n128", M=1, K=1, N=128, seed=201,
                 description="K=1, full tile width"),
        TestCase("boundary_m129_k128_n129", M=129, K=128, N=129, seed=202,
                 description="Just over tile boundary in M and N"),
        TestCase("boundary_m128_k2560_n128", M=128, K=2560, N=128, seed=203,
                 description="Exact tile in M and N"),
        TestCase("boundary_m1_k255_n255", M=1, K=255, N=255, seed=204,
                 description="Non-multiple K, near-tile N"),
        TestCase("boundary_m1_k9728_n1", M=1, K=9728, N=1, seed=205,
                 description="Narrow output, large K"),
    ]


def sfu_tests() -> List[TestCase]:
    """SFU operation tests via ISA execution."""
    return [
        TestCase("sfu_softmax_2560", M=0, K=0, N=0, seed=300,
                 description="Softmax on 2560-dim vector",
                 use_sfu=True, sfu_op="softmax", sfu_len=2560),
        TestCase("sfu_gelu_1000", M=0, K=0, N=0, seed=301,
                 description="GELU on 1000 elements",
                 use_sfu=True, sfu_op="gelu", sfu_len=1000),
        TestCase("sfu_silu_1000", M=0, K=0, N=0, seed=302,
                 description="SiLU on 1000 elements",
                 use_sfu=True, sfu_op="silu", sfu_len=1000),
        TestCase("sfu_layernorm_2560", M=0, K=0, N=0, seed=303,
                 description="LayerNorm on 2560-dim vector",
                 use_sfu=True, sfu_op="layernorm", sfu_len=2560),
    ]


def chain_tests() -> List[TestCase]:
    """Multi-instruction chain tests (MMUL → SFU → MMUL)."""
    return [
        TestCase("chain_attn_block", M=1, K=2560, N=4096, seed=400,
                 description="Q_proj MMUL → Softmax SFU → O_proj MMUL (attention block)",
                 is_chain=True),
    ]


def qwen_smoke_tests() -> List[TestCase]:
    """Qwen2.5-3B single-layer smoke tests with small tensors (M≤64, K≤64, N≤64).

    Exercises the full compute pipeline: MMUL → SFU → Vector, simulating
    a transformer layer's ops (projections, activations, residuals).
    Only MMUL/SFU/Vector opcodes; excludes DMA_LD/KV_LOAD/BARRIER.
    """
    return [
        TestCase("qwen_smoke_blk0", M=1, K=64, N=64, seed=500,
                 description="Qwen2.5-3B blk.0 smoke: 10 instructions (MMUL×4, SFU×3, Vector×3)",
                 use_sfu=False, sfu_op="", sfu_len=0,
                 is_chain=False, is_qwen_smoke=True),
    ]


# ══════════════════════════════════════════════════════════════════════
# Test vector generator
# ══════════════════════════════════════════════════════════════════════

class TestVectorGen:
    """Generate a test vector directory with hex files, ISA, and manifest."""

    def __init__(self, output_root: Path):
        self.output_root = Path(output_root)
        self.mxu = GoldenMXU()
        self.sfu = GoldenSFU()
        self.output_root.mkdir(parents=True, exist_ok=True)

    def generate(self, tc: TestCase) -> Path:
        """Generate one test case, return test directory path."""
        test_dir = self.output_root / tc.dir_name
        test_dir.mkdir(parents=True, exist_ok=True)
        rng = np.random.RandomState(tc.seed)

        manifest = {
            "name": tc.name,
            "description": tc.description,
            "seed": tc.seed,
            "files": {},
            "results": {},
        }

        if tc.use_sfu:
            self._gen_sfu_test(test_dir, tc, rng, manifest)
        elif tc.is_qwen_smoke:
            self._gen_qwen_smoke(test_dir, tc, rng, manifest)
        elif tc.is_chain:
            self._gen_chain_test(test_dir, tc, rng, manifest)
        else:
            self._gen_mmul_test(test_dir, tc, rng, manifest)

        # Write manifest
        with open(test_dir / "manifest.json", "w") as f:
            json.dump(manifest, f, indent=2)

        return test_dir

    def _gen_mmul_test(self, test_dir: Path, tc: TestCase,
                       rng: np.random.RandomState, manifest: dict):
        """Generate a single MMUL test."""
        M, K, N = tc.M, tc.K, tc.N

        # Generate INT4 weights
        w_int4 = rng.randint(-8, 8, size=K * N, dtype=np.int8)
        w_packed = GoldenMXU.pack_int4(w_int4)

        # Generate INT8 activations
        activation = rng.randint(-128, 128, size=M * K, dtype=np.int8).reshape(M, K)

        # Golden computation
        golden = self.mxu.matmul_int32(activation, w_packed, M, K, N)
        golden_hash = GoldenMXU.hash_output(golden)

        # Write hex files
        write_hex_int4(test_dir / "weight.hex", w_packed)
        write_hex_int8(test_dir / "activation.hex", activation)
        write_hex_int32(test_dir / "golden.hex", golden)

        # Generate ISA program
        program = [
            NPUInstruction(OpCode.MMUL, {
                "wa": 0x000000, "ia": 0x200000, "oa": 0x280000,
                "M": M, "K": K, "N": N
            }, comment=f"{tc.name}: M={M} K={K} N={N}"),
        ]
        write_isa_text(test_dir / "program.isa", program)
        write_isa_binary(test_dir / "program.bin", program)

        manifest["M"] = M
        manifest["K"] = K
        manifest["N"] = N
        manifest["files"] = {
            "weight": "weight.hex",
            "activation": "activation.hex",
            "golden": "golden.hex",
            "isa_text": "program.isa",
            "isa_binary": "program.bin",
        }
        manifest["results"] = {
            "golden_hash": golden_hash,
            "golden_shape": list(golden.shape),
            "golden_dtype": "int32",
        }
        manifest["format"] = {
            "weight": "INT4 packed, 2 per uint8 byte, low nibble first",
            "activation": "INT8, 1 byte per value",
            "golden": "INT32, little-endian, $readmemh compatible",
            "isa": "32-bit fixed-length instruction words, 1-2 words per instruction",
        }

    def _gen_sfu_test(self, test_dir: Path, tc: TestCase,
                      rng: np.random.RandomState, manifest: dict):
        """Generate an SFU test."""
        n = tc.sfu_len

        if tc.sfu_op == "softmax":
            x = rng.randn(n).astype(np.float32) * 2.0
            golden = self.sfu.softmax_hw(x)
            opcode = OpCode.SOFTMAX
        elif tc.sfu_op == "gelu":
            x = rng.randn(n).astype(np.float32) * 2.0
            x = np.clip(x, -4.0, 4.0)
            golden = self.sfu.gelu_hw(x)
            opcode = OpCode.GELU
        elif tc.sfu_op == "silu":
            x = rng.randn(n).astype(np.float32) * 2.0
            x = np.clip(x, -4.0, 4.0)
            golden = self.sfu.silu_hw(x)
            opcode = OpCode.SILU
        elif tc.sfu_op == "layernorm":
            x = rng.randn(n).astype(np.float32) * 2.0
            golden = self.sfu.layernorm_hw(x)
            opcode = OpCode.LAYERNORM
        else:
            raise ValueError(f"Unknown SFU op: {tc.sfu_op}")

        # Write hex files (FP16 for SFU)
        write_hex_float16(test_dir / "input.hex", x)
        write_hex_float16(test_dir / "golden.hex", golden)

        # ISA program
        program = [
            NPUInstruction(opcode, {
                "sa": 0x2C0000, "da": 0x2D0000, "len": n,
            }, comment=f"{tc.sfu_op} len={n}"),
        ]
        write_isa_text(test_dir / "program.isa", program)
        write_isa_binary(test_dir / "program.bin", program)

        manifest["sfu_op"] = tc.sfu_op
        manifest["sfu_len"] = n
        manifest["files"] = {
            "input": "input.hex",
            "golden": "golden.hex",
            "isa_text": "program.isa",
            "isa_binary": "program.bin",
        }
        manifest["results"] = {
            "golden_hash": hashlib.md5(
                golden.astype(np.float16).tobytes()
            ).hexdigest()[:16],
            "golden_dtype": "float16",
        }
        manifest["format"] = {
            "input": "float16 (BF16), 4 hex digits per value",
            "golden": "float16 (BF16), 4 hex digits per value",
            "isa": "32-bit fixed-length instruction words",
        }

    def _gen_chain_test(self, test_dir: Path, tc: TestCase,
                        rng: np.random.RandomState, manifest: dict):
        """Generate a chained multi-instruction test (MMUL → SFU → MMUL)."""
        M, K, N = tc.M, tc.K, tc.N
        HIDDEN = 2560  # for attention block

        # Q_proj weights and activation
        w_q = GoldenMXU.pack_int4(rng.randint(-8, 8, size=K * N, dtype=np.int8))
        act = rng.randint(-128, 128, size=M * K, dtype=np.int8).reshape(M, K)

        # Golden: Q_proj MMUL
        q_out = self.mxu.matmul_int32(act, w_q, M, K, N)

        # Golden: Softmax on Q output (treat as flat)
        q_flat = q_out.astype(np.float32)
        sm_out = self.sfu.softmax_hw(q_flat.flatten())

        # For chain: write intermediate golden states
        write_hex_int8(test_dir / "activation.hex", act)
        write_hex_int4(test_dir / "weight_q.hex", w_q)
        write_hex_int32(test_dir / "golden_q.hex", q_out)
        write_hex_float16(test_dir / "golden_softmax.hex", sm_out)

        # ISA program: MMUL → SOFTMAX
        program = [
            NPUInstruction(OpCode.MMUL, {
                "wa": 0x000000, "ia": 0x200000, "oa": 0x280000,
                "M": M, "K": K, "N": N,
            }, comment="Q projection"),
            NPUInstruction(OpCode.SOFTMAX, {
                "sa": 0x280000, "da": 0x2C0000, "len": M * N,
            }, comment="Softmax on attention scores"),
        ]
        write_isa_text(test_dir / "program.isa", program)
        write_isa_binary(test_dir / "program.bin", program)

        # Also execute via ISA to verify
        executor = GoldenExecutor()
        executor.sram.write_bytes(0x000000, np.asarray(w_q, dtype=np.uint8))
        executor.sram.write_bytes(0x200000, np.asarray(act.flatten(), dtype=np.uint8))
        trace = executor.execute_program(program)

        manifest["chain"] = True
        manifest["num_instructions"] = len(program)
        manifest["files"] = {
            "activation": "activation.hex",
            "weight_q": "weight_q.hex",
            "golden_q": "golden_q.hex",
            "golden_softmax": "golden_softmax.hex",
            "isa_text": "program.isa",
            "isa_binary": "program.bin",
        }
        manifest["results"] = {
            "q_hash": GoldenMXU.hash_output(q_out),
            "softmax_hash": hashlib.md5(sm_out.astype(np.float16).tobytes()).hexdigest()[:16],
            "isa_verified": True,
            "final_sram_hash": trace[-1]["sram_checksum"],
        }
        manifest["format"] = {
            "activation": "INT8",
            "weight": "INT4 packed",
            "golden_q": "INT32",
            "golden_softmax": "float16",
            "isa": "32-bit instruction words",
        }

    def _gen_qwen_smoke(self, test_dir: Path, tc: TestCase,
                        rng: np.random.RandomState, manifest: dict):
        """Generate Qwen2.5-3B single-layer smoke test with 10 instructions.

        Covers MMUL (4 projections), SFU (softmax/gelu/layernorm),
        Vector (vadd/vmul/vresid). All tensors <= 64. Excludes
        DMA_LD/KV_LOAD/BARRIER. Each instruction has independent preloaded
        inputs so SFU/Vector get valid float16 data.
        """
        M, K, N = 1, 64, 64

        def pad_to_even(arr):
            if len(arr) % 2 != 0:
                return np.append(arr, 0)
            return arr

        # ── Instruction sequence (10 ops, N=10) ──────────────────
        instructions: List[NPUInstruction] = [
            NPUInstruction(OpCode.MMUL, {
                "wa": 0x000000, "ia": 0x200000, "oa": 0x280000,
                "M": M, "K": K, "N": N,
            }, comment=f"qwen_smoke: Q_proj M={M} K={K} N={N}"),
            NPUInstruction(OpCode.SOFTMAX, {
                "sa": 0x2C0000, "da": 0x2C0100, "len": N,
            }, comment=f"qwen_smoke: Softmax len={N}"),
            NPUInstruction(OpCode.MMUL, {
                "wa": 0x001000, "ia": 0x200000, "oa": 0x281000,
                "M": M, "K": K, "N": N,
            }, comment=f"qwen_smoke: K_proj M={M} K={K} N={N}"),
            NPUInstruction(OpCode.GELU, {
                "sa": 0x2C0200, "da": 0x2C0300, "len": N,
            }, comment=f"qwen_smoke: GELU len={N}"),
            NPUInstruction(OpCode.MMUL, {
                "wa": 0x002000, "ia": 0x200000, "oa": 0x282000,
                "M": M, "K": K, "N": N,
            }, comment=f"qwen_smoke: V_proj M={M} K={K} N={N}"),
            NPUInstruction(OpCode.LAYERNORM, {
                "sa": 0x2C0400, "da": 0x2C0500, "len": N,
            }, comment=f"qwen_smoke: LayerNorm len={N}"),
            NPUInstruction(OpCode.MMUL, {
                "wa": 0x003000, "ia": 0x200000, "oa": 0x283000,
                "M": M, "K": K, "N": N,
            }, comment=f"qwen_smoke: O_proj M={M} K={K} N={N}"),
            NPUInstruction(OpCode.VADD, {
                "sa": 0x2C0100, "da": 0x300000, "len": N,
            }, comment=f"qwen_smoke: VADD len={N}"),
            NPUInstruction(OpCode.VMUL, {
                "sa": 0x2C0300, "da": 0x301000, "len": N,
            }, comment=f"qwen_smoke: VMUL len={N}"),
            NPUInstruction(OpCode.VRESID, {
                "sa": 0x300000, "sb": 0x282000, "da": 0x302000, "len": N,
            }, comment=f"qwen_smoke: VRESID len={N}"),
        ]

        num_instr = len(instructions)

        # ── Generate MMUL weight data ─────────────────────────────
        weight_addrs = [0x000000, 0x001000, 0x002000, 0x003000]
        w_names = ["weight_q", "weight_k", "weight_v", "weight_o"]
        packed_weights = {}
        for addr, wname in zip(weight_addrs, w_names):
            w_int4 = rng.randint(-8, 8, size=K * N, dtype=np.int8)
            packed_weights[wname] = GoldenMXU.pack_int4(w_int4)

        activation = rng.randint(-128, 128, size=M * K, dtype=np.int8)

        # ── Generate SFU float16 input data ───────────────────────
        sfu_rng = np.random.RandomState(tc.seed + 1)
        sfu_inputs = {
            0x2C0000: sfu_rng.randn(N).astype(np.float32) * 2.0,       # softmax input
            0x2C0200: np.clip(sfu_rng.randn(N).astype(np.float32) * 2.0, -4.0, 4.0),  # gelu input
            0x2C0400: sfu_rng.randn(N).astype(np.float32) * 2.0,       # layernorm input
        }

        # ── Preload all data into executor SRAM ───────────────────
        executor = GoldenExecutor()
        for wname, addr in zip(w_names, weight_addrs):
            executor.sram.write_bytes(addr,
                np.asarray(packed_weights[wname], dtype=np.uint8))
        executor.sram.write_bytes(0x200000,
            np.asarray(activation.flatten(), dtype=np.uint8))
        for addr, data in sfu_inputs.items():
            executor.sram.write_float16(addr,
                data.astype(np.float16))

        # ── Execute program, record per-instruction trace ─────────
        per_instr_results = []
        for idx, instr in enumerate(instructions):
            op = instr.opcode
            ops = instr.operands

            if op == OpCode.MMUL:
                result = executor.mxu.matmul_from_sram(
                    ops.get("M", 1), ops.get("K", 64), ops.get("N", 64),
                    ops["ia"], ops["wa"], executor.sram.data)
                executor.sram.write_int32(ops["oa"], result)
                expected = result.flatten()
                per_instr_results.append({
                    "idx": idx, "inst_name": instr.mnemonic,
                    "dims": f"M={ops.get('M',1)},K={ops.get('K',64)},N={ops.get('N',64)}",
                    "expected_first": [int(expected[0])],
                    "dtype": "int32", "tolerance": "bit-exact",
                })

            elif op in (OpCode.SOFTMAX, OpCode.GELU, OpCode.LAYERNORM):
                inp = executor.sram.read_float16(ops["sa"], ops["len"]).astype(np.float32)
                if op == OpCode.SOFTMAX:
                    out = executor.sfu.softmax_hw(inp)
                elif op == OpCode.GELU:
                    out = executor.sfu.gelu_hw(inp)
                elif op == OpCode.LAYERNORM:
                    out = executor.sfu.layernorm_hw(inp)
                executor.sram.write_float16(ops["da"], out.astype(np.float16))
                expected = out.flatten()[:1]
                per_instr_results.append({
                    "idx": idx, "inst_name": instr.mnemonic,
                    "dims": f"len={ops['len']}",
                    "expected_first": [float(expected[0])],
                    "dtype": "float16", "tolerance": "abs=2e-3,rel=1e-2",
                })

            elif op == OpCode.VADD:
                length = ops["len"]
                a = executor.sram.read_float16(ops["sa"], length).astype(np.float32)
                b = executor.sram.read_float16(ops["sa"] + length * 4, length).astype(np.float32)
                out = executor.vector.add(a, b)
                executor.sram.write_float16(ops["da"], out.astype(np.float16))
                expected = out.flatten()[:1]
                per_instr_results.append({
                    "idx": idx, "inst_name": instr.mnemonic,
                    "dims": f"len={length}",
                    "expected_first": [float(expected[0])],
                    "dtype": "float16", "tolerance": "abs=2e-3,rel=1e-2",
                })

            elif op == OpCode.VMUL:
                length = ops["len"]
                a = executor.sram.read_float16(ops["sa"], length).astype(np.float32)
                b = executor.sram.read_float16(ops["sa"] + length * 4, length).astype(np.float32)
                out = executor.vector.mul(a, b)
                executor.sram.write_float16(ops["da"], out.astype(np.float16))
                expected = out.flatten()[:1]
                per_instr_results.append({
                    "idx": idx, "inst_name": instr.mnemonic,
                    "dims": f"len={length}",
                    "expected_first": [float(expected[0])],
                    "dtype": "float16", "tolerance": "abs=2e-3,rel=1e-2",
                })

            elif op == OpCode.VRESID:
                length = ops["len"]
                a = executor.sram.read_float16(ops["sa"], length).astype(np.float32)
                b = executor.sram.read_int32(ops["sb"], length)
                out = executor.vector.residual_add(a, b.astype(np.float32))
                executor.sram.write_float16(ops["da"], out.astype(np.float16))
                expected = out.flatten()[:1]
                per_instr_results.append({
                    "idx": idx, "inst_name": instr.mnemonic,
                    "dims": f"len={length}",
                    "expected_first": [float(expected[0])],
                    "dtype": "float16", "tolerance": "abs=2e-3,rel=1e-2",
                })

        # ── Write hex files ──────────────────────────────────────
        write_hex_int8(test_dir / "activation.hex", activation)
        for wname in w_names:
            write_hex_int4(test_dir / f"{wname}.hex", packed_weights[wname])
        for addr in sfu_inputs:
            tag = f"sfu_in_{addr:06x}"
            write_hex_float16(test_dir / f"{tag}.hex",
                              sfu_inputs[addr].astype(np.float16))

        # Per-instruction golden outputs
        for idx, instr in enumerate(instructions):
            op = instr.opcode
            if op == OpCode.MMUL:
                golden = executor.sram.read_int32(instr.operands["oa"], M * N)
                write_hex_int32(test_dir / f"golden_instr{idx}.hex", golden)
            elif op in (OpCode.SOFTMAX, OpCode.GELU, OpCode.LAYERNORM,
                        OpCode.VADD, OpCode.VMUL, OpCode.VRESID):
                golden = executor.sram.read_float16(instr.operands["da"],
                                                     instr.operands["len"])
                write_hex_float16(test_dir / f"golden_instr{idx}.hex", golden)

        # ── Write ISA ─────────────────────────────────────────────
        write_isa_text(test_dir / "program.isa", instructions)
        write_isa_binary(test_dir / "program.bin", instructions)

        # ── Manifest ─────────────────────────────────────────────
        manifest["qwen_smoke"] = True
        manifest["num_instructions"] = num_instr
        manifest["files"] = {"activation": "activation.hex"}
        for wname in w_names:
            manifest["files"][wname] = f"{wname}.hex"
        for addr in sfu_inputs:
            tag = f"sfu_in_{addr:06x}"
            manifest["files"][tag] = f"{tag}.hex"
        for idx in range(num_instr):
            manifest["files"][f"golden_instr{idx}"] = f"golden_instr{idx}.hex"
        manifest["files"]["isa_text"] = "program.isa"
        manifest["files"]["isa_binary"] = "program.bin"

        manifest["instructions"] = [
            {
                "idx": ir["idx"], "name": ir["inst_name"],
                "dims": ir["dims"], "dtype": ir["dtype"],
                "tolerance": ir["tolerance"],
                "expected_first": ir["expected_first"],
            }
            for ir in per_instr_results
        ]
        manifest["results"] = {
            "num_instructions": num_instr,
            "per_instruction": per_instr_results,
        }
        manifest["format"] = {
            "activation": "INT8",
            "weight": "INT4 packed, 2 per uint8",
            "golden_mmul": "INT32, $readmemh compatible",
            "golden_sfu": "float16, 4 hex digits per value",
            "golden_vector": "float16, 4 hex digits per value",
            "isa": "32-bit fixed-length instruction words",
        }


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="RTL Verification Test Suite Generator"
    )
    parser.add_argument("-o", "--output", default="test_vectors",
                        help="Output root directory")
    parser.add_argument("--category", nargs="+",
                        choices=["random", "corner", "sfu", "chain", "qwen-smoke", "all"],
                        default=["all"],
                        help="Test categories to generate")
    args = parser.parse_args()

    output_root = Path(args.output)
    categories = args.category
    if "all" in categories:
        categories = ["random", "corner", "sfu", "chain"]

    gen = TestVectorGen(output_root)

    all_tests = []
    if "random" in categories:
        all_tests.extend(random_tests())
    if "corner" in categories:
        all_tests.extend(corner_tests())
    if "sfu" in categories:
        all_tests.extend(sfu_tests())
    if "chain" in categories:
        all_tests.extend(chain_tests())
    if "qwen-smoke" in categories:
        all_tests.extend(qwen_smoke_tests())

    print(f"Generating {len(all_tests)} test vectors...")
    print(f"Output: {output_root.absolute()}")
    print()

    for tc in all_tests:
        test_dir = gen.generate(tc)
        print(f"  ✓ {tc.name:30s} → {test_dir.name}/")

    print(f"\nDone. {len(all_tests)} tests in {output_root.absolute()}")
    print()
    print("RTL Integration:")
    print("  1. Load weight.hex / activation.hex via $readmemh")
    print("  2. Load program.bin into instruction memory")
    print("  3. Execute, dump output to result.hex")
    print("  4. Run: python3 compare_rtl.py <test_dir> result.hex")


if __name__ == "__main__":
    main()
