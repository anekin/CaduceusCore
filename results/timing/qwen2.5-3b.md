# Performance Dashboard — qwen2.5-3b

**Engine**: CaduceusCore TimingEngine
**Timestamp**: 2026-07-09T09:15:56.032785+00:00

## Summary

| Metric | Value |
|--------|-------|
| Tps | 21.59 |
| Ttft Ms | 196.09 |
| Tpot Us | 0.0 |
| Prefill Ms | 149.76 |
| Decode Per Token Us | 46326.69 |
| Itl Us P50 | 0.0 |
| Itl Us P90 | 0.0 |
| Itl Us P99 | 0.0 |
| Bandwidth Utilization Pct | 30.36 |
| Real Bw Utilization Pct | 30.36 |
| Dma Overlap Ratio | 0.7 |
| Total Cycles | 131869296 |

## Per-Module Cycles

| Module | Cycles |
|--------|--------|
| mxu | 43138332 |
| sfu | 700776 |
| vector | 8640 |
| dma_weight | 23563152 |
| dma_effective | 16467228 |
| kv_cache | 111888 |
| noc_latency | 47879280 |
| noc_contention | 0 |

## Module Utilization

| Module | % |
|--------|---|
| mxu | 32.71 |
| sfu | 0.53 |
| vector | 0.01 |
| dma_weight | 17.87 |
| dma_effective | 12.49 |
| kv_cache | 0.08 |
| noc_latency | 36.31 |
| noc_contention | 0.0 |

## NoC

| Metric | Value |
|--------|-------|
| Topology | crossbar |
| Ports | 4 |
| Latency (us) | 47879.28 |
| Contention (%) | 0.0 |

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
