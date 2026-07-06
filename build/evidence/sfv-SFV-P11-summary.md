# Vector Perf Case: SFV-P11

- **Op**: sum
- **Dim**: 128
- **Command**: `/home/prj/zhengs/caduceuscore/CaduceusCore/scripts/run_vector_perf_case.py --case SFV-P11 --op sum --dim 128 --dry-run`

## Dry-Run Formula Check

```
Case: SFV-P11
Op: sum, Dim: 128, Chunks: ceil(128/128) = 1
Expected cycle formula: ceil(N/128) * 10 + 2
  = ceil(128/128) * per_chunk_cycles + 2
  = 1 * 10 + 2
  = 12
Tolerance: |delta| <= 1
[SFV-P11] op=sum,dim=128 expected=12 — PASS (dry-run, formula check only)
```

**Final verdict: PASS (dry-run, formula check only)**
