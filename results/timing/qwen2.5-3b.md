# Performance Dashboard — qwen2.5-3b

**Engine**: CaduceusCore TimingEngine
**Timestamp**: 2026-06-23T12:58:20.041807+00:00

## Summary

| Metric | Value |
|--------|-------|
| Tps | 9.55 |
| Ttft Ms | 630.51 |
| Tpot Us | 104720.0 |
| Prefill Ms | 525.79 |
| Decode Per Token Us | 104720.0 |
| Itl Us P50 | 104720.0 |
| Itl Us P90 | 104720.0 |
| Itl Us P99 | 104720.0 |
| Bandwidth Utilization Pct | 27.96 |
| Dma Overlap Ratio | 0.1 |
| Total Cycles | 104720000 |

## Per-Module Cycles

| Module | Cycles |
|--------|--------|
| mxu | 31927280 |
| sfu | 47712 |
| vector | 1680 |
| dma_weight | 26622960 |
| dma_effective | 2652160 |
| kv_cache | 46368 |
| noc_latency | 43421840 |
| noc_contention | 0 |

## Module Utilization

| Module | % |
|--------|---|
| mxu | 30.49 |
| sfu | 0.05 |
| vector | 0.0 |
| dma_weight | 25.42 |
| dma_effective | 2.53 |
| kv_cache | 0.04 |
| noc_latency | 41.46 |
| noc_contention | 0.0 |

## NoC

| Metric | Value |
|--------|-------|
| Topology | crossbar |
| Ports | 4 |
| Latency (us) | 43421.84 |
| Contention (%) | 0.0 |

## ITL Distribution (ASCII histogram)

```
  104720.0 - 104721.0 us: ######################################## (127)
  104721.0 - 104722.0 us: # (0)
  104722.0 - 104723.0 us: # (0)
  104723.0 - 104724.0 us: # (0)
  104724.0 - 104725.0 us: # (0)
  104725.0 - 104726.0 us: # (0)
  104726.0 - 104727.0 us: # (0)
  104727.0 - 104728.0 us: # (0)
  104728.0 - 104729.0 us: # (0)
  104729.0 - 104730.0 us: # (0)
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
    "max_pending_descriptors": 16,
    "num_channels": 2,
    "per_channel_fifo_depth": 64,
    "max_burst_length": 8,
    "multi_block_mode": "linked_list",
    "ll_prefetch_en": true
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
    "ports": 4,
    "bandwidth_gbps": 500,
    "hop_latency_cycles": 3,
    "flit_width_bits": 256,
    "vcs": 2,
    "buffer_depth": 4,
    "arbitration": "round_robin",
    "routing": "destination_tag",
    "pipeline_stages": 3,
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
