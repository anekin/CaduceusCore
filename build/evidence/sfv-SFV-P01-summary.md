# SFU Perf Case: SFV-P01

- **Op**: softmax
- **Dim**: 64
- **Pos**: 0
- **Command**: `scripts/run_sfu_perf_case.py --case SFV-P01 --op softmax --dim 64 --dry-run`

## Dry-Run Formula Check

```
Case: SFV-P01
Op: softmax, Dim: 64
Expected cycle formula: 3*N + 33
  = 3*64 + 33
  = 225
Tolerance: |delta| <= 5
[SFV-P01] op=softmax,dim=64 expected=225 — PASS (dry-run, formula check only)
```

**Final verdict: PASS (dry-run, formula check only)**
