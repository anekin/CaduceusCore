# FSA Reference Architecture

**Source**: [VCA-EPFL/FSA](https://github.com/VCA-EPFL/FSA)  
**Paper**: [Fusing FlashAttention within a Single Systolic Array](http://arxiv.org/abs/2507.11331)  
**Date integrated**: 2026-06-27

## What FSA Does

FSA executes complete FlashAttention on a single systolic array — without requiring external vector units. Key innovations:

- **Inline softmax**: rowmax, exp, rowsum all computed inside the systolic array dataflow
- **CMP columns**: Column-level comparators for online max reduction
- **PE Split unit**: Reuses MAC hardware for exp piecewise linear interpolation
- **SystolicAttention scheduling**: Element-wise operations overlapped to minimize latency
- **Performance**: 1.77× AWS NeuronCore-v2, 4.83× TPUv5e FLOPs utilization

## Why in Our Repo

FSA represents an alternative architecture point in the design space:
- **CaduceusCore approach**: Separate MXU + SFU + Vector units, data moves between them
- **FSA approach**: Single systolic array with inline attention operations

We use FSA's Python golden reference model as an architecture option in our Arc Model to evaluate:
1. Area tradeoff (FSA +12% vs dedicated SFU)
2. Attention latency (inline vs MXU→SFU→Vector pipeline)
3. MAC utilization efficiency (shared vs dedicated compute)
4. Model flexibility (attention-only vs general-purpose SFU)

## Files

| Path | Description |
|------|-------------|
| `fsa/` | FSA Python kernel DSL and execution engine |
| `fa_ref.py` | Torch golden reference for FlashAttention |
| `main.py` | FlashAttention end-to-end example |
| `README.upstream.md` | Original FSA README |

## License

Original FSA code from EPFL VCA lab. See upstream repository for license terms.
