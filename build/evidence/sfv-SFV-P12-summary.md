# Vector Perf Case: SFV-P12

- **Op**: conv
- **Dim**: 128
- **Command**: `/home/prj/zhengs/caduceuscore/CaduceusCore/scripts/run_vector_perf_case.py --case SFV-P12 --op conv --dim 128 --dry-run`

## Dry-Run Formula Check

```
Case: SFV-P12
Op: conv, Dim: 128, Chunks: ceil(128/128) = 1
Expected cycle formula: ceil(N/128) * 132 + 2
  = ceil(128/128) * per_chunk_cycles + 2
  = 1 * 132 + 2
  = 134
Tolerance: |delta| <= 1
[SFV-P12] op=conv,dim=128 expected=134 — PASS (dry-run, formula check only)
```

**Final verdict: PASS (dry-run, formula check only)**
