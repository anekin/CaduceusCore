"""sim/verification/ — Transport-independent verification infrastructure.

Shared scenario, observation, scoreboard, and DUT-adapter contracts for
Func Model, RTL, and FPGA verification adapters. No cocotb or Func Model
dependencies in core types.

Public API:
    from sim.verification import (
        Scenario, Action, Observation, ToleranceConfig, Provenance,
        OperationClass, EvidenceRecord, DUTAdapter, Scoreboard, ScoreboardResult,
        FakeDUTAdapter, migrate_testcase_config,
    )
"""

from sim.verification.operation_classifier import OperationClass
from sim.verification.tolerance import ToleranceConfig, Provenance
from sim.verification.observation import Observation
from sim.verification.scenario import Scenario, Action, EvidenceRecord
from sim.verification.dut_adapter import DUTAdapter, FakeDUTAdapter
from sim.verification.scoreboard import Scoreboard, ScoreboardResult
from sim.verification.migration import migrate_testcase_config

__all__ = [
    "Scenario",
    "Action",
    "Observation",
    "ToleranceConfig",
    "Provenance",
    "OperationClass",
    "EvidenceRecord",
    "DUTAdapter",
    "FakeDUTAdapter",
    "Scoreboard",
    "ScoreboardResult",
    "migrate_testcase_config",
]
