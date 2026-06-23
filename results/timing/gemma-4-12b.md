# Performance Dashboard — gemma-4-12b

**Engine**: CaduceusCore TimingEngine
**Timestamp**: 2026-06-23T08:42:41.250531+00:00

## Summary

| Metric | Value |
|--------|-------|
| Tps | 8.44 |
| Ttft Ms | 287.33 |
| Tpot Us | 118439.92 |
| Prefill Ms | 168.89 |
| Decode Per Token Us | 118439.92 |
| Itl Us P50 | 118439.92 |
| Itl Us P90 | 118439.92 |
| Itl Us P99 | 118439.92 |
| Bandwidth Utilization Pct | 0.0 |
| Dma Overlap Ratio | 0.0 |
| Total Cycles | 118439920 |

## Per-Module Cycles

| Module | Cycles |
|--------|--------|
| mxu | 118303120 |
| sfu | 68160 |
| vector | 2400 |
| dma_weight | 0 |
| dma_effective | 0 |
| kv_cache | 66240 |

## Module Utilization

| Module | % |
|--------|---|
| mxu | 99.88 |
| sfu | 0.06 |
| vector | 0.0 |
| dma_weight | 0.0 |
| dma_effective | 0.0 |
| kv_cache | 0.06 |

## ITL Distribution (ASCII histogram)

```
  118439.9 - 118440.9 us: ######################################## (127)
  118440.9 - 118441.9 us: # (0)
  118441.9 - 118442.9 us: # (0)
  118442.9 - 118443.9 us: # (0)
  118443.9 - 118444.9 us: # (0)
  118444.9 - 118445.9 us: # (0)
  118445.9 - 118446.9 us: # (0)
  118446.9 - 118447.9 us: # (0)
  118447.9 - 118448.9 us: # (0)
  118448.9 - 118449.9 us: # (0)
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
