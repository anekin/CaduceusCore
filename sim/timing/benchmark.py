"""Benchmark CLI for LLM and CV model zoo aliases."""

from __future__ import annotations

import argparse
import copy
import csv
import importlib
import inspect
import sys
import tempfile
from pathlib import Path

import yaml

from sim.model_specs import ModelSpec, all_aliases, get_spec
from sim.timing.dashboard import Dashboard
from sim.timing.metrics import MetricsCollector
from sim.timing.timing_engine import TimingEngine
from sim.timing.types import RequestMetrics

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG = "sim/config/npu_config.yaml"
DEFAULT_OUTPUT = "results/timing/"
DEFAULT_PROMPT_LEN = 128
DEFAULT_GEN_LEN = 128


def _resolve_config(path: str) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    return REPO_ROOT / p


def _cv_generator_name(alias: str, module_name: str) -> str:
    base = module_name.rsplit(".", 1)[-1]
    if base == "cv_trace":
        return f"generate_{alias}_trace"
    return f"generate_{base}"


def _generate_cv_trace(alias: str, spec: ModelSpec) -> list[dict] | None:
    if not spec.cv_trace_module:
        return None
    module = importlib.import_module(spec.cv_trace_module)
    func_name = _cv_generator_name(alias, spec.cv_trace_module)
    if not hasattr(module, func_name):
        raise RuntimeError(
            f"CV trace generator '{func_name}' not found in {spec.cv_trace_module}"
        )
    func = getattr(module, func_name)
    sig = inspect.signature(func)
    if "onnx_path" in sig.parameters:
        onnx_path = REPO_ROOT / "assets" / "mobilenetv3_small.onnx"
        if not onnx_path.exists():
            print(
                f"Warning: MobileNetV3 ONNX not found at {onnx_path}; skipping '{alias}'.",
                file=sys.stderr,
            )
            return None
        return func(str(onnx_path))
    return func()


def _benchmark_alias(
    engine: TimingEngine,
    alias: str,
    args: argparse.Namespace,
) -> tuple[str, str] | None:
    spec = get_spec(alias)
    is_cv = spec.model_type == "cv"

    if is_cv:
        cv_trace = _generate_cv_trace(alias, spec)
        if cv_trace is None:
            print(f"Warning: skipped '{alias}' (CV trace unavailable).", file=sys.stderr)
            return None
        metrics = engine.simulate_cv(cv_trace)
        module_breakdown = metrics.module_breakdown
    else:
        metrics = engine.simulate_request(
            spec,
            prompt_len=args.prompt_len,
            gen_len=args.gen_len,
        )
        decode_timing = engine.simulate_decode(spec, prompt_len=1)
        module_breakdown = decode_timing.module_breakdown.cycles

    filled = RequestMetrics(**MetricsCollector.collect(metrics, args.freq))
    dashboard = Dashboard()
    json_path, md_path = dashboard.save(
        output_dir=args.output,
        model_name=alias,
        request_metrics=filled,
        module_breakdown=module_breakdown,
        freq_mhz=args.freq,
        is_cv=is_cv,
        engine_config=engine.config,
    )
    return json_path, md_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark CaduceusCore timing for LLM and CV model zoo aliases."
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Model alias to benchmark",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=DEFAULT_OUTPUT,
        help=f"Output directory (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=DEFAULT_CONFIG,
        help=f"NPU config YAML path (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--prompt-len",
        type=int,
        default=DEFAULT_PROMPT_LEN,
        help=f"Prompt length for LLM models (default: {DEFAULT_PROMPT_LEN})",
    )
    parser.add_argument(
        "--gen-len",
        type=int,
        default=DEFAULT_GEN_LEN,
        help=f"Generated token count for LLM models (default: {DEFAULT_GEN_LEN})",
    )
    parser.add_argument(
        "--freq",
        type=int,
        default=None,
        help="Clock frequency in MHz (default: from npu_config.yaml)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Benchmark all registered model aliases",
    )
    parser.add_argument(
        "--sweep-dma-channels",
        type=str,
        default=None,
        help="Comma-separated DMA channel counts to sweep (e.g., 1,2,4,8). "
             "Overrides dma.num_channels and dma.channels in config for each value. "
             "Outputs results/dma_sweep.csv.",
    )
    parser.add_argument(
        "--sweep-noc-topology",
        type=str,
        default=None,
        help="Comma-separated NoC topologies to sweep (e.g., crossbar,mesh). "
             "Overrides interconnect.type in config for each value. "
             "Outputs results/noc_sweep.csv.",
    )
    parser.add_argument(
        "--sweep-noc-ports",
        type=str,
        default=None,
        help="Comma-separated port counts to sweep (e.g., 2,4,8). "
             "Overrides interconnect.ports in config for each value. "
             "Default: 4 when not specified.",
    )
    return parser


