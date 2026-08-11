"""Regenerate summary.json and summary.md from individual dashboard JSONs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).resolve().parent.parent.parent / "results" / "timing"

LLM_KEYS = ["model", "type", "total_cycles", "ttft_ms", "tps", "tpot_us",
            "prefill_ms", "decode_per_token_us"]
CV_KEYS = ["model", "type", "total_cycles", "fps", "inference_latency_us"]

LLM_HEADERS = ["Model", "TTFT (ms)", "TPS (tok/s)", "TPOT (\u03bcs)",
               "Prefill (ms)", "Decode/Token (\u03bcs)", "Total Cycles"]
CV_HEADERS = ["Model", "FPS", "Inference Latency (\u03bcs)", "Total Cycles"]


def _is_band(v: Any) -> bool:
    return isinstance(v, dict) and {"low", "base", "high"} <= set(v.keys())


def _band_str(v: Any) -> str:
    if _is_band(v):
        return f"{v['low']:.2f}/{v['base']:.2f}/{v['high']:.2f}"
    return f"{_round_val(v):.2f}" if isinstance(v, (int, float)) else str(v)


def _round_val(v: float) -> float:
    return round(v, 2)


def _load_dashboard(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def _extract_band_or_scalar(d: dict, key: str) -> Any:
    """Extract a KPI value, preserving low/base/high bands when present."""
    v = d.get(key)
    if _is_band(v):
        return {
            "low": _round_val(v["low"]),
            "base": _round_val(v["base"]),
            "high": _round_val(v["high"]),
        }
    return _round_val(v) if isinstance(v, (int, float)) else v


def _extract_llm(d: dict) -> dict:
    return {
        "model": d["model"],
        "type": "llm",
        "total_cycles": d["total_cycles"],
        "ttft_ms": _extract_band_or_scalar(d, "ttft_ms"),
        "tps": _extract_band_or_scalar(d, "tps"),
        "tpot_us": _extract_band_or_scalar(d, "tpot_us"),
        "prefill_ms": _extract_band_or_scalar(d, "prefill_ms"),
        "decode_per_token_us": _extract_band_or_scalar(d, "decode_per_token_us"),
    }


def _extract_cv(d: dict) -> dict:
    return {
        "model": d["model"],
        "type": "cv",
        "total_cycles": d["total_cycles"],
        "fps": _extract_band_or_scalar(d, "fps"),
        "inference_latency_us": _extract_band_or_scalar(d, "inference_latency_us"),
    }


def _model_sort_key(entry: dict) -> str:
    return entry["model"]


def generate_summary_json(dashboards: list[dict]) -> list[dict]:
    entries: list[dict] = []
    for d in dashboards:
        is_cv = d.get("type") == "cv" or "fps" in d
        entries.append(_extract_cv(d) if is_cv else _extract_llm(d))
    entries.sort(key=_model_sort_key)
    return entries


def generate_summary_md(entries: list[dict]) -> str:
    llm_entries = [e for e in entries if e["type"] == "llm"]
    cv_entries = [e for e in entries if e["type"] == "cv"]

    lines = ["# CaduceusCore Timing Summary \u2014 Model Zoo\n"]

    lines.append("## LLM Models\n")
    header = " | ".join(h for h in LLM_HEADERS)
    sep = "|".join("---" for _ in LLM_HEADERS)
    lines.append(f"| {header} |")
    lines.append(f"|{sep}|")
    for e in llm_entries:
        cells = [
            e["model"],
            _band_str(e["ttft_ms"]),
            _band_str(e["tps"]),
            _band_str(e["tpot_us"]),
            _band_str(e["prefill_ms"]),
            _band_str(e["decode_per_token_us"]),
            str(e["total_cycles"]),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## CV Models\n")
    header_cv = " | ".join(h for h in CV_HEADERS)
    sep_cv = "|".join("---" for _ in CV_HEADERS)
    lines.append(f"| {header_cv} |")
    lines.append(f"|{sep_cv}|")
    for e in cv_entries:
        cells = [
            e["model"],
            _band_str(e["fps"]),
            _band_str(e["inference_latency_us"]),
            str(e["total_cycles"]),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("> **Note:** TTFT is engine-only (prefill + first decode). "
                 "Prefill latency is model- and workload-specific, derived "
                 "dynamically from each dashboard report.\n")
    return "\n".join(lines)


def main() -> None:
    dashboards: list[dict] = []
    for fpath in sorted(RESULTS_DIR.glob("*.json")):
        if fpath.name == "summary.json":
            continue
        dashboards.append(_load_dashboard(fpath))

    entries = generate_summary_json(dashboards)

    # Write summary.json
    json_path = RESULTS_DIR / "summary.json"
    with open(json_path, "w") as f:
        json.dump(entries, f, indent=2)
    print(f"Written: {json_path}")

    # Write summary.md
    md_path = RESULTS_DIR / "summary.md"
    with open(md_path, "w") as f:
        f.write(generate_summary_md(entries))
    print(f"Written: {md_path}")


if __name__ == "__main__":
    main()
