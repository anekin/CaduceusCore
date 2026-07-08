# Performance Dashboard — qwen2.5-3b

**Engine**: CaduceusCore TimingEngine
**Timestamp**: 2026-07-08T03:24:58.293453+00:00

## Summary

| Metric | Value |
|--------|-------|
| Tps | 8.41 |
| Ttft Ms | 335.83 |
| Tpot Us | 118905.11 |
| Prefill Ms | 216.92 |
| Decode Per Token Us | 118905.11 |
| Itl Us P50 | 118905.11 |
| Itl Us P90 | 118905.11 |
| Itl Us P99 | 118905.11 |
| Bandwidth Utilization Pct | 30.7 |
| Dma Overlap Ratio | 1.55 |
| Total Cycles | 118905108 |

## Per-Module Cycles

| Module | Cycles |
|--------|--------|
| mxu | 38948252 |
| sfu | 618688 |
| vector | 8400 |
| dma_weight | 14290332 |
| dma_effective | 22216348 |
| kv_cache | 46368 |
| noc_latency | 42776720 |
| noc_contention | 0 |

## Module Utilization

| Module | % |
|--------|---|
| mxu | 32.76 |
| sfu | 0.52 |
| vector | 0.01 |
| dma_weight | 12.02 |
| dma_effective | 18.68 |
| kv_cache | 0.04 |
| noc_latency | 35.98 |
| noc_contention | 0.0 |

## NoC

| Metric | Value |
|--------|-------|
| Topology | crossbar |
| Ports | 4 |
| Latency (us) | 42776.72 |
| Contention (%) | 0.0 |

## ITL Distribution (ASCII histogram)

```
  118905.1 - 118906.1 us: ######################################## (127)
  118906.1 - 118907.1 us: # (0)
  118907.1 - 118908.1 us: # (0)
  118908.1 - 118909.1 us: # (0)
  118909.1 - 118910.1 us: # (0)
  118910.1 - 118911.1 us: # (0)
  118911.1 - 118912.1 us: # (0)
  118912.1 - 118913.1 us: # (0)
  118913.1 - 118914.1 us: # (0)
  118914.1 - 118915.1 us: # (0)
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
      "softmax": 227,
      "exp": 66,
      "div": 161,
      "sqrt": 20,
      "log": 18,
      "tanh": 14,
      "layernorm": 210,
      "rmsnorm": 150,
      "gelu": 71,
      "relu": 1,
      "silu": 72,
      "rope": 82,
      "maxpool": 71,
      "avgpool": 71
    }
  },
  "vector": {
    "width": 128,
    "ops": {
      "add": 5,
      "mul": 5,
      "scale": 5,
      "bias": 5,
      "relu": 5,
      "mask": 5,
      "max": 12,
      "sum": 12,
      "reduce": 12,
      "conv_f16_i32": 260,
      "resid": 5
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
    "ll_prefetch_en": true,
    "arbitration": "round_robin"
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
