"""F3 TPS cross-check: dashboard tps vs NPUSimulator decode_tok_per_s."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from model_specs import get_spec, llm_aliases
from npu_sim import NPUSimulator
from timing.timing_engine import _build_llm_trace  # reuse trace builder

RESULTS_DIR = Path(__file__).resolve().parent.parent.parent / "results" / "timing"
TOLERANCE = 15.0  # ±15%


def main() -> int:
    config_path = str(Path(__file__).resolve().parent.parent / "config" / "npu_config.yaml")
    sim = NPUSimulator(config_path)

    failures = 0
    print("=" * 72)
    print(f"{'Model':<20} {'Dashboard TPS':>14} {'npu_decode_tok/s':>16} {'Diff %':>8} {'Status':>8}")
    print("-" * 72)

    for alias in sorted(llm_aliases()):
        spec = get_spec(alias)

        # 1. Build trace and run NPUSimulator decode
        trace = _build_llm_trace(spec, m=1)
        report = sim.simulate_decode(trace)
        npu_tps = report.decode_tok_per_s

        # 2. Load dashboard JSON
        dash_path = RESULTS_DIR / f"{alias}.json"
        if not dash_path.exists():
            print(f"  Warning: dashboard not found for {alias}, skipping.")
            continue
        with open(dash_path) as f:
            dashboard = json.load(f)
        dash_tps = dashboard["tps"]

        # 3. Compare
        if npu_tps > 0:
            diff_pct = (dash_tps - npu_tps) / npu_tps * 100
        else:
            diff_pct = 0.0 if dash_tps == 0 else 999.0

        within = abs(diff_pct) <= TOLERANCE
        status = "PASS" if within else "FAIL"
        if not within:
            failures += 1

        print(f"{alias:<20} {dash_tps:>14.2f} {npu_tps:>16.2f} {diff_pct:>+7.2f}% {status:>8}")

    print("-" * 72)
    if failures:
        print(f"FAILED: {failures} model(s) outside ±{TOLERANCE}% tolerance.")
        return 1
    else:
        print(f"ALL PASS: all models within ±{TOLERANCE}% tolerance.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
