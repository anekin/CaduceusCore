#!/usr/bin/env python3
"""
MMIO Consistency Checker: regmap.py ↔ npu-regmap.h

Parses sim/regmap.py (Python AST) and firmware/npu-regmap.h (C regex),
extracts all #define BASE addresses and register offsets, compares them,
and exits 0 on PASS or 1 on FAIL with a list of mismatches.

Usage:
    python3 sim/check_mmio_map.py           # from CaduceusCore/
    python3 check_mmio_map.py               # from sim/
"""

import ast
import os
import re
import sys


# ──────────────────────────────────────────────
#  regmap.py parser (Python AST)
# ──────────────────────────────────────────────

class _RegmapExtractor(ast.NodeVisitor):
    """Visit regmap.py AST, collecting Addr bases and per-module offsets."""

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
    # Fallback: compile + eval for expressions like 4 * 1024 * 1024
    try:
        code = compile(ast.Expression(body=node), "<regmap>", "eval")
        result = eval(code, {})
        if isinstance(result, int):
            return result
    except Exception:
        pass
    return None


def parse_regmap_py(path: str) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    visitor = _RegmapExtractor()
    visitor.visit(tree)
    return visitor.bases, visitor.modules


# ──────────────────────────────────────────────
#  npu-regmap.h parser (regex)
# ──────────────────────────────────────────────

_RE_DEFINE = re.compile(
    r"#define\s+NPU_(\w+)\s+"       # capture name after NPU_ prefix
    r"(0x[0-9A-Fa-f]+"             # hex literal
    r"|\([0-9][0-9 \t*+\-/]*\)"    # arithmetic expr e.g. (4*1024*1024)
    r")"
    r"\s*(?:UL)?"                   # optional UL suffix
)

_RE_TYPEDEF_STRUCT = re.compile(
    r"typedef\s+struct\s*\{" r"(.*?)" r"\}\s*npu_(\w+)_t\s*;", re.DOTALL
)

_RE_FIELD = re.compile(r"volatile\s+uint32_t\s+(\w+)\s*;")


def _parse_c_int_literal(literal: str) -> int:
    """Parse a C integer literal or simple parenthesised expression."""
    literal = literal.strip()
    if literal.startswith("("):
        # e.g. "(4 * 1024 * 1024)"  → eval as Python (safe for arithmetic)
        inner = literal[1 : literal.rfind(")")]
        return int(eval(inner, {}))
    # Strip UL suffix if present (already handled by regex, but be safe)
    literal = literal.rstrip("ULul")
    return int(literal, 16)


def parse_npu_regmap_h(path: str) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """Return (defines, structs) where defines maps BASE_NAME→value and
    structs maps struct_basename→{field_name: offset}."""
    with open(path, encoding="utf-8") as f:
        content = f.read()

    defines: dict[str, int] = {}
    for m in _RE_DEFINE.finditer(content):
        name = m.group(1)        # e.g. MXU_BASE, SRAM_BASE, SRAM_SIZE
        literal = m.group(2)
        try:
            defines[name] = _parse_c_int_literal(literal)
        except (ValueError, SyntaxError):
            # skip non-numeric defines (e.g. pointer casts)
            pass

    structs: dict[str, dict[str, int]] = {}
    for m in _RE_TYPEDEF_STRUCT.finditer(content):
        body = m.group(1)
        basename = m.group(2)    # e.g. "mxu", "sfu", "dma"
        fields: dict[str, int] = {}
        idx = 0
        for fm in _RE_FIELD.finditer(body):
            fname = fm.group(1)
            offset = idx * 4
            idx += 1
            if fname.startswith("_"):   # skip padding fields
                continue
            fields[fname] = offset
        structs[basename] = fields

    return defines, structs


# ──────────────────────────────────────────────
#  Comparison logic
# ──────────────────────────────────────────────

