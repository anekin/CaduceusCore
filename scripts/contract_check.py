#!/usr/bin/env python3
"""
SoC Golden Contract Drift Checker

Validates that all 4 generated ABI artifacts (Python, C header, firmware
header, SystemVerilog package) match the authoritative ABI schema
(spec/npu_abi.json). Reports any drift with file/line/expected/actual details.

Usage:
    python3 scripts/contract_check.py --check          # validate all artifacts
    python3 scripts/contract_check.py --check --verbose # show all comparisons

Exit codes:
    0 — all artifacts match the schema (no drift)
    1 — drift detected (mismatch or missing artifact)
    2 — usage error
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO / "spec" / "npu_abi.json"
GEN_DIR = REPO / "gen"

ARTIFACT_FILES = [
    "npu_abi.py",
    "npu_abi.h",
    "npu_abi_firmware.h",
    "npu_abi_pkg.sv",
]

# ── colour codes for terminal output ───────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"

# ═══════════════════════════════════════════════════════════════════
# Schema loader
# ═══════════════════════════════════════════════════════════════════

def load_schema() -> dict[str, Any]:
    if not SCHEMA_PATH.is_file():
        msg = f"ERROR: Schema not found at {SCHEMA_PATH}"
        sys.exit(msg)
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════════
# Artifact parsers
# ═══════════════════════════════════════════════════════════════════

def _read_artifact(name: str) -> str:
    """Read an artifact file, exit on missing."""
    path = GEN_DIR / name
    if not path.is_file():
        sys.exit(f"ERROR: missing artifact {path}")
    return path.read_text(encoding="utf-8")


def parse_python(src: str) -> dict[str, Any]:
    """Extract ABI constants from gen/npu_abi.py via regex."""
    result: dict[str, Any] = {
        "base_addrs": {},
        "reg_offsets": {},
        "opcodes": {},
        "isa_opcodes": {},
        "status_codes": {},
        "cap_bits": {},
        "sfu_ops": {},
        "vec_ops": {},
        "mxu_dtype": {},
        "intc": {},
        "desc_sizes": {},
        "ring": {},
    }

    # -- Address regions: parse ONLY from class Addr block,
    #    stopping at the next class or top-level comment marker.
    addr_match = re.search(r"class Addr:.*?(?=\nclass |\n# ═══|\n\n# ═══)", src, re.DOTALL)
    if addr_match:
        addr_block = addr_match.group(0)
        for m in re.finditer(r"^\s+(\w+)\s+=\s+(0x[0-9A-Fa-f]+)", addr_block, re.MULTILINE):
            if m.group(1) == "BASE":
                continue
            result["base_addrs"][m.group(1)] = int(m.group(2), 16)

    # -- All module/non-module classes in the file
    CLASS_BLOCKS = re.finditer(
        r"class (\w+)\s*(?:\([^)]*\))?:.*?(?=\nclass |\n# ═══|\Z)",
        src, re.DOTALL
    )

    for block in CLASS_BLOCKS:
        mod_name = block.group(1)
        block_text = block.group(0)

        # Addr block is handled above (base_addrs); skip it here
        if mod_name == "Addr":
            continue

        # EngineOp, OpCode, StatusCode, SFUOp, VectorOp, MXUDType — extract values
        if mod_name in ("EngineOp",):
            for m in re.finditer(r"^\s+(\w+)\s+=\s+(0x[0-9A-Fa-f]+)", block_text, re.MULTILINE):
                result["opcodes"][m.group(1)] = int(m.group(2), 16)
        elif mod_name in ("OpCode",):
            for m in re.finditer(r"^\s+(\w+)\s+=\s+(0x[0-9A-Fa-f]+)", block_text, re.MULTILINE):
                result["isa_opcodes"][m.group(1)] = int(m.group(2), 16)
        elif mod_name in ("StatusCode",):
            for m in re.finditer(r"^\s+(\w+)\s+=\s+(\d+)", block_text, re.MULTILINE):
                result["status_codes"][m.group(1)] = int(m.group(2))
        elif mod_name in ("SFUOp",):
            for m in re.finditer(r"^\s+(\w+)\s+=\s+(\d+)", block_text, re.MULTILINE):
                result["sfu_ops"][m.group(1)] = int(m.group(2))
        elif mod_name in ("VectorOp",):
            for m in re.finditer(r"^\s+(\w+)\s+=\s+(\d+)", block_text, re.MULTILINE):
                result["vec_ops"][m.group(1)] = int(m.group(2))
        elif mod_name in ("MXUDType",):
            for m in re.finditer(r"^\s+(\w+)\s+=\s+(\d+)", block_text, re.MULTILINE):
                result["mxu_dtype"][m.group(1)] = int(m.group(2))
        else:
            # Module register classes: MXU, SFU, VECTOR, DMA, DOORBELL, INTC, PCIE_DMA
            for m in re.finditer(r"^\s+(\w+)\s+=\s+(0x[0-9A-Fa-f]+)", block_text, re.MULTILINE):
                if m.group(1) == "BASE":
                    continue
                if mod_name not in result["reg_offsets"]:
                    result["reg_offsets"][mod_name] = {}
                result["reg_offsets"][mod_name][m.group(1)] = int(m.group(2), 16)

    # -- Capability bits: CAP_* = 1 << N (top-level in file, not in a class)
    for m in re.finditer(r"CAP_(\w+)\s+=\s+1\s*<<\s*(\d+)", src):
        result["cap_bits"][m.group(1)] = int(m.group(2))

    # -- INTC mappings (top-level)
    for m in re.finditer(r"INTC_(\w+)\s+=\s+1\s*<<\s*(\d+)", src):
        result["intc"][m.group(1)] = int(m.group(2))

    # -- Descriptor sizes
    for m in re.finditer(r"DESC_(\w+)_SIZE\s+=\s+(\d+)", src):
        result["desc_sizes"][m.group(1)] = int(m.group(2))

    # -- Ring config
    for m in re.finditer(r"(RING_ENTRIES|CMD_ENTRY_SIZE|COMPLETION_ENTRY_SIZE)\s+=\s+(\d+)", src):
        result["ring"][m.group(1)] = int(m.group(2))

    return result


def parse_c_header(src: str) -> dict[str, Any]:
    """Extract ABI constants from gen/npu_abi.h."""
    result: dict[str, Any] = {
        "base_addrs": {},
        "reg_offsets": {},
        "opcodes": {},
        "isa_opcodes": {},
        "status_codes": {},
        "cap_bits": {},
        "sfu_ops": {},
        "vec_ops": {},
        "intc": {},
        "ring": {},
    }

    # -- Base addresses: #define NPU_{NAME}_BASE 0x...
    for m in re.finditer(r"#define NPU_(\w+)_BASE (0x[0-9A-Fa-f]+)UL", src):
        result["base_addrs"][m.group(1)] = int(m.group(2), 16)

    # Register offsets: section-based — module name comes from the
    # section comment before each block of defines.
    for section in re.finditer(
        r"/\* ── (\w+) Registers \(base: NPU_\w+_BASE\) .*?\*/\n((?:#define.*?\n)*)",
        src
    ):
        mod_name = section.group(1)
        body = section.group(2)
        for m in re.finditer(
            r"#define NPU_(\w+?)_OFFSET (0x[0-9A-Fa-f]+)UL",
            body
        ):
            full = m.group(1)  # e.g. "DMA_CH0_DST"
            prefix = mod_name + "_"
            if full.startswith(prefix):
                reg_name = full[len(prefix):]
            else:
                reg_name = full
            offset = int(m.group(2), 16)
            if mod_name not in result["reg_offsets"]:
                result["reg_offsets"][mod_name] = {}
            result["reg_offsets"][mod_name][reg_name] = offset

    # -- Engine opcodes: #define NPU_ENGINE_OP_{NAME} value
    for m in re.finditer(r"#define NPU_ENGINE_OP_(\w+) (\d+)", src):
        result["opcodes"][m.group(1)] = int(m.group(2))

    # -- ISA opcodes: #define NPU_ISA_OP_{NAME} value
    for m in re.finditer(r"#define NPU_ISA_OP_(\w+) (\d+)", src):
        result["isa_opcodes"][m.group(1)] = int(m.group(2))

    # -- Status codes: #define NPU_STATUS_{NAME} value
    for m in re.finditer(r"#define NPU_STATUS_(\w+) (\d+)", src):
        result["status_codes"][m.group(1)] = int(m.group(2))

    # -- Capability bits: #define NPU_CAP_{NAME} (1U << N)
    for m in re.finditer(r"#define NPU_CAP_(\w+) \(1U << (\d+)\)", src):
        result["cap_bits"][m.group(1)] = int(m.group(2))

    # -- SFU sub-opcodes: #define SFU_OP_{NAME} value
    for m in re.finditer(r"#define SFU_OP_(\w+) (\d+)", src):
        result["sfu_ops"][m.group(1)] = int(m.group(2))

    # -- Vector sub-opcodes: #define VEC_OP_{NAME} value
    for m in re.finditer(r"#define VEC_OP_(\w+) (\d+)", src):
        result["vec_ops"][m.group(1)] = int(m.group(2))

    # -- INTC mappings: #define INTC_{NAME} (1U << N)
    for m in re.finditer(r"#define INTC_(\w+) \(1U << (\d+)\)", src):
        result["intc"][m.group(1)] = int(m.group(2))

    # -- Ring config: #define NPU_RING_ENTRIES etc.
    for m in re.finditer(r"#define NPU_(RING_ENTRIES|CMD_ENTRY_SIZE|COMPLETION_ENTRY_SIZE) (\d+)", src):
        result["ring"][m.group(1)] = int(m.group(2))

    return result


def parse_firmware_header(src: str) -> dict[str, Any]:
    """Extract ABI constants from gen/npu_abi_firmware.h."""
    result: dict[str, Any] = {
        "base_addrs": {},
        "opcodes": {},
        "status_codes": {},
        "desc_sizes": {},
        "desc_fields": {},
        "ring": {},
    }

    # -- Base addresses: #define NPU_ABI_{NAME}_BASE 0x...
    for m in re.finditer(r"#define NPU_ABI_(\w+)_BASE (0x[0-9A-Fa-f]+)UL", src):
        result["base_addrs"][m.group(1)] = int(m.group(2), 16)

    # -- Engine opcodes: #define NPU_ABI_ENGINE_OP_{NAME} value
    for m in re.finditer(r"#define NPU_ABI_ENGINE_OP_(\w+) (\d+)", src):
        result["opcodes"][m.group(1)] = int(m.group(2))

    # -- Status codes: #define NPU_ABI_STATUS_{NAME} value
    for m in re.finditer(r"#define NPU_ABI_STATUS_(\w+) (\d+)", src):
        result["status_codes"][m.group(1)] = int(m.group(2))

    # -- Descriptor sizes: #define NPU_ABI_DESC_{NAME}_PACKED_SIZE N
    for m in re.finditer(r"#define NPU_ABI_DESC_(\w+)_PACKED_SIZE (\d+)", src):
        result["desc_sizes"][m.group(1)] = int(m.group(2))

    # -- Descriptor field offsets: #define NPU_ABI_DESC_{DESC}_{FIELD}_OFFSET N
    for m in re.finditer(r"#define NPU_ABI_DESC_(\w+?)_(\w+)_OFFSET (\d+)", src):
        desc = m.group(1)
        field = m.group(2)
        if desc == "DESC":  # avoid double-matching
            continue
        if desc not in result["desc_fields"]:
            result["desc_fields"][desc] = {}
        result["desc_fields"][desc][field] = int(m.group(3))

    # -- Ring config
    for m in re.finditer(r"#define NPU_ABI_(RING_ENTRIES|CMD_ENTRY_SIZE|COMPLETION_ENTRY_SIZE) (\d+)", src):
        result["ring"][m.group(1)] = int(m.group(2))

    return result


def parse_sv_package(src: str) -> dict[str, Any]:
    """Extract ABI constants from gen/npu_abi_pkg.sv."""
    result: dict[str, Any] = {
        "base_addrs": {},
        "reg_offsets": {},
        "opcodes": {},
        "isa_opcodes": {},
        "status_codes": {},
        "cap_bits": {},
        "intc": {},
        "ring": {},
    }

    # -- Base addresses: localparam ... NPU_{NAME}_BASE = 32'h...
    for m in re.finditer(r"NPU_(\w+)_BASE = 32'h([0-9A-Fa-f]+)", src):
        result["base_addrs"][m.group(1)] = int(m.group(2), 16)

    # Register offsets: section-based, same pattern as C header.
    for section in re.finditer(
        r"// ── (\w+) Registers \(NPU_\w+_BASE\) ─────────\n((?:  localparam.*?\n)*)",
        src
    ):
        mod_name = section.group(1)
        body = section.group(2)
        for m in re.finditer(
            r"NPU_(\w+?)_OFFSET = 12'h([0-9A-Fa-f]+)",
            body
        ):
            full = m.group(1)
            prefix = mod_name + "_"
            if full.startswith(prefix):
                reg_name = full[len(prefix):]
            else:
                reg_name = full
            offset = int(m.group(2), 16)
            if mod_name not in result["reg_offsets"]:
                result["reg_offsets"][mod_name] = {}
            result["reg_offsets"][mod_name][reg_name] = offset

    # -- Engine opcodes: localparam ... NPU_ENGINE_OP_{NAME} = 8'h...
    for m in re.finditer(r"NPU_ENGINE_OP_(\w+) = 8'h([0-9A-Fa-f]+)", src):
        result["opcodes"][m.group(1)] = int(m.group(2), 16)

    # -- ISA opcodes: localparam ... NPU_ISA_OP_{NAME} = 5'h...
    for m in re.finditer(r"NPU_ISA_OP_(\w+) = 5'h([0-9A-Fa-f]+)", src):
        result["isa_opcodes"][m.group(1)] = int(m.group(2), 16)

    # -- Status codes: localparam ... NPU_STATUS_{NAME} = 32'h...
    for m in re.finditer(r"NPU_STATUS_(\w+) = 32'h([0-9A-Fa-f]+)", src):
        result["status_codes"][m.group(1)] = int(m.group(2), 16)

    # -- Capability bits: localparam ... NPU_CAP_{NAME} = 32'h...
    for m in re.finditer(r"NPU_CAP_(\w+) = 32'h([0-9A-Fa-f]+)", src):
        cap_val = int(m.group(2), 16)
        # Convert to bit position
        if cap_val > 0 and (cap_val & (cap_val - 1)) == 0:  # power of 2
            result["cap_bits"][m.group(1)] = cap_val.bit_length() - 1
        else:
            result["cap_bits"][m.group(1)] = cap_val  # store raw for later

    # -- INTC bit positions: localparam ... NPU_INTC_{NAME}_BIT = 7'h...
    for m in re.finditer(r"NPU_INTC_(\w+)_BIT = 7'h([0-9A-Fa-f]+)", src):
        bit_val = int(m.group(2), 16)
        if bit_val > 0 and (bit_val & (bit_val - 1)) == 0:
            result["intc"][m.group(1)] = bit_val.bit_length() - 1
        else:
            result["intc"][m.group(1)] = bit_val

    # -- Ring config
    for m in re.finditer(r"NPU_(RING_ENTRIES|CMD_ENTRY_SIZE|COMPLETION_ENTRY_SIZE) = (\d+)", src):
        result["ring"][m.group(1)] = int(m.group(2))

    return result


# ═══════════════════════════════════════════════════════════════════
# Validation helpers
# ═══════════════════════════════════════════════════════════════════

class DriftReport:
    """Accumulates drift findings with context."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.checked: int = 0

    def check(self, scope: str, key: str, expected: Any, actual: Any,
              artifact: str = "") -> None:
        self.checked += 1
        prefix = f"  [{artifact}] " if artifact else "  "
        if actual != expected:
            self.errors.append(
                f"{prefix}{scope}.{key}: "
                f"expected {repr(expected)}, got {repr(actual)}"
            )

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        lines: list[str] = []
        if self.errors:
            for e in self.errors:
                lines.append(f"  {RED}DRIFT{RESET} {e}")
        if self.warnings:
            for w in self.warnings:
                lines.append(f"  {YELLOW}WARN{RESET} {w}")
        lines.append("")
        total_checks = self.checked + len(self.warnings)
        err_count = len(self.errors)
        if err_count == 0:
            lines.append(
                f"  {GREEN}✓{RESET} {self.checked} checks passed, "
                f"{len(self.warnings)} warnings, 0 drift errors."
            )
        else:
            lines.append(
                f"  {RED}✗{RESET} {err_count} DRIFT ERROR(S), "
                f"{self.checked} checks passed, "
                f"{len(self.warnings)} warnings."
            )
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# Cross-validation: schema ↔ all artifacts
# ═══════════════════════════════════════════════════════════════════

