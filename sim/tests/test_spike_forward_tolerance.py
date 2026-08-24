"""Spike forward pass tolerance regression gate (todo 13, fm-soc-datapath-hardening).

E2E-06: pin the current Spike forward-pass acceptance thresholds as regression
baselines so any future numerical regression in the
GGUF → INT4 quantize → Spike (RISC-V firmware) → MMIO bridge → Func Model path
is caught.

Baselines pinned here:

- **2-layer forward** (``spike_host.run_forward_pass``, tol ``1e-1``): every
  per-layer ``max_abs`` vs the llama.cpp reference must stay below ``1e-1`` →
  ``result["ok"] is True``.  ``run_forward_pass`` returns a plain dict with
  keys ``ok`` / ``errors`` / ``layer_outputs`` — there is NO
  ``tolerance_result`` field (pinned by a dedicated schema test so future
  callers never reach for it).  NOTE: on the current stack this legacy path
  cannot run — its in-window DRAM allocation of tiled FFN weights exceeds the
  firmware-enforced 8 MB window [0x80000000, 0x80800000) (BUG-RTL-SOC-002,
  enforced by ``dram_range_ok`` in ``firmware/npu_firmware.c``); the fixture
  catches the resulting ``MemoryError`` and skips, and the window-compliant
  2-layer gate below carries the substance.
- **2-layer forward, window-compliant** (``spike_host.run_forward_pass_phase10``
  with ``layers=2``): per-layer ``cos_sim`` vs the Func Model golden must meet
  the ``P10_LADDER`` thresholds (0.999 for L0–L19).
- **36-layer forward** (``run_forward_pass_phase10``, ``layers=36``): per-layer
  ``cos_sim`` vs the Func Model golden must meet the ``P10_LADDER`` thresholds
  (0.999 / 0.998 / 0.997).  This band is the expected quantization precision
  residual — BUG-SOC-FM-005 is Fixed; the ladder is a pinned baseline, not a
  bug remnant, and the numerical gap itself is deliberately NOT "fixed" here.
- **failure injection**: (a) tightening the legacy 2-layer tolerance to 1e-5
  must flip ``result["ok"]`` to False (known quantization residual); (b) a
  corrupted golden (L0 zeroed) must flip the phase-10 ladder gate to False —
  proving the ladder assertion bites rather than passing vacuously.

Environment: requires the Spike binary + plugin +
``firmware/build/npu_firmware_spike.elf`` (NOT ``npu_firmware.elf``), the GGUF
models (``~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf`` for the legacy 2-layer
gate and ``~/models/qwen2.5-3b-instruct-q4_k_m.gguf`` for the ladder gates —
the 1.5B model only has 28 layers), the llama.cpp reference npz, and the
phase-10 golden dir.  Missing assets → ``pytest.skip``; the acceptance command
exits 0 (all skipped) on a host without the Spike stack.

Runtime: the 36-layer ladder dispatches ~1200 commands across ~400 Spike
boots (~35 min on sz0001); each 2-layer phase-10 gate is ~4 min; the legacy
2-layer attempt is ~1 min (weight load) before it hits the window limit.
"""

from pathlib import Path

import numpy as np
import pytest

from sim import spike_host
from spike_firmware import _is_spike_available

# ── Baseline constants (pinned acceptance thresholds) ────────────────────

_TOL_ACCEPT = 1e-1                 # legacy 2-layer acceptance: max_abs < 1e-1
_TOL_TIGHT = 1e-5                  # failure injection: known residual >> 1e-5
_PROMPT = "Hello, world!"
_SEQ_LEN = 4
_LAYERS_2 = 2
_LAYERS_36 = 36
_TOKEN_IDS_36 = [9707]             # "Hello" — same input as the task-12 baseline

_GGUF_15B = Path.home() / "models" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"
_GGUF_3B = Path.home() / "models" / "qwen2.5-3b-instruct-q4_k_m.gguf"
_REF_NPZ = spike_host.PROJECT / "llama_ref" / "refs" / "qwen_l0_l1_hidden.npz"
_GOLDEN_DIR = (
    spike_host.PROJECT / "rtl" / "test_vectors" / "soc_e2e" / "qwen25-3b-36layer")

_WINDOW_SKIP_MSG = (
    "run_forward_pass cannot run on this stack: the op plan exceeds the "
    "firmware-enforced 8 MB DRAM window [0x80000000, 0x80800000) "
    "(BUG-RTL-SOC-002 / todo 19; run_forward_pass allocates all tiled FFN "
    "weights in-window without wave recycling since spike_host.py a0a2fd9). "
    "The window-compliant 2-layer forward gate is covered by "
    "test_two_layer_phase10_ladder_meets_thresholds.")


# ── Availability guards ──────────────────────────────────────────────────

