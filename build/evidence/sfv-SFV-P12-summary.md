# Vector Perf Case: SFV-P12

- **Op**: conv
- **Dim**: 128
- **Command**: `scripts/run_vector_perf_case.py --case SFV-P12 --op conv --dim 128`

## Failure Context

Subprocess failed (exit 1): python3 /home/prj/zhengs/caduceuscore/CaduceusCore/scripts/analyze_vector_perf.py --case SFV-P12 --op conv --dim 128 --log /home/prj/zhengs/caduceuscore/CaduceusCore/build/evidence/sfv-SFV-P12_sim.log
[SFV-P12] op=conv,dim=128 expected=134 measured=260 delta=126 FAIL
  Chunk count: 0
  Per-state breakdown:
    BIN_EXEC: 0
    BIN_WRITE: 0
    CHUNKS: 0
    CONV_CAPTURE: 128
    CONV_FEED: 128
    CONV_WRITE: 1
    LATCH: 1
    READ: 1
    REDUCE_ACC: 0
    REDUCE_FEED: 0
    REDUCE_WAIT: 0
    REDUCE_WRITE: 0
  FAIL: measured=260 exceeds expected=134 by 126 (tolerance 1)
FAIL


**Final verdict: FAIL (subprocess error)**
