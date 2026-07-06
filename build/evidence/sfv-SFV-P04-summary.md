# SFU Perf Case: SFV-P04

- **Op**: gelu
- **Dim**: 64
- **Pos**: 0
- **Command**: `/home/prj/zhengs/caduceuscore/CaduceusCore/scripts/run_sfu_perf_case.py --case SFV-P04 --op gelu --dim 64 --dry-run`

## Dry-Run Formula Check

```
Case: SFV-P04
Op: gelu, Dim: 64
Expected cycle formula: N + 7
  = 64 + 7
  = 71
Tolerance: |delta| <= 1
[SFV-P04] op=gelu,dim=64 expected=71 — PASS (dry-run, formula check only)
```

**Final verdict: PASS (dry-run, formula check only)**