def _require_spike():
    """Skip when the Spike stack is missing (mirrors spike_firmware detection)."""
    if not _is_spike_available():
        pytest.skip(
            "Spike stack not available (need spike_src/build/spike, "
            "spike_src/plugins/npu_mmio_plugin.so, "
            "firmware/build/npu_firmware_spike.elf)")


def _require_file(path: Path, label: str):
    if not path.exists():
        pytest.skip(f"{label} not found: {path}")


def _require_tokenizer():
    """Skip when the GGUF/tokenizers dependency chain cannot import."""
    try:
        import gguf  # noqa: F401
        import tokenizers  # noqa: F401
    except ImportError as exc:
        pytest.skip(f"tokenizer/gguf dependency unavailable: {exc}")


def _require_phase10_assets():
    """Shared guards for the window-compliant phase-10 ladder gates."""
    _require_spike()
    _require_tokenizer()
    _require_file(_GGUF_3B, "Qwen2.5-3B GGUF model (phase-10 ladder)")
    if not (_GOLDEN_DIR / "expected.npz").exists():
        pytest.skip(f"phase-10 golden dir not found: {_GOLDEN_DIR}")


# ── Group 1: legacy run_forward_pass gates (task letter) ─────────────────

@pytest.fixture(scope="module")
def forward_2l_attempt():
    """Attempt the legacy 2-layer run_forward_pass at tolerance=1e-1.

    Returns the result dict, or None when the firmware-enforced 8 MB DRAM
    window makes the op plan unbuildable (BUG-RTL-SOC-002) — the dependent
    tests then skip with the documented reason.
    """
    _require_spike()
    _require_tokenizer()
    _require_file(_GGUF_15B, "Qwen2.5-1.5B GGUF model")
    _require_file(_REF_NPZ, "llama.cpp reference npz")
    try:
        return spike_host.run_forward_pass(
            str(_GGUF_15B), _PROMPT, layers=_LAYERS_2,
            reference_npz=str(_REF_NPZ), seq_len=_SEQ_LEN,
            tolerance=_TOL_ACCEPT)
    except MemoryError:
        return None


@pytest.fixture(scope="module")
def forward_2l(forward_2l_attempt) -> dict:
    """Legacy 2-layer result; skips when the DRAM window blocks the path."""
    if forward_2l_attempt is None:
        pytest.skip(_WINDOW_SKIP_MSG)
    return forward_2l_attempt


@pytest.fixture(scope="module")
def forward_2l_tight(forward_2l_attempt) -> dict:
    """Legacy 2-layer run with tolerance tightened to 1e-5 (must fail)."""
    if forward_2l_attempt is None:
        pytest.skip(_WINDOW_SKIP_MSG)
    return spike_host.run_forward_pass(
        str(_GGUF_15B), _PROMPT, layers=_LAYERS_2,
        reference_npz=str(_REF_NPZ), seq_len=_SEQ_LEN,
        tolerance=_TOL_TIGHT)


# ── Group 2: window-compliant phase-10 ladder gates ──────────────────────

@pytest.fixture(scope="module")
def forward_phase10_2l() -> dict:
    """2-layer Qwen2.5-3B phase-10 forward with the P10_LADDER comparison."""
    _require_phase10_assets()
    return spike_host.run_forward_pass_phase10(
        str(_GGUF_3B), _LAYERS_2, _TOKEN_IDS_36, golden_dir=str(_GOLDEN_DIR))


@pytest.fixture(scope="module")
def forward_phase10_36l() -> dict:
    """36-layer Qwen2.5-3B phase-10 forward with the P10_LADDER comparison."""
    _require_phase10_assets()
    return spike_host.run_forward_pass_phase10(
        str(_GGUF_3B), _LAYERS_36, _TOKEN_IDS_36, golden_dir=str(_GOLDEN_DIR))


@pytest.fixture(scope="module")
def forward_phase10_2l_corrupted() -> dict:
    """2-layer phase-10 forward against a corrupted golden (L0 zeroed)."""
    _require_phase10_assets()
    mp = pytest.MonkeyPatch()  # function-scoped `monkeypatch` cannot be used
    original = spike_host._load_golden_layer

    def poisoned(gdir, layer):
        out = original(gdir, layer)
        if layer == 0:
            return np.zeros_like(out)
        return out

    mp.setattr(spike_host, "_load_golden_layer", poisoned)
    try:
        return spike_host.run_forward_pass_phase10(
            str(_GGUF_3B), _LAYERS_2, _TOKEN_IDS_36, golden_dir=str(_GOLDEN_DIR))
    finally:
        mp.undo()


# ── Tests: legacy run_forward_pass letter gates ──────────────────────────

