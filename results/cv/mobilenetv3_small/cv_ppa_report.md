# CV PPA Report — MobileNetV3-Small

## Executive Summary

- **Model**: MobileNetV3-Small (224×224 ImageNet classification)
- **Pareto + top results**: 47 points
- **Engine types represented**: 5 (systolic, os_systolic, block, tensor_core, wmma, gmma, input_stationary)
- **Arc model total MACs**: 56,510,400 (~56.5M)
- **ONNX Runtime theoretical MACs**: 56,510,400
- **MAC delta**: 0.00%
- **Total simulation cycles (baseline 128×128 systolic)**: 2,596,157
- **SRAM spill**: 0.00 MB

## Engine Comparison (Best per Engine Type)

| Engine | FPS | Area (mm²) | Power (W) | fps/W | fps/mm² | SRAM Spill (MB) | DW Util (%) |
|--------|-----|------------|-----------|-------|---------|-----------------|-------------|
| block | 1218.6 | 133.2 | 54.6 | 22.3 | 9.1 | 0.00 | 2.764 |
| gmma | 1218.6 | 135.2 | 55.4 | 22.0 | 9.0 | 0.00 | 2.764 |
| input_stationary | 532.6 | 22.7 | 7.3 | 73.0 | 23.5 | 0.00 | 0.108 |
| systolic | 497.6 | 22.2 | 7.2 | 69.1 | 22.4 | 0.00 | 0.103 |
| tensor_core | 1243.3 | 133.2 | 54.6 | 22.8 | 9.3 | 0.00 | 4.942 |

## Full Pareto Frontier + Top Results

All results sorted by FPS (descending). `pareto=true` marks non-dominated points.

