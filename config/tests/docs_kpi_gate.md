# Bad Doc Fixture — KPI-as-Gate Without Report-Only

This fixture intentionally treats report-only KPIs as hard product gates,
which the doc checker must reject.

## Product Signoff

The Block 64×64 configuration is **approved for production** because it meets
the hard KPI gate:

- `decode_tps >= 21.59`
- `ttft_ms <= 200`

Any configuration that fails these KPIs must block tape-out. No uncertainty band
is needed because the Func Model is cycle-accurate and the numbers are final.

This violates the policy that Func Model KPIs are **report-only** until RTL
calibration; claims must include `uncalibrated`, `estimated_cycles`, and document
assumptions/uncertainty.
