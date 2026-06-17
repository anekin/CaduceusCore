"""SFU 延迟模型 — Special Function Unit pipeline depth lookup"""

from typing import Any, Dict


class SFUModel:
    """Fixed-pipeline SFU: each operator has known pipeline depth.

    SFU processes at width elements per cycle (typically 128).
    Operators are NOT pipelined across each other — they share
    the same datapath and execute sequentially per layer.

    Reference: 中科院 2024 hardware-oriented softmax+layernorm unified architecture
    """

    def __init__(self, config: Dict[str, Any]):
        sfu = config["sfu"]
        self.width = int(sfu["width"])
        self.latency_map = {
            k: int(v) for k, v in sfu["pipeline_cycles"].items()
        }
        # Default if operator not in map
        self.default_latency = 4

    def estimate(self, op_type: str, num_elements: int) -> int:
        """Return cycles for processing num_elements through op_type.

        op_type: 'softmax', 'layernorm', 'gelu', 'relu', 'silu', 'rope'
        num_elements: typically hidden_size (2560 for 3B model)
        """
        latency_per_batch = self.latency_map.get(
            op_type.lower(), self.default_latency
        )
        # ceil(num_elements / width) batches, each taking latency_per_batch cycles
        batches = (num_elements + self.width - 1) // self.width
        return batches * latency_per_batch

    def estimate_all_layer(self, hidden_size: int, intermediate_size: int,
                           has_attention: bool = True, has_rope: bool = True) -> int:
        """Estimate all SFU operations for one transformer layer.

        Operations per layer:
        - Attention: QK^T scaling + softmax + output projection (optional bias LN)
        - Post-attention: residual add + layernorm
        - FFN: GELU + residual add + layernorm (or SiLU for modern Llama-style)
        """
        total = 0

        if has_attention:
            # softmax over attention scores (per head, per token)
            # Each head has seq_len × seq_len attention; decode: 1 × seq_len
            total += self.estimate("softmax", hidden_size)

        # Post-attention layernorm
        total += self.estimate("layernorm", hidden_size)

        # FFN activation (GELU or SiLU)
        total += self.estimate("gelu", intermediate_size)

        # Post-FFN layernorm
        total += self.estimate("layernorm", hidden_size)

        # RoPE (applied to Q and K before attention)
        if has_rope:
            total += self.estimate("rope", hidden_size * 2)  # Q + K

        return total