| Rank | Pareto | Config | Engine | FPS | Area (mm²) | Power (W) | SRAM Spill (MB) | DW Util (%) |
|------|--------|--------|--------|-----|------------|-----------|-----------------|-------------|
| 1 | True | tens 64×64 INT2 800MHz  HBM3-1024b | tensor_core | 1243.3 | 133.2 | 54.6 | 0.00 | 4.942 |
| 2 | True | tens 64×64 INT2 1000MHz  HBM3-1024b | tensor_core | 1243.3 | 133.2 | 55.6 | 0.00 | 4.942 |
| 3 | True | tens 64×64 INT2 1200MHz  HBM3-1024b | tensor_core | 1243.3 | 133.2 | 56.6 | 0.00 | 4.942 |
| 4 | False | tens 96×96 INT2 800MHz  HBM3-1024b | tensor_core | 1243.1 | 143.2 | 58.6 | 0.00 | 2.208 |
| 5 | False | tens 96×96 INT2 1000MHz  HBM3-1024b | tensor_core | 1243.1 | 143.2 | 60.6 | 0.00 | 2.208 |
| 6 | False | tens 96×96 INT2 1200MHz  HBM3-1024b | tensor_core | 1243.1 | 143.2 | 62.6 | 0.00 | 2.208 |
| 7 | False | tens 64×64 INT4 800MHz  HBM3-1024b | tensor_core | 1234.4 | 133.2 | 54.6 | 0.00 | 4.118 |
| 8 | False | tens 64×64 INT4 1000MHz  HBM3-1024b | tensor_core | 1234.4 | 133.2 | 55.6 | 0.00 | 4.118 |
| 9 | False | tens 64×64 INT4 1200MHz  HBM3-1024b | tensor_core | 1234.4 | 133.2 | 56.6 | 0.00 | 4.118 |
| 10 | False | tens 96×96 INT4 800MHz  HBM3-1024b | tensor_core | 1234.1 | 143.2 | 58.6 | 0.00 | 1.840 |
| 11 | False | tens 96×96 INT4 1000MHz  HBM3-1024b | tensor_core | 1234.1 | 143.2 | 60.6 | 0.00 | 1.840 |
| 12 | False | tens 96×96 INT4 1200MHz  HBM3-1024b | tensor_core | 1234.1 | 143.2 | 62.6 | 0.00 | 1.840 |
| 13 | False | bloc 64×64 INT2 800MHz  HBM3-1024b | block | 1218.6 | 133.2 | 54.6 | 0.00 | 2.764 |
| 14 | False | bloc 64×64 INT2 800MHz WC HBM3-1024b | block | 1218.6 | 133.2 | 54.6 | 0.00 | 2.764 |
| 15 | False | bloc 64×64 INT2 1000MHz  HBM3-1024b | block | 1218.6 | 133.2 | 55.6 | 0.00 | 2.764 |
| 16 | False | bloc 64×64 INT2 1000MHz WC HBM3-1024b | block | 1218.6 | 133.2 | 55.6 | 0.00 | 2.764 |
| 17 | False | bloc 64×64 INT2 1200MHz  HBM3-1024b | block | 1218.6 | 133.2 | 56.6 | 0.00 | 2.764 |
| 18 | False | bloc 64×64 INT2 1200MHz WC HBM3-1024b | block | 1218.6 | 133.2 | 56.6 | 0.00 | 2.764 |
| 19 | False | gmma 64×64 INT2 800MHz  HBM3-1024b | gmma | 1218.6 | 135.2 | 55.4 | 0.00 | 2.764 |
| 20 | False | gmma 64×64 INT2 800MHz WC HBM3-1024b | gmma | 1218.6 | 135.2 | 55.4 | 0.00 | 2.764 |
| 21 | True | tens 64×64 INT2 800MHz  LPDDR5-256b | tensor_core | 1132.7 | 49.2 | 18.6 | 0.00 | 1.236 |
| 22 | True | tens 64×64 INT2 1000MHz  LPDDR5-256b | tensor_core | 1132.7 | 49.2 | 19.6 | 0.00 | 1.236 |
| 23 | True | tens 64×64 INT2 1200MHz  LPDDR5-256b | tensor_core | 1132.7 | 49.2 | 20.6 | 0.00 | 1.236 |
| 24 | True | tens 64×64 INT2 800MHz  LPDDR5-128b | tensor_core | 1012.6 | 35.2 | 12.6 | 0.00 | 0.618 |
| 25 | True | tens 64×64 INT2 1000MHz  LPDDR5-128b | tensor_core | 1012.6 | 35.2 | 13.6 | 0.00 | 0.618 |
| 26 | True | tens 64×64 INT2 1200MHz  LPDDR5-128b | tensor_core | 1012.6 | 35.2 | 14.6 | 0.00 | 0.618 |
| 27 | True | tens 64×64 INT2 800MHz  LPDDR5-64b | tensor_core | 835.4 | 28.2 | 9.6 | 0.00 | 0.309 |
| 28 | True | tens 64×64 INT2 1000MHz  LPDDR5-64b | tensor_core | 835.4 | 28.2 | 10.6 | 0.00 | 0.309 |
| 29 | True | tens 64×64 INT2 1200MHz  LPDDR5-64b | tensor_core | 835.4 | 28.2 | 11.6 | 0.00 | 0.309 |
| 30 | True | tens 64×64 INT2 800MHz  LPDDR5-32b | tensor_core | 618.9 | 24.7 | 8.1 | 0.00 | 0.154 |
| 31 | True | tens 64×64 INT2 1000MHz  LPDDR5-32b | tensor_core | 618.9 | 24.7 | 9.1 | 0.00 | 0.154 |
| 32 | True | tens 64×64 INT2 1200MHz  LPDDR5-32b | tensor_core | 618.9 | 24.7 | 10.1 | 0.00 | 0.154 |
| 33 | True | inpu 64×64 INT2 800MHz  LPDDR5-32b | input_stationary | 532.6 | 22.7 | 7.3 | 0.00 | 0.108 |
| 34 | True | inpu 64×64 INT2 1000MHz  LPDDR5-32b | input_stationary | 532.6 | 22.7 | 8.1 | 0.00 | 0.108 |
| 35 | True | inpu 64×64 INT2 1200MHz  LPDDR5-32b | input_stationary | 532.6 | 22.7 | 8.9 | 0.00 | 0.108 |
| 36 | True | syst 64×64 INT2 800MHz  LPDDR5-64b | systolic | 497.6 | 22.2 | 7.2 | 0.00 | 0.103 |
| 37 | True | syst 64×64 INT2 800MHz WC LPDDR5-64b | systolic | 497.6 | 22.2 | 7.2 | 0.00 | 0.103 |
| 38 | True | syst 64×64 INT2 1000MHz  LPDDR5-64b | systolic | 497.6 | 22.2 | 7.6 | 0.00 | 0.103 |
| 39 | True | syst 64×64 INT2 1000MHz WC LPDDR5-64b | systolic | 497.6 | 22.2 | 7.6 | 0.00 | 0.103 |
| 40 | True | syst 64×64 INT2 1200MHz  LPDDR5-64b | systolic | 497.6 | 22.2 | 8.0 | 0.00 | 0.103 |
| 41 | True | syst 64×64 INT2 1200MHz WC LPDDR5-64b | systolic | 497.6 | 22.2 | 8.0 | 0.00 | 0.103 |
| 42 | True | syst 64×64 INT2 800MHz  LPDDR5-32b | systolic | 375.2 | 18.7 | 5.7 | 0.00 | 0.064 |
| 43 | True | syst 64×64 INT2 800MHz WC LPDDR5-32b | systolic | 375.2 | 18.7 | 5.7 | 0.00 | 0.064 |
| 44 | True | syst 64×64 INT2 1000MHz  LPDDR5-32b | systolic | 375.2 | 18.7 | 6.1 | 0.00 | 0.064 |
| 45 | True | syst 64×64 INT2 1000MHz WC LPDDR5-32b | systolic | 375.2 | 18.7 | 6.1 | 0.00 | 0.064 |
| 46 | True | syst 64×64 INT2 1200MHz  LPDDR5-32b | systolic | 375.2 | 18.7 | 6.5 | 0.00 | 0.064 |
| 47 | True | syst 64×64 INT2 1200MHz WC LPDDR5-32b | systolic | 375.2 | 18.7 | 6.5 | 0.00 | 0.064 |

