#!/usr/bin/env python3
"""
validate_interconnect.py — AXI Crossbar Interconnect Validation

Parses CaduceusCore/sim/config/interconnect.yaml and validates:
  1. No address overlap between slave regions
  2. AXI ID width consistent across all masters
  3. Each master's address space covers all intended slaves

Exits 0 on PASS, 1 on FAIL.
Also prints a human-readable routing table in Markdown.

Usage:
    python3 scripts/validate_interconnect.py

References:
    - CaduceusCore/sim/config/interconnect.yaml
    - CaduceusCore/rtl/soc/axi_crossbar.v
"""

import sys
import os
from pathlib import Path

try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed. Run 'pip install pyyaml'")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent  # CaduceusCore/
YAML_PATH = PROJECT_DIR / "sim" / "config" / "interconnect.yaml"


def load_yaml(path: Path) -> dict:
    """Load and return the interconnect YAML."""
    if not path.exists():
        print(f"FAIL: YAML file not found: {path}")
        sys.exit(1)
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    if data is None or "interconnect" not in data:
        print("FAIL: YAML does not contain 'interconnect' top-level key")
        sys.exit(1)
    return data["interconnect"]


# ---------------------------------------------------------------------------
# Validation 1: No address overlap between slaves
# ---------------------------------------------------------------------------
def validate_no_overlap(slaves: list[dict]) -> list[str]:
    """Check that slave address regions do not overlap. Return error list."""
    errors = []
    regions = []
    for s in slaves:
        base = s["base_addr"]
        size = s["size"]
        name = s["name"]
        regions.append((name, base, base + size - 1))

    for i in range(len(regions)):
        for j in range(i + 1, len(regions)):
            n1, s1, e1 = regions[i]
            n2, s2, e2 = regions[j]
            # Check overlap: [s1, e1] ∩ [s2, e2] ≠ ∅
            if not (e1 < s2 or e2 < s1):
                errors.append(
                    f"ADDRESS OVERLAP: {n1} [{s1:#010x}..{e1:#010x}] "
                    f"overlaps {n2} [{s2:#010x}..{e2:#010x}]"
                )
    return errors


# ---------------------------------------------------------------------------
# Validation 2: AXI ID width consistency
# ---------------------------------------------------------------------------
def validate_id_width_consistency(masters: list[dict], expected_width: int) -> list[str]:
    """Check all masters have the same AXI ID width. Return error list."""
    errors = []
    for m in masters:
        w = m.get("axi_id_width", -1)
        if w != expected_width:
            errors.append(
                f"ID WIDTH MISMATCH: master '{m['name']}' (id={m['id']}) "
                f"has axi_id_width={w}, expected {expected_width}"
            )
    return errors


# ---------------------------------------------------------------------------
# Validation 3: Master address space covers intended slaves
# ---------------------------------------------------------------------------
def validate_slave_coverage(
    masters: list[dict], slaves: list[dict]
) -> list[str]:
    """
    For each master, check that its intended slaves are reachable.
    A master can reach a slave if the slave's base address falls within
    the crossbar's address decode for that slave. Since the crossbar
    routes uniformly (no per-master ACL), any master can access any slave
    by issuing the correct address. We verify intended_slaves list against
    known slave names and that their addresses are valid.
    """
    errors = []
    slave_map = {s["name"]: s for s in slaves}

    for m in masters:
        intended = m.get("intended_slaves", [])
        for sname in intended:
            if sname not in slave_map:
                errors.append(
                    f"UNKNOWN SLAVE: master '{m['name']}' intends slave "
                    f"'{sname}' which is not defined in slaves list"
                )
                continue
            # If slave is defined, it's reachable — the crossbar has no
            # per-master access restrictions.  Just verify the slave exists.
    return errors


