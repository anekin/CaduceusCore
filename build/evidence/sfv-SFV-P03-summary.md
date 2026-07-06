# SFU Perf Case: SFV-P03

- **Op**: rmsnorm
- **Dim**: 64
- **Pos**: 0
- **Command**: `/home/prj/zhengs/caduceuscore/CaduceusCore/scripts/run_sfu_perf_case.py --case SFV-P03 --op rmsnorm --dim 64 --dry-run`

## Dry-Run Formula Check

```
Case: SFV-P03
Op: rmsnorm, Dim: 64
Expected cycle formula: 2*N + 21
  = 2*64 + 21
  = 149
Tolerance: |delta| <= 5
[SFV-P03] op=rmsnorm,dim=64 expected=149 — PASS (dry-run, formula check only)
```

**Final verdict: PASS (dry-run, formula check only)**