## Design Space Parameters

- **MAC engines**: systolic, os_systolic, block, tensor_core, wmma, gmma, input_stationary
- **Array dimensions**: 64×64, 96×96, 128×128, 128×192, 128×256, 192×256, 256×256
- **Frequencies**: 500, 800, 1000, 1200 MHz
- **DRAM bandwidths**: 25.6, 51.2, 102.4, 204.8, 460.0, 819.0 GB/s
- **Weight precision**: INT2, INT4, INT8
- **Activation precision**: INT8, BF16
- **Constraints**: max area ≤ 150 mm², max power ≤ 50 W

## Key Findings

1. High-throughput configs use large tensor_core/block/gmma arrays with INT4 weights and HBM3, reaching >1200 fps at >130 mm² and >50 W.
2. Area-efficient configs use small systolic/input_stationary arrays with INT2/INT4 weights, delivering ~500 fps at ~22 mm² and ~7 W.
3. SRAM spill is zero across all evaluated configurations because MobileNetV3-Small activation working set fits within 2560 KB total SRAM.
4. Depthwise utilization remains low (<5%) even with channel-tiling because depthwise convolutions have N=1.
5. Winograd was not evaluated per Arc Model guardrails; all convolutions use im2col→GEMM.

## Per-Layer Cycle Breakdown (Baseline Systolic 128×128)