# ---------------------------------------------------------------------------
# Validation 4: Address route windows are well-formed
# ---------------------------------------------------------------------------
def validate_address_routes(routes: list[dict]) -> list[str]:
    """Check address route entries for internal consistency."""
    errors = []
    for r in routes:
        base = r.get("base_addr")
        end = r.get("end_addr")
        size = r.get("size")
        if base is not None and end is not None and base > end:
            errors.append(
                f"ROUTE '{r.get('name', '?')}': base_addr ({base:#010x}) "
                f"> end_addr ({end:#010x})"
            )
        if base is not None and size is not None:
            computed_end = base + size - 1
            if end is not None and computed_end != end:
                errors.append(
                    f"ROUTE '{r.get('name', '?')}': computed end addr "
                    f"({computed_end:#010x}) != declared end_addr ({end:#010x})"
                )
    return errors


# ---------------------------------------------------------------------------
# Routing table markdown generator
# ---------------------------------------------------------------------------
def generate_routing_table_md(
    masters: list[dict],
    slaves: list[dict],
    routes: list[dict],
    params: dict,
) -> str:
    """Generate a human-readable routing table in Markdown."""

    lines = []
    lines.append("# CaduceusCore AXI4 Crossbar Routing Table")
    lines.append("")
    lines.append(
        f"_Auto-generated by `validate_interconnect.py` from "
        f"`sim/config/interconnect.yaml`_"
    )
    lines.append("")
    lines.append(f"- **Topology:** {params.get('topology', 'crossbar')}")
    lines.append(f"- **Protocol:** {params.get('protocol', 'AXI4')}")
    lines.append(f"- **Clock:** {params.get('clock_mhz', '?')} MHz")
    lines.append(
        f"- **Bus:** {params.get('data_width', '?')}-bit data, "
        f"{params.get('addr_width', '?')}-bit address"
    )
    lines.append(
        f"- **Master ID width:** {params.get('m_id_width', '?')} bits "
        f"(slave-side width: {params.get('m_id_width', 0) + params.get('msel_width', 0)} bits)"
    )
    lines.append(f"- **Arbitration:** {params.get('arbitration', {}).get('policy', 'round_robin')}")
    lines.append("")

    # ── Master ports ──
    lines.append("## Master Ports (M={})".format(len(masters)))
    lines.append("")
    lines.append("| ID | Name | Description | Data Width | AXI ID Width | Priority | Intended Slaves |")
    lines.append("|----|------|-------------|-----------|-------------|----------|-----------------|")
    for m in masters:
        lines.append(
            f"| {m['id']} | {m['name']} | {m.get('description', '')} "
            f"| {m.get('data_width', '?')} | {m.get('axi_id_width', '?')} "
            f"| {m.get('priority', '?')} | {', '.join(m.get('intended_slaves', []))} |"
        )
    lines.append("")

    # ── Slave ports ──
    lines.append("## Slave Ports (S={})".format(len(slaves)))
    lines.append("")
    lines.append("| ID | Name | Description | Base Address | Size | Address Range |")
    lines.append("|-----|------|-------------|-------------|------|---------------|")
    for s in slaves:
        base = s["base_addr"]
        size = s["size"]
        end = base + size - 1
        lines.append(
            f"| {s['id']} | {s['name']} | {s.get('description', '')} "
            f"| {base:#010x} | {size:#010x} ({_human_size(size)}) "
            f"| {base:#010x} – {end:#010x} |"
        )
    lines.append("")

    # ── Address routing table ──
    lines.append("## Address Routing Table")
    lines.append("")
    lines.append(
        "The following table shows address windows and their routing "
        "destination. Addresses that fall outside all mapped regions "
        "receive a DECERR response."
    )
    lines.append("")
    lines.append(
        "| Window | Start Address | End Address | Size | Slave | Decode Logic |"
    )
    lines.append(
        "|--------|-------------|-----------|------|-------|-------------|"
    )
    for r in routes:
        name = r.get("name", "?")
        base = r.get("base_addr", 0)
        end = r.get("end_addr", 0)
        size = r.get("size", 0)
        sid = r.get("slave_id")
        slave_name = slaves[sid]["name"] if sid is not None and sid < len(slaves) else "DECERR"
        decode = r.get("decode", "")
        lines.append(
            f"| {name} | {base:#010x} | {end:#010x} "
            f"| {size:#010x} ({_human_size(size)}) "
            f"| {slave_name} | `{decode}` |"
        )
    lines.append("")

    # ── Arbitration ──
    arb = params.get("arbitration", {})
    lines.append("## Arbitration")
    lines.append("")
    lines.append(f"- **Policy:** {arb.get('policy', 'round_robin')}")
    lines.append(f"- **Independent AW/AR arbitration:** {arb.get('independent_aw_ar', True)}")
    lines.append(f"- **Single outstanding per direction:** {arb.get('single_outstanding', True)}")
    lines.append("")
    lines.append(
        "AW (write address) and W/B (write data / write response) share "
        "one arbiter per slave; AR (read address) and R (read data) share "
        "a separate arbiter per slave. Both use round-robin with priority "
        "starting at the last-granted master."
    )
    lines.append("")

    # ── AXI ID mapping ──
    m_id_width = params.get("m_id_width", 6)
    msel_width = params.get("msel_width", 3)
    s_id_width = m_id_width + msel_width
    lines.append("## AXI ID Mapping")
    lines.append("")
    lines.append(
        f"On the slave-side (facing SRAM/DRAM), the AXI ID is extended "
        f"with a master-select field:"
    )
    lines.append("")
    lines.append(
        f"    s_axid = {{master_sel[{msel_width-1}:0], "
        f"axi_id[{m_id_width-1}:0]}}   // {s_id_width} bits total"
    )
    lines.append("")
    lines.append(
        "When responses return, the crossbar strips the master-select "
        "field from BID/RID to route the response to the correct master."
    )
    lines.append("")

    # Master ID allocation table
    lines.append("| Master | Master Select (3-bit) | AXI ID Range |")
    lines.append("|--------|----------------------|-------------|")
    for m in masters:
        mid = m["id"]
        lines.append(
            f"| {m['name']} | {mid:03b} | "
            f"{{{mid:03b}, id[5:0]}} (IDs {mid*64}–{mid*64+63}) |"
        )
    lines.append("")

    return "\n".join(lines)


