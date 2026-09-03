"""A2-1 narrow-N attack matrix for GoldenMXU — bug-012 FM audit.

112 (M,K,N) combinations: N∈{2,10,12,20,33,40,64} × M∈{1,4,32,65} ×
K∈{1,64,128,129}. GoldenMXU packed-INT4×INT8→INT32 output is compared
bit-exact, per element, against an independent numpy oracle computed
directly in this file.

Oracle independence (config/func_model_perf_oracle_v1.json:13 anti-pattern):
this file pulls in ONLY numpy, pytest, and golden_executor (the system under
test). It must NOT pull in sim.models / sim.engine / sim.timing / sim.npu_sim.

Attack surface rationale: narrow/non-power-of-two N (the stride bug family
behind BUG-012), N=33→1 n-tile, K=129→3 k-tiles, M=65→2 m-tiles, K=1
(zero-padded activation path).

Note: GoldenMXU.matmul_int32 unpacks packed weights internally
(golden_executor.py:103); the docstring at :96 ("pre-unpacked") is stale.
Weights MUST be passed packed via GoldenMXU.pack_int4.
"""

import numpy as np
import pytest

from golden_executor import GoldenMXU

# 7 × 4 × 4 = 112 combinations, all run explicitly (no silent skip)
NS = [2, 10, 12, 20, 33, 40, 64]
MS = [1, 4, 32, 65]
KS = [1, 64, 128, 129]

COMBOS = [(m, k, n) for n in NS for m in MS for k in KS]


def _numpy_oracle(act: np.ndarray, wgt: np.ndarray) -> np.ndarray:
    """Independent direct matmul: INT8 × INT4 → INT32, no packing involved."""
    return np.dot(act.astype(np.int32), wgt.astype(np.int32))


def _fmt_first_10(arr: np.ndarray) -> str:
    return "[" + ", ".join(str(int(v)) for v in arr.flatten()[:10]) + "]"


@pytest.mark.parametrize("m,k,n", COMBOS, ids=[f"M{m}_K{k}_N{n}" for m, k, n in COMBOS])
def test_narrow_n_bit_exact(m: int, k: int, n: int) -> None:
    """GoldenMXU output == independent numpy oracle, bit-exact per element."""
    rng = np.random.default_rng(42)
    wgt = rng.integers(-8, 8, (k, n))       # INT4 full range incl. +7
    act = rng.integers(-128, 128, (m, k))   # INT8 full range incl. +127

    wgt_packed = GoldenMXU.pack_int4(wgt)
    actual = GoldenMXU().matmul_int32(act, wgt_packed, m, k, n)
    expected = _numpy_oracle(act, wgt)

    assert actual.shape == (m, n), f"expected ({m},{n}), got {actual.shape}"
    assert actual.dtype == np.int32, f"expected int32, got {actual.dtype}"

    if not np.array_equal(actual, expected):
        mismatches = int(np.count_nonzero(actual != expected))
        first_bad = int(np.flatnonzero(actual.flatten() != expected.flatten())[0])
        pytest.fail(
            f"FM-HUNT-NARROW-N first-fail: M={m} K={k} N={n} "
            f"mismatches={mismatches} first_bad_idx={first_bad}\n"
            f"  actual   {_fmt_first_10(actual)}\n"
            f"  expected {_fmt_first_10(expected)}"
        )


def test_narrow_n_matrix_explicit_112() -> None:
    """Anti-silent-skip: the attack matrix must be exactly 112 combos."""
    assert len(COMBOS) == 112, f"expected 112 combos, got {len(COMBOS)}"
    assert len(set(COMBOS)) == 112, "combos must be unique"
    # Boundary combos that exercise the targeted edges
    for combo in [(65, 129, 33), (1, 1, 2), (65, 1, 64), (4, 129, 10), (32, 128, 12)]:
        assert combo in COMBOS, f"missing boundary combo {combo}"