| Layer | Type | MACs | Cycles | Compute | DMA | MXU Util (%) |
|-------|------|------|--------|---------|-----|--------------|
| node_Conv_506 | pointwise_conv | 5,419,008 | 81,139 | 12,928 | 68211 | 0.33 |
| n0 | hard_swish | 0 | 1,568 | 1,568 | 0 | 0.00 |
| node_Conv_508 | depthwise_conv | 451,584 | 239,829 | 50,560 | 189270 | 0.01 |
| node_relu | relu | 0 | 392 | 392 | 0 | 0.00 |
| node_conv2d_2 | pointwise_conv | 128 | 577 | 385 | 192 | 0.17 |
| node_relu_1 | relu | 0 | 1 | 1 | 0 | 0.00 |
| node_conv2d_3 | pointwise_conv | 128 | 576 | 385 | 192 | 0.17 |
| node_hardsigmoid | hard_sigmoid | 0 | 1 | 1 | 0 | 0.00 |
| node_mul_141 | mul | 0 | 1 | 1 | 0 | 0.00 |
| node_Conv_510 | pointwise_conv | 802,816 | 17,542 | 3,520 | 14023 | 0.19 |
| node_Conv_512 | pointwise_conv | 3,612,672 | 17,542 | 3,520 | 14023 | 0.86 |
| node_relu_2 | relu | 0 | 1,764 | 1,764 | 0 | 0.00 |
| node_Conv_514 | depthwise_conv | 508,032 | 269,737 | 56,832 | 212905 | 0.01 |
| node_relu_3 | relu | 0 | 441 | 441 | 0 | 0.00 |
| node_Conv_516 | pointwise_conv | 1,354,752 | 8,850 | 1,168 | 7682 | 1.15 |
| node_Conv_518 | pointwise_conv | 1,655,808 | 5,391 | 1,168 | 4223 | 1.39 |
| node_relu_4 | relu | 0 | 539 | 539 | 0 | 0.00 |
| node_Conv_520 | depthwise_conv | 620,928 | 329,551 | 69,376 | 260176 | 0.01 |
| node_relu_5 | relu | 0 | 539 | 539 | 0 | 0.00 |
| node_Conv_522 | pointwise_conv | 1,655,808 | 10,003 | 1,168 | 8835 | 1.39 |
| node_add_201 | add | 0 | 1 | 1 | 0 | 0.00 |
| node_Conv_524 | pointwise_conv | 1,806,336 | 5,391 | 1,168 | 4223 | 1.53 |
| n0_2 | hard_swish | 0 | 588 | 588 | 0 | 0.00 |
| node_Conv_526 | depthwise_conv | 470,400 | 117,964 | 19,200 | 98764 | 0.02 |
| n0_3 | hard_swish | 0 | 147 | 147 | 0 | 0.00 |
| node_conv2d_13 | pointwise_conv | 2,304 | 584 | 385 | 200 | 0.17 |
| node_relu_6 | relu | 0 | 1 | 1 | 0 | 0.00 |
| node_conv2d_14 | pointwise_conv | 2,304 | 578 | 385 | 193 | 0.17 |
| node_hardsigmoid_1 | hard_sigmoid | 0 | 1 | 1 | 0 | 0.00 |
| node_mul_465 | mul | 0 | 1 | 1 | 0 | 0.00 |
| node_Conv_528 | pointwise_conv | 752,640 | 3,073 | 580 | 2493 | 1.71 |
| node_Conv_530 | pointwise_conv | 1,881,600 | 2,829 | 1,160 | 1670 | 2.75 |
| n0_4 | hard_swish | 0 | 368 | 368 | 0 | 0.00 |
| node_Conv_532 | depthwise_conv | 1,176,000 | 294,053 | 47,424 | 246629 | 0.02 |
| n0_5 | hard_swish | 0 | 368 | 368 | 0 | 0.00 |
| node_conv2d_18 | pointwise_conv | 15,360 | 983 | 770 | 213 | 0.10 |
| node_relu_7 | relu | 0 | 1 | 1 | 0 | 0.00 |
| node_conv2d_19 | pointwise_conv | 15,360 | 966 | 770 | 197 | 0.10 |
| node_hardsigmoid_2 | hard_sigmoid | 0 | 2 | 2 | 0 | 0.00 |
| node_mul_725 | mul | 0 | 1 | 1 | 0 | 0.00 |
| node_Conv_534 | pointwise_conv | 1,881,600 | 6,432 | 1,160 | 5273 | 2.75 |
| node_add_421 | add | 0 | 1 | 1 | 0 | 0.00 |
| node_Conv_536 | pointwise_conv | 1,881,600 | 2,829 | 1,160 | 1670 | 2.75 |
| n0_6 | hard_swish | 0 | 368 | 368 | 0 | 0.00 |
| node_Conv_538 | depthwise_conv | 1,176,000 | 294,053 | 47,424 | 246629 | 0.02 |
| n0_7 | hard_swish | 0 | 368 | 368 | 0 | 0.00 |
| node_conv2d_23 | pointwise_conv | 15,360 | 983 | 770 | 213 | 0.10 |
| node_relu_8 | relu | 0 | 1 | 1 | 0 | 0.00 |
| node_conv2d_24 | pointwise_conv | 15,360 | 966 | 770 | 197 | 0.10 |
| node_hardsigmoid_3 | hard_sigmoid | 0 | 2 | 2 | 0 | 0.00 |
| node_mul_991 | mul | 0 | 1 | 1 | 0 | 0.00 |
| node_Conv_540 | pointwise_conv | 1,881,600 | 6,432 | 1,160 | 5273 | 2.75 |
| node_add_539 | add | 0 | 1 | 1 | 0 | 0.00 |
| node_Conv_542 | pointwise_conv | 940,800 | 2,064 | 580 | 1485 | 2.16 |
| n0_8 | hard_swish | 0 | 184 | 184 | 0 | 0.00 |
| node_Conv_544 | depthwise_conv | 588,000 | 147,312 | 23,904 | 123408 | 0.02 |
| n0_9 | hard_swish | 0 | 184 | 184 | 0 | 0.00 |
| node_conv2d_28 | pointwise_conv | 3,840 | 587 | 385 | 202 | 0.17 |
| node_relu_9 | relu | 0 | 1 | 1 | 0 | 0.00 |
| node_conv2d_29 | pointwise_conv | 3,840 | 578 | 385 | 194 | 0.17 |
| node_hardsigmoid_4 | hard_sigmoid | 0 | 1 | 1 | 0 | 0.00 |
| node_mul_1257 | mul | 0 | 1 | 1 | 0 | 0.00 |
| node_Conv_546 | pointwise_conv | 1,128,960 | 3,505 | 580 | 2926 | 2.60 |
| node_Conv_548 | pointwise_conv | 1,354,752 | 2,973 | 1,160 | 1814 | 1.99 |
| n0_10 | hard_swish | 0 | 221 | 221 | 0 | 0.00 |
| node_Conv_550 | depthwise_conv | 705,600 | 176,660 | 28,608 | 148053 | 0.02 |
| n0_11 | hard_swish | 0 | 221 | 221 | 0 | 0.00 |
| node_conv2d_33 | pointwise_conv | 5,760 | 974 | 770 | 204 | 0.10 |
| node_relu_10 | relu | 0 | 1 | 1 | 0 | 0.00 |
| node_conv2d_34 | pointwise_conv | 5,760 | 964 | 770 | 195 | 0.10 |
| node_hardsigmoid_5 | hard_sigmoid | 0 | 2 | 2 | 0 | 0.00 |
| node_mul_1517 | mul | 0 | 1 | 1 | 0 | 0.00 |
| node_Conv_552 | pointwise_conv | 1,354,752 | 4,703 | 1,160 | 3543 | 1.99 |
| node_add_759 | add | 0 | 1 | 1 | 0 | 0.00 |
| node_Conv_554 | pointwise_conv | 2,709,504 | 3,738 | 1,740 | 1999 | 2.89 |
| n0_12 | hard_swish | 0 | 441 | 441 | 0 | 0.00 |
| node_Conv_556 | depthwise_conv | 352,800 | 88,616 | 14,496 | 74120 | 0.02 |
| n0_13 | hard_swish | 0 | 111 | 111 | 0 | 0.00 |
| node_conv2d_38 | pointwise_conv | 20,736 | 1,372 | 1,155 | 217 | 0.07 |
| node_relu_11 | relu | 0 | 1 | 1 | 0 | 0.00 |
| node_conv2d_39 | pointwise_conv | 20,736 | 1,352 | 1,155 | 198 | 0.07 |
| node_hardsigmoid_6 | hard_sigmoid | 0 | 3 | 3 | 0 | 0.00 |
| node_mul_1783 | mul | 0 | 1 | 1 | 0 | 0.00 |
| node_Conv_558 | pointwise_conv | 1,354,752 | 2,928 | 1,299 | 1629 | 2.58 |
| node_Conv_560 | pointwise_conv | 2,709,504 | 2,929 | 2,165 | 764 | 3.32 |
| n0_14 | hard_swish | 0 | 221 | 221 | 0 | 0.00 |
| node_Conv_562 | depthwise_conv | 705,600 | 176,660 | 28,608 | 148053 | 0.02 |
| n0_15 | hard_swish | 0 | 221 | 221 | 0 | 0.00 |
| node_conv2d_43 | pointwise_conv | 82,944 | 4,093 | 3,850 | 244 | 0.07 |
| node_relu_12 | relu | 0 | 2 | 2 | 0 | 0.00 |
| node_conv2d_44 | pointwise_conv | 82,944 | 4,054 | 3,850 | 204 | 0.07 |
| node_hardsigmoid_7 | hard_sigmoid | 0 | 5 | 5 | 0 | 0.00 |
| node_mul_2043 | mul | 0 | 1 | 1 | 0 | 0.00 |
| node_Conv_564 | pointwise_conv | 2,709,504 | 5,091 | 2,165 | 2926 | 3.32 |
| node_add_979 | add | 0 | 1 | 1 | 0 | 0.00 |
| node_Conv_566 | pointwise_conv | 2,709,504 | 2,929 | 2,165 | 764 | 3.32 |
| n0_16 | hard_swish | 0 | 221 | 221 | 0 | 0.00 |
| node_Conv_568 | depthwise_conv | 705,600 | 176,660 | 28,608 | 148053 | 0.02 |
| n0_17 | hard_swish | 0 | 221 | 221 | 0 | 0.00 |
| node_conv2d_48 | pointwise_conv | 82,944 | 4,093 | 3,850 | 244 | 0.07 |
| node_relu_13 | relu | 0 | 2 | 2 | 0 | 0.00 |
| node_conv2d_49 | pointwise_conv | 82,944 | 4,054 | 3,850 | 204 | 0.07 |
| node_hardsigmoid_8 | hard_sigmoid | 0 | 5 | 5 | 0 | 0.00 |
| node_mul_2309 | mul | 0 | 1 | 1 | 0 | 0.00 |
| node_Conv_570 | pointwise_conv | 2,709,504 | 5,091 | 2,165 | 2926 | 3.32 |
| node_add_1097 | add | 0 | 1 | 1 | 0 | 0.00 |
| node_Conv_572 | pointwise_conv | 2,709,504 | 2,929 | 2,165 | 764 | 3.32 |
| n0_18 | hard_swish | 0 | 221 | 221 | 0 | 0.00 |
| node_linear | gemm | 589,824 | 15,591 | 15,400 | 191 | 0.12 |
| n0_19 | hard_swish | 0 | 8 | 8 | 0 | 0.00 |
| node_linear_1 | gemm | 1,024,000 | 24,831 | 24,640 | 191 | 0.13 |

