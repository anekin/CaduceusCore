#!/usr/bin/env python3
"""
MMIO Consistency Checker: gen/npu_abi.py ↔ gen/npu_abi.h

After Todo 2 migration, the authoritative sources are the generated
artifacts from spec/npu_abi.json → gen/.  This script verifies that
the Python and C generated bindings agree on all base addresses,
register offsets, and struct field layouts.

Usage:
    python3 sim/check_mmio_map.py           # from CaduceusCore/
    python3 check_mmio_map.py               # from sim/
"""

import ast
import os
import re
import sys


# ──────────────────────────────────────────────
#  gen/npu_abi.py parser (Python AST)
# ──────────────────────────────────────────────

class _GenPyExtractor(ast.NodeVisitor):
    """Visit gen/npu_abi.py AST, collecting Addr bases and per-module offsets."""

    def __init__(self):
        self.bases: dict[str, int] = {}
        self.modules: dict[str, dict[str, int]] = {}
        self._current_class: str | None = None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        old = self._current_class
        self._current_class = node.name
        if node.name != "Addr":
            self.modules.setdefault(node.name, {})
        self.generic_visit(node)
        self._current_class = old

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._current_class is None:
            return
        targets = [t for t in node.targets if isinstance(t, ast.Name)]
        if not targets:
            return
        name = targets[0].id
        val = _eval_ast_expr(node.value)
        if val is None:
            return
        if self._current_class == "Addr":
            self.bases[name] = val
        else:
            self.modules[self._current_class][name] = val