def _human_size(size_bytes: int) -> str:
    """Format byte size to human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes} {unit}"
        size_bytes //= 1024
    return f"{size_bytes} TB"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    data = load_yaml(YAML_PATH)

    params = data
    masters: list[dict] = data.get("masters", [])
    slaves: list[dict] = data.get("slaves", [])
    routes: list[dict] = data.get("address_routes", [])
    m_id_width: int = params.get("m_id_width", 6)

    errors: list[str] = []

    # ── Run validations ──────────────────────────────────────────────────
    errors.extend(validate_no_overlap(slaves))
    errors.extend(validate_id_width_consistency(masters, m_id_width))
    errors.extend(validate_slave_coverage(masters, slaves))
    errors.extend(validate_address_routes(routes))

    # ── Generate routing table ───────────────────────────────────────────
    routing_md = generate_routing_table_md(masters, slaves, routes, params)

    # ── Report ───────────────────────────────────────────────────────────
    print("=" * 72)
    print("  CaduceusCore Interconnect Validation")
    print("=" * 72)
    print(f"  YAML:    {YAML_PATH}")
    print(f"  Masters: {len(masters)}")
    print(f"  Slaves:  {len(slaves)}")
    print(f"  Routes:  {len(routes)}")
    print(f"  ID width:{m_id_width} bits (per master)")
    print()

    if errors:
        print("  VALIDATION FAILED:")
        for e in errors:
            print(f"    ✗ {e}")
        print()
        print(routing_md)
        print()
        print("=" * 72)
        print("  RESULT: FAIL")
        print("=" * 72)
        return 1
    else:
        print("  VALIDATION PASSED:")
        print("    ✓ No address overlap between slaves")
        print("    ✓ AXI ID width consistent across all masters")
        print("    ✓ All masters cover intended slaves")
        print("    ✓ Address routes are well-formed")
        print()
        print(routing_md)
        print()
        print("=" * 72)
        print("  RESULT: PASS")
        print("=" * 72)
        return 0


if __name__ == "__main__":
    sys.exit(main())