def _run_dma_sweep(args: argparse.Namespace) -> int:
    """Run the DMA channel sweep and write results/dma_sweep.csv.

    For each channel count, overrides ``dma.num_channels`` and ``dma.channels``
    in the base config, creates a fresh TimingEngine, runs decode simulation,
    and records per-channel metrics.

    Returns exit code (0 on success, 1 on failure).
    """
    channel_strs = args.sweep_dma_channels.split(",")
    channel_counts: list[int] = []
    for s in channel_strs:
        s = s.strip()
        if not s:
            continue
        try:
            channel_counts.append(int(s))
        except ValueError:
            print(f"Error: invalid channel count '{s}'", file=sys.stderr)
            return 1

    if not channel_counts:
        print("Error: --sweep-dma-channels provided but no valid values", file=sys.stderr)
        return 1

    model_alias = args.model or "qwen2.5-3b"
    spec = get_spec(model_alias)
    if spec.model_type != "llm":
        print(f"Error: DMA sweep only supports LLM models, got '{model_alias}' ({spec.model_type})",
              file=sys.stderr)
        return 1

    config_path = _resolve_config(args.config)
    if not config_path.exists():
        print(f"Error: config not found: {config_path}", file=sys.stderr)
        return 1

    with open(config_path) as f:
        base_config = yaml.safe_load(f)

    freq_mhz = (
        args.freq
        if args.freq is not None
        else int(base_config.get("mxu", {}).get("frequency_mhz", 1000))
    )

    rows: list[dict] = []

    for channels in channel_counts:
        sweep_config = copy.deepcopy(base_config)
        sweep_config.setdefault("dma", {})
        sweep_config["dma"]["num_channels"] = channels
        sweep_config["dma"]["channels"] = channels

        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as tf:
                yaml.dump(sweep_config, tf)
                tf.flush()
                temp_path = tf.name
            sweep_engine = TimingEngine(temp_path)
        except Exception as exc:
            print(f"Error: failed to create engine for channels={channels}: {exc}",
                  file=sys.stderr)
            return 1
        finally:
            if temp_path is not None:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except Exception:
                    pass

        try:
            decode_timing = sweep_engine.simulate_decode(spec)
        except Exception as exc:
            print(f"Error: decode simulation failed for channels={channels}: {exc}",
                  file=sys.stderr)
            return 1

        mb = decode_timing.module_breakdown.cycles
        total_cycles = decode_timing.total_cycles

        dma_weight = mb.get("dma_weight", 0)
        dma_effective = mb.get("dma_effective", 0)
        mxu_cycles = mb.get("mxu", 0)

        tps = freq_mhz * 1e6 / total_cycles if total_cycles > 0 else 0.0
        dma_stall_pct = dma_weight / total_cycles * 100 if total_cycles > 0 else 0.0
        dma_sum = dma_weight + dma_effective
        dma_overlap_pct = dma_effective / dma_sum * 100 if dma_sum > 0 else 0.0
        mxu_pct = mxu_cycles / total_cycles * 100 if total_cycles > 0 else 0.0

        if mxu_pct > 60:
            bottleneck = "MXU"
        elif dma_stall_pct > 15:
            bottleneck = "DMA"
        else:
            bottleneck = "balanced"

        rows.append({
            "channels": channels,
            "tps": round(tps, 2),
            "dma_stall_pct": round(dma_stall_pct, 2),
            "dma_overlap_pct": round(dma_overlap_pct, 2),
            "bottleneck": bottleneck,
        })

        print(
            f"  channels={channels}: tps={tps:.2f}, "
            f"dma_stall={dma_stall_pct:.1f}%, "
            f"dma_overlap={dma_overlap_pct:.1f}%, "
            f"bottleneck={bottleneck}"
        )

    results_dir = REPO_ROOT / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    csv_path = results_dir / "dma_sweep.csv"

    fieldnames = ["channels", "tps", "dma_stall_pct", "dma_overlap_pct", "bottleneck"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDMA sweep results written to {csv_path}")
    return 0


def _run_noc_sweep(args: argparse.Namespace) -> int:
    """Run the NoC topology × ports sweep and write results/noc_sweep.csv.

    For each (topology, ports) combination, overrides ``interconnect.type``
    and ``interconnect.ports`` in the base config, creates a fresh
    TimingEngine, runs decode simulation, and records per-combination
    metrics.

    Returns exit code (0 on success, 1 on failure).
    """
    topologies = [t.strip() for t in args.sweep_noc_topology.split(",") if t.strip()]
    if not topologies:
        print("Error: --sweep-noc-topology provided but no valid topologies",
              file=sys.stderr)
        return 1

    port_strs = args.sweep_noc_ports.split(",") if args.sweep_noc_ports else ["4"]
    ports_list: list[int] = []
    for s in port_strs:
        s = s.strip()
        if not s:
            continue
        try:
            ports_list.append(int(s))
        except ValueError:
            print(f"Error: invalid port count '{s}'", file=sys.stderr)
            return 1

    if not ports_list:
        ports_list = [4]

    model_alias = args.model or "qwen2.5-3b"
    spec = get_spec(model_alias)
    if spec.model_type != "llm":
        print(
            f"Error: NoC sweep only supports LLM models, got '{model_alias}' "
            f"({spec.model_type})",
            file=sys.stderr,
        )
        return 1

    config_path = _resolve_config(args.config)
    if not config_path.exists():
        print(f"Error: config not found: {config_path}", file=sys.stderr)
        return 1

    with open(config_path) as f:
        base_config = yaml.safe_load(f)

    freq_mhz = (
        args.freq
        if args.freq is not None
        else int(base_config.get("mxu", {}).get("frequency_mhz", 1000))
    )

    rows: list[dict] = []

    for topology in topologies:
        for ports in ports_list:
            sweep_config = copy.deepcopy(base_config)
            sweep_config.setdefault("interconnect", {})
            sweep_config["interconnect"]["type"] = topology
            sweep_config["interconnect"]["ports"] = ports

            temp_path: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".yaml", delete=False
                ) as tf:
                    yaml.dump(sweep_config, tf)
                    tf.flush()
                    temp_path = tf.name
                sweep_engine = TimingEngine(temp_path)
            except Exception as exc:
                print(
                    f"Error: failed to create engine for "
                    f"{topology}x{ports}: {exc}",
                    file=sys.stderr,
                )
                return 1
            finally:
                if temp_path is not None:
                    try:
                        Path(temp_path).unlink(missing_ok=True)
                    except Exception:
                        pass

            try:
                decode_timing = sweep_engine.simulate_decode(spec)
            except Exception as exc:
                print(
                    f"Error: decode simulation failed for "
                    f"{topology}x{ports}: {exc}",
                    file=sys.stderr,
                )
                return 1

            mb = decode_timing.module_breakdown.cycles
            total_cycles = decode_timing.total_cycles

            noc_latency_cycles = mb.get("noc_latency", 0)
            noc_contention_cycles = mb.get("noc_contention", 0)

            tps = freq_mhz * 1e6 / total_cycles if total_cycles > 0 else 0.0
            noc_latency_us = noc_latency_cycles / freq_mhz if freq_mhz > 0 else 0.0
            noc_contention_pct = (
                noc_contention_cycles / total_cycles * 100
                if total_cycles > 0
                else 0.0
            )

            rows.append({
                "topology": topology,
                "ports": ports,
                "tps": round(tps, 2),
                "noc_latency_us": round(noc_latency_us, 2),
                "noc_contention_pct": round(noc_contention_pct, 2),
            })

            print(
                f"  {topology}x{ports}: tps={tps:.2f}, "
                f"noc_latency={noc_latency_us:.2f}us, "
                f"noc_contention={noc_contention_pct:.1f}%"
            )

    results_dir = REPO_ROOT / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    csv_path = results_dir / "noc_sweep.csv"

    fieldnames = [
        "topology", "ports", "tps", "noc_latency_us", "noc_contention_pct",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nNoC sweep results written to {csv_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # ── DMA sweep path (mutually exclusive with normal benchmark) ──
    if args.sweep_dma_channels is not None:
        return _run_dma_sweep(args)

    # ── NoC sweep path (mutually exclusive with normal benchmark) ──
    if args.sweep_noc_topology is not None:
        return _run_noc_sweep(args)

    if args.all:
        aliases = all_aliases()
    elif args.model:
        aliases = [args.model]
    else:
        parser.error(
            "Specify --model, --all, --sweep-dma-channels, "
            "or --sweep-noc-topology"
        )
        return 1

    config_path = _resolve_config(args.config)
    if not config_path.exists():
        print(f"Error: config not found: {config_path}", file=sys.stderr)
        return 1

    try:
        engine = TimingEngine(str(config_path))
    except Exception as exc:
        print(f"Error: failed to load TimingEngine: {exc}", file=sys.stderr)
        return 1

    if args.freq is None:
        args.freq = engine.freq_mhz

    output_dir = Path(args.output)
    failures: list[str] = []

    for alias in aliases:
        try:
            result = _benchmark_alias(engine, alias, args)
            if result is None:
                continue
            json_path, md_path = result
            print(f"{alias}: {json_path}, {md_path}")
        except Exception as exc:
            message = f"{alias}: {exc}"
            print(f"Error: {message}", file=sys.stderr)
            failures.append(message)

    if failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
