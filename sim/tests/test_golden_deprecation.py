"""RED test: importing from models.golden should trigger DeprecationWarning.

This test asserts that importing GoldenMXU from the legacy models.golden
module emits a DeprecationWarning, since models/golden.py is a stale
duplicate of golden_executor.py.

This test is expected to FAIL until the deprecation warning is added to
models/golden.py (GREEN phase).
"""

import pytest


def test_models_golden_deprecated():
    """Importing from models.golden must emit DeprecationWarning."""
    with pytest.warns(DeprecationWarning, match="golden_executor"):
        from models.golden import GoldenMXU
        # Verify the import actually works — we still get the class
        assert GoldenMXU is not None
