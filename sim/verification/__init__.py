"""sim/verification/ — Transport-independent verification infrastructure.

Shared scenario, observation, scoreboard, and DUT-adapter contracts for
Func Model, RTL, and FPGA verification adapters. No cocotb or Func Model
dependencies in core types.

Public API:
    from verification import (
        Scenario, Action, Observation, ToleranceConfig, Provenance,
        OperationClass, EvidenceRecord, DUTAdapter, Scoreboard, ScoreboardResult,
        FakeDUTAdapter, migrate_testcase_config,
        FaultClass, FaultInjector, FaultInjectionRecord,
    )
"""

from verification.operation_classifier import OperationClass
from verification.tolerance import ToleranceConfig, Provenance
from verification.observation import Observation
from verification.scenario import Scenario, Action, EvidenceRecord
from verification.dut_adapter import DUTAdapter, FakeDUTAdapter
from verification.scoreboard import Scoreboard, ScoreboardResult
from verification.migration import migrate_testcase_config
from verification.rtl_adapter import RTLAdapter
from verification.fm_adapter import FuncModelAdapter
from verification.fault_injector import (
    FaultClass,
    FaultInjector,
    FaultInjectionRecord,
)

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
    "FuncModelAdapter",
    "Scoreboard",
    "ScoreboardResult",
    "migrate_testcase_config",
    "RTLAdapter",
    "FaultClass",
    "FaultInjector",
    "FaultInjectionRecord",
]