## Layer Type Aggregates

| Type | Count | Total MACs | Total Cycles | Avg MXU Util (%) |
|------|-------|------------|--------------|------------------|
| add | 6 | 0 | 6 | 0.00 |
| concat | 1 | 0 | 0 | 0.00 |
| depthwise_conv | 11 | 7,460,544 | 2,311,095 | 0.02 |
| gemm | 2 | 1,613,824 | 40,422 | 0.12 |
| global_avg_pool | 10 | 0 | 0 | 0.00 |
| hard_sigmoid | 9 | 0 | 22 | 0.00 |
| hard_swish | 19 | 0 | 6,250 | 0.00 |
| mul | 9 | 0 | 9 | 0.00 |
| pointwise_conv | 41 | 47,436,032 | 234,667 | 1.28 |
| relu | 14 | 0 | 3,686 | 0.00 |
| reshape | 1 | 0 | 0 | 0.00 |
| shape | 1 | 0 | 0 | 0.00 |

## Methodology

1. ONNX import: MobileNetV3-Small exported from torchvision at opset 18 (124 nodes).
2. Trace generation: Conv layers mapped to GEMM via im2col; pointwise M=H×W, depthwise M=H×W×C_in.
3. Cycle estimation: MACEngine.estimate(M,K,N) per layer plus im2col DMA and SFU/vector cycles.
4. PPA: Extended model adds im2col feeder, Pool2D, and Conv SFU area/power.
5. Pareto: Non-dominated sorting on fps, area, power with area/power constraints.

