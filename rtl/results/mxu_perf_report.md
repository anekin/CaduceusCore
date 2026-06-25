# MXU RTL vs Timing Model — Performance Co-simulation Report

This report compares RTL-measured cycle counts against the `block` timing model predictions for the 64x64 MXU Phase 1 design.

## Summary Statistics

- **Scenarios compared:** 115
- **Mean gap (cycles):** 15990.37 cycles
- **StdDev gap (cycles):** 59990.27 cycles
- **Mean gap (% of RTL):** 85.76%
- **Min gap (% of RTL):** -104%
- **Max gap (% of RTL):** 92.87%
- **StdDev gap (% of RTL):** 17.93%

Excluding the degenerate `zero_dim` scenario (M=0), the gap tightens: mean=87.42%, min=79.16%, max=92.87%, std=2.38%.

## Root Cause Analysis

The timing model counts only **DMA + pure compute cycles**. The RTL measurement spans the full MXU command lifecycle:

- MMIO register configuration (CMD, dimension, address registers)
- `LOAD_W` — weight tile DMA into on-chip weight buffer
- `LOAD_A` — activation tile DMA into on-chip activation buffer
- Tile-loop controller state-machine overhead
- `STORE_OUT` — result tile write-back to memory
- IRQ assertion and testbench handshaking

For the `single_tile` anchor (M=64, K=64, N=64), the RTL reports **1355** total cycles while the model predicts **145** cycles (compute 4 + DMA 141). The resulting gap is **1210** cycles (**89.3%** of RTL).

This gap is the fixed + per-tile deterministic overhead of the RTL control state machine. It is not modeled by the current BlockEngine, which abstracts the controller as zero-latency and only accounts for data movement and MAC array utilization.

## Key Insight — Consistent Deterministic Overhead

The gap is **consistent across non-degenerate scenarios**, not random. Excluding the `zero_dim` outlier, the standard deviation is 2.38% around a mean of 87.42%, confirming the overhead is deterministic state-machine latency rather than a functional bug or non-deterministic stall. Because it is consistent, the gap can be used as a one-time calibration offset for the timing model when translating BlockEngine estimates to expected RTL cycle counts. The `zero_dim` case (M=0) is a synthetic edge case where the model still allocates a weight tile while the RTL finishes almost immediately; it should be treated separately.

## Per-Scenario Comparison (sorted by gap_pct descending)

