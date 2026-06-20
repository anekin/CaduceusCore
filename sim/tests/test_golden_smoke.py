"""Parameterized MXU smoke tests from golden_executor.py configs.

Each test verifies deterministic INT32 matmul output for a given (M, K, N)
configuration — matching the CLI `golden_executor.py smoke` behavior.
"""

import numpy as np
import pytest
from golden_executor import GoldenMXU, generate_random_test, generate_smoke_tests

# Pre-generate all smoke test vectors once at import time
_smoke_tests = generate_smoke_tests()

# Build parameter list: (test_vector, desc)
_smoke_params = [(tv, tv.name) for tv in _smoke_tests]


@pytest.mark.parametrize("test_vector,desc", _smoke_params,
                         ids=[p[1] for p in _smoke_params])
def test_smoke_determinism(test_vector, desc):
    """Golden MXU matmul must be deterministic — same inputs → same outputs."""
    mxu = GoldenMXU()
    golden2 = mxu.matmul_int32(
        test_vector.activation,
        test_vector.weight_packed,
        test_vector.M, test_vector.K, test_vector.N,
    )
    assert np.array_equal(test_vector.golden_int32, golden2), \
        f"{desc}: golden output differs on recompute"


@pytest.mark.parametrize("test_vector,desc", _smoke_params,
                         ids=[p[1] for p in _smoke_params])
def test_smoke_hash_match(test_vector, desc):
    """Pre-computed hash must match recomputed hash."""
    mxu = GoldenMXU()
    golden2 = mxu.matmul_int32(
        test_vector.activation,
        test_vector.weight_packed,
        test_vector.M, test_vector.K, test_vector.N,
    )
    h2 = GoldenMXU.hash_output(golden2)
    assert test_vector.golden_hash == h2, \
        f"{desc}: hash mismatch {test_vector.golden_hash} vs {h2}"


@pytest.mark.parametrize("test_vector,desc", _smoke_params,
                         ids=[p[1] for p in _smoke_params])
def test_smoke_no_overflow(test_vector, desc):
    """All INT32 output values must be within valid INT32 range."""
    v = test_vector.golden_int32
    assert np.all(v >= -2**31), f"{desc}: underflow detected"
    assert np.all(v <= 2**31 - 1), f"{desc}: overflow detected"


@pytest.mark.parametrize("test_vector,desc", _smoke_params,
                         ids=[p[1] for p in _smoke_params])
def test_smoke_output_shape(test_vector, desc):
    """Output shape must be (M, N)."""
    assert test_vector.golden_int32.shape == (test_vector.M, test_vector.N), \
        f"{desc}: expected shape ({test_vector.M},{test_vector.N}), got {test_vector.golden_int32.shape}"


@pytest.mark.parametrize("test_vector,desc", _smoke_params,
                         ids=[p[1] for p in _smoke_params])
def test_smoke_input_shape(test_vector, desc):
    """Test vector input arrays must have consistent sizes."""
    expected_w_bytes = (test_vector.K * test_vector.N + 1) // 2
    assert len(test_vector.weight_packed) == expected_w_bytes, \
        f"{desc}: weight_packed size mismatch"
    assert test_vector.activation.size == test_vector.M * test_vector.K, \
        f"{desc}: activation size mismatch"


@pytest.mark.parametrize("test_vector,desc", _smoke_params,
                         ids=[p[1] for p in _smoke_params])
def test_smoke_validate_method(test_vector, desc):
    """TestVector.validate() must return True for valid vectors."""
    assert test_vector.validate(), \
        f"{desc}: validate() returned False — corrupt test vector"
