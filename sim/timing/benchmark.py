"""Benchmark CLI for LLM and CV model zoo aliases."""

from __future__ import annotations

import argparse
import importlib
import inspect
import sys
from pathlib import Path

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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.all:
        aliases = all_aliases()
    elif args.model:
        aliases = [args.model]
    else:
        parser.error("Specify --model or --all")
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
