# Performance Dashboard — qwen3-8b

**Engine**: CaduceusCore TimingEngine
**Timestamp**: 2026-06-23T08:42:40.847059+00:00

## Summary

| Metric | Value |
|--------|-------|
| Tps | 14.07 |
| Ttft Ms | 239.98 |
| Tpot Us | 71091.55 |
| Prefill Ms | 168.89 |
| Decode Per Token Us | 71091.55 |
| Itl Us P50 | 71091.55 |
| Itl Us P90 | 71091.55 |
| Itl Us P99 | 71091.55 |
| Bandwidth Utilization Pct | 0.0 |
| Dma Overlap Ratio | 0.0 |
| Total Cycles | 71091552 |

## Per-Module Cycles

| Module | Cycles |
|--------|--------|
| mxu | 70982112 |
| sfu | 54528 |
| vector | 1920 |
| dma_weight | 0 |
| dma_effective | 0 |
| kv_cache | 52992 |

## Module Utilization

| Module | % |
|--------|---|
| mxu | 99.85 |
| sfu | 0.08 |
| vector | 0.0 |
| dma_weight | 0.0 |
| dma_effective | 0.0 |
| kv_cache | 0.07 |

## ITL Distribution (ASCII histogram)

```
   71091.6 -  71092.6 us: ######################################## (127)
   71092.6 -  71093.6 us: # (0)
   71093.6 -  71094.6 us: # (0)
   71094.6 -  71095.6 us: # (0)
   71095.6 -  71096.6 us: # (0)
   71096.6 -  71097.6 us: # (0)
   71097.6 -  71098.6 us: # (0)
   71098.6 -  71099.6 us: # (0)
   71099.6 -  71100.6 us: # (0)
   71100.6 -  71101.6 us: # (0)
```

## Configuration

```json
{
  "cores": 1,
  "optimizations": {
    "weight_cache": true,
    "dma_bw_multiplier": 1.0
  },
  "mxu": {
    "type": "block",
    "array_height": 64,
    "array_width": 64,
    "frequency_mhz": 1000,
    "weight_precision_bits": 4,
    "activation_precision_bits": 8,
    "accumulate_precision_bits": 32,
    "dataflow": "weight_stationary",
    "double_buffer": true,
    "ops_per_mac": 2
  },
  "sram": {
    "l1_per_core_kb": 512,
    "l2_shared_kb": 2048,
    "banks": 16,
    "read_width_bits": 256,
    "write_width_bits": 256
  },
  "sfu": {
    "width": 128,
    "pipeline_cycles": {
      "softmax": 8,
      "exp": 12,
      "div": 16,
      "sqrt": 20,
      "log": 18,
      "tanh": 14,
      "layernorm": 6,
      "gelu": 4,
      "relu": 1,
      "silu": 4,
      "rope": 12,
      "maxpool": 3,
      "avgpool": 3
    }
  },
  "vector": {
    "width": 128,
    "ops": {
      "add": 1,
      "mul": 1,
      "scale": 1,
      "bias": 1,
      "relu": 1,
      "mask": 1
    }
  },
  "kv_cache": {
    "sram_kb": 256,
    "dram_region_mb": 96,
    "precision_bits": 8
  },
  "dma": {
    "channels": 2,
    "burst_size_bytes": 256,
    "descriptor_overhead_cycles": 5,
    "max_pending_descriptors": 16
  },
  "memory": {
    "type": "LPDDR5-6400",
    "bandwidth_gbps": 51.2,
    "bandwidth_bytes_per_cycle": 51.2,
    "dram_efficiency": 0.85,
    "tRC_cycles": 48,
    "tRAS_cycles": 42,
    "refresh_overhead_percent": 3.0
  },
  "interconnect": {
    "type": "crossbar",
    "port_bandwidth_gbps": 500
  },
  "riscv": {
    "isa": "RV64IMAFD",
    "pipeline_stages": 4,
    "fetch_cycles": 4,
    "decode_cycles": 1,
    "dispatch_cycles": 2
  }
}
```

---
*TTFT (Time-To-First-Token) is engine-only latency (prefill + first decode), excluding queue/network overhead.*
