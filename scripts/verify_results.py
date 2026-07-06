#!/usr/bin/env python3
"""Verify all generated RTL result files using compare_rtl.py default tolerance."""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sim"))
from compare_rtl import compare_test

RESULTS_DIR = REPO_ROOT / "rtl/results"
SFU_DIR = REPO_ROOT / "rtl/test_vectors/sfu"
VEC_DIR = REPO_ROOT / "rtl/test_vectors/vector"
EVIDENCE = REPO_ROOT / "build/evidence/task-17-compare_rtl-verify.txt"


def discover_scenarios(base: Path):
    out = []
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        if d.name == "random_regression":
            for sub in sorted(d.iterdir()):
                if sub.is_dir() and (sub / "manifest.json").exists():
                    out.append(sub)
        elif (d / "manifest.json").exists():
            out.append(d)
    return out


def result_path_for(scenario_dir: Path):
    name = scenario_dir.name
    kind = "sfu" if scenario_dir.is_relative_to(SFU_DIR) else "vector"
    return RESULTS_DIR / f"{kind}_{name}.hex"


def main():
    lines = []
    all_pass = True
    for base, label in [(SFU_DIR, "sfu"), (VEC_DIR, "vector")]:
        scenarios = discover_scenarios(base)
        pass_count = 0
        for s in scenarios:
            rp = result_path_for(s)
            key = "sfu" if label == "sfu" else "mmul"
            try:
                if rp.exists():
                    results = compare_test(s, {"default": rp})
                else:
                    results = [type("R", (), {"test_name": s.name, "passed": False, "details": f"missing {rp}"})]
            except Exception as e:
                results = [type("R", (), {"test_name": s.name, "passed": False, "details": str(e)})]
            for r in results:
                status = "PASS" if r.passed else "FAIL"
                if not r.passed:
                    all_pass = False
                    lines.append(f"[{status}] {label:6} {r.test_name}: {getattr(r, 'details', '')}")
                else:
                    pass_count += 1
        lines.append(f"{label}: {pass_count}/{len(scenarios)} passed")
    lines.append(f"OVERALL: {'PASS' if all_pass else 'FAIL'}")
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text("\n".join(lines))
    print(EVIDENCE.read_text())
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
