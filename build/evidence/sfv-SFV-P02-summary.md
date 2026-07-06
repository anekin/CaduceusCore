# SFU Perf Case: SFV-P02

- **Op**: layernorm
- **Dim**: 64
- **Pos**: 0
- **Command**: `/home/prj/zhengs/caduceuscore/CaduceusCore/scripts/run_sfu_perf_case.py --case SFV-P02 --op layernorm --dim 64 --dry-run`

## Dry-Run Formula Check

```
Case: SFV-P02
Op: layernorm, Dim: 64
Expected cycle formula: 3*N + 17
  = 3*64 + 17
  = 209
Tolerance: |delta| <= 5
[SFV-P02] op=layernorm,dim=64 expected=209 — PASS (dry-run, formula check only)
```

**Final verdict: PASS (dry-run, formula check only)**
