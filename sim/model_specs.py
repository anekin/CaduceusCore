from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    name: str
    qkv_dim: int       # num_heads * head_dim
    hidden: int
    intermediate: int
    layers: int
    num_heads: int
    kv_heads: int
    head_dim: int = 128

    def __getitem__(self, idx: int):
        """Preserve legacy tuple access: (qkv, hidden, intermediate, layers, num_heads, kv_heads)."""
        return (self.qkv_dim, self.hidden, self.intermediate,
                self.layers, self.num_heads, self.kv_heads)[idx]

    def __len__(self) -> int:
        return 6


MODELS = {
    "qwen2.5-1.5b": ModelSpec("qwen2.5-1.5b", 1536, 1536, 8960, 28, 12, 2, head_dim=128),
    "qwen2.5-3b":   ModelSpec("qwen2.5-3b",   4096, 2560, 9728, 28, 32, 2, head_dim=128),
    "qwen2.5-7b":   ModelSpec("qwen2.5-7b",   3584, 3584, 18944, 28, 28, 4, head_dim=128),
    "qwen3-8b":     ModelSpec("qwen3-8b",     4096, 4096, 12288, 32, 32, 4, head_dim=128),
    "gemma-4-12b":  ModelSpec("gemma-4-12b",  4096, 4096, 16384, 40, 16, 8, head_dim=256),
}


def get_spec(alias: str) -> ModelSpec:
    if alias not in MODELS:
        raise ValueError(f"Unknown model alias: {alias}. Known: {list(MODELS.keys())}")
    return MODELS[alias]


def all_aliases() -> list:
    return list(MODELS.keys())