def _eval_ast_expr(node: ast.expr) -> int | None:
    """Evaluate a Python AST expression node to an integer, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    try:
        code = compile(ast.Expression(body=node), "<abi>", "eval")
        result = eval(code, {})
        if isinstance(result, int):
            return result
    except Exception:
        pass
    return None


def parse_gen_abi_py(path: str) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    visitor = _GenPyExtractor()
    visitor.visit(tree)
    return visitor.bases, visitor.modules


# ──────────────────────────────────────────────
#  gen/npu_abi.h parser (regex)
# ──────────────────────────────────────────────

_RE_DEFINE = re.compile(
    r"#define\s+NPU_(\w+)\s+"        # capture name after NPU_ prefix
    r"(0x[0-9A-Fa-f]+"              # hex literal
    r"|\([0-9][0-9 \t*+\-/]*\)"     # arithmetic expr
    r")",
)

_RE_TYPEDEF_STRUCT = re.compile(
    r"typedef\s+struct\s*\{" r"(.*?)" r"\}\s*npu_(\w+)_t\s*;", re.DOTALL
)
_RE_FIELD = re.compile(
    r"volatile\s+uint32_t\s+(\w+)"   # volatile uint32_t NAME
    r"\s*;"                           # semicolon terminator
)
_RE_FIELD_ARRAY = re.compile(
    r"volatile\s+uint32_t\s+(\w+)"   # volatile uint32_t NAME
    r"\s*\["                          # array subscript
)
_RE_PAD = re.compile(
    r"uint8_t\s+(_pad_\w+)"          # uint8_t _pad_* padding field
    r"\s*\[(\d+)\]\s*;"              # array size
)


def _parse_c_int_literal(literal: str) -> int:
    """Parse a C integer literal or simple parenthesised expression."""
    literal = literal.strip()
    if literal.startswith("("):
        inner = literal[1 : literal.rfind(")")]
        return int(eval(inner, {}))
    literal = literal.rstrip("ULul")
    return int(literal, 16)


def parse_gen_abi_h(path: str) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    with open(path, encoding="utf-8") as f:
        content = f.read()

    defines: dict[str, int] = {}
    for m in _RE_DEFINE.finditer(content):
        name = m.group(1)
        literal = m.group(2)
        try:
            defines[name] = _parse_c_int_literal(literal)
        except (ValueError, SyntaxError):
            pass

    structs: dict[str, dict[str, int]] = {}
    for m in _RE_TYPEDEF_STRUCT.finditer(content):
        body = m.group(1)
        basename = m.group(2)
        fields: dict[str, int] = {}

        # Build ordered list of (offset, name_or_None) from the body
        members: list[tuple[int, str | None]] = []
        for match in re.finditer(
            # Match volatile uint32 (scalar or array) OR uint8_t padding
            r"volatile\s+uint32_t\s+(\w+)\s*(\[[^\]]*\])?\s*;"
            r"|uint8_t\s+(_pad_\w+)\s*\[\d+\]\s*;",
            body
        ):
            vol_name = match.group(1)
            arr_spec = match.group(2)
            pad_name = match.group(3)

            if pad_name is not None:
                # Padding: advances offset by array size (bytes)
                sz = int(re.search(r"\[(\d+)\]", match.group(0)).group(1))
                members.append((sz, None))
                continue

            if vol_name is None:
                continue

            offset = sum(size for size, _ in members)
            members.append((4, vol_name))

            if vol_name.startswith("_"):
                continue
            fields[vol_name] = offset

        structs[basename] = fields

    return defines, structs


# ──────────────────────────────────────────────
#  Comparison logic
# ──────────────────────────────────────────────

# Map Python Addr attribute → C #define NPU_xxx name
_BASE_MAPPING: list[tuple[str, str]] = [
    ("MXU",      "MXU_BASE"),
    ("SFU",      "SFU_BASE"),
    ("VECTOR",   "VECTOR_BASE"),
    ("DMA",      "DMA_BASE"),
    ("PCIE",     "PCIE_BASE"),
    ("DOORBELL", "DOORBELL_BASE"),
    ("INTC",     "INTC_BASE"),
    ("PCIE_DMA", "PCIE_DMA_BASE"),
    ("SRAM",     "SRAM_BASE"),
    ("DRAM",     "DRAM_BASE"),
]

# Map Python class → C struct basename (after npu_ and before _t)
_MODULE_MAPPING: list[tuple[str, str]] = [
    ("MXU",      "mxu"),
    ("SFU",      "sfu"),
    ("VECTOR",   "vector"),
    ("DMA",      "dma"),
    ("DOORBELL", "doorbell"),
    ("INTC",     "intc"),
    ("PCIE_DMA", "pcie_dma"),
]


def run_check(py_path: str, h_path: str) -> tuple[bool, list[str]]:
    """Run all consistency checks. Returns (passed, error_messages)."""
    py_bases, py_mods = parse_gen_abi_py(py_path)
    c_defines, c_structs = parse_gen_abi_h(h_path)
    errors: list[str] = []

    # ── 1. Base address cross-check ──
    for py_name, c_name in _BASE_MAPPING:
        py_val = py_bases.get(py_name)
        c_val = c_defines.get(c_name)
        if py_val is None:
            errors.append(f"MISSING in gen/npu_abi.py: Addr.{py_name}")
        elif c_val is None:
            errors.append(f"MISSING in gen/npu_abi.h: NPU_{c_name}")
        elif py_val != c_val:
            errors.append(
                f"BASE MISMATCH: Addr.{py_name}=0x{py_val:08X}  "
                f"vs  NPU_{c_name}=0x{c_val:08X}"
            )

    # ── 2. SRAM/DRAM expected-value check ──
    for py_name, expected in [
        ("SRAM", 0x2000_0000),
        ("DRAM", 0x8000_0000),
    ]:
        actual = py_bases.get(py_name)
        if actual is None:
            errors.append(f"MISSING in gen/npu_abi.py: Addr.{py_name}")
        elif actual != expected:
            errors.append(
                f"EXPECTED VALUE: Addr.{py_name}=0x{actual:08X}  "
                f"expected 0x{expected:08X} per unified address-space spec"
            )

    # ── 3. Per-module register offsets ──
    matched_count = 0

    for py_cls, c_basename in _MODULE_MAPPING:
        py_regs = py_mods.get(py_cls, {})
        c_regs = c_structs.get(c_basename, {})

        py_offsets = {k: v for k, v in py_regs.items()
                      if not k.startswith("__")}

        for reg_name, py_offset in py_offsets.items():
            if reg_name == "BASE":
                continue
            c_offset = c_regs.get(reg_name)
            if c_offset is None:
                errors.append(
                    f"REG MISSING in C: {py_cls}.{reg_name} "
                    f"(npu_{c_basename}_t has no field {reg_name})"
                )
            elif py_offset != c_offset:
                errors.append(
                    f"OFFSET MISMATCH: {py_cls}.{reg_name}=0x{py_offset:02X}  "
                    f"vs  npu_{c_basename}_t.{reg_name}=0x{c_offset:02X}"
                )
            else:
                matched_count += 1

        for reg_name, c_offset in c_regs.items():
            if reg_name not in py_offsets:
                errors.append(
                    f"REG MISSING in Python: npu_{c_basename}_t.{reg_name} "
                    f"not found in gen/npu_abi.py class {py_cls}"
                )

        # Intra-module offset conflict check
        seen: dict[int, str] = {}
        for reg_name, offset in sorted(py_offsets.items(), key=lambda x: x[1]):
            if offset in seen:
                errors.append(
                    f"INTRA-MODULE CONFLICT in {py_cls}: "
                    f"{reg_name}(0x{offset:02X}) overlaps with "
                    f"{seen[offset]}(0x{offset:02X})"
                )
            else:
                seen[offset] = reg_name

    if errors:
        return False, errors

    return True, [f"{matched_count} registers match"]


# ──────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────

def main() -> None:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.dirname(script_dir)  # CaduceusCore/

    py_path = os.path.join(base_dir, "gen", "npu_abi.py")
    h_path  = os.path.join(base_dir, "gen", "npu_abi.h")

    if not os.path.isfile(py_path):
        print(f"ERROR: gen/npu_abi.py not found at {py_path}", file=sys.stderr)
        sys.exit(2)
    if not os.path.isfile(h_path):
        print(f"ERROR: gen/npu_abi.h not found at {h_path}", file=sys.stderr)
        sys.exit(2)

    passed, lines = run_check(py_path, h_path)

    if passed:
        print(f"✅ MMIO map consistent (gen artifacts): {lines[0]}")
        sys.exit(0)
    else:
        print("❌ MMIO consistency FAILED:")
        for line in lines:
            print(f"  - {line}")
        sys.exit(1)


if __name__ == "__main__":
    main()
