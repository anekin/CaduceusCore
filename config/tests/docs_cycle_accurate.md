# Bad Doc Fixture — Cycle-Accurate Overclaim

This fixture intentionally contains forbidden language that the doc checker must reject.

## Performance Claims

The Func Model is a **cycle-accurate** simulator that produces **RTL-calibrated**
measurements for every operation. All numbers are **measured cycles** from real
hardware and can be used directly as product KPI gates.

This language violates the perf-spec policy that all Func Model numbers must be
marked as `estimated_cycles`, `architecture_assumption`, and `uncalibrated` until
future RTL calibration is complete.