def validate_base_addresses(
    schema: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    report: DriftReport,
    verbose: bool = False,
) -> None:
    """Validate address region base addresses across all artifacts."""
    schema_regions = schema["address_regions"]
    for name, sreg in sorted(schema_regions.items()):
        expected = int(sreg["base"], 16)
        for art_name, art in artifacts.items():
            bases = art.get("base_addrs", {})
            actual = bases.get(name)
            if actual is None and "_base_addrs" in repr(art):
                continue  # optional artifact dimension
            if actual is None:
                report.check(
                    f"base_addr.{name}", art_name, expected, "MISSING",
                    artifact=art_name
                )
            else:
                if verbose:
                    print(f"  base_addr.{name} [{art_name}]: "
                          f"0x{actual:08X} == 0x{expected:08X}")
                report.check(
                    f"base_addr.{name}", art_name, expected, actual,
                    artifact=art_name
                )


def validate_register_offsets(
    schema: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    report: DriftReport,
    verbose: bool = False,
) -> None:
    """Validate register offsets per module. Only check artifacts that have reg_offsets."""
    schema_modules = schema["modules"]
    for mod_name, mod in sorted(schema_modules.items()):
        for reg_name, reg in sorted(mod["registers"].items()):
            expected = int(reg["offset"], 16)
            for art_name, art in artifacts.items():
                reg_offsets = art.get("reg_offsets", {})
                if not reg_offsets:
                    continue  # firmware header doesn't have reg offsets
                actual = reg_offsets.get(mod_name, {}).get(reg_name)
                if actual is None:
                    report.check(
                        f"reg.{mod_name}.{reg_name}", art_name, expected,
                        "MISSING", artifact=art_name
                    )
                    continue
                if verbose:
                    print(f"  reg.{mod_name}.{reg_name} [{art_name}]: "
                          f"0x{actual:04X} == 0x{expected:04X}")
                report.check(
                    f"reg.{mod_name}.{reg_name}", art_name, expected, actual,
                    artifact=art_name
                )


