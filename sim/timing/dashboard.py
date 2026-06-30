"""JSON and Markdown report generation from timing metrics."""

from __future__ import annotations

import datetime
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any

from timing.metrics import MetricsCollector
from timing.types import RequestMetrics


def _mesh_dims(ports: int) -> tuple[int, int]:
    """Return (rows, cols) for a closest-to-square 2-D mesh of *ports* nodes.

    Mirrors :meth:`sim.models.noc.NoCModel._compute_dims` to avoid a
    cross-package import.
    """
    limit = int(math.ceil(math.sqrt(ports)))
    for c in range(limit, 0, -1):
        if ports % c == 0:
            r = ports // c
            return (min(r, c), max(r, c))
    return (limit, int(math.ceil(ports / limit)))


def _percentile(sorted_data: list[float], p: float) -> float:
    """Compute the p-th percentile (0-100) using linear interpolation."""
    if not sorted_data:
        return 0.0
    if p <= 0.0:
        return sorted_data[0]
    if p >= 100.0:
        return sorted_data[-1]
    k = (len(sorted_data) - 1) * p / 100.0
    f = int(k)
    c = k - f
    if f + 1 < len(sorted_data):
        return sorted_data[f] + c * (sorted_data[f + 1] - sorted_data[f])
    return sorted_data[f]


def _round_dict_values(d: dict[str, Any]) -> dict[str, Any]:
    """Round all float values in a dict to 2 decimal places."""
    result: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, float):
            result[k] = round(v, 2)
        elif isinstance(v, dict):
            result[k] = _round_dict_values(v)
        else:
            result[k] = v
    return result


def _itl_histogram_ascii(itl_list: list[float], bins: int = 10) -> str:
    """Build an ASCII histogram string for ITL values."""
    if not itl_list:
        return "No ITL data available.\n"

    sorted_vals = sorted(itl_list)
    min_val = sorted_vals[0]
    max_val = sorted_vals[-1]

    if min_val == max_val:
        bin_width = 1.0
    else:
        bin_width = (max_val - min_val) / bins

    counts = [0] * bins
    for val in itl_list:
        idx = min(bins - 1, int((val - min_val) / bin_width) if bin_width > 0 else 0)
        counts[idx] += 1

    max_count = max(counts) if counts else 1
    bar_max = 40

    lines: list[str] = []
    for i in range(bins):
        lo = min_val + i * bin_width
        hi = lo + bin_width
        bar = "#" * max(1, int(counts[i] / max_count * bar_max)) if max_count > 0 else ""
        lines.append(f"  {round(lo, 1):>8} - {round(hi, 1):>8} us: {bar} ({counts[i]})")

    return "\n".join(lines)