# Map Python Addr attribute → C #define NPU_xxx name
_BASE_MAPPING: list[tuple[str, str]] = [
    ("MXU_BASE",     "MXU_BASE"),
    ("SFU_BASE",     "SFU_BASE"),
    ("VECTOR_BASE",  "VECTOR_BASE"),
    ("DMA_BASE",     "DMA_BASE"),
    ("PCIE_BASE",    "PCIE_BASE"),
    ("DOORBELL",     "DOORBELL_BASE"),   # note: Python uses "DOORBELL" without _BASE
    ("INTC_BASE",    "INTC_BASE"),
    ("SRAM_BASE",    "SRAM_BASE"),
]

# Map Python class → C struct basename (the part after npu_ and before _t)
_MODULE_MAPPING: list[tuple[str, str]] = [
    ("MXU",      "mxu"),
    ("SFU",      "sfu"),
    ("VECTOR",   "vector"),
    ("DMA",      "dma"),
    ("DOORBELL", "doorbell"),
    ("INTC",     "intc"),
]


def run_check(regmap_path: str, header_path: str) -> tuple[bool, list[str]]:
    """Run all consistency checks. Returns (passed, error_messages)."""
    py_bases, py_mods = parse_regmap_py(regmap_path)
    c_defines, c_structs = parse_npu_regmap_h(header_path)
    errors: list[str] = []

    # ── 1.  Base address cross-check ──
    for py_name, c_name in _BASE_MAPPING:
        py_val = py_bases.get(py_name)
        c_val = c_defines.get(c_name)
        if py_val is None:
            errors.append(f"MISSING in regmap.py: Addr.{py_name}")
        elif c_val is None:
            errors.append(f"MISSING in npu-regmap.h: NPU_{c_name}")
        elif py_val != c_val:
            errors.append(
                f"BASE MISMATCH: Addr.{py_name}=0x{py_val:08X}  "
                f"vs  NPU_{c_name}=0x{c_val:08X}"
            )

    # ── 2.  SRAM_BASE / DRAM_BASE expected-value check ──
    for name, expected in [
        ("SRAM_BASE", 0x2000_0000),
        ("DRAM_BASE", 0x8000_0000),
    ]:
        actual = py_bases.get(name)
        if actual is None:
            errors.append(f"MISSING in regmap.py: Addr.{name}")
        elif actual != expected:
            errors.append(
                f"EXPECTED VALUE: Addr.{name}=0x{actual:08X}  "
                f"expected 0x{expected:08X} per unified address-space spec"
            )

    # ── 3.  Per-module register offsets ──
    matched_count = 0

    for py_cls, c_basename in _MODULE_MAPPING:
        py_regs = py_mods.get(py_cls, {})
        c_regs = c_structs.get(c_basename, {})

        # Filter out BASE from register dict (it's not an offset register)
        py_offsets = {k: v for k, v in py_regs.items() if not k.startswith("__")}

        # Cross-check: Python → C
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

        # Cross-check: C → Python (catch fields only in C)
        for reg_name, c_offset in c_regs.items():
            if reg_name not in py_offsets:
                errors.append(
                    f"REG MISSING in Python: npu_{c_basename}_t.{reg_name} "
                    f"not found in regmap.py class {py_cls}"
                )

        # Intra-module offset conflict check (within Python definition)
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
    # Resolve paths relative to this script's directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.dirname(script_dir)  # CaduceusCore/

    regmap_path = os.path.join(base_dir, "sim", "regmap.py")
    header_path = os.path.join(base_dir, "firmware", "npu-regmap.h")

    if not os.path.isfile(regmap_path):
        print(f"ERROR: regmap.py not found at {regmap_path}", file=sys.stderr)
        sys.exit(2)
    if not os.path.isfile(header_path):
        print(f"ERROR: npu-regmap.h not found at {header_path}", file=sys.stderr)
        sys.exit(2)

    passed, lines = run_check(regmap_path, header_path)

    if passed:
        print(f"✅ MMIO map consistent: {lines[0]}")
        sys.exit(0)
    else:
        print("❌ MMIO consistency FAILED:")
        for line in lines:
            print(f"  - {line}")
        sys.exit(1)


if __name__ == "__main__":
    main()
