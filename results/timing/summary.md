# CaduceusCore Timing Summary — Model Zoo

## LLM Models

| Model | TTFT (ms) | TPS (tok/s) | TPOT (μs) | Prefill (ms) | Decode/Token (μs) | Total Cycles |
|---|---|---|---|---|---|---|
| gemma-4-12b | 287.33 | 8.44 | 118439.92 | 168.89 | 118439.92 | 118439920 |
| qwen2.5-1.5b | 184.37 | 64.59 | 15481.26 | 168.89 | 15481.26 | 15481256 |
| qwen2.5-3b | 200.91 | 31.23 | 32023.04 | 168.89 | 32023.04 | 32023040 |
| qwen2.5-7b | 245.61 | 13.03 | 76725.57 | 168.89 | 76725.57 | 76725572 |
| qwen3-8b | 239.98 | 14.07 | 71091.55 | 168.89 | 71091.55 | 71091552 |

## CV Models

| Model | FPS | Inference Latency (μs) | Total Cycles |
|---|---|---|---|
| mobilenetv3 | 1280.24 | 781.10 | 781103 |
| resnet18 | 1202.81 | 831.39 | 831387 |
| resnet50 | 537.94 | 1858.95 | 1858949 |
| vit-b16 | 139.74 | 7156.18 | 7156175 |
| yolov8n | 387.83 | 2578.45 | 2578453 |

> **Note:** TTFT is engine-only (prefill + first decode). Prefill is constant (103.23 ms) across all LLM models because it depends only on prompt length (128 tokens) and hardware config (1 core, 1 GHz), not model size.