class Dashboard:
    """Generate JSON and Markdown performance reports from timing metrics."""

    @staticmethod
    def generate_json(
        model_name: str,
        request_metrics: RequestMetrics,
        module_breakdown: dict[str, int],
        freq_mhz: int,
        is_cv: bool = False,
        engine_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate a complete JSON metrics dict.

        Args:
            model_name: Human-readable model identifier.
            request_metrics: Aggregated metrics from TimingEngine.
            module_breakdown: Per-module cycle counts.
            freq_mhz: Clock frequency in MHz.
            is_cv: If True, emit FPS / inference_latency_us instead of TPS / TTFT.
            engine_config: Optional engine metadata dict.

        Returns:
            Deterministically ordered dict with all required keys and 2-dp float
            precision.
        """
        total_cycles = sum(module_breakdown.values()) if module_breakdown else 0
        dma_weight = module_breakdown.get("dma_weight", 0) if module_breakdown else 0
        dma_effective = module_breakdown.get("dma_effective", 0) if module_breakdown else 0

        # --- per-module utilization from MetricsCollector ---
        module_utilization_pct: dict[str, float] = MetricsCollector.compute_module_utilization(
            module_breakdown, total_cycles
        )

        # --- bandwidth & DMA overlap ---
        bandwidth_pct = round(
            (dma_weight + dma_effective) / total_cycles * 100.0
            if total_cycles else 0.0, 2
        )
        dma_overlap = round(
            dma_effective / dma_weight if dma_weight > 0 else 0.0, 2
        )

        # --- NoC metrics ---
        noc_latency_cycles = module_breakdown.get("noc_latency", 0) if module_breakdown else 0
        noc_contention_cycles = module_breakdown.get("noc_contention", 0) if module_breakdown else 0
        noc_latency_us = round(
            noc_latency_cycles / freq_mhz if freq_mhz else 0.0, 2
        )
        noc_contention_pct = round(
            noc_contention_cycles / total_cycles * 100.0
            if total_cycles else 0.0, 2
        )
        noc_topology = "unknown"
        noc_ports = 0
        noc_mesh_rows: int | None = None
        noc_mesh_cols: int | None = None
        if engine_config and "interconnect" in engine_config:
            ic = engine_config["interconnect"]
            noc_topology = ic.get("type", "unknown")
            noc_ports = int(ic.get("ports", 0))
            if noc_topology == "mesh" and noc_ports > 0:
                noc_mesh_rows, noc_mesh_cols = _mesh_dims(noc_ports)

        # --- ITL percentiles ---
        itl_list = request_metrics.itl_us_list
        itl_sorted = sorted(itl_list)
        itl_p50 = round(_percentile(itl_sorted, 50.0), 2)
        itl_p90 = round(_percentile(itl_sorted, 90.0), 2)
        itl_p99 = round(_percentile(itl_sorted, 99.0), 2)

        # --- Build output dict ---
        output: dict[str, Any] = {
            "model": model_name,
            "engine": "CaduceusCore TimingEngine",
            "config": engine_config if engine_config is not None else {
                "engine": "CaduceusCore",
                "version": "1.0",
            },
            "itl_us_list": itl_list,
            "itl_us_p50": itl_p50,
            "itl_us_p90": itl_p90,
            "itl_us_p99": itl_p99,
            "module_breakdown": module_breakdown,
            "module_utilization_pct": _round_dict_values(module_utilization_pct),
            "noc_topology": noc_topology,
            "noc_ports": noc_ports,
            "noc_latency_us": noc_latency_us,
            "noc_contention_pct": noc_contention_pct,
            "noc_mesh_rows": noc_mesh_rows,
            "noc_mesh_cols": noc_mesh_cols,
            "bandwidth_utilization_pct": bandwidth_pct,
            "dma_overlap_ratio": dma_overlap,
            "total_cycles": total_cycles,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        if is_cv:
            output["fps"] = round(
                freq_mhz * 1e6 / total_cycles if total_cycles else 0.0, 2
            )
            output["inference_latency_us"] = round(
                total_cycles / freq_mhz if freq_mhz else 0.0, 2
            )
        else:
            decode_per_token_us = round(
                mean(request_metrics.decode_cycles_per_token) / freq_mhz
                if request_metrics.decode_cycles_per_token and freq_mhz else 0.0, 2
            )
            output["tps"] = round(request_metrics.tps, 2)
            output["ttft_ms"] = round(request_metrics.ttft_us / 1000.0, 2)
            output["tpot_us"] = round(
                request_metrics.tpot_us if request_metrics.tpot_us is not None else 0.0, 2
            )
            output["prefill_ms"] = round(
                request_metrics.prefill_cycles / (freq_mhz * 1e3) if freq_mhz else 0.0, 2
            )
            output["decode_per_token_us"] = decode_per_token_us

        # --- Deterministic sorting ---
        return dict(sorted(output.items()))

    @staticmethod
    def generate_markdown(json_data: dict[str, Any]) -> str:
        """Render a JSON metrics dict as a Markdown report.

        Sections:
          - Title with model name
          - Summary table
          - Per-module cycle table
          - ITL histogram (ASCII)
          - Config section
          - Footer with TTFT definition note
        """
        model = json_data.get("model", "unknown")
        lines: list[str] = []

        # --- Title ---
        lines.append(f"# Performance Dashboard — {model}\n")
        lines.append(f"**Engine**: {json_data.get('engine', 'N/A')}")
        lines.append(f"**Timestamp**: {json_data.get('timestamp', 'N/A')}\n")

        # --- Summary table ---
        lines.append("## Summary\n")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")

        summary_keys = ["tps", "fps", "ttft_ms", "tpot_us", "prefill_ms",
                        "decode_per_token_us", "inference_latency_us",
                        "itl_us_p50", "itl_us_p90", "itl_us_p99",
                        "bandwidth_utilization_pct", "dma_overlap_ratio",
                        "total_cycles"]

        for key in summary_keys:
            if key in json_data:
                val = json_data[key]
                label = key.replace("_", " ").title()
                lines.append(f"| {label} | {val} |")

        lines.append("")

        # --- Per-module cycle table ---
        mb = json_data.get("module_breakdown", {})
        if mb:
            lines.append("## Per-Module Cycles\n")
            lines.append("| Module | Cycles |")
            lines.append("|--------|--------|")
            for mod, cycles in mb.items():
                lines.append(f"| {mod} | {cycles} |")
            lines.append("")

        # --- Module utilization table ---
        mu = json_data.get("module_utilization_pct", {})
        if mu:
            lines.append("## Module Utilization\n")
            lines.append("| Module | % |")
            lines.append("|--------|---|")
            for mod, pct in mu.items():
                lines.append(f"| {mod} | {round(float(pct), 2)} |")
            lines.append("")

        # --- NoC section ---
        noc_topology = json_data.get("noc_topology", "unknown")
        if noc_topology != "unknown":
            lines.append("## NoC\n")
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            lines.append(f"| Topology | {noc_topology} |")
            noc_ports = json_data.get("noc_ports", 0)
            if noc_ports:
                lines.append(f"| Ports | {noc_ports} |")
            noc_lat = json_data.get("noc_latency_us", 0.0)
            lines.append(f"| Latency (us) | {noc_lat} |")
            noc_cont = json_data.get("noc_contention_pct", 0.0)
            lines.append(f"| Contention (%) | {noc_cont} |")

            if noc_topology == "mesh":
                mesh_rows = json_data.get("noc_mesh_rows")
                mesh_cols = json_data.get("noc_mesh_cols")
                if mesh_rows is not None and mesh_cols is not None:
                    max_hops = (mesh_rows - 1) + (mesh_cols - 1)
                    lines.append("")
                    lines.append("**Mesh Geometry**")
                    lines.append(f"- Dimensions: {mesh_rows}×{mesh_cols}")
                    lines.append(f"- Max XY hop count: {max_hops}")
            lines.append("")

        # --- ITL histogram ---
        itl_list = json_data.get("itl_us_list", [])
        if itl_list:
            lines.append("## ITL Distribution (ASCII histogram)\n")
            lines.append("```")
            lines.append(_itl_histogram_ascii(itl_list))
            lines.append("```\n")

        # --- Config section ---
        config = json_data.get("config", {})
        if config:
            lines.append("## Configuration\n")
            lines.append("```json")
            lines.append(json.dumps(config, indent=2))
            lines.append("```\n")

        # --- Footer ---
        lines.append("---")
        lines.append(
            "*TTFT (Time-To-First-Token) is engine-only latency "
            "(prefill + first decode), excluding queue/network overhead.*"
        )

        return "\n".join(lines) + "\n"

    def save(
        self,
        output_dir: str | Path,
        model_name: str,
        request_metrics: RequestMetrics,
        module_breakdown: dict[str, int],
        freq_mhz: int,
        is_cv: bool = False,
        engine_config: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Save JSON and Markdown reports under *output_dir*.

        Returns:
            ``(json_path, md_path)`` as strings.
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        json_data = self.generate_json(
            model_name=model_name,
            request_metrics=request_metrics,
            module_breakdown=module_breakdown,
            freq_mhz=freq_mhz,
            is_cv=is_cv,
            engine_config=engine_config,
        )
        md_text = self.generate_markdown(json_data)

        json_path = out / f"{model_name}.json"
        md_path = out / f"{model_name}.md"

        json_path.write_text(json.dumps(json_data, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(md_text, encoding="utf-8")

        return (str(json_path), str(md_path))
