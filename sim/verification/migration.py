"""Migration helpers — convert TestCaseConfig to Scenario.

This module provides migration functions that convert existing
TestCaseConfig objects (from sim.rtl_soc_runner) into the new
transport-independent Scenario representation without breaking
existing FM-SOC vector loading.

Key design: migration is additive. The original load_golden_vectors()
continues to work unchanged. This module provides a bridge to the new
verification framework.
"""

from typing import Optional

from verification.scenario import Scenario, Action, EvidenceRecord
from verification.observation import Observation, ObservationType
from verification.tolerance import ToleranceConfig, Provenance
from verification.operation_classifier import OperationClass


def migrate_testcase_config(cfg, scenario_id: Optional[str] = None) -> Scenario:
    """Convert a TestCaseConfig to a transport-independent Scenario.

    This is a bridge function that reads the existing TestCaseConfig
    fields and produces equivalent Scenario actions and observations.
    The original TestCaseConfig is not modified.

    Args:
        cfg: A sim.rtl_soc_runner.TestCaseConfig instance.
        scenario_id: Optional override scenario ID (defaults to cfg.case_id).

    Returns:
        A fully populated Scenario ready for execution.
    """
    sid = scenario_id or getattr(cfg, "case_id", "unknown")
    actions = []
    expected_obs = []

    # ── MMIO writes → frontdoor MMIO write actions ──────────────────
    for addr, value in getattr(cfg, "mmio_write_sequence", []):
        actions.append(Action.mmio_write(addr, value))

    # Also handle mmio_writes dict entries that aren't in the sequence
    seen_mmio = {(a.parameters["address"], a.parameters["value"])
                 for a in actions if a.action_type == "mmio_write"}
    for addr, value in getattr(cfg, "mmio_writes", {}).items():
        if (int(addr), int(value)) not in seen_mmio:
            actions.append(Action.mmio_write(int(addr), int(value)))

    # ── SRAM preloads → initialization backdoors ────────────────────
    for offset, data in getattr(cfg, "sram_preloads", {}).items():
        actions.append(Action.sram_preload(int(offset), data))

    # handle sram_initial (legacy field)
    sram_initial = getattr(cfg, "sram_initial", None)
    if sram_initial is not None:
        actions.append(Action.sram_preload(0, sram_initial))

    # ── DRAM preloads → initialization backdoors ────────────────────
    for offset, data in getattr(cfg, "dram_preloads", {}).items():
        actions.append(Action.dram_preload(int(offset), data))

    dram_initial = getattr(cfg, "dram_initial", None)
    if dram_initial is not None:
        actions.append(Action.dram_preload(0, dram_initial))

    # ── PCIe writes → frontdoor PCIe TLP write actions ──────────────
    for addr, data in getattr(cfg, "pcie_writes", {}).items():
        actions.append(Action.pcie_write(int(addr), data))

    # ── Doorbell → frontdoor doorbell action ────────────────────────
    doorbell_cmd = getattr(cfg, "doorbell_cmd", None)
    doorbell_desc = getattr(cfg, "doorbell_desc_addr", None)
    if doorbell_cmd is not None:
        actions.append(Action.doorbell(int(doorbell_cmd), doorbell_desc))

    # ── IRQ / poll → frontdoor action ───────────────────────────────
    irq_source = getattr(cfg, "irq_source", None)
    if irq_source is not None:
        actions.append(Action.wait_irq(int(irq_source)))

    poll_addr = getattr(cfg, "poll_status_addr", None)
    if poll_addr is not None:
        poll_mask = getattr(cfg, "poll_status_mask", 0x2)
        poll_timeout = getattr(cfg, "poll_timeout_cycles", 100_000)
        actions.append(
            Action.poll_status(int(poll_addr), int(poll_mask), int(poll_timeout))
        )

    # ── Expected MMIO readbacks → observations ─────────────────────
    for addr, expected_val in getattr(cfg, "mmio_readbacks", {}).items():
        expected_obs.append(
            Observation.mmio_read(
                f"mmio_{int(addr):08X}",
                int(addr),
                int(expected_val),
            )
        )

    # ── Expected SRAM readbacks → observations ─────────────────────
    for addr, size in getattr(cfg, "sram_readbacks", {}).items():
        expected_obs.append(
            Observation.sram_readback(
                f"sram_{int(addr):08X}",
                int(addr),
                int(size),
            )
        )

    # ── Expected DRAM readbacks → observations ─────────────────────
    for addr, size in getattr(cfg, "dram_readbacks", {}).items():
        expected_obs.append(
            Observation.dram_readback(
                f"dram_{int(addr):08X}",
                int(addr),
                int(size),
            )
        )

    # ── Expected PCIe readbacks → observations ─────────────────────
    for addr, data in getattr(cfg, "pcie_readbacks", {}).items():
        expected_obs.append(Observation(
            observation_id=f"pcie_{int(addr):08X}",
            observation_type=ObservationType.pcie_readback,
            address=int(addr),
            data={"raw_hex": data.hex() if isinstance(data, bytes) else str(data)},
        ))

    # ── Build provenance ───────────────────────────────────────────
    provenance = Provenance(
        case_id=getattr(cfg, "case_id", None),
        created_at="",  # will be set by scenario factory if needed
    )

    # ── Build tolerance ────────────────────────────────────────────
    tolerance = ToleranceConfig.from_testcase_config(cfg)

    # ── Build scenario ─────────────────────────────────────────────
    scenario = Scenario(
        scenario_id=sid,
        scenario_version=1,
        description=getattr(cfg, "description", ""),
        actions=actions,
        expected_observations=expected_obs,
        tolerance=tolerance,
        provenance=provenance,
    )

    return scenario
