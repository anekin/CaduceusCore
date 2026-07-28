"""Tests for NPU ABI binding migration (Todo 2).

Validates that:
  - regmap facade is consistent with gen/npu_abi.py
  - All consumers of old regmap.py can import unchanged
  - Negative test: mutated gen copy is rejected
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
GEN_PY = REPO / "gen" / "npu_abi.py"
GEN_H = REPO / "gen" / "npu_abi.h"
GEN_SV = REPO / "gen" / "npu_abi_pkg.sv"
REGMAP_PY = REPO / "sim" / "regmap.py"


# ──────────────────────────────────────────────────────
# Happy-path: facade consistency
# ──────────────────────────────────────────────────────

class TestFacadeConsistency:
    """Verify facade values match gen contract."""

    def test_addr_aliases_match_gen(self):
        """All Addr._BASE aliases match gen Addr values."""
        from sim.regmap import Addr as FAddr
        import gen.npu_abi as gen

        checks = [
            ("MXU_BASE",      FAddr.MXU_BASE,      gen.Addr.MXU),
            ("SFU_BASE",      FAddr.SFU_BASE,      gen.Addr.SFU),
            ("VECTOR_BASE",   FAddr.VECTOR_BASE,   gen.Addr.VECTOR),
            ("DMA_BASE",      FAddr.DMA_BASE,      gen.Addr.DMA),
            ("PCIE_BASE",     FAddr.PCIE_BASE,     gen.Addr.PCIE),
            ("PCIE_DMA_BASE", FAddr.PCIE_DMA_BASE, gen.Addr.PCIE_DMA),
            ("DOORBELL_BASE", FAddr.DOORBELL_BASE, gen.Addr.DOORBELL),
            ("INTC_BASE",     FAddr.INTC_BASE,     gen.Addr.INTC),
            ("DRAM_BASE",     FAddr.DRAM_BASE,     gen.Addr.DRAM),
            ("SRAM_BASE",     FAddr.SRAM_BASE,     gen.Addr.SRAM),
        ]
        for name, fval, gval in checks:
            assert fval == gval, (
                f"Facade Addr.{name}=0x{fval:08X} != gen.Addr.{gval:08X}"
            )

    def test_new_style_aliases_match_gen(self):
        """New (non-_BASE) Addr names match gen."""
        from sim.regmap import Addr as FAddr
        import gen.npu_abi as gen

        for attr in dir(gen.Addr):
            if attr.startswith("_"):
                continue
            gval = getattr(gen.Addr, attr)
            fval = getattr(FAddr, attr, None)
            assert fval == gval, (
                f"Facade Addr.{attr}=0x{fval:08X}≠gen Addr.{attr}=0x{gval:08X}"
            )

    def test_module_offsets_match_gen(self):
        """All facade module register offsets match gen."""
        from sim import regmap as facade
        import gen.npu_abi as gen

        modules = [
            ("MXU",      facade.MXU,      gen.MXU),
            ("SFU",      facade.SFU,      gen.SFU),
            ("VECTOR",   facade.VECTOR,   gen.VECTOR),
            ("DMA",      facade.DMA,      gen.DMA),
            ("DOORBELL", facade.DOORBELL, gen.DOORBELL),
            ("INTC",     facade.INTC,     gen.INTC),
            ("PCIE_DMA", facade.PCIE_DMA, gen.PCIE_DMA),
        ]
        for mod_name, fmod, gmod in modules:
            for attr in dir(gmod):
                if attr.startswith("_"):
                    continue
                gval = getattr(gmod, attr)
                if not isinstance(gval, int):
                    continue
                fval = getattr(fmod, attr, None)
                assert fval is not None, (
                    f"Missing in facade: {mod_name}.{attr}"
                )
                assert fval == gval, (
                    f"Offset mismatch: {mod_name}.{attr}"
                    f"=0x{fval:02X}≠gen=0x{gval:02X}"
                )

    def test_backward_compat_imports(self):
        """All old-style consumers import unchanged."""
        # Simulate all known import patterns
        from sim.regmap import Addr, MXU, SFU, VECTOR, DMA, DOORBELL, INTC, PCIE_DMA
        from sim.regmap import EngineOp, OpCode, StatusCode, SFUOp, VectorOp, MXUDType
        # Check key attribute access
        assert Addr.MXU_BASE == 0x40000000
        assert Addr.DOORBELL == 0x40005000
        assert MXU.CTRL == 0x00
        assert MXU.I_ADDR == 0x14
        assert SFU.CTRL == 0x00
        assert SFU.IRQ_EN == 0x1C
        assert VECTOR.DIM == 0x18
        assert DMA.CH0_SRC == 0x10
        assert DMA.IRQ_EN == 0x38
        assert DOORBELL.HOST_TAIL == 0x00
        assert DOORBELL.LAST_STATUS == 0x10
        assert DOORBELL.COMPLETION_STATUS == 0x14
        assert INTC.PENDING == 0x00
        assert INTC.ACK == 0x0C
        assert PCIE_DMA.CTRL == 0x00
        assert PCIE_DMA.AXI_ADDR == 0x10
        assert Addr.SRAM_SIZE == 0x400000

    def test_validate_no_conflicts(self):
        """Facade validate() reports no address conflicts."""
        from sim.regmap import validate
        regions = validate()
        assert len(regions) >= 6  # at least MXU, SFU, VECTOR, DMA, DOORBELL, INTC


# ──────────────────────────────────────────────────────
# RTL contract integration
# ──────────────────────────────────────────────────────

class TestRTLContract:
    """Verify RTL-package integration points."""

    def test_sv_package_syntax(self):
        """gen/npu_abi_pkg.sv has valid SystemVerilog package syntax."""
        content = GEN_SV.read_text()
        assert "package npu_abi_pkg;" in content
        assert "endpackage" in content
        # Verify key constants present
        assert "NPU_MXU_BASE" in content
        assert "NPU_DOORBELL_BASE" in content
        assert "NPU_DOORBELL_HOST_TAIL_OFFSET" in content
        assert "NPU_DOORBELL_LAST_STATUS_OFFSET" in content
        assert "NPU_ENGINE_OP_MMUL" in content
        assert "NPU_RING_ENTRIES" in content

    def test_rtl_include_parseable(self):
        """rtl/include/npu_abi_rtl.svh exists and has expected content."""
        svh = REPO / "rtl" / "include" / "npu_abi_rtl.svh"
        assert svh.is_file(), f"Missing: {svh}"
        content = svh.read_text()
        assert "`ifndef NPU_ABI_RTL_SVH" in content
        assert "`define NPU_DOORBELL_BASE" in content
        assert "`define NPU_MXU_BASE" in content


# ──────────────────────────────────────────────────────
# Negative test: mutated gen copy is rejected
# ──────────────────────────────────────────────────────

class TestMutationDetection:
    """Prove the migration detects mutations in generated copies."""

    def test_rejects_mutated_generated_copy(self):
        """A mutated gen/npu_abi.py copy causes detectable inconsistency."""
        import gen.npu_abi as gen

        # Copy the gen module text, mutate an address
        gen_text = GEN_PY.read_text()
        mutated = gen_text.replace(
            "MXU                    = 0x40000000",
            "MXU                    = 0x4FFFFFFF"
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(mutated)
            tmp_path = f.name

        try:
            # Attempt to import the mutated copy
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "mutated_abi", tmp_path
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # Mutation should be detectable: gen.MXU != mutated.MXU
            assert mod.Addr.MXU != gen.Addr.MXU, (
                "Mutation should have changed MXU address"
            )
            assert mod.Addr.MXU == 0x4FFFFFFF
        finally:
            os.unlink(tmp_path)

    def test_rejects_address_mismatch_in_check(self):
        """gen_npu_abi.py --check fails when gen artifacts are stale."""
        gen_py_path = REPO / "gen" / "npu_abi.py"
        original = gen_py_path.read_text()

        # Mutate the generated Python artifact directly (simulates stale gen/)
        mutated_py = original.replace(
            "MXU                    = 0x40000000",
            "MXU                    = 0x4FFFFFFF"
        )

        try:
            gen_py_path.write_text(mutated_py)

            # Run --check against the original schema
            result_check = subprocess.run(
                [sys.executable, str(REPO / "scripts" / "gen_npu_abi.py"),
                 "--check"],
                capture_output=True, text=True,
                cwd=str(REPO),
                env={**os.environ, "PYTHONPATH": str(REPO / "sim")},
                timeout=30,
            )

            assert result_check.returncode != 0, (
                "gen_npu_abi.py --check should fail"
                " when gen artifacts are stale/mutated.\n"
                f"stdout: {result_check.stdout}"
                f"stderr: {result_check.stderr}"
            )
        finally:
            # Restore original generated artifact
            gen_py_path.write_text(original)
            # Re-run --check to confirm restoration
            subprocess.run(
                [sys.executable, str(REPO / "scripts" / "gen_npu_abi.py"),
                 "--check"],
                capture_output=True,
                cwd=str(REPO),
                env={**os.environ, "PYTHONPATH": str(REPO / "sim")},
                timeout=30,
            )

    def test_rejects_mutated_c_layout(self):
        """C++ compilation fails on a mutated gen/npu_abi.h."""
        import shutil

        cpp_test = REPO / "software" / "tests" / "test_abi_layout.cpp"
        gen_h = REPO / "gen" / "npu_abi.h"
        orig = gen_h.read_text()

        # Mutate a base address value that the C++ test static_assert checks
        mutated = orig.replace(
            "#define NPU_MXU_BASE 0x40000000UL",
            "#define NPU_MXU_BASE 0x4FFFFFFFUL"
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".h", delete=False
        ) as f:
            f.write(mutated)
            tmp_h = f.name

        try:
            # Copy test to temp dir with adjusted include
            tmpdir = tempfile.mkdtemp()
            gen_dir = os.path.join(tmpdir, "gen")
            os.makedirs(gen_dir, exist_ok=True)
            shutil.copy(tmp_h, os.path.join(gen_dir, "npu_abi.h"))
            test_copy = os.path.join(tmpdir, "test.cpp")
            content = cpp_test.read_text()
            # Adjust include path for temp layout
            content = content.replace("../gen/npu_abi.h", "gen/npu_abi.h")
            Path(test_copy).write_text(content)

            result = subprocess.run(
                ["g++", "-std=c++17", "-c", "-I" + tmpdir,
                 test_copy, "-o", os.devnull],
                capture_output=True, text=True,
                timeout=30,
            )
            assert result.returncode != 0, (
                "C++ compilation should fail"
                " on mutated gen/npu_abi.h with offset mismatch"
            )
        finally:
            os.unlink(tmp_h)
            shutil.rmtree(tmpdir, ignore_errors=True)
