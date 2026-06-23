"""Regenerate summary.json and summary.md from individual dashboard JSONs."""
from __future__ import annotations

import json
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent.parent.parent / "results" / "timing"

LLM_KEYS = ["model", "type", "total_cycles", "ttft_ms", "tps", "tpot_us",
            "prefill_ms", "decode_per_token_us"]
CV_KEYS = ["model", "type", "total_cycles", "fps", "inference_latency_us"]

LLM_HEADERS = ["Model", "TTFT (ms)", "TPS (tok/s)", "TPOT (\u03bcs)",
               "Prefill (ms)", "Decode/Token (\u03bcs)", "Total Cycles"]
CV_HEADERS = ["Model", "FPS", "Inference Latency (\u03bcs)", "Total Cycles"]


def _round_val(v: float) -> float:
    return round(v, 2)


def _load_dashboard(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def _extract_llm(d: dict) -> dict:
    return {
        "model": d["model"],
        "type": "llm",
        "total_cycles": d["total_cycles"],
        "ttft_ms": _round_val(d["ttft_ms"]),
        "tps": _round_val(d["tps"]),
        "tpot_us": _round_val(d["tpot_us"]),
        "prefill_ms": _round_val(d["prefill_ms"]),
        "decode_per_token_us": _round_val(d["decode_per_token_us"]),
    }


def _extract_cv(d: dict) -> dict:
    return {
        "model": d["model"],
        "type": "cv",
        "total_cycles": d["total_cycles"],
        "fps": _round_val(d["fps"]),
        "inference_latency_us": _round_val(d["inference_latency_us"]),
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
        row = " | ".join(
            str(e.get(k.replace(" (", "_").replace(")", "").replace(" ", "_")
                       .lower().replace("\u03bcs", "us").replace("tok/s", "tok_per_s")
                       .replace("ms", "ms"), ""))
            for k in LLM_HEADERS
        )
        # simpler approach
        cells = [
            e["model"],
            f"{e['ttft_ms']:.2f}",
            f"{e['tps']:.2f}",
            f"{e['tpot_us']:.2f}",
            f"{e['prefill_ms']:.2f}",
            f"{e['decode_per_token_us']:.2f}",
            str(e["total_cycles"]),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## CV Models\n")
    header_cv = " | ".join(CV_HEADERS)
    sep_cv = "|".join("---" for _ in CV_HEADERS)
    lines.append(f"| {header_cv} |")
    lines.append(f"|{sep_cv}|")
    for e in cv_entries:
        cells = [
            e["model"],
            f"{e['fps']:.2f}",
            f"{e['inference_latency_us']:.2f}",
            str(e["total_cycles"]),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("> **Note:** TTFT is engine-only (prefill + first decode). "
                 "Prefill is constant (103.23 ms) across all LLM models because "
                 "it depends only on prompt length (128 tokens) and hardware "
                 "config (1 core, 1 GHz), not model size.\n")
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
