"""SFU 延迟模型 — Special Function Unit (spec-aligned, architecture assumption).

SFU handles complex mathematical functions (softmax, layernorm, rmsnorm, gelu, silu, rope).
Pipeline: width=128 elements/cycle; each op has a fixed pipeline depth (cycles per batch).

All 6 SFU ops are aligned to the normative T1 spec pipeline depths:
  softmax=227, layernorm=210, rmsnorm=150, gelu=71, silu=72, rope=82

Unsupported ops and dim<=0 fail with typed errors.
No unknown-op defaults.
"""

from typing import Any, Dict, Tuple

# ── SFU spec pipeline depths (architecture assumption, NOT rtl_measurement) ─────
# Pipeline depths established at reference dim=64 (half the SFU width).
# Normalization ops (softmax, layernorm, rmsnorm) scale effective depth with
# element count when elements < 64: effective = ceil(depth * min(elements, 64) / 64).
# Element-wise ops (gelu, silu, rope) use constant depth per batch.
SFU_PIPELINE = {
    "softmax": 227,
    "layernorm": 210,
    "rmsnorm": 150,
    "gelu": 71,
    "silu": 72,
    "rope": 82,
}

SFU_WIDTH = 128  # elements per cycle
SFU_REF_DIM = 64  # reference dimension at which pipeline depths were established
SFU_NORM_OPS = frozenset({"softmax", "layernorm", "rmsnorm"})


class SFUUnsupportedOpError(ValueError):
    """SFU operation not supported."""


class SFUInvalidDimError(ValueError):
    """SFU dimension <= 0."""


class SFUModel:
    """SFU performance model: cycles = pipeline_depth * ceil(elements / sfu_width)."""

    def __init__(self, config: Dict[str, Any]):
        _ = config  # unused; spec depths are frozen, not config-dependent
        self.width = SFU_WIDTH
        self.latency_map = dict(SFU_PIPELINE)

    def estimate(self, op_type: str, num_elements: int) -> int:
        """Return cycles for SFU operation on num_elements.

        Raises:
            SFUUnsupportedOpError: op_type not in the 6 spec-aligned ops.
            SFUInvalidDimError: num_elements <= 0.
        """
        import math
        op = op_type.lower()
        if op not in self.latency_map:
            raise SFUUnsupportedOpError(
                f"SFU op '{op_type}' not supported. "
                f"Supported: {sorted(self.latency_map.keys())}"
            )
        if num_elements <= 0:
            raise SFUInvalidDimError(
                f"SFU num_elements must be > 0, got {num_elements}"
            )
        latency_ref = self.latency_map[op]
        batches = (num_elements + self.width - 1) // self.width
        if op in SFU_NORM_OPS and num_elements < SFU_REF_DIM:
            effective_depth = math.ceil(latency_ref * num_elements / SFU_REF_DIM)
        else:
            effective_depth = latency_ref
        return batches * effective_depth

    def estimate_softmax_decomposed(self, num_elements: int) -> Dict[str, int]:
        """Decomposed softmax: returns SFU-only portions (exp + div).

        Softmax(x) = exp(x - max) / sum(exp(x - max))
        SFU handles: exp, div (mapped to softmax pipeline depth).
        Vector handles: max_reduce, sub, sum_reduce.
        """
        batches = (num_elements + self.width - 1) // self.width
        sd = self.latency_map["softmax"]
        # Decompose: exp portion ~ sd*0.3, div portion ~ sd*0.7
        exp_cycles = max(1, (batches * sd * 30) // 100)
        div_cycles = batches * sd - exp_cycles
        return {"exp": exp_cycles, "div": div_cycles}

    def estimate_attention_sfu(self, hidden_size: int,
                                num_heads: int = 32) -> Dict[str, int]:
        """Estimate SFU portion of attention for one decode token."""
        head_dim = hidden_size // num_heads
        decomposed = self.estimate_softmax_decomposed(hidden_size)
        return {
            "attn_exp": decomposed["exp"],
            "attn_div": decomposed["div"],
        }

    def estimate_all_layer(self, hidden_size: int, intermediate_size: int,
                            has_attention: bool = True, has_rope: bool = True) -> Tuple[int, Dict[str, int]]:
        """Estimate ALL SFU operations for one transformer layer."""
        breakdown = {}
        total = 0

        if has_attention:
            dec = self.estimate_softmax_decomposed(hidden_size)
            breakdown["softmax_exp"] = dec["exp"]
            breakdown["softmax_div"] = dec["div"]
            total += dec["exp"] + dec["div"]

        # Post-attention layernorm
        ln = self.estimate("layernorm", hidden_size)
        breakdown["ln1"] = ln
        total += ln

        # FFN gelu/silu
        gelu = self.estimate("gelu", intermediate_size)
        breakdown["gelu"] = gelu
        total += gelu

        # Post-FFN layernorm/rmsnorm
        ln2 = self.estimate("layernorm", hidden_size)
        breakdown["ln2"] = ln2
        total += ln2

        # RoPE
        if has_rope:
            rope = self.estimate("rope", hidden_size * 2)
            breakdown["rope"] = rope
            total += rope

        return total, breakdown