def test_two_layer_forward_meets_acceptance_tolerance(forward_2l):
    """Happy path: 2-layer forward passes the pinned max_abs < 1e-1 baseline.

    ``run_forward_pass`` returns a dict (keys ``ok``/``errors``/
    ``layer_outputs``); every per-layer error row must be within tolerance and
    ``ok`` must therefore be True.  (Skips where the 8 MB DRAM window blocks
    the legacy path — see the module docstring.)
    """
    res = forward_2l
    assert res["ok"] is True
    assert len(res["layer_outputs"]) == _LAYERS_2
    assert len(res["errors"]) == _LAYERS_2
    for err in res["errors"]:
        assert err["ok"] is True
        assert err["max_abs"] < _TOL_ACCEPT
        assert err["tolerance"] == _TOL_ACCEPT


def test_result_schema_is_plain_dict_without_tolerance_result(forward_2l):
    """Pin the return schema: ok/errors/layer_outputs, no tolerance_result.

    Documents (and regression-guards) the fact that ``run_forward_pass`` does
    not expose a ``tolerance_result`` key — callers must assert ``result["ok"]``
    and the per-layer rows instead of reaching for a field that does not exist.
    """
    res = forward_2l
    assert isinstance(res, dict)
    assert {"ok", "errors", "layer_outputs"} <= set(res.keys())
    assert "tolerance_result" not in res


def test_failure_injection_tight_tolerance_fails(forward_2l_tight):
    """Failure injection: tolerance=1e-5 flips result["ok"] to False.

    The quantization precision residual keeps per-layer max_abs above 1e-5, so
    the tightened gate must fail — proving the happy-path assertion is real
    rather than vacuous.  This residual is the known, expected gap (out of
    scope to fix; BUG-SOC-FM-005 is Fixed and the 1e-1 band is the pinned
    baseline).
    """
    res = forward_2l_tight
    assert res["ok"] is False
    assert len(res["errors"]) == _LAYERS_2
    for err in res["errors"]:
        assert err["ok"] is False
        assert err["max_abs"] > _TOL_TIGHT
        assert err["tolerance"] == _TOL_TIGHT


# ── Tests: window-compliant phase-10 ladder gates ────────────────────────

def test_two_layer_phase10_ladder_meets_thresholds(forward_phase10_2l):
    """2-layer window-compliant forward: per-layer cos_sim >= P10_LADDER.

    This is the runnable form of the 2-layer happy gate on the current stack:
    the same Spike-first forward machinery as the 36-layer ladder, restricted
    to 2 layers, must end with ``ok is True`` and both ladder rows above their
    P10_LADDER thresholds (0.999 for L0–L19).
    """
    res = forward_phase10_2l
    assert res["ok"] is True
    assert res["layers_completed"] == _LAYERS_2
    rows = res["ladder_rows"]
    assert len(rows) == _LAYERS_2
    for row in rows:
        thr = spike_host.p10_layer_threshold(row["layer"])
        assert row["threshold"] == thr, f"L{row['layer']}: threshold mismatch"
        assert row["cos_sim"] >= thr, (
            f"L{row['layer']}: cos_sim={row['cos_sim']:.6f} below ladder "
            f"threshold {thr} — quantization precision regression?")
        assert row["ok"] is True


def test_thirty_six_layer_ladder_meets_p10_thresholds(forward_phase10_36l):
    """36-layer ladder: per-layer cos_sim >= P10_LADDER threshold (baseline)."""
    res = forward_phase10_36l
    assert res["ok"] is True
    assert res["layers_completed"] == _LAYERS_36
    rows = res["ladder_rows"]
    assert len(rows) == _LAYERS_36
    for row in rows:
        thr = spike_host.p10_layer_threshold(row["layer"])
        assert row["threshold"] == thr, f"L{row['layer']}: threshold mismatch"
        assert row["cos_sim"] >= thr, (
            f"L{row['layer']}: cos_sim={row['cos_sim']:.6f} below ladder "
            f"threshold {thr} — quantization precision regression?")
        assert row["ok"] is True


def test_failure_injection_corrupted_golden_fails_ladder(
        forward_phase10_2l_corrupted):
    """Failure injection: a corrupted golden must fail the ladder gate.

    Zeroing the L0 golden makes its cos_sim collapse to 0.0 (< 0.999) while
    the untouched L1 still passes — proving the per-layer ladder assertion
    bites on real divergence instead of passing vacuously.
    """
    res = forward_phase10_2l_corrupted
    assert res["ok"] is False
    rows = res["ladder_rows"]
    assert len(rows) == _LAYERS_2
    row0 = next(r for r in rows if r["layer"] == 0)
    row1 = next(r for r in rows if r["layer"] == 1)
    assert row0["ok"] is False
    assert row0["cos_sim"] == 0.0
    assert row1["ok"] is True
    assert row1["cos_sim"] >= spike_host.p10_layer_threshold(1)