| scenario | M | K | N | RTL cycles | model compute | model DMA | model total | gap cycles | gap % |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| qwen_e2e | 1 | 2048 | 2048 | 696985 | 4096 | 45602 | 49698 | 647287 | 92.87% |
| random_056 | 1 | 242 | 177 | 7825 | 48 | 538 | 586 | 7239 | 92.51% |
| random_038 | 3 | 251 | 178 | 8155 | 48 | 573 | 621 | 7534 | 92.39% |
| random_078 | 78 | 60 | 182 | 6265 | 12 | 477 | 489 | 5776 | 92.19% |
| random_017 | 208 | 56 | 36 | 4545 | 4 | 352 | 356 | 4189 | 92.17% |
| multi_tile_M | 128 | 64 | 64 | 2685 | 4 | 235 | 239 | 2446 | 91.1% |
| multi_tile_M | 128 | 64 | 64 | 2685 | 4 | 235 | 239 | 2446 | 91.1% |
| multi_tile_M | 128 | 64 | 64 | 2685 | 4 | 235 | 239 | 2446 | 91.1% |
| random_057 | 82 | 48 | 253 | 7545 | 16 | 658 | 674 | 6871 | 91.07% |
| random_051 | 14 | 255 | 203 | 11465 | 64 | 1022 | 1086 | 10379 | 90.53% |
| partial_tile_M | 33 | 64 | 64 | 1045 | 4 | 95 | 99 | 946 | 90.53% |
| random_074 | 135 | 36 | 252 | 10345 | 16 | 970 | 986 | 9359 | 90.47% |
| random_075 | 10 | 230 | 31 | 2595 | 16 | 235 | 251 | 2344 | 90.33% |
| random_045 | 128 | 49 | 49 | 2385 | 4 | 235 | 239 | 2146 | 89.98% |
| random_034 | 18 | 189 | 88 | 4425 | 24 | 421 | 445 | 3980 | 89.94% |
| random_035 | 99 | 41 | 218 | 7665 | 16 | 758 | 774 | 6891 | 89.9% |
| random_085 | 180 | 37 | 253 | 12265 | 16 | 1235 | 1251 | 11014 | 89.8% |
| random_015 | 91 | 122 | 147 | 10615 | 24 | 1065 | 1089 | 9526 | 89.74% |
| random_096 | 133 | 254 | 235 | 37865 | 64 | 3822 | 3886 | 33979 | 89.74% |
| random_047 | 241 | 125 | 136 | 23335 | 24 | 2388 | 2412 | 20923 | 89.66% |
| random_067 | 241 | 120 | 61 | 7595 | 8 | 798 | 806 | 6789 | 89.39% |
| multi_tile_N | 64 | 64 | 128 | 2685 | 8 | 278 | 286 | 2399 | 89.35% |
| multi_tile_N | 64 | 64 | 128 | 2685 | 8 | 278 | 286 | 2399 | 89.35% |
| random_003 | 50 | 58 | 177 | 3415 | 12 | 353 | 365 | 3050 | 89.31% |
| overflow | 64 | 64 | 64 | 1355 | 4 | 141 | 145 | 1210 | 89.3% |
| partial_tile_N | 64 | 64 | 33 | 1355 | 4 | 141 | 145 | 1210 | 89.3% |
| single_tile | 64 | 64 | 64 | 1355 | 4 | 141 | 145 | 1210 | 89.3% |
| single_tile | 64 | 64 | 64 | 1355 | 4 | 141 | 145 | 1210 | 89.3% |
| single_tile | 64 | 64 | 64 | 1355 | 4 | 141 | 145 | 1210 | 89.3% |
| random_012 | 224 | 110 | 221 | 28025 | 32 | 2983 | 3015 | 25010 | 89.24% |
| random_022 | 134 | 168 | 33 | 6795 | 12 | 724 | 736 | 6059 | 89.17% |
| random_026 | 153 | 185 | 35 | 7495 | 12 | 808 | 820 | 6675 | 89.06% |
| random_080 | 43 | 53 | 98 | 2045 | 8 | 216 | 224 | 1821 | 89.05% |
| random_099 | 249 | 116 | 30 | 7515 | 8 | 822 | 830 | 6685 | 88.96% |
| random_060 | 243 | 113 | 144 | 21955 | 24 | 2406 | 2430 | 19525 | 88.93% |
| random_066 | 24 | 115 | 82 | 2985 | 16 | 317 | 333 | 2652 | 88.84% |
| random_027 | 23 | 109 | 86 | 2845 | 16 | 311 | 327 | 2518 | 88.51% |
| random_010 | 161 | 179 | 69 | 14765 | 24 | 1682 | 1706 | 13059 | 88.45% |
| random_058 | 222 | 173 | 141 | 29005 | 36 | 3329 | 3365 | 25640 | 88.4% |
| random_041 | 137 | 220 | 152 | 25465 | 48 | 2938 | 2986 | 22479 | 88.27% |
| random_002 | 160 | 21 | 244 | 9545 | 16 | 1117 | 1133 | 8412 | 88.13% |
| random_072 | 244 | 182 | 256 | 40985 | 48 | 4826 | 4874 | 36111 | 88.11% |
| random_037 | 160 | 95 | 161 | 14185 | 24 | 1674 | 1698 | 12487 | 88.03% |
| random_093 | 95 | 246 | 85 | 12445 | 32 | 1466 | 1498 | 10947 | 87.96% |
| random_019 | 105 | 26 | 206 | 6705 | 16 | 793 | 809 | 5896 | 87.93% |
| random_005 | 22 | 224 | 7 | 2655 | 16 | 305 | 321 | 2334 | 87.91% |
| random_070 | 215 | 229 | 130 | 35995 | 48 | 4314 | 4362 | 31633 | 87.88% |
| random_050 | 194 | 144 | 198 | 32905 | 48 | 3944 | 3992 | 28913 | 87.87% |
| random_031 | 95 | 170 | 38 | 4635 | 12 | 552 | 564 | 4071 | 87.83% |
| random_043 | 73 | 22 | 10 | 1295 | 4 | 154 | 158 | 1137 | 87.8% |
| random_094 | 91 | 164 | 11 | 4475 | 12 | 534 | 546 | 3929 | 87.8% |
| random_064 | 146 | 150 | 152 | 19075 | 36 | 2323 | 2359 | 16716 | 87.63% |
| random_055 | 201 | 142 | 191 | 24655 | 36 | 3051 | 3087 | 21568 | 87.48% |
| random_008 | 88 | 215 | 109 | 11065 | 32 | 1383 | 1415 | 9650 | 87.21% |
| random_061 | 175 | 240 | 152 | 28405 | 48 | 3608 | 3656 | 24749 | 87.13% |
| random_024 | 168 | 159 | 44 | 6865 | 12 | 874 | 886 | 5979 | 87.09% |
| random_032 | 209 | 76 | 6 | 5515 | 8 | 704 | 712 | 4803 | 87.09% |
| random_073 | 128 | 108 | 112 | 7265 | 16 | 929 | 945 | 6320 | 86.99% |
| random_090 | 204 | 136 | 79 | 16025 | 24 | 2062 | 2086 | 13939 | 86.98% |
| random_004 | 205 | 199 | 217 | 42785 | 64 | 5516 | 5580 | 37205 | 86.96% |
| random_029 | 24 | 211 | 191 | 7585 | 48 | 944 | 992 | 6593 | 86.92% |
| random_006 | 238 | 227 | 252 | 48585 | 64 | 6292 | 6356 | 42229 | 86.92% |
| random_018 | 202 | 195 | 240 | 42025 | 64 | 5445 | 5509 | 36516 | 86.89% |
| random_052 | 170 | 14 | 33 | 2295 | 4 | 297 | 301 | 1994 | 86.88% |
| random_084 | 160 | 216 | 3 | 8615 | 16 | 1117 | 1133 | 7482 | 86.85% |
| random_001 | 50 | 124 | 209 | 7345 | 32 | 936 | 968 | 6377 | 86.82% |
| random_091 | 181 | 163 | 78 | 14205 | 24 | 1859 | 1883 | 12322 | 86.74% |
| random_020 | 174 | 229 | 248 | 36505 | 64 | 4787 | 4851 | 31654 | 86.71% |
| random_092 | 33 | 102 | 8 | 1465 | 8 | 187 | 195 | 1270 | 86.69% |
| random_016 | 240 | 10 | 86 | 6025 | 8 | 795 | 803 | 5222 | 86.67% |
| random_025 | 245 | 154 | 218 | 36545 | 48 | 4844 | 4892 | 31653 | 86.61% |
| random_071 | 117 | 251 | 225 | 26145 | 64 | 3445 | 3509 | 22636 | 86.58% |
| random_044 | 219 | 203 | 203 | 43985 | 64 | 5845 | 5909 | 38076 | 86.57% |
| random_079 | 162 | 212 | 135 | 25495 | 48 | 3379 | 3427 | 22068 | 86.56% |
| random_040 | 148 | 134 | 165 | 17695 | 36 | 2350 | 2386 | 15309 | 86.52% |
| random_088 | 236 | 78 | 154 | 17545 | 24 | 2344 | 2368 | 15177 | 86.5% |
| random_011 | 119 | 252 | 47 | 6595 | 16 | 876 | 892 | 5703 | 86.47% |
| random_014 | 101 | 85 | 147 | 8695 | 24 | 1153 | 1177 | 7518 | 86.46% |
| random_000 | 23 | 199 | 168 | 7195 | 48 | 926 | 974 | 6221 | 86.46% |
| random_039 | 38 | 249 | 23 | 3065 | 16 | 399 | 415 | 2650 | 86.46% |
| random_082 | 145 | 70 | 117 | 7665 | 16 | 1029 | 1045 | 6620 | 86.37% |
| random_054 | 104 | 223 | 28 | 5865 | 16 | 787 | 803 | 5062 | 86.31% |
| random_081 | 101 | 150 | 88 | 8565 | 24 | 1153 | 1177 | 7388 | 86.26% |
| random_077 | 232 | 74 | 28 | 5665 | 8 | 772 | 780 | 4885 | 86.23% |
| random_065 | 166 | 143 | 52 | 6365 | 12 | 865 | 877 | 5488 | 86.22% |
| random_083 | 202 | 65 | 149 | 14965 | 24 | 2044 | 2068 | 12897 | 86.18% |
| random_021 | 237 | 209 | 102 | 22845 | 32 | 3136 | 3168 | 19677 | 86.13% |
| partial_tile_K | 64 | 33 | 64 | 1045 | 4 | 141 | 145 | 900 | 86.12% |
| random_089 | 186 | 229 | 121 | 18505 | 32 | 2536 | 2568 | 15937 | 86.12% |
| multi_tile_K | 64 | 128 | 64 | 2035 | 8 | 278 | 286 | 1749 | 85.95% |
| multi_tile_K | 64 | 128 | 64 | 2035 | 8 | 278 | 286 | 1749 | 85.95% |
| random_098 | 183 | 77 | 126 | 8845 | 16 | 1252 | 1268 | 7577 | 85.66% |
| random_033 | 189 | 222 | 229 | 36265 | 64 | 5140 | 5204 | 31061 | 85.65% |
| random_023 | 100 | 202 | 251 | 21545 | 64 | 3045 | 3109 | 18436 | 85.57% |
| random_059 | 256 | 142 | 203 | 35065 | 48 | 5038 | 5086 | 29979 | 85.5% |
| random_087 | 242 | 3 | 246 | 10985 | 16 | 1599 | 1615 | 9370 | 85.3% |
| random_013 | 183 | 73 | 85 | 8605 | 16 | 1252 | 1268 | 7337 | 85.26% |
| random_009 | 201 | 2 | 135 | 6895 | 12 | 1019 | 1031 | 5864 | 85.05% |
| random_069 | 50 | 177 | 102 | 4825 | 24 | 703 | 727 | 4098 | 84.93% |
| random_048 | 45 | 94 | 226 | 5945 | 32 | 877 | 909 | 5036 | 84.71% |
| random_068 | 40 | 220 | 240 | 11105 | 64 | 1634 | 1698 | 9407 | 84.71% |
| random_028 | 184 | 133 | 134 | 18685 | 36 | 2826 | 2862 | 15823 | 84.68% |
| random_063 | 16 | 25 | 206 | 1865 | 16 | 270 | 286 | 1579 | 84.66% |
| random_036 | 117 | 142 | 202 | 17105 | 48 | 2585 | 2633 | 14472 | 84.61% |
| random_042 | 62 | 107 | 211 | 7145 | 32 | 1077 | 1109 | 6036 | 84.48% |
| random_030 | 105 | 67 | 137 | 7735 | 24 | 1188 | 1212 | 6523 | 84.33% |
| random_007 | 117 | 6 | 118 | 2805 | 8 | 434 | 442 | 2363 | 84.24% |
| random_046 | 45 | 21 | 93 | 1445 | 8 | 222 | 230 | 1215 | 84.08% |
| random_097 | 54 | 169 | 101 | 4745 | 24 | 738 | 762 | 3983 | 83.94% |
| random_076 | 52 | 230 | 188 | 8995 | 48 | 1438 | 1486 | 7509 | 83.48% |
| random_086 | 60 | 232 | 183 | 9295 | 48 | 1579 | 1627 | 7668 | 82.5% |
| random_049 | 71 | 2 | 196 | 3425 | 16 | 593 | 609 | 2816 | 82.22% |
| random_095 | 50 | 66 | 118 | 2525 | 16 | 470 | 486 | 2039 | 80.75% |
| random_062 | 33 | 7 | 25 | 475 | 4 | 95 | 99 | 376 | 79.16% |
| zero_dim | 0 | 64 | 64 | 25 | 4 | 47 | 51 | -26 | -104% |

---
*Generated by `scripts/compare_mxu_perf.py`.*