def validate_opcodes(
    schema: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    report: DriftReport,
    verbose: bool = False,
) -> None:
    """Validate engine opcodes across artifacts."""
    schema_ops = schema["opcodes"]["values"]
    for op_name, op in sorted(schema_ops.items(), key=lambda x: x[1]["value"]):
        expected = op["value"]
        for art_name, art in artifacts.items():
            art_ops = art.get("opcodes", {})
            if not art_ops:
                continue
            actual = art_ops.get(op_name)
            if actual is None:
                report.check(
                    f"opcode.{op_name}", art_name, expected,
                    "MISSING", artifact=art_name
                )
                continue
            if verbose:
                print(f"  opcode.{op_name} [{art_name}]: "
                      f"{actual} == {expected}")
            report.check(
                f"opcode.{op_name}", art_name, expected, actual,
                artifact=art_name
            )


def validate_status_codes(
    schema: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    report: DriftReport,
    verbose: bool = False,
) -> None:
    """Validate status codes across artifacts."""
    schema_sc = schema["status_codes"]["values"]
    for sc_name, sc in sorted(schema_sc.items(), key=lambda x: x[1]["value"]):
        expected = sc["value"]
        for art_name, art in artifacts.items():
            art_sc = art.get("status_codes", {})
            if not art_sc:
                continue
            actual = art_sc.get(sc_name)
            if actual is None:
                report.check(
                    f"status.{sc_name}", art_name, expected,
                    "MISSING", artifact=art_name
                )
                continue
            if verbose:
                print(f"  status.{sc_name} [{art_name}]: "
                      f"{actual} == {expected}")
            report.check(
                f"status.{sc_name}", art_name, expected, actual,
                artifact=art_name
            )


