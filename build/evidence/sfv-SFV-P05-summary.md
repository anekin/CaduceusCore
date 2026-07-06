# SFU Perf Case: SFV-P05

- **Op**: silu
- **Dim**: 64
- **Pos**: 0
- **Command**: `/home/prj/zhengs/caduceuscore/CaduceusCore/scripts/run_sfu_perf_case.py --case SFV-P05 --op silu --dim 64 --dry-run`

## Dry-Run Formula Check

```
Case: SFV-P05
Op: silu, Dim: 64
Expected cycle formula: N + 7
  = 64 + 7
  = 71
Tolerance: |delta| <= 1
[SFV-P05] op=silu,dim=64 expected=71 — PASS (dry-run, formula check only)
```

**Final verdict: PASS (dry-run, formula check only)**
