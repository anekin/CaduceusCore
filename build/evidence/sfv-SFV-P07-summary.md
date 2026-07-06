# SFU Perf Case: SFV-P07

- **Op**: mmio
- **Dim**: 1 (gelu probe — minimal computation path)
- **Command**: `build/simv_tb_sfu_perf +case=SFV-P07 +op_code=2 +dim=1`

## Simulation Log (key events)

```
[TB] CMD=START at cycle 14
[TB] IRQ asserted at cycle 24
PERF|case=SFV-P07|op=op=gelu,dim=1|event=READ_INIT|cycles=1
PERF|case=SFV-P07|op=op=gelu,dim=1|event=RUN|cycles=2
PERF|case=SFV-P07|op=op=gelu,dim=1|event=FLUSH|cycles=5
PERF|case=SFV-P07|op=op=gelu,dim=1|event=TOTAL|cycles=8
[PERF] ASSERT (op 0): all anti-vacuous checks PASS
```

## Cycle Analysis

- **MMIO CMD.START → IRQ**: 10 cycles (cycle 14 → 24)
- **TOTAL active cycles**: 8 (READ_INIT=1, RUN=2, FLUSH=5)
- **BUSY ≤ 2 cycles**: ✅ (anti-vacuous check PASS)
- **DONE pulses**: 1 (exactly once per CMD)
- **IRQ timing**: IRQ asserted, DONE detected

## MMIO Timing Verification

| Check | Expected | Result |
|-------|----------|--------|
| BUSY rises ≤2 cycles after CMD | yes | ✅ PASS |
| STATUS.DONE pulses exactly once | 1 | ✅ PASS |
| IRQ asserted after DONE | yes | ✅ PASS |

**Final verdict: PASS**