def validate_capability_bits(
    schema: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    report: DriftReport,
    verbose: bool = False,
) -> None:
    """Validate capability bit assignments."""
    schema_cb = schema["capability_bits"]["bits"]
    for cb_name, cb in sorted(schema_cb.items(), key=lambda x: x[1]["bit"]):
        expected = cb["bit"]
        for art_name, art in artifacts.items():
            art_cb = art.get("cap_bits", {})
            if not art_cb:
                continue
            actual = art_cb.get(cb_name)
            if actual is None:
                report.check(
                    f"cap.{cb_name}", art_name, expected,
                    "MISSING", artifact=art_name
                )
                continue
            if verbose:
                print(f"  cap.{cb_name} [{art_name}]: "
                      f"bit {actual} == bit {expected}")
            report.check(
                f"cap.{cb_name}", art_name, expected, actual,
                artifact=art_name
            )


def validate_descriptor_layouts(
    schema: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    report: DriftReport,
    verbose: bool = False,
) -> None:
    """Validate descriptor sizes and field offsets (firmware header only)."""
    fw_artifacts = {k: v for k, v in artifacts.items()
                     if "desc_sizes" in v or "desc_fields" in v}

    for desc_name in sorted(schema["descriptors"]):
        if desc_name == "description":
            continue
        desc = schema["descriptors"][desc_name]

        # Check packed size
        expected_size = desc["packed_size"]
        for art_name, art in fw_artifacts.items():
            desc_sizes = art.get("desc_sizes", {})
            actual_size = desc_sizes.get(desc_name)
            if actual_size is None:
                continue
            if verbose:
                print(f"  desc.{desc_name}.size [{art_name}]: "
                      f"{actual_size} == {expected_size}")
            report.check(
                f"desc.{desc_name}.size", art_name, expected_size,
                actual_size, artifact=art_name
            )

        # Check field offsets
        for field in desc["fields"]:
            expected_off = field["offset"]
            for art_name, art in fw_artifacts.items():
                desc_fields = art.get("desc_fields", {})
                if not desc_fields:
                    continue
                actual_off = desc_fields.get(desc_name, {}).get(field["name"])
                if actual_off is None:
                    continue
                if verbose:
                    print(f"  desc.{desc_name}.{field['name']} [{art_name}]: "
                          f"{actual_off} == {expected_off}")
                report.check(
                    f"desc.{desc_name}.{field['name']}", art_name,
                    expected_off, actual_off, artifact=art_name
                )


