#!/usr/bin/env python3
"""
Firmware memory contract JSON generator (FW-09).

Derives ``firmware_memory_contract.json`` from the Func Model sources of
truth — this JSON is a DERIVED ARTIFACT, never a truth source:

- ``sim/address_space.py``  REGIONS + contract_check
- ``sim/command_ring.py``   ring constants
- an actual minimal FuncModel doorbell run (descriptor range used +
  observed max ring offset), so the ``run`` section records what a real
  firmware run touches, per the T1 gap item ("ring base/size, descriptor
  range used, completion ring range, observed max ring offset").

Modes:
    python3 scripts/gen_firmware_memory_contract.py              # (re)generate JSON
    python3 scripts/gen_firmware_memory_contract.py --check      # verify no drift (exit 0/1)

Exit codes:
    0 — contract generated / no drift
    1 — drift detected
    2 — usage error
"""

import argparse
import json
import sys
import warnings
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_SIM = REPO / "sim"
for _p in (str(REPO), str(_SIM)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from address_space import (  # noqa: E402
    OverlapError,
    WindowError,
    contract_check,
)
from address_space import COMPLETION_ENTRY_SIZE, DESC_BASE, P10_ACT_BASE, REGIONS  # noqa: E402
import command_ring  # noqa: E402

CONTRACT_PATH = REPO / "firmware_memory_contract.json"
DEFAULT_COMMANDS = 16  # one full FuncModel firmware ring (ring_size=16)


# ═══════════════════════════════════════════════════════════════════
# Actual FuncModel run — observes descriptor range + max ring offset
# ═══════════════════════════════════════════════════════════════════

def _run_actual_fm(n_commands: int) -> dict:
    """Run a minimal deterministic doorbell MMUL sequence and return the
    observed ring bookkeeping: descriptor range used and max ring offset."""
    import numpy as np

    from cocotb_bridge import pack_int8_activation_tile_major
    from engine.isa import OpCode
    from func_model import FuncModel
    from golden_executor import GoldenMXU

    with warnings.catch_warnings():
        # FuncModel() emits a DeprecationWarning for NPUFirmware — benign,
        # per the repo's Spike migration (fm-soc-datapath-hardening todo 1).
        warnings.simplefilter("ignore", DeprecationWarning)
        model = FuncModel()

    m, k, n = 1, 4, 2
    act_addr = P10_ACT_BASE
    wgt_addr = act_addr + 0x1000
    out_addr = act_addr + 0x2000
    scale_addr = act_addr + 0x3000

    rng = np.random.RandomState(20260824)
    act = rng.randint(-8, 8, size=m * k, dtype=np.int8)
    wgt_packed = GoldenMXU.pack_int4(rng.randint(-8, 8, size=k * n, dtype=np.int8))
    num_blocks = (k + 127) // 128
    scales = np.ones((num_blocks, n), dtype=np.float32)

    model.host_write_data(act_addr, np.frombuffer(
        pack_int8_activation_tile_major(act.tobytes(), m, k), dtype=np.uint8))
    model.host_write_data(wgt_addr, wgt_packed)
    model.host_write_data(scale_addr, scales.ravel())

    max_offset = 0
    for i in range(n_commands):
        desc_addr = DESC_BASE + i * command_ring.DESC_STRIDE
        model.host_write_descriptor(
            desc_addr,
            input_addr=act_addr, weight_addr=wgt_addr, output_addr=out_addr,
            scale_addr=scale_addr,
            scale_size=num_blocks * n * 4,
            input_size=((k + 63) // 64) * 4096,
            weight_size=(k * n + 1) // 2,
            output_size=m * n * 4,
            M=m, K=k, N=n,
        )
        model.host_write_command(int(OpCode.MMUL), desc_addr)
        results = model.firmware.run_loop(max_commands=1)
        if len(results) != 1 or results[0]["status"] != "done":
            raise RuntimeError(f"minimal FM run failed at command {i}: {results}")
        doorbell = model.firmware.doorbell
        max_offset = max(max_offset, doorbell["host_tail"], doorbell["npu_head"])

    doorbell = model.firmware.doorbell
    return {
        "total_commands": n_commands,
        "desc_base": DESC_BASE,
        "desc_count": n_commands,
        "desc_stride": command_ring.DESC_STRIDE,
        "desc_end": DESC_BASE + n_commands * command_ring.DESC_STRIDE,
        "firmware_ring_size": model.firmware.ring_size,
        "max_ring_offset_observed": max_offset,
        "final_host_tail": doorbell["host_tail"],
        "final_npu_head": doorbell["npu_head"],
    }


# ═══════════════════════════════════════════════════════════════════
# Contract construction
# ═══════════════════════════════════════════════════════════════════

def build_contract(n_commands: int = DEFAULT_COMMANDS) -> dict:
    """Assemble the contract from the sources of truth + an actual FM run."""
    run = _run_actual_fm(n_commands)
    regions = {
        name: {"base": f"0x{base:08x}", "size": size}
        for name, (base, size) in REGIONS.items()
    }
    return {
        "generated_by": "scripts/gen_firmware_memory_contract.py",
        "note": (
            "Derived artifact — sources of truth are sim/address_space.py, "
            "sim/command_ring.py and spec/npu_abi.json. Do not edit by hand."
        ),
        "regions": regions,
        "ring": {
            "RING_BASE": f"0x{command_ring.RING_BASE:08x}",
            "RING_ENTRIES": command_ring.RING_ENTRIES,
            "CMD_ENTRY_SIZE": command_ring.CMD_ENTRY_SIZE,
            "COMPLETION_RING_ADDR": f"0x{command_ring.COMPLETION_RING_ADDR:08x}",
            "COMPLETION_ENTRY_SIZE": COMPLETION_ENTRY_SIZE,
            "DESC_STRIDE": command_ring.DESC_STRIDE,
        },
        "run": {
            "total_commands": run["total_commands"],
            "desc_base": f"0x{run['desc_base']:08x}",
            "desc_count": run["desc_count"],
            "desc_stride": run["desc_stride"],
            "desc_end": f"0x{run['desc_end']:08x}",
            "firmware_ring_size": run["firmware_ring_size"],
            "max_ring_offset_observed": run["max_ring_offset_observed"],
            "final_host_tail": run["final_host_tail"],
            "final_npu_head": run["final_npu_head"],
        },
    }


# ═══════════════════════════════════════════════════════════════════
# Cross-check against the sources of truth
# ═══════════════════════════════════════════════════════════════════

def _as_int(value):
    """Accept plain ints or '0x…' hex strings from the JSON document."""
    return value if isinstance(value, int) else int(str(value), 16)


def compare_contract(doc: dict) -> list:
    """Compare a contract document against address_space/command_ring.

    Returns a list of mismatch descriptions; an empty list means the
    document is consistent with the sources of truth.
    """
    mismatches = []

    ring = doc.get("ring", {})
    ring_checks = [
        ("RING_BASE", command_ring.RING_BASE),
        ("RING_ENTRIES", command_ring.RING_ENTRIES),
        ("CMD_ENTRY_SIZE", command_ring.CMD_ENTRY_SIZE),
        ("COMPLETION_RING_ADDR", command_ring.COMPLETION_RING_ADDR),
        ("DESC_STRIDE", command_ring.DESC_STRIDE),
        ("COMPLETION_ENTRY_SIZE", COMPLETION_ENTRY_SIZE),
    ]
    for key, expected in ring_checks:
        if key not in ring:
            mismatches.append(f"ring.{key}: MISSING")
            continue
        actual = _as_int(ring[key])
        if actual != expected:
            mismatches.append(
                f"ring.{key}: expected {expected:#x}, got {actual:#x}"
            )

    regions = doc.get("regions", {})
    for name, (base, size) in REGIONS.items():
        reg = regions.get(name)
        if reg is None:
            mismatches.append(f"regions.{name}: MISSING")
            continue
        if _as_int(reg.get("base")) != base:
            mismatches.append(
                f"regions.{name}.base: expected {base:#x}, "
                f"got {_as_int(reg.get('base')):#x}"
            )
        if _as_int(reg.get("size")) != size:
            mismatches.append(
                f"regions.{name}.size: expected {size:#x}, "
                f"got {_as_int(reg.get('size')):#x}"
            )

    run = doc.get("run", {})
    try:
        contract_check(
            ring_entries=_as_int(ring.get("RING_ENTRIES", 0)),
            desc_base=_as_int(run.get("desc_base")),
            desc_count=_as_int(run.get("desc_count")),
            act_base=None,
        )
    except KeyError:
        mismatches.append("run: descriptor range fields incomplete")
    except (OverlapError, WindowError) as exc:
        mismatches.append(f"run: violates address-space contract: {exc}")
    else:
        if run.get("max_ring_offset_observed", 0) >= _as_int(
            ring.get("RING_ENTRIES", 0)
        ):
            mismatches.append(
                "run.max_ring_offset_observed exceeds the ring entry count"
            )
        expected_end = (
            _as_int(run["desc_base"])
            + _as_int(run["desc_count"]) * command_ring.DESC_STRIDE
        )
        if _as_int(run.get("desc_end")) != expected_end:
            mismatches.append(
                f"run.desc_end: expected {expected_end:#x}, "
                f"got {_as_int(run.get('desc_end')):#x}"
            )

    return mismatches


# ═══════════════════════════════════════════════════════════════════
# File I/O
# ═══════════════════════════════════════════════════════════════════

def write_contract(path: Path, doc: dict) -> None:
    """Serialize the contract deterministically (sorted keys, 2-space indent)."""
    path.write_text(
        json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def load_contract(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Firmware memory contract JSON generator (FW-09) — "
                    "derives firmware_memory_contract.json from the Func "
                    "Model sources of truth."
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Regenerate and verify no drift against sources and the "
             "on-disk JSON (exit 0 on match, 1 on drift)"
    )
    parser.add_argument(
        "--out", type=str, default=str(CONTRACT_PATH),
        help=f"Output JSON path (default: {CONTRACT_PATH})"
    )
    parser.add_argument(
        "--commands", type=int, default=DEFAULT_COMMANDS,
        help="Number of commands in the recorded actual FM run "
             f"(default: {DEFAULT_COMMANDS})"
    )
    args = parser.parse_args()

    if args.commands < 1:
        parser.error("--commands must be >= 1")

    doc = build_contract(n_commands=args.commands)
    out_path = Path(args.out)

    if args.check:
        ok = True
        print("Firmware memory contract check")
        print(f"  Sources: sim/address_space.py, sim/command_ring.py")
        mismatches = compare_contract(doc)
        for m in mismatches:
            print(f"  DRIFT {m}")
            ok = False
        if not mismatches:
            print("  OK: generated contract matches the sources of truth")
        if not out_path.is_file():
            print(f"  MISSING: {out_path}")
            ok = False
        elif load_contract(out_path) != doc:
            print(f"  MISMATCH: {out_path} differs from a freshly generated contract")
            ok = False
        else:
            print(f"  OK: {out_path} matches a freshly generated contract")
        print("PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)

    write_contract(out_path, doc)
    print(f"Wrote {out_path}")
    print(f"  regions: {', '.join(doc['regions'])}")
    print(f"  ring: RING_ENTRIES={doc['ring']['RING_ENTRIES']}, "
          f"RING_BASE={doc['ring']['RING_BASE']}")
    print(f"  run: {doc['run']['total_commands']} commands, "
          f"desc [{doc['run']['desc_base']}, {doc['run']['desc_end']}), "
          f"max_ring_offset_observed={doc['run']['max_ring_offset_observed']}")


if __name__ == "__main__":
    main()
