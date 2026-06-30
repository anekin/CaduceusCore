import os
import subprocess
import sys
from pathlib import Path

import pytest

from model_specs import all_aliases
from timing.benchmark import main
from timing.types import ModuleBreakdown, RequestMetrics, TokenTiming

CADUCEUSCORE_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _run_benchmark(argv: list[str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(CADUCEUSCORE_ROOT / "sim")
    return subprocess.run(
        [sys.executable, "-m", "sim.timing.benchmark", *argv],
        cwd=str(CADUCEUSCORE_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def test_llm_cli_produces_files(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    result = _run_benchmark(
        [
            "--model",
            "qwen2.5-1.5b",
            "--output",
            str(output_dir),
            "--gen-len",
            "2",
        ]
    )
    assert result.returncode == 0, result.stderr
    assert (output_dir / "qwen2.5-1.5b.json").exists()
    assert (output_dir / "qwen2.5-1.5b.md").exists()


def test_unsupported_alias_exits_nonzero() -> None:
    result = _run_benchmark(["--model", "not-a-real-model"])
    assert result.returncode != 0
    assert "Unknown model alias" in result.stderr


class _FakeTimingEngine:
    def __init__(self, config_path: str) -> None:
        self.config = {"test": True}
        self.freq_mhz = 1000

    def simulate_request(
        self, spec, prompt_len: int, gen_len: int
    ) -> RequestMetrics:
        return RequestMetrics(
            prompt_len=prompt_len,
            output_tokens=gen_len,
            prefill_cycles=100,
            decode_cycles_per_token=[50] * gen_len,
        )

    def simulate_decode(self, spec, prompt_len: int = 1) -> TokenTiming:
        return TokenTiming(
            token_idx=0,
            phase="decode",
            total_cycles=50,
            module_breakdown=ModuleBreakdown(),
        )

    def simulate_cv(self, cv_trace: list[dict]) -> RequestMetrics:
        metrics = RequestMetrics(
            output_tokens=1,
            prefill_cycles=0,
            decode_cycles_per_token=[10],
        )
        metrics.module_breakdown = ModuleBreakdown().cycles
        return metrics


def test_all_produces_multiple_outputs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "sim.timing.benchmark.TimingEngine", _FakeTimingEngine
    )
    output_dir = tmp_path / "all_out"
    aliases = all_aliases()
    code = main(["--all", "--output", str(output_dir), "--gen-len", "1"])
    assert code == 0
    files = list(output_dir.iterdir())
    json_files = [f for f in files if f.suffix == ".json"]
    assert len(json_files) == len(aliases)