def validate_intc_mappings(
    schema: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    report: DriftReport,
    verbose: bool = False,
) -> None:
    """Validate INTC source bit assignments."""
    schema_intc = schema["intc_mappings"]["sources"]
    for intc_name, intc in sorted(schema_intc.items(), key=lambda x: x[1]["bit"]):
        expected = intc["bit"]
        for art_name, art in artifacts.items():
            art_intc = art.get("intc", {})
            if not art_intc:
                continue
            actual = art_intc.get(intc_name)
            if actual is None:
                report.check(
                    f"intc.{intc_name}", art_name, expected,
                    "MISSING", artifact=art_name
                )
                continue
            if verbose:
                print(f"  intc.{intc_name} [{art_name}]: "
                      f"bit {actual} == bit {expected}")
            report.check(
                f"intc.{intc_name}", art_name, expected, actual,
                artifact=art_name
            )


def validate_ring_config(
    schema: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    report: DriftReport,
    verbose: bool = False,
) -> None:
    """Validate ring buffer configuration."""
    rc = schema["rings"]["configuration"]
    ring_keys = {
        "ring_entries": (rc["ring_entries"], "RING_ENTRIES"),
        "cmd_entry_size": (rc["cmd_entry_size"], "CMD_ENTRY_SIZE"),
        "completion_entry_size": (rc["completion_entry_size"],
                                   "COMPLETION_ENTRY_SIZE"),
    }
    for schema_key, (expected, art_key) in ring_keys.items():
        for art_name, art in artifacts.items():
            art_ring = art.get("ring", {})
            if not art_ring:
                continue
            actual = art_ring.get(art_key)
            if actual is None:
                report.check(
                    f"ring.{schema_key}", art_name, expected,
                    "MISSING", artifact=art_name
                )
                continue
            if verbose:
                print(f"  ring.{schema_key} [{art_name}]: "
                      f"{actual} == {expected}")
            report.check(
                f"ring.{schema_key}", art_name, expected, actual,
                artifact=art_name
            )


