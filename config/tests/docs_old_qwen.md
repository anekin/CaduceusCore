# Bad Doc Fixture — Stale Qwen2.5-3B Dimensions

This fixture intentionally contains stale Qwen parameters that the doc checker
must reject.

## Model Dimensions

The Qwen2.5-3B model used for performance analysis has the following canonical
dimensions:

| Parameter | Value |
|-----------|-------|
| hidden_size | 2560 |
| intermediate_size | 9728 |
| num_hidden_layers | 28 |
| num_attention_heads | 32 |
| num_key_value_heads | 16 |
| head_dim | 128 |
| kv_dim | 2048 |

These values are wrong: the corrected Qwen2.5-3B parameters are hidden=2048,
intermediate=11008, layers=36, heads=16, kv_heads=2, kv_dim=256.
