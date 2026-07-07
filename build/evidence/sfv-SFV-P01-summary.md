# SFU Perf Case: SFV-P01

- **Op**: softmax
- **Dim**: 64
- **Pos**: 0
- **Command**: `scripts/run_sfu_perf_case.py --case SFV-P01 --op softmax --dim 64 --rebuild`

## Failure Context

Subprocess failed (exit 1): ssh zhengs@192.168.0.11 source /NAS/Tools/methodology/modules/init/bash && module load vcs/vcs_2023.12sp2 && cd /home/prj/zhengs/caduceuscore/CaduceusCore && vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -top tb_sfu_perf rtl/tb/tb_sfu_perf.v rtl/sfu/*.v -o /home/prj/zhengs/caduceuscore/CaduceusCore/build/simv_tb_sfu_perf -l /home/prj/zhengs/caduceuscore/CaduceusCore/build/simv_tb_sfu_perf.compile.log
Chronologic VCS (TM)
       Version V-2023.12-SP2_Full64 -- Mon Jul  6 17:12:37 2026

                    Copyright (c) 1991 - 2024 Synopsys, Inc.
   This software and the associated documentation are proprietary to Synopsys,
 Inc. This software may only be used in accordance with the terms and conditions
 of a written license agreement with Synopsys, Inc. All other use, reproduction,
   or distribution of this software is strictly prohibited.  Licensed Products
     communicate with Synopsys servers for the purpose of providing software
    updates, detecting software piracy and verifying that customers are using
    Licensed Products in conformity with the applicable License Key for such
  Licensed Products. Synopsys will use information gathered in connection with
    this process to deliver software updates and pursue software pirates and
                                   infringers.

 Inclusivity & Diversity - Visit SolvNetPlus to read the "Synopsys Statement on
            Inclusivity and Diversity" (Refer to article 000036315 at
                        https://solvnetplus.synopsys.com)

Parsing design file 'rtl/tb/tb_sfu_perf.v'
Parsing design file 'rtl/sfu/exp_lut.v'
Parsing design file 'rtl/sfu/gelu_hw.v'
Parsing design file 'rtl/sfu/layernorm_hw.v'
Parsing design file 'rtl/sfu/rmsnorm_hw.v'
Parsing design file 'rtl/sfu/rope_hw.v'
Parsing design file 'rtl/sfu/sfu_top.v'
Parsing design file 'rtl/sfu/silu_hw.v'
Parsing design file 'rtl/sfu/softmax_hw.v'
Top Level Modules:
       tb_sfu_perf
TimeScale is 1 ns / 1 ps
Starting vcs inline pass...

5 modules and 0 UDP read.
recompiling module tb_sfu_perf
recompiling module layernorm_hw
recompiling module rmsnorm_hw
recompiling module rope_hw
recompiling module softmax_hw
All of 5 modules done
CPU time: 1.349 seconds to compile
/NAS/Tools/EDA/synopsys/VCS_V-2023.12-SP2_P/vcs/V-2023.12-SP2/
Error: Failed to make rmapats.so @ /home/prj/zhengs/caduceuscore/CaduceusCore/build/simv_tb_sfu_perf.daidir/rmapats.so

**Final verdict: FAIL (subprocess error)**
