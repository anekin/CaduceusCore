"""Vector 单元延迟模型 — element-wise SIMD operations (spec-aligned, architecture assumption).

Vector Unit handles simple element-wise operations (add, mul, max, sum, conv, resid).
SIMD width = 128 elements/cycle; each op has a fixed latency per batch.

All 6 Vector ops are aligned to the normative T1 spec op latencies:
  add=5, mul=5, max=12 (reduce_tree), sum=12 (reduce_tree), conv=260 (type_convert), resid=5

Unsupported ops and dim<=0 fail with typed errors.
No unknown-op defaults.
"""

from typing import Any, Dict

# ── Vector spec op latencies (architecture assumption, NOT rtl_measurement) ─────
VECTOR_OPS = {
    "add": 5,
    "mul": 5,
    "max": 12,
    "sum": 12,
    "conv": 260,
    "resid": 5,
}

VECTOR_WIDTH = 128  # SIMD elements/cycle


class VectorUnsupportedOpError(ValueError):
    """Vector operation not supported."""


class VectorInvalidDimError(ValueError):
    """Vector dimension <= 0."""


class VectorModel:
    """Vector performance model: cycles = op_latency * ceil(dim / vector_width)."""

    def __init__(self, config: Dict[str, Any]):
        _ = config  # unused; spec latencies are frozen, not config-dependent
        self.width = VECTOR_WIDTH
        self.op_latency = dict(VECTOR_OPS)

    def estimate(self, op_type: str, num_elements: int) -> int:
        """Return cycles for processing num_elements through op_type.

        Raises:
            VectorUnsupportedOpError: op_type not in the 6 spec-aligned ops.
            VectorInvalidDimError: num_elements <= 0.
        """
        op = op_type.lower()
        if op not in self.op_latency:
            raise VectorUnsupportedOpError(
                f"Vector op '{op_type}' not supported. "
                f"Supported: {sorted(self.op_latency.keys())}"
            )
        if num_elements <= 0:
            raise VectorInvalidDimError(
                f"Vector num_elements must be > 0, got {num_elements}"
            )
        latency_per_batch = self.op_latency[op]
        batches = (num_elements + self.width - 1) // self.width
        return batches * latency_per_batch

    def estimate_softmax_vector_parts(self, num_elements: int) -> Dict[str, int]:
        """Estimate Vector-only portions of softmax (max_reduce, sub, sum_reduce).

        Softmax = max_reduce → sub → exp(SFU) → sum_reduce → div(SFU)
        Vector handles: max_reduce, sub, sum_reduce.
        """
        batches = (num_elements + self.width - 1) // self.width
        return {
            "max_reduce": batches * self.op_latency["max"],
            "scale_sub": batches * self.op_latency["mul"],
            "sum_reduce": batches * self.op_latency["sum"],
        }

    def estimate_residual_add(self, num_elements: int) -> int:
        """Residual connection: x = x + attn_out (or x = x + ffn_out)."""
        return self.estimate("resid", num_elements)