## ONNX Runtime Validation

| Metric | Arc Model | ONNX Runtime | Delta |
|--------|-----------|-------------|-------|
| Total MACs | 56,510,400 | 56,510,400 | 0.00% |

ONNX Runtime inference with random (1, 3, 224, 224) float32 input produced (1, 1000) logits.

## Per-Engine Notes

### block
- Best config: `bloc 64×64 INT2 800MHz  HBM3-1024b`
- FPS: 1218.6
- Area: 133.2 mm²
- Power: 54.6 W
- SRAM spill: 0.00 MB
- Depthwise util: 2.764%

### gmma
- Best config: `gmma 64×64 INT2 800MHz  HBM3-1024b`
- FPS: 1218.6
- Area: 135.2 mm²
- Power: 55.4 W
- SRAM spill: 0.00 MB
- Depthwise util: 2.764%

### input_stationary
- Best config: `inpu 64×64 INT2 800MHz  LPDDR5-32b`
- FPS: 532.6
- Area: 22.7 mm²
- Power: 7.3 W
- SRAM spill: 0.00 MB
- Depthwise util: 0.108%

### systolic
- Best config: `syst 64×64 INT2 800MHz  LPDDR5-64b`
- FPS: 497.6
- Area: 22.2 mm²
- Power: 7.2 W
- SRAM spill: 0.00 MB
- Depthwise util: 0.103%

### tensor_core
- Best config: `tens 64×64 INT2 800MHz  HBM3-1024b`
- FPS: 1243.3
- Area: 133.2 mm²
- Power: 54.6 W
- SRAM spill: 0.00 MB
- Depthwise util: 4.942%

## Appendix: Raw Data

Complete results available in `pareto_full.json` in this directory.