def validate_isa_opcodes(
    schema: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    report: DriftReport,
    verbose: bool = False,
) -> None:
    """Validate ISA opcodes (C header and SV package only)."""
    schema_isa = schema["isa_opcodes"]["values"]
    isa_artifacts = {k: v for k, v in artifacts.items()
                      if v.get("isa_opcodes")}
    for isa_name, isa in sorted(schema_isa.items(),
                                 key=lambda x: x[1]["value"]):
        expected = isa["value"]
        for art_name, art in isa_artifacts.items():
            actual = art["isa_opcodes"].get(isa_name)
            if actual is None:
                report.check(
                    f"isa_opcode.{isa_name}", art_name, expected,
                    "MISSING", artifact=art_name
                )
                continue
            if verbose:
                print(f"  isa_opcode.{isa_name} [{art_name}]: "
                      f"{actual} == {expected}")
            report.check(
                f"isa_opcode.{isa_name}", art_name, expected, actual,
                artifact=art_name
            )


def validate_sfu_vec_opcodes(
    schema: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    report: DriftReport,
    verbose: bool = False,
) -> None:
    """Validate SFU and Vector sub-opcodes where present."""
    for kind, schema_key in [("sfu_op", "sfu_sub_opcodes"),
                              ("vec_op", "vector_sub_opcodes")]:
        schema_vals = schema[schema_key]["values"]
        art_key = "sfu_ops" if kind == "sfu_op" else "vec_ops"
        for op_name, op in sorted(schema_vals.items(),
                                   key=lambda x: x[1]["value"]):
            expected = op["value"]
            for art_name, art in artifacts.items():
                art_ops = art.get(art_key, {})
                if not art_ops:
                    continue
                actual = art_ops.get(op_name)
                if actual is None:
                    # Not all artifacts carry SFU/Vector sub-opcodes
                    continue
                if verbose:
                    print(f"  {kind}.{op_name} [{art_name}]: "
                          f"{actual} == {expected}")
                report.check(
                    f"{kind}.{op_name}", art_name, expected, actual,
                    artifact=art_name
                )


