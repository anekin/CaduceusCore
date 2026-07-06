# Vector Perf Case: SFV-P08

- **Op**: add
- **Dim**: 128
- **Command**: `/home/prj/zhengs/caduceuscore/CaduceusCore/scripts/run_vector_perf_case.py --case SFV-P08 --op add --dim 128 --dry-run`

## Dry-Run Formula Check

```
Case: SFV-P08
Op: add, Dim: 128, Chunks: ceil(128/128) = 1
Expected cycle formula: ceil(N/128) * 4 + 2
  = ceil(128/128) * per_chunk_cycles + 2
  = 1 * 4 + 2
  = 6
Tolerance: |delta| <= 1
[SFV-P08] op=add,dim=128 expected=6 — PASS (dry-run, formula check only)
```

**Final verdict: PASS (dry-run, formula check only)**
