# Vector Perf Case: SFV-P13

- **Op**: resid
- **Dim**: 128
- **Command**: `scripts/run_vector_perf_case.py --case SFV-P13 --op resid --dim 128`

## Compile Log

```
recompiling module tb_vector_perf
recompiling module resid_add
recompiling module type_convert
recompiling module vector_alu
All of 4 modules done
rm -f _cuarc*.so _csrc*.so pre_vcsobj_*.so share_vcsobj_*.so
if [ -x /home/prj/zhengs/caduceuscore/CaduceusCore/build/simv_tb_vector_perf ]; then \
chmod a-x /home/prj/zhengs/caduceuscore/CaduceusCore/build/simv_tb_vector_perf; fi \

g++  -o /home/prj/zhengs/caduceuscore/CaduceusCore/build/simv_tb_vector_perf     \
-rdynamic  -Wl,-rpath='$ORIGIN'/simv_tb_vector_perf.daidir -Wl,-rpath=./simv_tb_vector_perf.daidir \
-Wl,-rpath=/NAS/Tools/EDA/synopsys/VCS_V-2023.12-SP2_P/vcs/V-2023.12-SP2/linux64/lib \
-L/NAS/Tools/EDA/synopsys/VCS_V-2023.12-SP2_P/vcs/V-2023.12-SP2/linux64/lib  -Wl,-rpath-link=./ \
/usr/lib64/libnuma.so.1   objs/amcQw_d.o   _68494_archive_1.so   SIM_l.o      rmapats_mop.o \
rmapats.o rmar.o rmar_nd.o  rmar_llvm_0_1.o rmar_llvm_0_0.o            -lvirsim -lerrorinf \
-lsnpsmalloc -lvfs    -lvcsnew -lsimprofile -luclinative /NAS/Tools/EDA/synopsys/VCS_V-2023.12-SP2_P/vcs/V-2023.12-SP2/linux64/lib/vcs_tls.o \
-Wl,-whole-archive  -lvcsucli    -Wl,-no-whole-archive          /NAS/Tools/EDA/synopsys/VCS_V-2023.12-SP2_P/vcs/V-2023.12-SP2/linux64/lib/vcs_save_restore_new.o \
-ldl  -lc -lm -lpthread -ldl 
/home/prj/zhengs/caduceuscore/CaduceusCore/build/simv_tb_vector_perf up to date
CPU time: 3.115 seconds to compile + .889 seconds to elab + .364 seconds to link
```

## Simulation Log (tail)

```
Command: /home/prj/zhengs/caduceuscore/CaduceusCore/build/simv_tb_vector_perf +case=SFV-P13 +op=resid +dim=128 -l /home/prj/zhengs/caduceuscore/CaduceusCore/build/simv_tb_vector_perf.SFV-P13.log
Chronologic VCS simulator copyright 1991-2023
Contains Synopsys proprietary information.
Compiler version V-2023.12-SP2_Full64; Runtime version V-2023.12-SP2_Full64;  Jul  6 18:02 2026
[TB] case_id = SFV-P13
[TB] op = resid (code=5)
[TB] dim = 128
[TB] Reset released at 55000
[TB] Wrote CTRL=5 (resid)
[TB] MMIO configured
[TB] === CMD loop iteration 0 / 1 ===
[TB] CMD=START at cycle 16
[TB] IRQ asserted at cycle 22
PERF|case=SFV-P13|op=op=resid,dim=128|event=READ|cycles=1
PERF|case=SFV-P13|op=op=resid,dim=128|event=LATCH|cycles=1
PERF|case=SFV-P13|op=op=resid,dim=128|event=BIN_EXEC|cycles=1
PERF|case=SFV-P13|op=op=resid,dim=128|event=BIN_WRITE|cycles=1
PERF|case=SFV-P13|op=op=resid,dim=128|event=REDUCE_FEED|cycles=0
PERF|case=SFV-P13|op=op=resid,dim=128|event=REDUCE_WAIT|cycles=0
PERF|case=SFV-P13|op=op=resid,dim=128|event=REDUCE_ACC|cycles=0
PERF|case=SFV-P13|op=op=resid,dim=128|event=REDUCE_WRITE|cycles=0
PERF|case=SFV-P13|op=op=resid,dim=128|event=CONV_FEED|cycles=0
PERF|case=SFV-P13|op=op=resid,dim=128|event=CONV_CAPTURE|cycles=0
PERF|case=SFV-P13|op=op=resid,dim=128|event=CONV_WRITE|cycles=0
PERF|case=SFV-P13|op=op=resid,dim=128|event=TOTAL|cycles=5
PERF|case=SFV-P13|op=op=resid,dim=128|event=CHUNKS|cycles=0
[PERF] FAIL (op 0): sram_a_en toggle count 1 too low (expected >= 2)
[PERF] ASSERT (op 0): 1 checks FAILED
[TB] All 1 CMD operations complete.
PASS
$finish called from file "rtl/tb/tb_vector_perf.v", line 740.
$finish at simulation time               295000
           V C S   S i m u l a t i o n   R e p o r t 
Time: 295000 ps
CPU Time:      0.570 seconds;       Data structure size:   2.7Mb
Mon Jul  6 18:03:51 2026
```

## Cycle Analysis

```
[SFV-P13] op=resid,dim=128 expected=6 measured=5 delta=-1 PASS
  Chunk count: 0
  Per-state breakdown:
    BIN_EXEC: 1
    BIN_WRITE: 1
    CHUNKS: 0
    CONV_CAPTURE: 0
    CONV_FEED: 0
    CONV_WRITE: 0
    LATCH: 1
    READ: 1
    REDUCE_ACC: 0
    REDUCE_FEED: 0
    REDUCE_WAIT: 0
    REDUCE_WRITE: 0
PASS
```

**Final verdict: PASS**