# ═══════════════════════════════════════════════════════════════════
# Main check pipeline
# ═══════════════════════════════════════════════════════════════════

def run_check(schema: dict[str, Any], verbose: bool = False) -> int:
    """Run all validations. Return 0 on pass, 1 on drift."""
    # Ensure all artifacts exist
    for fname in ARTIFACT_FILES:
        if not (GEN_DIR / fname).is_file():
            print(f"{RED}ERROR:{RESET} missing artifact "
                  f"{GEN_DIR / fname}")
            return 1

    # Parse all artifacts
    parsers = {
        "npu_abi.py":             parse_python,
        "npu_abi.h":              parse_c_header,
        "npu_abi_firmware.h":     parse_firmware_header,
        "npu_abi_pkg.sv":         parse_sv_package,
    }

    artifacts: dict[str, dict[str, Any]] = {}
    for fname, parser in parsers.items():
        try:
            src = _read_artifact(fname)
            artifacts[fname] = parser(src)
        except Exception as e:
            print(f"{RED}ERROR:{RESET} parsing {fname}: {e}")
            return 1

    report = DriftReport()

    print(f"\n{BOLD}CaduceusCore SoC Golden Contract Check{RESET}")
    print(f"  Schema: spec/npu_abi.json v{schema['abi']['version_string']}")
    print(f"  Artifacts: {', '.join(ARTIFACT_FILES)}")
    print()

    # Run all validation passes
    validators = [
        ("Base addresses", validate_base_addresses),
        ("Register offsets", validate_register_offsets),
        ("Engine opcodes", validate_opcodes),
        ("ISA opcodes", validate_isa_opcodes),
        ("Status codes", validate_status_codes),
        ("Capability bits", validate_capability_bits),
        ("Descriptor layouts", validate_descriptor_layouts),
        ("INTC mappings", validate_intc_mappings),
        ("Ring configuration", validate_ring_config),
        ("SFU/Vector sub-opcodes", validate_sfu_vec_opcodes),
    ]

    for label, validator_fn in validators:
        print(f"  Checking {label}...")
        validator_fn(schema, artifacts, report, verbose=verbose)

    print()
    print(report.summary())

    return 0 if report.ok else 1


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="SoC Golden Contract Drift Checker — "
                    "validates generated artifacts against spec/npu_abi.json"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Validate all generated artifacts against the ABI schema"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show every comparison (pass and fail)"
    )
    args = parser.parse_args()

    if not args.check:
        parser.print_help()
        sys.exit(2)

    schema = load_schema()
    rc = run_check(schema, verbose=args.verbose)
    sys.exit(rc)


if __name__ == "__main__":
    main()
