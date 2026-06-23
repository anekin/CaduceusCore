# Performance Dashboard — qwen2.5-3b

**Engine**: CaduceusCore TimingEngine
**Timestamp**: 2026-06-23T08:42:40.251355+00:00

## Summary

| Metric | Value |
|--------|-------|
| Tps | 31.23 |
| Ttft Ms | 200.91 |
| Tpot Us | 32023.04 |
| Prefill Ms | 168.89 |
| Decode Per Token Us | 32023.04 |
| Itl Us P50 | 32023.04 |
| Itl Us P90 | 32023.04 |
| Itl Us P99 | 32023.04 |
| Bandwidth Utilization Pct | 0.0 |
| Dma Overlap Ratio | 0.0 |
| Total Cycles | 32023040 |

## Per-Module Cycles

| Module | Cycles |
|--------|--------|
| mxu | 31927280 |
| sfu | 47712 |
| vector | 1680 |
| dma_weight | 0 |
| dma_effective | 0 |
| kv_cache | 46368 |

## Module Utilization

| Module | % |
|--------|---|
| mxu | 99.7 |
| sfu | 0.15 |
| vector | 0.01 |
| dma_weight | 0.0 |
| dma_effective | 0.0 |
| kv_cache | 0.14 |

## ITL Distribution (ASCII histogram)

```
   32023.0 -  32024.0 us: ######################################## (127)
   32024.0 -  32025.0 us: # (0)
   32025.0 -  32026.0 us: # (0)
   32026.0 -  32027.0 us: # (0)
   32027.0 -  32028.0 us: # (0)
   32028.0 -  32029.0 us: # (0)
   32029.0 -  32030.0 us: # (0)
   32030.0 -  32031.0 us: # (0)
   32031.0 -  32032.0 us: # (0)
   32032.0 -  32033.0 us: # (0)
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
