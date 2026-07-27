"""Operation classification for DUT actions.

Every action on a DUT must be classified so the scoreboard and evidence
records can distinguish legitimate software-E2E operations from testbench
conveniences.

Classifications:
    frontdoor              — Software E2E operation through the real
                              hardware interface (MMIO, PCIe, doorbell, IRQ).
                              Must appear in signoff scenarios.

    allowed_init_backdoor  — Initialization backdoor (SRAM/DRAM preload for
                              setup before the real operation begins).
                              Allowed in any scenario.

    allowed_obs_backdoor   — Observation backdoor (reading internal state
                              that the software wouldn't normally see).
                              Allowed for verification but must be tagged.

    diagnostic             — Diagnostic-only access (debug, timing probes,
                              internal signal inspection). Not allowed in
                              signoff scenarios.
"""

from enum import Enum


class OperationClass(str, Enum):
    """Classification of a DUT action by how it accesses the device."""

    frontdoor = "frontdoor"
    allowed_init_backdoor = "allowed_init_backdoor"
    allowed_obs_backdoor = "allowed_obs_backdoor"
    diagnostic = "diagnostic"


# ── Classification helpers ──────────────────────────────────────────────

# Actions that are always frontdoor (real hardware interface)
_FRONTDOOR_ACTIONS = frozenset({
    "mmio_write", "mmio_read", "pcie_write", "pcie_read",
    "doorbell", "wait_irq", "poll_status", "reset",
})

# Actions that are always initialization backdoors
_INIT_BACKDOOR_ACTIONS = frozenset({
    "sram_preload", "dram_preload",
})

# Actions that are always observation backdoors
_OBS_BACKDOOR_ACTIONS = frozenset({
    "sram_readback", "dram_readback", "mmio_readback",
})


def classify_action(action_type: str) -> OperationClass:
    """Return the default OperationClass for an action type.

    Args:
        action_type: One of the known action type strings.

    Returns:
        The default OperationClass for that action type.
        Unknown action types default to diagnostic.

    Raises:
        ValueError: If action_type is empty or None.
    """
    if not action_type:
        raise ValueError("action_type must not be empty")

    if action_type in _FRONTDOOR_ACTIONS:
        return OperationClass.frontdoor
    if action_type in _INIT_BACKDOOR_ACTIONS:
        return OperationClass.allowed_init_backdoor
    if action_type in _OBS_BACKDOOR_ACTIONS:
        return OperationClass.allowed_obs_backdoor

    return OperationClass.diagnostic


def validate_scenario_operations(actions: list) -> list[str]:
    """Validate operation classifications in a list of actions.

    Detects undeclared backdoor operations — actions whose classification
    doesn't match their type (e.g., a sram_preload classified as frontdoor).

    Returns:
        List of error messages (empty if valid).
    """
    errors = []

    for i, action in enumerate(actions):
        action_type = getattr(action, "action_type", "")
        classification = getattr(action, "classification", None)

        if not classification:
            continue  # will be filled by default in action construction

        expected = classify_action(action_type)

        # backdoor classified as frontdoor
        if expected in (
            OperationClass.allowed_init_backdoor,
            OperationClass.allowed_obs_backdoor,
        ) and classification == OperationClass.frontdoor:
            errors.append(
                f"Action[{i}] ({action_type}): classified as frontdoor "
                f"but type requires {expected.value}"
            )

        # diagnostic classified as non-diagnostic
        if expected == OperationClass.diagnostic and classification != OperationClass.diagnostic:
            errors.append(
                f"Action[{i}] ({action_type}): unknown action type "
                f"classified as {classification.value} instead of diagnostic"
            )

    return errors
