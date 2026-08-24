"""Firmware memory contract JSON — generation + cross-source verification.

fm-soc-datapath-hardening todo 9 (FW-09).

The contract JSON is a DERIVED artifact. These tests verify it against the
sources of truth — ``sim/address_space.py``, ``sim/command_ring.py`` and
``spec/npu_abi.json`` — plus failure injection (tampered RING_ENTRIES).
"""

import copy
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_ABI_PATH = _REPO / "spec" / "npu_abi.json"
_CONTRACT_PATH = _REPO / "firmware_memory_contract.json"

sys.path.insert(0, str(_REPO / "scripts"))

import gen_firmware_memory_contract as genmc  # noqa: E402

from sim import address_space  # noqa: E402
from sim import command_ring  # noqa: E402


def _abi_schema():
    with open(_ABI_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _hx(v):
    return v if isinstance(v, int) else int(str(v), 16)


@pytest.fixture(scope="module")
def contract():
    """Freshly generated contract (runs the actual minimal FM sequence)."""
    return genmc.build_contract(n_commands=genmc.DEFAULT_COMMANDS)


# ── Happy path: JSON matches each source of truth ───────────────────

def test_contract_regions_match_address_space(contract):
    """Every address_space.REGIONS entry is present with matching base/size."""
    assert set(contract["regions"]) == set(address_space.REGIONS)
    for name, (base, size) in address_space.REGIONS.items():
        reg = contract["regions"][name]
        assert _hx(reg["base"]) == base, f"regions.{name}.base"
        assert reg["size"] == size, f"regions.{name}.size"


def test_contract_ring_matches_command_ring(contract):
    """Ring section equals the command_ring single-source constants."""
    ring = contract["ring"]
    assert _hx(ring["RING_BASE"]) == command_ring.RING_BASE
    assert ring["RING_ENTRIES"] == command_ring.RING_ENTRIES
    assert ring["CMD_ENTRY_SIZE"] == command_ring.CMD_ENTRY_SIZE
    assert _hx(ring["COMPLETION_RING_ADDR"]) == command_ring.COMPLETION_RING_ADDR
    assert ring["DESC_STRIDE"] == command_ring.DESC_STRIDE
    assert ring["COMPLETION_ENTRY_SIZE"] == address_space.COMPLETION_ENTRY_SIZE


def test_contract_ring_matches_abi_ring_configuration(contract):
    """Ring section equals spec/npu_abi.json rings.configuration."""
    cfg = _abi_schema()["rings"]["configuration"]
    ring = contract["ring"]
    assert _hx(ring["RING_BASE"]) == int(cfg["ring_buffer_addr"], 16)
    assert ring["RING_ENTRIES"] == cfg["ring_entries"]
    assert ring["CMD_ENTRY_SIZE"] == cfg["cmd_entry_size"]
    assert _hx(ring["COMPLETION_RING_ADDR"]) == int(
        cfg["completion_ring_addr"], 16
    )
    assert ring["COMPLETION_ENTRY_SIZE"] == cfg["completion_entry_size"]


def test_contract_run_section_satisfies_address_contract(contract):
    """The recorded actual run is a legal layout per address_space.contract_check."""
    run = contract["run"]
    ring = contract["ring"]

    # No exception = the descriptor range respects ring + window bounds.
    address_space.contract_check(
        ring_entries=ring["RING_ENTRIES"],
        desc_base=_hx(run["desc_base"]),
        desc_count=run["desc_count"],
        act_base=None,
    )
    assert _hx(run["desc_end"]) == (
        _hx(run["desc_base"]) + run["desc_count"] * run["desc_stride"]
    )
    assert run["desc_stride"] == command_ring.DESC_STRIDE
    assert run["max_ring_offset_observed"] < ring["RING_ENTRIES"]
    # Doorbell heads are persistent: final NPU head = total mod firmware ring.
    assert run["final_npu_head"] == run["total_commands"] % run["firmware_ring_size"]
    assert run["final_host_tail"] == run["final_npu_head"]


def test_on_disk_contract_file_is_fresh(contract):
    """The committed firmware_memory_contract.json is not stale."""
    assert _CONTRACT_PATH.is_file(), f"missing {_CONTRACT_PATH}"
    on_disk = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
    assert on_disk == contract, "on-disk contract drifted from regeneration"


def test_compare_contract_reports_no_mismatch(contract):
    """The generator's own cross-check is clean for a fresh document."""
    assert genmc.compare_contract(contract) == []


def test_write_contract_roundtrip(tmp_path, contract):
    """Serialization is deterministic: write then reload == in-memory doc."""
    out = tmp_path / "contract.json"
    genmc.write_contract(out, contract)
    assert genmc.load_contract(out) == contract


# ── Failure injection: tampered JSON must be caught ─────────────────

def test_failure_injection_ring_entries_tamper_detected(contract):
    """Setting JSON RING_ENTRIES=512 must fail the source comparison."""
    tampered = copy.deepcopy(contract)
    assert tampered["ring"]["RING_ENTRIES"] != 512, "tamper target must differ"
    tampered["ring"]["RING_ENTRIES"] = 512

    mismatches = genmc.compare_contract(tampered)
    assert mismatches, "tampered RING_ENTRIES was not detected"
    assert any("RING_ENTRIES" in m for m in mismatches), mismatches
    # The document no longer agrees with the source constant.
    assert tampered["ring"]["RING_ENTRIES"] != command_ring.RING_ENTRIES


def test_failure_injection_region_base_tamper_detected(contract):
    """Tampering a region base address must also be caught."""
    tampered = copy.deepcopy(contract)
    tampered["regions"]["command_ring"]["base"] = "0x70000000"
    mismatches = genmc.compare_contract(tampered)
    assert any("regions.command_ring.base" in m for m in mismatches), mismatches
